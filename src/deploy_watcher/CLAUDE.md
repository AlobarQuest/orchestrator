# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The deploy watcher (`src/deploy_watcher/`)

- `deploy_watcher` derives a unit from the merge commit's `SDS-Unit:` trailer, confirmed against
  `pr_merge` history; the change record only names which pull request to watch, and any status
  suffices. Detail: `docs/operations/landing-lanes.md`.
- Moving a rollout workflow is a paired change: re-transcribe it in
  `src/deploy_watcher/workflows.py` AND ratify the derived criteria plus the landing blob pin in
  change-manager's `app/deploy_policy.py`, or its records stay silently pending. Detail:
  `docs/operations/landing-lanes.md`.
