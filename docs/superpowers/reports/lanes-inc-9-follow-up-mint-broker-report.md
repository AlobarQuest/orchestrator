Outcome: done

# Report: follow-up-mint reads its secret through the lane secrets broker (broker-first Phase B)

Handoff: `sds-hq/state/handoffs/lanes-inc-9-follow-up-mint-broker.md`. Worker W-254. Base
`origin/main` at `26f196d2`, confirmed unmoved by a fetch in the worktree before work started.
Branch `hq/lanes-inc-9-follow-up-mint-broker`, PR #769 (ready, not a draft; not merged). The code
commit is `25eefd7b`; this report is the commit after it, and the PR head is that commit.

## What I built

The follow-up-mint copy of #762 (bump driver) and #761 (estate landing).

1. `lanes/secrets-broker/allowlist.toml`, new lines 48-51: `[lanes.follow-up-mint]` with one UUID,
   `221a48d5-3f29-4898-b300-b4820140c880` (orchestrator-system-bearer), the only UUID on a code
   line of the launcher. The compose's `SDS_DEADMAN_PING_URL_UUID` is empty, so no ping-URL id
   joins it.
2. `lanes/follow-up-mint/compose.yaml`: dropped `BWS_ACCESS_TOKEN: ${FOLLOW_UP_MINT_BWS_ACCESS_TOKEN}`;
   added `volumes: - /data/sds/broker/follow-up-mint:/run/sds-broker:ro` (lines 25-26). Header
   lines 6-12: the "no volume" line now says its one mount is the broker's socket directory, and
   the token paragraph says it holds no token and reads through the broker, as #762's does. No
   `coolify` network, empty ping id kept. Unlike #762 there is no "keeps its token variable as a
   fallback" sentence: this lane never had a token issued, and under the broker none is.
3. `scripts/run-follow-up-mint.sh`:
   - Lines 49-54: `sds-bws.sh` is sourced first, and `if ! sds_in_lane_container` replaces the
     `-z BWS_ACCESS_TOKEN` guard. It still runs before `sds_outcome_begin` (line 58) and before any
     read. The refusal line names both: `no secrets-broker socket or BWS_ACCESS_TOKEN`.
   - Lines 60-67: the bearer is read with one-argument `sds_bws_value` (socket path, or token if a
     token lane); the failure message says `(the lane's socket or token)`; `unset BWS_ACCESS_TOKEN
     SDS_SECRETS_SOCKET` before the CLI.
   - Header: EXIT CODES lines 16-20 (no lane socket or token), CREDENTIALS lines 22-25 (read through
     the broker, allowlist lists exactly this one), THE CONTAINER ONLY lines 27-34 (socket named by
     `lane-run`, both variables unset, socket stays reachable as the design accepts, refuses when
     `sds_in_lane_container` is false).
4. `tests/scripts/test_run_follow_up_mint.py`:
   - `_run` accepts `None` to remove a variable; the CLI stub logs `socket=${SDS_SECRETS_SOCKET:+set}`.
   - New: `test_on_the_socket_without_a_token_a_pass_reads_every_secret_through_it` (bearer and ping
     URL through the socket, `bws` never runs, CLI line ends `lane_token= socket=`, start and
     success pings); `test_on_the_socket_with_the_composes_empty_ping_id_no_lane_secret_reaches_bws`;
     `test_a_bearer_the_socket_refuses_fails_the_pass_before_the_call` (`bws_failed` and
     `not_allowed`: exit 1, named reason, no CLI call, `/start` then `/fail`);
     `test_a_dead_socket_fails_the_pass_through_the_outcome_and_never_falls_back_to_bws`
     (`no_reply`, exit 1, no CLI call, no lane secret asked of `bws`);
     `test_a_socket_variable_naming_no_socket_refuses_and_calls_nothing`.
   - Changed: the refusal test is now `test_outside_the_container_it_refuses_and_calls_nothing`
     (unset and empty token, nothing read or pinged); the unreadable-bearer test expects
     `(the lane's socket or token)`; the compose test asserts `broker_lane`, `add_refusals(...,
     allowlist) == []`, `compose_broker_refusals == []`, the allowlist entry, and the service with
     no token and the one mount. It was the only test asserting `FOLLOW_UP_MINT_BWS_ACCESS_TOKEN`.
5. `docs/operations/deploy.md`, the follow-up-mint section:
   - "Each pass reads one secret" paragraph: through the lane secrets broker, compose maps no
     token, allowlist lists exactly that one (replaces "with the lane's one token").
   - "Without `BWS_ACCESS_TOKEN` it refuses" sentence: now "Outside its container (no broker socket
     and no token, `sds_in_lane_container`)"; the ping-URL secret joins the allowlist too.
   - "Until the infraops-mcp-server change in step 7" → step 9 (renumbered).
   - Old step 3 (lane account, keeper commands for the lane token and ping URL, two registry rows)
     → new step 3: the keeper command for `follow-up-mint-ping-url` and its registry row only; no
     lane account, token keeper, or token mint.
   - Old step 4 (token mint and executor write, then ping URL due) → new step 4: the broker
     account's reads (SYSTEM bearer in `SDS Operator`, already read for inert landing; Devon's
     single-secret grant of `follow-up-mint-ping-url` to machine account `lane-secrets-broker`,
     as estate landing's ping-URL secret). New step 5: the ping URL's row made due and the
     executor's write.
   - Old 5 → new 6 unchanged. New step 7: `install -d -o 999 -g 999 -m 0750
     /data/sds/broker/follow-up-mint`, before the broker moves to a release listing the lane and
     before the `--add`. New step 8 (old 6): broker moved to that release and its `listening` line,
     then the `--add`, the `--read` proof over both secrets, the schedule, the first pass.
   - Old 7, 8 → new 9, 10 (text unchanged except "after step 9 merges").
   - I left out HQ's gate id (G-81) from the page: nothing in this repository defines it.
6. Registry text: `.bws-secrets.toml` lines 25-26, the SYSTEM bearer's follow-up-mint comment now
   says it reads through the broker with no token of its own. `.cred-consumers.toml` has no
   follow-up-mint reader row, so nothing changed there. I also changed
   `docs/operations/credentials.md` lines 96-99, the same stale "lane account ... with the lane's
   own token" text for this lane (as #762 and #768 edited credentials.md for theirs). If HQ wants
   that out of scope, it's one hunk to drop.

## Item 7: ADR lines that say the lane has its own BWS identity or token (not amended)

ADR-0059 (`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`),
Amendment 10 (starts line 3090):

- Lines 3132-3134, decision item 1: "its lane BWS token and ping URL issued as ADR-0055 amendment
  19 issues them (Amendment 2)".
- Lines 3148-3153, decision item 3: "one new lane BWS machine account for `follow-up-mint` with
  exactly two read grants ... The account reads no other secret".
- Lines 3157-3159, decision item 3: "The account, its grants, the resource, the check and the lane
  token are created in increment 9's identity and cut-over steps".
- Lines 3162-3163: "The new account is one more reader."
- Line 3214, the superseded-text table row: "Answered by decision item 3: the `follow-up-mint` lane
  account."

ADR-0055: no line names the follow-up-mint lane (`grep -n follow-up-mint` is empty).

## Item 8: deploy checks and crontab need no change

- `scripts/deploy_orchestrator.py:1546-1555` `broker_lane`: true for the new compose (no token
  variable, the exact `LANE_BROKER_BIND` from line 978, no stray mount, allowlisted).
- `scripts/deploy_orchestrator.py:1575-1597` `add_refusals`: with no token it calls `broker_lane`
  (line 1590); the schedule mapping is unchanged; `follow-up-mint` isn't in `STATEFUL_LANES`
  (line 1017), so no volume is required.
- `scripts/deploy_orchestrator.py:1521-1543` `compose_broker_refusals`: only the lane's own bind,
  no top-level volumes.
- The compose test asserts all three against the real allowlist, and it passes. The only
  lane-specific entry in the file is `LANE_CHECKS` (line 1004), unchanged.
- `lanes/follow-up-mint/crontab`: `43 6 * * * lane-run follow-up-mint`. `scripts/lane_run.py`
  sets `SDS_SECRETS_SOCKET` whenever `/run/sds-broker/broker.sock` is mounted (lines 10, 74-75),
  with no per-lane setting, so the crontab needs nothing.

## Tests and checks

- `make check` with `TEST_DATABASE_URL` at `orchestrator_test_hq_w_254` (migrated first): exit 0;
  14,857 collected, 14,856 passed, 1 skipped; ruff and pyright clean.
- Mutation controls on the launcher, each restored after:
  - `unset BWS_ACCESS_TOKEN` alone (socket marker leaks to the CLI): 2 tests fail. The first
    run of this mutation found that one brokered test used a substring match (`socket=` inside
    `socket=set`); I changed it to `endswith`, and both brokered pass tests now fail.
  - Guard reverted to `[ -z "${BWS_ACCESS_TOKEN:-}" ]`: 5 tests fail.
- Security scan from the repo root: 0 BLOCK, 2 WARN, 1 INFO. The WARNs (`*-migration/` gitignore
  coverage; undeclared UUID `aaff45ca-…`) are present without this diff: the UUID isn't in it.
- `code-standards risk`: `high-risk` (secrets: `.bws-secrets.toml`, `lanes/secrets-broker/allowlist.toml`).
- `/code-review` low on `origin/main...HEAD`: no findings.
- `vendor-review`: complete, 2 of 2 seats succeeded (claude, codex), both approve, 0 findings,
  0 dropped. agy: off by configuration.

## Files changed

`.bws-secrets.toml`, `docs/operations/credentials.md`, `docs/operations/deploy.md`,
`lanes/follow-up-mint/compose.yaml`, `lanes/secrets-broker/allowlist.toml`,
`scripts/run-follow-up-mint.sh`, `tests/scripts/test_run_follow_up_mint.py`, and this report.

## Self-review and concerns

- The launcher still accepts a token lane (`sds_in_lane_container` is true with a token), as
  #762's does; the existing token-path tests still pass. Under the broker the lane gets no token,
  so this path is unused, not removed.
- With the compose's empty ping id, a broker pass resolves no ping URL in the container (the name
  lookup needs the broad identity, which the container doesn't have), so a refused or dead-socket
  read fails the pass with exit 1 and pings nothing until the ping-URL id lands (new step 6). That
  is the existing placeholder behaviour, not new.
- The uid 999 in the `install -d` step is the handoff's. The image's `orchestrator` user is a
  `useradd --system` user (`Dockerfile:38`) with no fixed uid in the file; confirm 999 on the VM
  at that step.
- No logging change, and no production act of any kind.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout): no logging changes; the launcher's stderr
  lines and the CLI's JSON report are unchanged in form.
- Code Brain rule 4 (never swallow an exception silently): the new guard exits 1 with a named
  line; a failed socket read prints its reason (`bws_failed`, `not_allowed`, `no_reply`) and
  `sds_outcome_fail` ends the pass with exit 1. Tests assert each.
- Code Brain rule 5 (never log secrets): no new line prints a value; the tests assert the bearer
  isn't in stdout or stderr.
- Infra Brain 591, 592, 593: no token in any tracked file or history (scan: 0 BLOCK); no inline
  `BWS_ACCESS_TOKEN` value (the compose maps none); test values are placeholders like
  `value-of-<uuid>` and `a-lane-token`, which don't match the token shape.
- Infra Brain 1032 (exposure means rotate): I saw no secret value.
- Infra Brain 212 (HTTPS for public FQDNs): doesn't apply; the lane serves nothing.
- Infra Brain 2890 (never commit a ping URL): none committed; the compose's ping id stays empty and
  the tests use `https://hc.example/...` placeholders.
