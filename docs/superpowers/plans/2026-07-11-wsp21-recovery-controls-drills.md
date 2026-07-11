# WS-P2.1 Recovery Controls, Reconciliation & Scripted Drills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the orchestrator a designed recovery path — detect when pushed external reality contradicts stored lifecycle state, make terminal failures visible and actionable, let a lease-expired worker's evidence be attached without redoing the work, and prove all of it with four re-runnable drills.

**Architecture:** Per **ADR-0002** (as amended 2026-07-11): the orchestrator process stays **push-only and loop-free**. Detection is **hybrid** — on-ingest (post-commit, own transaction) for `github_pr`, `github_check`, and `digest_divergence`; a **detect-pass** for `deploy_split_brain` only, which is time-elapsed by nature and unknowable at ingest. Active pulling lives in a **separate report-only runner** that ships in this repo, imports nothing from `orchestrator.*`, and may call exactly two write endpoints. Conditions are **append-only**; an open condition is one with no resolution row. Nothing auto-merges, auto-completes, or auto-resolves.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL 16, pytest, typer, httpx, pydantic. Package/venv via `uv`.

**Governing docs:** design `docs/superpowers/specs/2026-07-11-wsp21-recovery-controls-drills-design.md` (v4, approved at AC-012); review `…-adversarial-architecture-review.md` (3 rounds, 2 reviewers); `docs/decisions/0002-…md` (amended).

---

## Global Constraints

Every task's requirements implicitly include this section.

- **TDD, always.** Failing test → run it and see it fail → minimal implementation → run it and see it pass → commit. Never write implementation before a red test.
- **Postgres-backed.** Tests use the `tests/services/conftest.py` mold (`migrated_engine` drops/recreates the schema + `alembic upgrade head`). **Never point a drill or dogfood run at `orchestrator_test`** — the fixtures drop and recreate it.
- **`make check` exit 0 does NOT prove tests ran.** The vendored Makefile swallows pytest exit 5 ("no tests collected"). **Read the `collected N items` count**, not the exit code.
- **Detection must never raise.** A malformed or forged correlation is *skipped and counted*, never raised — otherwise a bad correlation field turns a valid observation into a rejected ingest, i.e. a DoS on the observation path.
- **Detection must never write `work_units`.** No transition, no `state`, no `version`. Proven by a test that drives a `COMPLETED` unit through every detection path and asserts state and version unchanged.
- **Every new route must be added to the pinned route inventory** in `tests/architecture/test_scope_guards.py` (POST is pinned today; Task 10 adds the GET pin). A route that is not pinned fails the architecture guard.
- **Never widen an allowlist to make a test green.** If the AC-011 outbound scan trips because a comment merely mentions Coolify, reword the comment.
- **Never weaken an assertion to make a drill green.** The drill is the alarm, not the bug.
- No secret in a tracked file. Commit messages end with the Co-Authored-By / Claude-Session trailers.

---

## ⚠️ Cross-task contracts (CANONICAL — these override any drafting drift)

The four plan sections below were drafted independently and disagreed on eight shared names. **These are the binding definitions.** An implementer must use these exactly.

| # | Contract | CANONICAL value |
|---|---|---|
| 1 | Migration revision | **`0014_wsp21_recovery_controls`**, `down_revision = "0013_ws62_governed_promotion"` |
| 2 | Evidence head index | **`uq_evidence_unsuperseded_head`** on `evidence (work_package_revision_id, work_unit_id, ac_id) WHERE supersedes_evidence_id IS NULL` |
| 3 | Condition writer | **`record_reconciliation_condition(session, command: ConditionCommand) -> ConditionOutcome \| DomainError`**, where `ConditionOutcome` is a frozen dataclass `(condition: ReconciliationCondition, suppressed: bool)`. **It returns `ConditionOutcome`, not the bare row** — callers must be able to count `suppressed_duplicates`. It **commits its own transaction** (§1.8). |
| 4 | `ConditionCommand` fields | `actor, work_unit_id, observation_kind, condition_type, **key_facts**, stored_state, observed_state, detail, observation_id=None, deployment_observation_id=None`. **`key_facts` is required** — it is the hash input. Detection sites supply it: PR → `{"pr_number", "head_sha"}`; check → `{"check_name"}`; deploy → `{"release_artifact_binding_id"}`. |
| 5 | Claim release | **`release_claim(claim: Claim, *, terminal_reason: str, released_at: datetime) -> None`** — takes **no Session** and takes `released_at` as a parameter (the caller reuses its own transaction timestamp). It is the **sole writer** of `Claim.released_at` / `Claim.terminal_reason`. |
| 6 | Split-brain threshold | **`Settings.reconcile_split_brain_stall_seconds: int = 900`**, env `ORCHESTRATOR_RECONCILE_SPLIT_BRAIN_STALL_SECONDS`. **Added in Task 1 only** (Task 8 consumes it; it must not add it again). |
| 7 | Advisory-lock namespaces (**must not collide**) | reconciliation conditions → **`0x57503231`**; evidence head → **`0x57503232`**. (Both drafts used `0x57503231`; that is a real lock collision.) |
| 8 | AC-011 forbidden-outbound module list | `services/reconciliation.py`, `services/reconciliation_detection.py`, `services/pr_bindings.py`, `services/in_flight.py`, `services/dead_letter.py`, `services/consistency.py`. **There is no `services/evidence_recovery.py`** — `recover_evidence` lives in `services/evidence.py`. |

**Also reconciled:** the GET route inventory pin (`test_production_get_route_inventory_is_explicit`) is created **once, in Task 10**; Task 14 extends its literal set. And Task 14's `in_flight.py` draft contains a stray `post_deploy = session.get_bind() and None` no-op line — **delete it.**

**Gap closed by this review:** the `/review` **resolution route** was produced by no drafter — `record_resolution` (Task 5) had no HTTP surface, so every condition would have stayed open forever. It is now **Task 5b** below.

---

## Task index

| # | Task | AC | Depends on |
|---|---|---|---|
| 1 | Migration `0014` + ORM models + config knob | foundation | — |
| 2 | Shared primitive: circuit-breaker predicate split | AC-005 prep | — |
| 3 | Shared primitive: `release_claim` | AC-004 prep | — |
| 4 | `unit_pr_binding` service (write-once verification head) | AC-001 prep | 1 |
| 5 | Reconciliation condition + resolution service | AC-001..003 | 1 |
| 5b | `/review` resolution route (**gap closed in review**) | AC-001..003 | 5 |
| 6 | On-ingest `github_pr` detection | AC-001 | 4, 5 |
| 7 | On-ingest `github_check` + route-layer `digest_divergence` | AC-002 | 6 |
| 8 | Detect-pass: `deploy_split_brain` | AC-003 | 1, 7 |
| 9 | `recover-evidence` (supersede the head — the wedge) | AC-004 | 1, 3 |
| 10 | Dead-letter view (+ the GET route pin) | AC-005 | 2 |
| 11 | `requeue` (the only new recovery action) | AC-006 | 10 |
| 12 | Duplicate-delivery idempotency matrix | AC-007 | 5b, 8, 9, 11 |
| 13 | Projection-vs-source consistency check | AC-008 | 1, 10 |
| 14 | `GET /in-flight-units` read surface | AC-009 | 4, 10 |
| 15 | The report-only reconciliation runner | AC-009 | 8, 14 |
| 16 | Four scripted recovery drills | AC-010 | all |
| 17 | AC-011 invariant scan | AC-011 | all |

Full per-task detail (files, interfaces, and every TDD step with real test and implementation code) is carried in the four companion sections, which are the drafted output pinned to the contracts above:

- **Tasks 1–5:** `docs/superpowers/plans/wsp21/tasks-01-05.md`
- **Tasks 6–9:** `docs/superpowers/plans/wsp21/tasks-06-09.md`
- **Tasks 10–13:** `docs/superpowers/plans/wsp21/tasks-10-13.md`
- **Tasks 14–17:** `docs/superpowers/plans/wsp21/tasks-14-17.md`

---

## Task 5b: the reconciliation resolution route (gap closed during self-review)

**Why this exists:** Task 5 produces `record_resolution(...)`, but **no drafter gave it an HTTP surface.** Without one, `open_conditions()` never shrinks: every condition stays open forever, the AC-008 consistency check's "no open condition implies an illegal auto-mutation" invariant is permanently violated, and the package's own `follow_up.signals` ("a reconciliation_required condition that had no correct operator action") is guaranteed on day one.

**Files:**
- Modify: `src/orchestrator/web.py` — add the `/review` route beside the existing unit-review routes
- Modify: `src/orchestrator/api/schemas.py` — add `ResolutionCommandModel`
- Modify: `tests/architecture/test_scope_guards.py` — pinned POST inventory
- Test: `tests/web/test_reconciliation_resolution_route.py`

**Interfaces:**
- Consumes: `record_resolution(session, command: ResolutionCommand) -> ReconciliationResolution | DomainError` and `open_conditions(session, work_unit_id=None)` (Task 5); `ReconciliationCondition` (Task 1).
- Produces: `POST /review/reconciliation/conditions/{condition_id}/resolution`.

**It is a `/review` (HUMAN) route, not `/api`.** `record_resolution` requires `ActorRole.HUMAN` — detection must never auto-resolve (invariant #4) — and production `/api` strips the Authentik headers, so a human actor cannot reach an `/api` route at all. It must ride the `/review` forward-auth chain.

- [ ] **Step 5b.1 — Write the failing test.** Create `tests/web/test_reconciliation_resolution_route.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.kernel.states import ActorRole
from orchestrator.persistence.models import ReconciliationCondition, ReconciliationResolution
from orchestrator.services.lifecycle import ActorContext
from orchestrator.services.reconciliation import (
    ConditionCommand,
    open_conditions,
    record_reconciliation_condition,
)
from tests.services.test_dependencies import register_unit

SYSTEM = ActorContext("system", ActorRole.SYSTEM)


def _condition(session: Session) -> ReconciliationCondition:
    unit = register_unit(session, "resolve-route")
    session.commit()
    outcome = record_reconciliation_condition(
        session,
        ConditionCommand(
            actor=SYSTEM,
            work_unit_id=unit.id,
            observation_kind="github_check",
            condition_type="check_result_flip",
            key_facts={"check_name": "Quality"},
            stored_state={"conclusion": "success"},
            observed_state={"conclusion": "failure"},
            detail="Quality flipped after verification read it",
        ),
    )
    return outcome.condition


def test_a_human_can_resolve_a_condition_and_it_leaves_the_open_set(
    review_client: TestClient, migrated_session: Session
) -> None:
    condition = _condition(migrated_session)
    assert [row.id for row in open_conditions(migrated_session)] == [condition.id]

    response = review_client.post(
        f"/review/reconciliation/conditions/{condition.id}/resolution",
        data={
            "decision": "corrected",
            "rationale": "Re-ran the check; it is green.",
            "idempotency_key": "resolve-route-1",
        },
    )

    assert response.status_code in {200, 303}
    migrated_session.expire_all()
    assert list(migrated_session.scalars(select(ReconciliationResolution)))
    assert open_conditions(migrated_session) == ()


def test_resolving_twice_is_refused(review_client: TestClient, migrated_session: Session) -> None:
    condition = _condition(migrated_session)
    body = {
        "decision": "accepted",
        "rationale": "Acknowledged.",
        "idempotency_key": "resolve-route-2",
    }
    review_client.post(f"/review/reconciliation/conditions/{condition.id}/resolution", data=body)

    second = review_client.post(
        f"/review/reconciliation/conditions/{condition.id}/resolution",
        data={**body, "decision": "dismissed", "idempotency_key": "resolve-route-3"},
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "condition_already_resolved"
```

- [ ] **Step 5b.2 — Run it; expect failure.** `uv run pytest tests/web/test_reconciliation_resolution_route.py -x` → `404` on the route.

- [ ] **Step 5b.3 — Minimal impl.** In `src/orchestrator/web.py`, beside the existing `/review/units/{unit_id}/retry` route:

```python
@router.post("/reconciliation/conditions/{condition_id}/resolution")
def resolve_reconciliation_condition(
    condition_id: UUID,
    request: Request,
    actor: ReviewActorDep,
    session: SessionDep,
) -> object:
    # FastAPI does not inject Form() inside a Depends — read the form directly.
    form = _form(request)
    return _raise_error(
        record_resolution(
            session,
            ResolutionCommand(
                actor=actor,
                condition_id=condition_id,
                decision=form.get("decision", ""),
                rationale=form.get("rationale", ""),
                idempotency_key=form.get("idempotency_key", ""),
            ),
        )
    )
```

Add `"/review/reconciliation/conditions/{condition_id}/resolution"` to the pinned POST inventory in `tests/architecture/test_scope_guards.py`.

- [ ] **Step 5b.4 — Run; expect pass.** `uv run pytest tests/web/test_reconciliation_resolution_route.py tests/architecture/test_scope_guards.py -q`

- [ ] **Step 5b.5 — Commit.**
```bash
git add src/orchestrator/web.py src/orchestrator/api/schemas.py tests/web/test_reconciliation_resolution_route.py tests/architecture/test_scope_guards.py && git commit -m "$(cat <<'EOF'
WS-P2.1: add the /review resolution route for reconciliation conditions

record_resolution had no HTTP surface, so open_conditions() could never shrink: every
condition would stay open forever and the AC-008 "no open condition implies an illegal
auto-mutation" invariant would be violated on day one. It is a /review (HUMAN) route because
resolution is an operator decision — detection must never auto-resolve — and production /api
strips the Authentik headers, so a human actor cannot reach an /api route at all.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017dGd5vqakETSqrGuyPHCTN
EOF
)"
```

---

## Self-review record

**1. Spec coverage.** Every AC maps to at least one task: AC-001 → 4, 5, 6; AC-002 → 7; AC-003 → 1, 8; AC-004 → 1, 3, 9; AC-005 → 2, 10; AC-006 → 11; AC-007 → 12; AC-008 → 13; AC-009 → 14, 15; AC-010 → 16; AC-011 → 17. AC-012 (design approval) is **done** — approved 2026-07-11, recorded as chained factory event `evt-728df5d0…`. AC-013 (merge) is Devon's, after Quality-green.

**2. Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Every code step carries real code. Two drafting artifacts were caught and corrected: the stray no-op line in `in_flight.py`, and the non-existent `services/evidence_recovery.py` in the AC-011 module list.

**3. Type consistency.** Eight cross-task conflicts found and pinned in the contracts table above — including a **real advisory-lock collision** (both the reconciliation service and the evidence-head lock had been assigned the same namespace `0x57503231`, which would have made two unrelated writers serialize against each other) and a **return-type mismatch** (`record_reconciliation_condition` was defined to return the bare row but consumed as if it returned a `suppressed` flag, so every `suppressed_duplicates` counter would have been dead code).

**4. Gap.** The `/review` resolution route existed in no drafter's output; added as Task 5b.
