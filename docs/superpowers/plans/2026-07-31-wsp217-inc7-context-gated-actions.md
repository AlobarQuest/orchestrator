# WS-P2.17 Increment 7 — Context-gated actions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** The unit page offers only actions the service would actually accept, and reports what the
work affects when the envelope says so.

**Architecture:** Increment 2 established that the adjudication dropdown must offer exactly what
`_authorize_outcome` accepts, and pinned it with a test. This increment **extends that same principle
to every action form on the page** — each form renders only when the service's own precondition for
it holds. It also fixes a projection that reports *not known* for work whose blast radius is stated
in its authority envelope. No new route, module, or migration.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, pytest. Repo: `~/Projects/orchestrator`.

**Predecessor:** Increment 6, merged `7331a72`; deployed as `7331a72-wsp217inc3456-amd64`.

---

## Why this exists — observed in production, 2026-07-31

Devon reviewed two real units on the deployed surface and confirmed the gate is now usable. Two
defects were visible on both pages:

**1. Every action form renders unconditionally.** A **cancelled** drill unit
(`ac4a2ebd-…`) offered five forms — Record approval, Approve this authority envelope, Review
outcome, Cancel work unit, Authorize retry — **every one of which the service would refuse.** A
**completed** unit (`ffe46e6a-…`) offered three of the same. Devon: *"Removing the irrelevant
buttons and options, to match the context at view time, will help a lot also."*

**2. "What it affects" says *not known* when the envelope says otherwise.** On the WS-P2.13 rotation
unit, the decision surface reported no target repository and no mutating command — while the
authority envelope, rendered three sections lower on the same page, reads:

```
credential: bws-machine-account:8ba33ccd-…
irreversible_actions: []
secret_value_handling: Devon alone handles the token value, at the Bitwarden console and his own terminal
```

The projection only looks for `target_repository` and `mutation_commands`, so it is **repo-shaped**:
for `non-software-operational` work — the class where blast radius matters most — it discards an
answer already on file and shows an unknown. This is the same defect HQ made with reversibility in
Increment 4: deriving a weak answer when a real one was present.

---

## Global Constraints

- **No new route, no new service module, no migration.** If you need one, **stop and report.**
- **Gate on the SERVICE's condition, never on cosmetic preference.** Increment 2's pin asserts the
  form offers exactly what the service accepts. Hiding a form the service *would* accept breaks that
  invariant as surely as offering one it would refuse. For each form, find the precondition the
  service actually enforces and render on that.
- **Every task lands its mechanism AND a production caller in the same commit.**
- **Increment 2's form/service agreement pin must still pass**, and Task 1 extends it. If your change
  reds it, fix your work — unless you can prove a strengthening the way Increment 3 did.
- **Meet the discrimination bar.** Assertions scoped to the section under test, stating the negative,
  and **proved by control** — deliberately break the gating, watch the assertions red, restore.
  Record that you did it.
- **Word guards.** No bare `dispatch`, `deploy`, `merges` in `web.py` bodies, strings or docstrings;
  hyphenated compounds tokenize (`post-deploy` matches `deploy`).
- **Read collected counts, never colours.** Baseline: **1837 collected → 1836 passed, 1 skipped.**
- **`make check` needs Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR`, and a migrated DB** —
  a bare clone fails ~18 tests unmodified; clean-clone control before attributing red.
- Never run two pytest suites against the test DB concurrently. `ruff format` before committing,
  never on `.json`. **Do not deploy.** Merge per R12.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/orchestrator/web.py` | Compute per-form availability from service preconditions | Modify |
| `src/orchestrator/templates/unit.html` | Render only available actions | Modify |
| `src/orchestrator/services/decision_facts.py` | "What it affects" reads the whole constraints block | Modify |
| `tests/web/test_human_actions.py`, `tests/web/test_adjudication_route.py` | Availability + the extended pin | Modify |
| `tests/services/test_decision_facts.py` | The affects projection | Modify |

**Read `web.py` and the service guards before editing.** This table is a starting point, not a fact —
HQ has twice cited a helper that a later increment had moved.

---

### Task 1: Offer only the actions the service would accept

**Files:** `src/orchestrator/web.py`, `src/orchestrator/templates/unit.html`,
`tests/web/test_human_actions.py`

**Derive each form's condition from the service, not from this plan.** The forms are: record
approval, approve authority envelope, review outcome, cancel, authorize retry, adjudicate. For each,
find what the service enforces — the legal-edge table in `kernel/transitions.py` (`HUMAN_EDGES` and
the `TransitionGuards`), `authorize_retry`'s own preconditions, and the existing
`authority_violation` check already used to gate the authority form. **Where a service precondition
is expensive or unknowable at render time, leave the form rendered** — a false hide is worse than a
false offer, because it removes the operator's only route.

If **no** action is available, say so plainly rather than rendering an empty "Human actions"
section — an empty heading reads as a bug.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_terminal_unit_offers_no_action_the_service_would_refuse() -> None:
    # Observed in production 2026-07-31: a CANCELLED unit offered five forms, all of which the
    # service refuses. A completed unit offered three.
    page = render_unit_page(cancelled_unit)
    assert "Record approval" not in page
    assert "Approve this authority envelope" not in page
    assert "Record review outcome" not in page


def test_a_unit_awaiting_review_offers_the_review_outcome_form() -> None:
    # The negative half: gating must not hide a form the service WOULD accept.
    ...


def test_authorize_retry_is_offered_only_when_the_service_would_accept_it() -> None:
    # authorize_retry requires FAILED and exhausted attempts; a FAILED unit with attempts
    # remaining is refused today with no indication which case the reviewer is in.
    ...


def test_a_unit_with_no_available_action_says_so() -> None:
    ...
```

- [ ] **Step 2: Run and verify they FAIL.** Paste the verbatim output.
- [ ] **Step 3: Prove discrimination by control** — make the gating unconditionally `True`, watch the
      negative assertions red; then unconditionally `False`, watch the positive ones red. Restore.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Extend Increment 2's agreement pin** from the adjudication options to the action
      forms: for a given unit state, the set of forms offered equals the set the service would
      accept. **This is the increment's durable artifact** — the gating will otherwise drift the way
      the three `JUDGMENT_TYPES` consumers did.
- [ ] **Step 6:** `.venv/bin/pytest tests/web -q`. Record the count.
- [ ] **Step 7: Commit** — `feat(review): offer only the actions the service would accept`

---

### Task 2: "What it affects" reads the whole envelope

**Files:** `src/orchestrator/services/decision_facts.py`, `tests/services/test_decision_facts.py`

The projection currently reports *not known* unless `constraints.target_repository` or
`constraints.mutation_commands` is present. For non-software operational work the envelope instead
carries keys such as `credential`, `irreversible_actions` and `secret_value_handling` — **stated
plainly, and discarded.**

**Do not hardcode a second key list to match today's operational packages.** Constraints are an open
map; a fixed allowlist would repeat this defect for the next profile. Prefer summarising what the
envelope actually declares, with the repo-shaped keys given their existing prominence when present,
and the explicit unknown reserved for a genuinely empty constraints block.

- [ ] **Step 1: Write the failing tests**

```python
def test_operational_constraints_are_reported_rather_than_reported_unknown() -> None:
    # Observed on the WS-P2.13 rotation unit: the envelope named the credential it rotates and
    # whether the action is irreversible; the surface said "Not known."
    facts = decision_facts_for_unit(...)  # envelope with credential + irreversible_actions
    assert facts["affects"]["known"] is True
    assert "bws-machine-account" in facts["affects"]["detail"]


def test_an_empty_constraints_block_is_still_an_explicit_unknown() -> None:
    ...


def test_a_repo_targeted_unit_still_reports_its_repository_and_mutating_commands() -> None:
    # No regression: the repo-shaped answer is what dependency-update work depends on.
    ...
```

- [ ] **Step 2: Run and verify the first FAILS and the third PASSES.** Say which is which in the
      report — the third is a regression guard, not a failing-first control.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `.venv/bin/pytest tests/services/test_decision_facts.py tests/web -q`.
- [ ] **Step 5: Commit** — `fix(review): report what the envelope declares, not only repository facts`

---

### Task 3: The full gate

- [ ] **Step 1:** `.venv/bin/pytest tests/architecture tests/idempotency -q`. Nothing should fire; if
      an inventory or the matrix objects, a route changed — **stop and report.**
- [ ] **Step 2:** `git status` clean, then `make check`. Record the count against 1837.
- [ ] **Step 3: Commit** any incidental fix with its reason.

---

## Self-review notes

- **Closes:** both defects Devon observed on the deployed surface, 2026-07-31.
- **Still deliberately open:** AC-022 (the intake payload paste — needs a staged-intake table and two
  routes); the `waived`-authority narrowing; the verifier-conclusion signal (a reviewer cannot tell
  whether they are being asked because the machine could not resolve the criterion or because it was
  never automatable); a full deciding/auditing split.
- **Known risk handed over:** Task 1 removes controls. A false hide is worse than a false offer —
  if a precondition is not cheaply knowable at render time, leave the form and say so in the report
  rather than guessing.
