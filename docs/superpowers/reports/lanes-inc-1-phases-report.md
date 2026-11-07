Outcome: done

# Build report: lanes-inc-1-phases (worker W-35)

ADR-0059 increment 1, the second of HQ's two PRs: the deploy command's three lane phases, their run
files, `prepare`'s lag check, and the `.bws-secrets.toml` entry for the read-only Healthchecks key.
This PR makes no Coolify, infraops, VPS, Healthchecks or BWS call, and changes no lane image,
`lane-run`, placeholder compose or `Release image` file.

- **PR:** #625, draft, "ADR-0059 increment 1: the deploy command's lane phases"
- **Head:** `b33341e6bf3f39b582a61d6c66afc86378ce5fd4` (the code). The commit that adds this report
  follows it on the branch and changes no other file.
- **Base:** `b9f8b164` (`origin/main` after `git fetch origin`, PR #624's merge)
- **Branch:** `hq/lanes-inc-1-phases`

```
 .bws-secrets.toml                         |    8 +
 docs/operations/deploy.md                 |  148 ++++-
 layers.toml                               |    1 +
 scripts/deploy_orchestrator.py            | 1017 ++++++++++++++++++++++++++++-
 tests/architecture/test_runtime_edges.py  |   12 +-
 tests/scripts/test_deploy_lanes.py        |  816 +++++++++++++++++++++++
 tests/scripts/test_deploy_orchestrator.py |   85 +++
 7 files changed, 2072 insertions(+), 15 deletions(-)
```

## What I built

1. **The three lane phases.** `lanes-prepare`, `lanes-recheck` and `lanes-finish` in
   `scripts/deploy_orchestrator.py`, each with `--lane` and exactly one change: `--tag sha-<40 hex>`,
   `--schedule <true|false>`, or `--add --tag sha-<40 hex>`. A malformed or missing change is usage
   (exit 1).
   - **`lanes-prepare`** refuses while the lane has an `unfinished` run. It plans the change, then
     resumes the waiting run for the same `change_key` (same deadline) or records a new one with a
     deadline 6 hours on. It saves that run, then marks any other waiting run `superseded`. A passed
     deadline marks the run `timed_out`, prints one stderr line naming the lane, the change and the
     deadline, and exits 4. Otherwise it reads the quiet slot (exit 2 if unmet). On exit 0 it prints,
     on stderr:
     - the unified diff of the compose against the recorded one;
     - `lanes-recheck`;
     - `coolify_update_service` with the whole compose between markers;
     - the one variable write: `SDS_LANES_IMAGE_TAG` through `coolify_update_service_env` for
       `--tag` or `coolify_create_service_env` for `--add`, or `LANE_SCHEDULE_ENABLED` for
       `--schedule`;
     - `coolify_deploy`;
     - the `vps_exec` container reads;
     - `lanes-finish`;
     - the inverse.
   - **The session's reads at `lanes-prepare`:** `--running-tag` (must equal the last finished run's
     tag), `--running-schedule` (must equal the recorded schedule, and differ from the new one), and
     `--running-volume` for the eleven stateful lanes (must equal the recorded volume). For `--add`
     they're `--resource-uuid` and `--schedule-written`, which must be `false`.
   - **The change's checks:**
     - `--tag` and `--add` must name the served orchestrator tag (read from `/health/live`).
     - `--tag` and `--schedule` need a passed `--add`.
     - A second `--add` on a lane with a finished run refuses.
     - An `--add`'s tracked compose must map
       `LANE_SCHEDULE_ENABLED: ${LANE_SCHEDULE_ENABLED:-false}` exactly.
     - An `--add` of any lane but the placeholder must map `BWS_ACCESS_TOKEN` as
       `${<LANE>_BWS_ACCESS_TOKEN}`. The placeholder must map no token, the named exception.
     - A stateful lane's compose must mount a named volume at `/var/lib/sds/<lane>`. A bind mount
       or an interpolated source refuses.

     A refused plan is recorded as a `refused` run.
   - **`lanes-recheck`** needs the change's `waiting` run that `lanes-prepare` found open. It
     repeats the quiet-slot reads, and marks the run `unfinished` only on exit 0. If the slot has
     closed, it exits 2 and sends the session back to `lanes-prepare`.
   - **`lanes-finish`** needs the change's `unfinished` run. It takes `--observed-digest`
     (the release's lane digest), `--observed-schedule`, `--observed-volume` for stateful lanes,
     and `--token-present` for an `--add` of any lane but the placeholder. For a `--tag` or
     `--schedule`, the volume must equal the recorded one. For an `--add` it must be exactly
     `<resource uuid>_<compose volume name>`, so a double-prefixed name refuses. With
     `--redeploy-failed`, or on any mismatch, it exits 2 and prints the inverse.
   - **`--restored`** for `--tag` and `--schedule` takes fresh reads and exits 0 only on the
     replaced digest, schedule and volume. For `--add` it takes `--service-listed` and
     `--container-present` and exits 0 only when both are `false`. The printed inverse names the
     D-20 delete and the session's 10-minute re-read.

   The command holds no Coolify credential, sends no alert, reads no alert-hub token, and creates
   or deletes nothing.
2. **Run files** (option A). See "Run-file layout", following.
3. **The first compose.** There's no `lanes-finish --initial`. An `--add` run's
   `compose_replaced` is null, so its diff shows the whole tracked file as new, and its inverse is
   HQ's delete. When its `lanes-finish` passes, it's the lane's last finished run, so its
   `compose_sent` is the recorded compose for the next change.
4. **Tag resolution.**
   - `--tag` accepts `sha-<40 hex>` only.
   - `resolve_commit` fetches and requires the commit on `origin/main`.
   - `lane_release` lists the newest 200 dispatched `Release image` runs with `status` and
     `conclusion`, and requires exactly one completed, successful run titled
     `Release image <sha>`. It reads the digest with `pushed_lane_digest()`.
   - `change_key` uses the full commit.
   - A `--tag` whose commit isn't a descendant of the lane's first passed `--add` refuses
     (`require_descendant`, after the fetch).
5. **`LANE_CHECKS`.** See "Check-name table", following.
6. **`World.now`**, `datetime.now(UTC)` by default. `World.clock` is still monotonic.
7. **`prepare`'s lag check.** `require_lanes_current(world, served, sha)` is called from
   `_prepare` after `pre_swap_revision` and its "already serves" check, and before
   `migrations_between` and `state.save`. A refusal dispatches nothing: a test asserts no dispatch
   and no state file. It refuses on any `unfinished` run in any lane directory. It also refuses on
   any registered lane whose last finished run's full commit isn't the served one; the message
   names the lane and both exits. A registered lane with no finished run is skipped. Run files
   store each tag with its full commit.
8. **`State.lane_digest`**, a defaulted `""` field. `_build` reads the run log once and takes both
   digests from it with `logged_digest`. A state file without the field loads. `finish` fetches,
   reads the registered lanes with a passed `--add` before the binder, and after binding prints
   `lanes-prepare --lane <lane> --tag sha-<built commit>` for each. The commands go on stderr and
   in `lane_phases` in the JSON.
9. **Exit 4.** `TimedOut` maps to `EXIT_TIMED_OUT = 4` in `main()`, documented beside 0 to 3.
   `migrations_between` is unchanged, and `prepare` admits no `--ref` at a lane's commit.
10. **`docs/operations/deploy.md`.** It gains a lane-current bullet in `prepare`'s list, the lane
    digest in "Build", step 6 of `finish`, exit 4, and a "The lane phases" section. That section
    covers the changes, run files and statuses, the quiet slot, "Run a change" (the session's
    steps in order with every infraops call), the 10-minute re-read bound before an `--add`'s
    `--restored`, and the lane-token matcher.
11. **`.bws-secrets.toml`.** It gains `f07b1368-f937-4287-baf1-b4ed00b6b274`, `HC_API_KEY`,
    project `Rotation / Healthchecks`, as estate-dashboard's manifest names it, with a comment
    naming its reader and the direct grant.

I didn't build the more-than-one-service refusal or the rollback compose (verify report items 11
and 14), which Amendment 1 removed.

### Run-file layout

`LANES_STATE_DIR = ~/Library/Application Support/sds/deploy-lanes/`, overridden by
`World.lanes_state_dir`:

```
deploy-lanes/
  <lane>/
    .lock                                         # flock(LOCK_EX|LOCK_NB) around each phase
    <UTC stamp>-<tag|schedule|add>-<8 hex>.json   # one run, written to a temp file, then os.replace
```

Each file is a `LaneRun`, with the following fields:

- `lane`, `change`, `change_key` (`tag:<commit>`, `add:<commit>` or `schedule:<value>`), `status`;
- `created_at`, `updated_at` and `deadline`, as ISO 8601 UTC;
- `sequence`, the run's place in the chain, assigned under the lock;
- `resource_uuid`, `tag`, `commit`, `lane_digest`, `workflow_run_url`;
- `schedule` (the value expected after the change);
- `volume` and `volume_slug`;
- `compose_sent` and `compose_replaced`;
- `replaced_tag`, `replaced_schedule`, `replaced_digest`;
- `slot_open`, `reason`, `run_id` (the file's stem), and `schema`.

The statuses are `waiting`, `refused`, `timed_out`, `superseded`, `unfinished`, `finished` and
`restored`. "The lane's last finished run" is the newest `finished` run only. The verify report's
item 8 counted `restored` too, but a restored run returns the lane to the values it replaced, which
are the previous `finished` run's. Counting it would hand the lag check and the next compose diff
the failed values. A file this program didn't write (malformed, another schema, or a lane or
`run_id` that disagrees with its path) is refused, not skipped. A lock held by another session is a
condition (exit 2).

### Check-name table

`LANE_CHECKS` holds 17 rows, each taken from the launcher's dead-man ping. The 9 lanes that arm
the dead-man switch (`sds_deadman_arm <name>`) are:

- `deploy-watcher` (`sds-deploy-watcher`)
- `estate-landing` (`sds-estate-landing`)
- `execution-watch` (`sds-execution-watch`)
- `inert-landing` (`sds-inert-landing`)
- `landing-ledger` (`sds-landing-ledger`)
- `pin-watcher` (`sds-pin-watcher`)
- `revision-watcher` (`sds-revision-watcher`)
- `rotation-proposer` (`sds-rotation-proposer`)
- `unit-verifier` (`sds-unit-verifier`)

The 8 outcome-alarm lanes, which set `CHECK_NAME="<name>"`, are:

- `bump-driver` (`sds-bump-driver`)
- `bump-proposer` (`sds-bump-proposer`)
- `change-proposer` (`sds-change-proposer`)
- `fork-harden` (`sds-fork-hardening`)
- `fork-sync-watch` (`sds-fork-sync-watch`)
- `membership-check` (`sds-membership-check`)
- `rotation-decomposer` (`sds-rotation-decomposer`)
- `work-carrier` (`sds-work-carrier`)

Each source is `scripts/run-<lane>.sh`. `run-activation-sweep.sh` and `run-tool-installer.sh` stay
on the Mac (decision 4) and have no row; `placeholder` has none.
`test_the_check_names_are_the_launchers` parses every `scripts/run-*.sh` and holds the table equal
to it, minus the two Mac launchers. The listing is
`GET https://healthcheck.alobar.net/api/v3/checks/` with `X-Api-Key` from
`_bws_value("f07b1368-…")`, which reads as `sds-operator` (Keychain `BWS_ACCESS_TOKEN_SDS`).
It sits behind `World.checks`, matched on the exact `name`, as `sds-deadman.sh` matches. A name
listed zero or two times, or a check without a boolean `started`, refuses.

### The lane-token matcher

`lanes_in_registry` reads `.cred-consumers.toml` at `origin/main` (`git show`, after `prepare`'s
or `finish`'s fetch). A `[[credential.consumer]]` is a lane token's entry when it has:

- `kind = "coolify-env"`;
- `resource_type = "service"`;
- a `key` matching `^([A-Z][A-Z0-9_]*)_BWS_ACCESS_TOKEN$`.

The lane is group 1 lower-cased with `_` as `-`, so `EXECUTION_WATCH_BWS_ACCESS_TOKEN` is
`execution-watch`. A key naming a lane outside `LANE_CHECKS`, and two entries for one lane, refuse.
`placeholder` is added by name. The repository's registry matches no lane today, which a test
asserts. I added no entry; increment 2 adds the first, and deploy.md documents the shape.

## Tests and checks

| Check | Result |
|---|---|
| `make check` (ruff, ruff format, pyright, full pytest against `orchestrator_test_hq_w_35`) on the head | Green: 13925 passed, 1 skipped. |
| `tests/scripts/test_deploy_lanes.py` | 58 passed. |
| `tests/scripts/test_deploy_orchestrator.py`, `tests/architecture/test_runtime_edges.py` | All passed. Existing test bodies are unchanged. |
| Security scanner, repository root, `--category security` | No BLOCK. Two WARNs, the same two the base carries (`*-migration/` glob, UUID `aaff45ca-…`). |
| PR #625 checks on `b33341e6` | All three passed: Quality (15m21s), Rollout transcription currency, and Runner consumer compatibility (run `42316952904`). |

Every item in the handoff's test list has a test:

- **Each quiet-slot condition:** inside the window; `started` and `up`; `started` and `grace`;
  `started` and `down` (passes); not started; no `started` field; zero or two checks named;
  another lane's running check not holding this lane.
- **The placeholder:** `listing_reads == 0` across prepare and recheck, and the window still
  applies.
- **The deadline:**
  - A re-run resumes the same run id and deadline.
  - A run for another change supersedes the waiting run.
  - A refused change keeps its new run's deadline.
  - The timeout marks `timed_out`, exits 4 and prints one line naming the lane, the change and
    the deadline.
  - A run after the timeout records a new deadline.
- **`lanes-recheck`:** `unfinished` only on exit 0. It stays `waiting` on exit 2 and refuses until
  `lanes-prepare` finds the slot open again.
- **`lanes-finish`:**
  - The old digest, a wrong schedule, a double-prefixed volume and an unprefixed volume each exit
    2 and print the inverse.
  - Five wrong `--add` volume names refuse, the double-prefixed ones among them.
  - The correct reads finish.
- **The token check:** `execution-watch`'s `--add` needs `--token-present true`, and the
  placeholder needs none.
- **`--restored`:** the replaced values restore a `--tag`, and the run never becomes the lane's
  record. An `--add` is restored only when both gone reads are `false`.
- **The `--add` refusals:** a `--schedule-written` of `true`; four schedule mappings other than
  `${LANE_SCHEDULE_ENABLED:-false}`; a missing token mapping; three bind or interpolated mounts; an
  unserved tag; two successful releases for one tag.
- **The descendant refusal**, and a `--tag` needing a passed `--add` and the recorded running tag.
- **The lag check's four outcomes**, plus `prepare` dispatching nothing on a lag and admitting a
  lane on the served tag:
  - a stale tag refuses;
  - an unfinished run refuses;
  - an unregistered lane is skipped;
  - a registered lane with no passed `--add` is skipped until its `--add` passes, then refuses.
- **The matcher:** application resources, `coolify-env-hash`, a bare key, an unknown lane and
  duplicates.
- **The tables:** `LANE_CHECKS` against the launchers, `STATEFUL_LANES` against the lane image's
  state directories, the landing window against `estate_lander/cli.py`, and the manifest entry.
- **`State.lane_digest`:** recorded, and an old state file loads. `finish` prints the lane phases'
  command.

The existing tests' shared fixtures gained three things, and no test body changed:

- `Gh._log` emits the lane verify step's `LANE_DIGEST`, `LANE_REVISION` and `LANE_SHA_TAG`, as
  every release run since #624 does. `prepare` now reads them and refuses without them.
- The throwaway repository's base commit carries a `.cred-consumers.toml`, because `prepare`
  reads it at `origin/main`.
- `Harness.world()` points `lanes_state_dir` into `tmp_path`, so no test touches the real
  directory.

`test_runtime_edges.py`'s host row now pins two hosts, `sds.alobar.net` and
`healthcheck.alobar.net`, and its "second host" control still fails the row.

**Mutation pass:** I made 21 mutations and the tests killed all 21. Each run cleared `__pycache__`
and restored the file, and the restored file is byte-identical. The mutations covered:

- the quiet slot: the `down` exemption, the placeholder's early return, the window;
- the descendant check, the timeout, and a resume taking a new deadline;
- recheck not marking `unfinished`;
- the digest check, the token check applied to the placeholder, and the token check removed;
- a lax volume match;
- the `--schedule-written` and schedule-mapping refusals;
- the lag check's unfinished, stale and skip terms;
- `restored` counting as finished, and both `--restored` checks;
- the matcher's kind;
- `lanes-prepare`'s unfinished block.

## Self-review

I ran `/code-review` (medium) on the diff. I fixed five of its findings:

- An `--add` volume matched only by suffix. It's now exactly `<uuid>_<slug>`.
- Older waiting runs were superseded before the new run was saved, so a later refusal lost a
  deadline. The run is now saved first, and a test covers it.
- A bind mount passed as a named volume.
- `finish` read the registry without a fetch.
- The descendant check ran before the fetch, which I found separately.

I declined three, for the following reasons:

- **`State.lane_digest` is write-only.** Handoff item 8 asks for it, and it's the release's record
  of the lane image beside the orchestrator's.
- **Lock contention shares exit 2 with an unmet slot.** Both mean "re-run later"; the JSON reason
  tells them apart.
- **The `not-a-vocabulary` marker is missing on `LANE_CHECKS`.** The vocabulary guard scans
  `src/orchestrator` only, so I removed the two markers I had added in `scripts/` as noise.

I also replaced two `# type: ignore` comments from my first draft by restructuring, so the diff
adds no suppression comment.

## Concerns

- **The `--add` volume name assumes the verify report's item 2.** That item reads Coolify as naming
  a compose volume `<resource-uuid>_<name>`, and increment 1 confirms it on the placeholder. The
  placeholder isn't stateful, so no phase checks its volume. If production names volumes another
  way, the first stateful `--add` (increment 2) fails closed at `lanes-finish`, and
  `volume_refusal` changes with it.
- **The lane's service must be named after the lane.** `lane_service` picks the compose service
  whose key is the lane's name, as the placeholder's compose does (`placeholder`). Increment 2's
  compose must follow it; deploy.md says so.
- **A second `--add` on a lane with a finished run refuses.** Re-moving a lane after "Rollback for
  one lane" is a new cut-over. The old run files block it until they're archived or the procedure
  gains a step for it. That isn't increment 1's path; I flag it for the rollback increment.
- **`lanes-recheck` doesn't re-check the deadline.** The deadline bounds the wait for a slot; a
  recheck that finds the slot open still repeats both reads.
- **No lane-phase run is checked between `prepare` and the orchestrator swap.** The lag check is
  `prepare`'s, as the ADR places it. A `--tag` run started after `prepare` can't name the new tag
  until it's served, so it can only move a lane to the old served tag.
- **The D-7 grant isn't made.** Until Devon grants `sds-operator` read on `f07b1368-…`, every
  `lanes-prepare` for a lane with a check refuses at the key read (exit 3). The placeholder reads
  no listing, so increment 1's placeholder `--add` doesn't need the grant.
- **Uncovered concern:** the handoff's standards don't cover file locking or local state files.
  I used `fcntl.flock` and `os.replace`, with no further standard to check them against.

## Standards followed

- **Code Brain BLOCK rule 3 (structured JSON logs to stdout, not files):** the lane phases keep
  the command's output style: one JSON object on stdout per run, and instructions on stderr. The run
  files are state the phases read back, not logs.
- **Code Brain BLOCK rule 4 (never swallow an exception silently):** the diff adds no swallow.
  Every new `except` re-raises as `Refused`, `Condition` or `Usage` with its cause chained. The
  `refused`-run path saves the record and re-raises. The Healthchecks read reports the error's type,
  and drops its text so no request detail leaves.
- **Code Brain BLOCK rule 5 (never log secrets, credentials, auth headers, tokens or request
  bodies):** the Healthchecks key is read in-process by `_bws_value` and goes only into the
  `X-Api-Key` header. It never reaches argv, stdout, stderr, a run file or the state file. The
  printed infraops calls name a key and value only for `SDS_LANES_IMAGE_TAG` and
  `LANE_SCHEDULE_ENABLED`, and a test asserts every printed `key=` and `value=`. The token check
  is `test -n`, which prints no value, and the compose names the token only as a `${...}`
  reference.
- **Code Brain WARN rule 6 (no bare `print()` in application code):** the deploy command is a CLI
  that prints its result and instructions. The lane phases follow its convention, `print` to stdout
  and stderr, as `prepare` already does.
- **Infra Brain BLOCK 5 (a Coolify redeploy fires only after the GHCR push completes):** the lane
  digest is read from the one `Release image` run that concluded `success` for the tag's commit,
  from its post-push verify step. `lanes-finish` refuses unless the redeployed container's
  `RepoDigest` equals it.
- **Infra Brain BLOCK 2, 591, 592, 593 (no secrets, `.env` files or BWS tokens in tracked files;
  no inline bootstrap token):** `.bws-secrets.toml` gains a UUID, name and project only. No value,
  `.env` file or token is added, and the scanner finds no BLOCK.
- **Infra Brain BLOCK 3 (never `:latest`):** `--tag` accepts `sha-<40 hex>` only, and the image is
  checked by digest.
