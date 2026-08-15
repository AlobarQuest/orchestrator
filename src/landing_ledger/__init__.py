"""The landing ledger: one observation per commit that reached a repository's default branch.

A SEPARATE program (ADR-0002's shape), sibling to `reconciliation_runner` and
`tracker_projection_adapter`. It reads GitHub and writes observations. It shares no import path
with `src/orchestrator/`, and its write surface is one endpoint.
"""
