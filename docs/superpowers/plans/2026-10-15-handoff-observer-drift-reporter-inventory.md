# Handoff: inventory the observer and drift-reporter bearers' consumers

Read `2026-10-15-handoff-common-rules.md` first. Repositories: the orchestrator (its registry), and
read-only every repository that launches a consumer. This is the inventory pull request in
`docs/superpowers/plans/2026-10-14-rotation-increment-6-plan.md`, "Order of work", item 1, for these
two credentials. It's a prerequisite for rotating them. Rotating them is NOT this handoff.

## The problem

Two registry entries in the orchestrator's `.cred-consumers.toml` hold one value today:
`orchestrator-observer-bearer` (BWS `f793576f-…`, project `SDS Operator`) and
`orchestrator-drift-reporter-bearer` (BWS `8998c4ea-…`, project `Ops / Platform`). ADR-0055
amendment 9, decision 3, splits them into two values in one coordinated rotation, after which each
rotates alone. The executor rotates a credential only to the destinations its registry entry lists,
so a missing consumer keeps the old value and breaks when the old value is retired.

- **The observer** has about eight launchers.
- **The drift-reporter's** consumer list is coarse. It names `drift-audit.sh` and the two
  `observe-run.sh` copies, but not everything that calls them.

## What to do

1. **Find every reader of each value** across the portfolio. A reader may name it by BWS UUID, by
   secret name, or through a helper or wrapper that fetches it for its caller. Check the following
   places:
   - every repository under `~/Projects`;
   - `~/.claude/bin` and `~/.claude/hooks`;
   - `~/Library/LaunchAgents`;
   - any crontab;
   - the VPS through infraops' read-only tools.

   For each reader, record:
   - the file;
   - what schedules it, and when;
   - which BWS identity it reads with (which Keychain item);
   - whether a scheduled job's secret path has been proven with only `PATH` and `HOME` set;
   - which of the two values it needs. A reader that posts as the drift-reconciler needs the
     drift-reporter value; one that posts as the observer needs the observer value.

   A reader that fetches a secret and gets "" on failure fails silently. Note each one.
2. **Find each reader's BWS identity's access** to the secret it reads, and to which project. After
   the keepers move to `Rotation / Keepers`, each identity needs a direct grant on exactly the one
   secret it reads. List the grants the move will need. Don't make any.
3. **Write the registry change** to both entries' `[[credential.consumer]]` lists, in the kinds and
   fields the registry already uses. Before merging, confirm that infraops still parses the
   registry: run `security-drift-cli cred-findings` with `INFRADRIFT_CONFIG_DIR` set to a scratch
   directory. In that directory, write a `cred-consumers.list` (newline-separated registry paths)
   that names your worktree's `.cred-consumers.toml` in place of the main tree's, and copies the
   other paths from `~/.config/infra-drift/cred-consumers.list`. The state directory defaults to
   the same scratch directory, so the run doesn't touch the real `cred-rotation-state.json`.
4. **Don't merge until HQ reports the operator-bearer rotation finished.** This is the one
   exception to the common rules' "merging to `main` is fine". The executor's code is pinned, but
   it reads the registry's data live from GitHub `main` and parses the whole file, so a bad edit
   merged during the rotation breaks that rotation too.
5. **After merging**, run `.venv/bin/rotation-worker compare-destinations <id>` for each credential
   and paste its registry half into the pull request as a comment. It reads the registry from
   GitHub `main`, not from your worktree, so before the merge it shows the old list. It exits 2
   because neither credential has a package yet, which is expected.
6. **Don't set `rotated_by_sds`, and don't set `rotate_requested`.**
7. **Report** the reader table, the grants the keeper moves will need, and any reader that doesn't
   fit the executor's consumer kinds (`src/rotation_worker/destinations.py`). An unfit reader is a
   design question for HQ, not something to work around.

## Out of scope

Moving keepers, granting access, splitting the value, writing a rotation package, and building
Healthchecks reads. Each of those comes after a decision Devon hasn't made yet, or after this
inventory.
