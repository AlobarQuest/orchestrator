# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: Launchers and installers (`scripts/`)

- Every LaunchAgent installer sources `scripts/sds-install.sh` and calls
  `sds_refuse_linked_worktree` before writing; `tests/scripts/test_launchd_installers.py` enforces
  it. Detail: `docs/operations/local-development.md`.
- `sds_deadman_finish` pings success for 0 and for the lane's declared `--finding` code, and
  `sds_outcome_end` for 0 and its `--told` code; only other codes ping `/fail`, so the check
  answers liveness, never whether something was found. A Healthchecks key a lane can't read means
  no ping, heard as the check going down after its grace (ADR-0021 amendment 3). Detail:
  `docs/operations/local-development.md`.
- The outcome-alarm launchers (`run-bump-driver.sh`, `run-bump-proposer.sh`,
  `run-change-proposer.sh`, `run-fork-harden.sh`, `run-fork-sync-watch.sh`,
  `run-membership-check.sh`, `run-rotation-decomposer.sh`, `run-work-carrier.sh`) run through
  `sds_outcome_begin`/`sds_outcome_end` in `scripts/sds-deadman.sh`: `/fail` with the report on
  every non-zero exit, findings included, except a `--told` code. The bump driver, both bridge
  proposers, the carrier and the rotation decomposer ping only on their acting run; the other
  three on every run. A launcher passes `--told <code>` only when every exit with that code was
  delivered as a keyed alert and a failed delivery exits with another code (`run-bump-driver.sh`,
  2 alone); `test_sds_deadman.py` holds it to the launcher's `EXIT CODES` block. They never arm the
  dead-man switch, and a launcher that doesn't arm it must not contain the arming function's name,
  which `test_sds_deadman.py` keys on. Detail: ADR-0021 amendments 2 and 3.
