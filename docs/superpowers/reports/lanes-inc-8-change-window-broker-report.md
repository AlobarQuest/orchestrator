Outcome: done

# Report: change-window reads its four secrets through the lane secrets broker

Handoff: `sds-hq/state/handoffs/lanes-inc-8-change-window-broker.md`. Branch
`hq/lanes-inc-8-change-window-broker`, base `origin/main` at `0f3929ac` (the handoff's facts held
there). Worker W-263. PR: the one open from this branch (`gh pr view hq/lanes-inc-8-change-window-broker`). A commit can't name its own head or a PR opened after it is pushed, so this report doesn't predict either. No production act was taken, and no secret value was
read.

## What I built

The change-window copy of #769 (follow-up-mint) and #762 (bump driver).

1. `scripts/run-change-window.sh`
   - Lines 61-72: the four ids are the defaults behind the `${...:-}` seams, each with its
     registry name in a comment.
   - Lines 91-97: `sds-bws.sh` is sourced before the guard (and its later `source` dropped), and
     `sds_in_lane_container` replaces the `BWS_ACCESS_TOKEN` test. Outside a container with a
     socket or token it refuses with exit 1 after the argument check, before
     `sds_outcome_begin` and before any read.
   - Lines 23-26 (exit codes): "no lane token" is now "no lane socket or token", and "a secret id
     this pass needs that is empty" is removed (see the next item).
   - Removed lines 95-103 at base (the `_needed` empty-id guard). With real defaults behind
     `${...:-}`, an empty override yields the default, so the guard could never fire. Its two
     tests went with it (`test_an_empty_secret_id_the_pass_needs_fails_closed_before_anything_runs`,
     `test_a_dry_run_needs_none_of_the_windows_other_secret_ids`). The dry run's "needs only the
     bearer" property is now proved by the recording broker instead.
   - Lines 42-53: the CREDENTIALS and THE CONTAINER ONLY paragraphs describe the broker.
   - Lines 109-110 and 120-123: comments on the clone and the reads name the socket.
   - Lines 126, 132, 135, 139: "(the lane's token)" is now "(the lane's socket or token)".
   - Lines 152-154: after the last read and before `node`,
     `unset BWS_ACCESS_TOKEN SDS_SECRETS_SOCKET`. The digest's curl still gets only
     `_DIGEST_TOKEN` on stdin.
2. `scripts/sds-cred-registries.sh` lines 34-35: `-u SDS_SECRETS_SOCKET` added to
   `sds_anonymous_clone`'s `env` (rewrapped so the line stays under 100 columns). `lane-run`
   passes the marker in the launcher's environment, so without it `git` would inherit it.
   Mutation M1 proves this. No other change to that file.
3. `lanes/change-window/compose.yaml`: the `BWS_ACCESS_TOKEN` mapping is gone, and
   `/data/sds/broker/change-window:/run/sds-broker:ro` is the one volume. Header lines 8-16 are
   rewritten as #769's were. There's still no state volume, no `coolify` network, and the
   `SDS_DEADMAN_PING_URL_UUID` is still empty.
4. `lanes/secrets-broker/allowlist.toml` lines 56-62: `[lanes.change-window]` lists exactly the
   four ids, each with its registry name. The Coolify token, the Anthropic key, and the sender
   token are marked `(--submit only)`.
5. `.bws-secrets.toml` lines 622-657: one `[[secret]]` per id, `project = "Rotation / Keepers"`,
   `lanes = ["change-window"]`. The comment says each is read only in the lane's container
   through the broker, and that the lane has no token.
6. `.cred-consumers.toml` lines 1793-1845: the following two rows, in the revision watcher read
   token row's form. Neither has a fingerprint or `rotated_by_sds`. Both have
   `created = "2026-11-22"`, a `bws-secret` keeper consumer, and a `runtime-fetch` of
   `scripts/run-change-window.sh` with `healthchecks_check = "Change Manager OPs Check"`. That's
   the exact check name, as `LANE_CHECKS` and change-manager's own row use it; the field holds a
   check's name, not its slug (`src/rotation_worker/readers.py` lines 12-13).
   - (a) `coolify-change-window-token`, class `coolify-api-token`.
   - (b) `anthropic-change-window-key`, class `anthropic-api-key`. The id isn't
     `anthropic-api-key`, because factory-runner's registry already uses that one. I grepped the
     factory-runner, change-manager, and infraops registries: neither new id appears in any of
     them.
   - (c) No sender row, as the handoff instructs (see Concerns). The broker's own row is
     unchanged, and the execute bearer's row is left to change-manager.
7. `docs/operations/deploy.md`, the change-window cut-over:
   - Lines 1305-1312: the "four secrets don't exist yet" paragraph is replaced by the broker
     description (no `EMPTY_IDS`; `DEFAULT_IDS` instead).
   - Lines 1314-1319, new: the broker-directory precondition paragraph, placed before the list
     because it must hold before two acts. It covers `install -d -o 999 -g 999 -m 0750`, the
     directory existing since G-85, and the need for it before any broker move to a release
     listing the lane and before the `--add`.
   - Items 2, 3, and 4 (lines 1326-1345) are rewritten:
     - Item 2: no lane account, token, or token keeper; the four secrets and the ping-URL keeper
       in `Rotation / Keepers` with single-secret `lane-secrets-broker` grants; and the execute
       bearer's hash write and restart, done at G-88.
     - Item 3: this PR.
     - Item 4: no token mint or write; the ping URL's row made due and the executor's write stay.
   - Item 6 (lines 1348-1351): the broker's move and its `listening` line come before the `--add`
     and the `--read` proof, as in follow-up-mint's item 8.
8. `docs/operations/credentials.md` lines 442-452: the change-window paragraph said the lane
   account would hold four secrets that don't exist yet, read with the lane's own token. It now
   says the lane reads them through the broker, minted at G-88. The handoff didn't name this
   file, but #769 made the same edit for its lane, and leaving the paragraph would contradict the
   code.
9. `tests/scripts/test_run_change_window.py`
   - `EMPTY_IDS` is gone. `DEFAULT_IDS` pins the four defaults to the handoff's ids
     (`test_the_four_secret_ids_default_to_their_secrets`).
   - The `git` stub logs `socket=`. `_run` drops a variable passed as `None`.
   - Outside the container, bare and `--submit` refuse with token unset or empty. A socket
     variable that names no socket also refuses. Each case asserts an empty argv, `bws`, and ping
     log.
   - A recording stand-in broker runs the tests with the seams removed, so the launcher reads its
     real defaults:
     - The dry run asks for the execute bearer alone.
     - `--submit` asks for the ping URL and then the four, in order. `bws` never runs. Neither
       `git` nor `node` gets `SDS_SECRETS_SOCKET` or `BWS_ACCESS_TOKEN`. The sender token reaches
       only curl's config.
     - A clone failure, a missing bundle, or no `node` asks for the ping URL only.
     - Each of the four refused (`bws_failed`, `not_allowed`) fails the pass through the outcome
       before the window.
     - A dead socket fails the pass with `no_reply` and never falls back to `bws`.
   - The compose test asserts `broker_lane`, `add_refusals` with the allowlist,
     `compose_broker_refusals`, the allowlist's set, and the volume.

## Tests and checks

- `make check` (database `orchestrator_test_hq_w_263`): green. Lint, format, and pyright pass, and pytest reports 14,883 passed, 1 skipped, in 755 s. A first run failed on three pyright errors and one format diff in the new tests, which I fixed in two follow-up commits before the final run.
- Mutations, each restored and checked with `cmp`:
  - M1: drop `-u SDS_SECRETS_SOCKET` from the clone. 2 tests fail (the `git` socket assertions).
  - M2: `unset BWS_ACCESS_TOKEN` alone before `node`. 2 tests fail (the `node` env assertions).
  - M3: drop the sender id from the allowlist. 2 tests fail
    (`test_each_allowlisted_lane_lists_exactly_what_its_launcher_reads` and the compose test).
- Registry and manifest tests: every test file that reads `.cred-consumers.toml` or
  `.bws-secrets.toml` passed (1,487) before the full run. No loader refused (a) or (b).
- Security scan from the repo root: 0 BLOCK, 2 WARN, 1 INFO. Both WARNs predate this branch: an
  undeclared UUID `aaff45ca-…`, and the gitignore glob `*-migration/`.
- `code-standards risk`: `high-risk` (`secrets: .bws-secrets.toml`,
  `secrets: lanes/secrets-broker/allowlist.toml`).
- `/code-review` (low) on `origin/main...HEAD`: no findings.
- `vendor-review`: complete. 2 of 2 seats succeeded (Claude and Codex), both `approve`, with 0 findings kept and 0 dropped. agy is off by configuration. A first run was stopped before any result because the pyright fix changed the diff, then rerun from a new `review-prepare` directory.

## Item 8: lines that say the lane has its own BWS identity, account, or token

These are in `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`, line
numbers at this branch (unchanged from base). ADR-0055 has none.

- 4175-4177 (Amendment 14 decision item 3): "The lane's own BWS machine account,
  `sds-lane-change-window` (decision 2, lines 487-494), reads exactly five secrets…"
- 4318-4321 (Amendment 14 decision item 7): "The lane account's read grants on exactly those four
  and its ping-URL secret. Devon's act, at the lane's identity gate… The lane token and ping URL
  are issued as Amendment 2 and ADR-0055 amendment 23 issue every lane's."
- 4384 (Amendment 14 table): decision 2's row, "`sds-lane-change-window` reads exactly the four
  secrets and the ping URL…"
- 4385 (Amendment 14 table): the change-window row, "its own account reads four secrets of its
  own…"
- 4396 (Amendment 14 table): "change-window's own account reads four new secrets of its own".
- 4468-4470 (Amendment 15 decision item 2): "The lane account's read grant on it. Devon's act at
  the lane's identity gate, which `deploy.md`'s change-window cut-over step 2 holds with the mint
  gates…" The deploy.md step 2 it cites no longer has an identity gate.
- 4497 (Amendment 15 table): "the grant is authorized, as Devon's act at the identity gate."
- Context only, not wrong in itself: 3073 and 3075 (Amendment 9's "change-window gets its own"
  identity) and 4052 (Amendment 14's question about "the lane's own BWS identity").

## Item 9: deploy checks, crontab, and lane-run need no change

- `scripts/deploy_orchestrator.py:1546` `broker_lane` accepts the compose. It maps no token,
  mounts exactly `LANE_BROKER_BIND`, has no stray mount (`stray_broker_mounts`, line 1484), and
  the allowlist lists the lane. The test asserts it.
- `add_refusals` (line 1575): with no token, it reaches `broker_lane`, and `change-window` isn't
  in `STATEFUL_LANES`. It returns `[]` (asserted).
- `compose_broker_refusals` (line 1521): one service, its own bind only, no `volumes_from`, no
  privileged flag, no Docker socket, no named volume. It returns `[]` (asserted, and
  `test_no_compose_mounts_the_broker_directory_beyond_its_own_lanes_bind` passes).
- `lanes/change-window/crontab` line 10, `0 4 * * * lane-run change-window --submit`, is unchanged
  and still right. The test passes.
- `scripts/lane_run.py` has no per-lane table. `lane_environment` (lines 103-114) copies only
  `COPIED` and sets `SDS_SECRETS_SOCKET` when `/run/sds-broker/broker.sock` is a socket.
  `--read` (lines 166-173) asks the socket for any UUIDs it's given.

## Item 10: can `node` still reach the socket?

Yes. Unsetting the variable removes only the pointer, not the reach. `node` can connect to
`/run/sds-broker/broker.sock` by path and read any of the lane's four secrets, the digest sender
token included. On a bare dry run, which the launcher reads only the bearer for, it can also read
the three `--submit`-only secrets, because the allowlist isn't scoped by mode.

- `scripts/secrets_broker.py` lines 8-11: "the socket a request arrives on is the lane's
  identity: the request carries no token, header or claim." Lines 170-182 (`handle`) check only
  that the UUID is in that lane's `allowed` set.
- `_bind` (lines 247-263) sets the socket to `0600` in a `0750` directory. Nothing checks the
  peer's credentials: no `SO_PEERCRED` or `getpeereid` in the file. Any process running as the
  socket owner's uid can connect.
- The lane image ends in `USER orchestrator` (`lanes/Dockerfile` line 90). The broker's compose
  (`lanes/secrets-broker/compose.yaml` line 10) runs as the image's `orchestrator` user, and the
  directory is 999:999. `node` runs as that same user, so mode 0600 doesn't stop it.
- The `:ro` bind doesn't stop a connect. The existing broker lanes (inert-landing,
  estate-landing, bump-driver, follow-up-mint) all read through the same `:ro` bind.
- #769's header says the same of follow-up-mint's CLI: "The socket itself stays reachable, as the
  design accepts: the CLI runs as the same user." I added the same sentence to this launcher's
  header. Closing the gap would need a peer-credential check or a different uid for `node`, both
  outside this PR.

## Self-review

- The launcher order is: argument check, broker or token check, outcome begin, clone (with
  neither variable), bundle and `node` checks, reads, unset, then `node`. This matches #769.
- The dead-socket case: the ping URL is read through the same dead socket, so it doesn't resolve
  and no `/fail` is sent. The pass still exits 1 with the named read failure. That's how every
  broker lane behaves (`sds_deadman_resolve_by_uuid`); the check goes down after its grace.
- No value is printed. The tests assert that no brokered value appears in stdout or stderr, and
  that no secret is a process argument.

## Concerns

- **The handoff's premise in item 5(c) doesn't hold.** This repository's `.cred-consumers.toml`
  already has class `alert-hub-sender-token` on `alert-hub-sds-orchestrator-sender` (line 713), and the class is in `tests/fixtures/rotation_class_policy.json` and
  `tests/architecture/test_rotation_agreement.py` line 28. As instructed, I added no row for
  `ALERT_HUB_SENDER_TOKEN_REPORT_CHANGE_WINDOW` (`71616bf5-…`). **Follow-up:** that row can
  likely use the existing class.
- **Follow-up:** HQ adds the four ids to `secrets-broker-bws-token`'s `reach_secrets` after the
  lane's `lane-run --read` proves the grants. The execute bearer's change-manager row is the
  other worker's.
- `docs/operations/deploy.md` line 1266 says "its compose mounts no volume", which is still true
  of state volumes. I left it as is.
- The standards cover every concern this change touches.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging changes. The launcher's
  existing `[change-window]` lines and `FATAL:` lines are unchanged in form.
- **Code Brain rule 4 (never swallow an exception silently):** every new failure path is named.
  A refused, failed, or unreachable broker read prints `sds_bws_value`'s line with its UUID and
  code, then the launcher's `FATAL: could not read …` line, and exits 1 through
  `sds_outcome_fail`. Outside the container, the launcher exits 1 with one `FATAL:` line. The
  tests assert each case.
- **Code Brain rule 5 (never log secrets):** no line prints a value. The tests assert that no
  brokered value, lane token, or ping URL appears in stdout, stderr, or argv. This report holds
  ids only.
- **Infra Brain 591, 592, 593:** no BWS token appears in any tracked file. The compose's token
  mapping is removed, and nothing assigns `BWS_ACCESS_TOKEN` inline. Test values are obvious
  placeholders (`brokered-<VARIABLE>`, `value-of-<uuid>`, `a-lane-token`) and don't match the
  token shape. The scan reports 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value anywhere. Only BWS ids were
  handled.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The lane serves nothing: no port,
  no domain.
- **Infra Brain 2890 (never commit a ping URL):** none committed. `SDS_DEADMAN_PING_URL_UUID`
  stays empty, and the ping-URL keeper's id appears only in `deploy.md` prose, as an id.
- **Code Brain road `notifications`:** the digest still goes to alert-hub as a `kind: notice`,
  `severity: info`. Its sender token is listed in `.bws-secrets.toml` and read by UUID at
  runtime, now through the broker. Only how the token is read changed
  (`test_a_submit_pass_sends_the_digest_as_an_info_notice` and the broker `--submit` test).
- **Secrets (no Code Brain road):** the Infra Brain BWS rules preceding this item stand in for it.
  Secrets are referenced by UUID in the launcher, allowlist, and manifest, and nothing caches them.
