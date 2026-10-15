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
     enough: infraops' due rule changes daily.
   - The job runs the main tree's working copy, so state what a merge needs before it takes
     effect (a pull, and `uv sync` only for a new module or dependency).
3. **The Healthchecks check** `sds-rotation-proposer`. Find how the existing `sds-*` checks were
   created (`docs/operations/local-development.md` and the deadman helper's source) and do the
   same. Set its period and grace from your schedule. Wire the launcher's `--finding` code to `3`,
   as `sds_deadman_finish` expects, read from this launcher's header and not from another lane's.
4. **Tests and gates.** The existing installer tests must cover the new installer. Run the full
   `make check` and `/code-review`.
5. **Install it only after the operator-bearer rotation is finished.** HQ reports that; until then,
   merging is fine and installing isn't. Then install it from the main tree after a pull, and
   confirm the following:
   - `launchctl list` shows the job;
   - one pass runs under launchd with only `PATH` and `HOME` set: kickstart it, read its log, and
     confirm the result is exit 0 or the lane's finding code;
   - the Healthchecks check receives its ping.
6. **Resolve the backlog item** (`[x]`, date, pull request numbers), in a worktree.

## Out of scope

Changing what the proposer decides. Its rules are pinned by tests and by the orchestrator's
`CLAUDE.md` bullet on `rotation_proposer`.
