Outcome: done

# Broker token rotation route, code (ADR-0055 Amendment 34 item 9, D-83)

Worker W-324, branch `hq/broker-token-rotation-code`, based on `e4412d4f`. Code and docs only: no
call to BWS, Coolify, Healthchecks or the VPS, and no production act. The PR isn't merged, and the
row stays `rotated_by_sds = false` with no `rotate_requested`.

- **PR:** https://github.com/AlobarQuest/orchestrator/pull/822 (open, not merged)
- **Head:** the commit that adds this line, one report-only commit after `a8fd3fe729047baacd989a9dc5a8a61c38c03972`, the last commit with code; `git log -1` on the branch gives it

## What I built, against item 9's list

1. **`.cred-consumers.toml`, row `secrets-broker-bws-token`.** I added
   `helper_target = "secrets-broker"` (item 4) and `created = "2026-11-20"` (item 3), as row fields
   before the first consumer. I rewrote the comment to state the route (item 2's steps), the unlock
   tier (item 1; the comment is the tier's mark, and it names the executor's root token too, per
   D-83 11 B), and `created` as the age anchor. `rotated_by_sds`, `reach_secrets` (25 ids),
   `reach_projects` (two), the exposure fields (none) and both consumers are unchanged.
2. **`tests/rotation_worker/test_lane_token_helper_targets.py`.** The broker-lane refusal test is
   renamed `test_no_broker_lane_names_one_and_the_broker_s_own_token_names_the_broker`. It still
   refuses a target on any broker lane, and it requires the broker's own row to name
   `secrets-broker`. The install-page test requires the page's table to list `secrets-broker` as
   `secrets-broker-wogvks6obmvv9fdotlgnw7r3`. `test_each_target_is_the_service_that_loads_the_row_s_variable`
   holds unchanged: it derives the broker's lane name and its compose service `secrets-broker`.
   `test_the_package_is_the_same_with_and_without_the_field` now renders the broker's row for both
   `first_issue` values and passes. The module docstring is updated.
3. **`tests/rotation_worker/test_secrets_broker_row.py`.** `test_the_row_has_no_age_anchor` became
   `test_the_row_is_anchored_on_the_paste_and_derives_the_rotation_plan`. It checks
   `created == "2026-11-20"`, no `last_rotated`, that the proposer's `_first_issue` reads the date
   infraops serves as `False`, and that `plan_for` returns the `bws-lane-token` rotation plan
   (`plan.issue` false). `test_the_row_is_not_handed_to_the_sds` is unchanged. The module
   docstring is updated.
4. **`src/rotation_worker/CLAUDE.md`.** The `helper_target` invariant names the broker's own token
   as the one row that isn't a lane and names a target, and cites amendment 34.
5. **`docs/operations/rotation-executor.md`.** The helper target table gains `secrets-broker` |
   `secrets-broker-wogvks6obmvv9fdotlgnw7r3`, and the text before it says the broker's target has
   the same consumers-only shape with `env = "BWS_ACCESS_TOKEN"`. I checked the name against
   `lanes/secrets-broker/compose.yaml`: one service, `secrets-broker`, mapping
   `BWS_ACCESS_TOKEN: ${SECRETS_BROKER_BWS_ACCESS_TOKEN}`, and the row's service uuid
   `wogvks6obmvv9fdotlgnw7r3`. "Lane tokens and ping URLs" now states the broker's route (and
   no longer says the SDS doesn't rotate it), and the anchor paragraph says a hand-issued token
   carries `created` so its first unit is a reissue.
6. **`docs/operations/secrets-broker.md`.** A new "Rotate the broker's token" section has four
   parts:
   - "Install the helper target": the root step with `docker ps` first, the
     `[target.secrets-broker]` TOML, and the `loaded` read that is exit check 1. The PR doesn't
     apply it.
   - "Run a rotation": item 2's steps, with item 6's restart as step 5. The session reads
     Healthchecks first and holds while a broker lane shows `started`, and avoids the 04:00
     `--submit` pass. It runs `coolify_deploy` on `wogvks6obmvv9fdotlgnw7r3` at the running tag,
     never `restart` and never `deploy_orchestrator.py --lane`.
   - "A failed start" and "After a leak": item 6's failure and item 7's leak case.
   - "What the root reaches through this keeper": T1's reach.

   "What the broker doesn't change" no longer calls the token manual. It states the tier.
7. **The measurement script (D-83 13 B):** `scripts/bws_reach_probe.py`, with
   `tests/scripts/test_bws_reach_probe.py`. Both are placed in `layers.toml`'s `rotation` layer.

`bws_lane_token.py`, `bws_machine_token.py` and `reach_secrets` are unchanged, as the handoff
directs.

## How D-83's four answers show

- **10 A**: `secrets-broker.md`, "What the root reaches through this keeper". It states T1's reach:
  whoever holds the root holds the broker's token and every secret it reads, the SYSTEM and
  VERIFIER bearers included. It's accepted for development and reopened with D-64. ADR-0055 is
  unedited.
- **11 B**: the tier statement in "What the broker doesn't change" and the row's comment both name
  the broker's token and the executor's root token. The root token is named only, and stays
  hand-rotated.
- **12 A**: "A failed start" says there's no machine rollback and no restore verb. Devon mints
  again, the broker lanes' reads fail until the next rotation completes, and the old value stays in
  the quarantine secret until `confirm`.
- **13 B**: the measurement script, without changes to the two adapters or `reach_secrets`.

## The measurement script, and how a session runs it

`scripts/bws_reach_probe.py` makes `_REACH`'s three SDK calls: log in, list projects, and list
secrets for the organization. It runs them with a new client and a throwaway `HOME`. It prints one
JSON line on stdout with the project ids, the secret ids, their counts, and, for each `--expect`
id, whether it was listed as a project (`expected.projects`) and as a secret (`expected.secrets`). It never prints a secret's value, key or name, the token, or any digest
of the token. It reads the token from `BWS_REACH_PROBE_TOKEN` only, never `BWS_ACCESS_TOKEN`. It
exits 0 when it lists, 2 on a usage error (no token, a malformed id, the SDK not installed), and 3
when the login or a listing fails,
naming only the error's class. It imports the SDK lazily, since the orchestrator image's
`--no-dev` sync doesn't install the `rotation-worker` dependency group. The tests use a fake
client with random values, and none of them reaches the output.

It uses only abilities the repository already uses: `bitwarden_sdk` (the executor's `_REACH`), a
Keychain read (`scripts/exit_probe.py`), and the BWS API the SDK reaches. A session runs it this
way:

1. **Devon prepares a project and a token, in the BWS web app only.** Devon creates a scratch
   project holding at least one secret of no value. Then he creates a throwaway machine account
   with read on that project, **and no single-secret grant**, and mints its access token. He stores
   the token as a secret in the BWS project `SDS HQ`, which SDS HQ's own machine account already
   reads, so no grant is needed. Then he clears the clipboard. He gives the session only that
   secret's name. The project must hold a secret, or both outcomes list zero secrets and the run
   can't tell them apart. Devon runs no terminal step (D-54 A).
2. **The session finds the secret's id** with SDS HQ's account (Keychain service `Claude`, account
   `BWS_ACCESS_TOKEN_SDS_HQ`). The listing carries values, so the filter keeps only the id and the
   listing never reaches the output:

   ```sh
   HQ_TOK=$(/usr/bin/security find-generic-password -s Claude -a BWS_ACCESS_TOKEN_SDS_HQ -w)
   BWS_ACCESS_TOKEN="$HQ_TOK" bws secret list --output json --color no \
     | jq -r --arg k '<secret name>' '.[] | select(.key == $k) | .id'
   unset HQ_TOK
   ```

   Then it syncs the group in a worktree: `uv sync --frozen --group rotation-worker`.
3. **The session runs the script** with the organization id (the executor's `organization_id`, a
   non-secret id), the project id and the scratch secret's id. The token is read in-process from
   the `SDS HQ` secret by UUID, straight into `BWS_REACH_PROBE_TOKEN` for this one command. It's
   never printed, never in an argument list, and never written to a file or the Keychain.
   `HQ_TOK` lives only in the command substitution's subshell, so the script's process doesn't
   inherit SDS HQ's token:

   ```sh
   BWS_REACH_PROBE_TOKEN="$(
     HQ_TOK=$(/usr/bin/security find-generic-password -s Claude -a BWS_ACCESS_TOKEN_SDS_HQ -w)
     BWS_ACCESS_TOKEN="$HQ_TOK" bws secret get <token secret id> --output json --color no \
       | jq -r .value
   )" .venv/bin/python -I scripts/bws_reach_probe.py --organization <org id> \
     --expect <project id> --expect <scratch secret id>
   ```

   The script's docstring shows a Keychain read as its generic usage; this is the session's form.
   A failed BWS read leaves the variable empty, and the script exits 2 ("no token"), so it can't be
   mistaken for a refused login (exit 3).
4. **The session reads the result.** It's measured only if `expected.projects[<project id>]` is
   `true`. Then `expected.secrets[<scratch secret id>]` decides item 5, as the following section
   describes. The
   session records the JSON line (ids and counts) in its report.
5. **Devon removes everything, and the session proves it.** The proof of the revoke needs the token
   secret, so the secret goes last:
   1. Devon revokes the token, and deletes the throwaway machine account and the scratch project.
   2. The session proves the revoke: it reruns step 3's command unchanged. The script logs in with
      a new client and an empty, throwaway `HOME`, so `bws`'s cached session per token id can't
      answer. The expected result is exit 3 with a `bws_reach_probe.failed` line; exit 2 means the
      read failed, and proves nothing.
   3. Devon deletes the token secret from `SDS HQ`.
   4. The session proves the secret is gone: `bws secret get <token secret id>` with SDS HQ's
      account, its output sent to `/dev/null`, exits non-zero.

## Item 5's two outcomes

- **A, the project is listed and its secret isn't.** The listing excludes a granted project's
  secrets. The live side of the bind (`_REACH`) then lists the two projects and the 25
  single-secret grants, which equals the row's declared reach exactly. Nothing changes: no code, no
  `reach_secrets` change, and no test. That equality also assumes a single-secret grant doesn't
  list its project. Every other lane-token row holds only single-secret grants with
  `reach_projects = []`, and those rows' binds have passed on issued tokens. That's evidence for the
  assumption, though not a measurement of it. The script's `projects` list shows it directly: on
  the throwaway account, which holds no single-secret grant, the list must hold the one project
  only.
- **The project isn't listed at all.** That's a result, not a failed measurement. A project grant
  doesn't appear in `projects().list`, so the row's two `reach_projects` would never match a live
  listing, and the bind refuses every broker token as written. Item 5 then needs a third form,
  which HQ decides: `reach_projects` emptied and the projects' secrets listed in `reach_secrets`
  (if they're listed), or a bind that ignores projects. Under that form, a run that lists neither
  the project nor its secret means a token whose project grant shows nothing. Then rerun the
  measurement with one single-secret grant added, to confirm the login works.
- **C, the secret is listed.** The bind refuses every broker token as written, because the live
  listing then holds every secret in `SDS Operator` and `SDS / Lane Apps`, for example
  `b415d03b-…`, and none of those is in `reach_secrets`. The change:
  - **`bws_machine_token.py`:** add a child script beside `_REACH`, for example `_REACH_IDS`,
    that makes the same three calls and prints the sorted project ids and secret ids as JSON (ids
    only, never a key or value). Add a method, for example `reach_sets(value) ->
    tuple[frozenset[str], frozenset[str]] | None`, that answers `None` on a failed login or
    listing, as `reach` does. `reach` and `same_identity` stay as they are for the machine-token
    adapter and the inbox's identity check.
  - **`bws_lane_token.py`:** `BwsLaneTokenProvider` keeps the declared sets, not only the digest.
    `bind_refusal` keeps the exact digest comparison for a row whose `reach_projects` is empty,
    which is every lane token. For a row with a non-empty `reach_projects`, it passes only when the
    listed projects equal the declared ones exactly and every declared secret is listed; any
    other answer, `None` included, refuses with "what it can see isn't the reach its registry row
    declares". `sharing_reach`, the other-keeper check and `accepts` are unchanged. The module
    docstrings say so.
  - **The tests,** in a new `tests/rotation_worker/test_lane_token_project_reach.py`, use a fake
    login:
    - A project-reach token whose listing holds the declared secrets plus the projects' own
      secrets binds.
    - The same token with one more project, one fewer project, or one declared secret missing
      refuses.
    - A failed listing refuses.
    - A lane token (`reach_projects = []`) with one extra secret still refuses (an exact match
      only).
    - The real broker row builds a provider that takes the project path, and every other real
      lane-token row takes the exact path.

  C is weaker than an exact match, as amendment 34 item 5 says: a broker token with one extra
  single-secret grant would bind. `reach_secrets` would stay as it is.

Measured 2026-11-27: outcome C. The throwaway token, on an account granted one scratch project
and no single-secret grant, listed 1 project (the scratch project) and 1 secret (the project's
filler secret).

## The four exit checks, the session's later steps

Amendment 34 puts each before the commit that sets `rotated_by_sds = true`. Checks 1 to 3 can run
then; check 4 can't (see Concerns):

1. **The target answers for the running broker, with no mint.** As root on `prod-vps`, after the
   `docker ps` and the target install in `secrets-broker.md`, run
   `echo '{"verb": "loaded", "target": "secrets-broker"}' | /usr/local/sbin/sds-pg-rotate`. It
   answers one consumer, `secrets-broker-wogvks6obmvv9fdotlgnw7r3/BWS_ACCESS_TOKEN`, with a
   non-empty fingerprint.
2. **The name survives a redeploy.** The same read after the next broker move (`coolify_deploy`)
   answers the same consumer, non-empty.
3. **Item 5's measurement** is recorded as ids and counts only, through the script in the preceding
   section. If the result is C, the bind change lands before step 4.
4. **One real rotation** through "Rotate the broker's token": the bind passes, `verify` passes on
   the `loaded` match, Devon revokes, and `confirm_dead` passes. After the restart, every
   allowlisted lane logs `listening`, and one lane's `lane-run --read` prints `ok`. This one can
   only follow the flip (see Concerns).

## Tests and checks run

- `make check` in the worktree, with the worktree's own database `orchestrator_test_hq_w_324`
  migrated to head, `SECURITY_STANDARDS_DIR` at the fixture, and `PGTZ=UTC`: exit 0. Ruff reported "All checks passed!", pyright 0 errors, and pytest **14935 passed, 1 skipped** (11 warnings, 5 min 38 s). The first run, before the review fixes, was 14934 passed, 1 skipped..
- **CI on PR #822** (run 45917694028, `Quality`, at `c33ac444`): passed, with **14922 passed, 14
  skipped**; run 45916301219 at `117e8d6c` gave the same counts. The run on fix round 1's head is
  pending at its push; "Fix round 1" names it. That's 14936 collected, the same as locally (14935 + 1). CI skips 13 more tests than
  my machine does, and the job log doesn't print their reasons. The new script tests don't import
  the SDK (they use a fake), so they run whether or not the `rotation-worker` group is installed.
- The two named row-test files and the new script test: `test_secrets_broker_row.py`, `test_lane_token_helper_targets.py`, `test_bws_reach_probe.py` and `test_layer_manifest.py` together gave 25 passed. One mutation checked the leak test: making the script append each secret's key to its id failed both `test_no_token_key_or_value_reaches_the_output` and the listing test, and the file was restored.
- In-process: `check_row` accepts the row with `helper_target`, `helper_target` answers
  `secrets-broker`, and `_first_issue({id: "2026-11-20"}, id)` is `False`.
- **Token-shape grep of the diff:** `git diff origin/main...HEAD | grep -E '0\.[0-9a-f]{8}-'` matches nothing (exit 1), and the BWS write-guard hook allowed every edit.
- `code-standards risk`: `high-risk` (auth: `tests/rotation_worker/test_lane_token_helper_targets.py`; secrets: `tests/rotation_worker/test_secrets_broker_row.py`). So I ran the vendor review as well as `/code-review`.
- `/code-review`: four nits. I fixed two: a missing SDK is now a usage error (exit 2, with the install command) instead of exit 3, read as a failed login; and `expected` now reports projects and secrets separately, so a listed project can't be read as its secrets listed. One was already disproved: the `layers.toml` double-ownership worry is answered by the layer-manifest test passing in `make check`. I kept the private `_first_issue` import in the row test: the handoff asks for the proposer's derivation, and no public function exposes it without infraops. **Vendor review** (`/tmp/vendor-review-nmgqthjc`, diff `31527ecb…`): Review complete, 2 of 2 seats succeeded (claude and codex, both approve, 0 findings kept, 0 dropped); agy: off by configuration. The script fixes came after the vendor review, and the targeted tests and the second `make check` cover them.

## Files changed

- `.cred-consumers.toml`
- `docs/operations/rotation-executor.md`
- `docs/operations/secrets-broker.md`
- `layers.toml`
- `scripts/bws_reach_probe.py` (new)
- `src/rotation_worker/CLAUDE.md`
- `tests/rotation_worker/test_lane_token_helper_targets.py`
- `tests/rotation_worker/test_secrets_broker_row.py`
- `tests/scripts/test_bws_reach_probe.py` (new)
- `docs/superpowers/reports/broker-token-rotation-code-report.md` (new; this report)

## Self-review

- **The plan fits the code at `e4412d4f`.** `check_row` accepts the target name. The helper-target
  derivation maps the row's key to the broker's service. `plan_for(first_issue=False)` returns the
  rotation plan, and `render` accepts the row for both plans. So no stop point fired.
- **The anchor test proves only this repository's half.** That infraops serves `created` as
  `last_rotated` is infraops code (`rotationAnchor` in infraops-mcp-server
  `src/security-drift/cred-rotation.ts`, as `rotation-executor.md` records). The test pins only that
  the proposer reads that date as issued and derives the rotation plan.
- **The deploy command still skips the broker's row** by id (`BROKER_TOKEN_ID` in
  `scripts/deploy_orchestrator.py`), not by the absence of a field, so the new fields don't change
  it.
- **The measurement script's output is ids only.** The SDK's secret listing carries each secret's
  key (its name), which the script drops. Its error path prints the class, never the text, since an
  SDK error message can echo request detail.

## Concerns

- **Exit check 4 can't run before `rotated_by_sds = true`.** Amendment 34 item 9 puts all four
  exit checks before the flip. But the executor refuses a row that isn't `rotated_by_sds`
  (`src/rotation_worker/checks.py` line 119, `worker.py` line 1068), and the proposer sends such a
  row to the manual checklist (`src/rotation_proposer/cli.py` line 529). So one real rotation
  through the route can't happen while the row is `false`. The workable order: run checks 1 to 3,
  then flip the row and add `rotate_requested`, and then run check 4, the first rotation. Devon's
  revision approval and mint stay gates on it. The route itself is unchanged, so this isn't a stop
  point. HQ decides the order, or another trigger. The row's comment and both operations pages say
  that check 4 follows the flip.

- **The run must discriminate.** The throwaway project must hold at least one secret, and the
  account must hold no single-secret grant. Otherwise both outcomes look the same. The run steps
  require both, and the script reports the expected ids.
- **The Healthchecks read in the restart step is a session act.** The broker's lanes have no
  deploy-command quiet-slot check of their own, because the deploy command never plans the broker.
  So the session reads the listing itself. The section states that, and names no new tool.
- **Code Brain has no `secrets` road.** The handoff says so too. The script follows the BLOCK rules
  the handoff lists, and the following section states how.

## Follow-ups

- `scripts/bws_reach_probe.py` `_uuid` lets argparse echo a malformed `--expect` or
  `--organization` value to stderr. Printing only "not a UUID" would keep a token pasted into the
  wrong flag out of the output. Not changed in fix round 1.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** the script prints exactly one JSON line to
  stdout per run, with an `event` field (`bws_reach_probe.listed`, `.refused` or `.failed`). It
  writes no log file.
- **Code Brain 4 (never swallow an exception):** the one `except Exception` in the script emits a
  `failed` line naming the class and exits 3. There's no bare `except …: pass`.
  `test_a_failed_login_names_only_the_error_class` holds it.
- **Code Brain 5 (never log secrets):** the script prints ids and counts only.
  `test_no_token_key_or_value_reaches_the_output` checks that a random token and every secret's key
  and value are absent from the output. The docs name fingerprints only (the helper's
  `loaded`), never a value.
- **Infra Brain 591, 592 and 593 (no token in tracked files, git history or inline):** no token or
  token-shaped literal appears in the diff (the grep in "Tests and checks run"). The test fakes
  build random values at run time with `secrets.token_urlsafe` and `token_hex`. The runbook reads
  the throwaway token from its BWS secret at run time, and the docs describe shapes only.
- **Infra Brain 1032 (`cred.exposure-rotate`):** the row's exposure and consumer fields are
  unchanged and valid (`check_row` and `lane_variable` pass on the real row). R10 is the
  route's `loaded` proof: the runbook's step 6 says no revoke alert is sent before the broker's
  loaded fingerprint equals the keeper's.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. No endpoint is added.

## Fix round 1

Worker W-325, report text only, on #822's head `c33ac444`. The per-task review found that steps 1
and 5 of "The measurement script, and how a session runs it" had Devon store the throwaway token in
the Keychain and delete the item, against his standing rule (D-54 A): his part of a credential is
generating it, storing it in BWS and granting read, with no terminal step.

- **Steps 1 to 5 rewritten.** Devon stores the token as a secret in BWS project `SDS HQ` and gives
  the session its name only. The session finds the id, then runs the script with the token read
  from that secret by UUID with SDS HQ's account, straight into `BWS_REACH_PROBE_TOKEN` for one
  command. Devon removes the token, the account, the project and the secret, and the session proves
  the revoke and the deletion. The script's docstring is unchanged.
- **One ordering change from the handoff's step 5.** The handoff has Devon delete the BWS secret
  before the session proves the revoke. The proof needs the token, which lives only in that secret,
  so step 5 deletes the secret after the revoke is proved.
- **CI line** names run 45917694028 at `c33ac444`, and says the fix round's run is pending at push.
- **Follow-ups** gains the `_uuid` echo line. The "Standards followed" line for 591 to 593 now says
  the runbook reads the token from BWS, not the Keychain.
- **Head:** the commit that adds this section, pushed fast-forward to
  `hq/broker-token-rotation-code`.

**Standards followed (this round):** Code Brain 5 and Infra Brain 591 to 593: the rewritten steps
never print, store or pass the token as an argument; it moves from a BWS read to one process's
environment. The shell forms hold placeholders only, and the diff's token-shape grep matches nothing.
Code Brain 3 and 4 and Infra Brain 1032 and 212 are unchanged by this round.
