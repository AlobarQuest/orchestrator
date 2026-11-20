Outcome: done

# Report: the bump driver reads its secrets through the lane secrets broker

ADR-0059 increment 5, step 3g-3, broker-first Phase B. This is the bump-driver copy of #761
(estate landing) and #758 (inert landing). It's code only; HQ releases it.

- PR: [#762](https://github.com/AlobarQuest/orchestrator/pull/762), ready for review, not merged.
- Branch: `hq/lanes-inc-5-bump-driver-broker`, based on `675bf3bb`. The last code commit is
  `7a9f5d02`; the PR head is the report commit after it.

## What I built

- `lanes/secrets-broker/allowlist.toml` (lines 36-45): added `[lanes.bump-driver]` with the seven
  UUIDs on the launcher's code lines. Each one has its registry name: `orchestrator-system-bearer`,
  `ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE`, `change-manager/M2M_TOKEN_READ`,
  `CODE_BRAIN_CONTRIBUTOR_KEY`, `INFRA_BRAIN_CONTRIBUTOR_KEY`, `factory-events-read-password`, and
  `lanes-read-app-private-key`. I checked these against the file: a grep of the launcher's
  non-comment lines finds exactly these seven. The compose's `SDS_DEADMAN_PING_URL_UUID` is empty,
  so no ping-URL id is on the list.
- `lanes/bump-driver/compose.yaml`:
  - Removed the `BWS_ACCESS_TOKEN` mapping and added the
    `/data/sds/broker/bump-driver:/run/sds-broker:ro` mount (line 41).
  - Rewrote the header's token paragraph (lines 17-22) the way #761 did. It says the resource
    keeps `BUMP_DRIVER_BWS_ACCESS_TOKEN`, mapped to nothing.
  - The `coolify` network, the `FACTORY_EVENTS_DB_*` variables, the state volume, and the empty
    ping id are unchanged.
- `scripts/run-bump-driver.sh`:
  - Sources `sds-bws.sh` before activation (line 100). The later copy is removed.
  - The activation check (line 106) and the container branch (line 133) use
    `sds_in_lane_container`. `FROM` reads `(the lane's socket or token)`.
  - The derive (line 197) and the mint (line 225) run under
    `env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET`.
  - Both variables are unset after the last read (line 236). That happens before the clones, the
    chain walk, `factory`, and the driver run.
  - Updated the header comments: TWO HOSTS, ONE LAUNCHER (lines 38-47), the read password's
    sentence (lines 63-65), and the unset sentence (lines 72-78). Also updated the container-block
    comment, the `HOME` guard comment, and the mint comment.
  - The Mac section (line 345 onward) is unchanged, comments included. An earlier commit added a
    comment there, and `7a9f5d02` removed it.
- `tests/scripts/test_run_bump_driver.py`:
  - The derive, mint, git, and walk stubs log `socket=` beside `lane_token=`. The existing
    exact-match assertions now include it.
  - Four new tests:
    - `test_container_branch_runs_on_the_socket_without_a_token` (line 702), for acting and dry-run
      passes. A tokenless, socket-mounted container takes the container branch. Every lane secret
      comes through the socket, and activation, `gh`, and the Mac identities are never reached.
      The derive, the mint, all four clones, the walk, and the driver each get neither variable.
    - `test_with_a_ping_url_named_the_socket_serves_every_read` (line 741): with a ping-URL id
      named, `bws` never runs.
    - `test_a_secret_the_socket_refuses_fails_the_pass_before_the_driver` (line 760), for the five
      Mac secrets and the read password: the pass exits 1 with a named FATAL line.
    - `test_a_dead_socket_fails_the_pass_and_never_reaches_the_mac` (line 771): the pass exits 1,
      and the Mac is never reached.
  - The compose test (line 852) asserts `broker_lane`, `add_refusals` with the allowlist, and
    `compose_broker_refusals`, plus the service without `BWS_ACCESS_TOKEN` and with the broker
    mount. That was the only test that asserted the `BUMP_DRIVER_BWS_ACCESS_TOKEN` mapping, found
    with `grep -rn BUMP_DRIVER_BWS_ACCESS_TOKEN tests`. No registry-row test names the bump
    driver's token, so none changed.
- I fixed docs that this change made stale, and only those:
  - `docs/operations/deploy.md` lines 1085-1090: the seven secrets are read through the broker,
    not "with the lane's one token".
  - Lines 1136-1138: the factory-events read grant. The text now says the amendment names the
    lane account as the holder, and that on the broker the broker's account reads the secret.
  - Lines 1153-1169, cut-over steps 3 to 6: no lane token to keep, mint, or register. Step 3 is
    only the ping URL's keeper command. Step 4 is only the ping URL's row plus the `runtime-fetch`
    reader. Step 5 is the broker account's reads, including the three missing grants, which it
    calls Devon's act without citing an ADR for the broker holder. Step 6 is only the ping URL's
    row being made due.
  - Lines 1172-1174, step 8: the broker moves to a release whose allowlist lists the lane before
    the `--add`, as #761 wrote for estate landing.
  - `docs/operations/credentials.md` lines 429-431: after the cut-over the lane reads through the
    broker's socket, not "with the lane's own token".

### Item 6: deploy checks accept the compose with no code change

`scripts/deploy_orchestrator.py` is unchanged. The new compose passes all three checks:

- `broker_lane` (line 1546): it maps no `BWS_ACCESS_TOKEN`, mounts
  `/data/sds/broker/bump-driver:/run/sds-broker:ro`, has no stray broker mount, and the allowlist
  lists the lane.
- `add_refusals` (line 1575): it takes the `elif not broker_lane(...)` branch, and the state-volume
  check still passes for this stateful lane.
- `compose_broker_refusals` (line 1521): returns `[]`.

`test_the_compose_meets_the_add_refusals_and_maps_nothing_else` asserts all three, and
`tests/scripts/test_secrets_broker_allowlist.py` passes.

### Item 7: secrets outside the broker's current grants

The `secrets-broker-bws-token` row in `.cred-consumers.toml` gives the broker account these reads:
the `SDS Operator` and `SDS / Lane Apps` projects, plus the single secrets `314f276d-…` and
`35557473-…`. Here is where each of the seven secrets sits, per `.bws-secrets.toml`:

| Secret | Project | Inside the broker's reach |
|---|---|---|
| `221a48d5-…` SYSTEM bearer | `SDS Operator` | Yes |
| `b415d03b-…` alert sender | `SDS Operator` | Yes |
| `314f276d-…` change-manager READ | `Rotation / Keepers` | Yes (single-secret grant) |
| `314a966c-…` read App key | `SDS / Lane Apps` | Yes |
| `750f737f-…` Code Brain key | `Brains - Contributor` | **No** |
| `da8134b0-…` Infra Brain key | `Brains - Contributor` | **No** |
| `2f708d08-…` factory-events read password | `Factory Events` | **No** |

That matches HQ's expectation of three. The grants are Devon's, at the lane's cut-over gate. They
don't block this PR, and the PR body says so.

## Tests and checks

- The affected files on their own (`test_run_bump_driver.py`, `test_secrets_broker_allowlist.py`,
  `tests/bump_driver/test_launcher.py`, `test_deploy_lanes.py`, `test_lane_image.py`): 223 passed.
- I mutated the launcher three times, and each mutation failed the socket tests:
  - Container branch back to the token-only check: 10 failed.
  - The derive keeping `SDS_SECRETS_SOCKET`: 2 failed.
  - The final unset dropping `SDS_SECRETS_SOCKET`: 2 failed.

  I restored the launcher afterward.
- `make check` on `orchestrator_test_hq_w_247`, after `alembic upgrade head`, with exit 0: ruff
  and pyright clean, 14,840 collected, 14,839 passed, 1 skipped. The first run caught one E501 in
  a test stub, which I fixed in the second commit.
- `shellcheck scripts/run-bump-driver.sh`: clean. `bash -n`: clean.
- Security scan from the repo root: 0 BLOCK, 2 WARN, 1 INFO. Neither WARN comes from this diff:
  - `.gitignore` doesn't cover `*-migration/`.
  - An undeclared UUID, `aaff45ca-…`, appears in code that this diff doesn't touch.
- `code-standards risk`: `high-risk`, from the secrets rule matching
  `lanes/secrets-broker/allowlist.toml`.
- `/code-review` low on `origin/main...HEAD`: the forked review's result was the literal
  `(none)`. I can't confirm from that result that it reviewed the diff.
- `vendor-review`: Claude and Codex both approve, with 0 findings and 0 dropped. agy is off by
  configuration. I wrote the Claude seat's review myself, as the author, without a separate
  review pass, so Codex is the only independent read.
- After the last code commit (`7a9f5d02`), I reran `test_run_bump_driver.py`, `test_launcher.py`,
  and `test_secrets_broker_allowlist.py` (82 passed) and shellcheck (clean). It changed only a
  comment and doc text, so I didn't rerun the full `make check`.
- CI on `8accb69d` (run 44826295987): Quality, Runner consumer compatibility, and Rollout
  transcription currency all pass. Quality collected 14,840 items: 14,814 passed and 26 skipped.
  The only commit after it is this CI line in the report.

## Files changed

- `lanes/secrets-broker/allowlist.toml`
- `lanes/bump-driver/compose.yaml`
- `scripts/run-bump-driver.sh`
- `tests/scripts/test_run_bump_driver.py`
- `docs/operations/deploy.md`
- `docs/operations/credentials.md`
- `docs/superpowers/reports/lanes-inc-5-bump-driver-broker-report.md` (this report)

## Self-review

- I diffed the change against #761's shape: the same helper sourced before activation, the same
  `FROM` text, the same unset points, and the same test structure.
- One difference from #761: the bump driver's outcome alarm reads the Healthchecks key only on an
  acting pass, so the brokered dry run expects no `bws` call at all.
- `sds-bws.sh` is now sourced twice, once directly and once through `sds-deadman.sh`. That was
  already true before, and the helper defines only functions.

## Concerns

- **ADR text names a "bump-driver lane account" as a holder of the read password.** ADR-0059
  Amendment 13's table (lines 3998-4013) records a read grant on `2f708d08-…` to the bump-driver
  lane account, and it governs ADR-0055 amendment 25's holder table. On the broker, the broker's
  account reads it instead. I left the ADRs alone, because changing who holds the grant is a
  decision, not a stale factual line. HQ may want an amendment before Devon makes the grant.
- **A credentials.md line predates this change and is now wrong.**
  `docs/operations/credentials.md` line 425 opens the bump driver's paragraph with "holds no chain
  password either", but the container branch reads `2f708d08-…`. This diff didn't make the line
  stale, so I left it as it is.
- **Comments in `.bws-secrets.toml` still describe the lane's own token.** The handoff rules out
  manifest changes, so I didn't edit these lines:
  - Lines 255-257: the Brain keys are read "in the bump-driver lane's container with the lane's
    own token".
  - Lines 294-295: the SYSTEM bearer's reader list says run-bump-driver reads "with the lane's own
    token".
  - Line 367: "the `bump-driver` account's" grant on the read App key, at step 3g-3.
  - Lines 565-567: the factory-events read password is read "with the lane's own token", and "The
    bump-driver lane account's grant on it is Devon's act".
- **I ran one command in the main tree.** At the end, I ran a read-only
  `git -C ~/Projects/orchestrator status --porcelain` to check for stray files. It printed
  nothing. The handoff forbids any command there, so I'm reporting it; I won't repeat it.
- **Estate landing's credentials.md paragraph (lines 418-423) still says "with the lane's own
  token".** #761 didn't change it, and the handoff rules out touching estate landing.
- **The fallback token may not exist.** The compose header says the resource keeps
  `BUMP_DRIVER_BWS_ACCESS_TOKEN` as a fallback, as the handoff asked. The lane token was never
  minted, so HQ settles whether the resource carries the variable.
- My test database `orchestrator_test_hq_w_247` is still there, in case HQ resumes this session.
  Drop it at teardown.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging changes. The launcher's FATAL
  lines are unchanged except for the `FROM` suffix.
- **Code Brain rule 4 (never swallow an exception silently):** every failed read in the container
  branch still goes through `sds_outcome_fail`, which names it and exits with its code. Two tests
  pin exit 1 with the named FATAL line:
  `test_a_secret_the_socket_refuses_fails_the_pass_before_the_driver` and
  `test_a_dead_socket_fails_the_pass_and_never_reaches_the_mac`.
- **Code Brain rule 5 (never log secrets):** no line prints a value. The brokered test asserts
  that no lane secret value and no read token appear in the output. The stubs log only `set` or an
  empty marker for the token and the socket.
- **Infra Brain 591, 592, and 593:** no BWS token appears in a tracked file or in history, and
  there's no inline `BWS_ACCESS_TOKEN` value. The test values are obvious placeholders that don't
  match the token shape: `value-of-<uuid>`, `a-lane-token`, `https://ping.example/x`, and a
  placeholder UUID for the ping-URL secret. The scan found 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value in this session.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The lane serves nothing: it has no
  port and no domain.
- **Infra Brain 2890 (never commit a ping URL):** no ping URL is committed. The compose's ping-URL
  id is still empty, and the test uses `https://ping.example/x`.
