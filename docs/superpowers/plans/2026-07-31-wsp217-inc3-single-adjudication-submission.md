# WS-P2.17 Increment 3 — One adjudication submission — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** A human adjudicates a unit's open criteria as **one atomic act** — so sibling forms can no
longer staleness-break each other, and a rejected submission writes nothing.

**Architecture:** Today each acceptance criterion renders its own `/review` form carrying an
`expected_version` fixed at page render. Submitting one bumps the unit version, so every sibling
form is instantly stale. This is a **correctness bug**, not friction: it recorded a wrong outcome on
WS-P2.13 AC-002. `record_adjudication` also calls `session.commit()` itself, so a route that simply
looped over it would commit per-criterion and leave partial writes behind on failure. This increment
extracts a non-committing core, adds a batch entry point that records N adjudications in one
transaction with all-or-nothing semantics, and collapses the form.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI, Jinja2, pytest. Repo: `~/Projects/orchestrator`.

**Spec:** `~/docs/software-delivery-system/2026-07-31-wsp217-human-gate-spec.md` §5.3
(AC-010…AC-013).
**Predecessors:** Increment 1 (`f28b9c2`), Increment 2 (PR #97). Reports in
`~/docs/software-delivery-system/`.

---

## Global Constraints

- **Transaction ownership is a documented invariant in this repo.** Request entry points own the
  transaction and `commit()`; functions invoked *inside* another transaction must never commit. A
  service that flushes but never commits looks correct in tests and is dead in production
  (WS-P2.1 shipped exactly that defect). **A test that asserts persistence must `expire_all()` and
  re-read** — otherwise it is asserting that a call returned an object.
- **All-or-nothing means all-or-nothing.** If any criterion in a submission is refused, **no**
  adjudication row, event, or version bump may persist. AC-011 is the whole point of the increment;
  a partial write is a worse bug than the one being fixed.
- **`record_adjudication` returns `DomainError`, it does not raise it.** Read its existing
  `except` structure before changing anything — it distinguishes `DomainError` (rollback, return),
  `IntegrityError` (rollback, race resolution) and bare `Exception` (rollback, re-raise). Preserve
  all three behaviours.
- **A task boundary is only valid if the tree is green AND the behaviour is coherent at it.**
- **Do not narrow `waived` authority in this increment.** It is a known open item with its own
  proposed rule (a human may waive only a criterion whose current evaluation is a failure). Mixing
  an authorization policy change into a correctness fix lowers the review value of both. If you
  believe it blocks you, stop and report.
- **Do not restructure the unit page.** Evidence rendering (§5.4), the queue (§5.5) and the shared
  decision surface (§5.6) are later increments. This increment changes the adjudication form's
  *submission model*, not the page's information design.
- **Read collected counts, never check colours.** Baseline after Increment 2: **1782 collected →
  1781 passed, 1 skipped**. `make check` exit 0 does not prove tests ran (exit 5 is swallowed).
- **`make check` needs Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR`, and a migrated DB.**
  A bare clone fails ~18 tests unmodified — run a clean-clone control before attributing red.
- **Never run two pytest suites against the test DB concurrently.**
- Run `ruff format` (or `make fix`) before committing.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/orchestrator/services/evidence.py` | Non-committing core + the batch entry point | Modify |
| `src/orchestrator/web.py` | The `/review` adjudication route: one submission, N outcomes | Modify |
| `src/orchestrator/templates/unit.html` | One form for all open criteria | Modify |
| `tests/services/test_adjudications.py` | Batch atomicity and replay tests | Modify |
| `tests/web/test_adjudication_route.py` | Form submission tests | Modify |
| `tests/idempotency/test_matrix.py` | Coverage row, if the route's identity changes | Check |

No migration. No new API route. No schema change. No cross-repo change.

---

### Task 1: Extract a non-committing core — pure refactor

**Files:**
- Modify: `src/orchestrator/services/evidence.py` (`record_adjudication`)
- Test: `tests/services/test_adjudications.py`

**Interfaces:**
- Produces: an internal function that performs one adjudication **without committing** and without
  owning the `except` structure, plus `record_adjudication` retained as the single-criterion entry
  point that opens the transaction, calls the core once, and commits. **Derive the exact signature
  by reading the existing body** — do not copy a signature from this plan.

This task is **behaviour-preserving and must be green at its boundary.** Nothing outside
`evidence.py` changes.

- [ ] **Step 1: Write the persistence pin**

Before refactoring, pin that a single adjudication is genuinely persisted — not merely returned.

```python
def test_a_recorded_adjudication_survives_the_session(migrated_session) -> None:
    # WS-P2.1's defect shape: a service that flushes but never commits returns the right object
    # and writes nothing. Re-read, do not trust the returned instance.
    result = record_adjudication(...)  # use this module's existing fixtures
    assert isinstance(result, Adjudication)
    recorded_id = result.id

    migrated_session.expire_all()
    reread = migrated_session.get(Adjudication, recorded_id)
    assert reread is not None and reread.outcome == result.outcome
```

- [ ] **Step 2: Run it and confirm it PASSES on current code**

```bash
.venv/bin/pytest tests/services/test_adjudications.py -k survives_the_session -v
```
Expected: PASS. This is the guard for Task 1's refactor, not a failing-first control — say so in the
report so it is not mistaken for one.

- [ ] **Step 3: Extract the core**

Move the body between `_validated_subject` and `session.commit()` into the new function. Leave
idempotency locking, replay, the `expected_version` check, and the `except` structure in
`record_adjudication`. **The core must not call `session.commit()`.**

- [ ] **Step 4: Run the full service and web suites**

```bash
.venv/bin/pytest tests/services tests/web -q
```
Expected: PASS with the **same collected count as before the refactor**. Record both numbers.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/evidence.py tests/services/test_adjudications.py
git commit -m "refactor(adjudication): extract a non-committing core, behaviour unchanged"
```

---

### Task 2: The batch entry point, all-or-nothing

**Files:**
- Modify: `src/orchestrator/services/evidence.py`
- Test: `tests/services/test_adjudications.py`

**Interfaces:**
- Consumes: Task 1's core.
- Produces: a batch entry point taking the unit, one `expected_version`, one `idempotency_key`, and
  an ordered collection of per-criterion decisions. Returns the recorded rows, or a single
  `DomainError` — **matching `record_adjudication`'s return-don't-raise convention.**

**Decide and record two things before writing code**, reading the existing helpers rather than
assuming:

1. **Idempotency.** `lock_evidence_idempotency_key` and the replay lookup are per-key. A batch needs
   one key for the whole submission, with replay returning the whole batch. Read
   `_adjudication_replay` and decide whether per-criterion keys derived from the batch key are
   needed for the replay comparison to stay meaningful. **State your choice and why in the report.**
2. **Version.** One `expected_version` is checked once, for the batch. Confirm whether recording N
   adjudications bumps the unit version once or N times, and make the post-condition explicit in a
   test either way.

- [ ] **Step 1: Write the failing atomicity tests**

```python
def test_two_criteria_are_adjudicated_in_one_submission(migrated_session) -> None:
    # AC-010. The bug: each criterion had its own form and its own expected_version fixed at page
    # render, so submitting one staleness-broke the next. This recorded a wrong outcome on
    # WS-P2.13 AC-002.
    ...
    assert both rows exist after expire_all() and a re-read


def test_a_refused_criterion_writes_nothing_at_all(migrated_session) -> None:
    # AC-011. All-or-nothing. Submit one valid decision and one the predicate refuses.
    ...
    result = record_adjudications(...)
    assert isinstance(result, DomainError)

    migrated_session.expire_all()
    # NEITHER row persisted, and the unit version is unchanged.
    assert no adjudication rows exist for either criterion
    assert unit.version == version_before
```

- [ ] **Step 2: Run and verify they FAIL**

Expected: `ImportError` / name not defined. **Paste the verbatim output into the report.**

- [ ] **Step 3: Implement the batch entry point**

One transaction: lock the batch idempotency key, resolve and version-check the unit once, then call
Task 1's core per decision, and commit once at the end. Any `DomainError` from any decision aborts
the whole batch — rollback and return it, unchanged, so the caller can still tell *which* criterion
was refused.

- [ ] **Step 4: Run and verify**

```bash
.venv/bin/pytest tests/services/test_adjudications.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/evidence.py tests/services/test_adjudications.py
git commit -m "feat(adjudication): record a unit's criteria atomically in one submission"
```

---

### Task 3: One form, one submission

**Files:**
- Modify: `src/orchestrator/web.py` (the adjudication route), `src/orchestrator/templates/unit.html`
- Test: `tests/web/test_adjudication_route.py`

**Read the existing route first.** It takes one `ac_id` and one `expected_version` from a per-AC
form and calls `record_adjudication` once. You are changing it to accept N decisions and one
`expected_version`. Preserve the existing CSRF and `confirm` handling exactly — `_require_form`'s
token binds the action, actor, subject and idempotency key, and this increment must not weaken it.

- [ ] **Step 1: Write the failing route tests**

```python
def test_the_form_submits_every_open_criterion_at_once(...) -> None:
    # AC-010 at the HTTP boundary.
    ...


def test_a_blank_criterion_is_not_written(...) -> None:
    # AC-012. Blank is not an outcome. A reviewer who answers two of three criteria must not
    # silently record anything for the third.
    ...


def test_generated_post_deploy_criteria_are_still_excluded_from_the_form(...) -> None:
    # AC-013. The existing rule must survive the rewrite: generated post-deploy criteria are
    # verifier-owned and public adjudication must reject them.
    ...
```

- [ ] **Step 2: Run and verify they FAIL.** Paste the output.

- [ ] **Step 3: Implement**

Collapse the per-criterion forms in `unit.html` into one form containing a fieldset per open
criterion, with one hidden `expected_version`, one CSRF token and one confirm checkbox. Route the
submission to Task 2's batch entry point. **Keep Increment 2's per-criterion option gating** — the
outcomes offered for each criterion still come from `human_may_adjudicate`, and Increment 2's
agreement pin must still pass.

- [ ] **Step 4: Run the web suite and the agreement pin**

```bash
.venv/bin/pytest tests/web -q
```
Expected: PASS, **including Increment 2's form/service agreement test**. If that test now fails, the
collapse has broken the per-criterion gating — fix the collapse, do not relax the pin.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/web.py src/orchestrator/templates/unit.html tests/web/test_adjudication_route.py
git commit -m "feat(review): adjudicate a unit's criteria in one submission"
```

---

### Task 4: The architecture guards and the full gate

**Files:**
- Check: `tests/idempotency/test_matrix.py`, `tests/architecture/`

- [ ] **Step 1: Run the whole-repo scans**

```bash
.venv/bin/pytest tests/idempotency tests/architecture -q
```

The idempotency matrix requires every ingress POST — `/api/v1` **and** `/review` — to have a
coverage row or a reasoned exclusion. If the route's identity or idempotency model changed, its row
must change with it. **Read the matrix's own docstring before editing it.** The route-inventory sets
are exact set-equality: if you did not add or remove a route, they should not need touching — if
they do, something changed that you did not intend.

- [ ] **Step 2: Full gate**

```bash
git status                # clean
make check
```
Expected: PASS. **Record the collected count** and compare to the 1782 baseline.

- [ ] **Step 3: Commit any guard updates**

```bash
git add tests/
git commit -m "test: update ingress coverage for the batched adjudication submission"
```

---

## Self-review notes

- **Spec coverage:** AC-010 → Tasks 2 and 3; AC-011 → Task 2; AC-012 → Task 3; AC-013 → Task 3.
- **Deliberately deferred:** §5.4 evidence rendering, §5.5 the queue, §5.6 the shared decision
  surface — each has its own increment. The known `waived`-authority gap is deferred with a
  recorded proposed rule.
- **Known risk handed over, not assumed away:** Task 2's idempotency and version semantics are
  genuine design choices that depend on helpers this plan does not quote. The plan names them as
  decisions to make and report, rather than asserting an answer — Increment 2's one plan defect was
  a signature asserted without reading the call site.
