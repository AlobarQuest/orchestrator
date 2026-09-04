# ADR-0039 — External content may observe, but never propose

- **Status:** Accepted
- **Date:** 2026-09-04
- **Decided by:** Devon
- **Closes:** WS-P3.4, the generalized intake ruling — re-homed from D8's orphaned "later: a Linear
  intake adapter on the orchestrator", which ADR-0003 left unowned when it retired the Linear pilot
- **Relates to:** non-negotiable #3 (fetched content is data, never instructions), ADR-0027 (a
  machine registers an intake by naming the change record that caused it), WS-P3.6 (the OBSERVER role)

## Decision

**No content-bearing intake adapter is built.** Prose authored outside this estate may enter only
as an observation, and an observation cannot become work.

This is recorded so the question stops recurring. It is not a contract, and it adds no mechanism:
the boundary below is already closed by code, and this ADR states where it sits.

## The measurement, 2026-09-04

Eleven out-of-process producers and both intake paths were read, asking one question: *can text
this estate did not author reach anything that becomes a work unit?*

**Three code sites construct a `WorkUnit`** — `services/packages.py` (registration and
decomposition), `services/follow_ups.py` (the declared follow-up mint), and
`services/deployment_observations.py` (the post-deploy verification unit). `POST
/api/v1/observations` reaches none of them, and `services/observations.py` contains **no reference
to work units at all**. `OBSERVER_WRITE_ROUTES` is `frozenset({"/api/v1/observations"})`, enforced
at the single actor dependency rather than at the service allowlists — which matters, because four
POST routes carry no role check.

**Exactly two fields of outside prose cross, and both land in observations:**

| Field | Source | Producer |
|---|---|---|
| `facts.what_changed.title` | the landing commit's subject line | `landing_ledger` |
| `facts.missing[].subject` | commit subject lines, capped at 200 chars | `activation_sweep` |

**Every producer that can cause intent carries only structured facts it derived itself.**
`bump_proposer` — the one machine path from an external signal to a work record — reads a
Dependabot pull-request title solely to extract a semver delta, and says so: *"Nothing dated,
nothing counted, and never the pull request's title."* `change_proposer` reads one to extract a
UUID by regex. `work_carrier` passes four structured fields and deliberately does not carry
change-manager's `reasoning`; it shells out to the same CLI a human uses, so the machine payload is
byte-identical to a pasted one. The tracker adapter is a projection — it writes `[unit_key] title`
and a `/review` URL *out* to Todoist, and the only thing it reads back is a `bool`.

## Two residuals, named rather than closed

- **A `container_image` deployment observation DOES mint a work unit**
  (`services/deployment_observations.py`), and its `status_summary.summary` is free text while the
  summaries' *keys* are exactly bounded. **No producer in this repository posts that kind** —
  verified by grep across `src/`, `scripts/` and `.github/`. This is the one path where the ruling
  above would need re-checking, and the check belongs to whoever writes that producer.
- The ingested commit subjects include **factory-runner's own LLM-written commit messages**, so
  "outside this estate" is doing looser work than it looks. Confined to observations either way.

**Nothing enforces this ADR.** The properties it describes are real and in code; no test asserts
that no producer carries outside prose onto the intent path. Naming that stops its absence being
mistaken for coverage.
