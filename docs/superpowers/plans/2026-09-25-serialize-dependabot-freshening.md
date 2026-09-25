# The lane edits one Dependabot branch per repository at a time — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At both branch-update acts, never make a Dependabot-owned branch edited while another open Dependabot pull request in the same repository is edited and still queued to land; serve that fact on both admission answers; teach both landers to report a withheld sibling as `withheld` (not a finding) and to skip its update.

**Architecture:** One new service module, `src/orchestrator/services/branch_update_serialization.py`, owns the whole rule: commit classification, ownership (GitHub commits + the event log), the three refusal sets, and one outcome function parameterized by a *composer* callable that answers "what does this sibling's own admission say?". Both acts call it as a new conjunct after `branch_update_qualifies` and before the remote call; both admission routes call it to fill a new served field. Neither admission module imports it, so composing a sibling can never recurse. `qualifies_for_branch_update` and `freshness_derived_refusals` do **not** change (ADR-0024).

**Tech Stack:** Python per `.python-version`, FastAPI + pydantic response models, SQLAlchemy 2.x + Postgres, pytest, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-09-25-serialize-dependabot-freshening-design.md` (accepted, tip `95905f5`). It supersedes `docs/superpowers/specs/2026-09-20-dependabot-branch-freshening-design.md` and its plan; nothing from the `stop-freshening` branch is carried over (spec §14).

## Global Constraints

- **Every file:line here was read on `95905f5`** (`src/` identical to `origin/main` `6df1d09`). Every step also names the anchoring text, so a shifted file stays navigable. Read the code; line numbers move.
- **No migration.** The rule reads the existing `events` table (`persistence/models.py:634`, `occurred_at` server default, `payload` JSONB) and adds no table, column or index. Deploy is build → swap → verify. Confirm `alembic current == heads` anyway.
- **Do not touch `qualifies_for_branch_update` or `freshness_derived_refusals`** (`estate_landing_admission.py:486,437`). The new check is a separate conjunct at the two acts.
- **Neither admission module may import the new module.** The new module may import constants from both; the acts and the routes import the new module. This is the recursion guard the spec asks for (§5 "Where it lives").
- **Name every refusal code once.** Import `UPDATE_BOT_LOGIN` (`estate_landing_admission.py:79`), the `LANDING_*` constants and the `INERT_LANDING_*` constants; never re-spell a literal in `src/`.
- **Word guards.** Nothing under `src/orchestrator/` may contain the bare tokens `dispatch`, `deploy`, `merges`, `coolify`, nor the multi-token sequences in `FORBIDDEN_SEQUENCES` (`merge pull request`, `auto merge`, `production mutation`, …), including in docstrings, and `github.actions` as a substring. Check new prose with the guard's own functions (Task 2 Step 6), never with a grep.
- **Absolute `cd` in every Bash call, inside backgrounded ones too.** Read pytest's `rootdir:` line beside the collected count — `-q` hides it.
- **Mutation runs:** `PYTHONDONTWRITEBYTECODE=1`, tree committed first, restore with `/usr/bin/git checkout HEAD -- <path>` and assert the restore, assert each anchor matches **exactly once**, read the kills (not the count), report every survivor. Write the harness into this worktree, not the shared scratchpad. Kill the pytest process *group* if you abort.
- **Clean-tree claims use `/usr/bin/git`.**
- **Never run two pytest suites against one test database.**

---

## Prerequisites (not a task)

This worktree exists (`.worktrees/serialize-freshening`, branch `serialize-freshening`) but has **no `.venv`** — measured while writing this plan.

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && uv venv --clear && uv sync --frozen && .venv/bin/python --version
```

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && createdb -h 127.0.0.1 -U postgres orchestrator_test_serialize
```

In every later shell (separate `export`s — `export A=x B="$A"` expands `$A` to its prior value):

```bash
export TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/orchestrator_test_serialize
export ORCHESTRATOR_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/orchestrator_test_serialize
export SECURITY_STANDARDS_DIR=/Users/devon/Projects/orchestrator/.worktrees/serialize-freshening/tests/fixtures/security-standards
```

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && .venv/bin/alembic upgrade head
```

Baseline node ids, taken from a **fetched** `origin/main` tree (this branch's `src/` and `tests/` equal it until Task 1):

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && git fetch origin && \
  git diff --stat origin/main -- src tests | tail -1 ; \
  .venv/bin/python -m pytest --collect-only -q 2>/dev/null | grep '::' | sort > /tmp/serialize-nodes-main.txt && \
  wc -l /tmp/serialize-nodes-main.txt
```

The `git diff --stat` must print nothing (no `src`/`tests` difference). The node-id file must be non-empty; this repo's pytest config carries no `addopts = "-q"`, so one `-q` prints node ids.

---

## Corrections against the spec

The code wins in each case. Carry all of these into ADR-0045 (Task 11).

1. **§11 "Build hazards": `test_cross_boundary_vocabulary` — the spec says the holding set and the read-failure set "must be registered in `VOCABULARY_REGISTRY`".** The scanner only discovers collections whose every element is an `ast.Constant` string (`tests/architecture/test_cross_boundary_vocabulary.py:150-165`, `_is_string_collection`). House style builds refusal sets from imported **names** (`DELIBERATE_REFUSALS = frozenset({LANDING_PACE_EXHAUSTED, LANDING_OUTSIDE_CHANGE_WINDOW})`, `estate_landing_admission.py:321`), which the scanner does not see; CLAUDE.md lists derived/union collections as structural exclusions "do NOT try to register". So: **build both sets from the imported constants and do not register them.** What pins the vocabulary instead is the completeness test (spec test 11, Task 3). A literal-string spelling that the scanner *would* discover is the worse option: it is a second spelling of 25 codes.
2. **§5 "What it reads": "Both gateways gain two read methods … on `EstateReadGateway`. Every fake gateway must implement them."** `EstateReadGateway` (`estate_landing_admission.py:402`) is also the base of the unit-bound landing path's gateways, whose fakes (`tests/services/test_pr_merge.py:54`, `tests/api/test_pr_merge_api.py:37`) would have to grow methods nothing calls. **Instead a new `SiblingReadGateway(EstateReadGateway, Protocol)`** lives in `estate_landing_admission.py` beside `EstateReadGateway`, `EstatePullRequest` and `HeadCheckRun` (with the two new read shapes, `OpenPullRequest` and `PullRequestCommit`), and is the base of `EstateBranchUpdateGateway` (`estate_pr_branch_update.py:116`) and `InertBranchUpdateGateway` (`inert_pr_branch_update.py:102`). **They may NOT live in the new module: that would be an import cycle** — `estate_pr_merge` would import `branch_update_serialization`, which imports `INERT_LANDING_POLICY_SOURCE_*` from `inert_landing_admission`, which imports `MERGE_COMMIT, SQUASH` from `estate_pr_merge` (`inert_landing_admission.py`, the `estate_pr_merge` import) → `ImportError` on a partially initialised module. `estate_landing_admission` is already every party's shared import, so the shapes go there; the recursion guard (no admission module imports the new module) is untouched. The real methods are written once on `GitHubEstatePullRequests` (`estate_pr_merge.py:471`); `GitHubInertPullRequests` (`inert_pr_merge.py:149`) inherits them and overrides nothing. `FakeEstateGateway` (`tests/services/estate_landing_doubles.py:158`) implements them with defaults, so `ActingInertGateway` and every act fixture inherit them.
3. **§9 "The mechanism": the field is served "the way `rollout_base_matches_pin` is served".** It cannot be computed the same way — `rollout_base_matches_pin` is computed *inside* `estate_landing_admission`, and the spec forbids the scan from living there. Three existing pins assert that the served body's keys equal the admission **dataclass** fields (`tests/services/test_estate_pr_branch_update.py:885-899`; `tests/api/test_estate_landing_admission_api.py::test_the_body_carries_every_field_of_the_composed_answer`; `tests/api/test_inert_landing_admission_api.py:35-40`), and the lander mirror-key pins are against the dataclass (`test_estate_pr_branch_update.py:445-464`). So **the field is added to both dataclasses with NO default**; each admission function sets it to `False` explicitly at its single constructor (`estate_landing_admission.py:684`, `inert_landing_admission.py:229`), and the **route** fills it with `dataclasses.replace(...)`. The act never reads that field (it calls the rule itself). No default, so a new constructor cannot forget it silently.
4. **§5 "Definitions": arm (b) is compared with "the reading transaction's clock".** Neither `inert_landing_admission` nor `update_inert_pull_request_branch` (`inert_pr_branch_update.py:120-129`) takes a clock. The new function takes `clock: Clock | None = None` (default `TransactionClock()`), the estate act passes its existing `clock`, and **the inert act gains `clock: Clock | None = None`** purely so the bound is testable. The routes pass nothing.
5. **§5 "What it reads": "paginated to the end: normally 1 call."** `GitHubEstatePullRequests._get` (`estate_pr_merge.py:494`) discards response headers and turns a 404 into `None`. Pagination needs a new page loop, and a 404 on a list read must raise, never read as empty. Separately, **`GET /repos/{r}/pulls/{n}/commits` returns at most 250 commits** in total whatever the paging (GitHub REST docs); a list that reaches 250 is not known to be complete and must raise (treated as unread). Dependabot branches carry 1–3 commits, so this never fires in practice; the guard exists so a truncated read is an unread one (spec §5 "a truncated read is an unread one").
6. **§5 "Definitions": arm (b) matches "`repository` and `pr_number` in the payload".** Both acts already record `{"repository", "pr_number", "head_sha"}` (`estate_pr_branch_update.py:316-320`, `inert_pr_branch_update.py:286-290`), and both action strings must be visible to the new module. The act modules import the new module, so the new module cannot import them back. **The two action strings move into the new module** (`BRANCH_UPDATE_ACTION`, `INERT_BRANCH_UPDATE_ACTION`) and each act re-imports its own under the same name, so existing test imports keep working.
7. **§6 "Holding": `landing_rollout_moved` is holding only when it is freshness-derived** (base carries the pinned bytes). The completeness test needs three disjoint sets, so it places `landing_rollout_moved` and `landing_head_not_current_with_base` in the **holding** set as the freshness criterion's vocabulary, and a separate test proves that a genuinely moved rollout (base does not carry the pin) makes a sibling **release**. Stated here so the test does not read as claiming the code is always holding.

## Spec ambiguities this plan resolves

1. **The target is absent from the open-pull-request list** (a race, or GitHub lag). The spec says the target's author comes from the list. Absent ⇒ its author and head are unknown ⇒ **outcome 3** (`…_siblings_unreadable`). Not outcome 4: releasing would decide a Dependabot branch's ownership question by default.
2. **Order of outcomes 2 and 3 when both apply** (one sibling positively holds and another sibling's read failed). The spec's order is 1, 2, 3, 4; the plan evaluates every sibling (no short-circuit on the first failure), then decides in that order. So a positively observed holding sibling with an owned target is outcome 2 even if some other read failed. The served fact is therefore true exactly on outcome 2, as §9 requires.
3. **A read failure of the TARGET's own commits** is a read failure (outcome 3 bullet 1), not an unestablished-by-content target. The plan separates the two: `read_failed` flips on any failed/truncated page anywhere; an unclassified commit makes the target `UNESTABLISHED` without flipping it.
4. **An edited sibling with an empty refusal list** (its own admission is satisfied — it would land). "Every refusal is in the holding set" is vacuously true ⇒ it **holds**. That is the intended reading: it is the branch queued to land next.
5. **A composer that raises.** The spec says "If composing a sibling's admission raises rather than answering, the act reaches outcome 3." The plan catches `Exception` around the composer **except `SQLAlchemyError`**, which is re-raised: a broken transaction must not be converted into a verdict (the act's `except Exception: session.rollback(); raise` then handles it).
6. **Where the conjunct sits in the act.** After `branch_update_qualifies` (`:213`/`:187`), after the head-identified and head-moved checks (`:222-237`/`:196-208`), and immediately before `gateway.update_branch(` (`:240`/`:211`). The head-moved refusal is self-clearing and costs nothing; the scan costs ~12 reads, so it goes last among the refusals and first before the write.
7. **`withheld` when the key is `True` but no freshness refusal is present.** The orchestrator cannot produce it (the field requires `branch_update_qualifies`, which requires being behind). Each lander returns `withheld` only when a freshness-derived refusal was actually subtracted; otherwise it falls through to the existing answer (`deliberate` in the estate lander, `held` in the inert one). This keeps "a key alone never quiets a line" true.

## Deliberately NOT changed (state it, do not "fix" it)

- A sibling refusing a positive code the spec does not list (`landing_record_not_approved`, `landing_record_absent`, `landing_checks_not_clean`, `landing_pull_request_conflicted`, …) **releases**. That is the accepted pre-change polarity (spec §6 "Releasing"). Do not widen the holding set.
- Existing "qualifies → acts" fixtures keep passing because `FakeEstateGateway`'s new defaults return an open list holding **only the target**, positively owned (one Dependabot commit at the target's head). That is exactly the spec's test 12 hygiene — no edited sibling, readable commits — made the default. It is **not** "the scan skipped": the target is positively owned and there are no siblings, so outcome 4 is reached honestly. Do not rewrite those fixtures.
- The route inventories, the idempotency matrix and both lander isolation tests' route allowlists do **not** move: no route is added or removed. If a builder finds they must move, a route changed and the plan was wrong — stop and say so.

## File Structure

**Created**

| Path | Responsibility |
|---|---|
| `src/orchestrator/services/branch_update_serialization.py` | The rule: the moved action strings, `HOLDING_REFUSALS`, `READ_FAILURE_REFUSALS`, `SiblingAnswer`, `SiblingOutcome`, `branch_update_sibling_outcome(...)`, `withheld_for_sibling(...)`. |
| `tests/services/test_branch_update_serialization.py` | Classification, ownership, arm (b), the three sets, the outcome function (spec tests 1–7, 9, 11). |
| `docs/decisions/0045-the-lane-edits-one-dependabot-branch-per-repository.md` | ADR. |

**Modified**

| Path | Change |
|---|---|
| `src/orchestrator/services/estate_pr_merge.py` | `GitHubEstatePullRequests` gains `open_pull_requests`, `pull_request_commits`, a private page loop; its class docstring's "Five calls" becomes seven. |
| `src/orchestrator/services/estate_pr_branch_update.py` | Gateway protocol base → `SiblingReadGateway`; action string imported; two new codes; the conjunct. |
| `src/orchestrator/services/inert_pr_branch_update.py` | Same, plus a `clock` parameter. |
| `src/orchestrator/services/estate_landing_admission.py` | `OpenPullRequest`, `PullRequestCommit`, `SiblingReadGateway` beside `EstateReadGateway` (`:402`) — Correction 2; and `EstateLandingAdmission.branch_update_withheld_for_sibling: bool`, set `False` at `:684`. |
| `src/orchestrator/services/inert_landing_admission.py` | `InertLandingAdmission.branch_update_withheld_for_sibling: bool`, set `False` at `:229`. |
| `src/orchestrator/api/schemas.py` | Both admission response models (`:637`, `:755`) gain the field and its docstring. |
| `src/orchestrator/api/routes.py` | Both admission routes (`:773`, `:873`) fill the field. |
| `src/estate_lander/cli.py`, `src/inert_lander/cli.py` | `_WITHHELD_FOR_SIBLING`, `withheld` status, classifier, `_branch_updates` gate, `_UPDATE_SELF_CLEARING`. |
| `tests/services/estate_landing_doubles.py` | `FakeEstateGateway` gains the two reads with owned-target defaults; new `SiblingGateway` multi-PR double. |
| `tests/services/test_estate_pr_branch_update.py`, `tests/services/test_inert_pr_branch_update.py` | Act conjunct tests, self-clearing equality pins, mirror-key pins, gateway HTTP tests. |
| `tests/api/test_estate_landing_admission_api.py`, `tests/api/test_inert_landing_admission_api.py` | Field present; a monkeypatched `True`. |
| `tests/estate_lander/test_estate_lander.py`, `tests/inert_lander/test_inert_lander.py` | `withheld` classification, update-pass gate, `_REPORTED` pin. |
| `CLAUDE.md` | Corrections below `<!-- code-standards:end -->` (`:41`). |

---

### Task 1: The two paginated GitHub reads and their fakes

**Files:**
- Modify: `src/orchestrator/services/estate_landing_admission.py` (two dataclasses + `SiblingReadGateway`, after `EstateReadGateway` at `:402-411`)
- Create: `src/orchestrator/services/branch_update_serialization.py` (skeleton: the two moved action strings only)
- Modify: `src/orchestrator/services/estate_pr_merge.py` (after `head_check_runs`, `:557-597`; class docstring `:472`)
- Modify: `tests/services/estate_landing_doubles.py` (`FakeEstateGateway`, `:158`)
- Test: `tests/services/test_estate_pr_branch_update.py` (append beside the existing monkeypatched-httpx gateway tests at `:927-998`)

**Interfaces produced:**

```python
# branch_update_serialization.py
BRANCH_UPDATE_ACTION: Final = "estate_pr_branch_update.updated"      # moved, value unchanged
INERT_BRANCH_UPDATE_ACTION: Final = "inert_pr_branch_update.updated" # moved, value unchanged

# estate_landing_admission.py (Correction 2 -- NOT the new module; see the import-cycle note)
@dataclass(frozen=True)
class OpenPullRequest:
    number: int
    head_sha: str
    author_login: str
    author_is_bot: bool

@dataclass(frozen=True)
class PullRequestCommit:
    sha: str
    author_login: str | None      # the LINKED GitHub account (top-level `author.login`), None when unlinked
    committer_login: str | None   # top-level `committer.login`, None when unlinked
    verified: bool                # `commit.verification.verified is True`

class SiblingReadGateway(EstateReadGateway, Protocol):
    def open_pull_requests(self, *, repository: str) -> tuple[OpenPullRequest, ...]: ...
    def pull_request_commits(self, *, repository: str, number: int) -> tuple[PullRequestCommit, ...]: ...
```

- [ ] **Step 1: Write the failing gateway tests** (monkeypatch `httpx.get` as the existing `:927` tests do):
  - `test_the_open_list_follows_every_page` — page 1 returns 100 rows, page 2 returns 3; result has 103 and the requested URLs carry `state=open`, `per_page=100`, `page=1`, `page=2`.
  - `test_a_failing_LATER_page_of_the_open_list_raises` — page 2 answers 500 → `EstateGatewayError`, never a 100-row result.
  - `test_a_404_on_the_open_list_raises_rather_than_reading_empty`.
  - `test_the_open_list_reads_author_type_and_head` — `user.type == "Bot"` → `author_is_bot`; `head.sha`; a row missing `number` or `head.sha` raises `EstateGatewayError("open_pulls_response_invalid")`.
  - `test_commits_follow_every_page_and_keep_order` — two pages, order preserved (last element is the newest).
  - `test_a_failing_later_page_of_the_commits_raises`.
  - `test_a_commit_list_that_reaches_the_platform_cap_is_not_believed_complete` — 250 rows across three pages → `EstateGatewayError("commits_list_truncated")`.
  - `test_an_unlinked_author_or_committer_reads_as_None` and `test_verification_is_true_only_when_the_platform_says_true` (`verified: "true"` string → `False`).
  - `test_a_token_never_appears_in_a_gateway_error` — reuse the `:966` shape.
- [ ] **Step 2: Run, watch them fail** (`AttributeError`/`ImportError`):

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && .venv/bin/python -m pytest tests/services/test_estate_pr_branch_update.py -k "open_list or commits or commit_list or unlinked or verification" -x 2>&1 | tail -15
```

- [ ] **Step 3: Implement.** In `estate_pr_merge.py`: a private `_get_pages(self, url: str, *, cap: int | None) -> list[Any]` that appends `per_page=100&page=N`, calls `httpx.get` with the same exception tuple as `_get` (`httpx.RequestError, httpx.InvalidURL, ValueError`), raises `EstateGatewayError("list_status", status)` on **any** non-200 including 404, stops on a page shorter than 100, and raises `EstateGatewayError("list_pagination_exceeded")` after 20 pages. Then `open_pull_requests` (`/repos/{r}/pulls?state=open`) and `pull_request_commits` (`/repos/{r}/pulls/{n}/commits`, raising `commits_list_truncated` at ≥250 rows). Parse defensively in the style of `_pull_from_body` (`:690`): every unrecognised shape raises. Import the dataclasses from `estate_landing_admission` (never from the new module — Correction 2's cycle). Update the class docstring at `:472` ("Five calls, and two of them change anything") to seven.
- [ ] **Step 4: Move the two action strings.** Define them in the new module; in `estate_pr_branch_update.py:80` and `inert_pr_branch_update.py:67` replace the definitions with `from orchestrator.services.branch_update_serialization import BRANCH_UPDATE_ACTION` / `INERT_BRANCH_UPDATE_ACTION`. Tests that import them from the act modules keep working. Change both act gateway protocols' base from `EstateReadGateway` to `SiblingReadGateway` (imported from `estate_landing_admission`). Then prove there is no cycle from every entry point: `.venv/bin/python -c "import orchestrator.services.estate_pr_merge; import orchestrator.services.inert_landing_admission; import orchestrator.services.branch_update_serialization; import orchestrator.api.routes"` in a fresh interpreter each, all four orders.
- [ ] **Step 5: Fakes.** `FakeEstateGateway.__init__` gains `open_pulls: tuple[OpenPullRequest, ...] | None = None`, `commits: dict[int, tuple[PullRequestCommit, ...]] | None = None`, `open_error`, `commits_error: dict[int, EstateGatewayError] | None = None`, and records `self.open_reads`, `self.commit_reads`. **Defaults:** `open_pulls=None` → `(OpenPullRequest(self._pull.number, self._pull.head_sha, self._pull.author_login, self._pull.author_is_bot),)`; a number absent from `commits` → `(dependabot_commit(sha=<that PR's head>),)`. Add module helpers `dependabot_commit(sha)`, `foreign_commit(sha, author="alobar-sds-dispatch[bot]")`, `open_pull(number, head_sha, author="dependabot[bot]", is_bot=True)`. Docstring states that the default is the spec's test-12 hygiene: the target alone, positively owned.
- [ ] **Step 6: Green:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && .venv/bin/python -m pytest tests/services/ -q -x 2>&1 | tail -5 && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests && .venv/bin/pyright src/orchestrator/services 2>&1 | tail -3
```

- [ ] **Step 7: Commit**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && git add -A && git commit -m "feat(branch-update): paginated reads of open pull requests and their commits

Two reads the sibling rule needs, on a narrower protocol than the shared read
gateway so the unit-bound landing path's fakes grow nothing. A 404 or a failed
later page raises; a commit list at the platform's 250 cap is not believed
complete. The branch-update action strings move beside the reader that will
match them.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01791bvRbFznUERbugr9aoy3"
```

---

### Task 2: Commit classification, ownership, and arm (b)

**Files:**
- Modify: `src/orchestrator/services/branch_update_serialization.py`
- Create: `tests/services/test_branch_update_serialization.py`

**Interfaces produced (private helpers are tested directly, as `_held_status` is):**

```python
DEPENDABOT_COMMITTER: Final = "web-flow"
EDIT_EVENT_BOUND: Final = timedelta(minutes=10)

class Ownership(Enum): OWNED, EDITED, UNESTABLISHED

def _commit_class(commit: PullRequestCommit) -> Literal["dependabot", "foreign", "unclassified"]
def _ownership(commits, *, listed_head: str, edited_by_event: bool) -> Ownership
def _recently_updated_heads(session, repository: str, numbers: Iterable[int], now: datetime) -> dict[int, set[str]]
```

Rules, exactly as spec §5:
- `dependabot`: `author_login == UPDATE_BOT_LOGIN` **and** `committer_login == DEPENDABOT_COMMITTER` **and** `verified`.
- `foreign`: `author_login` present and `!= UPDATE_BOT_LOGIN`, **or** `committer_login` present and `!= DEPENDABOT_COMMITTER`.
- `unclassified`: anything else.
- `EDITED` if any commit is `foreign` or `edited_by_event`; `OWNED` if every commit is `dependabot`, the list is non-empty, `commits[-1].sha == listed_head`, and not `edited_by_event`; otherwise `UNESTABLISHED`.
- `_recently_updated_heads`: one query — `select(Event.payload).where(Event.action.in_((BRANCH_UPDATE_ACTION, INERT_BRANCH_UPDATE_ACTION)), Event.occurred_at > now - EDIT_EVENT_BOUND, Event.payload["repository"].astext == repository)`, then keep rows whose `pr_number` (an `int`, not a `bool`) is in `numbers`, mapping number → set of recorded `head_sha`. Arm (b) holds for a PR when its **current listed head** is in that set. A query failure (`SQLAlchemyError`) propagates — the caller's transaction is broken and must not become a verdict.

- [ ] **Step 1: Failing tests** (`tests/services/test_branch_update_serialization.py`):
  - Classification, one row per case (spec test 4): `AlobarQuest` author → foreign; `dependabot[bot]` author + non-`web-flow` committer → foreign; `dependabot[bot]` + `web-flow` + verified → dependabot; no linked author → unclassified; no linked committer → unclassified; `web-flow` unverified → unclassified; App author `alobar-sds-dispatch[bot]` + `web-flow` + verified → **foreign** (the author, not the committer, separates Dependabot from the App — spec §16 item 5).
  - Ownership: all-Dependabot with last sha == head → OWNED; same with last sha ≠ head → UNESTABLISHED (spec test 5); one foreign commit → EDITED even if last sha ≠ head; empty list → UNESTABLISHED; `edited_by_event=True` over all-Dependabot → EDITED.
  - Arm (b) against a migrated database (`migrated_session`), events inserted with explicit `occurred_at` (spec test 3): event at `now - 9m59s` with `head_sha == listed head` → edited; same event with a **different** `head_sha` (recreate moved the head) → owned; event at `now - 10m01s` → owned (**the pair either side of the bound**); an event for another PR number → owned; an `inert_pr_branch_update.updated` event counts in the estate scan and vice versa; an event for another repository → owned.
- [ ] **Step 2: Run, watch them fail:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && .venv/bin/python -m pytest tests/services/test_branch_update_serialization.py -x 2>&1 | tail -15
```

- [ ] **Step 3: Implement** the three helpers with docstrings carrying the spec's *why* (committer catches a local rewrite; signature closes the `noreply@github.com` spoof; the bound's derivation — ~12 s delivery, 3–4 s between sibling acts, hourly passes; the event's recorded head is the pre-update head, so it retires the moment the head moves).
- [ ] **Step 4: Green** (same command as Step 2, then `ruff check`, `ruff format --check`, `pyright` on the module).
- [ ] **Step 5: Word-guard check of the new module's prose:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && .venv/bin/python -c "
import sys; sys.path.insert(0, 'tests/architecture')
from test_ws32_scope_guards import _tokenize, _contains_sequence, FORBIDDEN_SEQUENCES
t = _tokenize(open('src/orchestrator/services/branch_update_serialization.py').read())
bad = [label for label, seq in FORBIDDEN_SEQUENCES if _contains_sequence(t, seq)]
print('WOULD RED:', bad) if bad else print('clean')
"
```

Also `grep -n 'merges\|github\.actions' src/orchestrator/services/branch_update_serialization.py` must print nothing (ws33 whole-token `merges`; ws34 substring).

- [ ] **Step 6: Commit**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && git add -A && git commit -m "feat(branch-update): classify who owns a Dependabot branch

A branch is edited when it carries a foreign commit, or when this estate
updated it at its current head less than ten minutes ago (the 202 race). It is
owned only when every commit is Dependabot's, the list is complete and its last
sha is the head the list call named. Anything else is unestablished.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01791bvRbFznUERbugr9aoy3"
```

---

### Task 3: The three refusal sets and the completeness guard

**Files:**
- Modify: `src/orchestrator/services/branch_update_serialization.py`
- Modify: `tests/services/test_branch_update_serialization.py`

**Interfaces produced:**

```python
# Built from IMPORTED constants (Correction 1) -- no string literal is spelled here.
_HOLDING_BESIDE_THE_CRITERION: Final = frozenset({
    LANDING_CHECKS_IN_FLIGHT, LANDING_CHECKS_AWAITING_VERDICT, LANDING_MERGEABILITY_UNKNOWN,
})
READ_FAILURE_REFUSALS: Final = frozenset({ ...the 18 codes of spec §6, by constant name... })

@dataclass(frozen=True)
class SiblingAnswer:
    refusals: tuple[str, ...]
    rollout_base_matches_pin: bool     # always False from the inert composer

def _answer_class(answer: SiblingAnswer) -> Literal["holding", "releasing", "unreadable"]
```

`_answer_class`: any member of `READ_FAILURE_REFUSALS` → `unreadable` (checked first, so a read-failure code beside a releasing one is still unreadable — spec test 6); else if every refusal is in `DELIBERATE_REFUSALS | freshness_derived_refusals(refusals, rollout_base_matches_pin=…) | _HOLDING_BESIDE_THE_CRITERION` → `holding` (vacuously for an empty list — Ambiguity 4); else `releasing`. **Reuse `freshness_derived_refusals` and `DELIBERATE_REFUSALS`; do not restate them.**

The 18 read-failure constants, and where each is defined: estate module — `LANDING_PULL_REQUEST_UNREADABLE`, `LANDING_CHECKS_VERDICT_UNREADABLE`, `LANDING_FRESHNESS_UNREADABLE`, `LANDING_ROLLOUT_UNREADABLE`, `LANDING_ECOSYSTEM_UNREADABLE`, `LANDING_POLICY_UNREADABLE`, `LANDING_CONDITIONS_UNREADABLE`, `LANDING_RECORD_SOURCE_UNREADABLE`, `LANDING_RECORD_SOURCE_UNCONFIGURED`, `LANDING_RECORD_AMBIGUOUS`, `LANDING_RECORD_UNIDENTIFIED`, `LANDING_ESTATE_SOURCE_UNREADABLE`, `LANDING_ESTATE_SOURCE_UNCONFIGURED`, `LANDING_ESTATE_UNKNOWN`, `LANDING_MERGEABILITY_UNRECOGNISED`, `LANDING_APP_CREDENTIALS_MISSING`; inert module — `INERT_LANDING_POLICY_SOURCE_UNREADABLE`, `INERT_LANDING_POLICY_SOURCE_UNCONFIGURED`.

- [ ] **Step 1: Failing tests:**
  - **Completeness (spec test 11).** Enumerate every module-level upper-case `str` constant in `estate_landing_admission` and `inert_landing_admission` whose **value** starts with `landing_` or `inert_landing_` (that predicate excludes `MERGEABLE_*`, `RUN_*`, `SEMVER_*`, `UPDATE_BOT_LOGIN`, `_BRANCH_PREFIX`). Measured on `95905f5`: **47** distinct values. Assert `holding ∪ READ_FAILURE_REFUSALS ∪ RELEASING == enumerated`, the three pairwise disjoint, where `holding = DELIBERATE_REFUSALS | {LANDING_HEAD_NOT_CURRENT_WITH_BASE, LANDING_ROLLOUT_MOVED} | _HOLDING_BESIDE_THE_CRITERION` (7 — Correction 7) and **`RELEASING` is a string-literal set in the test, spelled out** (22):

```python
RELEASING = {
    "inert_landing_author_not_permitted", "inert_landing_repository_not_declared",
    "inert_landing_rules_undeclared", "inert_landing_target_not_inert",
    "landing_already_recorded", "landing_author_not_the_update_bot",
    "landing_base_not_default_branch", "landing_change_window_not_declared",
    "landing_checks_not_clean", "landing_ecosystem_excluded", "landing_not_enabled",
    "landing_policy_version_superseded", "landing_pull_request_conflicted",
    "landing_pull_request_not_open", "landing_record_absent",
    "landing_record_has_live_objections", "landing_record_not_approved",
    "landing_record_not_policy_approved", "landing_rollout_unpinned",
    "landing_target_not_routed", "landing_update_type_not_permitted",
    "landing_update_type_unparseable",
}
```

  Also assert `len(enumerated) == 47` in the failure message's context only — the equality is the guard; the count is there so a reader of a red run sees what moved. The docstring states the runtime/test split of spec §6: at runtime releasing is the complement; the literal exists so a new code reds CI until classified.
  - **Holding gate, row by row (spec test 6, the classification half):** each of the 7 holding codes, alone and beside `landing_head_not_current_with_base`, → `holding` (for `landing_rollout_moved`, with `rollout_base_matches_pin=True`); `landing_rollout_moved` + behind with `rollout_base_matches_pin=False` → `releasing` (Correction 7); `landing_checks_not_clean`, `landing_pull_request_conflicted`, and a made-up positive code `landing_invented_condition` → `releasing`; each of the 18 read-failure codes alone **and** beside `landing_checks_not_clean` → `unreadable`; `()` → `holding`.
- [ ] **Step 2: Run, watch fail.** **Step 3: Implement.** **Step 4: Green** (as Task 2).
- [ ] **Step 5: Confirm the vocabulary scanner does not discover the new sets** (so no registry entry is needed, and an entry would red `test_every_registry_entry…` if the registry test checks discovery):

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && .venv/bin/python -m pytest tests/architecture/test_cross_boundary_vocabulary.py -q 2>&1 | tail -3
```

- [ ] **Step 6: Commit** — message `feat(branch-update): classify a sibling's refusals as holding, releasing or unreadable`, body naming the completeness guard and that a read failure now withholds (the first draft's polarity is gone). Trailer lines as above.

---

### Task 4: The outcome function

**Files:**
- Modify: `src/orchestrator/services/branch_update_serialization.py`
- Modify: `tests/services/estate_landing_doubles.py` (new `SiblingGateway`)
- Modify: `tests/services/test_branch_update_serialization.py`

**Interfaces produced:**

```python
class SiblingOutcome(Enum):
    RELEASE_TARGET_EDITED = "release_target_edited"        # outcome 1
    WITHHOLD_SIBLING_HOLDING = "withhold_sibling_holding"  # outcome 2
    WITHHOLD_SIBLINGS_UNREADABLE = "withhold_siblings_unreadable"  # outcome 3
    RELEASE = "release"                                    # outcome 4

def branch_update_sibling_outcome(
    session: Session, *, repository: str, target_number: int,
    gateway: SiblingReadGateway, compose: Callable[[int], SiblingAnswer],
    clock: Clock | None = None,
) -> SiblingOutcome

def withheld_for_sibling(*, qualifies: bool, outcome: Callable[[], SiblingOutcome]) -> bool
    # False without calling `outcome` when not qualifies; True only for WITHHOLD_SIBLING_HOLDING;
    # any exception from `outcome` other than SQLAlchemyError -> False (the admission read must
    # still answer, spec §9).
```

Algorithm (order is the spec's; see Ambiguities 1–3, 5):
1. `repository = repository.lower()`; `now = (clock or TransactionClock()).now(session)`.
2. `open_pull_requests` — `EstateGatewayError` → `WITHHOLD_SIBLINGS_UNREADABLE`. Find the target by number; absent → `WITHHOLD_SIBLINGS_UNREADABLE`.
3. Target not `author_login == UPDATE_BOT_LOGIN and author_is_bot` → `RELEASE` (a sync-bot or other author is never affected — spec §7).
4. `dependabot_prs` = every open PR with that author test. `events = _recently_updated_heads(session, repository, [p.number for p in dependabot_prs], now)`.
5. Target commits: `EstateGatewayError` → `read_failed = True`, target `UNESTABLISHED`; else `_ownership(...)`. Target `EDITED` → `RELEASE_TARGET_EDITED`.
6. For each other Dependabot PR: commits (`EstateGatewayError` → `read_failed = True`, continue); `OWNED` → continue; else `compose(number)` (raise, except `SQLAlchemyError` → `read_failed = True`, continue); `_answer_class`: `unreadable` → `read_failed = True`; `holding` → `holding_seen = True` if the sibling is `EDITED`, else (`UNESTABLISHED`) `read_failed = True`; `releasing` → nothing.
7. `holding_seen and target is OWNED` → `WITHHOLD_SIBLING_HOLDING`; `holding_seen or read_failed` → `WITHHOLD_SIBLINGS_UNREADABLE`; else `RELEASE`.

The docstring must say why it lives outside both admission modules, and that the composer is how "which lane's admission" is parameterized.

`SiblingGateway(FakeEstateGateway)` in the doubles: `pulls: dict[int, EstatePullRequest]`, `behind: dict[int, int]`, `runs: dict[int, tuple[HeadCheckRun, ...]]`; `read_pull_request`, `commits_behind_base`, `head_check_runs` answer per number (by head sha → number lookup for the last two). Used by the act-level tests in Task 5; Task 4's unit tests pass a **stub composer** (`lambda n: answers[n]`) and need only the default `FakeEstateGateway` plus `open_pulls`/`commits`.

- [ ] **Step 1: Failing tests** (spec tests 1, 2, 4-polarity, 5, 6-outcome-half, 7, 9):
  - **THE DISCRIMINATING CONTROL (spec test 1)** — `test_an_owned_target_beside_an_edited_HOLDING_sibling_is_withheld_and_beside_an_owned_one_is_released`: open = target #10 (owned) + sibling #11; sibling carries a foreign commit and its composed answer is `(landing_pace_exhausted, landing_head_not_current_with_base)` → `WITHHOLD_SIBLING_HOLDING`; the **same fixture with #11 owned** → `RELEASE` and the composer is never called. This is the pair that fails if the rule is deleted.
  - **Day-one guarantee (spec test 2):** target carries a foreign commit, holding sibling present → `RELEASE_TARGET_EDITED`; composer never called; target edited only by a fresh event (arm b) → same.
  - **Polarity controls (spec test 4):** target with an unclassified commit beside a holding edited sibling → `WITHHOLD_SIBLINGS_UNREADABLE` (not 1, not 2); sibling with an unclassified commit whose answer is holding-only → `WITHHOLD_SIBLINGS_UNREADABLE` (not 4).
  - **Last sha (spec test 5):** target whose commit list ends on a sha ≠ listed head, beside a holding sibling → outcome 3; sibling likewise with a holding-only answer → outcome 3.
  - **Holding gate at the outcome level (spec test 6):** for each holding code, an edited sibling → outcome 2; for `landing_checks_not_clean`, `landing_pull_request_conflicted`, `landing_invented_condition` → outcome 4; for each read-failure code, including beside `landing_checks_not_clean` → outcome 3; a composer that raises `DomainError` → outcome 3; a composer that raises `OperationalError` → **propagates**.
  - **Read failures (spec test 7):** open-list error, target-commits error, sibling-commits error (each as `EstateGatewayError`, which is what a failed later page raises in Task 1) → outcome 3. An event-query failure propagates as `SQLAlchemyError` — assert it is raised, not converted (Ambiguity 5; the act turns it into a rollback).
  - **Order (Ambiguity 2):** one holding edited sibling + one sibling whose commits fail, target owned → outcome 2.
  - **Target absent (Ambiguity 1):** open list lacks the target → outcome 3.
  - **Second author (spec test 9):** target authored by `octo-upstream-sync[bot]` beside a holding Dependabot sibling → `RELEASE`, no commit read of the target; a sync-bot sibling carrying a foreign commit and a holding answer → not a sibling at all, target owned → `RELEASE`; composer never called for it.
  - **`withheld_for_sibling`:** `qualifies=False` → `False` and `outcome` not called; each of the four outcomes → `True` only for outcome 2; `outcome` raising `DomainError` → `False`.
- [ ] **Step 2: Run, watch fail.** **Step 3: Implement.** **Step 4: Green.** **Step 5: Word-guard check** (Task 2 Step 5).
- [ ] **Step 6: Commit** — `feat(branch-update): decide whether a sibling holds the repository`, trailer lines.

---

### Task 5: Wire both acts, and both landers' self-clearing sets (one commit — the equality pins span both)

**Files:**
- Modify: `src/orchestrator/services/estate_pr_branch_update.py` (`:83-94` codes; conjunct between `:237` and `:239`)
- Modify: `src/orchestrator/services/inert_pr_branch_update.py` (`:70-81` codes; `clock` param at `:120-129`/`:152-161`; conjunct between `:208` and `:210`)
- Modify: `src/estate_lander/cli.py:168-170`, `src/inert_lander/cli.py:153-155` (`_UPDATE_SELF_CLEARING`)
- Test: `tests/services/test_estate_pr_branch_update.py`, `tests/services/test_inert_pr_branch_update.py`

**Codes (spelled so neither contains the other — CLAUDE.md substring rule):**

```python
BRANCH_UPDATE_SIBLING_HOLDING: Final = "estate_branch_update_sibling_holding"
BRANCH_UPDATE_SIBLINGS_UNREADABLE: Final = "estate_branch_update_siblings_unreadable"
INERT_BRANCH_UPDATE_SIBLING_HOLDING: Final = "inert_branch_update_sibling_holding"
INERT_BRANCH_UPDATE_SIBLINGS_UNREADABLE: Final = "inert_branch_update_siblings_unreadable"
```

**The conjunct** (estate; inert is identical with its own admission and codes):

```python
outcome = branch_update_sibling_outcome(
    session, repository=admission.repository, target_number=admission.pr_number,
    gateway=gateway,
    compose=lambda number: _sibling_answer(estate_landing_admission(
        session, admission.repository, number, landing_source, record_source, gateway,
        enabled=enabled, credentials_configured=credentials_configured, clock=clock)),
    clock=clock,
)
if outcome is SiblingOutcome.WITHHOLD_SIBLING_HOLDING:
    raise DomainError(BRANCH_UPDATE_SIBLING_HOLDING, "...a sibling edited by this lane is queued to land...",
                      "the sibling ahead of it lands first; the next pass asks again")
if outcome is SiblingOutcome.WITHHOLD_SIBLINGS_UNREADABLE:
    raise DomainError(BRANCH_UPDATE_SIBLINGS_UNREADABLE, "...could not establish that no other edited branch is queued...",
                      "read the open pull requests and their commits; nothing was changed")
```

`_sibling_answer` is a private one-liner per act (`SiblingAnswer(admission.refusals, admission.rollout_base_matches_pin)`; inert passes `False`). Both refusals raise **before** `gateway.update_branch` and write nothing. The lander self-clearing sets become three members each: add `…_sibling_holding` only (spec §9).

- [ ] **Step 1: Failing tests** — act level, with `SiblingGateway` and, for estate, `FakeChangeRecordSource` answers registered for **both** `(REPOSITORY, target)` and `(REPOSITORY, sibling)`:
  - estate + inert: `test_an_owned_branch_beside_an_edited_sibling_that_holds_is_never_touched` — sibling behind with a foreign commit; asserts `DomainError.code == …_SIBLING_HOLDING` **and** `gateway.branch_updates == []` **and** no `Event` row written (read through a second `Session(engine)`).
  - the same fixture with the sibling owned → updated (the pair; this is where the "act trusts the served field" mutant dies, because the act must compute it itself).
  - `test_an_already_edited_branch_is_freshened_again_beside_a_holding_sibling` (spec test 2 at the act).
  - `test_an_unreadable_scan_refuses_with_its_own_code_and_touches_nothing` — `open_error` set → `…_SIBLINGS_UNREADABLE`, `branch_updates == []`.
  - inert: `test_a_sync_bot_branch_is_never_withheld` (target `octo-upstream-sync[bot]`, holding Dependabot sibling) → updated.
  - inert: `test_the_ten_minute_bound_reads_the_injected_clock` — an event written 11 minutes before the `FixedClock` → sibling owned → updated; 9 minutes → withheld.
  - Equality pins, replacing the existing ones at `test_estate_pr_branch_update.py:1080-1095` and `test_inert_pr_branch_update.py:342-363`: `_UPDATE_SELF_CLEARING == {HEAD_MOVED, NOT_QUALIFIED, SIBLING_HOLDING}`; and new `test_an_UNREADABLE_scan_is_NOT_self_clearing` mirroring `test_the_remote_REFUSING_is_NOT_one_of_them` (`:357`) for both landers.
  - `test_neither_new_code_contains_the_other` over all four codes.
- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement** the codes, the conjunct, the inert `clock` parameter, and the two lander sets. Extend each act module's docstring with one section: "It edits one Dependabot branch per repository at a time" — the deadlock precondition, why a withheld sibling stays Dependabot's, and that ADR-0045 records it. Run Task 2 Step 5's word check on **both** act files.
- [ ] **Step 4: Green:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && .venv/bin/python -m pytest tests/services/test_estate_pr_branch_update.py tests/services/test_inert_pr_branch_update.py tests/estate_lander tests/inert_lander -q 2>&1 | tail -5
```

Every pre-existing "qualifies → acts" test must pass **unchanged** (Deliberately-not-changed bullet 2). If one needs editing, stop: the fake's default is wrong, not the test.
- [ ] **Step 5: Commit** — `feat(branch-update): withhold a first edit while an edited sibling is queued to land`, body naming both codes and that only the holding one is self-clearing for the landers. Trailer lines.

---

### Task 6: Serve the fact on both admission answers

**Files:**
- Modify: `src/orchestrator/services/estate_landing_admission.py` (`EstateLandingAdmission`, after `rollout_base_matches_pin: bool` at `:434`; constructor `:684-696`)
- Modify: `src/orchestrator/services/inert_landing_admission.py` (`InertLandingAdmission`, after `merge_method: str` at `:165`; constructor `:229-251`)
- Modify: `src/orchestrator/api/schemas.py` (`EstateLandingAdmissionResponse` `:637-661`, `InertLandingAdmissionResponse` `:755-786`)
- Modify: `src/orchestrator/api/routes.py` (`estate_landing_admission_route` `:773-802`, `inert_landing_admission_route` `:873-903`)
- Test: `tests/api/test_estate_landing_admission_api.py`, `tests/api/test_inert_landing_admission_api.py`, `tests/services/test_estate_pr_branch_update.py`, `tests/services/test_inert_pr_branch_update.py`

**Route shape** (estate; inert mirrors with `GitHubInertPullRequests`, `inert_landing_admission`, `policy_source`):

```python
credentials = github_app_credentials(settings)
gateway = GitHubEstatePullRequests(token_provider_for(credentials))
admission = estate_landing_admission(session, repository, pr_number, landing_source, record_source,
                                     gateway, enabled=..., credentials_configured=...)
withheld = withheld_for_sibling(
    qualifies=admission.branch_update_qualifies,
    outcome=lambda: branch_update_sibling_outcome(
        session, repository=admission.repository, target_number=admission.pr_number, gateway=gateway,
        compose=lambda n: SiblingAnswer(*_refusals_and_pin(estate_landing_admission(session, admission.repository, n, ..., gateway, ...)))),
)
return replace(admission, branch_update_withheld_for_sibling=withheld)
```

Keep the lambda bodies small; if they grow, give the new module two public helpers `estate_sibling_composer(...)` / `inert_sibling_composer(...)` and have **both the act and the route** call them (one definition of "compose a sibling", two callers — also satisfies `test_unreachable_guards`, which does not count same-module calls).

**Schema docstrings** (both models) must say, per spec §9: true only for outcome 2 (the target qualifies, is a Dependabot pull request, is positively owned, and another Dependabot pull request was positively observed edited and holding); false on outcome 3 so a failed scan never produces a quiet line; never co-occurs with a failing check; a fact about an observed sibling, **not** a record of the lane declining; a scan read failure leaves the admission answering with `false`; declared here or it does not exist on the wire. Check the prose with Task 2 Step 5's command pointed at `schemas.py` (it is in `WS42_DISPATCH_PATHS`, but the ws33 `merges` guard has no allowlist).

- [ ] **Step 1: Failing tests:**
  - Service: both admission functions return `branch_update_withheld_for_sibling is False` (composition alone observes no sibling).
  - The existing pins (`test_estate_pr_branch_update.py:885`, both API `…every_field_of_the_composed_answer`) go red until the model gains the field, then green — no edit to them.
  - Mirror-key pins beside `test_estate_pr_branch_update.py:445-464`: `from estate_lander.cli import _WITHHELD_FOR_SIBLING; assert _WITHHELD_FOR_SIBLING in EstateLandingAdmission.__dataclass_fields__`; the same in `test_inert_pr_branch_update.py` against `InertLandingAdmission` with `inert_lander.cli._WITHHELD_FOR_SIBLING`. (These import constants Task 7/8 create — so either add the constants to both `cli.py` files in this commit, or put these two pins in Tasks 7/8. **Put them in this commit** and add the two one-line constants here, so the key name is pinned the moment it is served.)
  - API, **observing `True` on the wire** (the WS-P2.12 hazard — a field that only ever serializes `False` is unproven). Precedent for faking a gateway at the API layer: `tests/api/test_pr_merge_api.py:37` (`FakeGateway`). Before writing it, confirm `routes.py` imports `GitHubEstatePullRequests`/`GitHubInertPullRequests` by name (they are the monkeypatch targets — measured: `routes.py` constructs them inline at `:799`/`:900`) and read what `token_provider_for` does with a non-`None` credential, so the fake is reached without minting a token. Then monkeypatch `orchestrator.api.routes.GitHubEstatePullRequests` (and `GitHubInertPullRequests`) to return a `SiblingGateway` producing outcome 2, and `orchestrator.api.routes.github_app_credentials` to return a non-`None` credential; for estate, override the record-source dependency with a `FakeChangeRecordSource` holding both records and the landing-source dependency with `redeploying_source()` (inert: `inert_source()` and an `inert_landing_doubles.FakeInertPolicySource`). Assert `body["branch_update_qualifies"] is True` **and** `body["branch_update_withheld_for_sibling"] is True`; the sibling-owned variant → `False`.
  - API: with no credentials (the existing fixture) the key is present and `False` (qualifies is false, so no scan runs).
  - Served fact versus act (spec test 8): for the same `SiblingGateway` fixture, the route's field is `True` iff the act refuses `…_SIBLING_HOLDING`; with `open_error` the field is `False` and the act refuses `…_SIBLINGS_UNREADABLE`.
- [ ] **Step 2: Run, watch fail. Step 3: Implement. Step 4: Green:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && .venv/bin/python -m pytest tests/api/test_estate_landing_admission_api.py tests/api/test_inert_landing_admission_api.py tests/services/test_estate_pr_branch_update.py tests/services/test_inert_pr_branch_update.py tests/services/test_estate_landing_admission.py tests/services/test_inert_landing_admission.py -q 2>&1 | tail -5
```

- [ ] **Step 5: Commit** — `feat(landing-admission): serve whether the branch update is withheld for a sibling`. Trailer lines.

---

### Task 7: Estate lander — `withheld`, and skip the withheld update

**Files:**
- Modify: `src/estate_lander/cli.py` (`_held_status` `:271-324`, `_consider` `:341-344`, `_NOT_A_FINDING` `:178-180`, `_REPORTED` `:186-197`, `_branch_updates` `:475`)
- Test: `tests/estate_lander/test_estate_lander.py`

**Classifier** (Ambiguity 7):

```python
def _held_status(refusals, *, rollout_base_matches_pin: bool, withheld_for_sibling: bool = False) -> str:
    present = set(refusals)
    unexplained = present - _DELIBERATE - _EXCEPTION
    derived = _freshness_derived(present, rollout_base_matches_pin=rollout_base_matches_pin)
    if _EXCEPTION & present or withheld_for_sibling:
        unexplained -= derived
    if unexplained or not refusals:
        return "held"
    if _EXCEPTION & present:
        return "exception"
    if withheld_for_sibling and derived:
        return "withheld"
    return "deliberate"
```

The default is `False` deliberately: the existing parametrized callers (`test_estate_lander.py:575` onward) keep compiling and keep asserting what they asserted, and a caller that forgets the argument gets `held` — the fail-toward-a-finding direction. `_consider` passes `withheld_for_sibling=answer.get(_WITHHELD_FOR_SIBLING) is True` (a missing key is `False` — the old-orchestrator direction). `_branch_updates` skips when `answer.get(_WITHHELD_FOR_SIBLING) is True`, with no line. `_NOT_A_FINDING` and `_REPORTED` gain `"withheld"` (place it after `"exception"` in `_REPORTED`). Extend the docstrings: `withheld` is keyed on an observed sibling, clears when the branch ahead lands, and is its own category (Devon's ruling that collapsing categories loses which is which).

- [ ] **Step 1: Failing tests (spec test 10):**
  - `{pace, behind}` + key `True` → `withheld`; key absent → `held`; key `False` → `held`.
  - `{behind}` + key `True` → `withheld`.
  - `{behind, landing_checks_not_clean}` + key `True` → `held` (guards the lander on its own).
  - `{behind, rollout_moved, update_type_unparseable}` + key `True` + base matches → `exception`.
  - `{pace}` + key `True` → `deliberate` (Ambiguity 7).
  - `_branch_updates` with `_qualifies() | {"branch_update_withheld_for_sibling": True}` → no outcome, `update_branch` never called; with the key absent → called (the existing `:739` fixture).
  - An act refused with `estate_branch_update_sibling_holding` → update line `deliberate`; with `estate_branch_update_siblings_unreadable` → `held` (spec test 8 lander half).
  - The literal `_REPORTED` pin (`:714`) gains `"withheld"`; `_NOT_A_FINDING < set(_REPORTED)` still holds; the summary-sums test still passes.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green** (`tests/estate_lander tests/architecture/test_estate_lander_isolation.py`).
- [ ] **Step 5: Commit** — `feat(estate-lander): report a sibling withheld for a holding branch as withheld`. Trailer lines.

---

### Task 8: Inert lander — the same

**Files:**
- Modify: `src/inert_lander/cli.py` (module docstring "THERE IS NO DELIBERATE REFUSAL HERE" section `:~53-64`; `_unsatisfied_status` `:364-385`; `_consider` `:409`; `_NOT_A_FINDING` `:209-211`; `_REPORTED` `:216-227`; `_branch_updates` `:461`)
- Test: `tests/inert_lander/test_inert_lander.py`

```python
def _unsatisfied_status(refusals, *, withheld_for_sibling: bool = False) -> str:
    present = set(refusals)
    unexplained = present - _EXCEPTION
    if _EXCEPTION & present or withheld_for_sibling:
        unexplained.discard(_FRESHNESS)
    if unexplained or not refusals:
        return "held"
    if _EXCEPTION & present:
        return "exception"
    if withheld_for_sibling and _FRESHNESS in present:
        return "withheld"
    return "held"
```

The module docstring's claim "this lane has no deliberate refusal" stays and gains one sentence: `withheld` is keyed on an observed sibling, not on a clock (spec §9).

- [ ] **Step 1: Failing tests** — mirror Task 7's list with `_held(...)`/`_qualifies(...)` helpers (`:172`, `:501`): `{behind}` + key → `withheld`; absent/false → `held`; `{behind, checks_not_clean}` + key → `held`; `{behind, ecosystem_excluded}` + key → `exception`; update pass skips on key `True`; `inert_branch_update_sibling_holding` → `deliberate`, `…_siblings_unreadable` → `held`; the `_REPORTED` iteration tests (`:621-650`) and `_NOT_A_FINDING <= set(_REPORTED)` still pass.
- [ ] **Step 2–4:** fail, implement, green (`tests/inert_lander tests/architecture/test_inert_lander_isolation.py`).
- [ ] **Step 5: Commit** — `feat(inert-lander): report a withheld sibling as withheld`. Trailer lines.

---

### Task 9: Mutation run

**Files:** none committed (harness lives in this worktree under a git-ignored path, or is deleted after). Tree must be clean first:

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && /usr/bin/git status --porcelain | head
```

Each mutant: assert the anchor matches **exactly once** in its file (the two act files and the two lander files carry near-identical text — a count of 2 means the anchor is ambiguous, not that it applied), apply, run the named test file(s) under `PYTHONDONTWRITEBYTECODE=1`, record killed/survived **and which test killed it**, restore with `/usr/bin/git checkout HEAD -- <path>`, assert `git diff --quiet`. Re-run the whole set after the last behavioural change.

| # | File | Mutation (anchor) | Must be killed by |
|---|---|---|---|
| M1 | serialization | delete the Dependabot-target return (step 3: `return SiblingOutcome.RELEASE` after the author test) → fall through | Task 4 second-author test (sync-bot target beside a holding sibling) |
| M2 | serialization | delete outcome 1 (`return SiblingOutcome.RELEASE_TARGET_EDITED`) | Task 4 day-one test |
| M3a/b | serialization | unestablished target treated as OWNED / as EDITED | Task 4 polarity control (target) |
| M4a/b | serialization | unestablished sibling treated as OWNED / as EDITED (`read_failed = True` → `continue` / → `holding_seen = True`) | Task 4 polarity control (sibling) |
| M5 | serialization | `_commit_class`: `!= UPDATE_BOT_LOGIN` → `== UPDATE_BOT_LOGIN` in the foreign-author clause | Task 2 classification rows |
| M6 | serialization | delete the committer clause of `foreign` | Task 2 "dependabot author + non-web-flow committer → foreign" |
| M7 | serialization | delete `and commit.verified` | Task 2 "web-flow unverified → unclassified" |
| M8a/b/c | serialization | arm (b): return `{}` / ignore `head_sha` (any event counts) / drop the `occurred_at` bound | Task 2 arm-(b) rows (the recreate row; the 10m01s row) |
| M9 | serialization | drop `commits[-1].sha == listed_head` | Task 2 last-sha row; Task 4 last-sha test |
| M10 | estate_pr_merge | `_get_pages`: return after page 1 | Task 1 two-page tests |
| M11 | estate_pr_merge | treat a failed later page as end-of-list | Task 1 failing-later-page tests |
| M12×7 | serialization | drop each holding member in turn (and `DELIBERATE_REFUSALS`, and the `freshness_derived_refusals` term) | Task 3 holding rows; Task 4 holding-gate outcome rows |
| M13×18 | serialization | remove each read-failure code in turn | Task 3 completeness test **and** the per-code `unreadable` row |
| M14 | serialization | check `READ_FAILURE_REFUSALS` after the holding test | Task 3 "read-failure beside checks_not_clean" |
| M15 | serialization | outcome 3 returns `WITHHOLD_SIBLING_HOLDING` | Task 5 unreadable-scan test (code assertion); Task 6 served-vs-act test |
| M16 | estate/inert act | raise the holding code for `WITHHOLD_SIBLINGS_UNREADABLE` | Task 5 unreadable-scan test |
| M17 | both lander cli | add `…_siblings_unreadable` to `_UPDATE_SELF_CLEARING` | Task 5 `…is_NOT_self_clearing` |
| M18 | both lander cli | classifier: `if _EXCEPTION & present or withheld_for_sibling` → `if True` | Task 7/8 "key absent → held" |
| M19 | serialization | `withheld_for_sibling` returns `qualifies` | Task 4 `withheld_for_sibling` rows; Task 6 sibling-owned API variant |
| M20 | serialization | `withheld_for_sibling` true for outcome 3 too | Task 4 row; Task 6 served-vs-act with `open_error` |
| M21 | estate/inert act | delete the conjunct (both `if outcome is …` blocks) | Task 5 discriminating act test |
| M22 | estate act | replace the scan with `if admission.branch_update_withheld_for_sibling:` (act trusts the served field) | Task 5 discriminating act test (the field is always `False` inside the act) |
| M23 | both lander cli | `_branch_updates`: drop the withheld gate | Task 7/8 update-pass skip test |
| M24 | serialization | composer exception handler catches `SQLAlchemyError` too | Task 4 `OperationalError` propagates |

- [ ] **Step 1:** run the set; **Step 2:** for every survivor, decide equivalent-mutant vs missing control and add the control (a clause no test can falsify is a defect of the tests — prefer strengthening the fixture over deleting the clause); **Step 3:** re-run the full set; **Step 4:** if tests were added, commit them: `test(branch-update): controls for the mutants the first run left alive`, trailer lines. Report kills per mutant in the PR body.

---

### Task 10: Whole-repo gates and collected-count reconciliation

- [ ] **Step 1: Fetch and re-read `main`** (the re-read is inert without the fetch):

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && git fetch origin && git log --no-show-signature --oneline HEAD..origin/main | head && git rebase origin/main
```

If `origin/main` moved, retake the baseline from the fetched ref (`git stash -u` is forbidden here — use `git worktree add /tmp/serialize-base origin/main` read-only, collect there, then `git worktree remove`).

- [ ] **Step 2: Full gate, alone** (no other suite on `orchestrator_test_serialize`):

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && make check 2>&1 | tee /tmp/serialize-check.log | tail -30 ; grep -m1 'rootdir:' /tmp/serialize-check.log ; grep -E 'collected|passed|failed' /tmp/serialize-check.log | tail -3
```

`rootdir:` must be this worktree. Whole-repo scans that a per-task loop does not run and that this change can trip: `test_ws32_scope_guards`, `test_ws33_scope_guards`, `test_ws34_scope_guards` (word guards on the new module, both acts, `schemas.py`), `test_unreachable_guards` (every public function in the new module has a caller in another module: acts and routes), `test_cross_boundary_vocabulary` (should be untouched — Correction 1), `test_wsp21_invariant_scan` (new module), `test_scope_guards` route inventories and `tests/idempotency/test_matrix.py` (must NOT move), both lander isolation tests (must NOT move), `ruff format --check .`.

- [ ] **Step 3: Reconcile by node id:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && .venv/bin/python -m pytest --collect-only -q 2>/dev/null | grep '::' | sort > /tmp/serialize-nodes-branch.txt && \
  wc -l /tmp/serialize-nodes-main.txt /tmp/serialize-nodes-branch.txt && \
  echo '--- removed ---' && comm -23 /tmp/serialize-nodes-main.txt /tmp/serialize-nodes-branch.txt && \
  echo '--- added (count) ---' && comm -13 /tmp/serialize-nodes-main.txt /tmp/serialize-nodes-branch.txt | wc -l && \
  comm -13 /tmp/serialize-nodes-main.txt /tmp/serialize-nodes-branch.txt | grep wsp21
```

Expected: **removed** = only the two replaced self-clearing equality pins if they were renamed (prefer editing in place so removed is empty). **Added** = the tests written in Tasks 1–9 plus exactly **three** `test_wsp21_invariant_scan.py` cases carrying `branch_update_serialization.py` in their id (secret scan, merge-method scan, merge-pr scan — one new `src/**.py`). Both files non-empty; zero added and zero removed beside a count delta is a broken measurement.

- [ ] **Step 4: `/code-review`** naming the pull request number and `/Users/devon/Projects/orchestrator/.worktrees/serialize-freshening` explicitly (a forked review agent inherits the session cwd and will otherwise review the main tree). Fix findings in a separate commit.

---

### Task 11: ADR-0045

**Files:** Create `docs/decisions/0045-the-lane-edits-one-dependabot-branch-per-repository.md`

- [ ] **Step 1: Confirm the number is free on a fetched `main`:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && git fetch origin && git ls-tree --name-only origin/main docs/decisions/ | sort | tail -2
```

Measured at plan time: `0044-a-failed-rollout-production-has-moved-past-is-an-exception.md` is the highest. If a later ADR took 0045, use the next free number and update the four act/lander docstrings that cite it.

- [ ] **Step 2: Write it** in the house shape of `docs/decisions/0044-*.md`, in this order:
  1. **Status** — Accepted 2026-09-25. **Supersedes** the unmerged 2026-09-20 design (spec + plan on `main` since #286; no ADR was ever merged for it). **Amends** ADR-0019 Increment 6 and ADR-0038 part 2 (both branch-update acts gain a conjunct). ADR-0024's shared predicate is untouched.
  2. **What stays from 2026-09-20** (spec §1): update-branch merges base into head under the App; Dependabot disowns; the deadlock; the App cannot `@dependabot recreate`; no PAT in the landing path.
  3. **The two measurements** (spec §2): Dependabot rebased 0 of 51 merely-behind branches; the deadlock's precondition — two lane-edited branches in one repository and a landing — in all four stuck cases, two of them edited in the same pass (hence arm b).
  4. **The rule** and its three outcomes, the holding / read-failure / releasing sets, the ten-minute bound and its trade.
  5. **Why it costs no landing latency** (spec §4), and the one case where it does.
  6. **Reporting:** the served fact, `withheld` as its own category, why `…_siblings_unreadable` stays a finding.
  7. **Alternatives rejected** (spec §6): strict, `!= dirty`, G*, older-sibling-first, and the "older sibling about to land" clause with its revisit trigger.
  8. **Residuals** (spec §13), including queue jumping, lost response, server-side rebase probe, `[dependabot skip]` follow-up probe, stall residuals, the pre-existing edited population.
  9. **Corrections against the spec** — all seven from this plan's header (Correction 2 including the import-cycle note).
- [ ] **Step 3:** word-guard check any sentence reused in a docstring (Task 2 Step 5, pointed at the ADR). **Step 4: Commit** — `docs: ADR-0045, the lane edits one Dependabot branch per repository at a time`, trailer lines.

---

### Task 12: Correct CLAUDE.md

**Files:** Modify `CLAUDE.md` — every addition below `<!-- code-standards:end -->` (`:41`).

- [ ] **Step 1: Find the bullets:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && grep -n 'update-branch. clears it synchronously\|The rule for WHICH pull requests to update\|THIRD RULING, 2026-08-14\|a landing \*disarms\* its siblings\|BEING BEHIND BASE CAUSES\|landing_checks_in_flight. (still running' CLAUDE.md
```

At plan time: the `update-branch` bullet opens at `:3160` and its "WHICH pull requests" paragraph at `:3186`; the third freshness ruling at `:~2026`; the cascade-disarm bullet's "self-healing that exists is Dependabot's own rebase" at `:3350-3354`; the deadlock bullet at `:~3395`; the `mergeable_state` four-causes bullet at `:3723`.
- [ ] **Step 2: Append to the `update-branch` bullet** (do not delete it — the 12-seconds-versus-14-hours and 202-versus-200 facts stand):

```markdown
  **AMENDED 2026-09-25 (ADR-0045): the lane now edits at most ONE lane-edited, landable Dependabot
  branch per repository.** Update-branch merges base into head under the App's identity, and
  Dependabot then refuses to rebase the branch ("edited by someone other than Dependabot"); two such
  branches in one repository plus a landing is the whole precondition of the deadlock, measured in
  all four stuck cases (`brain#70`, `change-manager#87`, `#93`, `factory-runner#71`), two of them
  edited in the SAME pass seconds apart. So both acts withhold a FIRST edit while another edited
  Dependabot pull request in the repository still holds (`…_branch_update_sibling_holding`, served
  as `branch_update_withheld_for_sibling`, reported by both landers as `withheld`, not a finding),
  and refuse as a FINDING when they cannot establish that (`…_branch_update_siblings_unreadable`).
  **Freshening cannot be handed back to Dependabot**: across 51 weekly runs it rebased 0 merely-behind
  branches. The "sole remaining obstacle" rule below still decides WHETHER a branch may be freshened;
  the sibling rule decides WHICH ONE, and lives at the acts, never in the shared predicate.
```

- [ ] **Step 3: Append one line to the cascade-disarm bullet** (`:3350`): its "self-healing that exists is Dependabot's own rebase … on Dependabot's schedule" is now measured, not inferred — Dependabot does not rebase a merely-behind branch at all (0 of 51), only a conflicted one (~2 minutes) or on a newer version (ADR-0045).
- [ ] **Step 4: Append one line to the third freshness ruling** (`:~2026`): the rule is unchanged; `withheld` is a fourth, separate category keyed on an observed holding sibling, not on the lane declining (ADR-0045) — so it does not reopen the durability ruling.
- [ ] **Step 5: Add one new bullet at the end of the file** (below every existing bullet): *"A HOLDING branch whose checks never finish stalls its repository"* — `landing_checks_awaiting_verdict` on a current head, a run in flight that never gets a runner, or `landing_mergeability_unknown` that never resolves; reported once, as `held` on the holding branch's own line; a build session must not write a test asserting every holding member clears on its own (spec §6).
- [ ] **Step 6: Verify the boundary:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && grep -n 'code-standards:end' CLAUDE.md && grep -n 'ADR-0045' CLAUDE.md
```

Every `ADR-0045` line number must be greater than the `code-standards:end` line.
- [ ] **Step 7: Commit** — `docs: the lane edits one Dependabot branch per repository (ADR-0045)`, trailer lines.

---

### Task 13: Land, deploy, verify, tear down

**Files:** none.

- [ ] **Step 1: Push and open the PR.**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && git push -u origin serialize-freshening && gh pr create --title "The lane edits one Dependabot branch per repository at a time (ADR-0045)" --body "Implements docs/superpowers/specs/2026-09-25-serialize-dependabot-freshening-design.md per docs/superpowers/plans/2026-09-25-serialize-dependabot-freshening.md. Mutation results: <table from Task 9>.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01791bvRbFznUERbugr9aoy3"
```

Merge when `Quality` and `Runner consumer compatibility` are green. Record the merge SHA (full 40 characters): `git fetch origin && git rev-parse origin/main`.

- [ ] **Step 2: No migration — confirm, don't assume:**

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && git diff --stat <pre-merge-main>..<merge-sha> -- migrations alembic src/orchestrator/persistence | tail -1
```

Must print nothing. If it prints anything, stop: the order becomes build → migrate (from the new image, per the orchestrator's CLAUDE.md recipe) → swap.

- [ ] **Step 3: Build.** Trigger `Release image` with the full SHA as the **`ref` input** (the workflow's input is named `ref`, measured in `.github/workflows/release-image.yml`; `gh workflow run --ref` selects the workflow file's branch and 422s on a SHA):

```bash
cd /Users/devon/Projects/orchestrator/.worktrees/serialize-freshening && gh workflow run release-image.yml -f ref=<40-char-merge-sha> -f label=adr0045
gh run list --workflow=release-image.yml -L 1   # re-run until the new run appears; watch it with gh run watch
```

Wait for success; read the pushed tag `…:<short-sha>-adr0045-amd64` and `sha-<full>` and the digest from the run log. Building swaps nothing.

- [ ] **Step 4: Swap via infraops.** Before writing, record the outgoing tag and running digest (`mcp__infraops__coolify_get_application` on the orchestrator app — read `docker_registry_image_tag`, never call `coolify_get_deployment`, which leaks a deploy key). Then `coolify_update_application` (new tag) + `coolify_deploy`. Do not swap while an estate- or inert-landing pass is mid-run (read each plist's `StartCalendarInterval` in `scripts/com.devon.estate-landing.plist` / `com.devon.inert-landing.plist` for the minutes, and the logs' last lines for a pass in flight). Expect ~20 s of `no available server`: the orchestrator's Coolify health check is disabled.

- [ ] **Step 5: Verify — the served surface first, because health and digest cannot see it:**

```bash
curl -s https://sds.alobar.net/openapi.json | python3 -c "
import sys, json
s = json.load(sys.stdin)['components']['schemas']
for m in ('EstateLandingAdmissionResponse', 'InertLandingAdmissionResponse'):
    p = sorted(s[m]['properties']); print(m, 'branch_update_withheld_for_sibling' in p, p)
"
curl -s https://sds.alobar.net/health/live
```

Both `True`. `/health/live` `revision` equals the merge SHA. Then on the VPS (via `mcp__infraops__vps_exec`):

```bash
C=$(docker ps --filter name=<orchestrator-app-uuid> --format '{{.Names}}' | head -1)
docker exec "$C" sh -c 'cd /app && .venv/bin/alembic current; .venv/bin/alembic heads'
docker image inspect "$(docker inspect "$C" --format '{{.Image}}')" --format '{{index .RepoDigests 0}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

`current == heads`; the digest equals the one the Release run pushed; the revision label equals the merge SHA.

- [ ] **Step 6: Pull the main tree LAST** (the landers run its working copy; merging is what ships them):

```bash
cd /Users/devon/Projects/orchestrator && git pull && .venv/bin/python --version
```

No `[project.scripts]` entry changed, so no `uv sync`.

- [ ] **Step 7: Watch one real pass of each lander.** After the next scheduled runs, read `~/Library/Logs/estate-landing.log` and the inert-landing log: any `withheld` lines name a sibling of a branch that still holds; no `…_siblings_unreadable` unless a read genuinely failed; `updated` appears at most once per repository per pass for a Dependabot branch that was owned. A bare hand-run of either `run-*-landing.sh` is a dry run (the plists pass `--submit`) and proves nothing about the acting path.

- [ ] **Step 8: Tear down, at the end of the session, after the report:**

```bash
cd /Users/devon/Projects/orchestrator && /usr/bin/git status --porcelain && \
  git worktree remove .worktrees/serialize-freshening --force && git branch -D serialize-freshening && \
  dropdb -h 127.0.0.1 -U postgres orchestrator_test_serialize
```

Also, per spec §14, tear down the superseded work: `git worktree remove .worktrees/stop-freshening --force`, `git branch -D stop-freshening`, `git push origin --delete stop-freshening` (it has no PR, so `delete_branch_on_merge` will not remove it), `dropdb -h 127.0.0.1 -U postgres orchestrator_test_stopfresh`. The main tree must show nothing this work created.
