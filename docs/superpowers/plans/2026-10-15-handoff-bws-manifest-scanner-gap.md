# Handoff: the BWS manifest gap and the scanner that can't see it

Read `2026-10-15-handoff-common-rules.md` first. Repositories: security-standards (the scanner),
then each repository whose manifest the fix shows incomplete, starting with the orchestrator.
Backlog item: the orchestrator's `PROJECT.md`, the P2 item that begins "`.bws-secrets.toml` omits
three change-manager bearers".

## The problem

The orchestrator's `.bws-secrets.toml` doesn't list three change-manager bearers its launchers
read: read (`314f276d-…`), propose (`acccb346-…`) and observe (`3b9503da-…`). The backlog item
names the full UUIDs and the launchers that read each.

The scanner's `bws.manifest-matches-usage` rule never flags it.
`security_scan/manifest.py::referenced_uuids` counts a UUID as consumed only on a line that matches
`_BWS_LINE_RX`. The launchers assign the UUIDs to variables such as `CHANGE_MANAGER_UUID="…"` and
pass the variable to `bws` on another line, so the UUID line never matches. Other repositories
that assign UUIDs the same way probably have the same blind spot.

## What to do

1. **Measure before you change anything.** Run the scanner's `referenced_uuids` over every
   repository under `~/Projects` that has a `.bws-secrets.toml` or calls `bws`. For each one, list
   the UUIDs it uses that `referenced_uuids` misses. Find them by following each UUID-holding
   variable to a `bws` call, not by matching the name. Read the distribution before you design the
   fix (`docs/method-lessons.md` in the orchestrator has the lesson on validating a classifier
   against the population).
2. **Design the widened rule so it discriminates.** Many repositories also hold UUIDs that are not
   BWS secrets: Coolify application and project UUIDs, Healthchecks check UUIDs, work-unit ids in
   tests. A rule that counts any UUID assigned to a `*_UUID` variable would report those as
   undeclared secrets. Prove the rule on the population from step 1:
   - it finds every missed BWS UUID;
   - it reports no non-secret UUID.

   Report the counts and every case that fails either test. If no rule meets both, stop and report
   the trade-off to Devon instead of choosing one.
3. **Fix the manifests before the scanner lands.** The session-end hook `bws-scan-gate.sh` runs the
   scanner from the security-standards checkout, so a widened rule BLOCKs every session in any
   repository it newly finds incomplete. In each such repository, open a pull request that adds the
   missing entries in the manifest's existing format, with the secret's current BWS project. For
   the orchestrator, that is the three change-manager bearers, and their project is `Ops /
   Platform`. Read it; don't assume it. Merge those first.
4. **Then land the scanner change** in security-standards, with tests that show the rule firing on
   the launcher pattern and staying quiet on a non-secret UUID. Read security-standards'
   `CLAUDE.md` for how its changes reach the installed hooks: its `make install` deploys some
   artifacts and not others.
5. **Resolve the backlog item** in the orchestrator's `PROJECT.md` (`[x]` with the date and the
   pull request numbers), in a worktree.

## Out of scope

Moving any of these secrets to another project. The change-manager bearers' keepers move in a
later rotation increment, which updates the `project` field again then.
