# WS-P2.17 Increment 1 — The Judgment Floor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** A criterion declares a minimum scrutiny *floor*; evidence may satisfy at or above that
floor and never below it — making `automated_test` acceptance criteria deterministically evaluable
for the first time, without the documented factory-halt failure mode.

**Architecture:** Introduce an explicit floor mapping over the orchestrator's existing criterion
evidence vocabulary. `evaluate_criterion` consults the floor first and short-circuits to
`judgment_required` for human-floored criteria. For deterministic-permitted criteria, the evaluator
is looked up by the **arriving evidence row's** type rather than the criterion's declared type —
because the criterion says *what "done" means*, and the evidence says *what kind of thing it is*.
`DETERMINISTIC_TYPES`, `EVALUATORS` and `SPECIAL_CASE_TYPES` are **not modified**, so Assertion D
continues to hold unchanged.

**Tech Stack:** Python 3.12, SQLAlchemy, pytest. Repo: `~/Projects/orchestrator`.

**Spec:** `~/docs/software-delivery-system/2026-07-31-wsp217-human-gate-spec.md` §5.1, ruling R1.

---

## Global Constraints

- **R1 is guaranteed by a control test that must FAIL before the implementation exists.** A floor
  test that passes before the floor exists is a defect in the test, not a success. Task 1 requires
  you to paste the observed failure output into the closeout.
- **Fail closed on the floor, fail toward asking on the evidence.** An unrecognised or absent
  criterion evidence type resolves to the HUMAN floor. A deterministic-permitted criterion with no
  usable evidence resolves to `judgment_required` — **never** to `failed_closed`.
- **Do not add anything to `DETERMINISTIC_TYPES`.** CLAUDE.md documents, from four adversarial
  reviews, that adding `automated_test` there halts the factory. This plan achieves the same
  outcome by a different mechanism and must not be "simplified" into that one.
- **`make check` exit 0 does not prove tests ran.** Quote the collected count (`collected N items`)
  in every completion claim. Exit code 5 is swallowed by the Makefile.
- **`make check` needs Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR` pointed at
  `tests/fixtures/security-standards`, and a migrated database.** A bare clone fails ~18 tests
  *unmodified*. Run a clean-clone control before attributing any red to this work.
- **Never run two pytest suites against the test database concurrently** — the fixtures drop and
  recreate `orchestrator_test`.
- Python tools resolve from repo-local `.venv/bin` before global PATH.
- Run `ruff format` (or `make fix`) before committing, not just `ruff check` — whole-repo format
  debt is invisible to the diff-scoped Stop hook.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/orchestrator/services/verifier_evaluators.py` | Floor mapping, `floor_for()`, and the reworked `evaluate_criterion` | Modify |
| `tests/services/test_criterion_evidence_vocabulary.py` | Vocabulary + floor assertions, incl. the R1 control | Modify |
| `tests/architecture/test_cross_boundary_vocabulary.py` | Register the floor mapping | Modify |
| `CLAUDE.md` | Two stale-invariant corrections | Modify |

No migration. No route change. No schema change. No `/review` change. No cross-repo change.

---

### Task 1: The floor mapping and its control test

**Files:**
- Modify: `src/orchestrator/services/verifier_evaluators.py`
- Test: `tests/services/test_criterion_evidence_vocabulary.py`

**Interfaces:**
- Produces: `CriterionFloor` (a `Literal["human", "deterministic_permitted"]`),
  `HUMAN_FLOOR_TYPES: frozenset[str]`, `DETERMINISTIC_PERMITTED_TYPES: frozenset[str]`, and
  `floor_for(evidence_type: str) -> CriterionFloor`. Tasks 2–4 consume all four.

- [ ] **Step 1: Write the failing control test**

Add to `tests/services/test_criterion_evidence_vocabulary.py`:

```python
from orchestrator.services.verifier_evaluators import (
    DETERMINISTIC_PERMITTED_TYPES,
    HUMAN_FLOOR_TYPES,
    floor_for,
)


def test_an_unknown_criterion_type_floors_to_human() -> None:
    # R1 fail-closed: forgetting to classify a type must produce a gate that fires too often
    # (recoverable), never one that does not fire (not recoverable).
    assert floor_for("automated_tset") == "human"
    assert floor_for("") == "human"


def test_human_floor_types_and_deterministic_permitted_types_are_disjoint() -> None:
    assert HUMAN_FLOOR_TYPES & DETERMINISTIC_PERMITTED_TYPES == frozenset()


def test_every_supported_criterion_type_has_exactly_one_floor() -> None:
    from orchestrator.services.verifier_evaluators import SUPPORTED_CRITERION_EVIDENCE_TYPES

    covered = HUMAN_FLOOR_TYPES | DETERMINISTIC_PERMITTED_TYPES
    assert SUPPORTED_CRITERION_EVIDENCE_TYPES <= covered, (
        f"unclassified: {sorted(SUPPORTED_CRITERION_EVIDENCE_TYPES - covered)}"
    )


def test_floor_is_case_and_whitespace_insensitive() -> None:
    assert floor_for("  Human_Review  ") == "human"
```

- [ ] **Step 2: Run the tests and verify they FAIL**

```bash
.venv/bin/pytest tests/services/test_criterion_evidence_vocabulary.py -v
```

Expected: `ImportError` / `cannot import name 'floor_for'`.
**Paste this output into the closeout.** If these tests pass at this step, stop — the test is wrong.

- [ ] **Step 3: Implement the floor**

In `src/orchestrator/services/verifier_evaluators.py`, after the `SUPPORTED_CRITERION_EVIDENCE_TYPES`
definition, add:

```python
CriterionFloor = Literal["human", "deterministic_permitted"]

# The MINIMUM scrutiny a criterion's declared evidence_type demands (WS-P2.17, ruling R1).
# Evidence may satisfy AT or ABOVE this floor and never below it. This is a separate concept from
# DETERMINISTIC_TYPES, which describes what the verifier knows how to READ; the floor describes what
# the package author is entitled to INSIST ON. Keeping them separate is what lets `automated_test`
# become machine-evaluable without being added to DETERMINISTIC_TYPES -- an addition CLAUDE.md
# records as halting the factory.
HUMAN_FLOOR_TYPES: frozenset[str] = JUDGMENT_TYPES - {"automated_test"}
DETERMINISTIC_PERMITTED_TYPES: frozenset[str] = DETERMINISTIC_TYPES | {"automated_test"}


def floor_for(evidence_type: str) -> CriterionFloor:
    """The minimum scrutiny a criterion type demands. Unknown types floor to human (fail closed)."""
    normalized = evidence_type.strip().lower()
    if normalized in DETERMINISTIC_PERMITTED_TYPES:
        return "deterministic_permitted"
    return "human"
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
.venv/bin/pytest tests/services/test_criterion_evidence_vocabulary.py -v
```
Expected: PASS. Record the collected count.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/verifier_evaluators.py tests/services/test_criterion_evidence_vocabulary.py
git commit -m "feat(verifier): add the criterion judgment floor, fail-closed on unknown types"
```

---

### Task 2: Wire the floor in as a short-circuit — provably behaviour-preserving

**Files:**
- Modify: `src/orchestrator/services/verifier_evaluators.py:88-112` (`evaluate_criterion`)
- Test: `tests/services/test_criterion_evidence_vocabulary.py`

**Interfaces:**
- Consumes: `floor_for` from Task 1.
- Produces: no signature change. `evaluate_criterion` keeps its
  `(criterion, evidence) -> tuple[EvaluationStatus, str | None, str]` shape.

At this task's end **behaviour is unchanged** — every human-floored type already returned
`judgment_required` via `JUDGMENT_TYPES` membership. That is the point: the mechanism lands
separately from the semantic change, so a reviewer can reject Task 3 or 4 while keeping this.

- [ ] **Step 1: Write the failing test**

```python
def test_human_floored_criterion_is_judgment_even_with_deterministic_evidence() -> None:
    # THE R1 FAIL-OPEN CONTROL. A criterion the author floored to human must not be auto-satisfied
    # by evidence that merely happens to carry a deterministic evaluator's type.
    criterion = PackageAcceptanceCriterion(ac_id="AC-001", evidence_type="human_review")
    evidence = Evidence(evidence_type="test", payload={"status": "pass"})

    status, outcome, detail = evaluate_criterion(criterion, evidence)

    assert (status, outcome) == ("judgment_required", None)
```

Add the import: `from orchestrator.persistence.models import Evidence, PackageAcceptanceCriterion`.

- [ ] **Step 2: Run it and confirm it PASSES already**

```bash
.venv/bin/pytest tests/services/test_criterion_evidence_vocabulary.py::test_human_floored_criterion_is_judgment_even_with_deterministic_evidence -v
```

Expected: **PASS** — today `human_review` is in `JUDGMENT_TYPES` and short-circuits at line 101.
This is the *pin*, not the R1 control (Task 4 supplies that). Note in the closeout that it passed
pre-change, and why that is correct here.

- [ ] **Step 3: Replace the type dispatch with the floor dispatch**

In `evaluate_criterion`, replace:

```python
    if evidence_type in JUDGMENT_TYPES or evidence_type not in DETERMINISTIC_TYPES:
        return ("judgment_required", None, f"{criterion.evidence_type} requires review")
```

with:

```python
    if floor_for(evidence_type) == "human":
        return ("judgment_required", None, f"{criterion.evidence_type} requires review")
```

Leave the `automated_check` branch above it and everything below it untouched.

- [ ] **Step 4: Run the full service suite and confirm no regression**

```bash
.venv/bin/pytest tests/services -q
```
Expected: PASS, same collected count as before the change. Record both counts.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/services/verifier_evaluators.py tests/services/test_criterion_evidence_vocabulary.py
git commit -m "refactor(verifier): dispatch on the criterion floor, behaviour unchanged"
```

---

### Task 3: Look the evaluator up by the ARRIVING evidence type

**Files:**
- Modify: `src/orchestrator/services/verifier_evaluators.py` (`evaluate_criterion`)
- Test: `tests/services/test_criterion_evidence_vocabulary.py`

**Interfaces:**
- Consumes: Task 2's floor dispatch. Produces: no signature change.

**⚠ Verify before you write anything.** The generated post-deploy verification units are minted with
five SYSTEM-written evidence rows. This task changes which field the evaluator is keyed on, so those
units are the blast radius. **Read `src/orchestrator/services/deployment_observations.py` and find
the `evidence_type` value written on each of those five rows, and the `evidence_type` of the five
generated criteria.** If they are equal, this change is transparent for post-deploy units. If they
differ, STOP and report — do not proceed on an assumption.

- [ ] **Step 1: Write the regression pin for post-deploy units**

Write a test asserting that a generated post-deploy criterion, paired with the evidence row
`deployment_observations.py` actually writes for it, still evaluates to `passed`. Use the real
values you read in the pre-check — do not invent them.

- [ ] **Step 2: Run it and confirm it passes on the CURRENT code**

```bash
.venv/bin/pytest tests/services/test_criterion_evidence_vocabulary.py -k post_deploy -v
```
Expected: PASS. This pin's job is to fail *after* the change if the change breaks post-deploy units.

- [ ] **Step 3: Write the failing test for the new behaviour**

```python
def test_evaluator_is_selected_by_the_arriving_evidence_type() -> None:
    # The criterion says what "done" means; the evidence says what kind of thing it is.
    criterion = PackageAcceptanceCriterion(ac_id="AC-001", evidence_type="gate.summary")
    evidence = Evidence(evidence_type="security.scan", payload={"block": 0, "warn": 0})

    status, outcome, _ = evaluate_criterion(criterion, evidence)

    assert (status, outcome) == ("passed", "passed")


def test_deterministic_criterion_with_unreadable_evidence_asks_rather_than_fails() -> None:
    # Fail TOWARD ASKING. `failed_closed` here is what produces the documented
    # REVISION_REQUIRED -> retry -> FAILED loop.
    criterion = PackageAcceptanceCriterion(ac_id="AC-001", evidence_type="test")
    evidence = Evidence(evidence_type="runner.pr.opened", payload={"pr_url": "https://example"})

    status, outcome, _ = evaluate_criterion(criterion, evidence)

    assert (status, outcome) == ("judgment_required", None)


def test_deterministic_criterion_with_no_evidence_asks_rather_than_fails() -> None:
    criterion = PackageAcceptanceCriterion(ac_id="AC-001", evidence_type="test")

    status, outcome, _ = evaluate_criterion(criterion, None)

    assert (status, outcome) == ("judgment_required", None)
```

- [ ] **Step 4: Run and verify they FAIL**

```bash
.venv/bin/pytest tests/services/test_criterion_evidence_vocabulary.py -k "arriving_evidence or asks_rather" -v
```
Expected: FAIL — the last two currently return `failed_closed`. Paste the output into the closeout.

- [ ] **Step 5: Implement**

Replace the body of `evaluate_criterion` below the `automated_check` branch and the floor
short-circuit with:

```python
    if evidence is None:
        return ("judgment_required", None, "no evidence has been recorded for this criterion")
    if not isinstance(evidence.payload, dict):
        return ("judgment_required", None, "evidence payload is not machine-readable")
    arriving_type = evidence.evidence_type.strip().lower()
    evaluator = EVALUATORS.get(arriving_type)
    if evaluator is not None:
        return evaluator(evidence.payload)
    if arriving_type == "infra_lane.final":
        return _infra_lane_result(evidence)
    return ("judgment_required", None, f"{evidence.evidence_type} has no deterministic evaluator")
```

- [ ] **Step 6: Run the full service suite**

```bash
.venv/bin/pytest tests/services -q
```
Expected: PASS, **including the post-deploy pin from Step 1**. Existing tests asserting
`failed_closed` on missing evidence will fail — for each one, decide deliberately whether it encodes
the old fail-closed intent (update it, and say why in the commit) or a genuine regression (stop and
report). Record every test you changed and the reason.

- [ ] **Step 7: Commit**

```bash
git add -A src/orchestrator/services/verifier_evaluators.py tests/
git commit -m "feat(verifier): key evaluation on arriving evidence; ask rather than fail-closed"
```

---

### Task 4: Flip `automated_test` and replace the superseded control

**Files:**
- Test: `tests/services/test_criterion_evidence_vocabulary.py:~40` (the existing
  `test_automated_test_still_requires_judgment_unchanged`)

No source change — Task 1 already placed `automated_test` in `DETERMINISTIC_PERMITTED_TYPES`, and
Tasks 2–3 made that meaningful. This task makes the semantic change *visible and reviewed* rather
than a side effect.

- [ ] **Step 1: Confirm the existing control now fails**

```bash
.venv/bin/pytest tests/services/test_criterion_evidence_vocabulary.py::test_automated_test_still_requires_judgment_unchanged -v
```

Expected: still PASS (it passes `None` evidence, which now returns `judgment_required` for a
different reason). **Read the test body before assuming.** If it passes, its *stated reason* is now
wrong even though its assertion holds — which is exactly the kind of test that decays into a false
invariant.

- [ ] **Step 2: Replace it — do not delete it**

```python
def test_automated_test_is_deterministically_evaluable_when_real_evidence_arrives() -> None:
    # SUPERSEDES test_automated_test_still_requires_judgment_unchanged (WS-P2.16 U4). That control
    # pinned the behaviour-preserving half: `automated_test` was judgment_required by membership.
    # WS-P2.17 gives it a deterministic-permitted FLOOR, so real test evidence now resolves it --
    # while evidence the verifier cannot read still asks a human.
    criterion = PackageAcceptanceCriterion(ac_id="AC-001", evidence_type="automated_test")

    readable = Evidence(evidence_type="pytest", payload={"status": "pass"})
    assert evaluate_criterion(criterion, readable)[:2] == ("passed", "passed")

    unreadable = Evidence(evidence_type="runner.pr.opened", payload={"pr_url": "https://example"})
    assert evaluate_criterion(criterion, unreadable)[:2] == ("judgment_required", None)

    assert evaluate_criterion(criterion, None)[:2] == ("judgment_required", None)


def test_automated_test_is_not_in_deterministic_types() -> None:
    # The mechanism guard. CLAUDE.md records that adding `automated_test` to DETERMINISTIC_TYPES
    # halts the factory (four adversarial reviews). WS-P2.17 deliberately achieves the outcome via
    # the floor instead. If a later change moves it, this reds.
    assert "automated_test" not in DETERMINISTIC_TYPES
    assert "automated_test" in DETERMINISTIC_PERMITTED_TYPES
```

- [ ] **Step 3: Run and verify**

```bash
.venv/bin/pytest tests/services/test_criterion_evidence_vocabulary.py -v
```
Expected: PASS. Record the collected count.

- [ ] **Step 4: Commit**

```bash
git add tests/services/test_criterion_evidence_vocabulary.py
git commit -m "test(verifier): supersede the automated_test judgment control with the floor control"
```

---

### Task 5: Register the vocabulary and correct CLAUDE.md

**Files:**
- Modify: `tests/architecture/test_cross_boundary_vocabulary.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run the whole-repo architecture scans**

```bash
.venv/bin/pytest tests/architecture -q
```

Expected: `test_cross_boundary_vocabulary.py` fails on the two new module-level frozensets. **Read
that test's docstring and `VOCABULARY_REGISTRY` before editing** — a genuine cross-boundary
vocabulary is REGISTERED, not marked `# not-a-vocabulary`. These are genuine: they mirror
`intent_packages/validate.py:46`.

- [ ] **Step 2: Register both, keyed `"services/verifier_evaluators.py:<symbol>"`**, naming
  `intent_packages/validate.py:46` (`EVIDENCE_TYPES`) as the cross-boundary source of truth.

- [ ] **Step 3: Correct the two stale CLAUDE.md bullets**

1. The invariant asserting *"nothing validates capability names at ingress at all"* and
   *"grep `github.pr.create` in src/ → zero hits"* — **stale**. `validate_unit_capabilities`
   (`capability_vocabulary.py`) is imported by `services/packages.py` and
   `services/decomposition.py`; WS-P2.16 closed it. Correct the bullet; do not delete the
   surrounding vocabulary-mismatch discussion, which remains true.
2. The authoring rule instructing package authors to *"declare `evidence_type: "test"` and never
   `automated_test`"* — **unfollowable**: `test` is not among the five types
   `intent_packages/validate.py:46` permits, so `factory validate` rejects it before intake.
   Replace it with: `automated_test` is now the correct declaration for an automated criterion, and
   it resolves deterministically when readable evidence arrives.

- [ ] **Step 4: Full gate**

```bash
git status                # must be clean of unintended changes
make check
```
Expected: PASS. **Record the collected count.** If red, run a clean-clone control before attributing
it to this work.

- [ ] **Step 5: Commit**

```bash
git add tests/architecture/test_cross_boundary_vocabulary.py CLAUDE.md
git commit -m "chore: register the criterion floor vocabulary; correct two stale invariants"
```

---

## Self-review notes

- **Spec coverage:** §5.1 AC-001 → Task 2 pin + Task 4; AC-002 → Task 3/4; AC-003 → Task 3;
  AC-004 → Task 1; AC-005 → Task 5. All five covered.
- **Deliberately deferred to later increments:** §5.2 (`failed` outcome), §5.3 (single submission),
  §5.4–5.6 (surface). No task here touches `web.py`, templates, or routes.
- **Known risk, explicitly handed to the implementer rather than assumed away:** Task 3 Step 6 will
  break existing tests that assert `failed_closed` on missing evidence. That is expected. Each one
  must be dispositioned deliberately and reported — a silent mass update is the failure mode.
