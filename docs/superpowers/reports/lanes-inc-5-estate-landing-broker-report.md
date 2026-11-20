Outcome: done

# Report: estate landing reads its secrets through the lane secrets broker

Handoff: `lanes-inc-5-estate-landing-broker` (ADR-0059 increment 5, step 3g-2, broker-first Phase B).
Branch `hq/lanes-inc-5-estate-landing-broker`, base `origin/main` at `1447b3da`. PR #761, ready
for review, not merged. Code head when the checks ran: `0868acf5c3a1ced3fbb72ed5093facc474e8bcec`.
This report's commit follows it.

## What I built

The estate-landing copy of #758's inert-landing change, file by file.

1. `lanes/secrets-broker/allowlist.toml` (the new table at the end of the file):
   `[lanes.estate-landing]`, with each UUID and its registry name. These are the five UUIDs on
   code lines of `scripts/run-estate-landing.sh` at the base: `314f276d-…`
   (change-manager/M2M_TOKEN_READ), `314a966c-…` (lanes-read-app-private-key), `221a48d5-…`
   (orchestrator-system-bearer), `b415d03b-…` (ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE, `--submit`
   only), and `f9f59233-…` (COOLIFY_TOKEN_SDS_ESTATE_LANDER, `--submit` only, the default of
   `ESTATE_LANDING_COOLIFY_UUID`). The compose's `SDS_DEADMAN_PING_URL_UUID` is empty, so no
   ping-URL id is listed.
2. `lanes/estate-landing/compose.yaml`:
   - Header lines 13 to 18: the token paragraph is rewritten as #758 wrote it for inert landing.
     The lane holds no token and reads through the broker's socket. The resource keeps
     `ESTATE_LANDING_BWS_ACCESS_TOKEN`, and the compose maps it to nothing.
   - The `BWS_ACCESS_TOKEN` mapping is removed.
   - The volumes gain `/data/sds/broker/estate-landing:/run/sds-broker:ro`.
   - The empty ping-URL id and its paragraph are unchanged.
3. `scripts/run-estate-landing.sh`:
   - Header lines 56 to 62 and 75 to 78 ("TWO HOSTS, ONE LAUNCHER"): `sds_in_lane_container`, the
     socket or a token, the allowlist, and unsetting both variables.
   - `sds-bws.sh` is sourced right after `REPO_ROOT` (lines 116 to 121), before activation, and
     removed from its old place.
   - Activation check (line 130): `if ! sds_in_lane_container`.
   - Container branch (lines 154 to 161): `if sds_in_lane_container`, with
     `FROM=" (the lane's socket or token)"` and the rewritten block comment.
   - `env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET` for `estate-landing repositories` (lines 185
     and 186) and for `sds-github-mint` (line 213), with the mint comment reflowed.
   - `unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET` before the lander (line 251).
   - The Mac branch's code is unchanged. Following #758, I added a three-line comment above it
     (lines 257 to 259) that explains why the Mac checks test the token and not
     `sds_in_lane_container`.
4. `tests/scripts/test_run_estate_landing.py`:
   - The docstring names the broker form.
   - The `repositories` and mint stubs log `socket=`, and the existing expectations include
     ` socket=`.
   - The Coolify-message assertion and the container-block index follow the launcher.
   - New tests:
     - `test_container_branch_runs_on_the_socket_without_a_token` (bare and submit): every lane
       secret comes through the socket, and the only `bws` call is the dead-man helper's
       Healthchecks key, by the test-only broad stand-in, because the compose's ping-URL id is
       empty. No activation runs and no `gh` call is made, and neither variable reaches
       `repositories`, the mint, or the lander.
     - `test_with_a_ping_url_named_the_socket_serves_every_read`: `bws` never runs, and nothing
       falls back to a broad identity.
     - `test_an_unreadable_coolify_token_on_the_socket_is_logged_and_the_pass_still_runs`.
     - `test_a_dead_socket_fails_the_pass_and_never_reaches_the_mac`: exit 1 with the named
       FATAL line.
   - The compose test asserts `broker_lane`, `add_refusals` with the allowlist, and
     `compose_broker_refusals`, and checks the new volume list and the absence of a token.
   - No estate-landing registry-row test exists. `grep -rn ESTATE_LANDING_BWS_ACCESS_TOKEN tests`
     matched only this file's compose test, so I changed no rows test.
5. `docs/operations/deploy.md`, estate landing's section (stale lines only):
   - The `.bws-secrets.toml` paragraph said the secrets were "all read with the lane's one token".
     It now says the broker reads them.
   - Cut-over item 2 dropped the lane token's keeper and keeps the ping URL's.
   - Item 3 dropped the token's registry row.
   - Item 4 replaced the lane account's grants with the broker account's project reads.
   - Item 5 dropped the token's mint and write.
   - Item 7 now starts with moving the broker to a release that lists the lane, and its
     `listening` line, as the "Add a lane" procedure in `docs/operations/secrets-broker.md`
     requires.

## Confirmations (handoff item 6)

- **The deploy check accepts the compose with no code change.** `broker_lane`
  (`scripts/deploy_orchestrator.py:1541`) is true for the service:
  - It maps no `BWS_ACCESS_TOKEN`.
  - It mounts `LANE_BROKER_BIND` for the lane and nothing else of the broker's directory.
  - The allowlist names the lane.

  `add_refusals` (`:1570`) returns `[]` with the allowlist, and `compose_broker_refusals`
  (`:1516`) returns `[]`. All three are asserted in
  `test_the_compose_meets_the_add_refusals_and_maps_nothing_else`. `deploy_orchestrator.py` is
  unchanged.
- **The broker's account reads each listed secret's project.** In `.bws-secrets.toml`:
  - `221a48d5-…` (line 28), `b415d03b-…` (line 307), and `f9f59233-…` (line 324) are in
    `SDS Operator`.
  - `314a966c-…` (line 369) is in `SDS / Lane Apps`.
  - `314f276d-…` (line 202) is in `Rotation / Keepers`.

  The broker's grants are "project by project" (ADR-0055 amendment 30, Devon's answer). Inert
  landing's entry already needs `SDS Operator`, `SDS / Lane Apps`, and `Rotation / Keepers`, so
  the only secret this change adds over inert's list is `f9f59233-…`, which is in `SDS Operator`.
  The repo doesn't record the broker's grants themselves, and I made no BWS call, so this check
  goes as far as the manifest's projects.

## Tests and checks

- Targeted: `test_run_estate_landing.py`, `test_secrets_broker_allowlist.py`, and
  `test_deploy_lanes.py` gave 179 passed.
- `make check` with `orchestrator_test_hq_w_246` exited 0: 14,822 passed and 1 skipped (14,823
  collected), with lint and type checks green. The first run failed on one E501 line in the test
  file, which I fixed in a separate commit.
- Security scan from the repo root found no BLOCK findings. It reported 2 WARN findings, both
  older than this branch: the `*-migration/` gitignore glob, and the undeclared UUID
  `aaff45ca-…`, which appears only in older reports and not in this diff. It also reported 1 INFO
  judgment item.
- `/code-review` low on `origin/main...HEAD` found nothing.

## Files changed

- `docs/operations/deploy.md`
- `lanes/estate-landing/compose.yaml`
- `lanes/secrets-broker/allowlist.toml`
- `scripts/run-estate-landing.sh`
- `tests/scripts/test_run_estate_landing.py`
- `docs/superpowers/reports/lanes-inc-5-estate-landing-broker-report.md` (this report)

## Self-review

- The diff follows #758 line for line where the two launchers match. Estate landing's extra
  Coolify read keeps its non-fatal path on the socket, and a test covers it.
- With the compose's empty ping-URL id, a brokered container's dead-man helper falls to the name
  lookup. On the VM it finds no broad identity, so it pings nothing, as the compose header already
  says. The tests keep the broad stand-in so that no test reads the real Keychain.

## Concerns

- **Comments outside my scope still say "the lane's own token".** `.bws-secrets.toml` comments
  (around lines 20 to 24, 190 to 193, 296 to 297, and 317 to 320) say estate landing reads in
  its container "with the lane's own token", and inert landing's lines say the same. The handoff
  rules out a manifest change, and #758 left the inert lines as they were, so I left these.
- **The fallback token may not exist.** The compose header says the resource keeps
  `ESTATE_LANDING_BWS_ACCESS_TOKEN` as a fallback, as the handoff asked me to write. Estate
  landing's lane token was never minted (cut-over item 2 was still open), so the variable may
  never hold a value. HQ creates the resource, so HQ settles whether it carries the variable.
- **Deploy-command support for broker lanes is an open question.** A `--token` run on a broker
  lane is refused at plan time. What it does for one is still open in ADR-0059, as before.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging changes. The launcher's
  stderr FATAL lines are unchanged except for the `FROM` suffix.
- **Code Brain rule 4 (never swallow an exception silently):** every failed read in the container
  branch still names itself and exits with its code. `test_a_dead_socket_fails_the_pass_and_never_reaches_the_mac`
  pins exit 1 with the named FATAL line. The Coolify token's non-fatal read still logs its own line.
- **Code Brain rule 5 (never log secrets):** no line prints a value. The brokered test asserts
  that no lane secret value appears in the output, and the stubs log only `set` or empty markers
  for the token and the socket.
- **Infra Brain 591, 592, and 593:** no BWS token appears in a tracked file or in history, and
  there's no inline `BWS_ACCESS_TOKEN` value. Test values are obvious placeholders
  (`value-of-<uuid>`, `a-lane-token`, `https://ping.example/x`, and a dummy UUID for the ping-URL
  secret) that don't match the token shape. The scan found no BLOCK findings.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value in this session.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The lane serves nothing: it has no
  port and no domain.
- **Infra Brain 2890 (never commit a ping URL):** no ping URL is committed. The compose's ping-URL
  id is still empty, and the test uses `https://ping.example/x`, a placeholder.
