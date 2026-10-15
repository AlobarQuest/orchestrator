# Handoff: schedule the rotation proposer

Read `2026-10-15-handoff-common-rules.md` first. Repository: the orchestrator. Backlog item: the
orchestrator's `PROJECT.md`, the P2 item "Schedule the rotation proposer".

## The problem

`scripts/run-rotation-proposer.sh` has no LaunchAgent and no Healthchecks check. A rotation starts
only when someone runs it by hand, and its dead-man switch logs "alerting disabled". Every other
scheduled lane has both.

## What to do

1. **Read** `docs/operations/rotation-proposer.md` and the header of
   `scripts/run-rotation-proposer.sh` in full, including its exit codes: `3` is this lane's finding
   code, and `2` means it couldn't read its inputs. Then read the header of one existing installer
   with a writing pass, `scripts/install-work-carrier-launchd.sh`, and
   `scripts/install-bump-proposer-launchd.sh`. The bump proposer shares this lane's intent-packages
   checkout.
2. **Write `scripts/install-rotation-proposer-launchd.sh` and its plist,** modelled on the existing
   installers. These rules come from the orchestrator's `CLAUDE.md`, and the tests enforce them:
   - Source `scripts/sds-install.sh` and call `sds_refuse_linked_worktree` before writing
     (`tests/scripts/test_launchd_installers.py`).
   - The job passes `--submit`. The header must say what a writing pass can and can't do:
     - it revises and publishes a package, and proposes a change record;
     - it never approves either: a named human approves the revision, and a click approves the
       record.
   - Pick the schedule so the pass never runs while the bump proposer's pass may be using the
     shared checkout. Read that installer's time, and state why yours differs. One pass a day is
     enough: infraops' due rule changes daily. Measure, don't assume, how long a pass takes: the
     log's modification time against the scheduled minute. A bump pass ended at 06:50:09 on
     2026-10-15, and a rotation dry run takes about one second. The work carrier (07:05) and the
     activation sweep (07:10) read the same checkout, so avoid those minutes too. The carry reads
     only approved records, so a record this pass proposes is carried at the first 07:05 after
     the click, whatever time the pass runs; the header says so.
   - The job runs the main tree's working copy, so state what a merge needs before it takes
     effect (a pull, and `uv sync` only for a new module or dependency).
3. **The Healthchecks check** `sds-rotation-proposer`. The launcher already arms the dead-man
   switch as `sds_deadman_arm sds-rotation-proposer --finding 3`, so there is nothing to wire:
   confirm that `3` matches the launcher's header. No repository documents how the existing
   `sds-*` checks were created. `sds-deadman.sh` only lists checks and matches one by exact name.
   Create the check with the Healthchecks Management API (`POST /api/v3/checks/`), using the key
   at BWS `260cc8ad-f170-44cc-a672-b47000df3350` (`SDS_DEADMAN_API_KEY_UUID`), fetched with the
   broad identity and used in the same process, never printed. Creating needs a read-write key;
   a listing that succeeds doesn't prove the key can write. Copy the channel assignment and the
   timeout and grace pattern from an existing daily check such as `sds-bump-proposer`, and set the
   period and grace from your schedule. Create it at install time, not before: once a check has
   a ping, any dry run starts its silence timer.
4. **Tests and gates.** The existing installer tests must cover the new installer. Run the full
   `make check` and `/code-review`.
5. **Install it only after the operator-bearer rotation is finished.** HQ reports that; until then,
   merging is fine and installing isn't. Before installing, run a dry run from the main tree
   (bare `scripts/run-rotation-proposer.sh`, which writes nothing). On 2026-10-15 it printed
   `would-propose` for `orchestrator-operator-bearer`, the rotation HQ is running. If it still
   does after the rotation, infraops hasn't recorded that rotation (the P2 backlog item on
   `record-rotation`), and the first scheduled pass acts on a rotation that already happened:
   stop and report rather than install. Then install it from the main tree after a pull, create
   the check, and confirm the following:
   - `launchctl list` shows the job;
   - one pass runs under launchd with only `PATH` and `HOME` set: kickstart it, read its log, and
     confirm the result is exit 0 or the lane's finding code;
   - the Healthchecks check receives its ping.
6. **Resolve the backlog item** (`[x]`, date, pull request numbers), in a worktree.

## Out of scope

Changing what the proposer decides. Its rules are pinned by tests and by the orchestrator's
`CLAUDE.md` bullet on `rotation_proposer`.
