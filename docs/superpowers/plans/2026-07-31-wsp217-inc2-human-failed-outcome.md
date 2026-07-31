# WS-P2.17 Increment 2 — Human adjudication authority — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** One authorization predicate decides who may adjudicate a criterion; a human can record
`failed`; and the `/review` form offers exactly what the service will accept.

**Architecture:** `JUDGMENT_TYPES` currently has **three independent consumers** — evaluation
(`verifier_evaluators.evaluate_criterion`), authorization (`evidence._authorize_outcome`), and the
form's `is_judgment` flag (`web.py:203`). Increment 1 moved evaluation onto the new
`floor_for()` and left the other two behind, which **opened a fail-open in `main`**: a human may
record `passed` on an `automated_test` criterion the verifier would now resolve deterministically.
This increment introduces a single predicate expressing *may a human decide this criterion right
now*, routes all three consumers through it, and then adds `failed` to the human vocabulary.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI, Jinja2, pytest. Repo: `~/Projects/orchestrator`.

**Spec:** `~/docs/software-delivery-system/2026-07-31-wsp217-human-gate-spec.md` §5.2, ruling R1.
**Predecessor:** Increment 1, merged as `f28b9c2`. Report:
`~/docs/software-delivery-system/2026-07-31-wsp217-inc1-build-report.md`.

---

## Global Constraints

- **The authorization rule, in full.** A human may adjudicate a criterion when **either**:
  - **(a)** its floor is `human` (`floor_for(criterion.evidence_type) == "human"`); **or**
  - **(b)** its floor is `deterministic_permitted`, its **current** evaluation is
    `judgment_required`, **and the verifier has explicitly routed the unit to human review**
    (the unit is in `awaiting_review`).

  Clause (b) exists because Increment 1 made *deterministic-floored-but-currently-asking* a common
  state; without it those criteria are adjudicable by **no actor at all** and units stall in
  `awaiting_review`, unable to complete and unable to be failed. The unit-state condition is what
  closes the "`automated_check` before CI evidence arrives" window that the existing
  `_authorize_outcome` comment describes — **by timing, which is the real concern, rather than by
  type, which was a proxy for it.**

- **A task boundary is only valid if the tree is green AND the behaviour is coherent at it.**
  Increment 1's Task 2 violated this: it was declared behaviour-preserving, was not, and its commit
  was red until the next task. Do not split a mechanism from the semantics that make it correct.

- **Do not register derived collections in `VOCABULARY_REGISTRY`.** The cross-boundary detector
  structurally excludes derived/union collections (`A | B`, `A - {…}`). Increment 1's plan predicted
  a registration that would have redded the suite. If you believe a registration is needed, run
  `tests/architecture` first and let the guard tell you.

- **`make check` exit 0 does not prove tests ran.** Quote `collected N items` in every completion
  claim. Exit code 5 is swallowed.
- **`make check` needs Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR`, and a migrated DB.**
  A bare clone fails ~18 tests *unmodified*. Run a clean-clone control before attributing red.
- **Never run two pytest suites against the test DB concurrently.**
- Baseline to beat: `main` at `f28b9c2` collects **1769 items → 1768 passed, 1 skipped**.
- Run `ruff format` (or `make fix`) before committing.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/orchestrator/services/verifier_evaluators.py` | Home of the shared predicate (it already owns `floor_for` and `evaluate_criterion`) | Modify |
| `src/orchestrator/services/evidence.py` | `_authorize_outcome` routes through the predicate; `HUMAN_ADJUDICABLE_OUTCOMES` gains `failed` | Modify |
| `src/orchestrator/web.py:203` | `is_judgment` routes through the same predicate | Modify |
| `src/orchestrator/templates/unit.html` | Offer `failed` where the service will accept it | Modify |
| `tests/services/test_adjudications.py` | Authorization control tests | Modify |
| `tests/web/test_adjudication_route.py` | Form/service agreement pin | Modify |
| `docs/operations/production-drill-adaptations.md` | Remove the obsolete `evidence_type: "test"` authoring rule | Modify |

No migration. No new route. No schema change. No cross-repo change.

---

### Task 1: The shared predicate, with both fail-open controls

**Files:**
- Modify: `src/orchestrator/services/verifier_evaluators.py`
- Test: `tests/services/test_adjudications.py`

**Interfaces:**
- Consumes: `floor_for`, `evaluate_criterion` (Increment 1).
- Produces: `human_may_adjudicate(criterion, evidence, unit_state) -> bool`. Tasks 2–4 consume it.
  Determine the exact parameter types by reading the call site in
  `services/evidence.py::record_adjudication` — `unit` and `session` are both in scope there, and
  the criterion's evidence type is fetched by `_criterion_evidence_type`. **Reuse the verifier's
  existing current-evidence lookup rather than writing a second one** — a divergent second lookup is
  the defect class this increment exists to close.

- [ ] **Step 1: Write both control tests**

```python
def test_a_human_may_not_decide_a_criterion_the_machine_owns() -> None:
    # FAIL-OPEN CONTROL (the mirror of R1). `automated_test` floors to deterministic_permitted
    # after Increment 1, so readable evidence resolves it. A human must not pre-empt that.
    criterion = PackageAcceptanceCriterion(ac_id="AC-001", evidence_type="automated_test")
    readable = Evidence(evidence_type="pytest", payload={"status": "pass"})

    assert human_may_adjudicate(criterion, readable, "awaiting_review") is False


def test_a_human_may_decide_a_human_floored_criterion_in_any_state() -> None:
    criterion = PackageAcceptanceCriterion(ac_id="AC-001", evidence_type="human_review")

    assert human_may_adjudicate(criterion, None, "submitted") is True


def test_a_human_may_decide_a_deterministic_criterion_only_once_the_verifier_has_asked() -> None:
    # Clause (b). Before the verifier routes to human review, evidence may still arrive -- this is
    # the automated_check-before-CI window, closed by STATE rather than by type.
    criterion = PackageAcceptanceCriterion(ac_id="AC-001", evidence_type="automated_check")

    assert human_may_adjudicate(criterion, None, "verifying") is False
    assert human_may_adjudicate(criterion, None, "awaiting_review") is True
```

- [ ] **Step 2: Run and verify they FAIL**

```bash
.venv/bin/pytest tests/services/test_adjudications.py -k human_may -v
```
Expected: `ImportError` / `cannot import name 'human_may_adjudicate'`.
**Paste the verbatim output into the report.** If any passes here, the test is wrong — stop.

- [ ] **Step 3: Implement the predicate**

In `verifier_evaluators.py`, below `floor_for`:

```python
# The single answer to "may a human decide this criterion right now?" -- consumed by the
# adjudication authorization gate AND by the /review form, so the form can never offer an outcome
# the service will refuse. Increment 1 moved EVALUATION onto the floor and left authorization and
# the form keyed on JUDGMENT_TYPES; that divergence let a human pre-empt the verifier on
# machine-owned ground.
def human_may_adjudicate(
    criterion: PackageAcceptanceCriterion,
    evidence: Evidence | None,
    unit_state: str,
) -> bool:
    if floor_for(criterion.evidence_type) == "human":
        return True
    status, _, _ = evaluate_criterion(criterion, evidence)
    # Clause (b): the verifier has run, could not resolve, and handed off. Requiring the handoff
    # STATE (rather than the criterion's declared type) is what closes the window in which further
    # evidence could still arrive.
    return status == "judgment_required" and unit_state == "awaiting_review"
```

- [ ] **Step 4: Run and verify they pass**

```bash
.venv/bin/pytest tests/services/test_adjudications.py -k human_may -v
```
Expected: PASS. Record the collected count.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/verifier_evaluators.py tests/services/test_adjudications.py
git commit -m "feat(verifier): add the single human-adjudication predicate"
```

---

### Task 2: Route authorization through the predicate — closes the fail-open in main

**Files:**
- Modify: `src/orchestrator/services/evidence.py` (`_authorize_outcome`, and its call site in
  `record_adjudication`)
- Test: `tests/services/test_adjudications.py`

**Interfaces:**
- Consumes: `human_may_adjudicate` from Task 1.
- Produces: `_authorize_outcome` gains whatever it needs to answer the predicate. **Read
  `record_adjudication` and choose the smallest change that gives it the criterion, the current
  evidence and `unit.state`** — all three are reachable at the existing call site. Do not fetch them
  a second time from a new query.

This task is **not** behaviour-preserving and is not claimed to be: it closes a real hole. Expect
existing tests that adjudicate deterministic criteria as a human to fail. Disposition each one
explicitly and list them in the report.

- [ ] **Step 1: Write the end-to-end control**

Add a service-level test that a HUMAN recording `passed` on an `automated_test` criterion **whose
current evidence is readable** is refused with `role_forbidden`, and that the same human recording
`passed` on a `human_review` criterion succeeds. Build the fixtures the way
`tests/services/test_adjudications.py` already does — do not invent a new fixture style.

- [ ] **Step 2: Run and verify the refusal case FAILS**

Expected: it currently **succeeds** (that is the fail-open). Paste the output.

- [ ] **Step 3: Implement**

Replace the `JUDGMENT_TYPES` membership test in `_authorize_outcome`'s HUMAN branch with
`human_may_adjudicate(...)`. Delete the now-superseded `# A-static:` comment and replace it with one
naming clause (b) and why state, not type, closes the window.

- [ ] **Step 4: Run the full service and web suites**

```bash
.venv/bin/pytest tests/services tests/web -q
```
Expected: PASS after dispositioning. **List every pre-existing test you changed and why.**

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/evidence.py tests/services/test_adjudications.py
git commit -m "fix(adjudication): authorize on the floor predicate, closing the human pre-empt hole"
```

---

### Task 3: A human may record `failed`

**Files:**
- Modify: `src/orchestrator/services/evidence.py` (`HUMAN_ADJUDICABLE_OUTCOMES`)
- Test: `tests/services/test_adjudications.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_human_may_record_failed_on_a_judgment_criterion(migrated_session) -> None:
    # Spec AC-006. Before this, the human vocabulary was passed / not_applicable / waived --
    # the gate could say yes, doesn't apply, or nothing. It could not say no.
    ...  # build the unit + human_review criterion using this module's existing fixtures
    result = record_adjudication(..., outcome="failed", actor=human_actor(), ...)
    assert isinstance(result, Adjudication)
    assert result.outcome == "failed"


def test_a_human_may_not_record_failed_on_a_machine_owned_criterion(migrated_session) -> None:
    # Spec AC-007. `failed` inherits the same predicate as passed -- it is not a wider door.
    ...
    result = record_adjudication(..., outcome="failed", actor=human_actor(), ...)
    assert isinstance(result, DomainError) and result.code == "role_forbidden"
```

- [ ] **Step 2: Run and verify they FAIL**

Expected: the first fails with `role_forbidden` (today `failed` is VERIFIER-only). Paste the output.

- [ ] **Step 3: Implement**

Add `"failed"` to `HUMAN_ADJUDICABLE_OUTCOMES`. No other change — `failed` flows through the same
predicate as `passed`, so it is not a wider door. Confirm `_validate_adjudication_fields` does not
require waiver fields for a non-waiver outcome before assuming this is sufficient.

- [ ] **Step 4: Run and verify**

```bash
.venv/bin/pytest tests/services/test_adjudications.py -q
```

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/evidence.py tests/services/test_adjudications.py
git commit -m "feat(adjudication): a human may record failed on criteria they may decide"
```

---

### Task 4: The completion guard honours a human `failed`

**Files:**
- Test: `tests/services/test_lifecycle_guards.py` (or wherever `completion_satisfied` is exercised —
  **find it, do not assume**)

No source change expected. This task proves spec AC-008 and AC-009 hold for the newly reachable
outcome. If a source change turns out to be needed, stop and report — that would mean the completion
guard treats `failed` differently depending on who recorded it, which would be a finding.

- [ ] **Step 1: Write the tests**

AC-008: a unit whose required criterion's current terminal is a human-recorded `failed` cannot reach
`COMPLETED`. AC-009: a later `passed` supersedes it and the unit can then complete.

- [ ] **Step 2: Run — they should pass without a source change**

If they fail, **stop and report** rather than modifying the guard.

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "test(lifecycle): pin completion against a human-recorded failure"
```

---

### Task 5: The form offers exactly what the service accepts

**Files:**
- Modify: `src/orchestrator/web.py:203`, `src/orchestrator/templates/unit.html:~48-52`
- Test: `tests/web/test_adjudication_route.py`

`web.py:203` computes `"is_judgment": criterion.evidence_type.strip().lower() in JUDGMENT_TYPES` —
the **third** consumer of `JUDGMENT_TYPES`, and the last one still diverging.

- [ ] **Step 1: Write the agreement pin**

```python
def test_the_form_offers_exactly_the_outcomes_the_service_will_accept(...) -> None:
    # The divergence guard. Increment 1 moved evaluation onto the floor and left authorization and
    # this flag on JUDGMENT_TYPES; that is how a human could be offered an outcome the service
    # would refuse -- or refused one it would accept. Pin them together.
    ...
```

Render the unit page for a criterion of each floor kind and assert the offered `<option>` values
equal the set the service authorizes for that same criterion, evidence and unit state.

- [ ] **Step 2: Run and verify it FAILS**

Expected: fail — the form does not offer `failed` at all, and its `is_judgment` flag disagrees with
the predicate for `automated_test`. Paste the output.

- [ ] **Step 3: Implement**

Replace `is_judgment` with a value derived from `human_may_adjudicate`, and add the `failed` option
to the template inside the same conditional that guards `passed` / `not_applicable`. Keep `waived`
where it is — it is authorized separately (HUMAN, any criterion) and this increment does not change
that.

- [ ] **Step 4: Run the web suite, then the full gate**

```bash
.venv/bin/pytest tests/web -q
git status                # clean
make check
```
Expected: PASS. **Record the collected count** and compare against the 1769 baseline.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/web.py src/orchestrator/templates/unit.html tests/web/test_adjudication_route.py
git commit -m "feat(review): offer failed, and pin the form to the service's authorization"
```

---

### Task 6: Retire the obsolete authoring rule

**Files:**
- Modify: `docs/operations/production-drill-adaptations.md`

- [ ] **Step 1: Remove the `evidence_type: "test"` authoring rule**

It instructs authors to declare a value `intent_packages/validate.py` rejects (`test` is not among
the five types a package may legally declare), and Increment 1 removed the need for the workaround:
`automated_test` now resolves deterministically when readable evidence arrives. Replace it with that
statement. Increment 1 flagged this as an unactioned follow-up because it fell outside its four-file
constraint; it is in scope here.

- [ ] **Step 2: Grep for other copies of the same rule**

```bash
grep -rn 'evidence_type' docs/ CLAUDE.md | grep -i 'test"'
```
Fix every hit or report why one should stay.

- [ ] **Step 3: Commit**

```bash
git add docs/ CLAUDE.md
git commit -m "docs: retire the unfollowable evidence_type authoring rule"
```

---

## Self-review notes

- **Spec coverage:** AC-006 → Task 3; AC-007 → Tasks 2 and 3; AC-008/AC-009 → Task 4. The
  authorization/evaluation divergence is not in the spec — it was created by Increment 1 and is
  closed by Tasks 1–2 and 5.
- **Deliberately deferred:** §5.3 (single multi-criterion submission), §5.4–5.6 (evidence rendering,
  queue, decision surface). This increment touches `unit.html` only to add one option and fix one
  flag — it does not restructure the page.
- **Known risk handed over, not assumed away:** Task 2 is not behaviour-preserving and will break
  existing tests. That is expected; a silent mass update is the failure mode. Every changed test
  must be listed with its disposition.
