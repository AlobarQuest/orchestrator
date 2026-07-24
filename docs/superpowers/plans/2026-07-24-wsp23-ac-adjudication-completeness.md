# WS-P2.3 AC Adjudication Completeness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the already-enforced completion invariant satisfiable in-band (a human can record a canonical `passed`/`not_applicable`/`waived` for an AC from `/review`) and make waivers auditable (risk-class enum + thin-waiver audit), without touching the completion gate or the deferred `automated_test` evaluator boundary.

**Architecture:** Extend the existing `record_adjudication` service and its `_authorize_outcome` with one static predicate (a HUMAN may pass a criterion iff its `evidence_type ∈ JUDGMENT_TYPES`); add a `/review` per-AC form mirroring the reconciliation-conditions pattern; add a structural risk-class `CHECK` and an independent thin-waiver reporter in `consistency.py`. The completion gate (`lifecycle.py::_current_terminal_is_satisfied`) is not modified — a human `passed` is an ordinary `Adjudication` row that flows through it unchanged.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, FastAPI + Jinja2, pytest, PostgreSQL.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-07-24-wsp23-ac-adjudication-completeness-design.md`.
- Do **not** modify `evaluate_criterion` or the `DETERMINISTIC_TYPES`/`JUDGMENT_TYPES`/`SPECIAL_CASE_TYPES` sets in `services/verifier_evaluators.py`. `tests/services/test_criterion_evidence_vocabulary.py` must stay unchanged and green (deferred-boundary guard).
- Do **not** modify the completion gate `services/lifecycle.py::_current_terminal_is_satisfied` or its `SYSTEM_EDGES`/`→ completed` guard. It remains the sole arbiter of completion.
- `WAIVER_RISK_CLASSES = ("low", "medium", "high", "critical")` — the single risk-class vocabulary, defined once.
- The new human adjudication path must be `record_adjudication`, never a `commands/{command}` lifecycle transition (so WS-P2.2's improvisation counter does not miscount it).
- `/review` write routes: forward-auth (`orchestrator-review` router), `_human(actor)` + `_require_form(...)` (CSRF + explicit confirm), and the entry point owns its transaction (`session.commit()` — here via `record_adjudication`, which commits internally).
- `make check` must be green with a real collected-test count; run `ruff format` (not just `ruff check`) before each commit; resolve tools from `.venv/bin` first.
- `scope` is never exposed by the human form (a scoped waiver silently fails the completion gate). Per-AC `failed` is out of scope.

---

### Task 1: Risk-class vocabulary + structural CHECK + migration + fixture sweep

Introduces the controlled `risk` vocabulary as a DB `CHECK`, the single-source constant, a service-level clean-error validation, and migrates existing test fixtures whose `risk` strings are outside the vocabulary (they would otherwise break under the new constraint).

**Files:**
- Modify: `src/orchestrator/kernel/states.py` (add `WAIVER_RISK_CLASSES`)
- Modify: `src/orchestrator/persistence/models.py:425-438` (add risk-class CHECK to `Adjudication.__table_args__`)
- Create: `migrations/versions/0017_wsp23_waiver_risk_class.py`
- Modify: `src/orchestrator/services/evidence.py:693-703` (`_validate_adjudication_fields` — reject out-of-vocab risk for waivers)
- Modify (fixture sweep): `tests/services/test_waivers.py:54`, `tests/services/test_lifecycle_guards.py:97`, `tests/web/test_evidence_pack.py:128`, `tests/services/test_event_publications.py:297`
- Test: `tests/services/test_waivers.py`

**Interfaces:**
- Produces: `WAIVER_RISK_CLASSES: tuple[str, ...]` in `orchestrator.kernel.states`, imported by `models.py`, `services/evidence.py`, `services/consistency.py` (Task 4), and the web layer (Task 5).

- [ ] **Step 1: Add the vocabulary constant**

In `src/orchestrator/kernel/states.py`, add near the other domain vocabularies:

```python
# The controlled risk-class vocabulary a waiver must declare. Structurally enforced by the
# adjudications risk CHECK (migration 0017) and validated for a clean error in the service layer.
WAIVER_RISK_CLASSES: tuple[str, ...] = ("low", "medium", "high", "critical")
```

- [ ] **Step 2: Add the model CHECK constraint**

In `src/orchestrator/persistence/models.py`, add an import at the top with the other `orchestrator.kernel.states` imports:

```python
from orchestrator.kernel.states import WAIVER_RISK_CLASSES
```

Then add a third `CheckConstraint` to `Adjudication.__table_args__` (after `ck_adjudications_waiver_fields`, `models.py:432`):

```python
        CheckConstraint(
            "risk IS NULL OR risk IN ("
            + ", ".join(f"'{value}'" for value in WAIVER_RISK_CLASSES)
            + ")",
            name="ck_adjudications_risk_class",
        ),
```

- [ ] **Step 3: Write the migration**

Create `migrations/versions/0017_wsp23_waiver_risk_class.py`:

```python
"""Add adjudications risk-class CHECK — waiver risk becomes a controlled vocabulary (WS-P2.3).

Revision ID: 0017_wsp23_waiver_risk_class
Revises: 0016_wsp22_event_improvisation

Exit criterion #4 ("waivers structurally approved and auditable") wants risk to be an auditable
class, not free prose. The ledger is near-empty, so no backfill is required. Non-waivers keep
risk NULL (allowed); a waiver's non-empty risk (already required by ck_adjudications_waiver_fields)
must now be one of the controlled classes.
"""

from alembic import op

revision = "0017_wsp23_waiver_risk_class"
down_revision = "0016_wsp22_event_improvisation"
branch_labels = None
depends_on = None

_RISK_CLASSES = ("low", "medium", "high", "critical")


def upgrade() -> None:
    values = ", ".join(f"'{value}'" for value in _RISK_CLASSES)
    op.create_check_constraint(
        "ck_adjudications_risk_class",
        "adjudications",
        f"risk IS NULL OR risk IN ({values})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_adjudications_risk_class", "adjudications", type_="check")
```

- [ ] **Step 4: Add service-level validation for a clean error**

In `src/orchestrator/services/evidence.py`, import the vocabulary (add to the existing `from orchestrator.kernel.states import ...` line):

```python
from orchestrator.kernel.states import ActorRole, WorkUnitState, WAIVER_RISK_CLASSES
```

In `_validate_adjudication_fields` (`evidence.py:693`), extend the waiver validation so an out-of-vocab risk raises a clean `waiver_invalid` instead of an `IntegrityError` from the CHECK:

```python
    if outcome == "waived" and (
        failed_evidence_id is None
        or not _text(risk)
        or (risk is not None and risk not in WAIVER_RISK_CLASSES)
        or not _text(follow_up)
        or (expires_at is not None and expires_at <= now)
    ):
        raise DomainError(
            "waiver_invalid",
            "waiver requires failed evidence, a risk class, follow-up, and a future expiry when set",
            None,
        )
```

- [ ] **Step 5: Sweep existing fixtures to valid risk classes**

Replace each out-of-vocab `risk` literal so existing waiver-creating tests satisfy the new CHECK:
- `tests/services/test_waivers.py:54` — change `"risk": "minor compatibility risk",` to `"risk": "medium",`
- `tests/services/test_lifecycle_guards.py:97` — change `risk="accepted" if outcome == "waived" else None,` to `risk="high" if outcome == "waived" else None,`
- `tests/web/test_evidence_pack.py:128` — change `risk="bounded",` to `risk="medium",`
- `tests/services/test_event_publications.py:297` — change `risk="bounded",` to `risk="medium",`

(`tests/services/test_slo_report.py:159` already uses `"low"` — leave it.)

- [ ] **Step 6: Write the failing test**

Add to `tests/services/test_waivers.py` (uses the existing module `command`/`record` helpers and `ready_unit` fixture):

```python
def test_waiver_risk_class_must_be_in_vocabulary(migrated_session: Session, ready_unit) -> None:
    result = record(
        migrated_session,
        command(ready_unit) | {"risk": "catastrophic", "idempotency_key": "waiver-bad-risk"},
    )
    assert isinstance(result, DomainError)
    assert result.code == "waiver_invalid"
```

If `test_waivers.py` has no `command(unit)` factory, inline the existing waiver command dict from the top of the file (see `test_waivers.py:50-62`) with `risk="catastrophic"` instead. Match the file's existing helper style.

- [ ] **Step 7: Run the test to verify it fails, then the migration applies**

Run: `.venv/bin/pytest tests/services/test_waivers.py::test_waiver_risk_class_must_be_in_vocabulary -v`
Expected: PASS once Step 4 is in place (this test exercises the service validation). Then run the full waiver + lifecycle suites to prove the CHECK + sweep hold:

Run: `.venv/bin/pytest tests/services/test_waivers.py tests/services/test_lifecycle_guards.py -q`
Expected: all pass (the migrated fixtures satisfy the new CHECK; the migration ran via the `migrated_engine` fixture at `head`).

- [ ] **Step 8: Commit**

```bash
ruff format src/orchestrator/kernel/states.py src/orchestrator/persistence/models.py src/orchestrator/services/evidence.py migrations/versions/0017_wsp23_waiver_risk_class.py tests/services/test_waivers.py
git add src/orchestrator/kernel/states.py src/orchestrator/persistence/models.py migrations/versions/0017_wsp23_waiver_risk_class.py src/orchestrator/services/evidence.py tests/services/test_waivers.py tests/services/test_lifecycle_guards.py tests/web/test_evidence_pack.py tests/services/test_event_publications.py
git commit -m "feat(wsp23): controlled waiver risk-class vocabulary (structural CHECK)"
```

---

### Task 2: A-static authorizer predicate (a human may pass only judgment-type ACs)

Lets a HUMAN record `passed`/`not_applicable` iff the criterion's `evidence_type ∈ JUDGMENT_TYPES`; leaves verifier and waiver rules unchanged; keeps HUMAN `failed` forbidden.

**Files:**
- Modify: `src/orchestrator/services/evidence.py` (imports; `_criterion_evidence_type` helper; `_authorize_outcome` signature; call site at `evidence.py:231`)
- Test: `tests/services/test_adjudications.py`

**Interfaces:**
- Consumes: `JUDGMENT_TYPES` from `orchestrator.services.verifier_evaluators`; `PackageAcceptanceCriterion` from `orchestrator.persistence.models`.
- Produces: `_authorize_outcome(actor: ActorContext, outcome: str, evidence_type: str | None) -> None`; `_criterion_evidence_type(session, revision_id, ac_id) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_adjudications.py`. First a helper to attach a criterion row (mirrors `test_lifecycle_guards.py`'s direct construction), then three tests:

```python
from orchestrator.persistence.models import Adjudication, PackageAcceptanceCriterion


def add_criterion(session: Session, unit, ac_id: str, evidence_type: str) -> None:
    session.add(
        PackageAcceptanceCriterion(
            work_package_revision_id=unit.work_package_revision_id,
            ac_id=ac_id,
            condition="condition",
            evidence_type=evidence_type,
            evidence="evidence",
            approver="human-1",
        )
    )
    session.flush()


def test_human_may_pass_a_judgment_type_ac(migrated_session: Session, ready_unit) -> None:
    add_criterion(migrated_session, ready_unit, "ac-1", "human.review")
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="reviewed and met",
        idempotency_key="human-pass-1",
    )
    assert isinstance(result, Adjudication)
    assert result.outcome == "passed"
    assert result.decided_by == "human-1"


def test_human_may_not_pass_a_deterministic_ac(migrated_session: Session, ready_unit) -> None:
    add_criterion(migrated_session, ready_unit, "ac-1", "test")
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="looks green to me",
        idempotency_key="human-pass-det",
    )
    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"


def test_human_may_not_record_failed(migrated_session: Session, ready_unit) -> None:
    add_criterion(migrated_session, ready_unit, "ac-1", "human.review")
    result = record_adjudication(
        migrated_session,
        work_package_revision_id=ready_unit.work_package_revision_id,
        work_unit_id=ready_unit.id,
        ac_id="ac-1",
        outcome="failed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="not met",
        idempotency_key="human-failed-1",
    )
    assert isinstance(result, DomainError)
    assert result.code == "role_forbidden"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/services/test_adjudications.py -k "human_may" -v`
Expected: `test_human_may_pass_a_judgment_type_ac` FAILS with `role_forbidden` (HUMAN pass currently disallowed); the two negative tests may already pass — that is fine, they lock the boundary once Step 3 lands.

- [ ] **Step 3: Implement the predicate**

In `src/orchestrator/services/evidence.py`:

Add imports (extend the existing `persistence.models` import list to include `PackageAcceptanceCriterion`, and add the evaluators import):

```python
from orchestrator.services.verifier_evaluators import JUDGMENT_TYPES
```

Add the constant near `NON_WAIVER_OUTCOMES` (`evidence.py:36`):

```python
# Outcomes a HUMAN may record on an intrinsically-judgment AC (see _authorize_outcome).
HUMAN_ADJUDICABLE_OUTCOMES = frozenset({"passed", "not_applicable"})
```

Add the lookup helper (near `_authorize_outcome`, `evidence.py:659`):

```python
def _criterion_evidence_type(
    session: Session, revision_id: uuid.UUID, ac_id: str
) -> str | None:
    return session.scalar(
        select(PackageAcceptanceCriterion.evidence_type).where(
            PackageAcceptanceCriterion.work_package_revision_id == revision_id,
            PackageAcceptanceCriterion.ac_id == ac_id,
        )
    )
```

Replace `_authorize_outcome` (`evidence.py:659-665`) with:

```python
def _authorize_outcome(actor: ActorContext, outcome: str, evidence_type: str | None) -> None:
    if outcome == "waived":
        allowed = actor.role is ActorRole.HUMAN
    elif actor.role is ActorRole.VERIFIER:
        allowed = outcome in NON_WAIVER_OUTCOMES
    elif actor.role is ActorRole.HUMAN and outcome in HUMAN_ADJUDICABLE_OUTCOMES:
        # A-static: a human resolves only intrinsically-judgment ACs. A deterministic type is
        # verifier-owned; keying on the static type (not the current evaluation) closes the
        # automated_check-before-CI-evidence window.
        allowed = evidence_type is not None and evidence_type.strip().lower() in JUDGMENT_TYPES
    else:
        allowed = False
    if not allowed:
        raise DomainError("role_forbidden", "actor may not record this outcome", None)
```

Update the call site in `record_adjudication` (`evidence.py:231`), replacing `_authorize_outcome(actor, outcome)` with:

```python
        evidence_type = _criterion_evidence_type(session, work_package_revision_id, ac_id)
        _authorize_outcome(actor, outcome, evidence_type)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/services/test_adjudications.py -v`
Expected: all pass — the new human-pass tests plus the existing verifier/worker/idempotency tests (verifier path is unchanged; `evidence_type` is `None` for the fixture's criterion-less `ac-1` but the verifier branch ignores it).

- [ ] **Step 5: Commit**

```bash
ruff format src/orchestrator/services/evidence.py tests/services/test_adjudications.py
git add src/orchestrator/services/evidence.py tests/services/test_adjudications.py
git commit -m "feat(wsp23): human may adjudicate only intrinsically-judgment ACs (A-static)"
```

---

### Task 3: Human `passed` satisfies the same completion gate

Proves a human-recorded `passed` flows through the unchanged completion gate — the gate is not modified, only exercised via the new authorization path.

**Files:**
- Test: `tests/services/test_lifecycle_guards.py`

**Interfaces:**
- Consumes: `record_adjudication` (Task 2 authorization); `add_criterion` pattern; the file's existing `submitted_unit`, `completion_command`, `FixedClock`, `transition_unit`.

- [ ] **Step 1: Write the failing test**

Add to `tests/services/test_lifecycle_guards.py`. It records a human `passed` on a judgment AC through `record_adjudication`, then drives the real completion transition:

```python
from orchestrator.services.evidence import record_adjudication


def test_human_passed_satisfies_completion_gate(migrated_session: Session) -> None:
    unit = submitted_unit(migrated_session)
    migrated_session.add(
        PackageAcceptanceCriterion(
            work_package_revision_id=unit.work_package_revision_id,
            ac_id="ac-1",
            condition="condition",
            evidence_type="human.review",
            evidence="evidence",
            approver="human-1",
        )
    )
    migrated_session.commit()

    result = record_adjudication(
        migrated_session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit.id,
        ac_id="ac-1",
        outcome="passed",
        actor=ActorContext("human-1", ActorRole.HUMAN),
        rationale="reviewed and met",
        idempotency_key="human-pass-gate",
    )
    assert isinstance(result, Adjudication)

    migrated_session.refresh(unit)
    transition_unit(migrated_session, completion_command(unit), clock=FixedClock())
    migrated_session.refresh(unit)
    assert unit.state == WorkUnitState.COMPLETED
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/pytest tests/services/test_lifecycle_guards.py::test_human_passed_satisfies_completion_gate -v`
Expected: PASS (no production code change — the gate already accepts a satisfying terminal `passed`; this confirms the human path reaches it). If it fails on unit state prerequisites, confirm `submitted_unit` leaves the unit in `SUBMITTED` and that `completion_command` targets `COMPLETED` (both already defined in the file).

- [ ] **Step 3: Commit**

```bash
ruff format tests/services/test_lifecycle_guards.py
git add tests/services/test_lifecycle_guards.py
git commit -m "test(wsp23): human-recorded passed satisfies the unchanged completion gate"
```

---

### Task 4: Thin-waiver audit reporter

Adds a read-only `consistency.py` reporter that surfaces current (unsuperseded) waivers that are structurally thin — expired, or carrying a risk outside the vocabulary (legacy defense). No gate, no migration.

**Files:**
- Modify: `src/orchestrator/services/consistency.py` (add `_WAIVER_HARDENING` SQL, `_waiver_findings`, include in `check_consistency`)
- Test: `tests/services/test_consistency.py`

**Interfaces:**
- Consumes: `WAIVER_RISK_CLASSES` (Task 1); the existing `ConsistencyFinding` dataclass and `check_consistency`.
- Produces: findings with `check="waiver_hardening"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/services/test_consistency.py` (match the file's existing setup helpers; it already builds units and adjudications). Use `add_adjudication` from `test_lifecycle_guards` if imported there, or build an `Adjudication` inline as that helper does. Example:

```python
from datetime import UTC, datetime, timedelta

from orchestrator.services.consistency import check_consistency
from tests.services.test_lifecycle_guards import add_adjudication, submitted_unit


def test_expired_waiver_is_reported_as_thin(migrated_session: Session) -> None:
    unit = submitted_unit(migrated_session)
    past = datetime(2026, 7, 5, tzinfo=UTC) - timedelta(days=1)
    add_adjudication(
        migrated_session, unit, ac_id="ac-1", outcome="waived", expires_at=past
    )
    migrated_session.commit()

    report = check_consistency(migrated_session)
    waiver_findings = [f for f in report.findings if f.check == "waiver_hardening"]
    assert any(f.work_unit_id == unit.id and f.subject == "ac-1" for f in waiver_findings)


def test_healthy_waiver_is_not_reported(migrated_session: Session) -> None:
    unit = submitted_unit(migrated_session)
    add_adjudication(migrated_session, unit, ac_id="ac-1", outcome="waived")
    migrated_session.commit()

    report = check_consistency(migrated_session)
    assert not [f for f in report.findings if f.check == "waiver_hardening"]
```

Note: `add_adjudication` sets `risk="high"` for waivers after Task 1's sweep (a valid class), so the healthy waiver is clean; the expired one is thin only by its past `expires_at`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/services/test_consistency.py -k "waiver" -v`
Expected: FAIL — `check="waiver_hardening"` findings do not exist yet.

- [ ] **Step 3: Implement the reporter**

In `src/orchestrator/services/consistency.py`:

Add the import:

```python
from orchestrator.kernel.states import WAIVER_RISK_CLASSES
```

Add the SQL near `SATISFIED_ACS` (`consistency.py:72`). It selects unsuperseded waiver terminals that are expired or carry an out-of-vocabulary risk:

```python
_THIN_WAIVERS = text(
    """
    WITH terminals AS (
        SELECT a.work_unit_id, a.ac_id, a.risk, a.expires_at
        FROM adjudications a
        WHERE a.outcome = 'waived'
          AND NOT EXISTS (
              SELECT 1 FROM adjudications s
              WHERE s.supersedes_adjudication_id = a.id
          )
    )
    SELECT work_unit_id, ac_id, risk, expires_at
    FROM terminals
    WHERE (expires_at IS NOT NULL AND expires_at <= :now)
       OR risk IS NULL
       OR NOT (risk = ANY(:risk_classes))
    ORDER BY work_unit_id, ac_id
    """
)
```

Add the finding builder (after `_completion_findings`):

```python
def _waiver_findings(session: Session, now: datetime) -> tuple[ConsistencyFinding, ...]:
    """Surface current waivers that are structurally thin -- expired, or (legacy defense) a risk
    outside the controlled vocabulary. Reporting only; the completion gate already refuses an
    expired waiver, but nothing else makes an outlived accepted-risk visible."""
    rows = session.execute(
        _THIN_WAIVERS, {"now": now, "risk_classes": list(WAIVER_RISK_CLASSES)}
    ).all()
    findings: list[ConsistencyFinding] = []
    for work_unit_id, ac_id, risk, expires_at in rows:
        if expires_at is not None and expires_at <= now:
            detail = "waiver expired; its accepted risk has outlived the approved window"
            observed = f"expired at {expires_at.isoformat()}"
        else:
            detail = "waiver risk is outside the controlled vocabulary"
            observed = f"risk={risk!r}"
        findings.append(
            ConsistencyFinding(
                check="waiver_hardening",
                work_unit_id=work_unit_id,
                subject=ac_id,
                detail=detail,
                observed=observed,
                expected="a current waiver with an in-vocabulary risk class",
            )
        )
    return tuple(findings)
```

Include it in `check_consistency` (`consistency.py:116`):

```python
    return ConsistencyReport(
        checked_at=now,
        findings=(
            *_evidence_head_findings(session),
            *_completion_findings(session, now),
            *_waiver_findings(session, now),
        ),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/services/test_consistency.py -v`
Expected: all pass — the expired waiver is reported under `waiver_hardening`, the healthy one is not, and existing consistency checks are unaffected.

- [ ] **Step 5: Commit**

```bash
ruff format src/orchestrator/services/consistency.py tests/services/test_consistency.py
git add src/orchestrator/services/consistency.py tests/services/test_consistency.py
git commit -m "feat(wsp23): thin-waiver audit reporter (expired / out-of-vocab risk)"
```

---

### Task 5: `/review` per-AC adjudication form + route

Adds the forward-auth `/review` route recording a canonical outcome via `record_adjudication`, the GET context that renders one form per required AC (outcomes filtered by whether the AC is intrinsically-judgment), and the template. Drives the route end-to-end in a web test.

**Files:**
- Modify: `src/orchestrator/web.py` (new `POST /units/{unit_id}/adjudication` route; extend `detail` GET at `web.py:434` to build per-AC form context; add `_adjudicatable_criteria` helper)
- Modify: `src/orchestrator/templates/unit.html` (add per-AC adjudication forms)
- Test: `tests/web/` (new test module, mirroring the existing `/review` route tests)

**Interfaces:**
- Consumes: `record_adjudication` (Tasks 1–2); `load_required_criteria` from `orchestrator.services.verifier_criteria`; `JUDGMENT_TYPES` from `orchestrator.services.verifier_evaluators`; `WAIVER_RISK_CLASSES`; the existing `_human`, `_require_form`, `_issue_token`, `_redirect`, `_render`, `_projection`.
- Produces: route name `adjudication`; GET context keys `adjudicatable_criteria`, `adjudication_csrf_tokens`, `adjudication_idempotency_keys`, `waiver_risk_classes`.

- [ ] **Step 1: Write the failing web test**

Create `tests/web/test_adjudication_route.py`. Follow the auth/CSRF harness the other `/review` route tests use (find one, e.g. the review-verdict or authority-approval route test, and reuse its client fixture + token-issuing helper). The test must (a) POST a human `passed` for a judgment AC and (b) assert persistence via `expire_all()` + re-read:

```python
def test_human_pass_via_review_route_persists(review_client, review_unit_with_judgment_ac) -> None:
    unit = review_unit_with_judgment_ac
    form = post_review_form(
        review_client,
        f"/review/units/{unit.id}/adjudication",
        unit_id=unit.id,
        action="adjudication",
        fields={
            "ac_id": "ac-1",
            "outcome": "passed",
            "rationale": "reviewed and met",
            "expected_version": unit.version,
        },
    )
    assert form.status_code in (303, 302)

    with Session(review_client.engine) as verify:
        verify.expire_all()
        row = current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-1")
        assert row is not None
        assert row.outcome == "passed"
        assert row.decided_by  # a human actor id
```

Adapt `review_client`, `post_review_form`, and the `review_unit_with_judgment_ac` fixture to the concrete harness in the existing `/review` web tests (the fixture must create the unit AND a `PackageAcceptanceCriterion` row for `ac-1` with `evidence_type="human.review"`, and put the unit in a reviewable state). Import `current_adjudication` from `orchestrator.services.evidence`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/web/test_adjudication_route.py -v`
Expected: FAIL with 404 (route does not exist yet).

- [ ] **Step 3: Add the `_adjudicatable_criteria` GET helper**

In `src/orchestrator/web.py`, add imports:

```python
from orchestrator.services.verifier_criteria import load_required_criteria
from orchestrator.services.verifier_evaluators import JUDGMENT_TYPES
from orchestrator.kernel.states import WAIVER_RISK_CLASSES
from orchestrator.services.evidence import record_adjudication
```

Add a helper (near `_projection`, `web.py:178`) that returns the per-AC form descriptors, tolerant of a criteria-load failure:

```python
def _adjudicatable_criteria(
    session: Session, unit: WorkUnit, revision: WorkPackageRevision
) -> tuple[dict[str, Any], ...]:
    try:
        criteria = load_required_criteria(session, unit, revision)
    except DomainError:
        return ()
    return tuple(
        {
            "ac_id": criterion.ac_id,
            "evidence_type": criterion.evidence_type,
            "is_judgment": criterion.evidence_type.strip().lower() in JUDGMENT_TYPES,
        }
        for criterion in criteria
    )
```

- [ ] **Step 4: Extend the `detail` GET to build per-AC form context**

In `detail` (`web.py:434-469`), after the reconciliation-condition block and before `return _render(...)`, add:

```python
    criteria = _adjudicatable_criteria(session, context["unit"], context["revision"])
    adjudication_keys = {row["ac_id"]: str(uuid.uuid4()) for row in criteria}
    context["adjudicatable_criteria"] = criteria
    context["adjudication_idempotency_keys"] = adjudication_keys
    context["adjudication_csrf_tokens"] = {
        row["ac_id"]: _issue_token(
            request, actor, unit_id, "adjudication", adjudication_keys[row["ac_id"]]
        )
        for row in criteria
    }
    context["waiver_risk_classes"] = WAIVER_RISK_CLASSES
```

- [ ] **Step 5: Add the POST route**

In `src/orchestrator/web.py`, add near the other `/review` unit routes (e.g. after `approve_authority`, `web.py:579`):

```python
@router.post("/units/{unit_id}/adjudication")
def adjudicate(
    request: Request,
    unit_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    expected_version: Annotated[int, Form()],
    ac_id: Annotated[str, Form(min_length=1)],
    outcome: Annotated[str, Form()],
    rationale: Annotated[str, Form(min_length=1)],
    idempotency_key: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
    confirm: Annotated[str | None, Form()] = None,
    failed_evidence_id: Annotated[uuid.UUID | None, Form()] = None,
    risk: Annotated[str | None, Form()] = None,
    follow_up: Annotated[str | None, Form()] = None,
    expires_at: Annotated[datetime | None, Form()] = None,
) -> RedirectResponse:
    _human(actor)
    _require_form(request, actor, unit_id, "adjudication", csrf_token, idempotency_key, confirm)
    unit = session.get(WorkUnit, unit_id)
    if unit is None:
        raise DomainError("work_unit_not_found", "work unit does not exist", None)
    result = record_adjudication(
        session,
        work_package_revision_id=unit.work_package_revision_id,
        work_unit_id=unit_id,
        ac_id=ac_id,
        outcome=outcome,
        actor=actor,
        rationale=rationale,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
        failed_evidence_id=failed_evidence_id,
        risk=risk,
        follow_up=follow_up,
        expires_at=expires_at,
    )
    if isinstance(result, DomainError):
        raise result
    return _redirect(unit_id)
```

Confirm `datetime` is imported in `web.py`; if not, add `from datetime import datetime`.

- [ ] **Step 6: Add the template forms**

In `src/orchestrator/templates/unit.html`, inside the "Human actions" block (after the "Review outcome" form, `unit.html:40`), add per-AC forms. Judgment ACs offer pass/N-A/waive; deterministic ACs offer waive only:

```html
{% if adjudicatable_criteria %}<h3>Adjudicate acceptance criteria</h3>
{% for ac in adjudicatable_criteria %}
<form method="post" action="/review/units/{{ unit.id }}/adjudication">
  <input type="hidden" name="csrf_token" value="{{ adjudication_csrf_tokens[ac.ac_id] }}">
  <input type="hidden" name="idempotency_key" value="{{ adjudication_idempotency_keys[ac.ac_id] }}">
  <input type="hidden" name="expected_version" value="{{ unit.version }}">
  <input type="hidden" name="ac_id" value="{{ ac.ac_id }}">
  <fieldset><legend>{{ ac.ac_id }} <small>({{ ac.evidence_type }})</small></legend>
  <label for="outcome-{{ ac.ac_id }}">Outcome</label>
  <select id="outcome-{{ ac.ac_id }}" name="outcome">
    {% if ac.is_judgment %}<option value="passed">Passed</option><option value="not_applicable">Not applicable</option>{% endif %}
    <option value="waived">Waived</option>
  </select>
  <label for="rationale-{{ ac.ac_id }}">Rationale</label>
  <textarea id="rationale-{{ ac.ac_id }}" name="rationale" required></textarea>
  <details><summary>Waiver fields (required only when waiving)</summary>
    <label for="failed-{{ ac.ac_id }}">Failed evidence id</label>
    <input id="failed-{{ ac.ac_id }}" name="failed_evidence_id">
    <label for="risk-{{ ac.ac_id }}">Risk class</label>
    <select id="risk-{{ ac.ac_id }}" name="risk"><option value="">—</option>{% for cls in waiver_risk_classes %}<option value="{{ cls }}">{{ cls }}</option>{% endfor %}</select>
    <label for="followup-{{ ac.ac_id }}">Follow-up</label>
    <textarea id="followup-{{ ac.ac_id }}" name="follow_up"></textarea>
    <label for="expires-{{ ac.ac_id }}">Expires at (optional, ISO 8601)</label>
    <input id="expires-{{ ac.ac_id }}" name="expires_at">
  </details>
  <label class="confirm"><input type="checkbox" name="confirm" value="yes" required> I confirm this adjudication.</label>
  <button type="submit">Record adjudication</button>
  </fieldset>
</form>
{% endfor %}{% endif %}
```

- [ ] **Step 7: Run the web test to verify it passes**

Run: `.venv/bin/pytest tests/web/test_adjudication_route.py -v`
Expected: PASS — the route records the human `passed`, persists it (re-read confirms), and redirects.

- [ ] **Step 8: Add negative-authorization web coverage**

Add a second test asserting a human `passed` POST for a **deterministic** AC (`evidence_type="test"`) is rejected (the route raises the `role_forbidden` `DomainError`, surfaced as the app's error response — assert the persisted adjudication is absent):

```python
def test_human_pass_of_deterministic_ac_is_rejected(review_client, review_unit_with_test_ac) -> None:
    unit = review_unit_with_test_ac
    form = post_review_form(
        review_client,
        f"/review/units/{unit.id}/adjudication",
        unit_id=unit.id,
        action="adjudication",
        fields={"ac_id": "ac-1", "outcome": "passed", "rationale": "x", "expected_version": unit.version},
    )
    assert form.status_code >= 400
    with Session(review_client.engine) as verify:
        assert current_adjudication(verify, unit.work_package_revision_id, unit.id, "ac-1") is None
```

Run: `.venv/bin/pytest tests/web/test_adjudication_route.py -v`
Expected: both pass.

- [ ] **Step 9: Commit**

```bash
ruff format src/orchestrator/web.py src/orchestrator/templates/unit.html tests/web/test_adjudication_route.py
git add src/orchestrator/web.py src/orchestrator/templates/unit.html tests/web/test_adjudication_route.py
git commit -m "feat(wsp23): /review per-AC human adjudication form + route"
```

---

### Task 6: Full-suite gate, format debt, and boundary confirmation

Runs the complete gate and confirms the deferred boundary was not crossed.

**Files:** none (verification + any format fixes surfaced).

- [ ] **Step 1: Confirm the boundary test is untouched and green**

Run: `git diff main -- tests/services/test_criterion_evidence_vocabulary.py src/orchestrator/services/verifier_evaluators.py`
Expected: empty diff (no changes). Then:

Run: `.venv/bin/pytest tests/services/test_criterion_evidence_vocabulary.py -v`
Expected: PASS (unchanged) — `automated_test` still requires judgment.

- [ ] **Step 2: Whole-repo format check (pre-existing debt may surface)**

Run: `.venv/bin/ruff format --check .`
If files you did not touch are flagged, that is pre-existing format-debt (verify with `git stash && ruff format --check . ; git stash pop` if unsure). Format only the files this workstream changed; do not sweep unrelated debt into this branch unless `make check` blocks on it, in which case fix it in a clearly-labeled separate commit.

- [ ] **Step 3: Run the full gate**

Run: `make check`
Expected: green, with a real collected-test count (read `collected N items` — exit 0 alone is not proof). Confirm the count increased by the tests added in Tasks 1–5.

- [ ] **Step 4: Code review**

Run `/code-review` on the branch diff against `main`. Address correctness bugs and any simplification opportunities (per Devon's minimal-onion stance: prefer removing over adding).

- [ ] **Step 5: Final commit if review produced changes**

```bash
ruff format <changed files>
git add -A
git commit -m "chore(wsp23): review follow-ups"
```

---

## Self-Review

**Spec coverage:**
- Decision A (static predicate) → Task 2. ✓
- Decision B (pass/N-A/waive form, no per-AC failed) → Task 5 (template offers only those; `HUMAN_ADJUDICABLE_OUTCOMES` excludes `failed`, Task 2). ✓
- Decision C Option 1 (risk-class enum) → Task 1. ✓
- Decision C Option 3 (thin-waiver audit) → Task 4. ✓
- Gate unchanged + human pass flows through it → Task 3 (test) + Global Constraints. ✓
- Boundary respected (`test_criterion_evidence_vocabulary` green) → Task 6 Step 1. ✓
- Reachability/commit discipline (drive the public route, re-read) → Task 5 Steps 1, 7, 8. ✓
- Improvisation-counter consistency (record_adjudication, not commands/) → Global Constraints + Task 5 route uses `record_adjudication`. ✓
- Migration + deploy note → Task 1 (migration); deploy is post-merge (documented in spec, not a code task).

**Placeholder scan:** No TBD/TODO. The web-test harness names (`review_client`, `post_review_form`, `review_unit_with_judgment_ac`) are explicitly flagged to adapt to the existing `/review` test harness — the implementer reuses a concrete pattern rather than inventing one; every production code block is complete.

**Type consistency:** `_authorize_outcome(actor, outcome, evidence_type)` — three-arg form used at the call site (Task 2 Step 3) and in all Task 2 tests. `_criterion_evidence_type(session, revision_id, ac_id) -> str | None` consistent. `WAIVER_RISK_CLASSES` tuple imported identically in models/evidence/consistency/web. `check="waiver_hardening"` used in both the reporter (Task 4 Step 3) and its tests (Task 4 Step 1). Context keys (`adjudicatable_criteria`, `adjudication_csrf_tokens`, `adjudication_idempotency_keys`, `waiver_risk_classes`) match between GET (Task 5 Step 4) and template (Task 5 Step 6).
