Outcome: done

# Broker token rotation route, code (ADR-0055 Amendment 34 item 9, D-83)

Worker W-324, branch `hq/broker-token-rotation-code`, based on `e4412d4f`. Code and docs only: no
call to BWS, Coolify, Healthchecks or the VPS, and no production act. The PR isn't merged, and the
row stays `rotated_by_sds = false` with no `rotate_requested`.

- **PR:** PR_URL
- **Head:** PR_HEAD

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
id, whether it was listed. It never prints a secret's value, key or name, the token, or any digest
of the token. It reads the token from `BWS_REACH_PROBE_TOKEN` only, never `BWS_ACCESS_TOKEN`. It
exits 0 when it lists, 2 on a usage error (no token, a malformed id), and 3 when the SDK fails,
naming only the error's class. It imports the SDK lazily, since CI's `uv sync --frozen` doesn't
install the `rotation-worker` extra. The tests use a fake client with random values, and none of
them reaches the output.

It uses only abilities the repository already uses: `bitwarden_sdk` (the executor's `_REACH`), a
Keychain read (`scripts/exit_probe.py`), and the BWS API the SDK reaches. A session runs it this
way:

1. **Devon prepares a project and a token.** In the BWS web app, Devon creates a scratch project
   holding at least one secret of no value. Then he creates a throwaway machine account with read
   on that project, **and no single-secret grant**, and mints its access token. He stores the token
   in the Keychain (service `Claude`, an item name he gives the session). The project must hold a
   secret, or both outcomes list zero secrets and the run can't tell them apart.
2. **The session syncs the extra** in a worktree: `uv sync --frozen --extra rotation-worker`.
3. **The session runs the script** with the organization id (the executor's `organization_id`, a
   non-secret id), the project id and the scratch secret's id:

   ```sh
   BWS_REACH_PROBE_TOKEN="$(security find-generic-password -s Claude -a <item> -w)" \
     .venv/bin/python -I scripts/bws_reach_probe.py --organization <org id> \
     --expect <project id> --expect <scratch secret id>
   ```

4. **The session reads the result.** It's measured only if `expected[<project id>]` is `true`.
   Then `expected[<scratch secret id>]` decides item 5, as the following section describes. The
   session records the JSON line (ids and counts) in its report.
5. **Devon revokes the token** and deletes the machine account. Then he deletes the Keychain item.

## Item 5's two outcomes

- **A, the secret isn't listed.** The listing excludes a granted project's secrets. The live side of
  the bind (`_REACH`) then lists the two projects and the 25 single-secret grants, which equals the
  row's declared reach exactly. Nothing changes: no code, no `reach_secrets` change, and no test.
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

## The four exit checks, the session's later steps

Each runs before the commit that sets `rotated_by_sds = true`:

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
   allowlisted lane logs `listening`, and one lane's `lane-run --read` prints `ok`.

## Tests and checks run

- `make check` in the worktree, with the worktree's own database `orchestrator_test_hq_w_324`
  migrated to head, `SECURITY_STANDARDS_DIR` at the fixture, and `PGTZ=UTC`: MAKE_CHECK_RESULT.
- The two named row-test files and the new script test: TARGETED_RESULT.
- In-process: `check_row` accepts the row with `helper_target`, `helper_target` answers
  `secrets-broker`, and `_first_issue({id: "2026-11-20"}, id)` is `False`.
- **Token-shape grep of the diff:** TOKEN_GREP_RESULT.
- `code-standards risk`: RISK_RESULT.
- `/code-review`: REVIEW_RESULT.

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

- **The run must discriminate.** The throwaway project must hold at least one secret, and the
  account must hold no single-secret grant. Otherwise both outcomes look the same. The run steps
  require both, and the script reports the expected ids.
- **The Healthchecks read in the restart step is a session act.** The broker's lanes have no
  deploy-command quiet-slot check of their own, because the deploy command never plans the broker.
  So the session reads the listing itself. The section states that, and names no new tool.
- **Code Brain has no `secrets` road.** The handoff says so too. The script follows the BLOCK rules
  the handoff lists, and the following section states how.

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
  the throwaway token from the Keychain at run time, and the docs describe shapes only.
- **Infra Brain 1032 (`cred.exposure-rotate`):** the row's exposure and consumer fields are
  unchanged and valid (`check_row` and `lane_variable` pass on the real row). R10 is the
  route's `loaded` proof: the runbook's step 6 says no revoke alert is sent before the broker's
  loaded fingerprint equals the keeper's.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. No endpoint is added.
