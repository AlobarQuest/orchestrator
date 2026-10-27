"""Does the SDS member record agree with everything that restates it? (ADR-0058)

A separate program beside the client package: `sds_members` reads and stages the record and
imports no other lane; this one compares the record with the declarations and the lists the
rollout, tool, hardening, fork-sync and rotation lanes carry, so it reads those lanes' own data.
Run daily by `scripts/run-membership-check.sh`.
"""
