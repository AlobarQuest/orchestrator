## Tasks 6–9 — on-ingest detection, the detect-pass, and lease-expired evidence recovery

> # ⚠️ CONTRACT ERRATA — READ BEFORE IMPLEMENTING
>
> This section was drafted before the cross-task contracts were reconciled. The migration id,
> the evidence-head index name, and the advisory-lock namespace have already been corrected in
> the text below. **Three contract corrections remain and are BINDING — they override every
> code block in this section.**
>
> ### E-1. `record_reconciliation_condition` takes a `ConditionCommand`, and `key_facts` is REQUIRED
>
> The body text below calls it with keyword arguments and **omits `key_facts` entirely**.
> `key_facts` is the **hash input**: without it, every condition on a unit hashes identically, so
> an `external_merge_alarm` and a `pr_state_divergence` on the same unit would collide into one
> row and the second would be silently swallowed as a duplicate. The canonical form is:
>
> ```python
> record_reconciliation_condition(session, command: ConditionCommand) -> ConditionOutcome | DomainError
> # ConditionCommand(frozen): actor, work_unit_id, observation_kind, condition_type, key_facts,
> #                           stored_state, observed_state, detail,
> #                           observation_id=None, deployment_observation_id=None
> # ConditionOutcome(frozen): (condition: ReconciliationCondition, suppressed: bool)
> ```
>
> Every call site in this section (`_record`, `_record_split_brain`, `record_digest_divergence`)
> must construct a `ConditionCommand` and supply `key_facts`:
>
> | detection site | `key_facts` |
> |---|---|
> | `github_pr` | `{"pr_number": facts["pr_number"], "head_sha": facts["head_sha"]}` |
> | `github_check` | `{"check_name": facts["check_name"]}` |
> | `deploy_split_brain` / `digest_divergence` | `{"release_artifact_binding_id": str(binding.id)}` |
>
> ### E-2. `release_claim` takes NO `Session`, and takes `released_at`
>
> Task 9's draft calls `release_claim(session, claim, terminal_reason="lease_expired")`. The
> canonical primitive (Task 3) is:
>
> ```python
> release_claim(claim: Claim, *, terminal_reason: str, released_at: datetime) -> None
> ```
>
> It is the **sole writer** of `Claim.released_at` / `Claim.terminal_reason` and does not commit —
> the caller reuses its own transaction timestamp. In `recover_evidence`, `now` is already
> computed, so the call site becomes:
>
> ```python
> release_claim(claim, terminal_reason="lease_expired", released_at=now)
> ```
>
> ### E-3. Task 8 must NOT re-add the config field
>
> `Settings.reconcile_split_brain_stall_seconds` is declared in **Task 1 only**, default **900**
> (env `ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS`). Task 8's draft adds it again with a
> default of `3600` — **skip that step.** Task 8 only *consumes* it via `get_settings()` and passes
> `stall_seconds` into the detect service (which is why the detect service takes it as an argument
> and the tests need no env or clock manipulation).
>
> Everything else in this section is correct and must survive verbatim — in particular: the
> post-commit hook placement, gating the PR identity check on `pr_number` and **not** `head_sha`
> (gating on head equality would make the head-change rule unfireable by construction), the
> route-layer `digest_divergence` write on a **rejected** ingest, and above all Task 9's
> head-supersession wedge prevention.

**Inherited from Tasks 1–5 (exact signatures these tasks consume):**

```python
# src/orchestrator/services/reconciliation.py            (Tasks 2–3)
@dataclass(frozen=True)
class ConditionCommand:
    actor: ActorContext
    work_unit_id: uuid.UUID
    observation_kind: str                      # "github_pr" | "github_check" | "deployment"
    condition_type: str                        # see CONDITION_TYPES
    key_facts: dict[str, Any]                  # REQUIRED — the divergence-hash input
    stored_state: dict[str, Any]
    observed_state: dict[str, Any]
    detail: str
    observation_id: uuid.UUID | None = None
    deployment_observation_id: uuid.UUID | None = None

@dataclass(frozen=True)
class ConditionOutcome:
    condition: ReconciliationCondition
    suppressed: bool          # True => identical unresolved condition already existed (dedup no-op)

def record_reconciliation_condition(
    session: Session,
    command: ConditionCommand,
) -> ConditionOutcome | DomainError:
    """Append-only. Own transaction (commits). Writes reconciliation_conditions + events only —
    never work_units, never a transition. Dedups on
    sha256(kind, condition_type, key_facts, resolution_generation)."""

# src/orchestrator/services/pr_bindings.py               (Task 4)
def get_pr_binding(session: Session, work_unit_id: uuid.UUID) -> UnitPrBinding | None
def upsert_pr_binding(session, *, actor, work_unit_id, pr_number: int, head_sha: str) -> UnitPrBinding | DomainError
def record_verification_read_head(session, *, actor, work_unit_id, head_sha: str) -> UnitPrBinding | DomainError
# UnitPrBinding: work_unit_id (PK), pr_number: int, head_sha: str,
#                verification_read_head_sha: str | None (write-once), updated_at

# src/orchestrator/services/claims.py                    (Task 5, factored out of _perform_reclaim)
def release_claim(claim: Claim, *, terminal_reason: str, released_at: datetime) -> None:
    """Sets claim.released_at and claim.terminal_reason. Takes NO Session and does NOT commit —
    the caller owns the transaction and supplies the transaction clock's `now`. Sole writer of
    those two columns."""
```

`key_facts` is the hash input and is **required** at every call site: PR →
`{"pr_number": …, "head_sha": …}`; check → `{"check_name": …}`; deploy / split-brain / digest →
`{"release_artifact_binding_id": str(binding.id)}`.

Migration `0014_wsp21_recovery_controls` (Task 1) has already created `reconciliation_conditions`,
`reconciliation_resolutions`, `unit_pr_binding`, and the partial unique index
`uq_evidence_unsuperseded_head` on `evidence (work_package_revision_id, work_unit_id, ac_id) WHERE
supersedes_evidence_id IS NULL` (§2.1 / §10). Task 9 has a test that fails loudly if that index
is missing. Task 1 also declares `Settings.reconcile_split_brain_stall_seconds: int = 900`; Task 8
only consumes it. Task 5b adds the HUMAN `/review` resolution route
(`POST /review/reconciliation/conditions/{condition_id}/resolution`), so a condition detected here
can actually be closed.

---

### Task 6: On-ingest `github_pr` detection (AC-001)

**Files:**
- Create: `src/orchestrator/services/reconciliation_detection.py`
- Create: `tests/services/test_reconciliation_detection_pr.py`
- Modify: `src/orchestrator/api/routes.py` — imports (~77, ~154-159) and `create_observation` (676-705)
- Modify: `tests/api/test_observations_api.py` — append the end-to-end hook test

**Interfaces:**
- **Consumes:** `record_reconciliation_condition(...) -> ConditionOutcome | DomainError`; `get_pr_binding(session, work_unit_id) -> UnitPrBinding | None`; `record_observation(session, ObservationCommand) -> Observation | DomainError` (`services/observations.py:83`); `DomainError` (`errors.py`); `ActorContext` (`services/lifecycle.py:38`); `WorkUnitState` (`kernel/states.py`).
- **Produces (Tasks 7–8 rely on these exact names):**
  - `DetectionCounters` — frozen dataclass, fields `conditions_recorded: int`, `skipped_correlations: int`, `suppressed_duplicates: int`; `__add__` for accumulation; `.as_dict() -> dict[str, int]`.
  - `detect_observation_conditions(session: Session, observation: Observation, actor: ActorContext) -> DetectionCounters` — the post-commit hook. **Never raises.**
  - `PR_STATE_DIVERGENCE = "pr_state_divergence"`, `EXTERNAL_MERGE_ALARM = "external_merge_alarm"`.
  - `_current_observation(session, subject_reference, observation_type, partition_key=None) -> Observation | None` (newest by `(observed_at, received_at, id)`).

**Why the hook is at the route layer and post-commit.** `record_observation` commits inside itself
(`observations.py:86`). Detection therefore runs *after* it returns, in the next transaction on the
same `Session` — a rejected ingest returns a `DomainError` and never reaches the hook, and a
detection failure cannot roll the observation back. `record_reconciliation_condition` commits each
condition in its own transaction (§1.8), so one failing condition cannot discard the others.

**Correlation gate (do not gate on head sha).** The identity check is `facts["pr_number"] ==
binding.pr_number` plus a well-formed 40-hex `head_sha`. The head is a *rule input*, not a gate —
gating on `head_sha == binding.head_sha` would make the head-change divergence rule unable to ever
fire, which is the rule's entire purpose.

#### Steps

- [ ] **6.1 — Failing test: an unknown/forged correlation is skipped and counted, never raised.**
  Create `tests/services/test_reconciliation_detection_pr.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Observation, ReconciliationCondition, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.observations import ObservationCommand, record_observation
from orchestrator.services.pr_bindings import record_verification_read_head, upsert_pr_binding
from orchestrator.services.reconciliation_detection import (
    DetectionCounters,
    detect_observation_conditions,
)
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
HEAD = "a" * 40
NEW_HEAD = "b" * 40
OBSERVED_AT = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def pr_facts(**overrides: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "pr_number": 42,
        "head_sha": HEAD,
        "state": "open",
        "merged": False,
    }
    facts.update(overrides)
    return facts


def ingest(
    session: Session,
    unit: WorkUnit,
    *,
    key: str,
    facts: dict[str, Any],
    observed_at: datetime = OBSERVED_AT,
    observation_type: str = "github_pr",
) -> Observation:
    reference = f"pr:{facts.get('pr_number')}@{facts.get('head_sha')}:{key}"
    result = record_observation(
        session,
        ObservationCommand(
            actor=SYSTEM,
            source_system="github",
            source_reference=reference,
            source_url=None,
            trust_classification="delivery_system",
            subject_type="work_unit",
            subject_reference=str(unit.id),
            environment=None,
            observation_type=observation_type,
            status="observed",
            severity="info",
            observed_at=observed_at,
            summary="pull request observed",
            facts=facts,
            payload_digest=None,
            idempotency_key=key,
        ),
    )
    assert isinstance(result, Observation)
    return result


def conditions(session: Session) -> list[ReconciliationCondition]:
    return list(session.scalars(select(ReconciliationCondition)))


def test_forged_work_unit_reference_is_skipped_and_counted(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-forged")
    migrated_session.commit()
    observation = ingest(migrated_session, unit, key="pr-forged-1", facts=pr_facts(merged=True))
    observation.subject_reference = str(uuid.uuid4())  # a work unit that does not exist

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters == DetectionCounters(
        conditions_recorded=0, skipped_correlations=1, suppressed_duplicates=0
    )
    assert conditions(migrated_session) == []


def test_pr_number_mismatch_against_binding_is_skipped_and_counted(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "pr-mismatch")
    upsert_pr_binding(migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    migrated_session.commit()
    observation = ingest(
        migrated_session, unit, key="pr-mismatch-1", facts=pr_facts(pr_number=99, merged=True)
    )

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.skipped_correlations == 1
    assert counters.conditions_recorded == 0
    assert conditions(migrated_session) == []


def test_missing_binding_is_skipped_and_counted(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-unbound")
    migrated_session.commit()
    observation = ingest(migrated_session, unit, key="pr-unbound-1", facts=pr_facts(merged=True))

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.skipped_correlations == 1
    assert conditions(migrated_session) == []


def test_malformed_facts_never_raise(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-malformed")
    upsert_pr_binding(migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    migrated_session.commit()
    observation = ingest(
        migrated_session,
        unit,
        key="pr-malformed-1",
        facts={"pr_number": "forty-two", "head_sha": 7, "state": ["open"], "merged": "yes"},
    )

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.skipped_correlations == 1
    assert conditions(migrated_session) == []
```

- [ ] **6.2 — Run it; expect collection failure.**
  `uv run pytest tests/services/test_reconciliation_detection_pr.py -x`
  → `ModuleNotFoundError: No module named 'orchestrator.services.reconciliation_detection'`.

- [ ] **6.3 — Minimal impl: the module skeleton + the skip-and-count spine.**
  Create `src/orchestrator/services/reconciliation_detection.py`:

```python
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import Observation, WorkUnit
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.pr_bindings import get_pr_binding
from orchestrator.services.reconciliation import (
    ConditionOutcome,
    record_reconciliation_condition,
)

EXTERNAL_MERGE_ALARM = "external_merge_alarm"
PR_STATE_DIVERGENCE = "pr_state_divergence"

SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class DetectionCounters:
    conditions_recorded: int = 0
    skipped_correlations: int = 0
    suppressed_duplicates: int = 0

    def __add__(self, other: "DetectionCounters") -> "DetectionCounters":
        return DetectionCounters(
            self.conditions_recorded + other.conditions_recorded,
            self.skipped_correlations + other.skipped_correlations,
            self.suppressed_duplicates + other.suppressed_duplicates,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "conditions_recorded": self.conditions_recorded,
            "skipped_correlations": self.skipped_correlations,
            "suppressed_duplicates": self.suppressed_duplicates,
        }


SKIPPED = DetectionCounters(skipped_correlations=1)


def detect_observation_conditions(
    session: Session,
    observation: Observation,
    actor: ActorContext,
) -> DetectionCounters:
    """Post-commit ingest hook. Runs in its own transaction and NEVER raises: a malformed or
    unknown correlation is skipped and counted, so a forged correlation can never turn a valid
    observation into a rejected ingest."""
    try:
        if observation.observation_type == "github_pr" and observation.subject_type == "work_unit":
            return _detect_pull_request(session, observation, actor)
        return DetectionCounters()
    except Exception:  # fail-open, counted (§1.7) — detection must never break ingest
        session.rollback()
        return SKIPPED


def _detect_pull_request(
    session: Session,
    observation: Observation,
    actor: ActorContext,
) -> DetectionCounters:
    unit = _correlated_unit(session, observation)
    if unit is None:
        return SKIPPED
    facts = _pull_request_facts(observation.facts)
    if facts is None:
        return SKIPPED
    binding = get_pr_binding(session, unit.id)
    if binding is None or binding.pr_number != facts["pr_number"]:
        return SKIPPED
    if _current_observation(session, observation.subject_reference, "github_pr") != observation:
        return DetectionCounters()  # a late-arriving older fact; the current one already ran

    counters = DetectionCounters()
    read_head = binding.verification_read_head_sha
    completed = WorkUnitState(unit.state) is WorkUnitState.COMPLETED
    stored = {
        "state": unit.state,
        "pr_number": binding.pr_number,
        "head_sha": binding.head_sha,
        "verification_read_head_sha": read_head,
    }
    if facts["merged"] and not completed:
        counters += _record(
            session,
            actor,
            unit,
            observation,
            condition_type=EXTERNAL_MERGE_ALARM,
            stored_state=stored,
            observed_state=facts,
            detail="pull request was merged outside the session before the unit completed",
        )
    head_changed = read_head is not None and facts["head_sha"] != read_head
    if facts["state"] == "closed" and not facts["merged"]:
        counters += _record(
            session,
            actor,
            unit,
            observation,
            condition_type=PR_STATE_DIVERGENCE,
            stored_state=stored,
            observed_state=facts,
            detail="pull request was closed outside the session",
        )
    elif head_changed:
        counters += _record(
            session,
            actor,
            unit,
            observation,
            condition_type=PR_STATE_DIVERGENCE,
            stored_state=stored,
            observed_state=facts,
            detail="pull request head changed after verification read it",
        )
    return counters


def _record(
    session: Session,
    actor: ActorContext,
    unit: WorkUnit,
    observation: Observation,
    *,
    condition_type: str,
    stored_state: dict[str, Any],
    observed_state: dict[str, Any],
    detail: str,
) -> DetectionCounters:
    outcome = record_reconciliation_condition(
        session,
        actor=actor,
        work_unit_id=unit.id,
        observation_kind=observation.observation_type,
        condition_type=condition_type,
        stored_state=stored_state,
        observed_state=observed_state,
        detail=detail,
        observation_id=observation.id,
    )
    if isinstance(outcome, DomainError):
        return SKIPPED
    if outcome.suppressed:
        return DetectionCounters(suppressed_duplicates=1)
    return DetectionCounters(conditions_recorded=1)


def _correlated_unit(session: Session, observation: Observation) -> WorkUnit | None:
    try:
        unit_id = uuid.UUID(observation.subject_reference)
    except ValueError:
        return None
    return session.get(WorkUnit, unit_id)


def _current_observation(
    session: Session,
    subject_reference: str,
    observation_type: str,
    partition_key: tuple[str, object] | None = None,
) -> Observation | None:
    """The 'current' observation for a (unit, kind) is the newest by
    (observed_at, received_at, id) — never observed_at alone: two upstream facts can carry the
    same upstream timestamp, and upstream clocks skew."""
    stmt = select(Observation).where(
        Observation.subject_type == "work_unit",
        Observation.subject_reference == subject_reference,
        Observation.observation_type == observation_type,
    )
    if partition_key is not None:
        name, value = partition_key
        stmt = stmt.where(Observation.facts[name].astext == str(value))
    stmt = stmt.order_by(
        Observation.observed_at.desc(),
        Observation.received_at.desc(),
        Observation.id.desc(),
    ).limit(1)
    return session.scalar(stmt)


def _pull_request_facts(facts: dict[str, Any]) -> dict[str, Any] | None:
    number = facts.get("pr_number")
    head_sha = facts.get("head_sha")
    state = facts.get("state")
    merged = facts.get("merged")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or not isinstance(head_sha, str)
        or SHA.fullmatch(head_sha) is None
        or not isinstance(state, str)
        or not isinstance(merged, bool)
    ):
        return None
    return {"pr_number": number, "head_sha": head_sha, "state": state, "merged": merged}
```

- [ ] **6.4 — Run; expect pass.** `uv run pytest tests/services/test_reconciliation_detection_pr.py -x`

- [ ] **6.5 — Failing test: the three detection rules + the never-un-complete guard.**
  Append to `tests/services/test_reconciliation_detection_pr.py`:

```python
def test_merged_pr_on_incomplete_unit_records_external_merge_alarm(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "pr-merged")
    upsert_pr_binding(migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    migrated_session.commit()
    observation = ingest(
        migrated_session, unit, key="pr-merged-1", facts=pr_facts(state="closed", merged=True)
    )

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.conditions_recorded == 1
    recorded = conditions(migrated_session)
    assert [row.condition_type for row in recorded] == ["external_merge_alarm"]
    assert recorded[0].work_unit_id == unit.id
    assert recorded[0].observation_id == observation.id
    migrated_session.refresh(unit)
    assert unit.state == WorkUnitState.READY  # never merged, never completed


def test_closed_pr_records_pr_state_divergence(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-closed")
    upsert_pr_binding(migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    migrated_session.commit()
    observation = ingest(
        migrated_session, unit, key="pr-closed-1", facts=pr_facts(state="closed", merged=False)
    )

    detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert [row.condition_type for row in conditions(migrated_session)] == ["pr_state_divergence"]


def test_head_change_before_verification_read_does_not_alarm(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-rebase")
    upsert_pr_binding(migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    migrated_session.commit()
    observation = ingest(migrated_session, unit, key="pr-rebase-1", facts=pr_facts(head_sha=NEW_HEAD))

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_head_change_after_verification_read_records_divergence(migrated_session: Session) -> None:
    unit = register_unit(migrated_session, "pr-forcepush")
    upsert_pr_binding(migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    record_verification_read_head(
        migrated_session, actor=SYSTEM, work_unit_id=unit.id, head_sha=HEAD
    )
    migrated_session.commit()
    observation = ingest(
        migrated_session, unit, key="pr-forcepush-1", facts=pr_facts(head_sha=NEW_HEAD)
    )

    detect_observation_conditions(migrated_session, observation, SYSTEM)

    recorded = conditions(migrated_session)
    assert [row.condition_type for row in recorded] == ["pr_state_divergence"]
    assert recorded[0].observed_state["head_sha"] == NEW_HEAD


def test_completed_unit_is_never_un_completed_and_never_alarms_on_merge(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "pr-completed")
    unit.state = WorkUnitState.COMPLETED
    upsert_pr_binding(migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    migrated_session.commit()
    version = unit.version
    observation = ingest(
        migrated_session, unit, key="pr-completed-1", facts=pr_facts(state="closed", merged=True)
    )

    counters = detect_observation_conditions(migrated_session, observation, SYSTEM)

    assert counters.conditions_recorded == 0
    assert conditions(migrated_session) == []
    migrated_session.refresh(unit)
    assert (unit.state, unit.version) == (WorkUnitState.COMPLETED, version)


def test_newest_observation_wins_and_re_detection_is_a_counted_duplicate(
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "pr-newest")
    upsert_pr_binding(migrated_session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    migrated_session.commit()
    older = ingest(
        migrated_session,
        unit,
        key="pr-newest-open",
        facts=pr_facts(state="open"),
        observed_at=OBSERVED_AT,
    )
    newer = ingest(
        migrated_session,
        unit,
        key="pr-newest-merged",
        facts=pr_facts(state="closed", merged=True),
        observed_at=OBSERVED_AT + timedelta(minutes=5),
    )

    assert detect_observation_conditions(migrated_session, older, SYSTEM) == DetectionCounters()
    first = detect_observation_conditions(migrated_session, newer, SYSTEM)
    second = detect_observation_conditions(migrated_session, newer, SYSTEM)

    assert first.conditions_recorded == 1
    assert second == DetectionCounters(suppressed_duplicates=1)
    assert len(conditions(migrated_session)) == 1
```

- [ ] **6.6 — Run; expect pass** (the rules are already in 6.3's impl; this step proves them).
  `uv run pytest tests/services/test_reconciliation_detection_pr.py`
  If `test_head_change_before_verification_read_does_not_alarm` fails, the implementation gated on
  `binding.head_sha` instead of `verification_read_head_sha` — fix the rule, not the test.

- [ ] **6.7 — Failing test: the route runs the hook post-commit.**
  Append to `tests/api/test_observations_api.py` (uses the existing `db_client` + `SYSTEM` header
  fixtures already imported there):

```python
def test_observation_route_records_reconciliation_condition_after_commit(
    db_client: TestClient,
    migrated_session: Session,
) -> None:
    unit = register_unit(migrated_session, "pr-route")
    upsert_pr_binding(
        migrated_session,
        actor=ActorContext("system", ActorRole.SYSTEM),
        work_unit_id=unit.id,
        pr_number=42,
        head_sha="a" * 40,
    )
    migrated_session.commit()
    body = observation_body(key="pr-route-1") | {
        "observation_type": "github_pr",
        "source_reference": "pr:42@" + "a" * 40 + ":route",
        "subject_type": "work_unit",
        "subject_reference": str(unit.id),
        "status": "observed",
        "facts": {"pr_number": 42, "head_sha": "a" * 40, "state": "closed", "merged": True},
    }

    response = db_client.post("/api/v1/observations", json=body, headers=SYSTEM)

    assert response.status_code == 201
    migrated_session.expire_all()
    rows = list(migrated_session.scalars(select(ReconciliationCondition)))
    assert [row.condition_type for row in rows] == ["external_merge_alarm"]
```

  (Add the imports this test needs at the top of the file: `select`, `Session`,
  `ReconciliationCondition`, `ActorContext`, `ActorRole`, `upsert_pr_binding`, `register_unit`.)

- [ ] **6.8 — Run; expect failure** — 201 is returned but no condition row exists (`assert [] ==
  ["external_merge_alarm"]`).

- [ ] **6.9 — Minimal impl: wire the hook into the route.**
  In `src/orchestrator/api/routes.py`, add `Observation` to the `orchestrator.persistence.models`
  import block (~77) and add:

```python
from orchestrator.services.reconciliation_detection import detect_observation_conditions
```

  Then replace the body of `create_observation` (routes.py:676-705):

```python
@router.post("/observations", response_model=ObservationResponse, status_code=201)
def create_observation(
    body: ObservationCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_observation(
        session,
        ObservationCommand(
            actor=actor,
            source_system=body.source_system,
            source_reference=body.source_reference,
            source_url=body.source_url,
            trust_classification=body.trust_classification,
            subject_type=body.subject_type,
            subject_reference=body.subject_reference,
            environment=body.environment,
            observation_type=body.observation_type,
            status=body.status,
            severity=body.severity,
            observed_at=body.observed_at,
            summary=body.summary,
            facts=body.facts,
            payload_digest=body.payload_digest,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
        ),
    )
    # Post-commit, own transaction: record_observation has already committed. Detection never
    # raises, so a forged correlation cannot turn a valid observation into a rejected ingest.
    if isinstance(result, Observation):
        detect_observation_conditions(session, result, actor)
    return _raise_error(result)
```

- [ ] **6.10 — Run; expect pass.**
  `uv run pytest tests/api/test_observations_api.py tests/services/test_reconciliation_detection_pr.py`

- [ ] **6.11 — Commit.**
  `git add src/orchestrator/services/reconciliation_detection.py src/orchestrator/api/routes.py tests/services/test_reconciliation_detection_pr.py tests/api/test_observations_api.py && git commit -m "AC-001: on-ingest github_pr reconciliation detection (post-commit, fail-open counted)"`

---

### Task 7: On-ingest `github_check` detection (AC-002) + `digest_divergence`

**Files:**
- Modify: `src/orchestrator/services/reconciliation_detection.py` — add the check + deployment branches
- Modify: `src/orchestrator/api/routes.py` — `create_deployment_observation` (628-661)
- Create: `tests/services/test_reconciliation_detection_check.py`
- Create: `tests/api/test_digest_divergence_route.py`

**Interfaces:**
- **Consumes:** everything Task 6 produced; `record_deployment_observation(session, DeploymentObservationCommand) -> DeploymentObservation | DomainError` (`deployment_observations.py:70`), whose digest guard **raises** `DomainError("deployment_observation_digest_mismatch", …)` at `deployment_observations.py:415-420` and whose caller then **rolls back** (`:79-80`); `ReleaseArtifactBinding` (`models.py`).
- **Produces:**
  - `CHECK_RESULT_FLIP = "check_result_flip"`, `DIGEST_DIVERGENCE = "digest_divergence"`.
  - `record_digest_divergence(session: Session, *, actor: ActorContext, release_artifact_binding_id: uuid.UUID, observed_artifact_digest: str, environment: str) -> DetectionCounters` — **route-layer** writer for the rejected-ingest case. Never raises.
  - `detect_observation_conditions` now also handles `observation_type="deployment"` on
    `subject_type="release_binding"` (the runner's deploy channel, §1.3 M-F) — Task 8 rule (b)
    reads those rows.

**The digest condition cannot be written inside the ingest service.** The guard raises and
`record_deployment_observation` rolls the transaction back — a condition written there would be
erased. It is therefore written at the route layer, in a fresh transaction, *after* the
`DomainError` is caught, and the ingest **stays rejected** (409).

#### Steps

- [ ] **7.1 — Failing test: the check flip rule.**
  Create `tests/services/test_reconciliation_detection_check.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import ReconciliationCondition
from orchestrator.services.pr_bindings import record_verification_read_head, upsert_pr_binding
from orchestrator.services.reconciliation_detection import (
    DetectionCounters,
    detect_observation_conditions,
)
from tests.services.test_dependencies import register_unit
from tests.services.test_reconciliation_detection_pr import (
    HEAD,
    NEW_HEAD,
    OBSERVED_AT,
    SYSTEM,
    conditions,
    ingest,
)


def check_facts(**overrides: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "pr_number": 42,
        "head_sha": HEAD,
        "check_name": "Quality",
        "conclusion": "success",
    }
    facts.update(overrides)
    return facts


def bound_unit(session: Session, key: str, *, read_head: str | None = HEAD):
    unit = register_unit(session, key)
    upsert_pr_binding(session, actor=SYSTEM, work_unit_id=unit.id, pr_number=42, head_sha=HEAD)
    if read_head is not None:
        record_verification_read_head(session, actor=SYSTEM, work_unit_id=unit.id, head_sha=read_head)
    session.commit()
    return unit


def test_check_that_flips_from_success_to_failure_records_check_result_flip(
    migrated_session: Session,
) -> None:
    unit = bound_unit(migrated_session, "check-flip")
    passed = ingest(
        migrated_session,
        unit,
        key="check-flip-pass",
        facts=check_facts(conclusion="success"),
        observation_type="github_check",
    )
    detect_observation_conditions(migrated_session, passed, SYSTEM)
    failed = ingest(
        migrated_session,
        unit,
        key="check-flip-fail",
        facts=check_facts(conclusion="failure"),
        observed_at=OBSERVED_AT + timedelta(minutes=10),
        observation_type="github_check",
    )

    counters = detect_observation_conditions(migrated_session, failed, SYSTEM)

    assert counters.conditions_recorded == 1
    rows = conditions(migrated_session)
    assert [row.condition_type for row in rows] == ["check_result_flip"]
    assert rows[0].observation_kind == "github_check"
    migrated_session.refresh(unit)
    assert unit.state == WorkUnitState.READY  # never auto-un-completed


def test_check_failure_that_verification_never_read_as_success_does_not_flip(
    migrated_session: Session,
) -> None:
    unit = bound_unit(migrated_session, "check-never-green", read_head=None)
    failed = ingest(
        migrated_session,
        unit,
        key="check-red-1",
        facts=check_facts(conclusion="failure"),
        observation_type="github_check",
    )

    counters = detect_observation_conditions(migrated_session, failed, SYSTEM)

    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_a_different_check_name_is_an_independent_partition(migrated_session: Session) -> None:
    unit = bound_unit(migrated_session, "check-partition")
    quality = ingest(
        migrated_session,
        unit,
        key="check-quality-pass",
        facts=check_facts(check_name="Quality", conclusion="success"),
        observation_type="github_check",
    )
    detect_observation_conditions(migrated_session, quality, SYSTEM)
    other = ingest(
        migrated_session,
        unit,
        key="check-security-fail",
        facts=check_facts(check_name="Security", conclusion="failure"),
        observed_at=OBSERVED_AT + timedelta(minutes=1),
        observation_type="github_check",
    )

    counters = detect_observation_conditions(migrated_session, other, SYSTEM)

    # Security never went green under the verification-read head, so its failure is not a flip.
    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_completed_unit_check_flip_records_but_never_un_completes(migrated_session: Session) -> None:
    unit = bound_unit(migrated_session, "check-completed")
    passed = ingest(
        migrated_session,
        unit,
        key="check-completed-pass",
        facts=check_facts(conclusion="success"),
        observation_type="github_check",
    )
    detect_observation_conditions(migrated_session, passed, SYSTEM)
    unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()
    version = unit.version
    failed = ingest(
        migrated_session,
        unit,
        key="check-completed-fail",
        facts=check_facts(conclusion="failure"),
        observed_at=OBSERVED_AT + timedelta(minutes=10),
        observation_type="github_check",
    )

    detect_observation_conditions(migrated_session, failed, SYSTEM)

    assert [row.condition_type for row in conditions(migrated_session)] == ["check_result_flip"]
    migrated_session.refresh(unit)
    assert (unit.state, unit.version) == (WorkUnitState.COMPLETED, version)
```

- [ ] **7.2 — Run; expect failure.**
  `uv run pytest tests/services/test_reconciliation_detection_check.py -x`
  → `ImportError: cannot import name 'CHECK_RESULT_FLIP'` … or, once imports resolve,
  `assert 0 == 1` (`conditions_recorded`), because `detect_observation_conditions` returns
  `DetectionCounters()` for `github_check`.

- [ ] **7.3 — Minimal impl: the check branch.**
  In `reconciliation_detection.py` add the constant and the dispatch, then the rule:

```python
CHECK_RESULT_FLIP = "check_result_flip"
```

  In `detect_observation_conditions`, extend the dispatch:

```python
        if observation.subject_type == "work_unit":
            if observation.observation_type == "github_pr":
                return _detect_pull_request(session, observation, actor)
            if observation.observation_type == "github_check":
                return _detect_check(session, observation, actor)
        return DetectionCounters()
```

  And add:

```python
def _detect_check(
    session: Session,
    observation: Observation,
    actor: ActorContext,
) -> DetectionCounters:
    unit = _correlated_unit(session, observation)
    if unit is None:
        return SKIPPED
    facts = _check_facts(observation.facts)
    if facts is None:
        return SKIPPED
    binding = get_pr_binding(session, unit.id)
    if binding is None or binding.pr_number != facts["pr_number"]:
        return SKIPPED
    partition = ("check_name", facts["check_name"])
    current = _current_observation(
        session, observation.subject_reference, "github_check", partition
    )
    if current != observation:
        return DetectionCounters()  # newest per (unit, check_name) wins
    read_head = binding.verification_read_head_sha
    if read_head is None or facts["conclusion"] != "failure":
        return DetectionCounters()
    if not _was_green_at(session, observation, facts["check_name"], read_head):
        return DetectionCounters()
    return _record(
        session,
        actor,
        unit,
        observation,
        condition_type=CHECK_RESULT_FLIP,
        stored_state={
            "check_name": facts["check_name"],
            "conclusion": "success",
            "head_sha": read_head,
        },
        observed_state=facts,
        detail="check succeeded when verification read it and later failed",
    )


def _was_green_at(
    session: Session,
    observation: Observation,
    check_name: str,
    read_head: str,
) -> bool:
    """AC-002's supersession is computed, not stored: an earlier append-only row for the same
    (unit, check) that was `success` on the head verification actually read."""
    earlier = session.scalar(
        select(Observation.id)
        .where(
            Observation.id != observation.id,
            Observation.subject_type == "work_unit",
            Observation.subject_reference == observation.subject_reference,
            Observation.observation_type == "github_check",
            Observation.facts["check_name"].astext == check_name,
            Observation.facts["conclusion"].astext == "success",
            Observation.facts["head_sha"].astext == read_head,
        )
        .limit(1)
    )
    return earlier is not None


def _check_facts(facts: dict[str, Any]) -> dict[str, Any] | None:
    number = facts.get("pr_number")
    head_sha = facts.get("head_sha")
    check_name = facts.get("check_name")
    conclusion = facts.get("conclusion")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or not isinstance(head_sha, str)
        or SHA.fullmatch(head_sha) is None
        or not isinstance(check_name, str)
        or not check_name.strip()
        or not isinstance(conclusion, str)
        or not conclusion.strip()
    ):
        return None
    return {
        "pr_number": number,
        "head_sha": head_sha,
        "check_name": check_name,
        "conclusion": conclusion,
    }
```

- [ ] **7.4 — Run; expect pass.** `uv run pytest tests/services/test_reconciliation_detection_check.py`

- [ ] **7.5 — Failing test: a rejected digest ingest still records the condition.**
  Create `tests/api/test_digest_divergence_route.py` (reuse the existing binding/observation
  fixture helpers from `tests/api/test_deployment_observations_api.py`, which already builds a
  completed implementation unit + `ReleaseArtifactBinding` — `_validated_subject`
  (`deployment_observations.py:400-405`) requires `COMPLETED`):

```python
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import DeploymentObservation, ReconciliationCondition
from tests.api.test_deployment_observations_api import (
    SYSTEM,
    deployment_observation_body,
    register_release_binding,
)


def test_digest_mismatch_is_rejected_and_the_condition_survives(
    db_client: TestClient,
    migrated_session: Session,
) -> None:
    binding = register_release_binding(db_client, migrated_session)
    body = deployment_observation_body() | {
        "observed_artifact_digest": "sha256:" + "f" * 64,  # not the bound digest
        "idempotency_key": "digest-divergence-1",
    }

    response = db_client.post(
        f"/api/v1/release-artifacts/{binding.id}/deployment-observations",
        json=body,
        headers=SYSTEM,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "deployment_observation_digest_mismatch"
    migrated_session.expire_all()
    # The ingest is still rejected: no observation, no post-deploy unit.
    assert list(migrated_session.scalars(select(DeploymentObservation))) == []
    # …and the condition survives the service's rollback because it was written at the route
    # layer, in its own transaction, after the DomainError was caught.
    rows = list(migrated_session.scalars(select(ReconciliationCondition)))
    assert [row.condition_type for row in rows] == ["digest_divergence"]
    assert rows[0].work_unit_id == binding.work_unit_id
    assert rows[0].observed_state["observed_artifact_digest"] == "sha256:" + "f" * 64
    assert rows[0].stored_state["artifact_digest"] == binding.artifact_digest
```

- [ ] **7.6 — Run; expect failure** — 409 is correct, but `[] != ["digest_divergence"]`.

- [ ] **7.7 — Minimal impl: `record_digest_divergence` + the deployment branch.**
  In `reconciliation_detection.py`:

```python
DIGEST_DIVERGENCE = "digest_divergence"
```

  Add `ReleaseArtifactBinding` to the models import, then:

```python
def record_digest_divergence(
    session: Session,
    *,
    actor: ActorContext,
    release_artifact_binding_id: uuid.UUID,
    observed_artifact_digest: str,
    environment: str,
) -> DetectionCounters:
    """Route-layer writer for the REJECTED deployment ingest. The digest guard raises and the
    ingest service rolls back (deployment_observations.py:79-80, :415-420), so this must run in
    its own transaction after the DomainError is caught — otherwise the condition is erased.
    Never raises: the caller still returns the rejection."""
    try:
        binding = session.get(ReleaseArtifactBinding, release_artifact_binding_id)
        if binding is None:
            return SKIPPED
        outcome = record_reconciliation_condition(
            session,
            actor=actor,
            work_unit_id=binding.work_unit_id,
            observation_kind="deployment",
            condition_type=DIGEST_DIVERGENCE,
            stored_state={
                "artifact_digest": binding.artifact_digest,
                "release_artifact_binding_id": str(binding.id),
            },
            observed_state={
                "observed_artifact_digest": observed_artifact_digest,
                "environment": environment,
            },
            detail="observed artifact digest does not match the immutable release binding",
        )
        if isinstance(outcome, DomainError):
            return SKIPPED
        if outcome.suppressed:
            return DetectionCounters(suppressed_duplicates=1)
        return DetectionCounters(conditions_recorded=1)
    except Exception:
        session.rollback()
        return SKIPPED
```

  And handle the runner's deploy channel inside `detect_observation_conditions` (§1.3 M-F — the
  `subject_type="release_binding"` observation Task 8 rule (b) also reads):

```python
        if (
            observation.observation_type == "deployment"
            and observation.subject_type == "release_binding"
        ):
            return _detect_reported_deploy(session, observation, actor)
```

```python
def _detect_reported_deploy(
    session: Session,
    observation: Observation,
    actor: ActorContext,
) -> DetectionCounters:
    binding = _correlated_binding(session, observation)
    digest = observation.facts.get("artifact_digest")
    if binding is None or not isinstance(digest, str) or not digest.strip():
        return SKIPPED
    if digest == binding.artifact_digest:
        return DetectionCounters()
    outcome = record_reconciliation_condition(
        session,
        actor=actor,
        work_unit_id=binding.work_unit_id,
        observation_kind="deployment",
        condition_type=DIGEST_DIVERGENCE,
        stored_state={
            "artifact_digest": binding.artifact_digest,
            "release_artifact_binding_id": str(binding.id),
        },
        observed_state={"artifact_digest": digest, "environment": observation.environment},
        detail="runner-reported artifact digest does not match the release binding",
        observation_id=observation.id,
    )
    if isinstance(outcome, DomainError):
        return SKIPPED
    if outcome.suppressed:
        return DetectionCounters(suppressed_duplicates=1)
    return DetectionCounters(conditions_recorded=1)


def _correlated_binding(
    session: Session, observation: Observation
) -> ReleaseArtifactBinding | None:
    try:
        binding_id = uuid.UUID(observation.subject_reference)
    except ValueError:
        return None
    return session.get(ReleaseArtifactBinding, binding_id)
```

- [ ] **7.8 — Minimal impl: the route.**
  In `routes.py`, import `record_digest_divergence` alongside `detect_observation_conditions`, and
  rewrite `create_deployment_observation` (628-661) so the rejection still reaches the client:

```python
def create_deployment_observation(
    binding_id: UUID,
    body: DeploymentObservationCommandModel,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    result = record_deployment_observation(
        session,
        DeploymentObservationCommand(
            release_artifact_binding_id=binding_id,
            actor=actor,
            environment=body.environment,
            base_url=body.base_url,
            observed_artifact_digest=body.observed_artifact_digest,
            deployment_ref=body.deployment_ref,
            deployment_url=body.deployment_url,
            deployer=body.deployer,
            observed_at=body.observed_at,
            probe_summary=body.probe_summary,
            route_summary=body.route_summary,
            auth_summary=body.auth_summary,
            dispatch_summary=body.dispatch_summary,
            status_summary=body.status_summary,
            idempotency_key=body.idempotency_key,
            expected_version=body.expected_version,
        ),
    )
    # The digest guard raises and the service rolls back, so the condition is written here —
    # outside that transaction. The ingest stays rejected.
    if (
        isinstance(result, DomainError)
        and result.code == "deployment_observation_digest_mismatch"
    ):
        record_digest_divergence(
            session,
            actor=actor,
            release_artifact_binding_id=binding_id,
            observed_artifact_digest=body.observed_artifact_digest,
            environment=body.environment,
        )
    return _raise_error(result)
```

- [ ] **7.9 — Run; expect pass.**
  `uv run pytest tests/api/test_digest_divergence_route.py tests/api/test_deployment_observations_api.py tests/services/test_reconciliation_detection_check.py`

- [ ] **7.10 — Commit.**
  `git add src/orchestrator/services/reconciliation_detection.py src/orchestrator/api/routes.py tests/services/test_reconciliation_detection_check.py tests/api/test_digest_divergence_route.py && git commit -m "AC-002: on-ingest github_check flip detection + digest_divergence recorded at the route layer on a rejected ingest"`

---

### Task 8: Detect-pass for `deploy_split_brain` (AC-003)

**Files:**
- Modify: `src/orchestrator/config.py` — add `reconcile_split_brain_stall_seconds` to `Settings` (after `dispatch_human_gate_age_out_seconds`, ~line 29)
- Modify: `src/orchestrator/services/reconciliation_detection.py` — add the detect-pass
- Modify: `src/orchestrator/api/schemas.py` — add `ReconciliationDetectResponse`
- Modify: `src/orchestrator/api/routes.py` — add `POST /api/v1/reconciliation/detect`
- Modify: `src/orchestrator/cli.py` — add `reconcile-detect`
- Modify: `tests/architecture/test_scope_guards.py` — add the route to the pinned POST inventory (the set at :48-84)
- Create: `tests/services/test_reconciliation_detect_pass.py`, `tests/api/test_reconciliation_detect_api.py`, `tests/cli/test_reconcile_detect_cli.py`

**Interfaces:**
- **Consumes:** `DetectionCounters`, `SKIPPED`, `_record`-style condition writing from Task 6; `get_settings()` (`config.py:34`); `DeploymentObservation`, `ReleaseArtifactBinding`, `WorkUnit`, `Observation` models; `TransactionClock().now(session)` (`clock.py`).
- **Produces:**
  - `DEPLOY_SPLIT_BRAIN = "deploy_split_brain"`.
  - `detect_reconciliation_conditions(session: Session, actor: ActorContext, *, stall_seconds: int) -> DetectionCounters` — the operator/runner detect-pass. Never raises.
  - `Settings.reconcile_split_brain_stall_seconds: int = 3600` (production default: above a normal verification's worst case; the AC-010 drill sets it to `1`).
  - Route `POST /api/v1/reconciliation/detect` → `ReconciliationDetectResponse(conditions_recorded, skipped_correlations, suppressed_duplicates)`; CLI `orchestrator reconcile-detect`.

**Why a detect-pass and not on-ingest** (the one approved deviation, §1.1/§14): the post-deploy unit
is minted `SUBMITTED` *inside* the ingest transaction (`deployment_observations.py:156, 250-263`),
i.e. zero seconds old — "verification stalled" cannot be true at ingest under any design.

**Rule (a) needs no runner-pushed deploy observation.** The `DeploymentObservation` row's existence
already proves the deploy succeeded (its ingest requires a `COMPLETED` implementation unit and a
digest that matches the immutable binding). Rule (b) — "the deploy nobody reported" — is the
converse and is a cheap read with no threshold.

#### Steps

- [ ] **8.1 — Failing test: both firing rules, and the non-firing in-progress case.**
  Create `tests/services/test_reconciliation_detect_pass.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import WorkUnitState
from orchestrator.persistence.models import (
    DeploymentObservation,
    ReconciliationCondition,
    WorkUnit,
)
from orchestrator.services.reconciliation_detection import (
    DetectionCounters,
    detect_reconciliation_conditions,
)
from tests.services.test_deployment_observations import (
    observe_deployment,          # records a DeploymentObservation (mints the SUBMITTED unit)
    register_release_binding,    # completed implementation unit + ReleaseArtifactBinding
)
from tests.services.test_reconciliation_detection_pr import SYSTEM, conditions, ingest


def test_stalled_post_deploy_verification_records_deploy_split_brain(
    migrated_session: Session,
) -> None:
    binding = register_release_binding(migrated_session)
    observation = observe_deployment(migrated_session, binding)
    post_deploy = migrated_session.get(WorkUnit, observation.post_deploy_work_unit_id)
    assert post_deploy is not None and post_deploy.state == WorkUnitState.SUBMITTED
    version = post_deploy.version

    counters = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)

    assert counters.conditions_recorded == 1
    rows = conditions(migrated_session)
    assert [row.condition_type for row in rows] == ["deploy_split_brain"]
    assert rows[0].work_unit_id == post_deploy.id
    assert rows[0].deployment_observation_id == observation.id
    migrated_session.refresh(post_deploy)
    assert (post_deploy.state, post_deploy.version) == (WorkUnitState.SUBMITTED, version)


def test_in_progress_verification_under_the_threshold_does_not_fire(
    migrated_session: Session,
) -> None:
    binding = register_release_binding(migrated_session)
    observe_deployment(migrated_session, binding)

    counters = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=3600)

    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_completed_post_deploy_verification_does_not_fire(migrated_session: Session) -> None:
    binding = register_release_binding(migrated_session)
    observation = observe_deployment(migrated_session, binding)
    post_deploy = migrated_session.get(WorkUnit, observation.post_deploy_work_unit_id)
    assert post_deploy is not None
    post_deploy.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    counters = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)

    assert counters == DetectionCounters()
    assert conditions(migrated_session) == []


def test_runner_reported_deploy_with_no_post_deploy_unit_fires_without_a_threshold(
    migrated_session: Session,
) -> None:
    binding = register_release_binding(migrated_session)  # deliberately never observed
    assert list(migrated_session.scalars(select(DeploymentObservation))) == []
    deploy = ingest(
        migrated_session,
        binding,                      # subject_reference = str(binding.id)
        key="deploy-unreported-1",
        facts={"deploy_status": "succeeded", "artifact_digest": binding.artifact_digest},
        observation_type="deployment",
        subject_type="release_binding",
    )

    counters = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=3600)

    assert counters.conditions_recorded == 1
    rows = conditions(migrated_session)
    assert [row.condition_type for row in rows] == ["deploy_split_brain"]
    assert rows[0].work_unit_id == binding.work_unit_id
    assert rows[0].observation_id == deploy.id


def test_detect_pass_is_idempotent_and_reports_suppressed_duplicates(
    migrated_session: Session,
) -> None:
    binding = register_release_binding(migrated_session)
    observe_deployment(migrated_session, binding)

    first = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)
    second = detect_reconciliation_conditions(migrated_session, SYSTEM, stall_seconds=0)

    assert first == DetectionCounters(conditions_recorded=1)
    assert second == DetectionCounters(suppressed_duplicates=1)
    assert len(conditions(migrated_session)) == 1
```

  Generalize the shared `ingest` helper in `tests/services/test_reconciliation_detection_pr.py` to
  take the subject: `def ingest(session, subject, *, key, facts, observed_at=OBSERVED_AT,
  observation_type="github_pr", subject_type="work_unit")` with `subject_reference=str(subject.id)`.

- [ ] **8.2 — Run; expect failure.**
  `uv run pytest tests/services/test_reconciliation_detect_pass.py -x`
  → `ImportError: cannot import name 'detect_reconciliation_conditions'`.

- [ ] **8.3 — Minimal impl: the detect-pass.**
  In `reconciliation_detection.py` (add `timedelta`, `TransactionClock`, `DeploymentObservation`
  imports):

```python
DEPLOY_SPLIT_BRAIN = "deploy_split_brain"


def detect_reconciliation_conditions(
    session: Session,
    actor: ActorContext,
    *,
    stall_seconds: int,
) -> DetectionCounters:
    """AC-003. Operator/runner-invoked; creates no work unit and sets no lifecycle state.
    Never raises — each rule is independently fail-open and counted."""
    counters = DetectionCounters()
    for detect in (_detect_stalled_verifications, _detect_unreported_deploys):
        try:
            counters += detect(session, actor, stall_seconds)
        except Exception:
            session.rollback()
            counters += SKIPPED
    return counters


def _detect_stalled_verifications(
    session: Session,
    actor: ActorContext,
    stall_seconds: int,
) -> DetectionCounters:
    now = TransactionClock().now(session)
    deadline = now - timedelta(seconds=stall_seconds)
    stalled = tuple(
        session.execute(
            select(DeploymentObservation, WorkUnit)
            .join(WorkUnit, WorkUnit.id == DeploymentObservation.post_deploy_work_unit_id)
            .where(
                WorkUnit.state == WorkUnitState.SUBMITTED,
                WorkUnit.created_at <= deadline,
            )
            .order_by(DeploymentObservation.id)
        )
    )
    counters = DetectionCounters()
    for observation, unit in stalled:
        counters += _record_split_brain(
            session,
            actor,
            work_unit_id=unit.id,
            stored_state={"state": unit.state, "created_at": unit.created_at.isoformat()},
            observed_state={
                "environment": observation.environment,
                "observed_artifact_digest": observation.observed_artifact_digest,
                "deployment_ref": observation.deployment_ref,
                "stall_seconds": stall_seconds,
            },
            detail=(
                "deployment succeeded but its post-deploy verification has not completed "
                f"within {stall_seconds}s"
            ),
            deployment_observation_id=observation.id,
        )
    return counters


def _detect_unreported_deploys(
    session: Session,
    actor: ActorContext,
    stall_seconds: int,
) -> DetectionCounters:
    del stall_seconds  # rule (b) is threshold-free: nothing was ever ingested to time out
    reported = tuple(
        session.scalars(
            select(Observation)
            .where(
                Observation.observation_type == "deployment",
                Observation.subject_type == "release_binding",
            )
            .order_by(Observation.id)
        )
    )
    counters = DetectionCounters()
    for observation in reported:
        binding = _correlated_binding(session, observation)
        if binding is None:
            counters += SKIPPED
            continue
        observed = session.scalar(
            select(DeploymentObservation.id)
            .where(DeploymentObservation.release_artifact_binding_id == binding.id)
            .limit(1)
        )
        if observed is not None:
            continue
        counters += _record_split_brain(
            session,
            actor,
            work_unit_id=binding.work_unit_id,
            stored_state={"release_artifact_binding_id": str(binding.id), "post_deploy_unit": None},
            observed_state={
                "deploy_status": observation.facts.get("deploy_status"),
                "artifact_digest": observation.facts.get("artifact_digest"),
                "environment": observation.environment,
            },
            detail="a deploy was reported for a release binding with no post-deploy verification",
            observation_id=observation.id,
        )
    return counters


def _record_split_brain(
    session: Session,
    actor: ActorContext,
    *,
    work_unit_id: uuid.UUID,
    stored_state: dict[str, Any],
    observed_state: dict[str, Any],
    detail: str,
    observation_id: uuid.UUID | None = None,
    deployment_observation_id: uuid.UUID | None = None,
) -> DetectionCounters:
    outcome = record_reconciliation_condition(
        session,
        actor=actor,
        work_unit_id=work_unit_id,
        observation_kind="deployment",
        condition_type=DEPLOY_SPLIT_BRAIN,
        stored_state=stored_state,
        observed_state=observed_state,
        detail=detail,
        observation_id=observation_id,
        deployment_observation_id=deployment_observation_id,
    )
    if isinstance(outcome, DomainError):
        return SKIPPED
    if outcome.suppressed:
        return DetectionCounters(suppressed_duplicates=1)
    return DetectionCounters(conditions_recorded=1)
```

- [ ] **8.4 — Run; expect pass.** `uv run pytest tests/services/test_reconciliation_detect_pass.py`

- [ ] **8.5 — Failing test: the API surface + the pinned route inventory.**
  Create `tests/api/test_reconciliation_detect_api.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import ReconciliationCondition
from tests.api.test_lifecycle_api import SYSTEM, WORKER


def test_detect_route_is_system_only(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/reconciliation/detect", headers=WORKER)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_detect_route_reports_counters_and_creates_no_unit(
    db_client: TestClient,
    migrated_session: Session,
) -> None:
    response = db_client.post("/api/v1/reconciliation/detect", headers=SYSTEM)

    assert response.status_code == 200
    assert response.json() == {
        "conditions_recorded": 0,
        "skipped_correlations": 0,
        "suppressed_duplicates": 0,
    }
    assert list(migrated_session.scalars(select(ReconciliationCondition))) == []
```

  and add `"/api/v1/reconciliation/detect"` to the pinned POST set in
  `tests/architecture/test_scope_guards.py` (`test_production_post_route_inventory_is_explicit`).

- [ ] **8.6 — Run; expect failure** — `404` (route absent) and the scope-guard set mismatch.

- [ ] **8.7 — Minimal impl: config, schema, route, CLI.**
  `src/orchestrator/config.py`, inside `Settings` (the AC-010 drill overrides it with
  `ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS=1`; production keeps it above a normal
  verification's worst case):

```python
    # AC-003 is time-elapsed by nature: the post-deploy unit is minted SUBMITTED inside the
    # ingest transaction, so the stall threshold is the only thing that can distinguish a
    # normal in-progress verification from a split brain.
    reconcile_split_brain_stall_seconds: int = 3600
```

  `src/orchestrator/api/schemas.py`:

```python
class ReconciliationDetectResponse(BaseModel):
    conditions_recorded: int
    skipped_correlations: int
    suppressed_duplicates: int
```

  `src/orchestrator/api/routes.py` (import `ReconciliationDetectResponse` and
  `detect_reconciliation_conditions`; place the route after the observations routes, ~734):

```python
@router.post("/reconciliation/detect", response_model=ReconciliationDetectResponse)
def reconciliation_detect(
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> object:
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may run reconciliation detection",
            None,
        )
    counters = detect_reconciliation_conditions(
        session,
        actor,
        stall_seconds=settings.reconcile_split_brain_stall_seconds,
    )
    return counters.as_dict()
```

  `src/orchestrator/cli.py` (mirrors the `list-evidence` mold — `request` + `_run`):

```python
@app.command("reconcile-detect")
def reconcile_detect(json_output: JsonOption = False) -> None:
    _run(lambda: request("POST", "/api/v1/reconciliation/detect"), json_output)
```

- [ ] **8.8 — Run; expect pass.**
  `uv run pytest tests/api/test_reconciliation_detect_api.py tests/architecture/test_scope_guards.py`

- [ ] **8.9 — Failing test then impl: CLI parity.**
  Create `tests/cli/test_reconcile_detect_cli.py` in the `tests/cli/test_status_ledger_cli.py` mold
  (a `httpx.MockTransport` bridged to `db_client`, `ORCHESTRATOR_API_TOKEN=system-token`,
  `ORCHESTRATOR_API_CREDENTIAL_KEY_ID=system-key`):

```python
def test_reconcile_detect_cli_matches_api_json(db_client, detect_transport, monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "system-token")
    monkeypatch.setenv("ORCHESTRATOR_API_CREDENTIAL_KEY_ID", "system-key")
    api = db_client.post("/api/v1/reconciliation/detect", headers=SYSTEM).json()

    result = CliRunner().invoke(app, ["reconcile-detect", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == api
```

  Run: `uv run pytest tests/cli/test_reconcile_detect_cli.py` → passes with the 8.7 CLI command.

- [ ] **8.10 — Commit.**
  `git add src/orchestrator/config.py src/orchestrator/api/schemas.py src/orchestrator/api/routes.py src/orchestrator/cli.py src/orchestrator/services/reconciliation_detection.py tests/services/test_reconciliation_detect_pass.py tests/api/test_reconciliation_detect_api.py tests/cli/test_reconcile_detect_cli.py tests/architecture/test_scope_guards.py && git commit -m "AC-003: deploy_split_brain detect-pass (stalled verification + the deploy nobody reported)"`

---

### Task 9: `recover-evidence` — lease-expired evidence attach (AC-004)

> **Read §2.1 before writing a line.** `_store_evidence:368-378` is the **only** guard against two
> supersession heads. `Evidence`'s table constraints (`models.py:341-371`) happily permit two rows
> with `supersedes_evidence_id IS NULL` for one `(revision, unit, ac)`. Two heads ⇒ `_terminal`
> **raises** (`evidence.py:853-854`) ⇒ `current_evidence` raises ⇒ the verifier can never adjudicate
> that AC **and** `_store_evidence:368` blocks every further evidence write for it. `evidence` is in
> `APPEND_ONLY_TABLES` with a `BEFORE UPDATE OR DELETE` trigger, so **the row cannot be repaired and
> the unit can never complete.** One naive recovery call permanently wedges the unit. Everything
> below exists to prevent exactly that.

**Files:**
- Modify: `src/orchestrator/services/evidence.py` — add `recover_evidence` + helpers; parameterize `_evidence_replay` (733-758) and `_evidence_race_result` (785-797) with `action`
- Modify: `src/orchestrator/api/schemas.py` — add `RecoverEvidenceCommand`
- Modify: `src/orchestrator/api/routes.py` — add `POST /api/v1/work-units/{unit_id}/attempts/{attempt}/recover-evidence`
- Modify: `src/orchestrator/cli.py` — add `recover-evidence`
- Modify: `tests/architecture/test_scope_guards.py` — pinned POST inventory
- Create: `tests/services/test_evidence_recovery.py`, `tests/api/test_recover_evidence_api.py`

**Interfaces:**
- **Consumes:** `current_evidence(session, work_package_revision_id, work_unit_id, ac_id) -> Evidence | None` (`evidence.py:152` — spans **all** attempts; there is no attempt argument, which is exactly why an attempt-`n` recovery must supersede an attempt-`n+1` head); `_validated_subject` (`evidence.py:521`, takes the `WorkUnit` row lock at :529); `_lock_idempotency_key` (`evidence.py:775`); `_validate_evidence_fields` (:575); `_event` (:815); `authorize_transition` + `TransitionGuards` (`kernel/transitions.py:73`, SYSTEM edges `CLAIMED/EXECUTING → FAILED` at :18-19); `release_claim(session, claim, *, terminal_reason)` (Task 5); `TransactionClock`; migration index `uq_evidence_unsuperseded_head` (Task 1).
- **Produces:**
  - `recover_evidence(session, *, work_package_revision_id, work_unit_id, ac_id, attempt, actor, evidence_type, stable_ref, payload, source_revision, idempotency_key, expected_version=None) -> Evidence | DomainError` — **no `lease_token` parameter**: the whole point is that the lease is gone.
  - `EVIDENCE_HEAD_LOCK_NAMESPACE = 0x57503232`.
  - Error codes: `role_forbidden`, `recovery_not_allowed`, `claim_not_found`, `lease_not_expired`, `claim_not_recoverable`, plus the inherited `evidence_subject_invalid` / `version_conflict` / `idempotency_conflict` / `evidence_chain_invalid`.
  - Event action `"evidence.recovered"`; evidence `payload["recovery"] = {"reason": "recovered_from_expired_lease", "claim_id": …, "attempt": n, "recovered_by": …}`.
  - Route + CLI `orchestrator recover-evidence <unit_id> <attempt> --data '{…}'`.

**What AC-004 does and does not promise** (§2, and the retraction in §14.3): attaching the evidence
**without re-executing the work**, and the worker **still cannot transition to `completed`**. It
does *not* promise completion without a new attempt — `FAILED` has no edge to `SUBMITTED`, and only
`WORKER_EDGES` reach `SUBMITTED` (`transitions.py:29`). Attempt *n+1* short-circuits; it does not
disappear.

**Concurrency, stated exactly.** Recovery takes, in this order: (1) `pg_advisory_xact_lock` on
`(work_unit_id, ac_id)` — the head key; (2) the idempotency-key advisory lock; (3) the `WorkUnit`
row lock via `_validated_subject`. It then re-reads `current_evidence` **under those locks**. The
`WorkUnit` row lock is the mutual exclusion that actually serializes recovery against a concurrent
`_store_evidence` submit (both take it, `evidence.py:529`); the head lock is what serializes two
concurrent recoveries and any future writer that forgets the row lock. Check-then-insert without
them is TOCTOU-racy and the losing race writes the second head.

#### Steps

- [ ] **9.1 — Failing test: the headline — recovery can never produce two heads.**
  Create `tests/services/test_evidence_recovery.py`:

```python
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.errors import DomainError
from orchestrator.kernel.states import ActorRole, WorkUnitState
from orchestrator.persistence.models import Claim, Event, Evidence, WorkUnit
from orchestrator.services.claims import LeaseGrant, claim_unit
from orchestrator.services.evidence import (
    append_evidence,
    current_evidence,
    record_adjudication,
    recover_evidence,
)
from orchestrator.services.lifecycle import ActorContext, TransitionCommand, transition_unit
from tests.services.test_claims import worker
from tests.services.test_dependencies import register_unit
from tests.services.test_reclaim import expire

SYSTEM = ActorContext("system", ActorRole.SYSTEM)
VERIFIER = ActorContext("verifier-1", ActorRole.VERIFIER)


def recovery_kwargs(unit: WorkUnit, attempt: int, key: str) -> dict[str, Any]:
    return {
        "work_package_revision_id": unit.work_package_revision_id,
        "work_unit_id": unit.id,
        "ac_id": "ac-1",
        "attempt": attempt,
        "actor": SYSTEM,
        "evidence_type": "test",
        "stable_ref": "artifact://recovered-result",
        "payload": {"exit_code": 0},
        "source_revision": "abc123",
        "idempotency_key": key,
    }


def expired_claim(session: Session, unit: WorkUnit) -> LeaseGrant:
    grant = claim_unit(session, unit.id, worker(), f"claim-{unit.id}")
    assert isinstance(grant, LeaseGrant)
    expire(session, grant.claim_id)
    session.expire_all()
    return grant


def heads(session: Session, unit: WorkUnit) -> list[Evidence]:
    return list(
        session.scalars(
            select(Evidence).where(
                Evidence.work_unit_id == unit.id,
                Evidence.ac_id == "ac-1",
                Evidence.supersedes_evidence_id.is_(None),
            )
        )
    )


def test_recovery_twice_never_creates_a_second_head(migrated_session: Session, ready_unit) -> None:
    grant = expired_claim(migrated_session, ready_unit)

    first = recover_evidence(migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-1"))
    second = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-2")
    )

    assert isinstance(first, Evidence)
    assert isinstance(second, Evidence)
    assert second.supersedes_evidence_id == first.id
    assert len(heads(migrated_session, ready_unit)) == 1
    # _terminal still resolves — the unit is not wedged.
    terminal = current_evidence(
        migrated_session, ready_unit.work_package_revision_id, ready_unit.id, "ac-1"
    )
    assert isinstance(terminal, Evidence)
    assert terminal.id == second.id


def test_recovery_supersedes_an_existing_head(migrated_session: Session, ready_unit) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-head")
    assert isinstance(grant, LeaseGrant)
    existing = append_evidence(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        attempt=grant.attempt,
        actor=worker(),
        lease_token=grant.lease_token,
        evidence_type="test",
        stable_ref="artifact://partial",
        payload=None,
        source_revision="abc123",
        idempotency_key="evidence-head",
    )
    assert isinstance(existing, Evidence)
    expire(migrated_session, grant.claim_id)
    migrated_session.expire_all()

    recovered = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-head")
    )

    assert isinstance(recovered, Evidence)
    assert recovered.supersedes_evidence_id == existing.id
    assert len(heads(migrated_session, ready_unit)) == 1
    assert current_evidence(
        migrated_session, ready_unit.work_package_revision_id, ready_unit.id, "ac-1"
    ).id == recovered.id


def test_a_second_null_supersedes_head_is_structurally_impossible(
    migrated_session: Session, ready_unit
) -> None:
    """Defense in depth (§2.1): even a direct INSERT cannot mint a second head."""
    grant = expired_claim(migrated_session, ready_unit)
    first = recover_evidence(migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-x"))
    assert isinstance(first, Evidence)

    with pytest.raises(IntegrityError):
        migrated_session.add(
            Evidence(
                id=uuid.uuid4(),
                work_package_revision_id=ready_unit.work_package_revision_id,
                work_unit_id=ready_unit.id,
                ac_id="ac-1",
                attempt=grant.attempt,
                evidence_type="test",
                stable_ref="artifact://second-head",
                payload=None,
                source_revision="abc123",
                recorded_by="system",
                event_id=uuid.uuid4(),
                idempotency_key="second-head",
                supersedes_evidence_id=None,
            )
        )
        migrated_session.flush()
    migrated_session.rollback()


def test_replay_of_the_same_idempotency_key_returns_the_same_row(
    migrated_session: Session, ready_unit
) -> None:
    grant = expired_claim(migrated_session, ready_unit)

    first = recover_evidence(migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-r"))
    replay = recover_evidence(migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-r"))

    assert isinstance(first, Evidence)
    assert isinstance(replay, Evidence)
    assert replay.id == first.id
    assert (
        len(
            list(
                migrated_session.scalars(
                    select(Evidence).where(Evidence.work_unit_id == ready_unit.id)
                )
            )
        )
        == 1
    )
```

- [ ] **9.2 — Run; expect failure.**
  `uv run pytest tests/services/test_evidence_recovery.py -x`
  → `ImportError: cannot import name 'recover_evidence' from 'orchestrator.services.evidence'`.

- [ ] **9.3 — Minimal impl: `recover_evidence`.**
  In `src/orchestrator/services/evidence.py` — add `WorkUnitState` is already imported (:13); add
  `TransitionGuards, authorize_transition` from `orchestrator.kernel.transitions` and
  `release_claim` from `orchestrator.services.claims`:

```python
EVIDENCE_HEAD_LOCK_NAMESPACE = 0x57503232
RECOVERY_REASON = "recovered_from_expired_lease"


def recover_evidence(
    session: Session,
    *,
    work_package_revision_id: uuid.UUID,
    work_unit_id: uuid.UUID,
    ac_id: str,
    attempt: int,
    actor: ActorContext,
    evidence_type: str,
    stable_ref: str | None,
    payload: dict[str, Any] | None,
    source_revision: str,
    idempotency_key: str,
    expected_version: int | None = None,
) -> Evidence | DomainError:
    """AC-004. Attaches evidence produced by an attempt whose lease expired before it could
    submit. SYSTEM/operator only — never the expired worker, who has no valid lease left.

    This bypasses _store_evidence (whose _validate_attempt rejects a SYSTEM actor, a released
    claim, and an expired lease), and _store_evidence:368-378 is the ONLY code preventing two
    supersession heads. So this function must never write a second NULL-supersedes head: it
    resolves the current head under the locks and supersedes it. Two heads make _terminal raise,
    and `evidence` is append-only — the row could never be repaired and the unit could never
    complete.
    """
    command = {
        "ac_id": ac_id,
        "actor_id": actor.actor_id,
        "actor_role": actor.role,
        "attempt": attempt,
        "context_snapshot_id": None,
        "evidence_type": evidence_type,
        "expected_version": expected_version,
        "payload": payload,
        "recovery": RECOVERY_REASON,
        "source_revision": source_revision,
        "stable_ref": stable_ref,
        "work_package_revision_id": str(work_package_revision_id),
        "work_unit_id": str(work_unit_id),
    }
    try:
        _authorize_recovery(actor)
        # Order matters: head lock, then idempotency lock, then the WorkUnit row lock (taken by
        # _validated_subject). The row lock is what serializes this against a concurrent submit
        # from attempt n+1; the head lock serializes two concurrent recoveries.
        _lock_evidence_head(session, work_unit_id, ac_id)
        _lock_idempotency_key(session, idempotency_key)
        unit, _revision = _validated_subject(session, work_package_revision_id, work_unit_id, ac_id)
        replay = _evidence_replay(session, idempotency_key, command, action="evidence.recovered")
        if replay is not None:
            session.commit()
            return replay
        if expected_version is not None and unit.version != expected_version:
            raise DomainError(
                "version_conflict",
                "work unit version has changed",
                "reload",
                current_state=unit.state,
                current_version=unit.version,
            )
        _validate_evidence_fields(stable_ref, payload, evidence_type, source_revision)
        if WorkUnitState(unit.state) in {WorkUnitState.COMPLETED, WorkUnitState.CANCELLED}:
            raise DomainError(
                "recovery_not_allowed",
                "completed and cancelled work units may not receive recovered evidence",
                None,
            )
        now = TransactionClock().now(session)
        claim = _recoverable_claim(session, unit, attempt, now)
        if claim.released_at is None:
            # The AC's actual scenario: the lease lapsed just before submit and nothing has
            # reclaimed it. Recovery is the releaser — one writer of released_at/terminal_reason.
            release_claim(session, claim, terminal_reason="lease_expired")
            _system_fail(session, unit, actor, now, idempotency_key, claim)
        previous = current_evidence(session, work_package_revision_id, work_unit_id, ac_id)
        event_id = uuid.uuid4()
        row = Evidence(
            work_package_revision_id=work_package_revision_id,
            work_unit_id=work_unit_id,
            ac_id=ac_id,
            attempt=attempt,
            evidence_type=evidence_type,
            stable_ref=stable_ref,
            payload={
                **(payload or {}),
                "recovery": {
                    "reason": RECOVERY_REASON,
                    "claim_id": str(claim.id),
                    "attempt": attempt,
                    "recovered_by": actor.actor_id,
                },
            },
            source_revision=source_revision,
            recorded_by=actor.actor_id,
            recorded_at=now,
            event_id=event_id,
            idempotency_key=idempotency_key,
            supersedes_evidence_id=previous.id if previous is not None else None,
            context_snapshot_id=None,
        )
        session.add(row)
        session.flush()
        session.add(
            _event(
                event_id,
                now,
                actor,
                "evidence.recovered",
                "evidence",
                row.id,
                command,
                idempotency_key,
            )
        )
        session.commit()
        return row
    except DomainError as error:
        session.rollback()
        return error
    except IntegrityError as error:
        session.rollback()
        return _evidence_race_result(
            session, idempotency_key, command, error, action="evidence.recovered"
        )
    except Exception:
        session.rollback()
        raise


def _authorize_recovery(actor: ActorContext) -> None:
    if actor.role not in {ActorRole.SYSTEM, ActorRole.HUMAN}:
        raise DomainError(
            "role_forbidden",
            "only the system actor or a human operator may recover evidence",
            None,
        )


def _recoverable_claim(
    session: Session,
    unit: WorkUnit,
    attempt: int,
    now: datetime,
) -> Claim:
    claim = session.scalar(
        select(Claim)
        .where(Claim.work_unit_id == unit.id, Claim.attempt == attempt)
        .with_for_update()
    )
    if claim is None:
        raise DomainError("claim_not_found", "work unit has no claim for that attempt", None)
    if claim.lease_expires_at > now:
        raise DomainError("lease_not_expired", "claim lease has not expired", None)
    if claim.released_at is not None and claim.terminal_reason != "lease_expired":
        raise DomainError(
            "claim_not_recoverable",
            "claim was released for a reason other than lease expiry",
            None,
        )
    return claim


def _system_fail(
    session: Session,
    unit: WorkUnit,
    actor: ActorContext,
    now: datetime,
    idempotency_key: str,
    claim: Claim,
) -> None:
    """CLAIMED/EXECUTING -> FAILED on a SYSTEM edge (transitions.py:18-19), attributed to the
    recovering actor but performed as SYSTEM (the claims.py:91 pattern). Mints NO new attempt:
    attempt_count is untouched, so requeue/reclaim still has its budget."""
    source = WorkUnitState(unit.state)
    if source not in {WorkUnitState.CLAIMED, WorkUnitState.EXECUTING}:
        return
    authorize_transition(source, WorkUnitState.FAILED, ActorRole.SYSTEM, TransitionGuards())
    unit.state = WorkUnitState.FAILED
    unit.version += 1
    session.add(
        Event(
            id=uuid.uuid4(),
            occurred_at=now,
            actor_id=actor.actor_id,
            action="work_unit.transitioned",
            subject_type="work_unit",
            subject_id=unit.id,
            from_state=source,
            to_state=WorkUnitState.FAILED,
            payload={
                "attempt": claim.attempt,
                "expired_claim_id": str(claim.id),
                "reason": RECOVERY_REASON,
                "version": unit.version,
            },
            correlation_id=uuid.uuid4(),
            idempotency_key=f"{idempotency_key}:failed",
        )
    )
    session.flush()


def _lock_evidence_head(session: Session, work_unit_id: uuid.UUID, ac_id: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:head_key))"),
        {
            "namespace": EVIDENCE_HEAD_LOCK_NAMESPACE,
            "head_key": f"{work_unit_id}:{ac_id}",
        },
    )
```

  And parameterize the two replay helpers (evidence.py:733, :785) so recovery reuses them instead of
  duplicating them:

```python
def _evidence_replay(
    session: Session,
    idempotency_key: str,
    command: dict[str, object],
    *,
    action: str = "evidence.recorded",
) -> Evidence | None:
    ...
    if (
        event is None
        or event.action != action
        ...


def _evidence_race_result(
    session: Session,
    idempotency_key: str,
    command: dict[str, object],
    error: IntegrityError,
    *,
    action: str = "evidence.recorded",
) -> Evidence | DomainError:
    try:
        replay = _evidence_replay(session, idempotency_key, command, action=action)
    ...
```

- [ ] **9.4 — Run; expect pass.**
  `uv run pytest tests/services/test_evidence_recovery.py`
  If `test_a_second_null_supersedes_head_is_structurally_impossible` fails with *no* `IntegrityError`,
  the Task-1 migration is missing `uq_evidence_unsuperseded_head` — **stop and add it there**; the whole
  wedge argument in §2.1 depends on it.

- [ ] **9.5 — Failing test: preconditions, and the worker's road to `completed` stays closed.**
  Append to `tests/services/test_evidence_recovery.py`:

```python
def test_recovery_refuses_a_live_lease(migrated_session: Session, ready_unit) -> None:
    grant = claim_unit(migrated_session, ready_unit.id, worker(), "claim-live")
    assert isinstance(grant, LeaseGrant)

    result = recover_evidence(migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-live"))

    assert isinstance(result, DomainError)
    assert result.code == "lease_not_expired"


def test_recovery_refuses_the_expired_worker(migrated_session: Session, ready_unit) -> None:
    grant = expired_claim(migrated_session, ready_unit)
    command = recovery_kwargs(ready_unit, grant.attempt, "rec-worker") | {"actor": worker()}

    result = recover_evidence(migrated_session, **command)

    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_recovery_refuses_a_completed_unit(migrated_session: Session, ready_unit) -> None:
    grant = expired_claim(migrated_session, ready_unit)
    ready_unit.state = WorkUnitState.COMPLETED
    migrated_session.commit()

    result = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-completed")
    )

    assert isinstance(result, DomainError)
    assert result.code == "recovery_not_allowed"


def test_recovery_releases_the_claim_and_system_fails_without_a_new_attempt(
    migrated_session: Session, ready_unit
) -> None:
    grant = expired_claim(migrated_session, ready_unit)

    recovered = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-release")
    )

    assert isinstance(recovered, Evidence)
    migrated_session.expire_all()
    claim = migrated_session.get(Claim, grant.claim_id)
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    assert claim is not None and claim.released_at is not None
    assert claim.terminal_reason == "lease_expired"
    assert unit is not None and unit.state == WorkUnitState.FAILED
    assert unit.attempt_count == 1  # no new attempt was minted
    assert recovered.attempt == grant.attempt
    assert recovered.payload["recovery"]["reason"] == "recovered_from_expired_lease"
    assert recovered.payload["recovery"]["claim_id"] == str(grant.claim_id)


def test_recovery_admits_after_a_reclaim_already_released_the_claim(
    migrated_session: Session, ready_unit
) -> None:
    grant = expired_claim(migrated_session, ready_unit)
    claim = migrated_session.get(Claim, grant.claim_id)
    assert claim is not None
    claim.released_at = claim.lease_expires_at
    claim.terminal_reason = "lease_expired"
    ready_unit.state = WorkUnitState.FAILED
    migrated_session.commit()

    recovered = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-reclaimed")
    )

    assert isinstance(recovered, Evidence)
    assert len(heads(migrated_session, ready_unit)) == 1


def test_worker_cannot_complete_and_attempt_two_submits_without_redoing_the_work(
    migrated_session: Session, ready_unit
) -> None:
    grant = expired_claim(migrated_session, ready_unit)
    recovered = recover_evidence(
        migrated_session, **recovery_kwargs(ready_unit, grant.attempt, "rec-flow")
    )
    assert isinstance(recovered, Evidence)
    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    assert unit is not None and unit.state == WorkUnitState.FAILED

    # requeue: SYSTEM FAILED -> READY (transitions.py:22), attempts not exhausted.
    transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.READY,
            actor=SYSTEM,
            expected_version=unit.version,
            idempotency_key="requeue-flow",
        ),
    )
    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    next_grant = claim_unit(migrated_session, unit.id, worker(), "claim-flow-2")
    assert isinstance(next_grant, LeaseGrant)
    assert next_grant.attempt == 2

    # Attempt 2 does NOT redo the job: it writes no new evidence for ac-1.
    for target in (WorkUnitState.EXECUTING, WorkUnitState.SUBMITTED):
        migrated_session.expire_all()
        unit = migrated_session.get(WorkUnit, ready_unit.id)
        transition_unit(
            migrated_session,
            TransitionCommand(
                unit_id=unit.id,
                target=target,
                actor=worker(),
                expected_version=unit.version,
                idempotency_key=f"flow-{target}",
                attempt=next_grant.attempt,
                lease_token=next_grant.lease_token,
            ),
        )
    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    assert unit.state == WorkUnitState.SUBMITTED
    assert (
        len(
            list(
                migrated_session.scalars(
                    select(Evidence).where(Evidence.work_unit_id == unit.id)
                )
            )
        )
        == 1
    )

    # The worker still cannot declare completion — SUBMITTED -> COMPLETED is not a WORKER edge.
    with pytest.raises(DomainError) as error:
        transition_unit(
            migrated_session,
            TransitionCommand(
                unit_id=unit.id,
                target=WorkUnitState.COMPLETED,
                actor=worker(),
                expected_version=unit.version,
                idempotency_key="flow-worker-complete",
                attempt=next_grant.attempt,
                lease_token=next_grant.lease_token,
            ),
        )
    assert error.value.code == "role_forbidden"

    # The verifier adjudicates the RECOVERED evidence and the unit completes: not wedged.
    adjudication = record_adjudication(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=VERIFIER,
        rationale="recovered evidence satisfies ac-1",
        idempotency_key="flow-adjudication",
        evidence_id=recovered.id,
    )
    assert not isinstance(adjudication, DomainError)
    migrated_session.expire_all()
    unit = migrated_session.get(WorkUnit, ready_unit.id)
    result = transition_unit(
        migrated_session,
        TransitionCommand(
            unit_id=unit.id,
            target=WorkUnitState.COMPLETED,
            actor=VERIFIER,
            expected_version=unit.version,
            idempotency_key="flow-complete",
        ),
    )
    assert result.state == WorkUnitState.COMPLETED
```

- [ ] **9.6 — Run; expect pass.** `uv run pytest tests/services/test_evidence_recovery.py`
  A failure in the last test at `record_adjudication` or the `COMPLETED` transition means
  `current_evidence`/`_terminal` is raising — i.e. a second head was written. That is the wedge;
  do not work around it in the test.

- [ ] **9.7 — Failing test: the API + CLI surface.**
  Create `tests/api/test_recover_evidence_api.py`:

```python
def test_recover_evidence_route_is_denied_to_the_worker(db_client: TestClient, ...) -> None:
    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/attempts/1/recover-evidence", json=body, headers=WORKER
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_forbidden"


def test_recover_evidence_route_attaches_to_the_prior_attempt(
    db_client: TestClient, migrated_session: Session
) -> None:
    ...  # claim, expire the lease via SQL, then:
    response = db_client.post(
        f"/api/v1/work-units/{unit_id}/attempts/1/recover-evidence", json=body, headers=SYSTEM
    )
    assert response.status_code == 201
    assert response.json()["attempt"] == 1
    assert response.json()["supersedes_evidence_id"] is None
```

  and add `"/api/v1/work-units/{unit_id}/attempts/{attempt}/recover-evidence"` to the pinned POST
  set in `tests/architecture/test_scope_guards.py`.

- [ ] **9.8 — Run; expect failure** — `404` on the route, and the scope-guard set mismatch.

- [ ] **9.9 — Minimal impl: schema, route, CLI.**
  `src/orchestrator/api/schemas.py` (note: **no `lease_token`** — that is the point):

```python
class RecoverEvidenceCommand(CommandBase):
    work_package_revision_id: UUID
    ac_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    stable_ref: str | None = None
    payload: dict[str, Any] | None = None
    source_revision: str = Field(min_length=1)
```

  `src/orchestrator/api/routes.py` (import `recover_evidence` into the existing
  `orchestrator.services.evidence` import block at :125-130; place the route beside the evidence
  routes at ~1134):

```python
@router.post(
    "/work-units/{unit_id}/attempts/{attempt}/recover-evidence",
    response_model=EvidenceResponse,
    status_code=201,
)
def recover_evidence_route(
    unit_id: UUID,
    attempt: int,
    body: RecoverEvidenceCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    return _raise_error(
        recover_evidence(
            session,
            work_unit_id=unit_id,
            attempt=attempt,
            actor=actor,
            **body.model_dump(exclude={"expected_version"}),
            expected_version=body.expected_version,
        )
    )
```

  `src/orchestrator/cli.py` (the `append-evidence` mold):

```python
@app.command("recover-evidence")
def recover_evidence(
    unit_id: str,
    attempt: Annotated[int, typer.Option("--attempt", min=1)],
    data: DataOption,
    json_output: JsonOption = False,
) -> None:
    _post_data(
        f"/api/v1/work-units/{unit_id}/attempts/{attempt}/recover-evidence", data, json_output
    )
```

- [ ] **9.10 — Run; expect pass.**
  `uv run pytest tests/api/test_recover_evidence_api.py tests/architecture/test_scope_guards.py tests/services/test_evidence_recovery.py tests/services/test_evidence.py`
  (`tests/services/test_evidence.py` is the regression gate on the `_evidence_replay` /
  `_evidence_race_result` signature change.)

- [ ] **9.11 — Full gate.** `make check` — read the *collected-test count*, not the exit code
  (exit 5 is swallowed by the vendored Makefile). Then `/code-review` on the diff.

- [ ] **9.12 — Commit.**
  `git add src/orchestrator/services/evidence.py src/orchestrator/api/schemas.py src/orchestrator/api/routes.py src/orchestrator/cli.py tests/services/test_evidence_recovery.py tests/api/test_recover_evidence_api.py tests/architecture/test_scope_guards.py && git commit -m "AC-004: recover-evidence attaches an expired-lease attempt's evidence by superseding the current head (never a second head)"`
