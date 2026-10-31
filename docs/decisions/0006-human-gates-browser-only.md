# ADR-0006 — Human gates are browser-only, permanently

**Date:** 2026-07-28
**Status:** Accepted 2026-07-28 by Devon.
**Closes:** remediation item 3.1 (the human-actor trilemma), `PROJECT.md:52`.
**Hard prerequisite for:** WS-P2.9 (the `factory` CLI front door). This was settled while WS-P2.9
was being scoped rather than discovered inside it, which is what item 3.2 asked for.

## Context

Three gates in this system require `ActorRole.HUMAN`: package intake, per-unit authority approval,
and the decomposition decision. All three M2M credentials are worker / system / verifier — **there
is no HUMAN credential**, and the 2026-07-27 production drill run confirmed there is no way to
obtain one against production. Two consequences had accumulated as standing improvisations:

- `orchestrator intake-package` and `orchestrator record-approval` **cannot execute against
  production at all.** They call services that raise unless the actor is HUMAN, and they sit on the
  M2M-only `orchestrator-api` Traefik router, so a browser session's identity is stripped before it
  arrives and a SYSTEM bearer is rejected as non-human. **No principal can reach them.** Every
  session that met them rediscovered this.
- Package intake was therefore crossed by pasting a `fetch()` into browser devtools — the last gate
  with no surface of its own. Authority approval had the same shape until WS-6.3 gave it a form;
  the decomposition decision has always had one.

The trilemma (remediation 3.1) offered three ways out: **(a)** a human credential path for the CLI
(device/OIDC against Alobar ID yielding a HUMAN actor); **(b)** `POST` routes in `/review` for the
gates that lack them; **(c)** an explicit ruling that these gates are permanently browser-only.

## Decision

**(c), with (b) as its implementation.** Human gates are interactive-browser-session-only,
**permanently**. **No standing HUMAN credential will ever exist** — not for the CLI, not for
automation, not for a build agent. Every human gate gets a real `/review` surface instead, and this
PR gives intake the one it was missing.

## Rationale

Human authority stays bound to a **live Authentik session**. That is the property being bought, and
it is worth more than the ergonomics it costs:

- A standing HUMAN credential is a durable, stealable, delegable artifact. Once it exists, anything
  holding it can approve authority envelopes — and an authority approval is precisely the gate that
  says *this envelope may mutate that repository with these commands*. A credential file cannot
  decline.
- This system's agents combine access to external data (repos, issues, trackers, email) with
  infrastructure mutation. That is the lethal trifecta, and the containment that actually holds is
  that the human step **cannot be performed by the agent** — not that the agent is asked politely
  not to perform it. A HUMAN credential on disk converts a prompt-injection into an approved
  envelope in one hop.
- The gates are low-frequency by construction (one intake per package, one authority approval per
  unit, one decomposition decision per proposal). The cost of a browser click at each is small and
  bounded; the cost of a leaked standing approval credential is not.
- It is also honest about what was already true. There has never *been* a HUMAN credential; option
  (c) ratifies the system's actual posture instead of leaving it as an unfilled gap that every
  session re-litigates.

## Consequences

**For WS-P2.9 (the `factory` CLI front door):** the CLI wraps every **non-human** surface — intake
payload emission, conformance claims, decomposition proposal, verifier reads, status. At each
**human** gate it stops and hands off: it deep-links the `/review` page, or puts the payload on the
clipboard for the form. **It never impersonates a human**, and it must not grow a flag that
pretends to. A CLI that could satisfy `_require_human` would defeat this ADR by construction, so
"wrap the API" is not an available implementation for those three gates.

**For the surfaces:** every human gate now has a form under the `orchestrator-review` router
(`_human` + CSRF), or a dedicated forward-auth `/api` router:

| gate | human surface |
|---|---|
| package intake | ✅ `GET /review/intakes/new` → `POST /review/intakes` (**this ADR**) |
| staged package intake | ✅ `GET /review/staged-intakes/{id}` → `POST /review/staged-intakes/{id}/confirm` or `/withdraw` (amendment 1) |
| authority approval | ✅ `POST /review/units/{id}/authority-approval` (WS-6.3) |
| decomposition decision | ✅ `POST /review/decomposition-proposals/{id}/approve` |

The intake form runs the payload through the **same** `PackageIntakeRegistration` model and the
**same** `register_package_intake` service as `POST /api/v1/package-intakes`. No validation is
relaxed for the browser: the service still accepts only `caller_attested_cli_verified` payloads,
still demands `expected_version: 0`, still refuses a non-approved package. The form is a new *way
in*, not a new *set of rules*.

**For the two dead CLI commands:** `orchestrator intake-package` and `orchestrator record-approval`
are annotated **local-development-only** rather than deleted. They remain useful against a local
orchestrator and for protocol fixtures, where a HUMAN actor is available; the annotation exists so
that no future session rediscovers that they cannot execute against production.

**What this does not decide:** nothing here changes how *machine* actors authenticate, and nothing
here weakens the requirement that a human authority approval be bound to the exact envelope
fingerprint. Both are unchanged.

## Costs accepted

- **A human gate cannot be crossed without a browser.** Headless/CI paths through intake, authority
  approval, or decomposition approval are permanently unavailable. This is the point, but it does
  mean the factory can never be fully unattended end-to-end by design — the human gates are the
  attended part.
- **Automation driving a browser session remains possible** and was used for the 2026-07-27 drill
  run under explicit authorisation (disposition A2). This ADR governs *credentials*, not what a
  supervising human chooses to automate inside their own live session; the drill evidence records
  that distinction rather than glossing it. Anyone relying on "a human personally clicked this"
  must establish it separately — no exit criterion currently claims it.

## Evidence base

- `~/docs/software-delivery-system/2026-07-27-production-recovery-drill-run.md` — the production
  run that established the unreachable-route facts first-hand.
- `docs/operations/production-drill-adaptations.md` — the per-gate production variants, including
  which commands are GUI-only.
- `docs/superpowers/plans/2026-07-12-remediation-order.md` — Phase 3, and the 2026-07-28 Phases 1–6
  reconciliation block that records this decision.

## Amendment 1, 2026-10-06 — intake is staged by the CLI and confirmed in the browser

**Decided by Devon, 2026-10-06** (SDS 1.1 items 2a and L1a, option A). Intake stops being a payload
paste and becomes a decision surface.

- **The CLI stages.** `POST /api/v1/staged-intakes` takes the same `PackageIntakeRegistration` body
  as `POST /api/v1/package-intakes` and stores it in `staged_package_intakes`. Only the SYSTEM actor
  may call it (`orchestrator-system`, which the intent-packages CLI already holds); every other
  role, HUMAN included, draws `role_forbidden`.
- **A staged row does nothing.** It is not a revision, nothing reads it but the review queue and
  its own page, and no machine can move it on. `confirm_staged_intake` admits only a HUMAN actor,
  so a SYSTEM credential that names a change record still cannot register through it, and the
  decision stays bound to a live Authentik session as this ADR requires.
- **A person presses one button.** `/review/staged-intakes/{id}` renders the three decision facts
  first, computed at the surface from the payload's profile and enforcement snapshot with no
  package schema change, then the package content. The button posts to
  `/review/staged-intakes/{id}/confirm` with a CSRF token bound to the staged row and its key,
  which registers the intake as that person through `register_package_intake`, under the staged
  idempotency key, and records the staged row's id on the intake event beside the command.
- **The paste survives** as the escape hatch: `/review/intakes/new` still registers a payload that
  was never staged.

- **Staging refuses what the confirm would refuse.** `stage_package_intake` runs
  `preflight_package_intake`, which runs the registration's own checks (expected version, intake
  purpose, approved status, verification mode, follow-up, reach, acceptance criteria and evidence
  types, originating observation) through the same function `register_package_intake` calls, and
  refuses with the same code. It adds two read-only checks for the conflicts registration finds
  under its locks: an idempotency key an intake event already holds, and a package revision that
  is already registered (or a package registered from another source repository). The identical
  revision is refused too, which is stricter than the confirm, because confirming it would
  register nothing new. The checks were factored out of `register_package_intake` rather than run
  as a rolled-back dry registration: a dry run would need a HUMAN actor that does not exist yet.
- **A person can withdraw a staged row.** The estate can still change between staging and the
  confirm (another registration of the same revision, say), and the confirm re-runs everything.
  `POST /review/staged-intakes/{id}/withdraw` takes a reason and a CSRF token bound to the row and
  that action, and stamps who withdrew it, when and why. A withdrawn row leaves the queue, refuses
  the confirm, and stays withdrawn; withdrawing it again changes nothing. A registered row cannot
  be withdrawn. To bring a withdrawn intake back, stage it again under a new key.

## Amendment 2, 2026-10-30: a standing bump's decomposition decision graduates

**Decided by Devon, 2026-10-29 and 2026-10-30** (dependency bumps need no human approval; Landing
Part 4, decision 1). The decomposition decision of a standing dependency update stops being a human
gate when the conditions below all hold. No credential changes: there is still no HUMAN
credential, and this removes a gate rather than crossing one by machine. A person keeps every
decomposition these conditions don't cover.

`decomposition._bump_policy_approval` approves, in the proposal's own transaction, only when:

- SYSTEM proposed it;
- the revision's approver starts `policy:dependency-update@`, its profile is `dependency-update`,
  `profile_fields.standing` is true, and its declared reach is exactly `source_repository`, so a
  `live_estate` package waits for a person;
- there is exactly one proposed unit, with no dependencies, every criterion is mapped to it and
  none retained (a unit with no criteria completes without the named check), and `max_attempts` is
  at or below the envelope's budget;
- `human_authority_gate` reports no refusals, and only dependency-update known-good patterns
  recognise the stamped envelope;
- `bump_binding_violations` is empty: every command is the one intent-packages' generator emits
  for the package's own dependency, versions and tooling (ADR-0028 amendment 1 asks for this
  binding), and the target repository is the package's;
- the unit's `title` and `outcome` are the text the generator derives from `profile_fields`, since
  both reach the agent's prompt beside `repo.edit`;
- the member record grants `dependency_updates` on that repository.

The recorded approver is the proposing SYSTEM identity, as on the rotation path (ADR-0055
amendment 16). The decision reason names the patterns, the policy version and the revision's
approver, so "policy approved" and "a person approved" stay different records.

Each refusal leaves the proposal on `/review` and records `decomposition.policy_not_applied` with a
fixed reason code and what was read. So does an approval that fails after policy said yes. No route
serves `events` rows for a proposal, so `DecompositionProposalResponse.policy_not_applied` carries
the reason on `GET /api/v1/decomposition-proposals/{id}` and on the revision's proposal list.

**Cost accepted, Devon, 2026-10-30:** the decomposition is built without a local dry run, because
that run installs the bumped dependency, lifecycle scripts included, on the operator machine where
infrastructure credentials live. There is no local proof. A bad envelope fails its attempts, the
bump stays held, and an alert fires. A session-authored decomposition still dry-runs.

The binding also covers `allowed_commands` and `verify_commands`, which goes past mutation commands
alone: `allowed_commands` is the coding agent's whole vocabulary and a known-good pattern bounds
only each command's shape, so a second `uv add` listed there would be a bump nothing named.

What stays unbound, and why. The proposal's `rationale` never reaches the agent: factory-runner reads
no such field. `context_enrichment` does ride the unit, and is not bound, because the Brains supply it
rather than the proposer.

**Residuals, stated in full.** No check here closes the first three. Whether the fourth stays is
Devon's decision and is open.

1. **A forged SYSTEM intake yields a landed bump, not only a `READY` unit.** `approved_by` and
   `standing` on a SYSTEM-registered intake are caller-attested: `POST /api/v1/package-intakes` takes
   them from the body. The merge reads the same two fields (`standing_bump_refusal`, ADR-0025
   amendment 4), so a stolen SYSTEM bearer can forge an intake for any dependency in a granted inert
   repository, drive it through the real run, and have it land, with no person seeing it.
2. **SYSTEM writes a pull request binding with no claim** (`pr_bindings.py::_authorize_write`
   returns before any claim check). The binding names which pull request a unit's evidence and
   merge are about. On the policy path the merge now reads that pull request's branch, trailer and
   files (ADR-0025 amendment 4), which bounds what a binding can point at without making the writer
   any less trusted.
3. **`bump_binding_violations` does not narrow a forged intake.** It binds the envelope's commands
   to `profile_fields`, and a forged intake supplies its own `profile_fields`, so the commands are
   checked against the forgery. The binding protects an honest intake from a wrong envelope.
4. **Self-hosting: open decision.** `orchestrator`, `intent-packages`, `project-standards` and
   `security-standards` are granted inert members, and `~/.claude/bin/activate-checkout.sh` runs
   `uv sync --frozen` when `pyproject.toml` or `uv.lock` moves. So a factory-landed bump into one of
   them is installed on the operator machine at the next launcher pass, with no person. Whether
   those repositories stay on the policy path is Devon's to decide, and nothing here decides it.
