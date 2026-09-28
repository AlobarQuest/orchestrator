# ADR-0047 — The reach grandfathering table is removed; no revision is exempt from declaring reach

- **Status:** Accepted
- **Date:** 2026-09-28
- **Decided by:** Devon (Tier 3 item 25: "settle and remove in one change")
- **Relates to:** ADR-0009 (reach), ADR-0010 (the policy artifact is a refusal table),
  ADR-0012 (the change window, whose undeclared-reach exposure was this list)

## Decision

`[grandfathered]` is deleted from `src/orchestrator/factory-policy.toml`, together with
`Grandfathering`, `FactoryPolicy.admission_refusals`, `FactoryPolicy.require_live_subject`, the
liveness query in `reach_admission`, and the `grandfathered` field of `GET /api/v1/factory-policy`.
The schema moves from 5 to 6, and `SUPPORTED_SCHEMA_VERSIONS` is exactly `{6}`. Because
`[grandfathered]` is no longer a known top-level key, a document still carrying it does not load.

Every revision is now held to one rule: a package that declares no reach is refused admission as
`reach_undeclared`, whatever its id or age.

## Why

The table was introduced by WS-P2.18 Increment 4 (2026-08-01) to exempt approved revisions that
predated the `reach` key. It named one revision, `wsp211-conformance-kit` rev 1
(`f921c842-52b0-46f1-8568-caf5429d2d6b`), and was built to delete itself once that revision could
no longer produce work. It never could be settled safely while the table shipped: settling it would
have stopped the artifact loading, so the table had to go in the same change.

Before removal, production was asked whether the revision had any work units. On 2026-09-28 the
revision-anchored traceability query returned **0 chains** for it. That query selects every unit of
a revision with no state filter. As a positive control, the same query on the revision of the one
in-flight unit returned **1 chain** containing that unit. No in-flight unit belongs to it either.
The revision has never been broken down.

## Consequences

- `wsp211-conformance-kit` rev 1 can no longer be admitted without a declared reach. That is the
  intended outcome. It stays on the `/review` queue because nothing retires a revision.
- Merging deploys nothing. Production runs schema 5 with the table until an image carrying this
  ships. Until then the old hazard stands: do not break that revision down and settle it.
- `reach_admission_refusal` no longer needs a database session and no longer takes one.
- The change window's exposure to undeclared reach (ADR-0012) is now empty, because nothing with an
  undeclared reach is admitted.
