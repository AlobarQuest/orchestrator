Outcome: done

# Build report: lanes-inc-5-estate-landing-ping-url-due

PR: AlobarQuest/orchestrator#766, ready for review, not merged. Base `origin/main` at `15e520c5`
(confirmed after `git fetch` in the worktree). Code commit `63d105ff`; this report is committed
after it, so the PR head is the report commit.

## What I built

The `estate-landing-ping-url` row takes `rotate_requested = "2026-11-21"`, so infraops reports it
due and the proposer generates its first issue (deploy.md, estate landing's cut-over item 5;
ADR-0055 amendment 23). It mirrors #756. The broker token's reach and the inert ping URL's keeper
note are corrected as #765's follow-ups 1 and 2.

## Items 1 to 9

1. `.cred-consumers.toml:1415` adds `rotate_requested = "2026-11-21"` right after
   `rotated_by_sds = true`. No `created` and no `last_rotated`.
2. The block comment (`.cred-consumers.toml:1402-1408`) now says the row is "Handed to the SDS and
   due for its first issue by `rotate_requested`". The "not yet due: a later change adds" sentence
   is gone. Nothing else in the comment changed.
3. `tests/rotation_worker/test_estate_landing_rows.py:70-71`:
   `test_the_row_is_due_for_its_first_issue` replaces `test_the_row_is_not_yet_due` and asserts
   `rotate_requested == "2026-11-21"`. The module docstring (`:3-6`) now says the row is "due for
   its first issue" instead of "dated by a later change". A grep of `tests/` and `src/` for
   `estate-landing-ping-url`, `not yet due` and `not_yet_due` found no other assertion about this
   row. The `not_yet_due` hits are the intake follow-ups' skip reason, which is unrelated.
4. `.cred-consumers.toml:1442` adds `11e940d7-e43a-4ebd-9026-b508017d9cef` to
   `secrets-broker-bws-token`'s `reach_secrets`. The comment (`:1431-1434`) now says "three single
   secrets" and lists `estate-landing-ping-url`. `SECRETS` in
   `tests/rotation_worker/test_secrets_broker_row.py:41` gains the id.
5. The `inert-landing-ping-url` keeper consumer note (`.cred-consumers.toml:1400`) now says "the
   executor writes it, and the broker reads it for the lane". I checked these facts at the base:
   - `lanes/inert-landing/compose.yaml` maps no `BWS_ACCESS_TOKEN`. It mounts
     `/data/sds/broker/inert-landing:/run/sds-broker:ro`, and it sets
     `SDS_DEADMAN_PING_URL_UUID: 35557473-…`. Its header says the lane "holds no token" and that the
     resource's `INERT_LANDING_BWS_ACCESS_TOKEN` "maps to nothing".
   - `lanes/secrets-broker/allowlist.toml:19-26` lists `35557473-…` for `inert-landing`.

   The inert token row's comments (`:1350-1356`, `:1365-1367`) don't say that the lane reads with
   its own token, so I left them unchanged. See the follow-ups for the stale parts of that block.
6. The proposer derives the issue plan from the dated row:
   - `_generate` (`src/rotation_proposer/cli.py:444`) passes
     `first_issue=_first_issue(last_rotated, due.credential_id)` (`:462`). The revision path does
     the same at `:423`.
   - `_first_issue` (`:436-441`) returns `True` when infraops serves the row's `last_rotated` as
     null. Both item-8 runs served `"estate-landing-ping-url": null`. The scratch state was empty,
     so this is observed for the row's own fields, not for the live state.
   - `plan_for` (`src/rotation_proposer/generate.py:131-162`) returns the issue plan when
     `first_issue is True` (`:149-150`). The type has only an issue plan, so it also returns it at
     `:151-156` when `first_issue is None`.
   - `test_a_first_issue_derives_the_issue_plan`
     (`tests/rotation_worker/test_estate_landing_rows.py:82-84`) calls
     `plan_for(rows[PING_URL], first_issue=True)` and asserts `plan.issue`.
7. Here's what the VM executor's issue unit reads before the mint for a ping URL:
   - `_read_mint_waits` (`src/rotation_worker/worker.py:693-734`, called at `:468`) runs before
     the claim. It calls `provider.read_mint()` (the Healthchecks check listing). Then it calls
     `_other_keepers(setup, approved)` (`:713`) and refuses a URL that another keeper already
     holds (`:717-719`).
   - `_other_keepers` (`:824-834`) reads the keeper of every other `rotated_by_sds` row of the
     same provider (`healthchecks-ping-url`) through `setup.store.read`. `_bind_inbox` passes the
     same function as `other_keepers` (`:758`).
   - The store is the root's `BwsStore` (`src/rotation_worker/secret_store.py:83-109`);
     `locate` returns a `bws-secret` consumer's `uuid` (`:100-101`). From this change on, every
     other ping-URL unit's pre-mint read includes `11e940d7-…`, because the row is
     `rotated_by_sds`. The estate unit reads every other ping-URL keeper.
   - Whether the root reads `11e940d7-…`: `docs/operations/rotation-executor.md:448` gives the
     root "write on `Rotation / Work`, `Rotation / Keepers` and `Rotation / Inbox`", a project
     grant, and `:478-479` gives it "Can read, write" on `Rotation / Keepers`. The registry comment
     (`.cred-consumers.toml:1402-1405`) says that the VM root's keeper command created the keeper
     in `Rotation / Keepers` at G-81. So the root reads it through its keepers-project grant. This
     is from code and docs only; I made no BWS call.
8. Due-ness, from infraops' `cred-findings`:
   - Setup: I ran `dist/cli/security-drift-cli.js` from a scratch `rsync` copy (no `.git`) of
     `~/Projects/infraops-mcp-server` at `68c4af5`, in `/tmp/w251-infraops.3YdD`. For each run,
     `INFRADRIFT_CONFIG_DIR` and `SECURITY_DRIFT_STATE_DIR` named a scratch directory holding a
     one-file list and an empty (`{}`, mode `0600`) `cred-rotation-state.json`. Both runs used
     `--now 2026-11-21T01:00:00Z`. I didn't edit or build the checkout, and I didn't touch the
     real list.
   - Treated (this branch's file): exit 0 and 28 findings, all `cred.rotation-requested`,
     including
     `{'check': 'cred.rotation-requested', 'id': 'estate-landing-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-21', 'rotated_by_sds': True}`.
   - Control (`origin/main`'s file): exit 0 and 27 findings.
   - As `(check, id)` pairs, treated minus control is exactly
     `('cred.rotation-requested', 'estate-landing-ping-url')`, and control minus treated is empty.
   - Neither run raised a parse error or `cred.invalid-rotate-requested`, so the file parses under
     infraops' TOML subset. The other rows show as due in both runs only because the scratch state
     is empty.
   - The source rule is `src/security-drift/cred-rotation.ts:301-312` in infraops.
9. Mutation checks. I backed up the file, mutated it by exact pattern, ran the matching tests,
   restored from the backup, and confirmed the restore with `cmp`:
   - Without the `rotate_requested` line, `test_estate_landing_rows.py` gave 1 failed and 7 passed
     (`test_the_row_is_due_for_its_first_issue`, `KeyError: 'rotate_requested'` at `:71`).
   - Without the new `reach_secrets` entry, `test_secrets_broker_row.py` gave 1 failed and 5
     passed (`test_the_row_declares_the_granted_projects_and_secrets`, `AssertionError` at `:73`).
   - After the restore, the two files gave 14 passed.

## Compose at the base (out of scope)

`lanes/estate-landing/compose.yaml:31` holds `SDS_DEADMAN_PING_URL_UUID: ""`, and its comment
(`:19`) says a later change writes the ping-URL secret's id. `lanes/secrets-broker/allowlist.toml`
doesn't list `11e940d7-…` for `estate-landing` (`:28-35`); the allowlist test ties that list to
the compose, so deploy.md item 6 must add it there together with the compose id. I didn't change
the compose, the allowlist, `.bws-secrets.toml`, or `test_deploy_lanes.py`'s `placeholders`.

## Tests and checks

- `make check` with `TEST_DATABASE_URL` set to `orchestrator_test_hq_w_251`: exit 0, 14847
  passed, 1 skipped.
- The row tests (estate, inert, broker): 27 passed.
- Security scan from the repo root: exit 0, no BLOCK findings. It reported two WARN findings
  (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`) and one INFO finding
  (`bws.least-privilege-scope`). This diff touches no gitignore and no manifest.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_estate_landing_rows.py`
- `tests/rotation_worker/test_secrets_broker_row.py`
- `docs/superpowers/reports/lanes-inc-5-estate-landing-ping-url-due-report.md`

## Self-review

The diff is 13 lines of registry and test changes plus this report. The comments state the rules
as they stand, with no correction history. The new test compares exact values, and both new
entries are mutation-proven.

## Concerns and follow-ups

- The inert token row's block comment (`.cred-consumers.toml:1350-1356`) still says the account's
  grants "are a later act (deploy.md, the inert lander's cut-over item 4)". The inert lane reads
  through the broker, which makes that wording stale. It doesn't say "own token", so I left it out
  of item 5's scope.
- Other lanes' ping-URL keeper notes (for example, `:788` through `:1244`) still say "the lane
  reads it with its own token". The bump driver moved to the broker in #762, so its note might be
  stale too. The handoff puts the bump driver's rows out of scope, so I didn't check them.
- Deploy.md item 6 must add `11e940d7-…` to both the estate compose and the broker allowlist's
  `estate-landing` list.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout, not files): this change has no logging
  change.
- Code Brain rule 4 (never swallow an exception silently): the one test I added is a single
  assertion with no exception handling.
- Code Brain rule 5 (never log secrets, credentials, auth headers, tokens): nothing added prints a
  value. The diff adds only the BWS secret id `11e940d7-…`, which isn't the ping URL's UUID.
- Infra Brain 591, 592 and 593 (no BWS token in tracked files or history, bootstrap token never
  inline): the diff adds no token and no token-shaped string, and the scan found no BLOCK.
- Infra Brain 2890 (never commit a ping URL): no `https://healthcheck.alobar.net/ping/` URL is in
  the diff.
- Infra Brain 1032 (cred.exposure-rotate): none of the rows I touched (`estate-landing-ping-url`,
  `inert-landing-ping-url`, `secrets-broker-bws-token`) declares a `[[credential.exposure]]`.
- Infra Brain 212 (HTTPS for public FQDNs): not applicable, because there's no domain change.
- Infra Brain lesson 450 (rotation landmines): no secret value enters the registry, a plan, a test,
  or a log. The rows name ids only.
