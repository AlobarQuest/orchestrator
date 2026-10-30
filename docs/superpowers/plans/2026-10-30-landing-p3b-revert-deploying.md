# Landing layer, Part 3b: a bad bump comes back out (repositories that deploy) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a Dependabot bump the estate lane landed into brain or change-manager breaks a check
on `main` or leaves production serving the build before it, the SDS points production back at the
previous image, reverts the bump, holds the dependency, and alerts, with no person involved.

**Architecture:** Part 3a's machinery (the `landing_reverts` record, the judgment, the exact-inverse
revert, the revert subject, the automatic hold, never-again and the alerts) extends to the estate
lane, with three additions. The bad-deploy signal is what production serves, read from the
`revision_watcher` lane's `production_revision` observations, not the rollout job's verdict. The
revert carries a change-manager record of a new class, `dependency-revert`, filed by
`change_proposer` and approved by a deploy-policy version that exempts it from the pace and the
window. Before the revert opens, the estate lander points each affected application at the image of
the bump's parent commit through a Coolify client confined to those applications, and after the
revert's own build lands it points them back at the moving tag.

**Tech Stack:** Python 3, SQLAlchemy and Alembic, FastAPI, change-manager (Python), Coolify API, GHCR,
pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-landing-layer-design.md`, Part 3 and Decisions 3 to 9.
Builds on Part 3a (`docs/superpowers/plans/2026-10-29-landing-p3a-revert-inert.md`, shipped as #554).

## Findings this plan rests on

- **The rollout verdict is not a usable signal.** change-manager holds nine `failed` rollout
  observations for brain (#59, #60, #65, #70, #74, #79, #81, #82, #89). Every one has
  `production_reached = yes` and a successful trigger step. The failing step is brain CI's own
  "every brain serves the new revision" check. Most are `app-brain` alone not serving a build the
  other three applications served; one (#79) was a real broken image that left all four applications
  on the previous build for 8.5 hours until a person fixed it forward. Reverting on the verdict would
  have reverted eight good bumps. Measured 2026-10-30; the replay (Task 0) is in
  `2026-10-30-landing-p3b-signal-replay.md`.
- **What production serves is already measured.** `revision_watcher` (hourly) records a
  `production_revision` observation per application (`subject_type = service`) with the served and
  expected commits and a state: `current`, `behind`, `diverged`, `awaiting_deploy` or `unstamped`.
  Coolify's swap is rolling, so a build that never comes up healthy leaves the old container serving:
  `behind`.
- **Both repositories roll back by image** (`change_proposer/criteria.py::_ROLLBACKS`): brain feeds
  four applications from `ghcr.io/alobarquest/brain:latest` (and `:<sha>` per commit); change-manager
  feeds one. Re-pointing a Coolify application at `:<sha>` pins it, so the revert's own build
  deploys the pinned image until the tag is restored.
- **The revert record.** A `deploy` record of a new class is the only shape change-manager's deploy
  policy approves by itself. Today `change_proposer` would file the Dispatch App's revert pull
  request as `dependency-update` and approve it under the bump's rules; that hole closes here.
- **Coolify tokens name actions, not resources.** Any write token reaches every application, so the
  bound is the client (`src/rotation_worker/coolify.py` is the pattern). The word guards forbid this
  code under `src/orchestrator/`; it lives in the estate lander.

## Global constraints

- Work in `.worktrees/landing-p3b` with its own `.venv` and database `orchestrator_test_landing_p3b`;
  change-manager work in its own worktree. Never edit, check out or run `uv` in either main tree.
- Never put a secret in a prompt, tool argument or transcript. Every parsed `bws` call passes
  `--color no`. The Coolify token is fetched in-process by the estate launcher only.
- Merging change-manager redeploys it and it is a rotation target: before each merge there, confirm
  no unit is ready, claimed or in flight.
- In change-manager a field added to an asserted tuple 409s forever on stored rows; pass new fields
  as explicit keyword arguments outside `_ASSERTED_FIELDS`.
- A change-manager policy version is a release: V(N) moves verbatim to `deploy_policy_history.py` and
  `contracts/ratified_rollout_policy.json` is regenerated; the orchestrator's policy parser and
  `check_rollout_policy_ratification.py` accept the new version in the same change set.
- Word guards (ADR-0051) under `src/orchestrator/`; new routes in the scope and idempotency
  inventories; Alembic revision ids at most 32 characters; no finding kind a substring of another.
- `make check` is green before each commit that touches logic.

## Review focus

1. **A slow rollout.** An application `behind` on the first reading after the rollout and `current` on
   the next must never produce a revert. Pinned in Task 2.
2. **A later landing.** An application serving a descendant of the merge commit is `current` for this
   landing's purpose, even if the branch has moved again. Pinned in Task 2.
3. **Pinned forever.** After a re-point, every exit path (revert landed, revert refused, revert failed,
   stranded) either restores the moving tag or leaves an open alert saying production is pinned.
   Pinned in Task 4.
4. **The confined client widens.** The Coolify client may change only `docker_registry_image_tag`
   and trigger a redeploy, only for the applications the member record lists for the repository.
   Pinned in Task 4 by a surface test with a control.
5. **The revert record is filed as a bump.** `change_proposer` never files a Dispatch App pull request
   as `dependency-update`. Pinned in Task 3.

---

### Task 0: Prove the signal on the last 30 days

**Files:**
- Create: `scripts/replay_deploy_signal.py` (read-only; observer and change-manager read bearers,
  fetched in-process)
- Create: `docs/superpowers/plans/2026-10-30-landing-p3b-signal-replay.md` (the result)

**Interfaces:**
- Produces: for every estate-lane landing in brain and change-manager since 2026-09-30, the verdict
  Task 2's rule would have reached, beside change-manager's rollout verdict.

- [ ] **Step 1: Write and run the replay.** Expected: the nine harmless failures above judge
  `clean`; any landing the rule judges `bad` is listed with its served revisions.
- [ ] **Step 2: Stop and report if any judged-`bad` landing was in fact harmless.** The rule is then
  wrong and the plan returns to Devon before Task 2.
- [ ] **Step 3: Commit the script and the result.**

### Task 1: The record learns the image steps

**Files:**
- Modify: `src/orchestrator/persistence/models.py` (`LandingRevert`)
- Create: `migrations/versions/0045_landing_revert_images.py`
- Test: `tests/persistence/test_landing_reverts.py`

**Interfaces:**
- `LandingRevert` gains nullable `pinned_images` (JSONB: `{app_uuid: {tag, digest}}`) and
  `restored_at`. The status vocabulary gains `repointed` (production points at the parent's image,
  revert not yet open) and `restored` (the moving tag is back after the revert landed). Same CHECK
  construction as 0044.

- [ ] **Steps:** failing persistence tests, migration with `downgrade`, `make check`, commit
  `"landing: the revert record names the images it pinned"`.

### Task 2: Judge a deploying landing

**Files:**
- Modify: `src/orchestrator/services/landing/revert_watch.py`, `revert_act.py`
- Test: `tests/services/test_revert_watch.py`, `tests/services/test_revert_act.py`

**Interfaces:**
- The watch covers estate-lane Dependabot landings as well (same population rules as 3a: no reverts,
  `already_merged` included, `WATCH_HOURS`).
- `judge` for a deploying repository: Part 3a's check rule unchanged, plus a served-revision rule.
  - The applications are the member record's `apps` for the repository.
  - A reading counts only if its observation was made at least `ROLLOUT_SETTLE_MINUTES = 30` after the
    landing.
  - An application serving the merge commit or a descendant is fine.
  - `bad` when every application's latest counted reading shows it serving an ancestor of the merge
    commit (`behind`) or something unrelated (`diverged`), and that reading has stood for at least
    `ROLLOUT_STUCK_HOURS = 2`, measured by the observation's `received_at` (never `observed_at`, which
    is a commit date). Readings are content-addressed, so an application stuck on one commit leaves one
    row; time, not a count of rows, is the measure.
  - Liveness: if any application has no reading received within the last 2 hours, the verdict is
    `pending` (the watcher may not have run; it runs on the Mac and has had overnight gaps).
  - Otherwise `pending` until the stuck time is reached. No observation at all past `WATCH_HOURS`:
    the landing leaves the watch with no act.
- The `bad` verdict carries the reason (`checks` or `served`), recorded on the row.

- [ ] **Steps:** failing tests for each review-focus case 1 and 2, every application `behind` for
  2 hours is `bad`, one application of four `behind` (app-brain alone often lags) is NOT `bad`, a stale
  watcher (no reading in 2 hours) is `pending`, the replay's brain #79 shape is `bad`, `unstamped` never counts, a check failure on brain
  still follows 3a's rule; implement; `make check`; commit
  `"landing: judge a deploying landing by what production serves"`.

### Task 3: The revert record in change-manager

**Files (change-manager):**
- Modify: `app/deploy_policy.py` (V14: the `dependency-revert` class, its pinned criteria and
  rollback, and landing conditions that exempt it from the pace and the window and require the
  Dispatch App as author), `app/deploy_policy_history.py`, `contracts/ratified_rollout_policy.json`
- Test: the policy tests

**Files (orchestrator):**
- Modify: `src/change_proposer/cli.py` (file a pull request from an `sds-revert/` branch, authored by
  the Dispatch App and named by an orchestrator `revert_opened` record, as `dependency-revert`; never
  file a Dispatch App pull request as `dependency-update`)
- Modify: the landing-policy parser (`src/bump_proposer/landing_policy.py` and the orchestrator's
  reader) to accept V14, and `src/estate_lander/cli.py::_LANE_CLASSES`
- Modify: `scripts/check_rollout_policy_ratification.py` inputs if the contract shape moves
- Test: `tests/change_proposer/test_change_proposer.py`, `tests/contract/test_rollout_policy_ratification.py`

**Interfaces:**
- `change_proposer` reads `GET /api/v1/landing-watch` (SYSTEM is not its bearer: use the observer if
  the route admits it, otherwise add a read-only revert listing the observer may read) to recognise a
  recorded revert. Builder's call between those two; the observer confinement in
  `api/dependencies.py::_confine_observer` changes only if needed, with its test.

- [ ] **Steps:** failing tests on both sides (review focus 5), the V14 release, the parser and class,
  `make check` here and change-manager's suite there, two commits.

### Task 4: Point production back, and forward again

**Files:**
- Create: `src/estate_lander/image_repoint.py` (a Coolify client confined to the listed application
  uuids: read an application, set `docker_registry_image_tag`, trigger a redeploy; nothing else)
- Create: `src/estate_lander/served.py` (read an application's served revision from its health route,
  the way `revision_watcher` reads it, reusing its parser)
- Modify: `src/estate_lander/cli.py` (the revert pass), `scripts/run-estate-landing.sh` (fetch the
  Coolify token on `--submit` only)
- Modify: `src/orchestrator/services/landing/revert_act.py` and the routes: record `repointed` and
  `restored`; open the revert only from `repointed`
- Modify: `tests/architecture/test_landing_edges.py`, `tests/architecture/test_wsp21_invariant_scan.py`
  (`OUTBOUND_ALLOWLIST`), `.bws-secrets.toml`, the credential registry entry for the new token
- Test: `tests/estate_lander/test_image_repoint.py`, `tests/estate_lander/test_estate_lander.py`

**Interfaces:**
- The sequence the estate lander drives, one step per pass, each recorded before the next:
  1. Watch answers `bad` for a deploying landing.
  2. Read each application's current tag; resolve `:<parent sha>` to a digest in GHCR (refuse if the
     tag is missing); set each application's tag to `<parent sha>`; redeploy; record `repointed` with
     `pinned_images`.
  3. On a later pass, confirm each application serves the parent commit; then ask the orchestrator to
     open the revert (3a's act, from `repointed`).
  4. The revert record is filed by `change_proposer` and approved by V14; the estate lane lands the
     revert subject (3a's terms plus V14's landing conditions); the hold and never-again follow as in
     3a.
  5. After the revert landed and GHCR holds `:<revert merge sha>`, set each tag back to `latest`,
     redeploy, confirm each application serves the revert merge commit, record `restored`.
- Every exit before step 5 (refused, failed, stranded) keeps production pinned and raises
  `landing-production-pinned:<repo>:<pr>`, open until a person restores or the record is restored.
- The confined client's surface test lists every Coolify path and method it may send; a control
  widens it and the test fails (review focus 4).

- [ ] **Steps:** failing tests for each step, the confinement surface and control, each exit path's
  alert (review focus 3); implement; `make check`; commit
  `"landing: point a deploying repository back at its previous image, then revert"`.

### Task 5: Record the decision

- [ ] ADR-0019 amendment 3 (the deploying repositories: the served-revision signal and why, the
  re-point-then-revert order, the restore), and `docs/operations/landing-lanes.md` (how to restore a
  pinned application by hand). Commit.

### Task 6: Ship and prove

- [ ] **Step 1:** Final whole-branch review; fix.
- [ ] **Step 2:** The Coolify token: Devon mints it in Coolify (a person's act) and puts it in the
  `rotation-inbox`-style staging secret named in the registry entry; it is registered for rotation.
- [ ] **Step 3:** Land change-manager V14 (rotation check first), confirm it is served; land and
  deploy the orchestrator (migration 0045); pull the main tree.
- [ ] **Step 4: Prove it without breaking production.** A drill against change-manager: re-point its
  application at the image of its current commit (a no-op for what runs), confirm it serves that
  commit, restore `latest`, confirm again, with every step recorded. The revert, hold and alert path
  is proven by Part 3a's canary and by the tests.
- [ ] **Step 5:** Clean up.

## Decisions for Devon

1. **The bad-deploy signal is what production serves, not the rollout job's verdict.** The verdict was
   wrong 9 times in 9 for brain. This narrows his decision 3's "a failed deploy" to "production does
   not serve the bump, twice, after the rollout settles".
2. **A dedicated Coolify token**, minted by him, confined by the client to brain's four applications and
   change-manager's one, and registered for rotation. The alternative is reusing the rotation
   executor's token, which widens its consumers and blurs who acted.
3. **The live proof is a no-op drill**, not a deliberately broken bump in production.
