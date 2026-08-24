# ADR-0030 — Activation on the operator machine is a deployment

- **Status:** Accepted
- **Date:** 2026-08-21
- **Decided by:** Devon decided **Q1** ("I selected A for Q1", 2026-08-21) and **Q2 scope**
  ("six", 2026-08-24). **Q3 and Q4 were decided by HQ**, which told him in writing *"Q2, Q3, Q4 —
  answered, no decision needed from you."* The header previously read "Devon (Q1–Q4)" and was
  corrected 2026-08-24 when he said the scope rule did not match his memory — it did not, because
  he had never been asked. §2's original enrolment rule was HQ's, generalised further by the build
  session; it is superseded there.
- **Relates to:** ADR-0009 (`reach`, which already declares `operator_machine`), ADR-0022 (the
  watcher owns outcomes, and the observation discipline this ADR inherits), Phase-3 exit
  criteria 1 and 2
- **Spec:** `~/docs/software-delivery-system/2026-08-21-the-second-deployment-model-spec.md`

## Context

The estate models one deployment model: a change becomes live when an image with a digest is built,
pushed, and swapped in by the hosting platform. `ReleaseArtifactBinding`, `deployment_observations`
and the traceability chain's `commit` / `artifact` / `deployment` hops all describe that shape, and
they describe nothing else.

A second model has been running the whole time. A change becomes live when the code is pulled into
a working copy on the operator machine and the next process start picks it up:

```
~/.claude.json → bash ~/Projects/infraops-mcp-server/start.sh
start.sh       → exec node "$SCRIPT_DIR/dist/index.js"
$SCRIPT_DIR    =  ~/Projects/infraops-mcp-server        ← the main tree
```

So the activation path is merge → `git pull` → next process start, and nothing observes any step of
it. App Brain reports `inert` for these repositories and is correct — it answers *"does landing
redeploy a hosted app?"*, and no hosted app exists. Reading `inert` as *"nothing becomes live"* is
what sent two earlier analyses of exit criterion 2 wrong.

The cost is measured. Re-measured against production 2026-08-21 through `status-ledger` and
`/release-artifacts` (57 units: 38 completed, 15 cancelled, 2 failed, 1 submitted, 1 ready), there
are **3 release artifact bindings in the entire system**, and all three belong to units whose
subject *was* recording or drilling a deploy by hand:

| unit key | title |
|---|---|
| `drill-4-split-brain` | Drill 4 — deploy split brain |
| `wsp28-production-deploy-verification` | Verify and record the WS-P2.8 production deploy |
| `phase5-production-closeout` | Verify Phase 5 production closeout |

**No real lane work has ever produced a full chain.** For the machine-local repositories it cannot,
for a reason that has nothing to do with the work being lesser.

## Decision

### 1. A machine-local activation is recorded, and the first producer observes the disk

A watcher sweeps the enrolled working copies and reports each repository's `HEAD`. Self-reporting
consumers — a process announcing the revision it is executing — are a later increment, taken only if
the weak form proves insufficient.

**What the observation asserts is bounded, and the bound is the load-bearing part: it attests what
the next start, run, or invocation will execute.** It never attests the currency of a running
process. For a consumer that only executes at a start, those are the same fact. For a process
serving continuously between starts they are not, which is what §2 excludes.

### 2. Scope: the SDS targets

**SUPERSEDED AND CORRECTED 2026-08-24 by Devon's ruling — the rule stated in the rest of this
section was never ratified and admitted repositories that are not part of the SDS.** He decided
Q1 only; HQ decided Q2 and said so in writing; the build session then generalised HQ's list into
the fresh-start rule below, which admitted nine — including `email-capture`, `FacelessTT` and
`~/.claude`.

**The enrolled set is the SDS targets, by the estate's OWN definition, which this ADR does not
reinvent:** the repository self-identifies in `PROJECT.md` frontmatter (ADR-0015) and the
conformance kit judges it ready (`ADMISSION_CHECKS`). Measured 2026-08-24, that is six —
`orchestrator`, `intent-packages`, `security-standards`, `infraops-mcp-server`, `change-manager`,
`brain`. `project-standards` declares `factory_target: false` and is correctly absent.
`orchestrator` is absent from the production dispatch allowlist only because it IS the system and
cannot be dispatched to.

**An intermediate draft added a second condition — "something on this machine executes from the
working copy" — and dropped `change-manager` and `brain` on it. That condition was invented here,
is not the estate's, and is recorded because rejecting it is the useful part.** It was weaker than
it looked. What this sweep files is the state of a working copy relative to its upstream; the
"what the next start will execute" reading in §1 is an interpretation laid over that, not what the
row says (`summary_of` mentions no process). And the interpretation's own hazard survives for
hosted-application repositories anyway: a build session's first act is `git worktree add … main`,
which branches from the LOCAL default branch, so a stale checkout starts a session on stale code —
with nothing else watching, since no launcher pulls them.

**Consequent amendment to §1:** the observation attests **the state of a working copy on this
machine**. For a repository with a machine-local consumer that also predicts what the next start
runs; for one without, it does not, and the row neither says nor needs to say so. §1's original
bound — "it attests what the next start, run, or invocation will execute" — was narrower than the
code and is superseded by this sentence. What §1 still rules out is unchanged and is the point:
it never attests the currency of a RUNNING process.

**The KeepAlive exclusion below is now moot rather than wrong** — no daemon repository is an SDS
target, so the question does not arise. Its reasoning is kept because it is the argument for why a
disk sweep cannot speak for a continuously-serving process.

#### The superseded rule, kept because the reasoning about daemons is still used

~~Scope is the rule, not a list: a consumer must get a fresh start in the ordinary course~~

A repository is enrolled when its consumers begin a fresh process in the ordinary course of
operation, without a human deciding to restart them. Measured 2026-08-21 from
`~/Library/LaunchAgents` and `~/.claude.json`:

| consumer kind | fresh start | repositories |
|---|---|---|
| periodic LaunchAgent | next fire | `orchestrator` (×5), `vps-backup` (×3), `infraops-mcp-server` (×2), `project-standards` (×1) |
| per-invocation CLI | next call | `intent-packages`, `project-standards`, `security-standards` |
| MCP server (stdio, client-launched) | next Claude session | `infraops-mcp-server` |
| **KeepAlive daemon — excluded** | — | `veritok` (×2), `VideoCreator`, `listing-prep`, `residential-pricing-model`, `Chatterbox-TTS-Server`, `HostServiceManager` |

**The exclusion is not "nothing restarts them."** `render-services-start`/`-stop` cycles
`Chatterbox-TTS-Server` and `VideoCreator` nightly. It is that a sweep attests the disk, and for a
process serving continuously between starts, the gap between the disk and the loaded code is exactly
what goes unobserved. Enrolling one would have the watcher assert something it cannot see.

**Enrol everything the rule admits.** Measured 2026-08-21, that is nine working copies, all of
which track `origin/main`:

`orchestrator`, `vps-backup`, `infraops-mcp-server`, `project-standards`, `intent-packages`,
`security-standards`, `email-capture`, `FacelessTT`, and `~/.claude` itself.

An earlier scoping keyed on **factory membership** and produced six. That axis is the wrong one and
barely overlaps this population: `change-manager` and `brain` are factory repositories that deploy
to hosted applications, so they were never in this set at all, while `email-capture`, `FacelessTT`
and `~/.claude` are in it and are not factory repositories. The rule keys on *has a machine-local
consumer*; factory membership answers a different question.

The enrolled set remains a parameter of the rule, not the rule. Adding a consumer that begins a
fresh process in the ordinary course enrols its repository automatically.

**OPEN, 2026-08-24 — this rule was never ratified, and there is an established alternative it did
not consider.** The estate's existing pattern for "is this repository in scope" has two aspects:
the repository **self-identifies** in `PROJECT.md` frontmatter (ADR-0015, whose reason was that a
declaration should be *"repo-local and self-describing, rather than a list inside the kit that the
affected repository cannot see"*), and the SDS **determines readiness** through the conformance
kit's admission checks. That reasoning transfers to this sweep — `FacelessTT` cannot see that it is
being swept — and it was bypassed rather than rejected, because nobody asked. Two things weigh the
other way and belong in the decision: ADR-0015's mechanism has **never been built**, so this sweep
would have had to invent it; and this sweep is **read-only**, granting no authority over the
repositories it observes, where factory membership grants write. Self-identification matters most
when the answer confers power. Devon to rule.

### 3. Reuse `ReleaseArtifactBinding` with an explicit kind discriminator

A second table would mean teaching every traceability hop to read it — a second copy of a rule,
which is where this estate's drift consistently lives. The chain query is where criterion 2 is
measured, so reuse lights those hops with no query change.

The discriminator is the condition, and it is not cosmetic: a table shaped for digests silently
holding commits would make the two models indistinguishable in the data, which is the collapse this
ADR exists to prevent, one layer down.

**Measured: that collapse is already structurally impossible, and the finding sharpens the design
rather than relaxing it.** `_validate_digests` requires `artifact_digest` to match
`^sha256:[0-9a-f]{64}$`, and a 40-character git commit sha cannot satisfy it. The table **refuses**
a commit; it does not silently hold one. The error message states the real property — *"provide
registry digest, not a mutable tag"*.

So a machine-local binding must supply a genuine content digest, and one exists:
`git archive HEAD | shasum -a 256`. Verified stable across repeated runs on 2026-08-21
(`orchestrator` → `sha256:c837eefb…` twice). This is better than recording the commit: it is an
actual digest over actual content, it keeps the validator's invariant literally true with no
loosening, and two checkouts at the same commit produce the same value.

**Do not relax `_validate_digests` to admit a commit.** The validator is the mechanism that keeps
the two models distinguishable in the data.

### 4. Two lanes, and they are not interchangeable

`record_release_artifact` requires the unit to be `COMPLETED` (`work_unit_not_completed`) and
validates `package_revision_hash` against the revision's `content_hash`. A routine pull — a
Dependabot auto-merge, a hand `git pull` — has no unit, so **the staleness sweep structurally cannot
write bindings.**

- **Unit-caused activation** → `ReleaseArtifactBinding` + kind. This is the lane criterion 2 reads.
- **Routine staleness sweep** → a generic observation under the OBSERVER credential
  (`orchestrator-drift-reporter`, the standing identity for every observe-and-report producer per
  ADR-0017).

**The sweep must not route through `deployment_observations`**, whose summaries are exact-key-set
bounded to four shapes (`auth_summary`, `route_summary`, `dispatch_summary`, `status_summary`); a
machine fact fits none of them and would be refused as `deployment_observation_invalid`.

Stating this split is required. Without it, *"reuse lights the hops for free"* reads as covering the
sweep, which it cannot.

### 5. The observation reference keys on the moving fact

`record_observation` refuses a repeat `(source_system, source_reference)` carrying different facts —
the ADR-0022 trap. The watcher's subject is a repository's `HEAD`, which moves constantly, so a
fixed reference would wedge the producer permanently on its second sweep.

Key the reference on `(repository, head_sha)`, or carry a fact digest as change-manager's
`observation_key` does, so a moved `HEAD` appends and an unchanged sweep replays. Note also that
`record_observation` **returns** its `DomainError`s rather than raising them.

### 6. This ADR precedes the build

It defines a model, not an increment. ADR-0022 got one for a narrower decision.

## Consequences

- `reach: operator_machine` stops being a member the traceability chain cannot represent.
- Phase-3 criterion 2 becomes satisfiable for the five machine-local factory repositories.
- Criterion 1's second half — zero of 57 units have ever populated the `observations` hop — gains
  its first real producer.
- *"Is the machine current?"* becomes answerable. The estate has twice shipped a change and left the
  machine stale; both times a human noticed rather than a control.
- One new scheduled producer on the operator machine, subject to the launcher rules already
  recorded: read each Keychain identity directly rather than one ambient `BWS_ACCESS_TOKEN`, and do
  not let the exit-code fold swallow a missing binary.

## Residuals and non-goals

- **The weak form is deliberate.** The watcher answers *in place*, never *ran*. If that proves
  insufficient, self-reporting consumers are the named next increment — not a redesign.
- **`git archive` output can vary across git versions.** It is stable on the machine where the
  watcher runs, which is where it is read; a digest compared across machines is not in scope.
- **Not a change to the first model.** Hosted deployment works and must not be disturbed.
- **Not host monitoring.** The question is what code is live and what put it there.
- **Not automatic pulling.** Making the machine self-update is a separate decision with its own
  authority argument. This ADR stops at recording.

## The defect this exists to catch was already on the board

Checking every candidate for an upstream on 2026-08-21 — expecting `~/.claude` to fail the test and
give a clean exclusion — found instead that all nine track `origin/main`, seven were current, and
**two were stale at that moment**:

| repository | behind | missing commit | consumers running from it |
|---|---|---|---|
| `security-standards` | 1 | `ac6369d` bump ruff 0.16.2 → 0.16.3 | `com.devon.security-scan` (weekly), the `bws-scan-gate.sh` Stop hook |
| `project-standards` | 1 | `cd81ee7` bump ruff 0.16.2 → 0.16.3 | `com.devon.portfolio-scan` (03:00), the `portfolio` CLI |

A stale scanner and a stale backlog tool had been running for roughly two days with nothing
reporting it, and both were in use that same day. This is not a hypothetical the model is built
against — it is the model's first finding, discovered while scoping it, in two repositories a
narrower scope would have excluded. It is also the argument for the rule over a list: the list was
drawn on the wrong axis and would have missed both.
