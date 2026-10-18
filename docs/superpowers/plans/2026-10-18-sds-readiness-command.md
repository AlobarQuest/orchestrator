# SDS readiness command implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `portfolio sds-ready <repo>`, the deterministic readiness command that decides whether a
repository can be onboarded as an inert SDS factory target and writes a build handoff and a task
list for whatever stands in the way.

**Architecture:** The command reuses `portfolio onboard`'s existing checks, adds six SDS checks, and
sorts every result by owner through a guidance registry keyed by check id. A pure `assess` function
turns check results and Devon's recorded decisions into a verdict, and a pure renderer turns the
verdict into two markdown files. Nothing in the command writes to a remote or to the target's
working tree.

**Tech stack:** Python (the kit's `.python-version`), stdlib `tomllib`, `gh` via the kit's existing
`_gh` / `_gh_read` seams, pytest.

**Spec:** `docs/superpowers/specs/2026-10-18-sds-target-onboarding-design.md` (orchestrator).
This is plan 1 of 3; plan 2 (onboarding procedure) and plan 3 (canary) are written once this one
lands, because they consume its interfaces.

**Repository:** `~/Projects/project-standards`. Work in a worktree with its own venv.

**One deviation from the spec, deliberate:** the spec said `portfolio onboard` "stays the readiness
command". `portfolio onboard`'s output is the `portfolio-readiness/v1` document, a cross-repo
contract that intent-packages' `factory create --from-readiness` consumes. Adding SDS checks and
owner routing to it would change what that contract's consumers see. This plan adds `portfolio
sds-ready` beside it and reuses its check list, so "ready" still has one definition and the
contract doesn't move.

## Global constraints

- The command never writes to GitHub, App Brain, BWS or the target repository's working tree.
- Output files live under `config.portfolio_home() / "onboarding" / <repo-name> /`.
- Owners are exactly `"repository"`, `"devon"` and `"onboarding"`.
- Scope is inert repositories only; a recorded `redeploys` is a refusal, never a gap.
- A check whose status is `unknown` is never routed to the build agent and never reads as clean.
- `factory.pat_scope` is excluded from assessment: it is structurally `unknown` for a fine-grained
  token (`readiness_schema.py`).
- Rendered files are byte-identical for identical inputs: no timestamps, stable ordering.
- Branch protection guidance never sets `required_approving_review_count`.
- `make check` in project-standards must be green before each commit.

## Review focus

1. **A check that couldn't measure** (`unknown`, for example a missing App Brain key) must land in
   an "environment" list that blocks ready, never in the handoff. Pinned in Task 8.
2. **A half-onboarded repository** (some onboarding actions done) must report phase
   `in-progress` and say "resume onboarding", not "ready to onboard". Pinned in Task 8.
3. **A malformed decisions file** must become one of Devon's tasks naming the parse error, never a
   crash or a silent default. Pinned in Task 3.
4. **A checkout with no GitHub origin** must exit 2 with a message, never write a handoff for the
   wrong repository. Pinned in Task 10.
5. **Running the command twice** on the same state must produce byte-identical files. Pinned in
   Task 9.

---

### Task 1: Fix two existing checks

**Files:**
- Modify: `src/portfolio/onboard_checks.py` (`check_protection`, `check_ci_executed`)
- Test: `tests/test_onboard_checks_advisory.py`, `tests/test_onboard_checks_ci.py`

**Interfaces:**
- Produces: unchanged signatures; new detail id `ci.no-workflow`.

- [ ] **Step 1: Write the failing tests**

```python
def test_unprotected_fix_never_requires_a_review(...):
    result = check_protection("o/r", gh_read=lambda a: (None, "HTTP 404: Branch not protected"))
    assert result["status"] == "violation"
    assert "required_approving_review_count" not in result["fix"]
    assert "strict" in result["fix"]

def test_ci_without_quality_workflow_names_the_cause(make_repo):
    repo = make_repo("r")  # no .github/workflows/quality.yml; origin set to a GitHub slug
    result = check_ci_executed(repo, gh=lambda a: None)
    assert result["status"] == "unknown"
    assert result["details"][0]["id"] == "ci.no-workflow"
    assert "gh auth" not in (result["fix"] or "")
```

- [ ] **Step 2: Run them and see them fail**

Run: `pytest tests/test_onboard_checks_advisory.py tests/test_onboard_checks_ci.py -v`
Expected: both new tests FAIL.

- [ ] **Step 3: Implement**

`check_protection`'s unset-branch fix names a required status check by job name, `strict: false`
and no review count, and points at the orchestrator runbook's step 9. `check_ci_executed` returns
`ci.no-workflow` (`unknown`, fix "add the Quality workflow: see code.onboarded") when
`repo / ".github/workflows/quality.yml"` is absent, before calling `gh`.

- [ ] **Step 4: Run the tests and `make check`**

Expected: PASS, and `make check` green.

- [ ] **Step 5: Commit** — `fix: protection fix text drops the review count; ci.executed names a missing workflow`

---

### Task 2: Measure factory checks for an explicitly named repository

**Files:**
- Modify: `src/portfolio/factory_checks.py` (`check_pat_access`, `check_pat_scope`,
  `check_secrets`, `check_landing_known`, `check_app_access`, `run_factory_checks`)
- Test: `tests/test_factory_checks.py`

**Interfaces:**
- Produces: `run_factory_checks(repo, slug: str, gh=_gh, reach=None, measure_unscoped: bool = False) -> list[dict]`;
  each of the five checks gains `measure_unscoped: bool = False`. `sweep` is unchanged and never
  passes it.

- [ ] **Step 1: Write the failing tests**

```python
def test_unscoped_repo_is_measured_when_asked(make_repo, monkeypatch):
    repo = make_repo("r", files={"PROJECT.md": "---\nname: r\n---\n"})  # no delivery_profile
    out = check_landing_known(repo, "o/r", fetch=lambda s: {"landing": "inert"},
                              measure_unscoped=True)
    assert out["status"] == "pass"

def test_sweep_still_skips_unscoped_repos(...):
    # existing behavior: without the flag, the same repo reads out of scope
    assert check_landing_known(repo, "o/r", fetch=...)["details"][0]["id"].endswith("out-of-scope")
```

(Copy the out-of-scope detail id from `_out_of_scope`; set the App Brain key env the way the
existing landing tests do.)

- [ ] **Step 2: Run and see the first fail**

- [ ] **Step 3: Implement** — each check's guard becomes `if not (measure_unscoped or in_q2_scope(repo))`.

- [ ] **Step 4: Run the tests and `make check`**

- [ ] **Step 5: Commit** — `feat: factory checks can measure a repository outside the sweep's scope`

---

### Task 3: Devon's decisions file

**Files:**
- Create: `src/portfolio/sds_decisions.py`
- Test: `tests/test_sds_decisions.py`

**Interfaces:**
- Produces:
  - `onboarding_dir(repo_name: str) -> Path` = `config.portfolio_home() / "onboarding" / repo_name`
  - `DECISIONS_FILE = "decisions.toml"`
  - `@dataclass(frozen=True) class Decisions: onboard: bool | None; onboard_reason: str | None; landing: str | None`
  - `read_decisions(repo_name: str) -> Decisions` — raises `DecisionsError(Exception)` for a file
    that exists and doesn't parse or carries a wrong type; returns all-`None` when absent.
  - `check_decisions(repo_name: str) -> dict` with id `sds.decisions`.

File format (`decisions.toml`):

```toml
onboard = true
onboard_reason = "why this repository should be a factory target"
landing = "inert"   # or "redeploys"
```

- [ ] **Step 1: Write the failing tests**

```python
def test_absent_file_is_a_violation_listing_both_decisions(portfolio_env):
    out = check_decisions("r")
    assert out["status"] == "violation"
    assert {d["id"] for d in out["details"]} == {"sds.decide-onboard", "sds.decide-landing"}

def test_malformed_file_is_a_violation_naming_the_error(portfolio_env):
    (onboarding_dir("r")).mkdir(parents=True); (onboarding_dir("r") / "decisions.toml").write_text("onboard = ")
    out = check_decisions("r")
    assert out["status"] == "violation"
    assert out["details"][0]["id"] == "sds.decisions-unreadable"

def test_declined_onboarding_is_a_refusal(portfolio_env):   # onboard = false
    assert check_decisions("r")["details"][0]["id"] == "sds.refused-not-onboarding"

def test_redeploys_is_a_refusal(portfolio_env):             # landing = "redeploys"
    assert check_decisions("r")["details"][0]["id"] == "sds.refused-redeploys"

def test_complete_inert_decision_passes(portfolio_env):
    assert check_decisions("r")["status"] == "pass"
```

- [ ] **Step 2: Run and see them fail**
- [ ] **Step 3: Implement.** `onboard = true` requires a non-empty `onboard_reason`; `landing` must
  be `"inert"` or `"redeploys"`. Use the kit's `_result` shape.
- [ ] **Step 4: Run the tests and `make check`**
- [ ] **Step 5: Commit** — `feat: record Devon's onboarding decisions per repository`

---

### Task 4: Declaration and inventory checks

**Files:**
- Create: `src/portfolio/sds_checks.py`
- Test: `tests/test_sds_checks.py`

**Interfaces:**
- Consumes: `onboard_checks._remote_contents(slug, path, gh_read)`,
  `factory_target.parse_declaration(text)`, `factory_target.FactoryTargetError`.
- Produces:
  - `check_declaration(slug: str, gh_read=_gh_read) -> dict` — id `sds.declaration`. Pass when the
    default branch declares `factory_target = true`; violation when absent or `false`; violation
    with id `sds.declaration-malformed` for `FactoryTargetError`; unknown when the read fails.
  - `check_cred_inventory(slug: str, gh_read=_gh_read) -> dict` — id `sds.cred_inventory`. Reads
    `.cred-consumers.toml` from `AlobarQuest/orchestrator` and `AlobarQuest/factory-runner` default
    branches. Pass when both list a `gh-actions-secret` consumer for `slug` (case-insensitive):
    `FACTORY_RUNNER_TOKEN` under credential id `orchestrator-factory-runner-bearer`, and
    `FACTORY_PR_TOKEN` under credential id `factory-pr-token`. One detail per missing entry,
    naming the file and credential id.
  - Module constants `INVENTORIES = (("AlobarQuest/orchestrator", "orchestrator-factory-runner-bearer", "FACTORY_RUNNER_TOKEN"), ("AlobarQuest/factory-runner", "factory-pr-token", "FACTORY_PR_TOKEN"))`.

- [ ] **Step 1: Write the failing tests** with a fake `gh_read` that serves base64 contents per
  path (mirror the fakes in `tests/test_onboard_checks_runner.py`):

```python
def test_declared_true_passes(): ...           # factory_target = true + reason
def test_absent_declaration_is_violation(): ... # 404 on file, repo answers
def test_bare_true_is_malformed(): ...          # no reason -> sds.declaration-malformed
def test_unreadable_is_unknown(): ...           # gh_read returns (None, "HTTP 500")
def test_inventory_missing_one_names_it():
    out = check_cred_inventory("AlobarQuest/RentVSBuyGA", gh_read=fake_with_orchestrator_entry_only)
    assert out["status"] == "violation"
    assert [d["id"] for d in out["details"]] == ["sds.inventory-missing-factory-pr-token"]
def test_inventory_slug_match_ignores_case(): ...
```

- [ ] **Step 2: Run and see them fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the tests and `make check`**
- [ ] **Step 5: Commit** — `feat: SDS checks for the declaration and the rotation inventories`

---

### Task 5: Branch settings check

**Files:**
- Modify: `src/portfolio/sds_checks.py`
- Test: `tests/test_sds_checks.py`

**Interfaces:**
- Produces: `check_branch_settings(slug: str, gh_read=_gh_read) -> dict` — id
  `sds.branch_settings`. Reads `repos/{slug}` and `repos/{slug}/branches/{default_branch}/protection`.
  One detail per unmet setting, ids:
  `sds.squash-disallowed` (`allow_squash_merge` not true),
  `sds.squash-message` (`squash_merge_commit_message` != `"COMMIT_MESSAGES"`),
  `sds.no-protection`,
  `sds.no-required-check` (protection has no required status check context),
  `sds.strict` (`required_status_checks.strict` true),
  `sds.review-required` (any `required_approving_review_count` > 0),
  `sds.auto-merge-off` (`allow_auto_merge` not true). Unknown when either read fails for a
  reason other than an unprotected-branch 404.

- [ ] **Step 1: Write the failing tests**

```python
def test_rentvsbuyga_as_surveyed():
    # squash allowed, COMMIT_MESSAGES, no protection, auto-merge off
    ids = [d["id"] for d in check_branch_settings("o/r", gh_read=fake(...))["details"]]
    assert ids == ["sds.no-protection", "sds.auto-merge-off"]

def test_fully_configured_passes(): ...
def test_review_count_is_flagged(): ...
def test_unreadable_repo_is_unknown(): ...
```

- [ ] **Step 2: Run and see them fail**
- [ ] **Step 3: Implement** — detail order is the order listed in Interfaces.
- [ ] **Step 4: Run the tests and `make check`**
- [ ] **Step 5: Commit** — `feat: SDS check for branch protection and merge settings`

---

### Task 6: Behavior-evidence and inert-landing checks

**Files:**
- Modify: `src/portfolio/sds_checks.py`
- Test: `tests/test_sds_checks.py`

**Interfaces:**
- Consumes: `factory_checks._landing_fetch`, `config.app_brain_url()`,
  `config.app_brain_read_key()`, `LANDING_INERT`, `LANDING_REDEPLOYS`, `LANDING_UNKNOWN`.
- Produces:
  - `EVIDENCE_PATH = "docs/sds-readiness/evidence.toml"`
  - `check_behavior_evidence(slug: str, gh_read=_gh_read) -> dict` — id `sds.behavior_evidence`,
    read from the default branch. Required keys: `serves_site` (bool); `baseline_commit` and
    `after_commit` (non-empty strings); `unexplained_differences` (list, must be empty). When
    `serves_site` is true, `pages` must be a non-empty list of strings; when false, `reason` must
    be a non-empty string. Detail ids: `sds.evidence-absent`, `sds.evidence-malformed` (names the
    key), `sds.evidence-differences` (lists them). Unknown when the read fails.
  - `check_landing_inert(slug: str, fetch=None) -> dict` — id `sds.landing_inert`. Pass for
    `inert`; violation `sds.landing-unrecorded` for App Brain `unknown`; violation
    `sds.refused-redeploys` for `redeploys`; unknown for no answer or no key (reuse
    `_credential_absent`'s wording).

Evidence file format:

```toml
serves_site = true
baseline_commit = "<sha before readiness work>"
after_commit = "<sha after readiness work>"
pages = ["/", "/results"]              # each captured at desktop and phone width, before and after
unexplained_differences = []
```

- [ ] **Step 1: Write the failing tests** — one per detail id and status above, plus
  `test_site_without_pages_is_malformed` and `test_non_site_needs_a_reason`.
- [ ] **Step 2: Run and see them fail**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the tests and `make check`**
- [ ] **Step 5: Commit** — `feat: SDS checks for behavior evidence and an inert landing`

---

### Task 7: Guidance registry

**Files:**
- Create: `src/portfolio/sds_guidance.py`
- Test: `tests/test_sds_guidance.py`

**Interfaces:**
- Produces:
  - `OWNERS = ("repository", "devon", "onboarding")`
  - `@dataclass(frozen=True) class Guidance: owner: str; why: str; done: str; how: str`
  - `GUIDANCE: dict[str, Guidance]` keyed by check id
  - `EXCLUDED = {"factory.pat_scope": "structurally unknown for a fine-grained token; step 6's probe tests it"}`
  - `REFUSAL_DETAIL_IDS = frozenset({"sds.refused-not-onboarding", "sds.refused-redeploys"})`

Owners, fixed by the spec:

| Owner | Check ids |
|---|---|
| `repository` | `git.current`, `project.manifest`, `code.onboarded`, `ci.executed`, `security.clean`, `profile.declared`, `deps.dependabot`, `backlog.hygiene`, `standards.pinned`, `sds.behavior_evidence` |
| `devon` | `sds.decisions`, `factory.pat_access`, `factory.app_access` |
| `onboarding` | `sds.declaration`, `runner.caller`, `sds.cred_inventory`, `factory.secrets`, `factory.landing_known`, `sds.landing_inert`, `repo.protection`, `sds.branch_settings` |

`why`, `done` and `how` are prose a build agent or Devon can act on without the runbook. Take
them from the orchestrator runbook `docs/operations/onboarding-a-target.md` and the spec's
"Acceptance bar". `ci.executed`'s `how` says to put the test runner where `uv sync` installs it
(a dependency group), and for an npm repository, an `npm test` that collects tests.
`sds.behavior_evidence`'s `how` states the full capture procedure and anti-goal 1 verbatim.
`factory.pat_access`'s `how` is the GitHub settings-page procedure (runbook step 4).

- [ ] **Step 1: Write the failing tests**

```python
def test_every_check_the_command_runs_has_guidance_or_is_excluded():
    ids = {c["id"] for c in sds_ready.run_all_checks(FAKE_REPO, fakes...)}   # Task 10 seam
    assert ids <= set(GUIDANCE) | set(EXCLUDED)

def test_owners_are_the_fixed_three():
    assert {g.owner for g in GUIDANCE.values()} == set(OWNERS)

def test_no_guidance_suggests_a_review_count():
    assert all("required_approving_review_count" not in g.how for g in GUIDANCE.values())
```

(Until Task 10 lands, assert against an explicit list of the ids in the owner table.)

- [ ] **Step 2–4: Run, implement, run** with `make check`
- [ ] **Step 5: Commit** — `feat: owner and handoff guidance for every readiness check`

---

### Task 8: Assess

**Files:**
- Create: `src/portfolio/sds_assess.py`
- Test: `tests/test_sds_assess.py`

**Interfaces:**
- Consumes: `GUIDANCE`, `EXCLUDED`, `REFUSAL_DETAIL_IDS`.
- Produces:
  - `@dataclass(frozen=True) class Verdict:`
    `refusals: tuple[dict, ...]` (refusal details),
    `environment: tuple[dict, ...]` (checks with status `unknown`, any owner),
    `repository: tuple[dict, ...]`, `devon: tuple[dict, ...]` (failing checks by owner),
    `onboarding: tuple[tuple[dict, bool], ...]` (every onboarding-owned check and whether it's done),
    `phase: str` (`"not-started"`, `"in-progress"`, `"complete"`),
    `ready: bool`
  - `assess(checks: list[dict]) -> Verdict`

Rules:
- A check whose details include a `REFUSAL_DETAIL_IDS` id is a refusal; any refusal makes `ready`
  false.
- `pass` and `not-applicable` are clean. `unknown` goes to `environment`, whatever its owner.
  `violation` goes to its owner's list; onboarding-owned violations are "not done", not gaps.
- `phase`: `complete` when every onboarding check passes, `not-started` when none does,
  otherwise `in-progress`.
- `ready` = no refusals, no environment entries, and empty `repository` and `devon` lists.
- Checks in `EXCLUDED` are dropped. A check id with no guidance raises `KeyError`, never routes
  silently.
- Tuple order follows the input order.

- [ ] **Step 1: Write the failing tests**

```python
def test_unknown_never_reaches_the_handoff():
    v = assess([chk("ci.executed", "unknown")])
    assert v.repository == () and len(v.environment) == 1 and not v.ready

def test_half_onboarded_is_in_progress_and_still_ready():
    v = assess([chk("sds.declaration", "pass"), chk("factory.secrets", "violation"), *clean_rest])
    assert v.phase == "in-progress" and v.ready

def test_redeploys_refuses():
    v = assess([chk("sds.decisions", "violation", detail="sds.refused-redeploys"), *clean_rest])
    assert not v.ready and v.refusals

def test_unguided_check_raises(): ...
def test_pat_scope_is_ignored(): ...
```

- [ ] **Step 2–4: Run, implement, run** with `make check`
- [ ] **Step 5: Commit** — `feat: assess readiness by owner, phase and refusal`

---

### Task 9: Render the handoff and the task list

**Files:**
- Create: `src/portfolio/sds_render.py`
- Test: `tests/test_sds_render.py`

**Interfaces:**
- Consumes: `Verdict`, `GUIDANCE`.
- Produces:
  - `@dataclass(frozen=True) class RepoFacts: name: str; slug: str; default_branch: str; tooling: tuple[str, ...]; checkout: Path`
  - `render_handoff(facts: RepoFacts, verdict: Verdict) -> str`
  - `render_tasks(facts: RepoFacts, verdict: Verdict) -> str`
  - `render_summary(facts: RepoFacts, verdict: Verdict) -> str` (the stderr digest)

`render_handoff` sections, in order (the spec's "Build handoff document"):
1. What SDS onboarding is, and the instruction: "Work until `portfolio sds-ready <checkout>`
   reports no repository work. Merge your own pull request once the repository's Quality job
   passes on it and the evidence file records no unexplained differences."
2. Repository facts.
3. One subsection per `verdict.repository` check: its details, then `why`, `done`, `how`.
4. Standards that apply, by path: `~/Developer/code-standards/STANDARDS.md`; the security scanner
   command from the orchestrator runbook; "query Code Brain before adding a cross-cutting
   pattern".
5. Hard constraints: the spec's two anti-goals, verbatim; never deploy.
6. How to rerun and what clean looks like.

`render_tasks` lists each `verdict.devon` check with `why`, `done`, `how`, and for `sds.decisions`
the decisions file's absolute path and format. `render_summary` names the phase and, when
`ready`, ends with exactly: `Ready to onboard <name>. Reply yes to onboard it.` When
`phase == "in-progress"` and ready, it says `Onboarding is partly done; reply yes to resume it.`

- [ ] **Step 1: Write the failing tests**

```python
def test_handoff_is_deterministic():
    assert render_handoff(FACTS, VERDICT) == render_handoff(FACTS, VERDICT)
    assert "2026" not in render_handoff(FACTS, VERDICT)   # no dates leak in

def test_handoff_carries_anti_goals_and_rerun_command(): ...
def test_tasks_name_the_decisions_file_path(): ...
def test_summary_asks_the_question_only_when_ready(): ...
def test_summary_says_resume_when_in_progress(): ...
```

- [ ] **Step 2–4: Run, implement, run** with `make check`
- [ ] **Step 5: Commit** — `feat: render the build handoff, the task list and the summary`

---

### Task 10: `portfolio sds-ready`

**Files:**
- Create: `src/portfolio/sds_ready.py`
- Modify: `src/portfolio/cli.py`, `README.md` (one section)
- Test: `tests/test_sds_ready.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `onboard._run_checks`, Tasks 2–9.
- Produces:
  - `run_all_checks(repo: Path, gh, gh_read, fetch=None) -> list[dict]` — `onboard._run_checks`
    with `measure_unscoped=True` threaded to the factory checks, then `check_decisions`,
    `check_declaration`, `check_cred_inventory`, `check_branch_settings`,
    `check_behavior_evidence`, `check_landing_inert`.
  - `run(repo: Path, gh=_gh, gh_read=_gh_read) -> int`
  - Exit codes: `0` ready, `1` not ready, `2` the kit couldn't run (including no GitHub origin),
    `3` refused.
  - Writes `onboarding_dir(name) / "HANDOFF.md"` when `verdict.repository` is non-empty, and
    `onboarding_dir(name) / "TASKS.md"` when `verdict.devon` is non-empty; deletes either file
    when its list is empty, so a stale handoff never survives. Writes `readiness.json` (the check
    list) every run. Prints the summary to stderr.

`onboard._run_checks` builds the factory checks through `run_factory_checks`; give it a
`measure_unscoped: bool = False` parameter that it passes through, so `portfolio onboard` is
unchanged.

- [ ] **Step 1: Write the failing tests** (all `gh`, `gh_read` and App Brain calls faked;
  `portfolio_env` set)

```python
def test_no_github_origin_exits_2_and_writes_nothing(make_repo, portfolio_env): ...
def test_gaps_write_handoff_and_exit_1(...): ...
def test_ready_removes_stale_handoff_and_exits_0(...): ...
def test_redeploys_exits_3(...): ...
def test_cli_wires_sds_ready(): assert main(["sds-ready", str(repo)]) in (0, 1, 2, 3)
```

- [ ] **Step 2–4: Run, implement, run** with `make check`
- [ ] **Step 5: Live acceptance, read-only.** From the worktree's venv, run
  `portfolio sds-ready ~/Projects/RentVSBuyGA` with `PORTFOLIO_HOME` pointed at a scratch
  directory. Expected: exit 1; `HANDOFF.md` lists `code.onboarded`, `ci.executed`,
  `profile.declared`, `git.current`, `deps.dependabot` and `sds.behavior_evidence`; `TASKS.md`
  lists `sds.decisions`, and `factory.pat_access` unless it lands in the environment list; any
  check that reports a missing credential appears under environment, not in either file; the
  summary's phase is `not-started`. Paste the summary into the pull
  request description. Record any difference from this list as a finding before merging.
- [ ] **Step 6: Commit** — `feat: portfolio sds-ready, the SDS readiness command`

---

## After this plan

- Point the orchestrator runbook's "Check the result" section at `portfolio sds-ready`.
- Write plan 2 (onboarding procedure: actions 1–7, `--confirm`, the onboarding record) against
  `Verdict` and `GUIDANCE`. The spec's "caller has exactly the `work_unit_id` input" check belongs
  there: onboarding writes the caller from the template, so plan 2 adds the check beside the
  action that makes it true.
- Write plan 3 (canary) against plan 2's resumable steps.
