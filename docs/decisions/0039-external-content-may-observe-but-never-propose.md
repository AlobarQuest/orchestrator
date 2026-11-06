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

## Amendment 2 (2026-11-06): outside prose becomes work only through a restatement the owner approves

- **Status:** Proposed. The direction is Devon's decision of 2026-10-28 (SDS year plan, "Decisions
  and open questions": "Amend ADR-0039"), and the text is the plan's Phase 4 "Request intake". This
  amendment awaits acceptance. Until it's accepted, the Decision section stands unamended.
- **Relates to:** ADR-0027 (a machine registers an intake by naming the change record that caused
  it), ADR-0058 (the member record's `app_changes` grant), ADR-0028 (bump-created standing
  packages), ADR-0055 (rotation packages)

This amendment builds nothing. It states the boundary that Phase 4's request intake is to be built
inside, so that the intake is designed against a ruling rather than ahead of one.

### What changes

The Decision's second sentence becomes: prose authored outside this estate may enter as an
observation, and it may enter as a **request**. An observation still can't become work. A request
becomes work only through a structured restatement that the member's owner approves. The request
itself never becomes work, and nothing in it is read as an instruction.

The first sentence, "No content-bearing intake adapter is built", is narrowed rather than kept:
Phase 4 builds one intake that carries outside prose, the request, and it carries that prose only
as far as the triage agent and the ticket. Every other route stays closed.

### What stays

- **Observations never become work on their own.** `POST /api/v1/observations` reaches no code
  that constructs a `WorkUnit`, `OBSERVER_WRITE_ROUTES` is unchanged, and the request path adds no
  fourth constructor: an approved restatement, once committed to intent-packages, enters through
  `register_package_intake` (`services/intake/package_intake.py`), the intake path every package
  already takes, and its units are constructed in `services/intake/packages.py`. The measurement
  earlier in this file names the constructor modules at their locations when ADR-0039 was written
  (`services/packages.py` among them); all three have since moved, and today's three are
  `WORK_UNIT_CONSTRUCTORS` in `tests/architecture/test_external_content_observes_only.py`.
  `test_exactly_three_modules_construct_a_work_unit` stays at three.
- **Every machine producer still carries only structured facts it derived itself.** The
  dependency-update and rotation lanes keep today's path, and so does every other
  machine-originated update; previews and owner acceptance apply only to request-originated app
  changes.
- **Amendment 1 is untouched.** `image_release` remains the one program that may send a
  `container_image` binding or observation.
- **The residuals stay named.** A restatement is LLM-written text, like factory-runner's commit
  messages. The ruling doesn't rest on the restatement being trustworthy prose. It rests on the
  owner approving the exact bytes and on authority coming from somewhere else.

### The trust boundary

1. **The request is data.** The owner submits it through a web form, and later by email. It's
   stored as data in a ticket and is never an instruction to any program, the triage agent
   included (non-negotiable #3).
2. **The triage agent is read-only.** It reads the target repository and the request, nothing
   more, and it holds no write tools. It classifies the request (change, bug, question,
   operational task, or out of scope), asks clarifying questions in the ticket, and drafts an
   intent package: a plain-language restatement, acceptance criteria the owner can observe, reach,
   risk, and a cost estimate (the year plan's Phase 4 "Request intake", step 2). The ticket is the
   only place its output goes: it drafts the restatement into the ticket and commits nothing.
   `register_package_intake` takes a package committed in intent-packages, so something other
   than the triage agent commits the bytes the owner approved; what that is, the ticket-intake
   increment decides (see "Planned, not decided").
3. **The owner approves the restatement, never the request.** The approval binds to the
   restatement's package fingerprint: the `canonical_package_hash` that a lineage approval names
   and that the intake emitter verifies before it emits (`work_carrier/prepare.py`,
   `package_sources.py`). A change to any field the hash covers is a different hash and needs a new
   approval. The approval never binds to the request text, so a request edited after approval
   changes nothing that was approved. The owner is the member record's `owner`; for Devon's own
   apps, the mock client, that's Devon acting only as owner.
4. **Authority never comes from the request.** What a unit may do comes from the member record's
   `app_changes` grant on its target repository (ADR-0058: asked at the decomposition proposal, its
   approval, the claim, admission, and every landing) and from the envelope that policy computes
   for it. No gate reads a capability, command, reach, or repository from the request. The
   restatement does declare reach and risk, and the owner approves those values, but no envelope
   it produces can pass what policy admits and the grant allows.
5. **The ticket is the cause the intake names.** A ticket is to be a change-manager change record
   with a new `source`. ADR-0027 already requires a machine-registered intake to name the change
   record that caused it (`intake_change_record_required` checks that one is named; the work
   carrier carries only approved records), so the request path needs no new exception there. No
   policy approves a ticket's record: the ticket source is to be added outside change-manager's
   `POLICY_APPROVED_SOURCES` (`app/sources.py`, today only the `deploy` source) and given no policy
   of its own, so its record waits for a person. change-manager's work policy
   (`app/work_policy.py`) is no part of this: it applies only to `work`-source records. This is a
   planned commitment, verified by the ticket-intake increment.
6. **A request over a size limit becomes a quote instead of a package.**
7. **The existing loop builds it** (carry, admission, factory-runner, verifier), and **the owner
   accepts a preview** before landing and deploy run.

### How this is enforced or measured

Since 2026-09-27 (#289), `tests/architecture/test_external_content_observes_only.py` enforces the
ruling: every out-of-process program under `src/` has a row whose permitted writes must equal what
its source can send, and the work-unit constructors are pinned. The sentence "Nothing enforces this
ADR" earlier in this file describes the ADR as first written. The request path keeps that guard and
adds the following items. Each is planned, and none exists yet.

| Item | Delivered by |
|---|---|
| The triage agent, if it's a program under `src/`, gets a `CLASSIFICATION` row with role `NOT_AN_ORCHESTRATOR_WRITER`, so the existing guard fails the build if it can send any orchestrator write. | Phase 4, the triage-agent increment |
| A check that the triage agent holds no write tools at all, not only no orchestrator writes: the existing predicate reads orchestrator paths, not tool grants, so it can't see a repository or ticket write. | Phase 4, the triage-agent increment |
| A test that an intake registered from a ticket is refused unless its lineage approval names the package's `canonical_package_hash` and the member's owner, and that no request field reaches the envelope. | Phase 4, the ticket-intake increment |
| `owner_acceptance` as a criterion type only the member's owner can decide: never in `DETERMINISTIC_TYPES`, routed through `human_may_adjudicate`, and the verifier can't decide it. Landing waits for it. A request-originated package carries no `human_review` criterion, as a bump-created standing package carries none today (ADR-0028); rotation packages do use `human_review` (ADR-0055) and are unaffected. | Phase 4, the preview increment |
| `test_exactly_three_modules_construct_a_work_unit` and `test_the_observations_service_reaches_no_work_unit_constructor` stay unchanged and green. | Every increment |

### Planned, not decided

The following later work is planned, and this amendment decides none of its mechanism. Each item
names the Phase 4 increment that decides and verifies it, the same increments as the preceding
table:

- A ticket `source` in change-manager, with a client-facing portal in front of it, added outside
  `POLICY_APPROVED_SOURCES` and with no approval policy of its own. Verified by the ticket-intake
  increment.
- What commits the owner-approved restatement to intent-packages, where `register_package_intake`
  reads it. The triage agent doesn't; the mechanism is undecided. Verified by the ticket-intake
  increment.
- The triage agent: where it runs, which model, and how its read-only reach is confined. Verified
  by the triage-agent increment.
- Owner acceptance of a preview: hosting, scrubbed data, test-mode credentials, the owner's Alobar
  ID role, and what an unanswered preview does. Verified by the preview increment.
- The size limit, and how a request over it becomes a quote. Verified by the ticket-intake
  increment.
- How the owner's approval is recorded against the fingerprint. Verified by the ticket-intake
  increment.
- How "the member's owner" maps to an identity that can approve a restatement and decide
  `owner_acceptance`. Today `SdsMember.owner` is a free string (`persistence/models.py`), and
  `human_may_adjudicate` (`services/verifier/verifier_evaluators.py`) admits any human on a
  human-floor type, so nothing yet ties a decision to the owner. Verified by the ticket-intake
  increment for the restatement approval, and by the preview increment for `owner_acceptance`.

### Open questions

- How many revision rounds come with a request before the work becomes a quote. This is a pricing
  decision.
- The mock-client graduation bar: for example, N requests from intake to production with Devon
  acting only as owner and zero operator interventions.
