# ADR 0058 — SDS membership is one record with per-service grants

**Date:** 2026-10-26
**Status:** Accepted (Devon, 2026-10-26, on `docs/superpowers/specs/2026-10-26-sds-membership-design.md`)

## Context

A survey on 2026-10-26 found 21 mechanisms that answer "which repositories does the SDS look
after", five of them hand-written lists, with the forks listed four different ways. Nothing could
say, per project, which services the SDS provides. Devon wants the SDS to be a system that
maintains projects, many of them vibe-coded, with what an IT team provides, and wants membership to
be granted explicitly, never assumed.

## Decision

The detail is in the design spec. In summary:

1. **One member record per repository,** in the orchestrator's database, keyed by `owner/name` as
   GitHub spells it and matched case-insensitively. Apps are links on the member (one repository may
   serve several), not members.
2. **Seven services, each granted separately:** dependency updates, security patches, app changes,
   backups, monitoring, deploys and credential rotation. A grant is `not_assessed`, `remediating`,
   `granted`, `not_applicable` or `withdrawn`. The deploys service's wire value is `rollouts`,
   because the word guards refuse `deploy` in code.
3. **Only a person writes the record.** SYSTEM stages a proposal (`POST /api/v1/member-proposals`);
   a person confirms or withdraws it on `/review`. Confirming applies it in one transaction, bound
   to a digest of the record the person saw. Grants are staged proposals, not `Approval` rows.
4. **The write split is a test.** `test_membership_writers.py` allows one module to write the
   tables and names the three routes that reach it.
5. **The record is readable by any role** (`GET /api/v1/members`). Lanes will move onto it one at a
   time, each with a parity step so no lane loses coverage silently; that follows in later parts.
6. **Existing declarations stay authoritative in phase 1:** `factory-target.toml` and
   change-manager's landing policy. The record mirrors them.

## Consequences

- Adding a project is two steps: stage, then confirm. Nothing enrols a repository by discovery.
- A lane that reads scope fails closed when the record is unreadable.
- Whether the record later becomes the source those declarations are generated from is decided after
  phase 1, with the agreement data in hand.

Operations: `docs/operations/membership.md`.
