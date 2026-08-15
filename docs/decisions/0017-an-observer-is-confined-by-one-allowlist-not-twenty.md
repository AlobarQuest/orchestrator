# ADR-0017: An observer is confined by one allowlist, not by twenty agreeing

- **Status:** accepted
- **Date:** 2026-08-07
- **Workstream:** WS-P3.6 Increment 1

## Context

Recording an observation required `ActorRole.SYSTEM` — the role that also drives
`commands/ready` and the work handoff. Every observation producer in this estate was therefore
over-privileged **by construction**, not by misconfiguration. `orchestrator-drift-reporter` holds
`system` today, and the only thing stopping it driving the factory is a sentence in `CLAUDE.md`
saying it "must not be borrowed."

This blocks the landing ledger (Increment 2), whose value is backfilling history. `agent_id`
attribution is permanent: history written under an over-privileged identity cannot be rewritten
later, so the role has to exist *before* the ledger does.

## Decision

Add an `OBSERVER` role whose entire write surface is `POST /api/v1/observations`, one shared
registry actor (`orchestrator-observer`), and one credential shared by every observe-and-report
producer — now and later.

**Per-producer identities are deliberately not created.** The observation row already carries
`source_system` and `source_reference`, and `OBSERVATION_SOURCE_SYSTEMS` already distinguishes
producers. The row says who spoke, so the credential does not have to, and a new producer needs
no new credential, no registry commit and no image rebuild.

**`SYSTEM` keeps its ability to record observations.** Removing it would be a second change
riding a security change: every producer posts as `SYSTEM` today, so dropping it would couple
this merge to the runtime credential rewrite and break the producers in between. `OBSERVER` is
the role a producer *should* hold; narrowing `SYSTEM` out is a later, separately-verifiable step.

### The part that is a real decision: where the confinement lives

Every other role in this system is confined by roughly twenty service-level allowlists that
happen to agree with each other. The obvious move was to rely on that agreement — a new enum
member is refused by every `if actor.role is not X` gate automatically, which is genuinely true
and genuinely load-bearing.

**It is not sufficient, because the agreement is not real.** Reading every role gate in `src/`
found four POST routes that carry no role check at all: `/work-units/{id}/preflight` and the
three `/event-publications/*` routes. `record_preflight` writes a `ContextSnapshot` row and an
event. A role confined only by service guards would have reached all four, and the increment's
claim — "may record observations and nothing else" — would have been false on the day it shipped.

So `OBSERVER` is confined **once, positively**, at `api/dependencies.py::_confine_observer`: the
single dependency through which *both* routers obtain their actor. It may POST to the routes in
`OBSERVER_WRITE_ROUTES` and to nothing else.

Three properties follow, and each was the reason:

1. **Total.** One rule covers all 50 POST routes, including the four nothing else covers.
2. **Default-refuse for the future.** A route added tomorrow is refused for `OBSERVER` without
   its author knowing this rule exists. For a role that must never gain a surface silently, the
   default has to point this way; twenty allowlists default the other way.
3. **Testable as a whole.** The refusal fires in the actor dependency, *above* request
   validation, so the negative test can drive every route with an empty body and read a real
   `role_forbidden` rather than a `422` that proves nothing. That is what makes the acceptance
   test the whole inventory instead of four hand-picked surfaces.

The service-level guards are **not** replaced. They are the layer that still refuses an in-process
or future non-HTTP caller, and each is tested directly, below the HTTP layer.

**Reads are not confined.** Every machine role already reads any unit's evidence pack, runner
brief and ledger. Making `OBSERVER` the sole exception would be an unrelated policy change riding
a security change. The claim this role makes is about what it can *change*.

## Consequences

- `OBSERVATION_ROLES` in `services/observations.py` is the only service gate naming `OBSERVER`.
  A second one widens what an observation producer may do to this estate.
- `_ALLOWED_ROLES` in `services/decomposition.py` held **every** member of `ActorRole`, so it
  read as "the role does not matter here" — the one gate a new role could have joined by
  assumption rather than decision. `OBSERVER` is excluded: proposing a breakdown authors work
  units, and authorship is not observation. That set must stay an enumeration.
- The four ungated POST routes are a **pre-existing** gap that affects `WORKER` and `VERIFIER`
  equally. This ADR routes around it for one role; it does not close it. Closing it means
  deciding which roles may preflight and queue publications, which is a behaviour change for
  existing roles and belongs to its own increment.
- `authenticate_m2m` and `ORCHESTRATOR_M2M_ROLES` needed **no change**: `ActorRole(value)` and the
  generic `m2m_roles.get(...)` promotion already resolve any member of the enum.

## Alternatives considered

- **Rely on the service allowlists alone.** Rejected: false for four routes today, and silently
  false for every route added later.
- **Add a role check to each of the four ungated routes.** Rejected: it decides, in passing, which
  roles may use surfaces this increment did not study, and it still leaves the next new route open.
- **Reuse `drift-sync-v1` as the authority profile.** Rejected: it grants `change_filing` and
  `email_send`. A shared observation credential claiming those reproduces the exact defect this
  increment exists to remove.
