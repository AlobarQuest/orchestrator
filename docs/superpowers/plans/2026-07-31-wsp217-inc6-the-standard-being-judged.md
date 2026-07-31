# WS-P2.17 Increment 6 — The standard being judged — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reviewer deciding an acceptance criterion can see **what the criterion requires** and
**what evidence was expected**, next to the evidence that actually arrived.

**Architecture:** One task. The data is already in the context — `pending_decisions.adjudicable_criteria`
returns the full `PackageAcceptanceCriterion` object — so this is a projection key and a template
change. No service logic, no new route, no module, no migration.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, pytest. Repo: `~/Projects/orchestrator`.

**Spec:** `~/docs/software-delivery-system/2026-07-31-wsp217-human-gate-spec.md` §5.4 — the finding
this closes was raised by the Increment 5 build session as the most likely reason a human test of
`/review` comes back negative.
**Predecessor:** Increment 5, merged `76b46c8`.

---

## Why this exists

The workstream began because Devon adjudicated a criterion with the rationale
**"It had words that looked right :/"** — he was shown prose and asked whether it was acceptable,
without being shown the standard it had to meet.

Five increments later the adjudication fieldset renders `AC-001 (human_review)`, the current
evidence, and a dropdown. **The criterion's own `condition` — the sentence stating what must be true
— is nowhere on the unit page.** It appears only on the queue, so a reviewer reads the standard,
clicks through, and loses it on the page where they decide. The workstream's founding complaint,
reproduced one level up.

`load_required_criteria` yields whole `PackageAcceptanceCriterion` rows and
`adjudicable_criteria` passes them through, so `condition`, the declared `evidence` expectation and
`approver` are all already reachable. Only the per-criterion dict `web.py` builds, and the fieldset
markup, drop them.

**Render the declared `evidence` expectation too, not just `condition`.** The judgment a human is
making is *does what arrived satisfy what was required* — which needs the standard, the expectation,
and the actual, in one place. Two of the three are already there.

---

## Global Constraints

- **No new route, no new service module, no migration.** If you need one, **stop and report**.
- **Every task lands its mechanism AND a production caller in the same commit.**
- **Increment 2's form/service agreement pin must still pass.** You are adding markup inside the
  `<fieldset>`; Increment 5 kept the pin passing by staying outside every control its regexes read.
  Do the same. If it reds, fix your work — unless you can prove a strengthening the way Increment 3
  did.
- **Scope your assertions to a single `<fieldset>`, and state the negative.** Increment 4 shipped
  assertions that passed with nothing built because the page already printed the value elsewhere;
  Increment 5 fixed that by asserting `"second-criterion" not in fieldsets["ac-1"]` and **proving it
  by control** — temporarily rendering everything into every fieldset and watching the tests red.
  Meet that bar.
- **Do not weaken the markdown redaction.** `/review` HTML is human-only and full fidelity;
  `render_evidence_pack_markdown` goes onto a possibly-public PR and stays redacted. Note
  `approver` is an identity — if you render it in the HTML, confirm by test that the markdown
  renderer is unchanged.
- **Word guards.** No bare `dispatch`, `deploy`, `merges` in `web.py` bodies, strings or docstrings;
  hyphenated compounds tokenize (`post-deploy` matches `deploy`).
- **Read collected counts, never colours.** Baseline after Increment 5: **1834 collected → 1833
  passed, 1 skipped.**
- **`make check` needs Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR`, and a migrated DB** —
  a bare clone fails ~18 tests unmodified; clean-clone control before attributing red.
- Never run two pytest suites against the test DB concurrently. `ruff format` before committing,
  never on `.json`. **Do not deploy.** Merge per R12.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/orchestrator/web.py` | Carry `condition` and the declared evidence expectation into the per-criterion dict | Modify |
| `src/orchestrator/templates/unit.html` | Render them inside the fieldset | Modify |
| `tests/web/test_adjudication_route.py` | Fieldset-scoped assertions | Modify |

**Read `web.py` before editing.** HQ's Increment 5 plan carried a defect because it cited
`_adjudicatable_criteria` as it existed *before* Increment 4 moved that logic into
`services/pending_decisions.py`. Verify where the per-criterion dict is built today rather than
trusting this table.

---

### Task 1: The standard, the expectation, and the evidence in one place

- [ ] **Step 1: Write the failing tests**

For a unit with two criteria whose `condition` texts differ:

```python
def test_each_fieldset_shows_its_own_criterion_condition(...) -> None:
    # The founding defect of this workstream, one level up: a reviewer is asked to accept evidence
    # without being shown the standard it must meet. The condition appears only on the queue today.
    fieldsets = _fieldsets_by_ac_id(render(...))
    assert "<ac-1 condition text>" in fieldsets["AC-001"]
    assert "<ac-2 condition text>" not in fieldsets["AC-001"]


def test_each_fieldset_shows_the_evidence_the_package_expected(...) -> None:
    # The judgment is "does what arrived satisfy what was required" -- which needs the expectation,
    # not only the standard and the actual.
    ...
```

- [ ] **Step 2: Run and verify they FAIL.** Paste the verbatim output.

- [ ] **Step 3: Prove the assertions discriminate.** Before implementing, temporarily render every
      criterion's condition into every fieldset and confirm **both** negative assertions red. Restore.
      Record that you did this — Increment 5 set this bar and it is the reason its tests are worth
      anything.

- [ ] **Step 4: Implement.** Carry `condition` and the declared evidence expectation into the
      per-criterion dict and render them inside the fieldset, above the outcome control. The
      criterion text is the first thing a reviewer should read in that block.

- [ ] **Step 5:** `.venv/bin/pytest tests/web -q`, **including Increment 2's agreement pin.** Record
      the count.

- [ ] **Step 6: Commit** — `feat(review): show the standard being judged beside the decision`

---

### Task 2: The full gate

- [ ] **Step 1:** `.venv/bin/pytest tests/architecture tests/idempotency -q`. Nothing should fire.
      If an inventory or the matrix objects, a route changed — **stop and report.**
- [ ] **Step 2:** `git status` clean, then `make check`. Record the collected count against 1834.
- [ ] **Step 3: Commit** any incidental fix with its reason.

---

## Self-review notes

- **Closes:** finding #2 of the Increment 5 build report — the criterion's own text is absent from
  the page where it is judged.
- **Still deliberately open:** AC-022 (the intake payload paste, which needs a staged-intake table
  and two routes); the `waived`-authority narrowing; a full deciding/auditing split.
- **This is the last increment of WS-P2.17.** After it, HQ deploys Increments 3–6 and Devon judges
  the gate by using it.
