# WS-P2.18 — The policy artifact

**Phase 2, Wave 4 (SIMPLE) · Spec written 2026-08-01 · Depends on WS-P2.17 (shipped)**

**Goal:** replace four scattered, hard-coded or absent policies with **one versioned artifact**, so
that the human gate fires on what is *novel* rather than on everything, and work that can disturb
Devon runs only when he has said it may.

---

## 1. Why now, and what this is not

R2 is the program goal: *"I do, in fact, plan on getting this to a zero or near zero human gating…
I will want to gate individual novel situations, and pre-authorize work once we know how to do it."*
R7 is why that is not a safety reduction: human review to date **has been ceremony**.

**But R2 moves the trust root, and that is the load-bearing constraint of this entire workstream.**
Containment today is *"a human must click."* As gating approaches zero it becomes *"the detector
decided this was not novel."* The detector **inherits the whole job the gate was doing, is held to
the standard that gate was held to, and must fail toward ASKING.**

WS-P2.17 is the precondition and it is met: a gate cannot be graduated before it has ever worked,
and as of 2026-07-31 it works.

**This workstream is not:** the stall detector (WS-P2.19, R6); rollback of in-process work (R5,
explicitly out of scope and not to be conflated with supersession); the attestation audit (open P1);
or the Dependabot delivery-path gap (§9).

## 2. Scope — one artifact, four policies

Per R3, the authority known-good pattern, the change window and per-class lease are *all policy
keyed on the same thing*, so they are **one versioned artifact** on the `routing-policy.toml`
precedent. R11 added self-deploy: the deploy is not a separate automation project, it is *the thing
the change window governs*.

| # | Policy | Ruling | Today |
|---|---|---|---|
| 1 | **Authority known-good pattern** — which envelopes need a human | 2c, R2, R7 | every envelope needs one |
| 2 | **Change window** — when work may be dispatched | 2d, R13 | no concept; `ORCHESTRATOR_DISPATCH_ENABLED` only |
| 3 | **Lease duration** — how long a claim lives | 3b | one arbitrary 15 minutes |
| 4 | **Self-deploy** — when the orchestrator may update itself, and to what | 7a, R11, R13 | manual, ceremonial |

## 3. THE DECISION THAT COMES FIRST — the key is *reach*, not `change_class`

**Do not start building until this is settled. Two of the four policies depend on it, and so does a
field the human gate already needs.**

R13, from Devon: *"I don't think the downtime for the actual orchestrator is an issue at all… Compare
that to the work the orchestrator is having done, which could be anything related to local infra, to
software updates for repos."*

Sorting the work by what it actually touches:

| Work | Touches | Needs a window |
|---|---|---|
| dependency-update / software-delivery / maintenance-remediation | a repo's `main` | **No** — a landed PR is inert until something deploys it |
| orchestrator self-update | a dev instance, ~30s | **Barely** |
| infrastructure-change | the Coolify/VPS estate | **Yes** |
| non-software-operational | credentials, external consoles, real-world state | **Yes** |
| local-heavy execution | **Devon's machine** | **Yes — the client-call case** |

**`change_class` is the wrong key.** A dependency bump and a credential rotation can share a change
class and have nothing in common in blast radius. What distinguishes the bottom three rows is not the
*kind* of change but *what the work reaches*.

**And that dimension already exists in the requirements under another name.** At Layer 1 Devon named
the three facts he needs in order to decide: what it does, **what it affects**, can we back out.
*What it affects* and the change-window key are **the same concept used twice** — it tells the human
what they are approving, and it tells the scheduler when the work may run. **Decide it once here**,
rather than discovering later that the gate's display field and the scheduler's key were always the
same thing.

**Constraints on that decision:**
- **Declared, not inferred** (R8). Prefer a value the package author states and intake validates,
  over one the orchestrator derives — inference trades a loud failure for a quiet one.
- It must be carried into the **enforcement snapshot**, the way `profile_fields` already is
  (`package_sources.py:545`), so the orchestrator holds it verbatim from intake.
- It is **displayed at the human gate**, so it must be legible to Devon, not just to the scheduler.
- Verify the proposed vocabulary against all five existing change classes and the real delivery
  profiles before adopting it. A vocabulary that cannot express the current population is wrong.

## 4. Policy 1 — the authority known-good pattern

The gate that fires today on every envelope should fire only on a **novel** one.

- Build the pattern on `AuthorityEnvelope.normalized()` and `KNOWN_FIELDS`. Read that invariant in
  CLAUDE.md first: fields outside `KNOWN_FIELDS` contribute only their *names* to the fingerprint, so
  **a field carrying real authority must be a known field** — and **adding to `KNOWN_FIELDS` rewrites
  every authority fingerprint**, a cost proportional to the live ledger.
- **Fail toward asking.** Unknown pattern → ask. Any field outside the matched pattern → ask. Any
  escalation in reach, capability level, or budget → ask. The detector may only ever *suppress* a
  gate it can positively justify.
- The envelope is **write-once** and `is_expansion()` was deleted in WS-P2.15 because it had no
  callers; `tests/architecture/test_authority_write_once.py` is what makes that safe. **If this
  workstream introduces any path that raises a unit's budget or capabilities, it must ship a
  fail-closed expansion check with it.** Do not "fix" that test.

## 5. Policy 2 — the change window

- Keyed on **reach** (§3), not `change_class`.
- Enforced at **dispatch admission**, alongside the existing `work_unit_not_ready` /
  authority-approval checks.
- **R4 is absolute: retain a single hard off-switch that outranks all policy.**
  `ORCHESTRATOR_DISPATCH_ENABLED=false` must remain the one-line, fail-closed answer to *"is the
  factory stopped?"* Policy may only ever narrow what that switch permits, never widen it. Prove it:
  with the switch off and a wide-open window, nothing dispatches.
- Note the operational hazard already documented: **closing a bounded dispatch window restarts the
  orchestrator**, and a restart while a run is live strands the unit — the runner calls back at the
  *end* of its run, and `fail-run` fails the same way. If the window becomes config the process reads
  at startup, this workstream inherits that hazard. **Prefer a design that does not require a restart
  to change the window.**

## 6. Policy 3 — lease duration

- Per-key duration replacing the single arbitrary 15 minutes.
- **R6 is a warning, not a feature request: lease lapse was never stall control.** It was merely the
  only thing incidentally bounding a hung worker. This workstream must not present a tuned lease as
  though it bounded anything — the hole is real and belongs to WS-P2.19.
- Any "may this unit run again" rule belongs in the shared `_readiness_eligibility_error`
  (`services/claims.py`), **not** in `claim_unit` — `reclaim_expired_claim` bypasses `claim_unit`
  entirely and grants a fresh attempt.

## 7. Policy 4 — self-deploy

- R13 puts self-deploy **near the bottom** of the risk order, superseding the earlier open question
  about keying it on "the highest-risk class in the delta."
- Devon's control is satisfied by **setting the window once**, keyed by reach and lane — never by a
  per-deploy decision.
- Verified and load-bearing: **merging a PR deploys nothing.** Coolify pulls a prebuilt GHCR tag only
  when a deployment is triggered; `release-image.yml` is `workflow_dispatch` and only builds. So the
  automation being designed here is the *trigger*, not the build.
- The image-build paved road already exists (`security-standards.pin.toml`, `release-image.yml`,
  the fail-closed bundle-digest gate). Self-deploy composes those; it does not replace them.

## 8. The graduation ledger — decide attribution BEFORE building it

R2's ladder reasons over evidence of what the gate caught. **That evidence is contaminated and it is
already known how.**

Construction-era gates driven by an agent are attributed to **`devon`**, because `/review` reads the
actor from the forward-auth header. Acceptable at the time — but *"Devon approved forty of these and
rejected none"* is worthless as graduation evidence if an agent performed most of them.

**Already decided: graduate on OUTCOMES, not approvals.** This workstream must honour that, and must
either **mark** construction-era approvals or **discount** them. The notes are explicit that this is
to be decided *before* the ledger is built — which is now.

## 9. Inputs this workstream must not silently assume

**Dependabot is named as an input source and has no delivery path.** Found 2026-08-01: orchestrator's
`dependabot.yml` declared `pip` while the repo locks with `uv.lock`, so every Python PR it opened was
unmergeable from birth (#46/#61/#74 — three bumps of one pin, none landed). Fixed in #113; four
dependency PRs landed the same day. Separately, **new Dependabot PRs generate no notification** to
Devon at all — the only orchestrator PR notification in his inbox was one an agent had commented on.

That is not this workstream's to fix, and it is **not** a notification-settings problem: routing these
to a human inbox still makes Devon the integration point, which is the thing the factory exists to
remove. **The relevant constraint here is narrower: do not design the change window on the assumption
that machine-originated work is already arriving.** For most of this estate's history, it was not.

## 10. What must be proven, not asserted

This programme's recurring defect is a guard that cannot fail. Every deliverable below carries its
own discrimination proof.

- [ ] The reach vocabulary **expresses the current population** — checked against all five change
      classes and the real delivery profiles, not against examples.
- [ ] The known-good detector **fires on a novel envelope** and **suppresses on a repeat** — both
      directions, on real envelopes from the ledger.
- [ ] The detector **asks** when a field is outside the pattern, when reach escalates, and when it
      cannot classify. Prove each by construction.
- [ ] The hard off-switch **outranks policy**: switch off + open window → nothing dispatches.
- [ ] The window **refuses** out-of-window work and **admits** in-window work, keyed on reach.
- [ ] Changing the window **does not require a restart**, or the strand hazard in §5 is accepted
      explicitly and in writing.
- [ ] `make check` green with the **collected count read from the job log**, locally and in CI.
      Baseline **1883**. Exit 0 proves nothing on its own.

## 11. Standing build rules for this repo

- **Every task lands its mechanism AND at least one production caller in the same commit.**
  `test_unreachable_guards` correctly reds a module with no production caller, so "define now, wire
  later" cannot be a task boundary here. This has bitten three times.
- **A task boundary is only valid if the tree is green AND behaviour is coherent at it.**
- Adding a route or a module trips a family of whole-repo architecture guards — the ws32/ws33 word
  guards (bare `dispatch`, `deploy`, `merges`, **including in docstrings**, and compounds tokenize),
  two exact route inventories, `test_unreachable_guards`, the egress scan, the cross-boundary
  vocabulary scan, and the idempotency matrix. Only a full `make check` runs them.
- A persistence assertion must re-read through a **different session**; `expire_all()` does not
  discriminate.
- Do not run two pytest suites against the test database concurrently.
- **Ask production what it is running** before reasoning about what it can do. Merged is not deployed.

## 12. Suggested increments

Sized on WS-P2.17's evidence that a seven-increment workstream with a fresh session per increment
worked well.

| # | Increment | Gate |
|---|---|---|
| **1** | §3 — the reach dimension: decide it, validate against the population, land it as an ADR + the declared field + intake validation + enforcement-snapshot carry | vocabulary expresses all five classes |
| **2** | The artifact: versioned schema, loader, validation, single source | loads; rejects malformed; versioned |
| **3** | Policy 1 — known-good pattern, wired to the gate | fires/suppresses both directions on real envelopes |
| **4** | Policy 2 — change window at dispatch admission + R4 off-switch precedence | off-switch outranks; window refuses and admits |
| **5** | Policies 3 & 4 — lease and self-deploy | lease per key; self-deploy triggers within window |
| **6** | The graduation ledger, honouring §8 | construction-era approvals marked or discounted |

**Increment 1 is the one that must not be rushed.** Everything downstream keys on it, and getting it
wrong means rebuilding two policies and a display field.
