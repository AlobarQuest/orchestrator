# ADR-0016 — Native auto-merge takes routine dependency updates; the factory takes the ones that fail

- **Status:** Accepted
- **Date:** 2026-08-06
- **Decided by:** Devon
- **Relates to:** the Phase-3 input-layer plan (WS-P3.1), ADR-0009 (`reach`), ADR-0015 (a factory
  target is declared)

## Context

Thirty-four Dependabot pull requests stand open across the estate. The programme's intended answer
was WS-P3.1 — an automated lane turning each signal into a proposed package, through the factory,
to a merged PR. Costed honestly, "all onboarded repos, minimal human interaction" is six to ten
workstreams: a standing rule, a proposed-package record with its correlation contract, producer
credential binding, supersession, a hostile-content ruling, a merge-guard reversal, and — the piece
with no existing mechanism at all — **graduating the intake and decomposition-approval gates**,
both `_require_human` by construction, one of them GUI-only.

Against that: **for the routine cases GitHub already does this natively.** Dependabot auto-merge on
green CI, gated by required status checks, is configuration rather than code.

So the question is not whether to automate dependency updates. It is what the factory is *for* in
this lane. A patch bump with green CI does not need an evidence pack to be safe. What the factory
can do that native auto-merge cannot is **fix a Dependabot PR that fails** — a stale lockfile, an
ecosystem mismatch, an update needing remediation. Native auto-merge can only merge or decline.

## Decision

**Invert the expected assignment. Native Dependabot auto-merge handles routine updates. The
factory handles the ones that fail, as `maintenance-remediation` — a profile already proven end to
end (WS-P2.33).**

**Auto-merge scope is decided by what a landed PR actually does**, read from App Brain's
`default-branch-landing` rather than inferred (measured 2026-08-06):

| landing | repos | disposition |
|---|---|---|
| `inert` | orchestrator, intent-packages, infraops-mcp-server, project-standards | auto-merge — an outage is structurally impossible; merging changes nothing running |
| `redeploys` | change-manager, brain | **a separate decision**: auto-merge here is an unattended production deploy at whatever hour CI goes green |
| `unknown` (`no_app_record`) | factory-runner, security-standards | no auto-merge until assessed — fail closed |

This is Devon's own stated risk model — *what is changing, what it affects, and when* —
operationalised against the data that already answers it.

## Consequences

**Accepted, deliberately:** routine dependency bumps land with **no governance record** — no
evidence pack, no traceability chain, no authority envelope. That is the cost, and it is
proportionate: the governance apparatus exists to make *consequential* change accountable, and a
green patch bump into a repo that deploys nothing is not that.

**Deferred, not cancelled:** the proposed-package record, the correlation contract, and the
intake/decomposition graduation mechanism. WS-P3.1 remains the right eventual shape for signals
that genuinely need it.

**Gained:** several months of evidence about *which* Dependabot signals actually need the factory,
before committing to automate the whole feed on the assumption that they all do. Today's honest
expectation is that most do not.

**Sequencing note:** this makes the eventual Phase-3 lane smaller and better motivated. It does not
make the Phase-3 plan's defects go away — the traceability correlation contract, the ingress
protocol, and the mis-sequenced hostile-content ruling all still need addressing before any adapter
is built.

## Revisit when

- The remediation path is exercised often enough that doing it on demand becomes the bottleneck —
  that is the signal an automated ingress lane has earned its cost.
- Or a `redeploys` repo's dependency load makes unattended deploys attractive enough to want the
  factory's outage reasoning in front of them.

## Note for whoever implements it

An auto-merge workflow must live in the **target** repositories. Placing one in `orchestrator`
would trip that repo's own no-merge architecture guards, which forbid `gh pr merge` and its
siblings across six test files — correctly, since nothing in the orchestrator may merge a pull
request. The guards are not in the way here; they are pointing at the right home.
