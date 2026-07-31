# WS-P2.17 Increment 4 — The decision surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** A human at any gate can see what the work does, what it affects, whether it can be backed
out, and the actual evidence — and can find the gates that need them without knowing the lifecycle.

**Architecture:** Spec §5.4, §5.5 and §5.6 are three findings about **one page and one component**,
so they ship as one increment rather than three sequential rewrites of the same template. A single
`decision facts` projection answers the three questions Devon named; one shared template partial
renders it; the unit page and the intake page both use it; and the queue is rebuilt around *pending
human decisions* instead of lifecycle state. Evidence rendering needs **no service change** — the
data is already in the context.

**Tech Stack:** Python 3.12, SQLAlchemy, FastAPI, Jinja2, pytest. Repo: `~/Projects/orchestrator`.

**Spec:** `~/docs/software-delivery-system/2026-07-31-wsp217-human-gate-spec.md` §5.4–§5.6
(AC-014…AC-023).
**Predecessors:** Inc 1 `f28b9c2`, Inc 2 PR #97, Inc 3 `7c9bb1a`. Inc 1+2 are deployed; Inc 3 is not.

---

## Global Constraints

- **NO NEW ROUTES.** Everything here renders through existing `/review` GET routes and existing POST
  forms. This deliberately avoids the exact set-equality route-inventory guards and the idempotency
  matrix entirely. **If you find yourself adding a route, stop and report** — it means the design
  drifted.
- **Increment 2's form/service agreement pin must still pass.** Increment 3 already had to adjust
  its extractor once (correctly, and it proved the change was a strengthening). If your work reds it,
  **fix your work, not the pin** — unless you can prove, as Increment 3 did, that the change makes it
  red under strictly more conditions than before.
- **Do not weaken the markdown redaction.** `render_evidence_pack_markdown` is posted by
  factory-runner onto a **possibly-public PR** and deliberately omits approver identities and waiver
  rationale. The `/review` HTML is human-only behind forward-auth and stays full fidelity. These are
  different renderers with different rules — Task 2 pins that.
- **Word guards.** `web.py` is in **no** allowlist: its route bodies and any string it emits may not
  contain the bare tokens `dispatch`, `deploy` (`test_ws32_scope_guards.py`) or `merges`
  (`test_ws33_scope_guards.py`, no allowlist) — **including in docstrings**. A "what needs you"
  queue naturally reaches for those words. Reword; do not add an allowlist entry.
- **A task boundary is only valid if the tree is green AND the behaviour is coherent at it.**
- **A persistence assertion must re-read through a DIFFERENT session.** `expire_all()` on the
  writing session does not discriminate — a flushed-uncommitted row is visible inside its own
  transaction. (Increment 3 proved HQ's snippet would have passed under the WS-P2.1 defect it
  guarded. Task 6 corrects the CLAUDE.md invariant that still prescribes it.)
- **Read collected counts, never check colours.** Baseline after Increment 3: **1795 collected →
  1794 passed, 1 skipped**. `make check` exit 0 does not prove tests ran (exit 5 swallowed).
- **`make check` needs Postgres on `127.0.0.1:5432`, `SECURITY_STANDARDS_DIR`, and a migrated DB.**
  A bare clone fails ~18 tests unmodified — clean-clone control before attributing red.
- **Never run two pytest suites against the test DB concurrently.** Run `ruff format` before
  committing. **Never run `ruff format` on a `.json` file** — it injects a trailing comma and
  produces invalid JSON.
- Merge per ruling R12: you open the PR, read the CI collected count from the job log, verify your
  report obligations, and merge yourself. **Do not deploy.**

---

## What HQ verified, so you do not have to re-derive it

- `web.py::detail` builds its context from `services/evidence_pack.py::evidence_pack_projection`,
  which returns **full `Evidence` ORM rows** — so `row.payload` and `row.stable_ref` are **already
  in the template context**. Rendering evidence is a template change, not a service change.
- `templates/unit.html` is **75 lines**, with `<h2>` sections: Package and authority · Dependencies ·
  Claims and lease status · Evidence · Adjudications and waivers · Approvals · Event history · Human
  actions; and seven forms (approval, authority-approval, review, adjudication, cancel,
  reconciliation resolution, retry).
- `templates/evidence_pack.html` line 8 renders `{{ row.stable_ref or row.payload }}` under a column
  headed "Reference or payload" — the `or` is the defect.
- `web.py::queue` selects **every** `WorkUnit`, calls `evaluate_readiness` per unit, and groups by
  `unit.state`.
- `web.py::_adjudicatable_criteria` already computes `human_may_decide` per criterion via
  `human_may_adjudicate(criterion.evidence_type, current_evidence(...), unit.state)`, excluding
  `POST_DEPLOY_AC_IDS`.
- `kernel/transitions.py::DESIGNED_HUMAN_GATES` holds the three *transition* gates. **It is not
  sufficient on its own** — see Task 4.

**Everything else you must read for yourself.** In particular this plan does **not** tell you how to
query pending decomposition proposals or pending intakes; find them.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/orchestrator/services/decision_facts.py` | The three-facts projection | **Create** |
| `src/orchestrator/services/pending_decisions.py` | What needs a human, across all gate kinds | **Create** |
| `src/orchestrator/templates/_decision.html` | The shared decision-surface partial | **Create** |
| `src/orchestrator/templates/unit.html` | Use the partial; render evidence content | Modify |
| `src/orchestrator/templates/intake.html` | Use the partial | Modify |
| `src/orchestrator/templates/evidence_pack.html` | `stable_ref` **and** payload | Modify |
| `src/orchestrator/templates/queue.html` | Driven by pending decisions | Modify |
| `src/orchestrator/web.py` | Wire both projections into existing routes | Modify |
| `tests/services/test_decision_facts.py`, `tests/services/test_pending_decisions.py` | New | **Create** |
| `tests/web/test_queue.py`, `tests/web/test_evidence_pack.py`, `tests/web/test_adjudication_route.py` | Extend | Modify |
| `CLAUDE.md` | Four corrections | Modify |

Two new service modules rather than one: they answer different questions (*what is this work* vs
*what needs me*) and only one of them is per-unit.

---

### Task 1: The decision-facts projection

**Files:** Create `src/orchestrator/services/decision_facts.py`, `tests/services/test_decision_facts.py`

**Interfaces:**
- Produces: a projection returning the three facts Devon named, each with an explicit
  **unknown** state. Derive the signature from the two call sites you will add in Task 3 (the unit
  page has a `WorkUnit`; the intake page has only a `WorkPackageRevision`) — **it must serve both, and
  the intake case genuinely cannot answer "what it affects" for a package that has not been
  decomposed yet.**

The three facts:

| Fact | Source | Unknown when |
|---|---|---|
| **What it does** | the unit's outcome / the revision's `outcome.what` | never — always present |
| **What it affects** | the authority envelope: `constraints.target_repository`, `constraints.mutation_commands`, granted capabilities | at intake, before decomposition has chosen a target |
| **Can we back out** | derived from `change_class` | no `change_class`, or a class with no recorded reversibility |

**Reversibility is informational in this increment** — it blocks nothing. Define the mapping as a
module-level dict from `change_class` to a short statement, with **an explicit unknown for anything
absent**. Do not invent a per-package reversibility field; that belongs to WS-P2.18's policy
artifact.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_undecomposed_revision_reports_affects_as_unknown() -> None:
    # AC-021: an unknown is RENDERED as unknown, never omitted. A missing row reads as "nothing to
    # worry about"; an explicit unknown reads as "nobody knows yet", which is the truth.
    facts = decision_facts_for_revision(...)
    assert facts["affects"]["known"] is False
    assert facts["affects"]["detail"]


def test_a_unit_reports_its_target_repository_and_mutating_commands() -> None:
    facts = decision_facts_for_unit(...)
    assert facts["affects"]["known"] is True
    assert "AlobarQuest/change-manager" in facts["affects"]["detail"]


def test_an_unmapped_change_class_reports_reversibility_as_unknown() -> None:
    facts = decision_facts_for_unit(...)  # unit whose change_class is absent
    assert facts["reversibility"]["known"] is False
```

- [ ] **Step 2: Run and verify they FAIL.** Paste the verbatim output into the report.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `.venv/bin/pytest tests/services/test_decision_facts.py -q`.** Record the count.
- [ ] **Step 5: Commit** — `feat(review): project the three facts a human needs to decide`

---

### Task 2: Render evidence where the decision is made

**Files:** Modify `templates/unit.html`, `templates/evidence_pack.html`; extend
`tests/web/test_evidence_pack.py`

The finding, verbatim from the review: `unit.html` — the page **containing the adjudication form** —
renders only `ac_id`, `evidence_type` and current/superseded. **The payload is not rendered at all.**
`evidence_pack.html:8` renders `stable_ref or payload`, so a row carrying a reference never shows its
content. The only prose on the page is the criterion's own condition text — which is exactly what
*"It had words that looked right"* describes.

- [ ] **Step 1: Write the failing tests**

Render a unit page whose evidence row has both a `stable_ref` and a payload, and assert **both**
appear (AC-014, AC-015). Then a test that the **markdown** renderer still omits approver identity and
waiver rationale (AC-016) — build it from the existing markdown tests' fixtures, not from this plan.

- [ ] **Step 2: Run and verify the first two FAIL and the redaction one PASSES.** Say so explicitly
      in the report: the redaction test is a **guard against this task**, not a failing-first control.
- [ ] **Step 3: Implement.** `stable_ref` **and** payload, never `or`. Render payload content in the
      unit page's Evidence section. Payloads are bounded at ingest and the page is human-only behind
      forward-auth, so full fidelity is correct here — **do not add redaction to the HTML.**
- [ ] **Step 4: Run `.venv/bin/pytest tests/web -q`.** Record the count.
- [ ] **Step 5: Commit** — `feat(review): render evidence content where the decision is made`

---

### Task 3: The shared decision-surface partial

**Files:** Create `templates/_decision.html`; modify `templates/unit.html`, `templates/intake.html`,
`web.py`

**One component, used twice.** The five human gates are the same shape — *here is a thing, here is
what you need to decide it, here is the button* — and building them separately is what produced five
surfaces with five failure modes.

- [ ] **Step 1: Write the failing tests**

Assert the unit page and the intake page both render all three facts, and that an unknown fact
renders **as an explicit unknown rather than an omitted row** (AC-020, AC-021).

- [ ] **Step 2: Run and verify they FAIL.** Paste the output.
- [ ] **Step 3: Implement.** The partial renders the three facts and nothing else — it is not a
      layout. Place it **above** the existing sections on the unit page, so the decision is the first
      thing on the page and the audit detail follows.
      **Do not restructure the rest of `unit.html`.** Splitting deciding from auditing into two views
      is deliberately deferred; this increment puts the decision first on one page.
- [ ] **Step 4: Run `.venv/bin/pytest tests/web -q`**, including Increment 2's agreement pin.
- [ ] **Step 5: Commit** — `feat(review): one decision surface, used at intake and adjudication`

---

### Task 4: The queue becomes "what needs you"

**Files:** Create `src/orchestrator/services/pending_decisions.py`,
`tests/services/test_pending_decisions.py`; modify `templates/queue.html`, `web.py`,
`tests/web/test_queue.py`

Today `/review` groups units by lifecycle state and lists readiness reasons — finding your work
requires already knowing which states imply a gate.

`DESIGNED_HUMAN_GATES` covers the three *transition* gates and **is not sufficient**. The full set of
pending human decisions also includes: package intakes awaiting registration, decomposition proposals
awaiting a decision, units awaiting **authority** approval (which sit in `DRAFT`, not at a gate
edge), criteria awaiting adjudication, and open reconciliation conditions. **Find each of these
yourself** — this plan deliberately does not tell you where they live.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_kind_of_pending_decision_appears(...) -> None:
    # AC-017. Five kinds, each naming the decision required.
    ...

def test_an_item_with_nothing_to_decide_does_not_appear(...) -> None:
    # AC-018.
    ...

def test_an_item_disappears_once_its_decision_is_recorded(...) -> None:
    # AC-019.
    ...
```

- [ ] **Step 2: Run and verify they FAIL.** Paste the output.
- [ ] **Step 3: Implement.** Each entry names **the decision required**, not the state it is in.
      Mind the word guards — `web.py` may not emit the bare tokens `dispatch`, `deploy` or `merges`.
- [ ] **Step 4: Run `.venv/bin/pytest tests/services tests/web -q`.** Record the count.
- [ ] **Step 5: Commit** — `feat(review): the queue lists pending human decisions, not states`

---

### Task 5: Whole-repo guards and the full gate

- [ ] **Step 1:** `.venv/bin/pytest tests/architecture tests/idempotency -q`

  Expect `test_unreachable_guards.py` to matter most: two new service modules must have a
  **production** caller — a test calling them is explicitly not sufficient. If the route-inventory or
  idempotency-matrix tests fire, a route changed and the design drifted (see Global Constraints):
  **stop and report rather than editing the inventories.**

- [ ] **Step 2:** `git status` clean, then `make check`. Record the collected count against 1795.
- [ ] **Step 3: Commit** any guard updates with the reason in the message.

---

### Task 6: Four documentation corrections

**Files:** `CLAUDE.md` (and `docs/` where a copy exists)

All four are HQ errors surfaced by prior sessions. State each as a correction, not a deletion.

- [ ] **1 — The persistence-pin invariant is insufficient.** The bullet requiring a test that asserts
      persistence to `expire_all()` and re-read **does not discriminate**: a flushed-uncommitted row
      is visible inside its own transaction, so the check passes under the exact WS-P2.1
      flush-without-commit defect it guards. Correct it to require a re-read through a **different
      session**. Proven by Increment 3, which injected `session.commit()` into the core and watched
      the pin red.
- [ ] **2 — The `RepoDigest` verification recipe does not work.** `docker inspect <container>
      --format '{{index .RepoDigests 0}}'` fails — `RepoDigests` is an **image** property. Verifying a
      running container's digest must go container → `.Image` → `docker image inspect`. The standing
      "ask production what it runs / verify the RepoDigest" invariant has been shipping an unusable
      command.
- [ ] **3 — A short-SHA `workflow_dispatch` ref cannot be built.** `actions/checkout` resolves a
      non-40-character ref as a branch/tag pattern, matches nothing, and fails. Use the full SHA.
- [ ] **4 — Adjudications do not bump `work_units.version`.** The only writers are
      `lifecycle._perform_transition`, `claims._transition` and
      `evidence._system_fail_without_new_attempt`. Any note claiming sibling adjudication forms
      staleness-broke each other is wrong; the real defects were the missing atomicity (fixed in
      Increment 3) and a `<select>` defaulting to `passed`.

- [ ] **Step 2: `git status` clean, `make check` green.** Record the count.
- [ ] **Step 3: Commit** — `docs: correct four invariants proven wrong by WS-P2.17`

---

## Self-review notes

- **Spec coverage:** AC-014/015/016 → Task 2; AC-017/018/019 → Task 4; AC-020/021 → Tasks 1 and 3;
  AC-022/023 → Task 3 (the intake surface renders the decision; the payload paste is **not** removed
  in this increment — see below).
- **Deliberately deferred, and named so it is not mistaken for an oversight:**
  - **Removing the intake payload paste (AC-022).** The CLI stages a verified payload and the form
    takes it; removing the paste means the browser must obtain it another way, which is a data-flow
    change, not a rendering change. Task 3 makes the intake page render the decision; the paste
    remains. **Report this as a known gap against AC-022.**
  - Splitting deciding from auditing into two views (§4c finding 3). This increment puts the
    decision first on one page.
  - The `waived`-authority narrowing, which has its own recorded proposed rule.
- **Known risk handed over, not assumed away:** Task 4's five pending-decision kinds are not all
  queryable the same way, and HQ has verified only that `DESIGNED_HUMAN_GATES` is insufficient. If
  one kind turns out to be unreachable without a new route, **stop and report** — that is a design
  finding, not a task to force.
