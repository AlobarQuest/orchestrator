# Stop freshening Dependabot's branches — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the estate lane's branch-update act, withhold the inert lane's for `dependabot[bot]`, and leave the shared qualification predicate untouched — so this estate stops editing branches Dependabot owns.

**Architecture:** Two landing lanes share one predicate and one act shape. The estate lane's freshening population is Dependabot by construction (a non-Dependabot author always draws `landing_author_not_the_update_bot`, which the predicate never subtracts), so its act is deleted outright. The inert lane serves two declared authors, only one of which disowns an edited branch, so it narrows: a new author-derived fact on `_RemoteTerms` withholds `branch_update_qualifies` at one call site. The predicate itself — `qualifies_for_branch_update` and `freshness_derived_refusals` — is one definition with two readers (ADR-0024) and does not change.

**Tech Stack:** Python 3.14 (`.python-version`), FastAPI + pydantic response models, SQLAlchemy 2.x + Postgres, pytest, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-09-20-dependabot-branch-freshening-design.md`

## Global Constraints

- **Every file:line in this plan was measured on 2026-09-20 at `a3a5f58`** (branch `rebase-design`). Read the code; line numbers move. Every step names the anchoring text as well as the line so a shifted file is still navigable.
- **No migration.** Nothing here touches a table, a column or a model. Deploy is build → swap → verify, with no migrate step. Confirm `alembic current == heads` anyway, as the estate's deploy verification requires.
- **Do not touch `qualifies_for_branch_update` or `freshness_derived_refusals`** (`src/orchestrator/services/estate_landing_admission.py:437,486`). ADR-0024 makes them one definition with two readers; changing them moves the out-of-process reporting agent's semantics as a side effect. The exclusion belongs at the call site of the lane that needs it.
- **Author the inert exclusion as a FACT, never a verdict.** The new `_RemoteTerms` field records what the author is; the call site decides what to do about it. A field named for the decision puts a judgment in a dataclass whose other members are observations.
- **The update bot's login is `dependabot[bot]`, and it is already declared once** as `UPDATE_BOT_LOGIN` (`estate_landing_admission.py:79`). Import it; never spell the literal a second time.
- **Run `make check` from an absolute `cd` inside the command itself**, and read pytest's `rootdir:` line beside the collected count. A backgrounded gate inherits a drifted cwd and a green over the wrong tree is success-shaped.
- **Reconcile the collected-test count by diffing node ids**, not by arithmetic: `pytest --collect-only -q | grep :: | sort` on both sides, then `comm`. Assert the node-id file is non-empty before believing the diff.
- **Mutation controls are required** for Task 4's withholding (repo rule: guard-shaped work is reviewed by mutation, not by reading). Set `PYTHONDONTWRITEBYTECODE=1`, restore from git between mutants, and report every survivor.

---

## Prerequisites (not a task)

The spec is PR #286 on branch `rebase-design`. Do not add these commits to it — its `Quality` check is mid-run and a new commit restarts a ~29-minute gate.

1. Merge #286 when green.
2. Cut a fresh worktree from the merged `main`:

```bash
cd /Users/devon/Projects/orchestrator
git fetch origin
git worktree add .worktrees/stop-freshening -b stop-freshening origin/main
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening
uv sync --frozen
createdb -h 127.0.0.1 -U postgres orchestrator_test_stopfresh
export TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/orchestrator_test_stopfresh
export ORCHESTRATOR_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/orchestrator_test_stopfresh
export SECURITY_STANDARDS_DIR="$PWD/tests/fixtures/security-standards"
uv run alembic upgrade head
```

Separate `export` statements are deliberate: `export A=x B="$A"` expands `$A` to its prior value.

3. Record the baseline before changing anything:

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -m pytest --collect-only -q 2>/dev/null | grep :: | sort > /tmp/nodes-main.txt && \
  wc -l /tmp/nodes-main.txt
```

## Corrections this plan makes to the spec

Two statements in the design were measured wrong and are corrected here rather than by reopening #286. Carry both into the ADR.

1. **§6 says `_remote_terms` returns from three sites in the inert lane, "two of them before the author is known."** Measured: it returns at `inert_landing_admission.py:331`, `:352` and `:404`, and `pull` — hence `author_login` — is in hand at **two** of the three. Only the gateway-error return at `:331` has no pull at all. The polarity requirement stands unchanged; only the count was wrong.
2. **§4 says the estate act's tests go with it.** `tests/services/test_estate_pr_branch_update.py` also holds the **entire test coverage of the shared predicate** (lines 137–521, ~28 assertions) plus the `EstateLandingAdmissionResponse` field-set pin (`:885`). Deleting the file wholesale would silently strip coverage the spec's §5 requires to survive. Task 1 exists for this.

## The one decision the spec left open

After the estate lander's pass is deleted, **`branch_update_qualifies` on the estate answer has zero readers** — measured: its only consumer was `src/estate_lander/cli.py:475`. `rollout_base_matches_pin` is *not* in the same position; it keeps an independent reader in the estate lander's reporting classifier (`cli.py:235,271,342`) and stays.

**Decision: delete `branch_update_qualifies` from the estate answer (Task 3).** This repository's own rule is that a dead config knob is the same defect as a dead function and both go together. Keeping it would serve a live-looking permission for a capability just removed, inviting a future lane to build on it. The inert answer keeps its copy; the two responses already differ deliberately (`schemas.py:769`).

Both deploy orderings are safe: an old lander against a new orchestrator reads a missing key and skips, which is the desired outcome; a new lander has no reader at all.

## File Structure

**Deleted**

| Path | Why |
|---|---|
| `src/orchestrator/services/estate_pr_branch_update.py` (340 lines) | The act. Its only `src/` importer is `api/routes.py:172`. |

**Modified**

| Path | Responsibility after the change |
|---|---|
| `src/orchestrator/api/routes.py` | Serves the landing routes; no longer the estate branch-update route (`:839`) or its import (`:172`). |
| `src/orchestrator/api/schemas.py` | Drops `EstateBranchUpdateCommandModel` (`:664`), `EstateBranchUpdateResponse` (`:688`), and `EstateLandingAdmissionResponse.branch_update_qualifies` (`:655`). |
| `src/orchestrator/services/estate_landing_admission.py` | Keeps both shared predicates and `rollout_base_matches_pin`; drops the `branch_update_qualifies` dataclass field (`:429`) and its computation (`:692`). |
| `src/orchestrator/services/inert_landing_admission.py` | Gains the author fact on `_RemoteTerms` and the exclusion at `:247`. |
| `src/orchestrator/services/inert_pr_branch_update.py` | Unchanged behaviour; its advisory-lock key stops naming a deleted module. |
| `src/estate_lander/cli.py` | Reports on landings only. Loses `_branch_updates` (`:447`), `_update_key` (`:225`), `_UPDATE_SELF_CLEARING` (`:168`), the two update statuses, and the pass call (`:546`). |
| `src/estate_lander/orchestrator_client.py` | Loses `_BRANCH_UPDATE` (`:33`), its allowlist membership (`:64`) and `update_branch` (`:206`). |

**Tests moved, then modified**

| Path | Responsibility |
|---|---|
| `tests/services/test_estate_landing_admission.py` | **Gains** the shared-predicate suites and the response field-set pin (Task 1), then absorbs the estate field's removal (Task 3). |
| `tests/services/test_estate_pr_branch_update.py` | Deleted in Task 2, after Task 1 has emptied it of everything that must survive. |
| `tests/services/test_inert_landing_admission.py` | Two assertions flip; four new ones pin both directions. |
| `tests/estate_lander/test_estate_lander.py` | Loses the update-pass suites and `FakeOrchestrator.update_branch`. |

**Unchanged, and deliberately so:** `tests/services/estate_landing_doubles.py` — imported by seven test files including three inert ones, and its `update_branch` gateway double still serves the surviving inert act.

---

### Task 1: Move the shared predicate's tests to a file that survives

The predicate is what §5 forbids changing, and its whole suite currently sits in the file Task 2 deletes. This task moves coverage and changes no behaviour, so it lands first and can be reviewed on its own.

**Files:**
- Modify: `tests/services/test_estate_landing_admission.py` (1278 lines; already imports from `estate_landing_admission` and the doubles)
- Modify: `tests/services/test_estate_pr_branch_update.py:137-521, 885-901` (the sections being moved out)

**Interfaces:**
- Consumes: `qualifies_for_branch_update`, `freshness_derived_refusals`, `EstateLandingAdmission`, `EstateLandingAdmissionResponse` — all unchanged by this task.
- Produces: predicate coverage at a path Task 2 does not delete. Task 3 edits the moved field-set pin.

- [ ] **Step 1: Record what the predicate suites currently prove**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -m pytest tests/services/test_estate_pr_branch_update.py --collect-only -q 2>/dev/null \
  | grep :: | sort > /tmp/dying-before.txt && wc -l /tmp/dying-before.txt
```

Keep this file. Step 6 uses it to prove nothing was lost in the move.

- [ ] **Step 2: Move three whole sections plus the pin**

Cut from `tests/services/test_estate_pr_branch_update.py` and paste into `tests/services/test_estate_landing_admission.py`, appending at the end of the file. Move complete sections including their banner comments:

| From (dying file) | Section |
|---|---|
| `:137-238` | The qualification rule — `test_freshness_alone_qualifies` through `test_without_freshness_there_is_NOTHING_TO_DO_and_it_does_not_qualify` |
| `:240-302` | The rollout carve-out — `test_a_rollout_pin_that_differs_BECAUSE_THE_HEAD_IS_STALE_qualifies` through `test_the_carve_out_excuses_ONE_refusal_and_nothing_beside_it` |
| `:304-521` | Freshness-derived refusals and the cross-lander agreement suite — `test_a_stale_rollout_pin_is_freshness_derived_when_the_base_carries_the_pinned_bytes` through `test_the_two_copies_of_the_freshness_criterion_AGREE_POINTWISE` |
| `:885-901` | `test_the_served_answer_DECLARES_the_verdict_the_caller_reads` |

These are pure moves. Do not reword a docstring or rename a test — a moved test whose text changed is two reviews, and the second one hides in the first.

- [ ] **Step 3: Carry the imports the moved tests need**

The moved tests reference names the destination may not import yet. Add to the destination's existing `from orchestrator.services.estate_landing_admission import (...)` block whatever the moved code uses and the destination lacks — at minimum `qualifies_for_branch_update` and `freshness_derived_refusals`. Resolve the rest by running the file; do not guess the list.

The moved pin imports inside the function body and needs no module-level import:

```python
    from orchestrator.api.schemas import EstateLandingAdmissionResponse
    from orchestrator.services.estate_landing_admission import EstateLandingAdmission
```

Then prune the dying file's now-unused imports from its `from orchestrator.services.estate_landing_admission import (` block at `:35`. Let ruff name them rather than reading by eye:

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && .venv/bin/ruff check tests/services/ --select F401
```

- [ ] **Step 4: Run both files**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -m pytest tests/services/test_estate_landing_admission.py \
    tests/services/test_estate_pr_branch_update.py 2>&1 | tail -20
```

Expected: PASS, with the total unchanged from Step 1's baseline plus the destination's own prior count.

- [ ] **Step 5: Prove the predicate tests actually moved rather than being duplicated or dropped**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -m pytest tests/services/ --collect-only -q 2>/dev/null | grep :: | sort > /tmp/svc-after.txt && \
  grep -c "qualifies_for_branch_update\|freshness_derived\|AGREE_POINTWISE\|DECLARES_the_verdict" /tmp/svc-after.txt
```

Then confirm no node id is present twice:

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  sort /tmp/svc-after.txt | uniq -d | head
```

Expected: empty output. A duplicate means a section was copied rather than moved.

- [ ] **Step 6: Confirm the move is conservative**

Every node id that existed in the dying file before the move must still exist somewhere, under a new path:

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  sed 's/.*:://' /tmp/dying-before.txt | sort > /tmp/before-names.txt && \
  sed 's/.*:://' /tmp/svc-after.txt | sort -u > /tmp/after-names.txt && \
  comm -23 /tmp/before-names.txt /tmp/after-names.txt
```

Expected: empty. Anything listed is a test the move lost.

- [ ] **Step 7: Commit**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git add tests/services/test_estate_landing_admission.py tests/services/test_estate_pr_branch_update.py && \
  git commit -m "test: move the shared branch-update predicate's suites out of the act's file

The predicate is one definition with two readers (ADR-0024) and survives the
estate act's deletion. Its tests were sitting in the file that deletion removes."
```

---

### Task 2: Delete the estate lane's branch-update act

The estate lane's freshening population is Dependabot by construction, so there is nothing to narrow. Measured at `estate_landing_admission.py:867`: a pull request whose author is not `UPDATE_BOT_LOGIN`, or is not a bot, draws `LANDING_AUTHOR_NOT_THE_UPDATE_BOT`, and `qualifies_for_branch_update` never subtracts it.

**Files:**
- Delete: `src/orchestrator/services/estate_pr_branch_update.py`
- Delete: `tests/services/test_estate_pr_branch_update.py`
- Modify: `src/orchestrator/api/routes.py:172-175, 839-870`
- Modify: `src/orchestrator/api/schemas.py:664-706`
- Modify: `src/orchestrator/services/inert_pr_branch_update.py:170`
- Modify: `src/estate_lander/cli.py:153,168,225,447-520,546` and the two status sets
- Modify: `src/estate_lander/orchestrator_client.py:33,64,206`
- Modify: `tests/estate_lander/test_estate_lander.py:25,90,745-1000,1067-1100`
- Modify (guards): `tests/idempotency/matrix.py:159,174`; `tests/architecture/test_inert_lander_isolation.py:98`; `tests/architecture/test_scope_guards.py:68`; `tests/architecture/test_estate_lander_isolation.py:82,85,101`; `tests/api/test_lifecycle_api.py:69,111`

**Interfaces:**
- Consumes: nothing from Task 1 beyond its guarantee that the predicate's tests now live elsewhere.
- Produces: an estate lane with no write surface but the landing itself. `is_allowed_write` narrows to a single path, which Task 3 does not touch.

- [ ] **Step 1: Prove two things before deleting anything**

**(a) The module has exactly one `src/` importer.**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  grep -rn "from orchestrator.services.estate_pr_branch_update import" src/ tests/ && \
  echo "--- bare references ---" && grep -rn "estate_pr_branch_update" src/
```

Expected: `src/orchestrator/api/routes.py:172` is the only `src/` import. The hit at `src/orchestrator/services/inert_pr_branch_update.py:170` is a **string literal**, not an import — Step 6 handles it. If anything else in `src/` imports a symbol from the module, stop: that symbol must move to `estate_landing_admission.py` beside the predicate it serves, and this task's file list is wrong.

**(b) The act's population really is Dependabot by construction.** This is the spec's first proof obligation, and it is what makes deleting the estate act right where the inert lane merely narrows. The argument is that a non-Dependabot author always draws `LANDING_AUTHOR_NOT_THE_UPDATE_BOT` (`estate_landing_admission.py:867`) and that the predicate never subtracts it — so no such pull request could ever have qualified. Demonstrate the second half rather than reading it:

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -c "
from orchestrator.services.estate_landing_admission import (
    LANDING_AUTHOR_NOT_THE_UPDATE_BOT as A,
    LANDING_HEAD_NOT_CURRENT_WITH_BASE as F,
    qualifies_for_branch_update as q,
)
# Freshness alone qualifies -- the positive control, without which the line below proves nothing.
assert q((F,), rollout_base_matches_pin=False), 'freshness alone should qualify'
# The same head, authored by anyone else, never does -- at either value of the carve-out.
assert not q((F, A), rollout_base_matches_pin=False)
assert not q((F, A), rollout_base_matches_pin=True)
print('the estate act could never have freshened a non-Dependabot branch')
"
```

A bare assertion that the author refusal disqualifies would pass against a predicate that disqualifies everything, which is why the positive control is on the first line.

- [ ] **Step 2: Delete the act and its route**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git rm src/orchestrator/services/estate_pr_branch_update.py \
         tests/services/test_estate_pr_branch_update.py
```

In `src/orchestrator/api/routes.py`, remove the import at `:172-175`:

```python
from orchestrator.services.estate_pr_branch_update import (
    EstateBranchUpdateCommand,
    update_estate_pull_request_branch,
)
```

and the whole route beginning at `:839`:

```python
@router.post("/estate-pr-branch-update", response_model=EstateBranchUpdateResponse)
def estate_pr_branch_update_route(
```

through the end of its body. Remove `EstateBranchUpdateResponse` and `EstateBranchUpdateCommandModel` from the `schemas` import block at the top of the file.

- [ ] **Step 3: Delete the two schemas**

In `src/orchestrator/api/schemas.py`, remove `class EstateBranchUpdateCommandModel` (`:664`) and `class EstateBranchUpdateResponse` (`:688`) entirely, including their docstrings. Leave `EstateLandingAdmissionResponse` alone — Task 3 owns that field.

- [ ] **Step 4: Delete the lander's pass and its now-dead vocabulary**

In `src/estate_lander/cli.py`, remove:

- `_UPDATE_SELF_CLEARING` (`:168`) with its preceding comment block
- `_update_key` (`:225`)
- `_branch_updates` (`:447`) in full
- the call at `:546`: `outcomes.extend(_branch_updates(selection.subjects, client, args.submit))`
- `"updated"` and `"would-update"` from `_NOT_A_FINDING` (`:179`) and from the status list at `:190-196`

Leave `_BASE_MATCHES_PIN` (`:153`), `_freshness_derived` (`:235`) and `_held_status` (`:271`) **in place** — they are the reporting classifier and read `rollout_base_matches_pin`, which survives.

- [ ] **Step 5: Narrow the client's write allowlist**

In `src/estate_lander/orchestrator_client.py`, remove `_BRANCH_UPDATE` (`:33`) and the `update_branch` method (`:206`), and reduce the allowlist at `:64`:

```python
def is_allowed_write(path: str) -> bool:
    return path == _LAND
```

- [ ] **Step 6: Stop the surviving act's lock key naming a deleted module**

Both acts took the same per-repository advisory lock. Only the inert one remains, so at `src/orchestrator/services/inert_pr_branch_update.py:170`:

```python
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        # One lock per repository for the branch-update act. It was named for the estate lane's
        # copy of this act until that act was deleted; the name is neutral now because only this
        # lane takes it. Renaming a lock key is safe here and would not be everywhere: the
        # orchestrator's swap drops the old container before the new one serves, so no two
        # containers ever hold the two spellings at once.
        {"key": f"pr_branch_update:{repository}"},
    )
```

- [ ] **Step 7: Update the six guard sites**

Each of these is an exact-set inventory that fails CI on a stale entry. Remove the estate branch-update path from every one:

- `tests/idempotency/matrix.py:159` — the `"/api/v1/estate-pr-branch-update"` row, and its `:174` reference to the deleted test
- `tests/architecture/test_inert_lander_isolation.py:98`
- `tests/architecture/test_scope_guards.py:68` — the POST-route inventory
- `tests/architecture/test_estate_lander_isolation.py:82,85,101` — `:82` asserts the path IS allowed and must go, not merely flip
- `tests/api/test_lifecycle_api.py:69,111` — both `"/api/v1/estate-pr-branch-update": "expected_head_sha"` entries

- [ ] **Step 8: Delete the lander's update-pass tests**

In `tests/estate_lander/test_estate_lander.py`: remove `_branch_updates` from the import at `:25`, delete `FakeOrchestrator.update_branch` (`:90`) and the subclass override at `:895`, and delete every suite calling `_branch_updates` (`:745-1000`) and the two client-level update tests at `:1067-1100`. Keep `test_the_landing_pass_runs_BEFORE_the_branch_update_pass` deleted with them — it pins an ordering that no longer has two terms.

- [ ] **Step 9: Run the gate**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && make check 2>&1 | tail -30
```

Expected: PASS. Read `rootdir:` in the header and confirm it names the worktree, not `/Users/devon/Projects/orchestrator`.

A failure naming `test_unreachable_guards` means a helper lost its last caller — delete it rather than allowlisting it. A failure naming a route inventory means Step 7 missed a site.

- [ ] **Step 10: Commit**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git add -A && \
  git commit -m "feat(estate-landing): stop editing branches Dependabot owns

Our update-branch call merges base into head under the App's identity, after
which Dependabot disowns the branch and will not rebase it. A competing bump
then conflicts it and neither party can act.

The estate lane's freshening population is Dependabot by construction -- a
non-Dependabot author always draws landing_author_not_the_update_bot, which
qualifies_for_branch_update never subtracts -- so the act is deleted rather
than narrowed."
```

---

### Task 3: Stop serving a verdict nobody reads

`branch_update_qualifies` on the estate answer lost its only consumer when Task 2 deleted the lander's pass. See "The one decision the spec left open".

**Files:**
- Modify: `src/orchestrator/services/estate_landing_admission.py:425-430, 692-694`
- Modify: `src/orchestrator/api/schemas.py:655`
- Modify: `tests/services/test_estate_landing_admission.py` (the pin moved in Task 1, plus any estate assertion on the field)

**Interfaces:**
- Consumes: Task 1's moved field-set pin, which is what keeps the dataclass and the response model honest across this change.
- Produces: an estate answer of eight fields. `qualifies_for_branch_update` keeps exactly one caller — the inert lane — which Task 4 then narrows.

- [ ] **Step 1: Prove the field has no reader left**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  grep -rn "branch_update_qualifies" src/ | sed 's/^/  /'
```

Expected: hits only in `inert_landing_admission.py`, `inert_pr_branch_update.py`, `inert_lander/cli.py`, and the two definition sites in `estate_landing_admission.py` / `schemas.py` about to be removed. **Any remaining `estate_lander` hit means Task 2 is incomplete — stop and finish it.**

- [ ] **Step 2: Remove the dataclass field**

In `src/orchestrator/services/estate_landing_admission.py`, delete from `EstateLandingAdmission` (`:429`) the field and its comment:

```python
    # A SECOND, much smaller permission composed from the same terms: not "may this land" but "may
    # the lane bring this branch up to date with its base". Served on the read surface so a dry run
    # can report what a live pass would do without anything acting -- the acting path recomposes
    # this from scratch and never trusts a caller's copy of it.
    branch_update_qualifies: bool
```

Keep `rollout_base_matches_pin` and its comment: it has an independent reader in the out-of-process reporting agent, which is what that comment says.

- [ ] **Step 3: Remove the computation**

At `:692`, delete the argument:

```python
        branch_update_qualifies=qualifies_for_branch_update(
            tuple(refusals), rollout_base_matches_pin=remote.rollout_base_matches_pin
        ),
```

Leave the sibling `rollout_base_matches_pin=remote.rollout_base_matches_pin` at `:695` in place.

- [ ] **Step 4: Remove the response-model field**

In `src/orchestrator/api/schemas.py`, delete `branch_update_qualifies: bool` at `:655` from `EstateLandingAdmissionResponse`. Update the class docstring if it names the field.

- [ ] **Step 5: Record why, where the next reader will look**

Add to `EstateLandingAdmission`'s docstring in `estate_landing_admission.py`:

```python
    """The composed answer, plus what the act needs in order to name what it acted on.

    It carries no branch-update verdict. This lane had one until ADR-0045, when the act it
    permitted was deleted: our update-branch call is an edit under the App's identity, and
    Dependabot disowns a branch it did not write last. `qualifies_for_branch_update` survives
    unchanged for the inert lane, which serves an author with no such behaviour.
    """
```

- [ ] **Step 6: Fix the moved pin's expectations and run**

The moved `test_the_served_answer_DECLARES_the_verdict_the_caller_reads` asserts set equality between the dataclass and the response model. Removing the field from **both** keeps it green with no edit — which is the point of having moved it. Confirm rather than assume:

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -m pytest tests/services/test_estate_landing_admission.py \
    -k "DECLARES_the_verdict" -v 2>&1 | tail -10
```

Expected: PASS. A failure here means the field was removed from one side only.

- [ ] **Step 7: Run the gate**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && make check 2>&1 | tail -30
```

- [ ] **Step 8: Commit**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git add -A && \
  git commit -m "refactor(estate-landing): stop serving a branch-update verdict nobody reads

Its only consumer was the lander pass deleted one commit ago. A live-looking
permission for a capability that no longer exists invites the next lane to
build on it. rollout_base_matches_pin stays: the reporting agent reads it."
```

---

### Task 4: Withhold the inert lane's branch update for the update bot

Deploy policy v8 declares two permitted authors. `octo-upstream-sync[bot]` rebuilds its own rolling branch daily and has none of Dependabot's disowning behaviour, so freshening it is harmless and must keep working. This is the task where a test that passes because nothing is freshened any more would be indistinguishable from the defect.

**Files:**
- Modify: `src/orchestrator/services/inert_landing_admission.py:80-100, 310-313, 331, 352, 404, 247`
- Modify: `tests/services/test_inert_landing_admission.py:444, 513` and new tests beside them

**Interfaces:**
- Consumes: `UPDATE_BOT_LOGIN` from `estate_landing_admission`, and `qualifies_for_branch_update` unchanged.
- Produces: `_RemoteTerms.author_is_update_bot: bool`. Nothing outside this module reads it.

- [ ] **Step 1: Write the failing tests — both directions, plus the unknown author**

In `tests/services/test_inert_landing_admission.py`, beside the existing freshness suites. `UPDATE_BOT` and `SYNC_BOT` are already imported from `tests.services.inert_landing_doubles` (`:34,40`).

```python
def test_a_stale_branch_the_UPDATE_BOT_owns_is_never_freshened(
    migrated_session: Session,
) -> None:
    """ADR-0045. Our update-branch call merges base into head under the App's identity, and
    Dependabot then refuses to rebase a branch somebody else edited. A competing bump conflicts
    it and no party can act, so the lane declines to create that state."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=UV_BRANCH, author_login=UPDATE_BOT), behind=2
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert LANDING_HEAD_NOT_CURRENT_WITH_BASE in answer.refusals
    assert answer.branch_update_qualifies is False


def test_a_stale_branch_the_SYNC_BOT_owns_is_STILL_freshened(
    migrated_session: Session,
) -> None:
    """The half that makes the test above mean something. The exclusion is keyed on the author
    that disowns an edited branch, not on freshening being withdrawn -- a suite where nothing is
    ever freshened would pass the test above for the wrong reason."""
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=SYNC_BRANCH, author_login=SYNC_BOT), behind=2
    )

    answer = _answer(migrated_session, gateway=gateway)

    assert LANDING_HEAD_NOT_CURRENT_WITH_BASE in answer.refusals
    assert answer.branch_update_qualifies is True


def test_an_author_the_remote_never_named_withholds_the_branch_update() -> None:
    """The gateway-error return knows no author at all, and an unknown author withholds.

    Asserted on `_RemoteTerms` DIRECTLY rather than through the composed answer, deliberately:
    that return already refuses with `landing_pull_request_unreadable`, which the predicate does
    not subtract, so the composed verdict is False whatever this field says. A control read
    through the answer cannot tell a fail-closed default from a fail-open one.
    """

    class _Unreadable:
        def read_pull_request(self, **_: object) -> object:
            raise EstateGatewayError("read_pull_request", 500)

    remote = _remote_terms(INERT_REPOSITORY, PR, None, _Unreadable())  # type: ignore[arg-type]

    assert remote.author_is_update_bot is True
```

Add `_remote_terms` to the module's imports from `orchestrator.services.inert_landing_admission`. Everything else this code needs is already imported there: `INERT_REPOSITORY`, `SYNC_BOT`, `SYNC_BRANCH` and `UPDATE_BOT` from `tests.services.inert_landing_doubles`; `pull_request`, `run` and `FakeEstateGateway` from `tests.services.estate_landing_doubles`; `EstateGatewayError` from the admission module. Note the repository constant is `INERT_REPOSITORY` and not `REPOSITORY` — there is no bare `REPOSITORY` in that module.

- [ ] **Step 2: Run them and watch them fail**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -m pytest tests/services/test_inert_landing_admission.py \
    -k "UPDATE_BOT_owns or SYNC_BOT_owns or never_named" -v 2>&1 | tail -20
```

Expected: `test_a_stale_branch_the_UPDATE_BOT_owns_is_never_freshened` FAILS asserting `True is False`; `test_an_author_the_remote_never_named...` FAILS with `AttributeError: 'ˍRemoteTerms' object has no attribute 'author_is_update_bot'`; the `SYNC_BOT` one PASSES already.

That last one passing now is expected and is not the test being useless — Step 5 is where it earns its place, by staying green while its twin flips.

- [ ] **Step 3: Carry the author as a fact on `_RemoteTerms`**

At `src/orchestrator/services/inert_landing_admission.py:310`:

```python
@dataclass(frozen=True)
class _RemoteTerms:
    term: Term
    head_sha: str | None
    merge_method: str
    # ADR-0045. A FACT about the pull request, not a verdict about it: what to do with it is the
    # caller's, one function below. `True` when the remote could not be read at all -- an author
    # nobody established must withhold, and asserting "is the update bot" of an unknown author is
    # the fail-closed reading rather than an observation.
    author_is_update_bot: bool
```

Add `UPDATE_BOT_LOGIN` to the existing import block at `:80`, in alphabetical position:

```python
from orchestrator.services.estate_landing_admission import (
    LANDING_ALREADY_RECORDED,
    ...
    MERGEABLE_UNKNOWN,
    UPDATE_BOT_LOGIN,
    EstateGatewayError,
    ...
)
```

- [ ] **Step 4: Supply it at all three returns**

`:331` — the remote could not be read, so there is no author:

```python
        return _RemoteTerms(
            Term(False, (LANDING_PULL_REQUEST_UNREADABLE,)), None, SQUASH, True
        )
```

`:352` — the pull request is in hand even though the rules are not:

```python
        return _RemoteTerms(
            Term(False, tuple(refusals)),
            pull.head_sha,
            SQUASH,
            pull.author_login == UPDATE_BOT_LOGIN,
        )
```

`:404` — the composed return:

```python
    return _RemoteTerms(
        Term(met, tuple(refusals)),
        pull.head_sha,
        merge_method,
        pull.author_login == UPDATE_BOT_LOGIN,
    )
```

- [ ] **Step 5: Apply the exclusion at the call site, not inside the predicate**

At `:247`, extend the existing comment block and wrap the call:

```python
        # ADR-0045. The exclusion sits HERE rather than inside the predicate, for the reason the
        # comment above gives about reuse: that predicate is one definition with two readers, and
        # teaching it about authors would move the reporting agent's semantics as a side effect.
        # This lane's other declared author rebuilds its own branch daily and has none of the
        # update bot's disowning behaviour, so it keeps being freshened.
        branch_update_qualifies=(
            not remote.author_is_update_bot
            and qualifies_for_branch_update(tuple(refusals), rollout_base_matches_pin=False)
        ),
```

- [ ] **Step 6: Run the new tests**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -m pytest tests/services/test_inert_landing_admission.py \
    -k "UPDATE_BOT_owns or SYNC_BOT_owns or never_named" -v 2>&1 | tail -20
```

Expected: all three PASS.

- [ ] **Step 7: Correct the two existing assertions the narrowing flips**

Both use the fixture default author, which is `dependabot[bot]` (`tests/services/estate_landing_doubles.py:139`), so both now correctly report `False`. Do not simply flip the literal — each test's subject is the freshness rule, not the author rule, so give each an author that keeps it about freshness.

At `:435`, `test_a_head_behind_its_base_is_refused_and_qualifies_for_a_branch_update`:

```python
    gateway = FakeEstateGateway(
        pull=pull_request(number=PR, head_ref=SYNC_BRANCH, author_login=SYNC_BOT), behind=2
    )
```

At `:502`, `test_a_head_behind_its_base_whose_checks_reached_no_verdict_still_qualifies`:

```python
    gateway = FakeEstateGateway(
        pull=pull_request(
            number=PR, head_ref=SYNC_BRANCH, author_login=SYNC_BOT, mergeable_state="blocked"
        ),
        behind=2,
        runs=(run(conclusion="cancelled"),),
    )
```

Leave `:472` and `:496` alone — both already assert `False` for reasons unrelated to the author.

- [ ] **Step 8: Run the file**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -m pytest tests/services/test_inert_landing_admission.py 2>&1 | tail -15
```

- [ ] **Step 9: Mutate, and read the kills rather than the count**

Write the harness into your own worktree, not a shared scratchpad — concurrent sessions share one scratch directory and a fixed name collides.

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git status --porcelain | head   # must be empty; a harness restores from git
```

Four mutants, each applied to `src/orchestrator/services/inert_landing_admission.py`, restored with `/usr/bin/git checkout HEAD -- <path>` between runs, under `PYTHONDONTWRITEBYTECODE=1`:

| # | Mutation | Must be killed by |
|---|---|---|
| M1 | `not remote.author_is_update_bot and qualifies_for_branch_update(...)` → `qualifies_for_branch_update(...)` | `test_a_stale_branch_the_UPDATE_BOT_owns_is_never_freshened` |
| M2 | `not remote.author_is_update_bot and ...` → `not remote.author_is_update_bot` | `test_a_head_behind_its_base_whose_checks...` and the `second_obstacle` suite |
| M3 | the `:331` default `True` → `False` | `test_an_author_the_remote_never_named_withholds_the_branch_update` **only** |
| M4 | `pull.author_login == UPDATE_BOT_LOGIN` at `:404` → `False` | `test_a_stale_branch_the_UPDATE_BOT_owns_is_never_freshened` |

Assert the anchor matched **exactly once** per mutant before running the suite. An anchor matching zero times reports a pass the run did not earn; matching twice means it hit a sibling return.

M3 is the one to watch: if it survives, the control at Step 1 was written through the composed answer rather than against `_RemoteTerms`, and clause C of the spec is unpinned.

- [ ] **Step 10: Run the gate**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && make check 2>&1 | tail -30
```

- [ ] **Step 11: Commit**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git add -A && \
  git commit -m "feat(inert-landing): withhold the branch update for the update bot

Policy v8 declares two authors. One rebuilds its own branch daily and is
unaffected by an edit; the other disowns a branch it did not write last. The
exclusion is at the call site -- the predicate is one definition with two
readers and stays untouched."
```

---

### Task 5: Record the decision as ADR-0045

The durable finding is about a third party's behaviour, established by a probe: a GitHub App cannot drive Dependabot. That outlives this particular act, which is why it is an ADR rather than an amendment to ADR-0019.

**Files:**
- Create: `docs/decisions/0045-the-lane-stops-freshening-dependabot-branches.md`

- [ ] **Step 1: Confirm the number is free**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && ls docs/decisions/ | sort | tail -3
```

Expected: `0044-…` is the highest. If not, take the next free number and use it consistently in the code comments already written in Tasks 3 and 4.

- [ ] **Step 2: Write the ADR**

Follow the house shape of `docs/decisions/0044-*.md`. It must carry, in this order:

1. **Status** — Accepted, 2026-09-20. **Supersedes in part:** ADR-0019 Increment 6, for the estate lane only.
2. **The defect, and that we built the first half of it.** The chain: a landing stales siblings → the lane freshens them → each carries a commit authored by `alobar-sds-dispatch[bot]` → Dependabot refuses to rebase → a competing bump conflicts one → neither party can act.
3. **The measurement.** 26 pull requests freshened across 50 update events; 24 landed anyway; the 2 that did not were `change-manager#87` and `brain#70`, both `DIRTY` with `author=alobar-sds-dispatch[bot]` on the head commit. The control: `code-standards` is not in the lane, its two open Dependabot pull requests have never been freshened, and both are `CLEAN`.
4. **The probe that killed the obvious fix.** `@dependabot recreate` posted as the App: HTTP 201, and Dependabot answered in three seconds — *"Sorry, only users with push access can use that command."* The same command under a user identity was obeyed and both pull requests recreated in place. **This was not discoverable by reading permissions**: the App holds `contents: write` and `pull_requests: write`, which is push access in GitHub's own model.
5. **The decision.** Stop creating the condition. Named cost: a behind-but-clean Dependabot pull request now waits for Dependabot rather than being freshened in seconds. Named benefit: it can never reach a state only a human can leave.
6. **Why the two lanes differ.** Estate: the population is Dependabot by construction (`estate_landing_admission.py:867`), so there is nothing to narrow and the act is deleted. Inert: policy v8 declares two authors and only one disowns, so it narrows at the call site.
7. **What deliberately does not change** — the shared predicate, per ADR-0024, and the reporting semantics. Being behind stays freshness-derived and stays a non-finding.
8. **Two consequential sub-decisions, recorded as decisions rather than tidying.** The estate answer stops serving `branch_update_qualifies` (its only reader was the deleted pass; a dead knob and a dead function go together). The surviving act's advisory-lock key stops naming a deleted module, which is safe only because the orchestrator's swap drops the old container before the new one serves.
9. **Residuals.** Recovery from an already-edited branch needs a user identity — the App is refused; the two open ones were cleared by hand on 2026-09-20. `@dependabot recreate` regenerates in place, keeping the pull request number, so a change record keyed on `(repository, pull_request_number)` follows it. Landing latency for behind-but-clean Dependabot pull requests will rise by an unmeasured amount: `weekly` governs *creating* pull requests, not maintaining them, and this estate has never observed Dependabot freshening an unedited branch here because the lane always got there first. **That measurement is worth taking once this is live.**
10. **The spec's two corrections**, from the top of this plan.

- [ ] **Step 3: Check the prose against the word guards before the gate does**

ADRs are not scanned by the `src/`-only guards, but the ADR's phrasing gets copied into module docstrings. Verify any sentence you intend to reuse in code:

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -c "
import sys; sys.path.insert(0, 'tests/architecture')
from test_ws32_scope_guards import _tokenize, _contains_sequence, FORBIDDEN_SEQUENCES
text = open('docs/decisions/0045-the-lane-stops-freshening-dependabot-branches.md').read()
tokens = _tokenize(text)
for label, seq in FORBIDDEN_SEQUENCES:
    if _contains_sequence(tokens, seq):
        print('WOULD RED IN SOURCE:', label)
print('checked')
"
```

- [ ] **Step 4: Commit**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git add docs/decisions/ && \
  git commit -m "docs: ADR-0045, the lane stops freshening Dependabot's branches"
```

---

### Task 6: Correct the record in CLAUDE.md

Two bullets describe an act that no longer exists. Left alone, they are the shape this file warns about — a closed item still written as live, inherited as fact by the next session.

**Files:**
- Modify: `CLAUDE.md` — the "A landing stales every sibling pull request, and `update-branch` clears it synchronously" bullet, and the third freshness ruling ("freshness beside an exception is itself non-finding")

- [ ] **Step 1: Find both bullets**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  grep -n "update-branch clears it synchronously\|freshness beside an exception\|_UPDATE_SELF_CLEARING\|never be touched" CLAUDE.md
```

- [ ] **Step 2: Append the correction to the `update-branch` bullet**

Do not delete the bullet — the measurement in it (12 seconds versus ~14 hours, the 202-versus-200 contract) is still true and still the reason the act looked right. Append:

```markdown
  **CLOSED 2026-09-20 (ADR-0045), AND THE ACT IS GONE FROM THE ESTATE LANE ENTIRELY.** Everything
  above is a correct account of a mechanism that worked and of a design defect underneath it: the
  update-branch call MERGES BASE INTO HEAD, so the resulting commit is authored by the App, and
  Dependabot then refuses to rebase a branch somebody else edited. The lane was therefore creating
  a deadlock it could not clear -- measured across 26 pull requests and 50 update events, of which
  24 landed anyway and 2 (`change-manager#87`, `brain#70`) stuck permanently. The obvious recovery
  does not exist: `@dependabot recreate` posted as the App returns HTTP 201 and is refused three
  seconds later with *"only users with push access can use that command"*, while the identical
  command under a user identity is obeyed. **An App cannot drive Dependabot, and no permission
  grant changes that** -- the App already holds `contents: write` and `pull_requests: write`.
  The estate lane's act, route, client method and lander pass are deleted; the INERT lane keeps its
  branch update and withholds it for `dependabot[bot]` alone, because the other author policy v8
  declares rebuilds its own branch daily and has no such behaviour. `change-manager#48` is no
  longer the standing live control for this rule; nothing on the estate lane freshens at all.
```

- [ ] **Step 3: Append a pointer to the third freshness ruling**

That ruling is about *reporting* and is unaffected — a refusal the system produced by deliberately declining to act still carries no information. Add one line so a reader does not go looking for the act:

```markdown
  **Note 2026-09-20 (ADR-0045): the estate lane no longer freshens anything**, so its half of this
  rule is now about a refusal nothing produces. The rule itself is unchanged and still live for the
  inert lane, which freshens the sync bot's branches and withholds for the update bot's.
```

- [ ] **Step 4: Verify the stanza boundary is intact**

Everything added must sit below `<!-- code-standards:end -->`, or a `code-standards sync` deletes it.

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  grep -n "code-standards:end" CLAUDE.md && \
  grep -n "CLOSED 2026-09-20 (ADR-0045)" CLAUDE.md
```

Expected: the ADR-0045 line numbers are all greater than the `code-standards:end` line.

- [ ] **Step 5: Commit**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git add CLAUDE.md && \
  git commit -m "docs: the estate lane no longer freshens branches (ADR-0045)"
```

---

### Task 7: Land, deploy and verify

**Files:** none — this is the operational close.

- [ ] **Step 1: Re-read `main` after fetching**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git fetch origin && \
  git log --no-show-signature --oneline origin/main -5 && \
  git merge-base --is-ancestor origin/main HEAD && echo "up to date with main" || echo "REBASE NEEDED"
```

The fetch is not optional: without it every comparison answers about a stale snapshot, and the answer is confidently wrong in exactly the case the check exists for.

- [ ] **Step 2: Reconcile the collected count**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  .venv/bin/python -m pytest --collect-only -q 2>/dev/null | grep :: | sort > /tmp/nodes-branch.txt && \
  wc -l /tmp/nodes-branch.txt /tmp/nodes-main.txt && \
  echo "--- removed ---" && comm -23 /tmp/nodes-main.txt /tmp/nodes-branch.txt | wc -l && \
  echo "--- added ---" && comm -13 /tmp/nodes-main.txt /tmp/nodes-branch.txt
```

Both node-id files must be non-empty. Expect a large `removed` count (the act's ~1095-line test file, minus what Task 1 moved) and three added (Task 4's new tests). Zero-added-zero-removed beside a non-zero count delta is a broken measurement, not a clean one.

- [ ] **Step 3: Full gate, then review**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && make check 2>&1 | tail -30
```

Then run `/code-review` **naming the pull request and the checkout path explicitly** — a forked review agent inherits the session's cwd and will otherwise review a different tree, returning plausible prose about code that is not yours.

- [ ] **Step 4: Open the pull request and merge it when green**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/stop-freshening && \
  git push -u origin stop-freshening && \
  gh pr create --title "Stop freshening Dependabot's branches (ADR-0045)" \
    --body "Implements docs/superpowers/specs/2026-09-20-dependabot-branch-freshening-design.md"
```

- [ ] **Step 5: Build the image, then deploy in the proven order**

No migration is needed. The order is still **build → swap → verify**; building deploys nothing.

Trigger `Release image` with the **full 40-character merge SHA as the workflow input**, not as `--ref`. Read the input's name from `.github/workflows/release-image.yml` rather than guessing it.

Then point Coolify at the new tag via `infraops` (`coolify_update_application` + `coolify_deploy`). **Record the outgoing tag before the write** — the derivable `sha-<full>` tag can be re-pointed by a later build, and the digest is the only immutable identity.

Expect a brief public outage: the orchestrator's Coolify health check is disabled, so the old container is dropped before the new one serves. This is normal and is not a symptom.

- [ ] **Step 6: Verify five things, not four**

```bash
# 1. Liveness and revision label
curl -s https://sds.alobar.net/health/live

# 2. Migration state -- unchanged by this release, and read anyway
#    (via docker exec on the VPS: alembic current vs alembic heads)

# 3. The estate answer NO LONGER carries the verdict
curl -s https://sds.alobar.net/openapi.json | python3 -c \
  "import sys,json; print(sorted(json.load(sys.stdin)['components']['schemas']['EstateLandingAdmissionResponse']['properties']))"

# 4. The inert answer STILL carries it
curl -s https://sds.alobar.net/openapi.json | python3 -c \
  "import sys,json; print(sorted(json.load(sys.stdin)['components']['schemas']['InertLandingAdmissionResponse']['properties']))"

# 5. The estate branch-update route is gone
curl -s https://sds.alobar.net/openapi.json | python3 -c \
  "import sys,json; p=json.load(sys.stdin)['paths']; print('/api/v1/estate-pr-branch-update' in p)"
```

Expected: (3) has no `branch_update_qualifies` and still has `rollout_base_matches_pin`; (4) still has `branch_update_qualifies`; (5) prints `False`.

Checks 3–5 are the ones that matter. Health, digest and revision label cannot see that a served surface changed — this estate lost two days to exactly that.

- [ ] **Step 7: Pull the main tree LAST**

The schedulers run the main tree's working copy, so nothing on this machine changes until this step.

```bash
cd /Users/devon/Projects/orchestrator && git pull && .venv/bin/python --version
```

No `uv sync` is needed: this change adds no `[project.scripts]` entry. Read the interpreter version back anyway — a sync triggered by anything else can relocate it under all eleven lanes.

- [ ] **Step 8: Watch one real pass**

The estate-landing lane runs on a schedule. After its next pass, confirm the report carries **no** `updated` or `would-update` lines and that its exit code is unchanged in meaning:

```bash
tail -60 ~/Library/Logs/sds-estate-landing.log
```

A bare hand-run of `scripts/run-estate-landing.sh` is a **dry run** — the plist passes `--submit` explicitly — so it proves nothing about the acting path.

- [ ] **Step 9: Tear down**

```bash
cd /Users/devon/Projects/orchestrator && \
  git -C /Users/devon/Projects/orchestrator status --porcelain && \
  git worktree remove .worktrees/stop-freshening --force && \
  git branch -D stop-freshening && \
  dropdb -h 127.0.0.1 -U postgres orchestrator_test_stopfresh
```

The main tree must show nothing this work created. Also tear down `.worktrees/rebase-design` and its branch once #286 is merged.
