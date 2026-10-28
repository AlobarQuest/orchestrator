# Bridge layer, Part 4: holds — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dependency Devon holds can't move past its ceiling by any path: no lane proposes it, no
lander or factory merge lands it, and a held Dependabot pull request is closed with a comment naming
the hold.

**Architecture:** A hold is a row in the SDS member record (`sds_member_holds`), staged and lifted
through the existing member-proposal flow and confirmed on `/review`. One pure matcher decides
"does this bump exceed a hold" in each process boundary (an orchestrator copy for the in-process
admissions, a lane copy beside `sds_members`), held equal by a test. The three merge admissions
(inert landing, estate landing, the factory's pull-request merge) refuse with one shared exception
code; a new close act, bounded by the same admission, closes the held pull request with a comment.
The bump proposer and the work carrier refuse a held bump earlier. A test classifies every merge and
propose path so a new one must declare whether it honours holds.

**Tech Stack:** Python 3 (`.python-version`), FastAPI, SQLAlchemy and Alembic (Postgres), pytest,
GitHub REST through the dispatch App.

**Spec:** `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`, Part 4 (with "Decisions Devon
made" item 4, "Anti-goals", "Constraints", "Order"). Parts 1–3 shipped as #533, #537, #538.

## Global constraints

- Work in `.worktrees/bridge-p4` with its own `.venv` and `orchestrator_test_bridge_p4` database.
  Never edit, check out or run `uv` in the main tree; another session may be working in its own
  worktree on `src/work_carrier/` and the rotation proposer (ADR-0055 amendment 15 increment 4), so
  keep this plan's carrier change to the one refusal in Task 6 and rebase before shipping.
- Never put a secret in a prompt, tool argument or transcript.
- Only `services/membership/proposals.py` writes member tables; reads return frozen views
  (`test_membership_writers.py`). Holds are written only when a human confirms a proposal.
- SQL `IN (...)` CHECKs use `_in_list`, never `f"{VOCAB!r}"`; the migration pins its own copies of
  vocabularies; revision id at most 32 characters.
- Word guards apply under `src/orchestrator/` (no `deploy`, `dispatch`, `merge_to_main`, etc. in
  identifiers or bare strings).
- New routes join `test_scope_guards.py`'s inventories and the idempotency matrix; new
  `services/landing/` modules are classified in `test_landing_direction.py`; httpx importers join
  `OUTBOUND_ALLOWLIST`; every new file gets a `layers.toml` row.
- Every App call stays bounded by an admission or allowlist check (`docs/operations/credentials.md`).
- Fail closed: a version or dependency list that can't be read, in a repository that has holds, is
  never treated as "not held".
- `make check` green before each commit that touches logic.

## Rulings made while planning

1. **A hold is a new table, `sds_member_holds`,** keyed `(member_id, ecosystem, directory,
   dependency)` with `ceiling`, `reason`, `set_by`, `set_at`. It's staged as a new optional field on
   the existing `MemberChange` (`holds: list[HoldChange]`, each `place` or `lift`), so no new write
   route is needed. A lift mirrors grant withdrawal: only an existing hold can be lifted; the row is
   deleted on confirm and the event records it. Cost if wrong: a later "hold history" view reads
   events instead of rows.
2. **Ecosystem uses Dependabot's spelling** (`npm_and_yarn`, `pip`, `uv`, `docker`,
   `github_actions`, …), unconstrained by a CHECK because Dependabot owns the vocabulary. The
   factory path maps tooling names (`npm` → `npm_and_yarn`). Cost if wrong: a hold typed with a
   tooling name matches nothing; validation refuses the three tooling spellings that differ.
3. **Directory defaults to `/`.** A path that can't read the directory (the factory path, a branch
   it can't parse) matches a hold on any directory for that repository, ecosystem and dependency.
   Cost if wrong: a hold on `/sub` also stops a root bump of the same dependency on that path.
4. **"Beyond the ceiling" is a numeric release comparison that fails closed.** A version parses to
   its leading dotted numeric release (`v` stripped; pre-release and build parts ignored for the
   release tuple); a bump is held when its release is greater than the ceiling's, or when either
   can't be parsed. The comparator is shared by npm, pip and uv. Cost if wrong: a pre-release of the
   ceiling's own release (e.g. `2.0.54rc1` vs ceiling `2.0.54`) is allowed.
5. **A grouped update is closed when any member exceeds a hold.** The comment names the held member;
   Dependabot reopens the group when its set changes. A grouped pull request whose dependency list
   can't be read, in a repository with holds, is refused (not closed). Cost if wrong: the group's
   other members wait for the next group change.
6. **The hold refusal is an exception** (it clears only when Devon lifts the hold), coded
   `landing_version_ceiling_exceeded` (never "held": that word is a lander status meaning finding).
   An unreadable list, in a repository with holds, is `landing_dependency_list_unreadable` and is
   a **reported finding**: an unreadable input stops loudly (spec anti-goal), it never sits refused
   with the outcome check green. Cost if wrong: an alert on a grouped update the lane can't read.
7. **One close act for both landers**: `POST /api/v1/held-pr-close`, which re-composes the lane's
   admission inside its transaction and closes only when the composed refusals contain the hold
   code, the author is `dependabot[bot]` and the repository is in that lane's population. It posts
   one comment, then sets the pull request closed, through the dispatch App (`pull_requests: write`
   suffices for both). Its record is one `Event` (`landing.held_pull_request_closed`, idempotency key
   `held-pr-close:<repository lowercased>#<number>`); a replay finds it and touches GitHub no more.
   Cost if wrong: a lane-specific close need later becomes a branch inside one route rather than its
   own route.
8. **Fork upstream syncs are exempt** (author `FORK_SECURE_UPSTREAM_SYNC`): they are a release of
   someone else's code, not a dependency bump.
9. **Member proposals join the `/review` queue** (`pending_decisions.py`), so a staged hold or lift
   is seen without the CLI's link. Cost if wrong: one more queue kind.
10. **The factory merge honours holds by `(repository, dependency, to_version)`** read from the
    revision's `profile_fields`, with the ecosystem mapped from the package's tooling (Ruling 2) and
    any directory (Ruling 3).

## Review focus

1. **A path that ignores holds.** Every merge path in `MERGE_EXEMPT_PATHS` and every propose path is
   classified as honouring holds or not a bump path; a new one fails the build. Pinned in Task 7.
2. **A grouped update hiding a held dependency.** Pinned in Tasks 2 and 3.
3. **An unparseable version or dependency list.** Refused, never landed. Pinned in Tasks 2 and 3.
4. **A stale confirm.** A hold that changes between page render and confirm is caught by the digest.
   Pinned in Task 1.
5. **Closing a pull request the lane doesn't own.** The close act refuses anything its re-composed
   admission doesn't refuse for the hold. Pinned in Task 4.

---

### Task 1: Holds in the member record

**Files:**
- Modify: `src/orchestrator/persistence/models.py` (`SdsMemberHold`, relationship on `SdsMember`),
  `src/orchestrator/services/membership/changes.py` (`HoldChange`, `MemberChange.holds`),
  `src/orchestrator/services/membership/proposals.py` (validate, apply, `proposal_preview`, event
  payload), `src/orchestrator/services/membership/members.py` (`HoldView`, `MemberView.holds`),
  `src/orchestrator/api/schemas/membership.py`, `templates/member_proposal.html`,
  `src/orchestrator/services/reporting/pending_decisions.py` (Ruling 9), `src/sds_members/client.py`
  (`Member.holds`, `Hold`), `src/sds_members/cli.py` docs for the TOML shape
- Create: `migrations/versions/0043_member_holds.py`
- Test: `tests/services/test_member_holds.py`, `tests/web/test_member_proposals.py`,
  `tests/api/test_membership_api.py` (pinned fields), `tests/architecture/test_membership_writers.py`
  (`WRITE_MODELS`, `ALL_MODELS`, `TABLE_NAMES`), `tests/sds_members/test_client.py`,
  `tests/services/test_membership_vocabulary.py`

**Interfaces:**
- `HoldChange{ecosystem: str, directory: str = "/", dependency: str, ceiling: str | None, action:
  Literal["place", "lift"]}`; `place` needs a `ceiling` that Task 2's `release()` parses (a typo'd
  ceiling would otherwise breach every bump of that dependency with no signal); `lift` needs an
  existing hold. A place for an existing key replaces its ceiling. The hold's `reason` is the
  enclosing `MemberChange.reason`; `set_by` is the confirming person.
- `HoldView{ecosystem, directory, dependency, ceiling, reason, set_by, set_at}`;
  `MemberView.holds: tuple[HoldView, ...]`; wire key `holds` on every member (empty list when none).
- Lane side: `sds_members.client.Hold(ecosystem, directory, dependency, ceiling)`,
  `Member.holds: tuple[Hold, ...]`; a member without the key is `MembersUnreadable` (fail closed).

- [ ] **Step 1: Write the failing tests.** `test_a_confirmed_hold_is_read_back`; `test_a_lift_removes_it`;
  `test_lifting_a_hold_that_does_not_exist_is_refused`; `test_a_place_needs_a_parseable_ceiling`;
  `test_tooling_spellings_are_refused` (`npm` refused; `npm_and_yarn` accepted);
  `test_a_hold_changed_after_render_fails_the_confirm_digest`; `test_only_a_human_confirms_a_hold`;
  the members response carries `holds`; the lane client parses it and refuses a member without it;
  a staged member proposal appears in the `/review` queue.
- [ ] **Step 2–4:** fail, implement (migration `0043_member_holds` with upgrade and downgrade, run
  against the worktree's Postgres both ways), pass, `make check`, commit.

### Task 2: One hold matcher, two copies

**Files:**
- Create: `src/orchestrator/services/membership/holds.py`, `src/sds_members/holds.py`
- Test: `tests/services/test_hold_matcher.py`, `tests/sds_members/test_holds.py`, an agreement test
  running both copies over one corpus

**Interfaces:**
- `release(version: str) -> tuple[int, ...] | None` (Ruling 4).
- `Bump(ecosystem, directory: str | None, dependency, to_version)`.
- `exceeded(holds, repository, bumps) -> tuple[HoldBreach, ...]`, where an empty result means
  admit; a bump whose `directory` is `None` matches any directory; an unparseable version on a held
  dependency is a breach.
- `dependencies_of(commit_message: str) -> tuple[Bump, ...] | None` reads Dependabot's
  `updated-dependencies` block (`dependency-name`, `dependency-version`, and the directory when
  present), returns `None` when there is no readable block.
- `bumps_of(title: str, commit_message: str, ecosystem: str) -> tuple[Bump, ...] | None` combines
  them: a single update takes its name from the block and its `to_version` from the title (the
  title is the fresh source, per `estate_landing_admission.py`); a grouped update takes both from
  the block; `None` when neither yields a readable list.
- `TOOLING_TO_DEPENDABOT = {"npm": "npm_and_yarn", "pip": "pip", "uv": "uv"}` and
  `ecosystem_of_package_id(package_id) -> str | None` (tooling segment of
  `<repo>-<tooling>-<dep>`, mapped to Dependabot's spelling) live in the orchestrator copy for
  Task 5; orchestrator services can't import the lane library `standing_identity`. Both copies hold
  the mapping; the agreement test pins them equal.

- [ ] **Step 1: Write the failing tests.** release parsing (`v1.2.3`, `2.0.54`, `2.0.55rc1`, `1!2.0`,
  `latest` → None); at, below and above the ceiling; directory `None` matches any; a grouped
  message with one held member breaches; a message with no block returns `None`; scoped npm names
  (`@types/node`); the agreement test.
- [ ] **Step 2–4:** fail, implement, pass, `make check`, commit.

### Task 3: The landers refuse a held bump

**Files:**
- Modify: `src/orchestrator/services/landing/interfaces.py` (codes
  `LANDING_VERSION_CEILING_EXCEEDED`, `LANDING_DEPENDENCY_LIST_UNREADABLE`; `PullRequestCommit`
  gains the commit message), `src/orchestrator/services/landing/terms.py` (the pure hold term),
  `inert_landing_admission.py::_remote_terms`, `estate_landing_admission.py::_remote_terms`, the
  GitHub gateway read of the head commit's message, `src/inert_lander/cli.py` and
  `src/estate_lander/cli.py` (`exception=` sets)
- Test: the two admissions' tests, both landers' tests, the substring guards

**Interfaces:**
- The admissions read holds in-process through `members.py` (the `MemberRecordGrants` pattern) and
  pass them to the pure term, which uses Task 2's matcher. Fork upstream syncs skip the term
  (Ruling 8). A repository with no holds never reads the commit message.

- [ ] **Step 1: Write the failing tests.** single held bump refused with the ceiling code; grouped
  update with one held member refused; below the ceiling admitted; unreadable list in a held
  repository refused with the unreadable code; a repository without holds unaffected; a fork sync
  unaffected; the landers report the code as an exception, not a finding.
- [ ] **Step 2–4:** fail, implement, pass, `make check`, commit.

### Task 4: Close a held pull request with a comment

**Files:**
- Create: `src/orchestrator/services/landing/held_close.py` (act), route in
  `src/orchestrator/api/routes/landing.py`
- Modify: the GitHub gateway (`POST /repos/{r}/issues/{n}/comments`, `PATCH /repos/{r}/pulls/{n}`
  with `state=closed`), both lander clients and `LandingClient` (a fourth method), both landers'
  passes (call close after a refusal whose codes include the ceiling code), `test_scope_guards.py`,
  the idempotency matrix, `test_landing_direction.py`
- Test: `tests/services/test_held_close.py`, route tests, both landers' tests

**Interfaces:**
- `POST /api/v1/held-pr-close {lane: "inert" | "estate", repository, pull_request_number,
  idempotency_key}`: re-composes that lane's admission in its transaction; closes only if the
  refusals contain `landing_version_ceiling_exceeded`, the author is `dependabot[bot]` and the
  repository is in the lane's population; posts one comment naming each held dependency, its
  ceiling, and one fixed sentence saying a person lifts the hold on `/review`; replay is idempotent
  through the `Event` (Ruling 7): a second call comments nothing. Responds with what it did.
- The comment is a public PR comment: dependency, ceiling and the fixed sentence only — never the
  hold's `reason`, `set_by`, any actor id, name or credential (the evidence-pack redaction rule).
- The route takes the same role as the lane's merge route. The lander calls it whenever the ceiling
  code is among the refusals, even beside other refusals (a failing held pull request still closes).

- [ ] **Step 1: Write the failing tests.** closes and comments once for a held bump; refuses a pull
  request whose admission doesn't carry the ceiling code; refuses a non-Dependabot author and a
  repository outside the population; replay comments nothing; the lander calls close only after
  that refusal and reports `closed`/`would-close` (dry run sends nothing).
- [ ] **Step 2–4:** fail, implement, pass, `make check`, commit.

### Task 5: The factory merge refuses a held bump

**Files:**
- Modify: `src/orchestrator/services/landing/pr_merge_admission.py` (a hold term reading the unit's
  revision `profile_fields` and `ecosystem_of_package_id` from Task 2's orchestrator copy; its own
  code)
- Test: `tests/services/test_pr_merge_admission.py`

- [ ] **Step 1:** a dependency-update unit whose `to_version` exceeds a hold is refused; below is
  admitted; a non-dependency-update unit is unaffected; an unparseable version is refused.
- [ ] **Step 2–4:** fail, implement, pass, `make check`, commit.

### Task 6: The lanes refuse earlier

**Files:**
- Modify: `src/bump_proposer/cli.py` (`_consider`, before `_route`: a bump past a hold is skipped
  with a `ceiling-exceeded` detail and no alert key, so a previous `dependency-bump:` alert resolves),
  `src/work_carrier/prepare.py` (`_policy_disagreement`: a record whose package bumps past a hold is
  refused)
- Test: the proposer's and carrier's tests

- [ ] **Step 1:** proposer skips a held bump (no create, no propose, no alert); proposer still acts
  below the ceiling; carrier refuses a record for a held bump; both fail closed on an unreadable
  member record (already exit 2).
- [ ] **Step 2–4:** fail, implement, pass, `make check`, commit.

### Task 7: Every path is classified

**Files:**
- Create: `tests/architecture/test_hold_paths.py`
- Modify: docs — an ADR-0058 amendment (holds in the member record) and ADR-0028 amendment (holds
  honoured by every bump path), `docs/operations/membership.md` (how to stage and lift a hold),
  `docs/operations/landing-lanes.md`, the CLAUDE.md bullets for holds (the rule as it stands)

**Interfaces:**
- `HOLD_PATHS`: every entry of `MERGE_EXEMPT_PATHS` plus every propose path (bump proposer, work
  carrier, change proposer, intake, dispatch) classified as `honours` (with the function that applies
  the term), `covered_by` (with the gate that covers it: intake by the carrier's refusal in Task 6,
  dispatch by `pr_merge_admission` in Task 5, the change proposer by the estate admission and close)
  or `not_a_bump` (with the reason). The test fails when `MERGE_EXEMPT_PATHS` gains an
  unclassified entry, and checks each `honours` function calls the shared matcher.

- [ ] **Step 1–3:** write the test against the real lists, prove it fails on an unclassified
  addition, docs, `make check`, commit.

### Task 8: Ship and prove

- [ ] **Step 1: Rebase** on `origin/main` (another session may have landed carrier changes); rerun
  `make check`.
- [ ] **Step 2: Pull request**, CI with `--watch --interval 30`, confirm the Quality test count, merge
  (loop until `MERGED`).
- [ ] **Step 3: Deploy** by `docs/operations/deploy.md`; this release has a migration, so the tag
  write, the migration from the new image, `recheck`, then the swap, and `finish` with
  `--expect-schema MemberView.holds`.
- [ ] **Step 4: Pull the main tree** when no lane pass is due.
- [ ] **Step 5: Prove read paths** in production: `GET /api/v1/members` carries `holds: []` on every
  member; the next inert and estate lander passes and the bump proposer's next pass are unchanged
  (no holds exist). Stage no real hold: the first one is Devon's decision. State in the handoff
  that the close path (`PATCH /pulls/{n}` as the App) is proven by tests only; the comment write was
  measured at 201 before, the close never was, so the first real hold is the first live close.
- [ ] **Step 6: Clean up** the worktree, test database and branch.
