"""Observe the production rollout a landed pull request caused, and record what happened.

ADR-0019 increment 2. A SEPARATE program (ADR-0002's shape), sibling to `landing_ledger`,
`reconciliation_runner` and `tracker_projection_adapter`. It shares no import path with
`src/orchestrator/`: hosting it in this repository is a packaging choice, not a coupling, and
the auto-merge lane it serves has no orchestrator involvement at all.

It reads GitHub -- which workflow run a merge caused, and how that run concluded -- and appends
one observation to the change record change-manager already holds. It REPORTS. It never reverts,
never re-points an image tag, never redeploys and never moves a record's state.
"""
