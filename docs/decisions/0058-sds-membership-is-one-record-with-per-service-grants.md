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

## Amendment, 2026-10-28 — scope comes from the record (bridge layer, Part 2)

Decision 6 is superseded for dispatch admission, which is the first consumer to move; the carrier
and the proposers followed in the same part, so by merge all of them read the record. Decision 1 of the
bridge-layer design (`docs/superpowers/specs/2026-10-27-bridge-layer-design.md`) is implemented: the member record is
the authority for which repositories the factory may work in.

The carrier selects the grant from the package's profile name and admission from the envelope's
`change_class`. They agree because every factory-executable profile's `change_class` equals its
name in intent-packages (`dependency_update.py` sets both to `dependency-update`). If that ever
broke, admission, the binding gate, would refuse with `target_repository_not_granted`; it would
never admit an ungranted repository.

**What changed.** Admission refuses a unit unless the record grants the service matching the
unit's change class on its target repository: `dependency_updates` for a `dependency-update` unit,
`app_changes` for every other class, including the fallback capability name used when an envelope
carries no class. Only a `granted` grant on a member that is not retired counts, and repositories
compare case-folded. The refusal is `target_repository_not_granted`, blocked like the term it
replaces. `target_repository_not_declared` and `target_repository_declaration_unreadable` no longer
exist; the record is read through the request's own session, so there is no longer an unreadable
case, and admission makes no GitHub request for this term.

**Rulings made while planning,** each with its cost if wrong
(`docs/superpowers/plans/2026-10-28-bridge-p2-scope-from-the-record.md`):

1. `factory-target.toml` stays in the target repositories but is no longer authority. Cost: one more
   declaration to keep equal by hand until its remaining readers move; `membership_check` reports
   disagreement daily.
2. The approval-policy grant and the policy file's known-good `target_repositories` list move to
   Part 3. Cost: intent-packages and policy keep gating by hand-kept lists until then.
3. The watcher is unchanged: it retires a record only when every unit of its work is complete, so
   its acting scope already follows from the carrier's. Cost: a stale report about an uncarried
   record in an ungranted repository.
4. A granted repository with no standing package is logged, not reported. Cost: none beyond Part
   3's timing.
5. The capability source's age limit is 48 hours: the portfolio scan runs daily at 03:00 and the
   carrier at 07:05, so one missed scan is tolerated and two refuse. Cost: a carrier pass that holds
   records after two missed scans, loudly.
6. An unreadable record stops a lane with the lane's own code 2, not the scope module's 3. Either
   way the alarm fires.
7. The carrier keeps reading the portfolio scan's file, with the age limit. Cost: capability is up
   to two days stale, never more.
8. The other `membership_check` rows stay. Cost: they report dead configuration for a repository
   nobody granted, which is still true.

Admission and the lanes hold the change-class mapping separately, because the lanes run out of
process; `tests/services/test_target_grant.py` holds the two equal over every class the policy
admits.

## Amendment 2, 2026-10-28 — holds live in the record (bridge layer, Part 4)

**Status:** Accepted (Devon's decisions in `docs/superpowers/specs/2026-10-27-bridge-layer-design.md`,
Part 4; plan `docs/superpowers/plans/2026-10-28-bridge-p4-holds.md`).

A hold is a version ceiling on one dependency of one member, and it is part of the member record:
table `sds_member_holds`, keyed `(member_id, ecosystem, directory, dependency)` with `ceiling`,
`reason`, `set_by` and `set_at`. It is changed the way every other part of the record is: staged
as a `holds` list on a member's change (`sds-members stage`), each entry a `place` (with a ceiling)
or a `lift`, and written only when a person confirms the proposal on `/review`
(`services/membership/proposals.py`, the one writer). A place on an existing key replaces its
ceiling. A lift deletes an existing hold and the confirming event records it; nothing else
removes one, so only a person ends a hold. Member proposals are on the `/review` queue.

The ecosystem is Dependabot's spelling, unconstrained by a CHECK because Dependabot owns the
vocabulary; the three tooling spellings that differ are refused at staging. The directory defaults
to `/`. A ceiling must parse to a numeric release. Reads go through
`services/membership/members.py::holds_for` (in-process) and `GET /api/v1/members` (the lanes), and
a retired member's holds still apply. How each path honours a hold is ADR-0028 amendment 2;
operations: `docs/operations/membership.md`.
