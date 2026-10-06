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
  summaries' *keys* are exactly bounded. **One producer posts that kind, `image_release`**, and
  the re-check it needed is recorded in Amendment 1 below.
- The ingested commit subjects include **factory-runner's own LLM-written commit messages**, so
  "outside this estate" is doing looser work than it looks. Confined to observations either way.

**Nothing enforces this ADR.** The properties it describes are real and in code; no test asserts
that no producer carries outside prose onto the intent path. Naming that stops its absence being
mistaken for coverage.

## Amendment 1 (2026-10-05): the orchestrator's deploy step mints post-deploy units

Devon chose on 2026-10-05 (SDS 1.1 item 5a) that the orchestrator's deploy binds every completed
factory unit its image carries and files the `container_image` deployment observation. That
observation mints one verifier-owned post-deploy unit per binding, and the same deploy session
verifies it. The producer is `src/image_release` (`image-release bind`). A person runs it in the
deploy session after the running container's digest has been checked against the pushed digest.
It is not scheduled, and no external content triggers it.

**What it binds.** A unit is bound to the first deployed image that carries it, from this
program's first run onward. A unit is bound when the built commit carries its landing commit and
the revision production served before the swap (`--previous-commit`) does not. Units an earlier
image shipped are not bound. The program writes nothing until production serves the built commit.

**The boundary.** This program alone may send a `container_image` binding or observation. The
exception is carried in its own row of `tests/architecture/test_external_content_observes_only.py`
(`mints_post_deploy_units`). Exactly one row may carry it, and that row's payloads must name
`container_image` explicitly. Every other program, including the activation sweep's machine-local
`bind`, is held to `machine_local` as before.

**The re-check the first residual asked for.** Everything the program reads is either one of the
orchestrator's own production endpoints (`/health/live`, `/health/ready`, `/openapi.json`, the
dead-letter status with and without a credential, the candidate read) or the operator's verified
digest and build identity. The minted unit's title and outcome are composed by the server from the
binding's digest and the base URL. The observation's one free-text field, `status_summary.summary`,
is composed by the program from status codes and counts. No text authored outside this estate
reaches the work path, so the ruling holds.
