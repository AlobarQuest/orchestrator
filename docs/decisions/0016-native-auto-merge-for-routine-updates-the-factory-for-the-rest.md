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
| `inert` | orchestrator, intent-packages, infraops-mcp-server, project-standards | auto-merge — an outage is structurally impossible; merging changes nothing running. **orchestrator subsequently excluded, see the implementation note** |
| `redeploys` | change-manager, brain | **a separate decision**: auto-merge here is an unattended production deploy at whatever hour CI goes green. Held pending the windowing mechanism (enable auto-merge at the window open, disable at its close, using `live_estate`'s declared 02:00–06:00) |
| `unknown` (`no_app_record`) | factory-runner, security-standards | **ASSESSED 2026-08-06 and included.** `no_app_record` is the correct answer, not a gap: neither is an application. Both are **tool homes** — a merge deploys nothing and reaches consumers only when a consumer deliberately advances a pin. Recorded per-repo in `security-standards/governance-map.toml` (`class = "tool-home"`; factory-runner was absent and has been registered) and as a class-level rule in Infra Brain (id 1502) |

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

An auto-merge workflow must live in the **target** repositories.

**CORRECTED 2026-08-06 during implementation — the original note was right about the constraint and
wrong about its consequence.** It read: *"Placing one in `orchestrator` would trip that repo's own
no-merge architecture guards … The guards are not in the way here; they are pointing at the right
home."* That was written assuming `orchestrator` was not itself a target. **It is** — App Brain
records it `inert`, so it is one of the four repos this decision covers. The guard therefore
**excludes a target** rather than redirecting to a different one, and the cost is real: its seven
open Dependabot pull requests stay manual.

The mechanics: `tests/architecture/test_no_automatic_merge.py` scans every file in
`.github/workflows/` outside four named exceptions and fails on the string `gh pr merge`. Since
`Quality` is now a required check on that repo, a pull request adding the workflow cannot merge
while the guard is red.

**Decision (Devon, 2026-08-06): leave `orchestrator` out for now.** Not because a fifth named
exception would be wrong — the workflow is repo hygiene rather than the factory merging its own
work, and the guard cannot tell the difference — but because amending a foundational constraint as
a side effect of a hygiene improvement is how this estate has gone wrong before. Revisit when the
other five have run clean and handling orchestrator's queue by hand actually grates: that is a
decision made on evidence rather than anticipation.

**Explicitly rejected: reaching the same outcome through the GraphQL
`enablePullRequestAutoMerge` mutation**, which contains no forbidden string and would pass the
guard untouched. Enabling auto-merge *is* causing a merge; passing a check by renaming the verb is
the validated-as-a-name-ignored-as-a-permission failure this estate keeps finding. If the exception
is ever wanted, take it openly.

Stage 3 therefore covers **five** repos: intent-packages, infraops-mcp-server, project-standards,
factory-runner and security-standards.
