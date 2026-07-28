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
