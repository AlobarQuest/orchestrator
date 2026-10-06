# ADR-0053 — Admission's capability and change-class lists move into the policy artifact

- **Status:** Accepted
- **Date:** 2026-10-06
- **Decided by:** Devon (SDS 1.1 item 3a-1, "Admission keeps two checks; posture moves to policy")
- **Relates to:** ADR-0010 (the policy artifact is a refusal table), ADR-0011 (known-good patterns
  withhold an objection), ADR-0015 amendment 4 (target repositories left the environment the same
  way), ADR-0046 (the lane kill switches default on)

## Decision

`ORCHESTRATOR_DISPATCH_ENABLED_CAPABILITIES` and `ORCHESTRATOR_DISPATCH_ALLOWED_CHANGE_CLASSES` are
deleted from `Settings` and `DispatchSettings`. Their values move into a new top-level
`[admission]` table of `src/orchestrator/factory-policy.toml`, as `capabilities` and
`change_classes`, with a `rationale` and `decided` like every row. The schema moves from 6 to 7, and
`SUPPORTED_SCHEMA_VERSIONS` is exactly `{7}`.

Admission reads the table through `reach_admission.posture_refusal`, which calls
`FactoryPolicy.posture_refusal` inside `_envelope_reason`, in the slot the two settings occupied. The
refusal codes are unchanged (`capability_not_enabled`, then `change_class_not_allowed`), and so is
their order. `ORCHESTRATOR_DISPATCH_ENABLED` stays in the environment as the off switch.

## The two lists are allowlists, and the artifact may only refuse

They are consistent because the table is written the way a known-good pattern is (ADR-0011).
Every unit's `required_capability` draws `capability_not_enabled`, and every change class draws
`change_class_not_allowed`. A value named in the table withholds that one objection and nothing
else. That is still a refusal: it is "refuse every value outside this declared set".

It holds only if absence keeps today's behaviour, and it does:

- **An empty list refuses every value.** The settings defaulted to an empty `frozenset`, and the
  check was `not in`, so an unset variable refused everything. The table reads the same way.
- **A missing table, or a missing list, stops the document loading.** It does not fall back to an
  empty or a permissive posture. A document that does not load permits nothing, and admission
  reports `reach_policy_unreadable`.
- **A missing `change_class` still falls back to `required_capability`.** No capability name is a
  listed change class, so such a unit is refused as `change_class_not_allowed`, as it was before.

## The artifact cannot widen beyond the code or the kill switch

- `factory_policy.py` still imports no config, so it cannot read the off switch, which is checked
  first.
- Withholding the posture objections leaves every other term standing: credentials, state, reach,
  estate, human authority, `capability_not_authorized`, `capability_outside_runner_vocabulary`, the
  target declaration, the runner authority rules, conformance and the change window.
- A capability outside `RUNNER_CAPABILITIES` is a load error. The table cannot name work no runner
  performs, such as `post_deploy_verification`. Change classes are free strings and have no code
  vocabulary to bound them; the list is the bound.

## Why top-level, not per reach row

The question is what kind of work the factory takes, and the answer does not depend on what the
work touches. Putting it per row would mean four copies of one value, which the artifact's header
rules out. Or it would add a reach-keyed restriction (union of refusals across a unit's reach set)
that nobody decided.

## Values carried over

The table ships with the values production enforced through the environment:

- `capabilities = ["github.pr.create", "repo.edit"]`
- `change_classes = ["dependency-update", "maintenance-remediation", "software-delivery"]`

Source: the standing decisions recorded in `docs/operations/driving-a-unit.md` #66, which were
container-verified on 2026-08-04. `maintenance-remediation` was added on 2026-08-03; `github.pr.create`
and `software-delivery` were added on 2026-08-04 (archive #147 and the WS-P2.35 note). The repository
records no later widening. These values were not re-read from the live container for this change.

## Consequences

- Widening either list is an edit to the artifact and costs a release. That is the same cost every
  policy change already has (deploy.md #82).
- `GET /api/v1/factory-policy` serves the table under `admission`. That is the deploy check that the
  running image enforces the intended posture.
- After the release, the two environment variables are dead. pydantic-settings ignores undeclared
  variables, so deleting them from the Coolify application can happen before or after the swap.
- A test that dispatches a harness envelope with no `change_class` opts in to the `harness_posture`
  fixture (`tests/_support/posture.py`). That fixture is the shipped table plus the fallback
  `repo.edit` change class. The runner-envelope contract test dispatches against the shipped table
  unmodified.
