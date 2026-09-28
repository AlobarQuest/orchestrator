# ADR-0046 — The lane kill switches default on; each variable stays as the off switch

- **Status:** Accepted
- **Date:** 2026-09-28
- **Decided by:** Devon (Tier 3 item 21, option A)
- **Relates to:** ADR-0010 (the policy artifact can only refuse; the off switch is a separate
  admission term), ADR-0019 increment 5b (the estate-landing switch), ADR-0038 part 2 (the
  inert-landing switch)

## Decision

`dispatch_enabled`, `estate_landing_enabled` and `inert_landing_enabled` in
`src/orchestrator/config.py` default to **true**. Each environment variable
(`ORCHESTRATOR_DISPATCH_ENABLED`, `ORCHESTRATOR_ESTATE_LANDING_ENABLED`,
`ORCHESTRATOR_INERT_LANDING_ENABLED`) is kept, and setting it to `false` still stops that lane with
the same named refusal (`dispatch_disabled`, `landing_not_enabled`). Only the meaning of an
**absent** variable changes.

## Why

All three switches were already on in production by standing decision, dispatch since 2026-08-04
(verified from inside the running container on 2026-09-28: all three read `true`). Each defaulted
off because it was introduced before the lane it guards was activated, and the comment beside each
said so: *the release carrying this code lands nothing until somebody writes an environment
variable.* That reasoning expired at activation, and nothing revisited it.

After activation a false default fails in the wrong direction. A deployment that loses the
variable — a recreated application, an environment not copied, a delete-and-recreate write that
fails half-way (this app's documented env fallback) — silently halts a lane nobody decided to
stop, and none of the three lanes reports that as a finding. The off switch was meant to be a
deliberate act; a false default made *forgetting* one too.

Nothing is loosened. Every other admission term is unchanged and still fails closed: a lane with
no App credentials, no policy source or no estate answer refuses on those terms whatever the
switch says.

## Consequences

- Tests that assert the switched-off behaviour now switch it off by name rather than relying on
  the default (`tests/api/test_dispatch_api.py`, and both landing admission API tests).
  `tests/test_config.py` pins both halves: absent → true, `=false` → false, for all three.
- The local recovery drills (`scripts/drill_common.sh`) export all three as `false`, because a
  drill must never fire a workflow or land a pull request and must not depend on a default for it.
- Once an image carrying this change is running, removing the variables from the production
  environment is harmless, and leaving them is too. Until then production runs the old False
  defaults, so the variables must stay set. Merging this deploys nothing.
