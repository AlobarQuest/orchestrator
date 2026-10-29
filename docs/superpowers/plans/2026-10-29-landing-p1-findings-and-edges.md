# Landing layer, Part 1: findings and edges — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pull request a lander holds for a day reaches Devon through alert-hub, each lander's
outbound surface is pinned by one test, and no member repository can arm auto-merge.

**Architecture:** The landers keep their dead-man checks (already routed to alert-hub, channel
`ed77a6f9-…`), because a held PR is the normal state of most passes: 94% of the last 300 inert passes
held something, with a current streak of 166, so a `/fail` per held pass would turn the check red
once and then say nothing new. Instead a shared `lander.held_alerts` module keeps, per lane, when it
first saw each PR in an alerting status, and sends one keyed alert-hub alert per PR held past 24
hours, resolved when it stops being held. The lander clients narrow to POST, the estate lander gets
its own read-only change-manager client, a new architecture test tabulates both landers' surfaces,
and `membership_check` gains a "no auto-merge" row.

**Tech Stack:** Python 3 (`.python-version`), pytest, Bash launchers, `alert_hub_client.KeyedAlerts`,
GitHub REST.

**Spec:** `docs/superpowers/specs/2026-10-29-landing-layer-design.md`, Part 1 and "What was
measured". This plan covers Part 1 only. Parts 2 to 5 each get their own plan after the previous part
lands.

## Global constraints

- Work in `.worktrees/landing-design` with its own `.venv` and database `orchestrator_test_landing_design`
  (`docs/operations/local-development.md`). Never edit, check out or run `uv` in the main tree.
- Never put a secret in a prompt, tool argument or transcript. Every parsed `bws` call passes
  `--color no`.
- Don't poll GitHub without a pause (`--watch --interval 30`).
- Every new file gets a row in `layers.toml`; `src/lander/**`, `tests/lander/**` and the lander
  packages are already covered by `[landing]`.
- A module-level string collection used in membership tests is registered in `VOCABULARY_REGISTRY` or
  marked `# not-a-vocabulary: <reason>` (`test_cross_boundary_vocabulary.py`).
- A file importing an HTTP client is in `OUTBOUND_ALLOWLIST` with a reason (`test_wsp21_invariant_scan.py`).
- Nothing here changes `src/orchestrator/`, so no image release or migration is needed.
- `make check` is green before each commit that touches logic.

## Review focus

1. **A report run sends alerts.** A person running a lander by hand without `--submit` expects
   nothing sent and no state written. Pinned in Task 1.
2. **A PR that flips between `held` and `unreadable` resets its clock.** #506 alternates between
   `landing_checks_not_clean` and `landing_mergeability_unknown` every pass; its first sighting must
   survive a status change inside the alerting set. Pinned in Task 1.
3. **A pass that couldn't read anything resolves every alert.** A pass that exits before producing
   outcomes (policy or records unreadable) must not sync, or every open alert resolves. Pinned in
   Task 1.
4. **A predicate widens and the table follows silently.** The edge test must fail when a lander's
   predicate accepts a corpus path its row doesn't list, proven by a control. Pinned in Task 2.
5. **An unreadable repository setting reads as "auto-merge off".** A GitHub read that fails is a gap
   (exit 3), never a pass. Pinned in Task 3.

---

### Task 1: A PR held for a day raises one alert

**Files:**
- Create: `src/lander/held_alerts.py`
- Modify: `src/inert_lander/cli.py` (`run`), `src/estate_lander/cli.py` (`run`)
- Modify: `scripts/run-inert-landing.sh`, `scripts/run-estate-landing.sh` (fetch the alert token on
  `--submit` only)
- Modify: `tests/architecture/test_wsp21_invariant_scan.py` (`OUTBOUND_ALLOWLIST` entry, if
  `held_alerts.py` imports `httpx`)
- Test: `tests/lander/test_held_alerts.py`, `tests/inert_lander/test_inert_lander.py`,
  `tests/estate_lander/test_estate_lander.py`

**Interfaces:**
- Consumes: `lander.core.Outcome` (`repository`, `number`, `status`, `detail`);
  `alert_hub_client.Alert`, `KeyedAlerts`, `alert_key`, `AlertHubError`.
- Produces, in `src/lander/held_alerts.py`:
  - `ALERTING_STATUSES: Final = frozenset({"held", "unreadable", "error"})`, marked
    `# not-a-vocabulary:` (lander report statuses, not exchanged).
  - `HELD_ALERT_HOURS: Final = 24`
  - `TOKEN_ENV: Final = "SDS_BRIDGE_ALERT_TOKEN"`; `STATE_DIR_ENV: Final = "LANDER_STATE_DIR"`;
    default state directory `~/Library/Application Support/sds/<lane>` where `<lane>` is
    `inert-landing` or `estate-landing`.
  - `held_key(repository: str, number: int) -> str`, returning
    `alert_key("landing-held", repository.lower(), str(number))`.
  - `firing(outcomes: Iterable[Outcome], first_seen: Mapping[str, str], now: datetime) -> tuple[dict[str, Alert], dict[str, str]]`:
    pure. Returns the alerts to fire (each PR in `ALERTING_STATUSES` first seen at least
    `HELD_ALERT_HOURS` before `now`) and the new first-seen map (ISO-8601 UTC strings, keyed by
    `held_key`), keeping an existing first sighting while the PR stays in the set and dropping it
    when the PR leaves the set or isn't in `outcomes`.
  - `sync_held_alerts(lane: str, outcomes: Sequence[Outcome], *, now: datetime, environ: Mapping[str, str] = os.environ, transport: httpx.BaseTransport | None = None) -> str | None`:
    reads `held-since.json` from the state directory, calls `firing`, syncs `KeyedAlerts`
    (state file `alerts.json` beside it, `user_agent="<lane>/1 (+AlobarQuest/orchestrator)"`,
    `resolved_title="Pull request no longer held"`), then writes `held-since.json` aside-and-rename.
    Returns `None` on success, or one line saying why it could not (a missing token, an unusable
    state directory, an `AlertHubError`). Never raises for those.
- Alert text: title `Pull request held: <repo>#<n>`; message
  `<status> for <h> hours: <detail>`; link `https://github.com/<repo>/pull/<n>`.

- [ ] **Step 1: Write the failing tests** in `tests/lander/test_held_alerts.py`, driving `firing`
  with fixed datetimes and `sync_held_alerts` with an `httpx.MockTransport` and `tmp_path`:
  - `test_a_pr_held_for_less_than_the_threshold_fires_nothing` (first seen 23 h ago: no alert, its
    sighting kept)
  - `test_a_pr_held_past_the_threshold_fires_one_keyed_alert` (first seen 24 h ago: one alert under
    `held_key("AlobarQuest/orchestrator", 506)`, title `Pull request held: AlobarQuest/orchestrator#506`)
  - `test_a_status_change_inside_the_set_keeps_the_first_sighting` (`held` then `unreadable`)
  - `test_a_pr_that_leaves_the_set_or_the_pass_is_forgotten` (`landed`, `waiting`, absent)
  - `test_deliberate_waiting_exception_and_settled_never_alert`
  - `test_the_first_sighting_survives_across_passes` (two `sync_held_alerts` calls through the state
    file; the second fires, the first doesn't; alert-hub receives one `firing` POST)
  - `test_a_resolved_pr_sends_resolved` (fired, then absent: one `resolved` POST)
  - `test_a_missing_token_or_unusable_state_dir_is_reported_not_raised`
- [ ] **Step 2: Run them and see them fail.** `.venv/bin/pytest tests/lander/test_held_alerts.py -q`
- [ ] **Step 3: Implement `src/lander/held_alerts.py`** to the interfaces above.
- [ ] **Step 4: Write the failing CLI tests**, in each lander's test file:
  - `test_a_submit_pass_syncs_held_alerts_with_its_outcomes`
  - `test_a_report_run_sends_no_alerts_and_writes_no_state`
  - `test_a_pass_that_produced_no_outcomes_does_not_sync` (the early exit-2 paths)
  - `test_an_alert_failure_exits_one_after_printing_the_report` (the pass's report still prints;
    the exit code is 1 so the dead-man check pings `/fail`)
- [ ] **Step 5: Wire each `run`**: after `report(...)`, only when `submit` is true and outcomes were
  produced, call `sync_held_alerts(<lane>, outcomes, now=datetime.now(UTC))`; on a returned reason,
  print `alert-hub: <reason>` and return 1.
- [ ] **Step 6: Update both launchers** to fetch `SDS_BRIDGE_ALERT_TOKEN` from BWS
  `b415d03b-f92d-46b1-8a9d-b4f10103df49` with `SDS_IDENTITY`, as `run-bump-proposer.sh:196-199`
  does, only when `--submit` is among the arguments; a failed fetch is `FATAL … exit 1`. Add one
  sentence to each header naming the alert. Exit codes are unchanged.
- [ ] **Step 7: Run** `.venv/bin/pytest tests/lander tests/inert_lander tests/estate_lander tests/scripts -q`,
  then `make check`. Expected: green.
- [ ] **Step 8: Commit.** `git commit -m "landers: a pull request held for a day raises one alert-hub alert"`

### Task 2: One test states each lander's surface

**Files:**
- Modify: `src/estate_lander/orchestrator_client.py:63-64`, `src/inert_lander/orchestrator_client.py`
  (the same `_permits`)
- Create: `src/estate_lander/change_manager.py`
- Modify: `src/estate_lander/cli.py:56-57` (use the new client)
- Modify: `tests/architecture/test_wsp21_invariant_scan.py` (`OUTBOUND_ALLOWLIST` entry for the new
  client)
- Create: `tests/architecture/test_landing_edges.py`
- Modify: `tests/estate_lander/test_client_surface.py`, `tests/inert_lander/test_client_surface.py`
- Modify: `layers.toml` (place `tests/architecture/test_landing_edges.py` in `[landing]`)
- Modify: `docs/operations/landing-lanes.md` (the SYSTEM-confinement residual)

**Interfaces:**
- Produces: both `_permits(method, path)` become
  `is_allowed_read(path) if method == "GET" else method == "POST" and is_allowed_write(path)`.
- Produces: `estate_lander.change_manager.ChangeManagerClient(token: str, *, base_url: str = DEFAULT_BASE_URL, transport: httpx.BaseTransport | None = None)`,
  a context manager on `estate_clients.confined.ConfinedClient` with one method,
  `records() -> list[dict[str, Any]]`, sending exactly what
  `change_proposer.change_manager.ChangeManagerClient.records` sends today
  (`GET /api/items`, `params={"source": DEPLOY_SOURCE}`), and `ChangeManagerError`. It permits
  `GET /api/items` and nothing else; `is_allowed_write` returns `False` for every path. The estate
  lander keeps importing the constants `DEPLOY_SOURCE` and `BOT_CHANGE_CLASS`, not the client.
- Produces: `tests/architecture/test_landing_edges.py` with `EDGES: dict[str, Edge]`, the same
  `Edge` shape as `tests/architecture/test_bridge_edges.py` (import it, don't copy it).

The table, from the 2026-10-29 recon. Confirm each cell against the predicates and launchers; a
mismatch is a finding to report, not to paper over:

| Lane | orchestrator | change-manager | GitHub | Bearers |
|---|---|---|---|---|
| `estate_lander` | `GET /api/v1/estate-pr-merge-admission`; `POST /api/v1/estate-pr-merge`, `/api/v1/estate-pr-branch-update`, `/api/v1/held-pr-close` | `GET /api/items` | none | `221a48d5-…`, `314f276d-…` |
| `inert_lander` | `GET /api/v1/inert-pr-merge-admission`; `POST /api/v1/inert-pr-merge`, `/api/v1/inert-pr-branch-update`, `/api/v1/held-pr-close` | `GET /api/landing-policy` (trace its reader) | the PR listing its `PullRequestSource` reads | `221a48d5-…`, `314f276d-…`, `gh auth token` |

Add `b415d03b-…` to both rows' bearers after Task 1. Use full UUIDs from the launchers.

- [ ] **Step 1: Write the failing tests.**
  - In each `test_client_surface.py`: `test_put_patch_and_delete_on_a_write_path_are_refused`.
  - In `tests/estate_lander/test_client_surface.py`:
    `test_the_change_manager_client_reads_items_and_writes_nothing`.
  - In `tests/architecture/test_landing_edges.py`, mirroring `test_bridge_edges.py`:
    `test_each_lander_accepts_exactly_its_routes_among_the_corpus` (corpus: every listed path, plus
    `/api/v1/work-units/44/pr-merge`, `/api/v1/work-units/44/dispatch`, `/api/v1/observations`,
    `/api/items/44/approve`, `/api/deploy-changes`, each listed path with a trailing `/` and an `x`
    suffix, and each over GET, POST, PUT, PATCH and DELETE),
    `test_each_launcher_fetches_exactly_its_bearers`,
    `test_the_table_is_the_landing_layer_population` (keys equal the lander programs in
    `layers.toml`'s `[landing]`), and
    `test_control_a_widened_predicate_fails_the_table` (monkeypatch one write predicate to accept
    `/api/v1/work-units/44/pr-merge`).
- [ ] **Step 2: Run them and see the surface tests fail.**
  `.venv/bin/pytest tests/architecture/test_landing_edges.py tests/estate_lander tests/inert_lander -q`
- [ ] **Step 3: Implement** the two predicates and the estate lander's client, and switch
  `estate_lander/cli.py` to it.
- [ ] **Step 4: Prove the edge test fires.** Mutate one predicate in `src/` by hand (accept a
  trailing slash), confirm the test fails, restore it, clearing `__pycache__` around the mutation.
- [ ] **Step 5: Record the residual.** In `landing-lanes.md`, one paragraph: the landers' SYSTEM
  bearer has no server-side route confinement, so it could reach `pr-merge` and every other SYSTEM
  route; confining it needs a dedicated lander identity (a security-standards registry change),
  which is a separate decision.
- [ ] **Step 6: Run `make check`.** Expected: green.
- [ ] **Step 7: Commit.** `git commit -m "landers: POST-only writes, the estate lander's own read client, one edge table"`

### Task 3: No member repository can arm auto-merge

**Files:**
- Create: `src/membership_check/auto_merge.py`
- Modify: `src/membership_check/consistency.py` (`check`), `src/membership_check/cli.py`
- Modify: `tests/architecture/test_wsp21_invariant_scan.py` (if the new module imports `httpx`)
- Test: `tests/membership_check/test_consistency.py`, `tests/membership_check/test_auto_merge.py`
- Modify: `docs/operations/onboarding-a-target.md:158`
- In `~/Projects/project-standards` (its own branch, pull request and venv): the `sds_checks.py`
  branch-settings check and its tests

**Interfaces:**
- Produces, in `src/membership_check/auto_merge.py`:
  - `ARMING_MARKERS: Final = ("gh pr merge --auto", "enablePullRequestAutoMerge", "enable-pull-request-automerge")`,
    marked `# not-a-vocabulary:`.
  - `@dataclass(frozen=True) class AutoMergeState: allowed: bool | None; arming_workflows: tuple[str, ...] | None`
    (`None` means the read failed).
  - `arming(text: str) -> bool`: true when the workflow text contains any marker.
  - `class GitHubAutoMergeSource`, mirroring `membership_check.declaration.GitHubDeclarationSource`:
    `state(repository: str) -> AutoMergeState`, reading `GET /repos/{r}` (`allow_auto_merge`) and
    every file under `.github/workflows/` on the default branch.
- Produces: `check(...)` gains `auto_merge: Mapping[str, AutoMergeState]`, one entry per member
  repository. Each `allowed is True` or non-empty `arming_workflows` is a disagreement naming the
  repository and the file; each `None` is a gap (exit 3), never a pass.

- [ ] **Step 1: Write the failing tests.**
  - `test_auto_merge.py`: `test_every_retired_auto_merge_rule_is_arming`, parametrised over
    `tests/fixtures/auto-merge-rules/*.yml`; `test_no_rollout_workflow_is_arming`, parametrised over
    `tests/fixtures/deploy-workflows/*.yml` (the control).
  - `test_consistency.py`: `test_a_member_allowing_auto_merge_is_a_disagreement`,
    `test_a_member_with_an_arming_workflow_is_a_disagreement`,
    `test_an_unreadable_setting_is_a_gap_not_a_pass`, and
    `test_a_member_with_auto_merge_off_and_no_arming_workflow_agrees`.
- [ ] **Step 2: Run them and see them fail.** `.venv/bin/pytest tests/membership_check -q`
- [ ] **Step 3: Implement** `auto_merge.py`, the `check` row and the CLI read.
- [ ] **Step 4: Fix the onboarding doc.** `onboarding-a-target.md:158` says to turn `allow_auto_merge`
  off: nothing in the SDS arms auto-merge since ADR-0038, and the setting only lets any writer arm
  a merge.
- [ ] **Step 5: Flip project-standards' check.** First grep `~/Projects` for `auto-merge-off` and
  `auto_merge_off` and list every reader in the PR body. Then `sds.auto-merge-off` becomes
  `sds.auto-merge-on`: auto-merge enabled is the violation. Update its tests; run its gate in its own
  worktree and venv.
- [ ] **Step 6: Run `make check`** here. Expected: green.
- [ ] **Step 7: Commit** each repository's change.
  `git commit -m "membership_check: no member repository may arm auto-merge"`

### Task 4: The stale text matches the code

**Files (amendments are appended under each ADR's existing amendments, dated 2026-10-29):**
- `docs/decisions/0019-*.md`: record Increment 6 (the branch-update act ADR-0045 cites), and state the
  `live_estate` window is 00:00 to 23:59 for the build phase (ADR-0012 amendment 1), 02:00 to 06:00
  once the SDS is in operation.
- `docs/decisions/0020-*.md`: the same window statement, and that ADR-0019 Increment 3 overtook its
  "anything that deploys is out of scope" boundary.
- `docs/decisions/0024-*.md`: acting (`qualifies_for_branch_update`) and reporting
  (`freshness_derived_refusals`) are separate criteria and must never be folded together.
- `docs/decisions/0025-*.md`: the estate lander sets factory records aside (`_LANE_CLASSES`).
- `docs/operations/landing-lanes.md`: `enforce_admins` is on for factory-runner, change-manager and
  brain (ADR-0038); "the cascade" means the inert lane.
- `docs/operations/driving-a-unit.md` #141: every dependency-update known-good pattern allows
  `github.pr.merge`.
- `docs/superpowers/specs/2026-10-29-landing-layer-design.md`, Part 1 "Findings": the landers keep
  their dead-man checks and alert per held PR, with the measurement in this plan's Architecture.

- [ ] **Step 1: Read each passage and its code** before changing it; a passage the code doesn't
  contradict stays.
- [ ] **Step 2: Write the amendments and edits.**
- [ ] **Step 3: Commit.** `git commit -m "docs: landing ADRs and operations pages match the code"`

### Task 5: Ship, install and prove

- [ ] **Step 1: Review.** Run `/code-review` on the branch diff; fix what holds up.
- [ ] **Step 2: Land both pull requests.** Open this branch's pull request (plan and Part 1) and
  project-standards'. Watch each with `gh pr checks --watch --interval 30`, merge on green, and
  confirm `state == MERGED`.
- [ ] **Step 3: Install.** Between inert passes (not at :35) and outside 02:15 to 05:15, `git pull`
  in the main tree. No new console script, so no `uv sync`. The launchers pick up the change by
  path; no installer changes.
- [ ] **Step 4: Turn auto-merge off.** For each member repository `membership_check` now reports
  (measured 2026-10-29: infraops-mcp-server, security-standards, project-standards, intent-packages,
  orchestrator, factory-runner), set `allow_auto_merge` to false with
  `gh api -X PATCH repos/<r> -F allow_auto_merge=false`, then run `scripts/run-membership-check.sh`
  and confirm no auto-merge row remains.
- [ ] **Step 5: Prove the alert.** After the next scheduled inert pass, read alert-hub's open alerts
  and confirm one `landing-held:` alert each for orchestrator#506 and #508 (both held since
  2026-10-25), and that the lane log shows a normal report. If either PR has closed meanwhile,
  confirm instead that no alert fired for it.
- [ ] **Step 6: Clean up.** Check the main tree's `git status`, then remove the worktree, its test
  database and the merged branch, after confirming the PR merged.
