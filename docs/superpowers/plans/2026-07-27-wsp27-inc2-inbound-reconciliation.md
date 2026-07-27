# WS-P2.7 Increment 2 — Inbound Tracker Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A human's completion of a bound tracker card becomes an append-only `tracker_state_divergence` reconciliation condition an operator resolves — never a blind lifecycle transition, and the tracker never canonical.

**Architecture:** Two halves, mirroring Increment 1. In-process (orchestrator): a new closed-vocabulary condition type, a SYSTEM-only detection function that compares observed tracker state against canonical state, and a dedicated SYSTEM-only detect route — all reusing the append-only `reconciliation_conditions` machinery and the generic `/review` operator surface (no operator-surface code change). Out-of-process (`src/tracker_projection_adapter/`): a new `reconcile` command that reads each bound Todoist item's completion state and reports it; the orchestrator owns the divergence rule (dumb adapter). The tracker does **zero** work inside `src/orchestrator/`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, Postgres, `httpx`, `typer`, pytest.

**Spec:** `docs/superpowers/specs/2026-07-27-wsp27-inc2-inbound-reconciliation-design.md`

## Global Constraints

- **Tracker is never canonical** (program exit criterion #9). Inbound edits are signals surfaced for a human, never applied blindly, never authority.
- **No Todoist/tracker call inside `src/orchestrator/`.** All Todoist I/O stays in `src/tracker_projection_adapter/`. The `todoist` import ban and `test_application_has_no_external_mutation_integrations` still hold.
- **Detection is append-only, fail-open, never auto-transitions, never raises past the caller.** Each item wrapped in `try/except Exception: session.rollback(); counters += SKIPPED`. It never writes `work_units` and never transitions.
- **Divergence rule (the one rule):** fire iff `observed_completed AND unit.state ∉ {completed, cancelled}`. That closed set is the exact mirror of the adapter's outbound `TERMINAL_STATES` and is protected by a sync-guard test.
- **Closed vocabularies extended via migration + CHECK, never loosened.** New members go in **both** the model `__table_args__` and the Alembic migration's frozen inline copy.
- **`RECONCILIATION_OBSERVATION_KINDS` and `RECONCILIATION_CONDITION_TYPES` stay ≥2 members**, so the existing `f"col IN {TUPLE!r}"` CHECK construction remains valid (the single-element trailing-comma footgun does not apply).
- **New `/api/v1` POST route obligations:** add the exact path to the `test_production_post_route_inventory_is_explicit` set literal; add a `tests/idempotency/matrix.py` `MatrixRow`; a JSON response_model auto-satisfies the every-success-has-a-JSON-schema invariant (do **not** add it to `NON_JSON_SUCCESS_PATHS`); `CommandBase` inheritance auto-satisfies the idempotency-key/expected-version invariant.
- **Never store tracker text** as facts. Store normalized state only (`observed_completed`, canonical/projected state strings) — never card titles/descriptions.
- **`make check` must be green on a clean tree** (Postgres + `SECURITY_STANDARDS_DIR`; read the collected-test count, not just exit code). The whole-repo architecture scans (route inventory, idempotency matrix, `test_unreachable_guards`, `test_wsp21_invariant_scan`, ws32/ws33 word guards) run only in a full `make check`.
- **`ORCHESTRATOR_DISPATCH_ENABLED=false` in production stays false;** this workstream does not touch dispatch.
- **Adapter import discipline:** any new import in the adapter must have its top-level name in `ALLOWED_TOP_LEVEL` (`tests/architecture/test_tracker_projection_adapter_isolation.py`): `{httpx, typer, tracker_projection_adapter, json, os, re, dataclasses, datetime, typing, __future__}`. Do **not** add new third-party deps and do **not** `import uuid` in the adapter.
- **Commit after each task.** Run focused tests in the **foreground** only — never a background full-suite + Monitor.

---

## File map

**Orchestrator (in-process):**
- Modify `src/orchestrator/persistence/models.py` — two vocab tuples (Task 1).
- Create `migrations/versions/0019_wsp27_tracker_reconciliation.py` — drop/recreate two CHECKs (Task 1).
- Modify `src/orchestrator/services/reconciliation_detection.py` — new constants, `ObservedTrackerItem`, `detect_tracker_conditions`, `_detect_tracker_item`, `_record_tracker`, `_tracker_binding` (Task 2).
- Modify `src/orchestrator/api/schemas.py` — `TrackerReconciliationDetectItem`, `TrackerReconciliationDetectCommand` (Task 3).
- Modify `src/orchestrator/api/routes.py` — the `tracker_reconciliation_detect` route (Task 3).
- Modify `tests/architecture/test_scope_guards.py` — add the route to the POST inventory (Task 3).
- Modify `tests/idempotency/matrix.py` — add the `MatrixRow` (Task 3).

**Adapter (out-of-process):**
- Modify `src/tracker_projection_adapter/orchestrator_client.py` — two-endpoint write surface + `report_tracker_reconciliation` (Task 4).
- Modify `src/tracker_projection_adapter/tracker.py` — `item_completed` read method + `_get` (Task 5).
- Modify `src/tracker_projection_adapter/cli.py` — `reconcile` command (Task 6).
- Create `scripts/run-tracker-reconciliation.sh` — launcher (Task 6).

**Docs:**
- Create `docs/decisions/0004-inbound-tracker-reconciliation.md` (Task 7).

---

## Task 1: Reconciliation vocabulary + migration 0019

**Files:**
- Modify: `src/orchestrator/persistence/models.py:1064-1072`
- Create: `migrations/versions/0019_wsp27_tracker_reconciliation.py`
- Test: `tests/persistence/test_tracker_reconciliation_vocab.py` (create)

**Interfaces:**
- Produces: `RECONCILIATION_OBSERVATION_KINDS` now includes `"tracker"`; `RECONCILIATION_CONDITION_TYPES` now includes `"tracker_state_divergence"`. The DB CHECKs `ck_reconciliation_conditions_observation_kind` and `ck_reconciliation_conditions_type` accept the new values.

- [ ] **Step 1: Write the failing test**

Create `tests/persistence/test_tracker_reconciliation_vocab.py`. It asserts the constants include the new members and that the DB CHECK accepts a `tracker` / `tracker_state_divergence` row while rejecting a bogus one. Use the repo's existing session fixture (mirror any test under `tests/persistence/` or `tests/services/test_reconciliation*` for the `session` fixture and a work-unit factory).

```python
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from orchestrator.persistence.models import (
    RECONCILIATION_CONDITION_TYPES,
    RECONCILIATION_OBSERVATION_KINDS,
    ReconciliationCondition,
)


def test_tracker_vocab_members_present():
    assert "tracker" in RECONCILIATION_OBSERVATION_KINDS
    assert "tracker_state_divergence" in RECONCILIATION_CONDITION_TYPES


def _condition(**overrides):
    base = dict(
        work_unit_id=overrides.pop("work_unit_id"),
        observation_kind="tracker",
        condition_type="tracker_state_divergence",
        stored_state={},
        observed_state={},
        lineage_hash="sha256:x",
        normalized_divergence_hash="sha256:y",
        detail="d",
        idempotency_key=str(uuid.uuid4()),
        event_id=overrides.pop("event_id"),
    )
    base.update(overrides)
    return ReconciliationCondition(**base)


def test_check_accepts_tracker_values(session, work_unit_and_event):
    unit_id, event_id = work_unit_and_event
    session.add(_condition(work_unit_id=unit_id, event_id=event_id))
    session.commit()  # no IntegrityError


def test_check_rejects_unknown_condition_type(session, work_unit_and_event):
    unit_id, event_id = work_unit_and_event
    session.add(
        _condition(work_unit_id=unit_id, event_id=event_id, condition_type="not_a_real_type")
    )
    with pytest.raises(IntegrityError):
        session.commit()
```

> Implementer note: `work_unit_and_event` is a fixture you add (a committed `WorkUnit` + `Event` returning their ids). Copy the smallest such factory already used by the reconciliation-condition tests; do not invent a new pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/persistence/test_tracker_reconciliation_vocab.py -v`
Expected: FAIL — `assert "tracker" in RECONCILIATION_OBSERVATION_KINDS` fails (member absent), and the CHECK still rejects `tracker`.

- [ ] **Step 3: Add the vocab members (model)**

In `src/orchestrator/persistence/models.py`, extend the two tuples (keep them ≥2 members so the existing `!r` CHECK stays valid):

```python
RECONCILIATION_OBSERVATION_KINDS = ("github_pr", "github_check", "deployment", "tracker")
RECONCILIATION_CONDITION_TYPES = (
    "external_merge_alarm",
    "pr_state_divergence",
    "check_result_flip",
    "deploy_split_brain",
    "digest_divergence",
    "tracker_state_divergence",
)
```

Leave the `ReconciliationCondition.__table_args__` CHECKs unchanged — they interpolate the tuples with `!r`, so they pick up the new members automatically.

- [ ] **Step 4: Write migration 0019**

Create `migrations/versions/0019_wsp27_tracker_reconciliation.py`. It drops and recreates the two named CHECKs with frozen inline copies of the post-change tuples (migrations never import model constants). Both tuples are ≥2 members, so `{TUPLE!r}` is valid — matching the model construction.

```python
"""wsp27 tracker reconciliation vocab

Revision ID: 0019_wsp27_tracker_reconciliation
Revises: 0018_wsp27_tracker_bindings
"""

from __future__ import annotations

from alembic import op

revision = "0019_wsp27_tracker_reconciliation"
down_revision = "0018_wsp27_tracker_bindings"
branch_labels = None
depends_on = None

# Frozen copies of orchestrator.persistence.models after this migration.
# Migrations never import model constants (established convention, see 0014).
OBSERVATION_KINDS = ("github_pr", "github_check", "deployment", "tracker")
CONDITION_TYPES = (
    "external_merge_alarm",
    "pr_state_divergence",
    "check_result_flip",
    "deploy_split_brain",
    "digest_divergence",
    "tracker_state_divergence",
)
# Pre-migration copies (for downgrade).
_OLD_OBSERVATION_KINDS = ("github_pr", "github_check", "deployment")
_OLD_CONDITION_TYPES = (
    "external_merge_alarm",
    "pr_state_divergence",
    "check_result_flip",
    "deploy_split_brain",
    "digest_divergence",
)


def _swap(name: str, column: str, new: tuple[str, ...]) -> None:
    op.drop_constraint(name, "reconciliation_conditions", type_="check")
    op.create_check_constraint(name, "reconciliation_conditions", f"{column} IN {new!r}")


def upgrade() -> None:
    _swap("ck_reconciliation_conditions_observation_kind", "observation_kind", OBSERVATION_KINDS)
    _swap("ck_reconciliation_conditions_type", "condition_type", CONDITION_TYPES)


def downgrade() -> None:
    _swap(
        "ck_reconciliation_conditions_observation_kind",
        "observation_kind",
        _OLD_OBSERVATION_KINDS,
    )
    _swap("ck_reconciliation_conditions_type", "condition_type", _OLD_CONDITION_TYPES)
```

> Verify the exact current CHECK constraint names against `0018`/the model (`ck_reconciliation_conditions_observation_kind`, `ck_reconciliation_conditions_type`) before running — a name typo makes `drop_constraint` fail.

- [ ] **Step 5: Apply the migration to the test DB and run the tests**

Run: `.venv/bin/alembic upgrade head && .venv/bin/pytest tests/persistence/test_tracker_reconciliation_vocab.py -v`
Expected: `alembic` reports `0019_wsp27_tracker_reconciliation` applied; all three tests PASS.

- [ ] **Step 6: Verify downgrade round-trips**

Run: `.venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head`
Expected: both succeed with no error (drops/recreates cleanly).

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/persistence/models.py migrations/versions/0019_wsp27_tracker_reconciliation.py tests/persistence/test_tracker_reconciliation_vocab.py
git commit -m "feat(wsp27): add tracker reconciliation vocabulary (migration 0019)"
```

---

## Task 2: Tracker detection service

**Files:**
- Modify: `src/orchestrator/services/reconciliation_detection.py`
- Test: `tests/services/test_tracker_reconciliation_detection.py` (create)
- Test: `tests/architecture/test_tracker_closed_states_sync.py` (create — the vocabulary-coupling guard)

**Interfaces:**
- Consumes: `record_reconciliation_condition`, `ConditionCommand`, `ConditionOutcome`, `DetectionCounters`, `SKIPPED` (all already in this module or `services/reconciliation.py`); `UnitTrackerBinding`, `WorkUnit` (models); `select` (sqlalchemy).
- Produces:
  - `TRACKER_OBSERVATION_KIND = "tracker"`, `TRACKER_STATE_DIVERGENCE = "tracker_state_divergence"`, `TRACKER_CLOSED_STATES = frozenset({"completed", "cancelled"})`
  - `@dataclass(frozen=True) class ObservedTrackerItem: tracker_system: str; external_item_id: str; observed_completed: bool`
  - `detect_tracker_conditions(session: Session, actor: ActorContext, *, observed_states: list[ObservedTrackerItem]) -> DetectionCounters`

- [ ] **Step 1: Write the failing detection tests**

Create `tests/services/test_tracker_reconciliation_detection.py`. Reuse the surrounding reconciliation-detection tests' fixtures for a committed `WorkUnit` in a given state plus its `UnitTrackerBinding` (add a small helper `make_unit_with_binding(session, state, external_item_id="tid-1")` mirroring existing factories). SYSTEM `ActorContext` comes from the same helper the existing detection tests use.

```python
from orchestrator.kernel.states import WorkUnitState
from orchestrator.services.reconciliation import open_conditions
from orchestrator.services.reconciliation_detection import (
    ObservedTrackerItem,
    detect_tracker_conditions,
)


def _obs(external_item_id="tid-1", completed=True):
    return [ObservedTrackerItem("todoist", external_item_id, completed)]


def test_fires_on_completed_card_for_non_closed_unit(session, system_actor):
    unit = make_unit_with_binding(session, WorkUnitState.READY, external_item_id="tid-1")
    counters = detect_tracker_conditions(session, system_actor, observed_states=_obs())
    assert counters.conditions_recorded == 1
    conditions = open_conditions(session, unit.id)
    assert len(conditions) == 1
    assert conditions[0].condition_type == "tracker_state_divergence"
    assert conditions[0].observation_kind == "tracker"


def test_fires_on_completed_card_for_failed_unit(session, system_actor):
    # failed is NOT a card-closed state (outbound keeps it open), so a completed card is a human edit.
    make_unit_with_binding(session, WorkUnitState.FAILED, external_item_id="tid-1")
    counters = detect_tracker_conditions(session, system_actor, observed_states=_obs())
    assert counters.conditions_recorded == 1


def test_no_condition_when_unit_completed(session, system_actor):
    unit = make_unit_with_binding(session, WorkUnitState.COMPLETED, external_item_id="tid-1")
    counters = detect_tracker_conditions(session, system_actor, observed_states=_obs())
    assert counters.conditions_recorded == 0
    assert open_conditions(session, unit.id) == ()


def test_no_condition_when_unit_cancelled(session, system_actor):
    # The false-fire the predicate exists to avoid: outbound closed this card, so it is agreement.
    unit = make_unit_with_binding(session, WorkUnitState.CANCELLED, external_item_id="tid-1")
    counters = detect_tracker_conditions(session, system_actor, observed_states=_obs())
    assert counters.conditions_recorded == 0
    assert open_conditions(session, unit.id) == ()


def test_no_condition_for_open_card(session, system_actor):
    make_unit_with_binding(session, WorkUnitState.READY, external_item_id="tid-1")
    counters = detect_tracker_conditions(
        session, system_actor, observed_states=_obs(completed=False)
    )
    assert counters == counters.__class__()  # all zero


def test_unknown_item_is_skipped_not_raised(session, system_actor):
    counters = detect_tracker_conditions(
        session, system_actor, observed_states=_obs(external_item_id="nope")
    )
    assert counters.skipped_correlations == 1
    assert counters.conditions_recorded == 0


def test_second_pass_suppresses_duplicate(session, system_actor):
    make_unit_with_binding(session, WorkUnitState.READY, external_item_id="tid-1")
    first = detect_tracker_conditions(session, system_actor, observed_states=_obs())
    second = detect_tracker_conditions(session, system_actor, observed_states=_obs())
    assert first.conditions_recorded == 1
    assert second.conditions_recorded == 0
    assert second.suppressed_duplicates == 1


def test_detection_never_mutates_unit_state(session, system_actor):
    unit = make_unit_with_binding(session, WorkUnitState.READY, external_item_id="tid-1")
    detect_tracker_conditions(session, system_actor, observed_states=_obs())
    session.expire_all()
    assert session.get(unit.__class__, unit.id).state == WorkUnitState.READY.value
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/services/test_tracker_reconciliation_detection.py -v`
Expected: FAIL — `ImportError: cannot import name 'ObservedTrackerItem'`.

- [ ] **Step 3: Implement the detector**

In `src/orchestrator/services/reconciliation_detection.py` add the imports you need (`from sqlalchemy import select`, `from orchestrator.persistence.models import UnitTrackerBinding`, and `WorkUnit` if not already imported), then add:

```python
TRACKER_OBSERVATION_KIND = "tracker"
TRACKER_STATE_DIVERGENCE = "tracker_state_divergence"
# The mirror of the outbound projection's card-closed set (adapter TERMINAL_STATES). A card the
# projection itself closes (unit completed/cancelled) is agreement; a completed card in any other
# state is a human edit. Coupled to the adapter set by test_tracker_closed_states_sync.py.
TRACKER_CLOSED_STATES = frozenset({"completed", "cancelled"})


@dataclass(frozen=True)
class ObservedTrackerItem:
    tracker_system: str
    external_item_id: str
    observed_completed: bool


def _tracker_binding(
    session: Session, tracker_system: str, external_item_id: str
) -> UnitTrackerBinding | None:
    return session.scalar(
        select(UnitTrackerBinding).where(
            UnitTrackerBinding.tracker_system == tracker_system,
            UnitTrackerBinding.external_item_id == external_item_id,
        )
    )


def _record_tracker(
    session: Session,
    actor: ActorContext,
    unit: WorkUnit,
    binding: UnitTrackerBinding,
) -> DetectionCounters:
    outcome = record_reconciliation_condition(
        session,
        ConditionCommand(
            actor=actor,
            work_unit_id=unit.id,
            observation_kind=TRACKER_OBSERVATION_KIND,
            condition_type=TRACKER_STATE_DIVERGENCE,
            key_facts={
                "tracker_system": binding.tracker_system,
                "external_item_id": binding.external_item_id,
            },
            stored_state={
                "canonical_state": unit.state,
                "projected_state": binding.projected_state,
            },
            observed_state={"completed": True},
            detail=(
                "tracker item was completed outside the orchestrator while the unit was not in a "
                "closed state (completed/cancelled)"
            ),
            observation_id=None,
        ),
    )
    if isinstance(outcome, DomainError):
        return SKIPPED
    if not isinstance(outcome, ConditionOutcome):  # pragma: no cover - defensive
        return SKIPPED
    if outcome.suppressed:
        return DetectionCounters(suppressed_duplicates=1)
    return DetectionCounters(conditions_recorded=1)


def _detect_tracker_item(
    session: Session, actor: ActorContext, item: ObservedTrackerItem
) -> DetectionCounters:
    if not item.observed_completed:
        return DetectionCounters()  # open card: agreement, nothing to record
    binding = _tracker_binding(session, item.tracker_system, item.external_item_id)
    if binding is None:
        return SKIPPED
    unit = session.get(WorkUnit, binding.work_unit_id)
    if unit is None:
        return SKIPPED
    if unit.state in TRACKER_CLOSED_STATES:
        return DetectionCounters()  # projection closed this card: agreement
    return _record_tracker(session, actor, unit, binding)


def detect_tracker_conditions(
    session: Session,
    actor: ActorContext,
    *,
    observed_states: list[ObservedTrackerItem],
) -> DetectionCounters:
    """Inbound tracker reconciliation. Report-only: creates no unit and sets no lifecycle state.

    Fail-open and counted: an unknown item is skipped and counted, never raised. Mirrors
    detect_reconciliation_conditions. The tracker is never canonical -- a completed card that
    disagrees with canonical state is surfaced as an append-only condition for an operator, never
    applied.
    """
    counters = DetectionCounters()
    for item in observed_states:
        try:
            counters += _detect_tracker_item(session, actor, item)
        except Exception:
            session.rollback()
            counters += SKIPPED
    return counters
```

> Word-guard note: this file already contains `deploy_*` tokens and is allowlisted, so adding tracker prose here is safe. Do not introduce the bare words `dispatch`/`merges` in the new docstrings.

- [ ] **Step 4: Run the detection tests to verify they pass**

Run: `.venv/bin/pytest tests/services/test_tracker_reconciliation_detection.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Write the vocabulary-coupling sync guard**

Create `tests/architecture/test_tracker_closed_states_sync.py`:

```python
from tracker_projection_adapter.projection import TERMINAL_STATES
from orchestrator.services.reconciliation_detection import TRACKER_CLOSED_STATES


def test_inbound_closed_set_mirrors_outbound_terminal_states():
    # If outbound changes which states close a card, inbound must change in lockstep, or it
    # false-fires on a state whose card the projection now legitimately closes.
    assert set(TRACKER_CLOSED_STATES) == set(TERMINAL_STATES)
```

- [ ] **Step 6: Run the sync guard**

Run: `.venv/bin/pytest tests/architecture/test_tracker_closed_states_sync.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/services/reconciliation_detection.py tests/services/test_tracker_reconciliation_detection.py tests/architecture/test_tracker_closed_states_sync.py
git commit -m "feat(wsp27): tracker divergence detection (report-only, mirrors outbound closed-set)"
```

---

## Task 3: API schema + detect route (+ WORKER-GET binding test)

**Files:**
- Modify: `src/orchestrator/api/schemas.py`
- Modify: `src/orchestrator/api/routes.py`
- Modify: `tests/architecture/test_scope_guards.py` (POST inventory set literal, ~line 90)
- Modify: `tests/idempotency/matrix.py` (add a `MatrixRow`)
- Test: `tests/api/test_tracker_reconciliation_api.py` (create)
- Test: `tests/idempotency/test_tracker_detect_idempotency.py` (create)
- Test: `tests/api/test_tracker_bindings_api.py` (add the WORKER-GET minor — or the existing tracker-bindings API test file)

**Interfaces:**
- Consumes: `detect_tracker_conditions`, `ObservedTrackerItem` (Task 2); `ReconciliationDetectResponse`, `CommandBase` (schemas); `_require_zero_expected_version`, `ActorDep`, `SessionDep`, `ActorRole`, `DomainError` (routes).
- Produces: `POST /api/v1/reconciliation/tracker-detect` returning the `{conditions_recorded, skipped_correlations, suppressed_duplicates}` counters.

- [ ] **Step 1: Write the failing route tests**

Create `tests/api/test_tracker_reconciliation_api.py`. Mirror the existing `/reconciliation/detect` API test for the client/actor-credential fixtures (SYSTEM, WORKER, HUMAN test clients).

```python
def _body(items, key="k1"):
    return {"observed_states": items, "idempotency_key": key, "expected_version": 0}


def test_system_detect_returns_counters(system_client, seeded_ready_unit_with_binding):
    # binding external_item_id == "tid-1", unit state READY
    resp = system_client.post(
        "/api/v1/reconciliation/tracker-detect",
        json=_body([{"tracker_system": "todoist", "external_item_id": "tid-1", "observed_completed": True}]),
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "conditions_recorded": 1,
        "skipped_correlations": 0,
        "suppressed_duplicates": 0,
    }


def test_worker_is_forbidden(worker_client):
    resp = worker_client.post("/api/v1/reconciliation/tracker-detect", json=_body([]))
    assert resp.status_code == 403


def test_human_is_forbidden(human_client):
    resp = human_client.post("/api/v1/reconciliation/tracker-detect", json=_body([]))
    assert resp.status_code == 403


def test_nonzero_expected_version_rejected(system_client):
    resp = system_client.post(
        "/api/v1/reconciliation/tracker-detect",
        json={"observed_states": [], "idempotency_key": "k", "expected_version": 1},
    )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run the route tests to verify they fail**

Run: `.venv/bin/pytest tests/api/test_tracker_reconciliation_api.py -v`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Add the request schemas**

In `src/orchestrator/api/schemas.py`, next to `ReconciliationDetectCommand`:

```python
class TrackerReconciliationDetectItem(BaseModel):
    """One bound tracker item's observed completion state, reported by the adapter.

    Normalized state only -- never card text. The orchestrator owns the divergence rule; this
    carries no interpretation.
    """

    tracker_system: str
    external_item_id: str = Field(min_length=1)
    observed_completed: bool


class TrackerReconciliationDetectCommand(CommandBase):
    """Inbound tracker reconciliation: a batch of observed item states. Conditions dedup on the
    divergence hash regardless of the idempotency key, so a duplicate delivery surfaces as
    suppressed_duplicates rather than a second row."""

    observed_states: list[TrackerReconciliationDetectItem]
```

The response reuses `ReconciliationDetectResponse` (identical counters shape) — do not add a new response model.

- [ ] **Step 4: Add the route**

In `src/orchestrator/api/routes.py`, near `reconciliation_detect`, add (import `TrackerReconciliationDetectCommand` from schemas and `ObservedTrackerItem` + `detect_tracker_conditions` from the detection service):

```python
@router.post("/reconciliation/tracker-detect", response_model=ReconciliationDetectResponse)
def tracker_reconciliation_detect(
    body: TrackerReconciliationDetectCommand,
    actor: ActorDep,
    session: SessionDep,
) -> object:
    """Inbound tracker reconciliation. SYSTEM-only, report-only: records append-only divergence
    conditions, creates no unit and sets no lifecycle state. The tracker is never canonical."""
    _require_zero_expected_version(body.expected_version, "tracker reconciliation detection")
    if actor.role is not ActorRole.SYSTEM:
        raise DomainError(
            "role_forbidden",
            "only the orchestrator system actor may run tracker reconciliation detection",
            None,
        )
    return detect_tracker_conditions(
        session,
        actor,
        observed_states=[
            ObservedTrackerItem(i.tracker_system, i.external_item_id, i.observed_completed)
            for i in body.observed_states
        ],
    ).as_dict()
```

- [ ] **Step 5: Add the route to the POST inventory**

In `tests/architecture/test_scope_guards.py`, inside the `test_production_post_route_inventory_is_explicit` set literal, add (near the other `/api/v1/reconciliation/*` entry):

```python
        # WS-P2.7 Inc-2: inbound tracker reconciliation. SYSTEM-only, report-only -- records
        # append-only divergence conditions, never a transition.
        "/api/v1/reconciliation/tracker-detect",
```

- [ ] **Step 6: Add the idempotency matrix row**

In `tests/idempotency/matrix.py`, in the WS-P2.7 ingress block, add:

```python
    MatrixRow(
        "tracker reconciliation detect-pass",
        "/api/v1/reconciliation/tracker-detect",
        ADVISORY_LOCK,
        "tests/idempotency/test_tracker_detect_idempotency.py::"
        "test_a_duplicate_tracker_detect_records_no_second_condition",
    ),
```

- [ ] **Step 7: Write the idempotency test**

Create `tests/idempotency/test_tracker_detect_idempotency.py` (mirror `test_wsp21_ingress_idempotency.py::test_a_duplicate_detect_pass_records_no_second_condition`):

```python
def test_a_duplicate_tracker_detect_records_no_second_condition(
    system_client, seeded_ready_unit_with_binding
):
    body = {
        "observed_states": [
            {"tracker_system": "todoist", "external_item_id": "tid-1", "observed_completed": True}
        ],
        "idempotency_key": "dup",
        "expected_version": 0,
    }
    first = system_client.post("/api/v1/reconciliation/tracker-detect", json=body).json()
    second = system_client.post("/api/v1/reconciliation/tracker-detect", json=body).json()
    assert first["conditions_recorded"] == 1
    assert second["conditions_recorded"] == 0
    assert second["suppressed_duplicates"] == 1
```

- [ ] **Step 8: Add the WORKER-GET tracker-bindings minor (deferred Inc-1 minor 4)**

In the existing tracker-bindings API test (find it via `grep -rl tracker-bindings tests/api`), add a case proving the GET is auth-only, not SYSTEM-only:

```python
def test_worker_can_read_tracker_bindings(worker_client):
    resp = worker_client.get("/api/v1/tracker-bindings")
    assert resp.status_code == 200
```

- [ ] **Step 9: Run the affected suites**

Run: `.venv/bin/pytest tests/api/test_tracker_reconciliation_api.py tests/idempotency/test_tracker_detect_idempotency.py tests/idempotency/test_matrix.py tests/architecture/test_scope_guards.py -v`
Expected: all PASS (inventory + matrix now include the route). Also run the WORKER-GET test file.

- [ ] **Step 10: Commit**

```bash
git add src/orchestrator/api/schemas.py src/orchestrator/api/routes.py tests/architecture/test_scope_guards.py tests/idempotency/matrix.py tests/idempotency/test_tracker_detect_idempotency.py tests/api/test_tracker_reconciliation_api.py tests/api/test_tracker_bindings_api.py
git commit -m "feat(wsp27): SYSTEM-only tracker-detect route + inventory/matrix/idempotency"
```

---

## Task 4: Adapter — two-endpoint write surface + report method

**Files:**
- Modify: `src/tracker_projection_adapter/orchestrator_client.py`
- Modify: `tests/architecture/test_tracker_projection_adapter_isolation.py` (rework the write-surface test; +Inc-1 minor 1)
- Test: `tests/tracker_projection_adapter/test_orchestrator_client.py` (add)

**Interfaces:**
- Produces: `TRACKER_DETECT_ENDPOINT = "/api/v1/reconciliation/tracker-detect"`; `_is_allowed_write(path) -> bool`; `OrchestratorClient.report_tracker_reconciliation(*, observed_states: list[dict], idempotency_key: str) -> dict`.

- [ ] **Step 1: Write the failing client tests**

In `tests/tracker_projection_adapter/test_orchestrator_client.py` add (use `httpx.MockTransport` as the existing tests do):

```python
from tracker_projection_adapter.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    _is_allowed_write,
)


def test_report_tracker_reconciliation_posts_to_the_allowed_endpoint():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"conditions_recorded": 0, "skipped_correlations": 0, "suppressed_duplicates": 0})

    client = OrchestratorClient(
        base_url="https://x", credential_key_id="orchestrator-system", token="t",
        transport=httpx.MockTransport(handler),
    )
    client.report_tracker_reconciliation(
        observed_states=[{"tracker_system": "todoist", "external_item_id": "tid-1", "observed_completed": True}],
        idempotency_key="k",
    )
    assert seen == [("POST", "/api/v1/reconciliation/tracker-detect")]


def test_write_surface_allows_only_the_two_report_only_endpoints():
    assert _is_allowed_write("/api/v1/work-units/00000000-0000-0000-0000-000000000000/tracker-binding")
    assert _is_allowed_write("/api/v1/reconciliation/tracker-detect")
    for forbidden in (
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/commands/ready",
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/evidence",
        "/api/v1/observations",
        "/api/v1/work-units/00000000-0000-0000-0000-000000000000/adjudications",
    ):
        assert not _is_allowed_write(forbidden)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_orchestrator_client.py -v -k "report_tracker or write_surface"`
Expected: FAIL — `ImportError: cannot import name '_is_allowed_write'`.

- [ ] **Step 3: Implement the two-endpoint write surface**

In `src/tracker_projection_adapter/orchestrator_client.py`, replace the single `ALLOWED_WRITE_PATTERN` gate. Keep the binding regex (renamed for clarity), add the fixed detect endpoint, and add the allow helper + the report method:

```python
# The tracker-binding write: /api/v1/work-units/<uuid>/tracker-binding. `\Z` (not `$`) so a
# trailing newline cannot slip through.
TRACKER_BINDING_PATTERN = re.compile(r"^/api/v1/work-units/[0-9a-fA-F-]{36}/tracker-binding\Z")
# WS-P2.7 Inc-2: the inbound report. Report-only -- it records append-only divergence conditions
# and can never change canonical state (exit #9). Fixed path, so an exact-string gate.
TRACKER_DETECT_ENDPOINT = "/api/v1/reconciliation/tracker-detect"


def _is_allowed_write(path: str) -> bool:
    """The adapter's TWO permitted writes, both provably non-canonical (a projection binding and
    an append-only reconciliation report). Every lifecycle/command/adjudication/observation path
    stays structurally unreachable."""
    return path == TRACKER_DETECT_ENDPOINT or bool(TRACKER_BINDING_PATTERN.match(path))
```

Update `post()` to use it:

```python
    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not _is_allowed_write(path):
            raise ForbiddenEndpointError(f"the adapter may not write to {path}")
        return self._request("POST", path, json=payload).json()
```

Add the report method (next to `upsert_tracker_binding`):

```python
    def report_tracker_reconciliation(
        self, *, observed_states: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]:
        return self.post(
            TRACKER_DETECT_ENDPOINT,
            {
                "observed_states": observed_states,
                "idempotency_key": idempotency_key,
                "expected_version": 0,
            },
        )
```

- [ ] **Step 4: Rework the isolation write-surface test (+ Inc-1 minor 1)**

In `tests/architecture/test_tracker_projection_adapter_isolation.py`, replace `test_write_pattern_matches_only_tracker_binding` with a two-endpoint version driving `_is_allowed_write`, and — folding in Inc-1 minor 1 — assert the mock transport is never reached when a forbidden write is attempted:

```python
from tracker_projection_adapter.orchestrator_client import (
    ForbiddenEndpointError,
    OrchestratorClient,
    _is_allowed_write,
)


def test_write_surface_allows_only_the_two_report_only_endpoints():
    assert _is_allowed_write("/api/v1/work-units/00000000-0000-0000-0000-000000000000/tracker-binding")
    assert _is_allowed_write("/api/v1/reconciliation/tracker-detect")
    assert not _is_allowed_write("/api/v1/work-units/00000000-0000-0000-0000-000000000000/commands/ready")
    assert not _is_allowed_write("/api/v1/observations")


def test_a_forbidden_write_never_reaches_the_transport():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    client = OrchestratorClient(
        base_url="https://x", credential_key_id="orchestrator-system", token="t",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ForbiddenEndpointError):
        client.post("/api/v1/work-units/00000000-0000-0000-0000-000000000000/commands/ready", {})
    assert seen == []
```

> Ensure `import httpx` and `import pytest` are present in the test file.

- [ ] **Step 5: Run the affected tests**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_orchestrator_client.py tests/architecture/test_tracker_projection_adapter_isolation.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tracker_projection_adapter/orchestrator_client.py tests/architecture/test_tracker_projection_adapter_isolation.py tests/tracker_projection_adapter/test_orchestrator_client.py
git commit -m "feat(wsp27): adapter two-endpoint report-only write surface + reconciliation report"
```

---

## Task 5: Adapter — Todoist item read (+ Inc-1 minor 3)

**Files:**
- Modify: `src/tracker_projection_adapter/tracker.py`
- Test: `tests/tracker_projection_adapter/test_tracker.py` (add)

**Interfaces:**
- Produces: `TrackerProjector.item_completed(self, item_ref: ItemRef) -> bool` (protocol + `TodoistProjector`); private `TodoistProjector._get(path) -> tuple[int, Any]`.

- [ ] **Step 1: Write the failing read tests**

In `tests/tracker_projection_adapter/test_tracker.py` add (use `httpx.MockTransport`):

```python
def _projector(handler):
    return TodoistProjector(
        token="t", project_id="p", review_base_url="https://sds.alobar.net",
        transport=httpx.MockTransport(handler),
    )


def test_item_completed_true_when_task_missing():
    # A completed Todoist task leaves the active set; GET /tasks/{id} returns 404.
    projector = _projector(lambda r: httpx.Response(404, json={}))
    assert projector.item_completed(ItemRef("tid-1", None)) is True


def test_item_completed_false_for_active_task():
    projector = _projector(lambda r: httpx.Response(200, json={"id": "tid-1", "is_completed": False}))
    assert projector.item_completed(ItemRef("tid-1", None)) is False


def test_item_completed_true_when_flag_set():
    projector = _projector(lambda r: httpx.Response(200, json={"id": "tid-1", "is_completed": True}))
    assert projector.item_completed(ItemRef("tid-1", None)) is True
```

Also add the deferred Inc-1 minor 3 tests for `update_item`:

```python
def test_update_item_falls_back_to_existing_url_when_response_has_none():
    projector = _projector(lambda r: httpx.Response(200, json={"id": "tid-1"}))
    ref = projector.update_item(ItemRef("tid-1", "https://old"), _unit(state="ready"))
    assert ref.external_url == "https://old"


def test_update_item_raises_on_non_2xx():
    projector = _projector(lambda r: httpx.Response(500, json={}))
    with pytest.raises(RuntimeError):
        projector.update_item(ItemRef("tid-1", "https://old"), _unit(state="ready"))
```

> `_unit(...)` builds a `UnitView`; reuse the helper the existing `test_tracker.py`/`test_projection.py` uses.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_tracker.py -v -k "item_completed or update_item"`
Expected: FAIL — `AttributeError: 'TodoistProjector' object has no attribute 'item_completed'`.

- [ ] **Step 3: Implement the read method + `_get`**

In `src/tracker_projection_adapter/tracker.py`, add `item_completed` to the `TrackerProjector` protocol:

```python
class TrackerProjector(Protocol):
    def create_item(self, unit: UnitView) -> ItemRef: ...
    def update_item(self, item_ref: ItemRef, unit: UnitView) -> ItemRef: ...
    def complete_item(self, item_ref: ItemRef) -> None: ...
    def item_completed(self, item_ref: ItemRef) -> bool: ...
```

Add to `TodoistProjector` a `_get` helper and `item_completed`:

```python
    def item_completed(self, item_ref: ItemRef) -> bool:
        """Whether the tracker item is completed (checked off). A completed Todoist task leaves
        the active set, so a 404 means completed. Otherwise read the completion flag."""
        status, data = self._get(f"/tasks/{item_ref.external_item_id}")
        if status == 404:
            return True
        if isinstance(data, dict):
            for flag in ("is_completed", "checked", "completed"):
                if flag in data:
                    return bool(data[flag])
            if data.get("completed_at") is not None:
                return True
        return False

    def _get(self, path: str) -> tuple[int, Any]:
        response = self._client.get(path)
        if response.status_code == 404:
            return 404, {}
        if response.status_code >= 400:
            raise RuntimeError(f"todoist rejected GET {path}: {response.status_code}")
        return response.status_code, (response.json() if response.content else {})
```

> **Verify against the live Todoist v1 API before finalizing:** confirm whether `GET /api/v1/tasks/{id}` returns a completed task with a completion flag (`is_completed`/`checked`/`completed_at`) or 404s once closed. The implementation above handles both; keep whichever branch the live API exercises and delete dead branches only if you have positively confirmed the behavior. A rare deleted card also 404s → reported completed → surfaces a condition the operator can dismiss (out-of-scope, tolerable, fail-safe).

- [ ] **Step 4: (Inc-1 minor 3) context-manage the Todoist client**

Add `close()` + context-manager methods to `TodoistProjector` so the one-shot process closes its `httpx.Client` cleanly:

```python
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TodoistProjector":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
```

(The CLI wires the `with` in Task 6.)

- [ ] **Step 5: Run the tracker tests**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_tracker.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tracker_projection_adapter/tracker.py tests/tracker_projection_adapter/test_tracker.py
git commit -m "feat(wsp27): TodoistProjector.item_completed read + update_item edge tests"
```

---

## Task 6: Adapter — `reconcile` command + launcher (+ Inc-1 minor 2)

**Files:**
- Modify: `src/tracker_projection_adapter/cli.py`
- Create: `scripts/run-tracker-reconciliation.sh`
- Modify: `tests/tracker_projection_adapter/test_projection.py` (Inc-1 minor 2)
- Test: `tests/tracker_projection_adapter/test_cli.py` (add `reconcile` core tests)
- Test: `tests/tracker_projection_adapter/test_cli_invocation.py` (add `reconcile` CliRunner tests)

**Interfaces:**
- Consumes: `client.tracker_bindings()`, `client.report_tracker_reconciliation(...)`, `projector.item_completed(...)`.
- Produces: `reconcile(client, projector, *, dry_run: bool) -> dict[str, int]` core function; `@app.command("reconcile") reconcile_command(...)`.

- [ ] **Step 1: Write the failing `reconcile` core test**

In `tests/tracker_projection_adapter/test_cli.py` add (mirror the existing `FakeClient`/`FakeProjector` pattern; extend the fake client with `report_tracker_reconciliation` capturing its args, and the fake projector with `item_completed` returning a scripted map):

```python
from tracker_projection_adapter.cli import reconcile


def test_reconcile_reports_observed_completion_for_each_todoist_binding():
    client = FakeClient(
        bindings=[
            {"work_unit_id": "u1", "tracker_system": "todoist", "external_item_id": "tid-1",
             "external_url": None, "projected_state": "ready"},
        ]
    )
    projector = FakeProjector(completed={"tid-1": True})
    counts = reconcile(client, projector, dry_run=False)
    assert client.reported == [
        {"tracker_system": "todoist", "external_item_id": "tid-1", "observed_completed": True}
    ]
    assert counts == {"reported": 1}


def test_reconcile_dry_run_makes_no_report():
    client = FakeClient(
        bindings=[
            {"work_unit_id": "u1", "tracker_system": "todoist", "external_item_id": "tid-1",
             "external_url": None, "projected_state": "ready"},
        ]
    )
    projector = FakeProjector(completed={"tid-1": False})
    reconcile(client, projector, dry_run=True)
    assert client.reported == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/test_cli.py -v -k reconcile`
Expected: FAIL — `ImportError: cannot import name 'reconcile'`.

- [ ] **Step 3: Extend the reader protocol and implement `reconcile`**

In `src/tracker_projection_adapter/cli.py`, add `report_tracker_reconciliation` to the `OrchestratorReader` protocol:

```python
    def report_tracker_reconciliation(
        self, *, observed_states: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]: ...
```

Add the core function:

```python
def reconcile(
    client: OrchestratorReader,
    projector: TrackerProjector,
    *,
    dry_run: bool,
) -> dict[str, int]:
    """Report each Todoist-bound item's observed completion. The orchestrator owns the divergence
    rule; this only observes and reports (dumb adapter). Reading Todoist is non-mutating, so a dry
    run still reads -- it only withholds the orchestrator report."""
    observed_states = []
    for row in client.tracker_bindings():
        binding = binding_view(row)
        if binding.tracker_system != "todoist":
            continue
        completed = projector.item_completed(
            ItemRef(binding.external_item_id, binding.external_url)
        )
        observed_states.append(
            {
                "tracker_system": binding.tracker_system,
                "external_item_id": binding.external_item_id,
                "observed_completed": completed,
            }
        )
    if not dry_run and observed_states:
        client.report_tracker_reconciliation(
            observed_states=observed_states, idempotency_key="tracker-detect-pass"
        )
    return {"reported": len(observed_states)}
```

- [ ] **Step 4: Add the `reconcile` CLI command**

Add the command (reconcile always needs the Todoist token, because it reads; dry-run only withholds the orchestrator report). Wrap the projector in `with` (uses the Task-5 context manager):

```python
@app.command("reconcile")
def reconcile_command(
    todoist_project_id: Annotated[str, typer.Option(help="Target Todoist project id.")],
    orchestrator_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    review_base_url: Annotated[str, typer.Option()] = "https://sds.alobar.net",
    credential_key_id: Annotated[str, typer.Option()] = "orchestrator-system",
    dry_run: Annotated[bool, typer.Option(help="Read + print the plan; send no report.")] = False,
) -> None:
    token = os.environ.get("TRACKER_PROJECTION_TOKEN")
    if not token:
        typer.echo("TRACKER_PROJECTION_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    todoist_token = os.environ.get("TODOIST_API_TOKEN")
    if not todoist_token:
        typer.echo("TODOIST_API_TOKEN is required", err=True)
        raise typer.Exit(code=1)
    client = OrchestratorClient(
        base_url=orchestrator_url, credential_key_id=credential_key_id, token=token
    )
    with TodoistProjector(
        token=todoist_token, project_id=todoist_project_id, review_base_url=review_base_url
    ) as projector:
        counts = reconcile(client, projector, dry_run=dry_run)
    typer.echo(json.dumps(counts, indent=2, sort_keys=True))
```

- [ ] **Step 5: Write the CliRunner invocation tests**

In `tests/tracker_projection_adapter/test_cli_invocation.py` add:

```python
def test_reconcile_is_a_named_command():
    assert runner.invoke(app, ["reconcile", "--help"]).exit_code == 0
    assert runner.invoke(app, ["reconcile", "--nonsense-flag"]).exit_code == 2


def test_reconcile_dry_run_runs(monkeypatch):
    monkeypatch.setenv("TRACKER_PROJECTION_TOKEN", "t")
    monkeypatch.setenv("TODOIST_API_TOKEN", "tt")
    monkeypatch.setattr(cli, "OrchestratorClient", lambda **kw: FakeClient(bindings=[]))
    monkeypatch.setattr(cli, "TodoistProjector", lambda **kw: FakeProjectorCM(completed={}))
    result = runner.invoke(app, ["reconcile", "--dry-run", "--todoist-project-id", "p"])
    assert result.exit_code == 0
    assert '"reported": 0' in result.output
```

> `FakeProjectorCM` is a fake that supports `with` (`__enter__`/`__exit__`) and `item_completed`. Reuse/extend the existing dry-run fake pattern in this file.

- [ ] **Step 6: (Inc-1 minor 2) tighten the projection skip-tests**

In `tests/tracker_projection_adapter/test_projection.py`, find the two tests that assert only `action.kind` for a `skip` and assert the full `Action` tuple instead, e.g.:

```python
    assert actions[0] == Action("skip", unit, None)
```

(Assert the whole `Action(kind, unit, binding)`, not just `.kind`.)

- [ ] **Step 7: Write the launcher**

Create `scripts/run-tracker-reconciliation.sh` (mirror `run-tracker-projection.sh`, same two BWS secrets, `reconcile` subcommand):

```bash
#!/usr/bin/env bash
# Operator-invoked inbound tracker reconciliation pass (WS-P2.7, Increment 2).
#
# No scheduler and no loop (ADR-0003/0004): one pass, then exit. It reads each bound Todoist
# item's completion state and reports it to the orchestrator, which records append-only
# divergence conditions an operator resolves. It never changes canonical state.
#
# Prerequisites:
#   - `uv pip install -e .` so the `tracker-projection-adapter` entry point exists.
#   - TODOIST_PROJECT_ID is set to the target Todoist project id.
#   - The macOS login Keychain holds BWS_ACCESS_TOKEN_VPS_BACKUP (loaded by bws-token.sh).
#
# Usage:
#   TODOIST_PROJECT_ID=<id> scripts/run-tracker-reconciliation.sh [--dry-run]
set -euo pipefail

SYSTEM_BEARER_UUID="221a48d5-3f29-4898-b300-b4820140c880"   # orchestrator-system SYSTEM bearer
TODOIST_TOKEN_UUID="ff396349-aec1-4250-b2f0-b493015188da"   # Todoist REST API token

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
source "$HOME/Projects/vps-backup/bws-token.sh"

_bws_value() {
  bws secret get "$1" | python3 -c 'import sys, json; print(json.load(sys.stdin)["value"])'
}

TRACKER_PROJECTION_TOKEN="$(_bws_value "$SYSTEM_BEARER_UUID")"
TODOIST_API_TOKEN="$(_bws_value "$TODOIST_TOKEN_UUID")"
export TRACKER_PROJECTION_TOKEN TODOIST_API_TOKEN

exec "$REPO_ROOT/.venv/bin/tracker-projection-adapter" reconcile \
  --todoist-project-id "${TODOIST_PROJECT_ID:?set TODOIST_PROJECT_ID to the target Todoist project id}" \
  "$@"
```

Then `chmod +x scripts/run-tracker-reconciliation.sh`.

- [ ] **Step 8: Run the adapter suites**

Run: `.venv/bin/pytest tests/tracker_projection_adapter/ -v`
Expected: all PASS (core + invocation + projection).

- [ ] **Step 9: Commit**

```bash
git add src/tracker_projection_adapter/cli.py scripts/run-tracker-reconciliation.sh tests/tracker_projection_adapter/test_cli.py tests/tracker_projection_adapter/test_cli_invocation.py tests/tracker_projection_adapter/test_projection.py
git commit -m "feat(wsp27): adapter reconcile command + launcher (inbound pass)"
```

---

## Task 7: ADR-0004 + backlog

**Files:**
- Create: `docs/decisions/0004-inbound-tracker-reconciliation.md`
- Modify: `PROJECT.md` (backlog)

- [ ] **Step 1: Write ADR-0004**

Create `docs/decisions/0004-inbound-tracker-reconciliation.md` capturing: the locked decisions (shape b; Option 2 dedicated `tracker-detect` route; dumb adapter; poll+diff; completion-only scope); the detection predicate and its coupling to the outbound closed-set (with the sync guard); and — explicitly — the **updated exit-#9 guarantee**: the adapter now permits **two** write endpoints, both report-only (a projection binding and an append-only reconciliation report), every lifecycle/command/adjudication/observation path still structurally unreachable, proven by `test_write_surface_allows_only_the_two_report_only_endpoints` and by the service test that `detect_tracker_conditions` never mutates `work_units.state`. Record the deferred cases (reopen/delete out of scope; a deleted card 404s → reported completed → operator-dismissable) and carry forward the create-then-record non-atomicity note (Inc-1 minor 5, tracker-tidiness only). Reference the spec.

- [ ] **Step 2: Update the backlog**

Mark WS-P2.7 Increment 2 status in `PROJECT.md` per the backlog convention (use the `backlog` skill or `portfolio add` rather than hand-editing free-form). Note that after this increment, only WS-P2.8 remains in Wave 2.

- [ ] **Step 3: Commit**

```bash
git add docs/decisions/0004-inbound-tracker-reconciliation.md PROJECT.md
git commit -m "docs(wsp27): ADR-0004 inbound tracker reconciliation + backlog"
```

---

## Final verification (before declaring done)

- [ ] **Full gate on a clean tree.** `git status` clean, then run `make check`. Read the collected-test count (`collected N items`), not just the exit code — exit 0 with 5 (no tests) is not a pass. Confirm the whole-repo scans ran: route inventory, idempotency matrix, `test_unreachable_guards` (the new `detect_tracker_conditions` is reached by the route), `test_wsp21_invariant_scan` (no new adapter egress file, so `OUTBOUND_ALLOWLIST` is unchanged — confirm it stays green), ws32/ws33 word guards, adapter isolation + `ALLOWED_TOP_LEVEL`.
- [ ] If `make check` reds on **pre-existing** format-debt in files you never touched, run `ruff format --check .` and diff against `main` before blaming your diff (a differential, not your regression). Run `ruff format` (or `make fix`) on your own changed files before the final commit.
- [ ] Run `/code-review` on the whole branch diff against the standards.

## Deploy & verify (Devon-gated — NOT a code task)

Orchestrator-side additions (vocab migration, detector, route) need a prod image rebuild + migrate-first deploy. Adapter-only files run from local operator code and need no redeploy.

1. Build the prod image via the paved-road `Release image` GitHub Actions workflow (`workflow_dispatch`), producing `ghcr.io/alobarquest/orchestrator:<sha>-wsp27inc2-amd64`.
2. Point Coolify at the new tag and deploy; run `alembic upgrade head` in the new container (migrate-first: the new route's detection writes conditions with the new vocab, so the CHECK must be migrated before traffic).
3. Verify the running container's `RepoDigest` == the pushed digest.
4. **MERGED ≠ DEPLOYED:** confirm the new route is actually served — `curl -s https://sds.alobar.net/openapi.json | python3 -c "import sys,json; print('/api/v1/reconciliation/tracker-detect' in json.load(sys.stdin)['paths'])"` → `True`.
5. Run one live pass: `TODOIST_PROJECT_ID=6h8fCrQ6pfhVp4qV scripts/run-tracker-reconciliation.sh --dry-run`, review, then without `--dry-run`. Complete a queue card by hand in Todoist and confirm a `tracker_state_divergence` condition appears on that unit's `/review` page and resolves via accept/correct/dismiss.
6. Write the Wave-2 closeout evidence note (mirror the Inc-1 closeout).

---

## Self-review (completed by plan author)

**Spec coverage:** vocab (Task 1) ✓; detection rule + predicate + fail-open + never-mutate (Task 2) ✓; sync guard (Task 2) ✓; dedicated SYSTEM-only route + inventory/matrix/JSON-schema/idempotency (Task 3) ✓; two-endpoint report-only adapter write surface (Task 4) ✓; poll+diff Todoist read (Task 5) ✓; dumb `reconcile` command + launcher (Task 6) ✓; operator surface reuse (no code — confirmed generic template/handler) ✓; ADR-0004 + exit-#9 guarantee update (Task 7) ✓; all 5 deferred Inc-1 minors (minor 1 Task 4, minor 2 Task 6, minor 3 Task 5, minor 4 Task 3, minor 5 ADR Task 7) ✓.

**Type consistency:** `ObservedTrackerItem(tracker_system, external_item_id, observed_completed)` used identically in the detector (Task 2), the route mapping (Task 3), and the adapter's reported dict keys (Task 6). `report_tracker_reconciliation(*, observed_states, idempotency_key)` defined in the client (Task 4) and the reader protocol (Task 6) with matching signature. `item_completed(item_ref) -> bool` defined on the protocol + `TodoistProjector` (Task 5) and called in `reconcile` (Task 6). Response reuses `ReconciliationDetectResponse` (counters) throughout.

**Placeholder scan:** no TBD/TODO; every code step shows real code. The one flagged uncertainty (Todoist v1 completion semantics, Task 5 Step 3) is called out explicitly with a defensive implementation and a verify-against-live-API instruction — not a placeholder.
