# ADR-0055 — Credential rotation runs in a pull-based executor through one contract

- **Status:** Accepted
- **Date:** 2026-10-08
- **Decided by:** Devon, decisions recorded 2026-10-08 in
  `docs/superpowers/specs/2026-10-08-credential-rotation-requirements.md`. Accepted 2026-10-08 ("Yes, I accept ADR-0055").
- **Supersedes:** the parts of ADR-0054 named in "What this changes in ADR-0054".
- **Relates to:** ADR-0054 (the SDS owns credential rotation), ADR-0052 (superseding an unworked
  decomposition), ADR-0039 (out-of-process producers), ADR-0011 (known-good patterns)
- **Review:** revised twice after independent adversarial reviews on 2026-10-08: fourteen defects in
  the first draft, then two blocking defects and six gaps in the revision. "Review findings and how
  they're resolved" maps each one.

## Context

ADR-0054 made credential rotation SDS work. Working the first rotation (`openrouter-generic`,
parked 2026-10-07) showed each credential class heading toward its own procedure, and showed the
operator machine's Keychain as a dead end for a system that might one day serve customers. Devon's
goal for this design:

> "As automatic of a system, as possible, to rotate exposed creds, without creating a new, larger,
> security hole."

The facts this ADR rests on are in `docs/superpowers/specs/2026-10-08-credential-rotation-research.md`.
The requirements (R1–R18), the options considered, and Devon's decisions are in
`docs/superpowers/specs/2026-10-08-credential-rotation-requirements.md`. This ADR records the design
they produce.

### Facts measured for this ADR (2026-10-08)

- **One hash per machine identity.** `identity/auth.py::_validate_m2m_credentials` refuses two key
  ids for one `agent_id`, and each key id holds one `token_hash`. It runs on every request.
- **The boot parser refuses any extra field.** `main.py::_m2m_credentials` requires each entry's
  keys to equal exactly `{"agent_id", "token_hash"}`, or the orchestrator doesn't start.
- **Change windows compose by intersection** (`factory_policy.py::window_refusal`), and return no
  objection for an undeclared reach, relying on admission to refuse that case.
- **Windows are checked only at dispatch admission** (`reach_admission.py::change_window_refusal`),
  and `services/lifecycle/claims.py` has no window check. Rotation units are claimed, never
  dispatched, so nothing windows them today.
- **`claim_unit` checks role, state, attempts and budget only.** Any WORKER can claim any ready unit;
  the `operational_action` guard applies only at dispatch.
- **Evidence isn't secret-scanned.** The secret detector (`kernel/secret_metadata.py`) is used by
  observations and release artifacts, not by `services/verifier/evidence.py`. Evidence rows are
  append-only, so a value written there can't be removed.
- **Reclaiming a lapsed claim is SYSTEM-only** (`POST /work-units/{id}/reclaim-expired-claim`), runs
  nowhere on its own, and grants a claim without calling `claim_unit`.
- **Only a WORKER holding a live claim can file evidence**, and a human's only edge out of `READY`
  is to `CANCELLED`. A human acts on a unit's progress only by resolving a dependency
  (`resolve_dependency_command`, HUMAN or SYSTEM).
- **Nothing resolves a `work_unit` dependency when its predecessor completes**; only a HUMAN or
  SYSTEM call does.
- **Package schemas are closed** (intent-packages rejects an unknown key), and the orchestrator
  stores a package snapshot as the caller attests it, without recomputing it from git.
- **The keeper projects hold the estate's largest secrets.** `Ops / Platform` holds the production
  Coolify API token, GitHub and Anthropic keys and the Namecheap keys
  (`infraops-mcp-server/.bws-secrets.toml`) beside several rotation keepers. `SDS Operator` holds the
  SYSTEM and VERIFIER bearers.
- **No registry repository requires a review to merge** (`reviews: null` on all four, by design).
- **Keychain items are readable by any process running as Devon's user** (`scripts/sds-token.sh`
  trusts `/usr/bin/security` with `-T`).
- **OrbStack (2.2.3) machines are reachable from the Mac.** With default settings, Devon's user gets
  root in a machine with no password. An isolated machine blocks the machine-to-Mac direction but
  keeps "SSH and `orb` access" from the Mac (https://docs.orbstack.dev/machines/isolated). Every
  machine's disk is one file, `data.img.raw`, mode 644, excluded from Time Machine.

## Decision

### 1. A rotation executor that pulls approved work

A deterministic program, the **rotation executor**, does every step that moves a credential value.
It has no LLM. It lives in this repository as `src/rotation_worker/` (ADR-0054's settled location).

- **It pulls; nothing pushes to it.** It claims approved rotation units from the orchestrator with
  its own WORKER M2M identity, as HQ claims operational units today. It listens on no port.
- **The orchestrator is the control plane; the executor is the only place values exist.** The
  orchestrator holds the change record, the package, the approvals, the units and the evidence. The
  executor holds the admin credentials and the values in flight. No value flows back.
- **Claims are confined both ways, at the server.** A **rotation unit** is one whose
  `required_capability` is `operational_action` and whose package revision has profile
  `non-software-operational` with `standing` and `credential_id` set. Only the executor's
  `agent_id` may hold a claim on a rotation unit, and the executor may hold a claim on nothing else.
  Both `claim_unit` and the reclaim path (`_acquire_reclaimed_claim`) enforce it; renewal is already
  bound to the claim's owner. Each check ships with a mutation test.
- **What it acts on is fixed by the human approval, not by the unit.** The package revision carries
  the credential's destinations in full: provider, endpoints, and every consumer's kind and
  destination, as a new `non-software-operational` profile field. The rotation proposer writes it
  from the registry when it revises the package. When Devon reviews a revision, `/review` shows the
  destination list's difference from the last approved revision, with new destinations marked, so a
  hostile registry edit merged earlier without review is in front of him at approval. The executor
  then:
  - checks the revision against intent-packages' git: its `source_commit` is an ancestor of
    `main`, and its `content_hash` recomputes;
  - reads the registry at a revision that is an ancestor of the registry repository's `main`;
  - refuses unless the registry's destinations equal the approved list exactly.
- **It re-probes rather than trusts.** Evidence tells the executor where a rotation stopped. Before
  it acts on a step evidence marks done, it re-probes that step's result wherever a probe exists. It
  never takes a provider key id from evidence: the id of a key to revoke is derived at revoke time
  from the quarantined value itself, through the provider (for OpenRouter, amendment 1 says how).
- **It checks the hand-over at act time.** Before any step, it confirms the credential is still
  `rotated_by_sds = true` in the registry (R6).

### 2. Where it runs

**A dedicated, isolated OrbStack VM on the operator machine** (`orb create --isolated` with
`--isolate-network`), built so it moves unchanged to a VPS if the SDS becomes a SaaS offering. In
that case each customer runs the executor beside their own credentials, as self-hosted CI runners
work.

- It runs as a service in the VM, started by the VM's init system. The root token is in a file
  readable only by the service's user. Its start path is proven with only `PATH` and `HOME` set
  (R16).
- The VM holds nothing that can't be rebuilt (decision 9).

**Accepted residual risk.** Any process running as Devon's user, including an agent session, can
reach the VM and read what it holds. Devon accepted this on 2026-10-08: the Mac already holds the
keys to every other host, so moving the executor elsewhere wouldn't stop a determined agent on the
Mac either. What the placement does prevent is an agent reaching the values *by accident*: no value
is in the Mac's Keychain, environment, shell or files, and none passes through a tool.

### 3. One contract, with an order each adapter declares

Every rotation is made of the same kinds of step: **mint**, **stage**, **verify-new**, **store**,
**deploy**, **verify-consumers**, **retire-old** (revoke, or end an overlap) and **confirm-dead**.
Each adapter declares its order and, per step, `machine` or `human`. A self-issued bearer, for
example, deploys before it can verify, because the orchestrator must know the new hash first.

The contract enforces these invariants for every adapter:

- **Retire-old is reachable only after every registered consumer has passed verify-consumers**
  (R10), and it is the last step that changes what any consumer accepts.
- **New is never old.** Every step after mint refuses if the new value's fingerprint equals the
  old one's. A missed human stage can't turn into revoking the live value.
- **The old value is quarantined in a named BWS secret before the keeper changes**, and the
  executor refuses if the keeper already holds a new value with no quarantine ("never guess", as the
  legacy executor does).
- **Every probe is shown to refuse a known-bad value in the same run** before its answer counts,
  and both results are evidence (R14). Live is exactly 200; dead is exactly 401 or the provider's
  documented equivalent; anything else stops the rotation. (For BWS, amendment 1 replaces the
  dead answer and its control.)
- **Human acts are dependencies, not units.** A rotation's decomposition is fixed per adapter and
  holds only machine units. Each human act (a console mint, a Coolify write, an attestation) is a
  dependency on the unit that needs it, which Devon resolves at `/review` with the fingerprints the
  act produced. The next machine unit becomes ready when its dependencies resolve, so no claim ever
  waits on a human. A human-minted value is staged in a per-credential **staging secret**, never in
  the keeper and never on the Mac.
- **A unit's completion resolves the `work_unit` dependencies that name it**, in the same
  transaction. This is a general orchestrator change (increment 3); without it every hand-off
  between units is another human act.
- **Each step that writes to a hosted consumer is its own unit**, so its first claim is windowed at
  the server (decision 8).
- **After confirm-dead, the quarantine and staging secrets are deleted**, and the deletion is
  recorded.
- **Values never enter a process argument list** (R1). The executor talks to BWS through its API
  in-process, not the `bws` CLI, whose `secret edit` takes the value as an argument. That adds one
  dependency, accepted for R1.

### 4. Where credentials live, and what the root can reach

- **Each rotated credential's keeper moves to its own keeper project** (`Rotation / Keeper /
  <credential>`), readable by exactly the machine accounts that consume that credential. A shared
  keeper project would give every consumer account every rotated credential, undoing the
  narrowing `sds-operator` has today. Credentials that share consumer accounts may share a project.
- **Quarantine and staging secrets live in `Rotation / Work`**, which no consumer account can read.
- **Each provider's admin credential lives in its own project** (`Rotation / <provider>`), the
  executor's WORKER bearer in `Rotation / Executor`, and the read-only Healthchecks API key in
  `Rotation / Healthchecks`. The executor fetches only the one admin secret
  the claimed unit's adapter needs, by UUID, when it needs it.
- **The executor's BWS machine account is the root.** It has write on the keeper projects and
  `Rotation / Work`, and read on `Rotation / <provider>`, `Rotation / Executor` and
  `Rotation / Healthchecks`, and nothing else: not `Ops / Platform`, not `SDS Operator`. Its token lives only in the VM.
- **Before a keeper moves, each consumer account is proven to read it in its new project** (with a
  canary secret: amendment 1) with only `PATH` and `HOME` set, and every repository's `.bws-secrets.toml` that names the secret is updated
  in the same change.
- **The hand-rotated set** is the root token, every admin credential, the executor's own WORKER
  bearer, and the Healthchecks key. Devon rotates these by hand; the executor never rotates them (R8, R12).
- **The SYSTEM and VERIFIER bearers stay in `SDS Operator` and stay hand-rotated.** Rotating them
  by machine would give the root standing write over identities that outrank the executor, which
  the "never borrow another identity" rule forbids. They can join later by a separate decision.
- **The Coolify API token stays out of the executor** until a narrower scope is measured and Devon
  decides it (increment 2). Until then every Coolify write and hosted restart is a human act.
- **Minted keys are as weak as the provider allows:** an `expires_at` and a credit `limit` on every
  OpenRouter key.

### 5. What may trigger a rotation

Only the registry (`rotate_requested`, age) and infraops' structured `security-drift-cli
cred-findings`, through the existing rotation proposer (R5). Content fetched from mail, web pages,
issues, commits or tasks never triggers anything, directly or through an agent's inference.

### 6. Exposure response is a human decision

**No automatic disable or revoke** (Devon, 2026-10-08). An exposure finding is one input; whether
and how fast to contain depends on factors the finding doesn't carry. When Devon approves an
exposure rotation, the record shows, at minimum: where the value was exposed (the scanner's exposure
record), every consumer and whether it is hosted, whether the provider can disable reversibly, and
any credential that shares the value. Devon names further factors before increment 5.

Credentials that share one value (the observer and drift-reporter bearers) are rotated together.
An exposure of either is an exposure of both.

### 7. Self-issued bearers overlap until every consumer has switched

The orchestrator accepts, per identity, an optional previous hash:
`"previous": {"token_hash": "<sha256>", "until": "<UTC instant>"}`. The current hash is always
accepted; the previous hash only before `until`. change-manager gets the equivalent change.

- **The overlap ends by an explicit retire-old step**, after every consumer has passed
  verify-consumers: the executor (or a human step, while Coolify writes are human) removes
  `previous` and restarts, inside a change window. `until` is a backstop, set to seven days, not the
  schedule.
- **Boot refuses an `until` more than eight days ahead**, so a mistyped expiry can't keep an old
  bearer valid indefinitely.
- **The backstop is enforced by the orchestrator, not by the executor stopping.** If consumers
  haven't all switched as `until` approaches, the executor reports, and extending the overlap is a
  defined step: a rewrite of `until` and a restart, a human act while Coolify writes are human.
- **An exposure rotation may skip the overlap**, taking a short outage so the exposed value stops
  working at once. Devon chooses at approval.
- **The parser change ships, deploys and is verified on production before any rotation writes
  `previous`.** An image older than that change refuses to start with `previous` present, so
  rolling back past it requires removing `previous` first. The deploy runbook states this.
- **The credentials setting is generated, not hand-edited.** The setting holds agent ids and
  hashes only, which aren't secret, so the executor produces the complete new value, validated with
  the orchestrator's own parser, and records it as evidence. While Coolify writes are human, Devon
  pastes that value; he never edits the JSON by hand.
- **Before any orchestrator restart, the restarter confirms no dispatched run is live.** While
  restarts are human, that is part of Devon's act; when they become machine work, the Coolify
  decision names the route the executor reads.

### 8. Change windows apply at the orchestrator's claim

**Devon decided (2026-10-08):** the window check lives in the orchestrator's claim path, so it holds
whoever claims, and resuming a paused rotation counts as continuing, not starting.

- **Where.** For operational units only, a claim refuses `outside_change_window` when the unit's
  package declares a reach whose window is closed. Unlike `window_refusal` at admission, it fails
  closed on an undeclared or unreadable reach, because no admission check stands behind it. It
  applies to every claimant of an operational unit, including HQ working one by hand.
- **Starting versus continuing.** A unit's first claim is a start and is windowed. A reclaim of a
  unit with evidence from an earlier attempt is a continuation of that unit and isn't. Renewal
  continues and is never windowed. This departs deliberately from the rule that a per-claim rule
  covers all three lease writers: the window gates starts, and renewal never starts work.
- **Hosted steps are windowed by the server.** Each step that writes to a hosted consumer is its own
  unit (decision 3), and every hosted unit's first claim is windowed. A rotation paused for a human
  act resumes its non-hosted work at any hour. A rotation whose next hosted unit becomes ready
  after the window closes waits for the next night, leaving both values live for up to a day. That
  is safe, because the old value is retired only after every consumer verifies, and it is accepted
  as the price of the server enforcing the window.
- **Only the window applies at claim.** The other admission terms (posture, known-good patterns)
  aren't asked: an operational unit's authority is already human-approved.

### 9. State, leases and recovery

- **Rotation state lives in the orchestrator**, as evidence on the units: fingerprints (sha256
  prefix and length) and probe results per step. Never a value.
- **Rotation evidence has a strict schema and is secret-scanned at ingest** with the observation
  detector. Field names avoid every one of its key-name parts (`SECRET_KEY_PARTS`, which includes
  `token`, `credential`, `log`, `body` and `response`).
- **A new value is stored in BWS (staging or keeper) before anything depends on it.** If the VM is
  lost, the rotation resumes from a rebuilt VM with both values still accepted, because retire-old
  runs only after every consumer verified.
- **Leases:** the executor renews while a step runs, and each step is bounded to fit the lease
  ceiling (2 hours). No claim waits on a human, because human steps are separate units. A step that
  can't fit (for example confirming a BWS token dead, which can read live for up to an hour after
  revoke) is split into its own unit that starts after the wait. (Amendment 1: a fresh login fails at
  once, so BWS confirm-dead needs no wait; only the exposure window lasts the hour.)
- **A lapsed claim** (a crash, a sleeping Mac) is reclaimed by the existing SYSTEM recovery path on
  request; that is rare enough to stay a human-initiated act. Rotation units are created with
  `work_units.max_attempts` of at least three (the column is what's enforced, not
  `authority.budgets.max_attempts`).

### 10. Human gates

Devon clicks every gate on the first rotation of each class (ADR-0054 amendment 1). Which gates
graduate, and on what evidence, is decided after that rotation has run.

## How each consumer kind is verified

R15 says a green write isn't proof. Three kinds can't be probed by the executor; each is a stated
exception, and Devon accepts these with this ADR.

| Consumer kind | Verified by | Exception to R15? |
|---|---|---|
| `bws-secret` (keeper) | Reading the keeper back and comparing fingerprints | No |
| `coolify-env-hash` (orchestrator bearers) | Authenticating to `sds.alobar.net` with the new value | No |
| `coolify-env` (change-manager bearers) | Authenticating to `change-mgr.alobar.net` with the new value, with a named User-Agent | No |
| `coolify-env` (a third-party key a hosted app uses) | A human attestation that the app works after restart, until an app-level probe exists | **Yes** |
| `runtime-fetch` on a schedule | The keeper readback, the consumer account's read proven at the keeper move (decision 4), and the consumer's next run reported green by Healthchecks, read directly with a read-only Healthchecks API key in `Rotation / Healthchecks` rather than through the orchestrator | No |
| `runtime-fetch` run only when invoked (for example the intent-packages CLI) | A human attestation after running it | **Yes** |
| `gh-actions-secret` | A human attestation until a probe workflow exists (increment 7 decides it; it needs both workflow-guard allowlists) | **Yes** |
| `keychain` | Not a machine kind. `openrouter-generic`'s Keychain copy is dropped (below); BWS machine tokens' Keychain consumers are human-rotated | — |

## Threat model

### Assets

| Asset | Where it lives | Who can read it | Scope | Rotated by |
|---|---|---|---|---|
| Executor BWS token (**root**) | The VM only | The executor; anything with root in the VM | Write on the keeper projects and `Rotation / Work`; read on `Rotation / <provider>`, `Rotation / Executor` and `Rotation / Healthchecks` | Devon, by hand |
| OpenRouter management key | `Rotation / OpenRouter` | The root | Account-wide, unscoped (provider limit) | Devon, by hand |
| Rotation GitHub App private key (increment 7; amendment 1) | `Rotation / GitHub` | The root | Mints installation tokens with Secrets: write (and Metadata: read) on only the repositories where the App is installed: those holding a registered Actions-secret copy | Devon, by hand |
| Read-only Healthchecks API key | `Rotation / Healthchecks` | The root | Read check status only | Devon, by hand |
| Executor WORKER bearer | `Rotation / Executor`, readable by the root | The root | Claim and file evidence on rotation units only (decision 1) | Devon, by hand |
| Rotated credentials and values in flight | Per-credential keeper projects; `Rotation / Work`; the executor's memory | The root, and each credential's consumers' accounts | That credential's scope | The executor |

### Threats

| # | Threat | What it gains | Containment |
|---|---|---|---|
| T1 | An agent session on the Mac reads the VM | Every asset above | **Accepted residual** (decision 2). No value is in the Mac's Keychain, files or environment, so it takes a deliberate act. The Coolify token, SYSTEM and VERIFIER bearers are outside the root's reach. |
| T1a | A consumer account is compromised | The credentials it consumes, plus nothing else | One keeper project per credential (decision 4); quarantined and staged values are in a project no consumer reads. |
| T2 | A forged trigger from fetched content | Unwanted rotations | Triggers are the registry and `cred-findings` only; every rotation needs a human-approved record. |
| T3 | The orchestrator is compromised | It can mark units approved and supply evidence | The executor checks the approved revision against intent-packages' git and the registry against its repository's `main`, compares destinations in full, re-probes rather than trusts evidence, and derives revoke ids from the quarantined value. Result: churn, not exposure. |
| T4 | A registry change adds a hostile consumer (no review is required to merge) | A value written where an attacker reads it | The revision carries the full destination list and `/review` shows its difference from the last approved revision, so a new destination is in front of Devon before any value goes there; the executor refuses any registry that differs from the approved list. |
| T5 | Another WORKER claims a rotation unit, or the executor claims other work | Forged step evidence, or the executor acting outside rotation | Claims and reclaims are confined both ways at the server (decision 1). |
| T6 | The root writes a keeper with an attacker's value | Consumers run on an attacker's account | Only through T1. Fingerprint evidence for every write makes a substitution visible after the fact. |
| T7 | A compromised executor dependency | The same as T1, from inside | Few dependencies, pinned by hash. |
| T8 | The management key revokes the wrong key | An outage elsewhere (for example the brains' separate OpenRouter key) | The revoke id is derived from the quarantined value at revoke time and confirmed by the provider (amendment 1), never taken from evidence or matched by label alone. |
| T9 | A value leaks into a log, evidence row or observation | The value, permanently in append-only evidence | R1; values never in argv; evidence has a strict schema and is secret-scanned at ingest; output passes through a scrubber of every value touched. |
| T10 | An executor defect causes an outage | Availability | Adapter-declared order with contract invariants; probes with known-bad controls; stop on indeterminate; windowed starts; overlap ended only after every consumer verified. |
| T11 | An orchestrator image rollback after a bearer rotation | Every program down (boot refuses `previous`) | `previous` is removed at retire-old; the runbook removes it before any rollback past the parser change. |
| T12 | The Mac is stolen | Every asset | FileVault; the hand-rotated set is rotated by hand. |

### What the baseline risk was

Before this design, an exposed credential waited on about eleven human acts, and the exposure path
had never run end to end. This design keeps the human decision (decision 6) and removes the human
mechanics where a provider has an API.

## Per-class disposition

| Class | Live | Mint | Deploy | Retire-old | Notes |
|---|---|---|---|---|---|
| OpenRouter key | 1 | machine | machine (BWS) | machine for keys the executor minted; human for today's key | |
| GitHub PAT (fine-grained) | 2 | human | machine for BWS and (increment 7) Actions secrets, through the rotation App's installation tokens; human for Coolify | machine (`POST /credentials/revoke`) | |
| Atlassian API token | 1 | human | machine for BWS; human for Coolify | human | Revoke API unverified |
| BWS machine token | 4 | human | human (Keychain consumers on the Mac) | human | Executor records evidence only; confirm-dead is Devon's fresh-login check (amendment 1) |
| change-manager bearer | 4 | machine | human until the Coolify decision | ends the overlap | Plaintext env: while deploy is human, the value passes through the Coolify UI on the Mac, a stated exception to "no value on the Mac" |
| orchestrator bearer | 4 of 6 | machine | human until the Coolify decision | ends the overlap | SYSTEM and VERIFIER stay hand-rotated (decision 4); observer and drift-reporter rotate together; factory-runner's bearer waits for increment 7's Actions-secret writer |

**The Keychain copy is dropped.** Devon decided on 2026-10-08 that whatever needs
`OPENROUTER_API_KEY` fetches it from BWS, so the credential's only stored copy is its keeper. The
shell export from Keychain item `openrouter-api` (infraops registry, lines 182–212) is replaced in
increment 5, and the registry entry loses its `keychain` consumer.

## What this changes in ADR-0054

- **"Execute" in the flow, and "The rotation worker's authority and boundaries":** the worker runs
  in an isolated OrbStack VM, holds its own root token over dedicated rotation projects, not
  `BWS_ACCESS_TOKEN_CRED_ROTATION`, and its claims are confined at the server.
- **"What stays human":** minting is machine work wherever the provider has an API the executor's
  credentials can use; containment of an exposure is human; the hand-rotated set (decision 4) stays
  human.
- **"Change window":** enforced at the orchestrator's claim (decision 8).
- **Increments 3 and 4** are replaced by the increment plan below.

Everything else in ADR-0054 and its amendments stands.

## The parked rotation

**Retire it** (Devon, 2026-10-08). On acceptance: supersede decomposition `ff08aeed` (ADR-0052; no
unit was claimed), remove `rotate_requested` from `openrouter-generic` in infraops' registry, and
resolve backlog item `37bb0f906c7b`. `openrouter-generic` stays flagged `rotated_by_sds`, so the
legacy window keeps refusing it.

Until increment 5, that credential has no machine path. Its next age-due date is 2027-07-02, after
the plan completes. An exposure in the gap is rotated by hand, as before ADR-0054. The proposer
isn't scheduled, so it can't re-propose against the stale rev-1 package meanwhile.

## Increments

1. **Accept and retire.** Accept this ADR; retire the parked rotation.
2. **Measure, then decide.** Measure, with throwaway keys and no live secret:
   - whether a Coolify token with `write` but not `read:sensitive` can PATCH application envs;
   - whether an OpenRouter management key can carry an expiry, and whether the key-info endpoint
     returns the id needed to revoke a key from its value;
   - whether a disabled or deleted OpenRouter key probes 401;
   - the `bws` (or SDK) result for a revoked machine token;
   - whether moving a BWS secret between projects keeps its UUID;
   - whether a GitHub App installation token can write Actions secrets;
   - whether `orb -u root` works without a password on an isolated machine.

   Then Devon decides the Coolify token's scope and holder, a standing-authority change.
3. **Orchestrator and package changes, shipped and deployed before any rotation uses them:**
   - claim and reclaim confinement both ways, with the rotation-unit predicate;
   - the operational claim window check;
   - a unit's completion resolving the `work_unit` dependencies that name it;
   - a `/review` form for Devon to resolve a human-act dependency (production `/api` accepts only
     machine bearers, so no human route exists today), added to the `test_scope_guards.py`
     inventories, with the resolution `detail` given a strict schema and the evidence secret scan;
   - the rotation evidence schema and its secret scan;
   - the bearer `previous` parser with its eight-day cap, verified on production;
   - the destination-list profile field (intent-packages schema), the proposer writing it, and
     `/review` showing its difference from the last approved revision;
   - the executor's WORKER identity (security-standards commit and image rebuild).

   Each guard is reviewed by mutation.
4. **The executor, with no live credentials.** The contract, its invariants, the revision and
   registry checks, a fake adapter, built and tested in an isolated OrbStack VM. The `Rotation`
   BWS projects, the root token, and the read-only Healthchecks key.
5. **OpenRouter.** Drop the Keychain copy; move the keeper to its own project with its consumer
   accounts proven; the adapter and the management key. Request `openrouter-generic` again and run
   it with every gate clicked.
6. **Self-issued bearers** (change-manager, and the orchestrator bearers named in the per-class
   table, except factory-runner's). Move each keeper to its project, proving every consumer
   account, including the shared account behind the vps-backup and infra-drift tokens that reads
   the drift-reporter bearer. Coolify steps per Devon's increment 2 decision.
7. **GitHub PATs, the Atlassian token, and factory-runner's bearer.** Keeper moves as above; the
   Actions-secret writer is a dedicated rotation GitHub App (amendment 1), installed only on the
   repositories holding a registered copy, with installation tokens minted per repository; the
   Actions-secret probe workflow, or its stated exception.
8. **BWS machine tokens.** All steps human, including amendment 1's fresh-login confirm-dead check;
   the executor records the evidence.
9. **Retire the infraops window** (ADR-0054 increment 5).

Gate graduations (decision 10) are decided per class after that class's first rotation.

## Consequences

- One contract replaces per-class procedures; a provider without an API is the same contract with
  human steps.
- Admin credentials no longer touch the Mac's Keychain, environment or tools. A deliberate act
  inside the VM can still reach them; that risk is accepted and stated. The Coolify token and the
  SYSTEM and VERIFIER bearers are outside the executor's reach.
- Until Devon decides the Coolify token, every hosted consumer deploy is a human step, which is ten
  of the eighteen live credentials.
- The orchestrator's claim path and authentication path both change (decisions 1, 7 and 8), and
  each ships with mutation review.
- Every human act is a dependency Devon resolves at `/review`. Until the Coolify decision and the
  gate graduations, a hosted rotation still has several of them; the design removes the human
  mechanics where a provider has an API, not the human decisions.
- About a dozen BWS projects replace one shared keeper project. That is configuration, not code,
  and it is what keeps a consumer account from reading every rotated credential.
- Moving to a VPS, or to customer-run executors, changes where the VM runs, not the design.

## Review findings and how they're resolved

| # | Finding | Resolved by |
|---|---|---|
| 1 | The root's keeper-project write reached the Coolify token and SYSTEM/VERIFIER bearers | Decision 4: rotation-only keeper projects (made per credential after finding C); SYSTEM and VERIFIER stay hand-rotated |
| 2 | A fixed step order broke bearers; `until` revoked before consumers switched | Decision 3 (adapter-declared order); decision 7 (explicit retire-old, `until` a backstop) |
| 3 | `previous` bricks boot on an older image | Decision 7: parser ships first; `previous` removed at retire-old; rollback runbook |
| 4 | Human steps and lapsed leases had no mechanism | Decision 3 (human steps are units); decision 9 (leases, reclaim) |
| 5 | Registries merge without review; the allowlist didn't contain | Decision 1: registry fingerprint in the approved revision |
| 6 | Resume trusted orchestrator data | Decision 1: ancestry check, re-probe, revoke id derived from the value |
| 7 | Claims weren't confined | Decision 1: confined both ways at the server |
| 8 | Claim window check underspecified | Decision 8: operational units only, fail closed, renewal exemption stated |
| 9 | Evidence isn't secret-scanned | Decision 9: strict schema, scanned at ingest |
| 10 | verify-consumers undefined for unreachable consumers | "How each consumer kind is verified", with two stated exceptions |
| 11 | No new-equals-old guard; human-mint handoff undefined | Decision 3: fingerprint guard, staging secret |
| 12 | R1, R4, R6, R13, R16 and the dispatched-run check missing | Decisions 1, 2, 3, 7 and 9 |
| 13 | Increment ordering defects | Increments 2 (measure and decide first), 3 (orchestrator first), 7 (factory-runner bearer with its writer); the gap is stated |
| 14 | Minor: analogy, cleanup, shared value, own bearer | Decisions 1, 3, 6 and 4 |
| A (2nd) | Human-step units couldn't be worked | Decision 3: human acts are dependencies Devon resolves; completion resolves `work_unit` dependencies |
| B (2nd) | The registry fingerprint had no author and didn't catch an earlier hostile edit | Decision 1: full destination list in the revision, shown as a diff at `/review`, revision checked against git |
| C (2nd) | A shared keeper project spread read access across consumer accounts | Decision 4: one keeper project per credential; `Rotation / Work` for quarantine and staging; consumer reads proven before a move |
| D (2nd) | The rotation-unit predicate was undefined; reclaim wasn't confined | Decision 1: named predicate, enforced in claim and reclaim |
| E (2nd) | The `until` backstop wasn't enforceable by stopping; no cap | Decision 7: eight-day boot cap; extension is a defined step |
| F (2nd) | `runtime-fetch` verification was an undeclared exception | Verification table: Healthchecks read directly; invoked-only consumers a stated exception |
| G (2nd) | The first-hosted-step window lived only in the executor | Decisions 3 and 8: each hosted step is its own unit, windowed at its first claim |
| H (2nd) | The increment plan was missing work | Increments 3, 5, 6 and 7; `max_attempts` column (decision 9) |

## Amendment 1 — Increment 2 measurements (2026-10-09)

Increment 2 measured the seven facts in the increment plan with throwaway resources named
`probe-inc2-…` and no live credential. The full record, with timestamps, is
`docs/superpowers/plans/2026-10-09-rotation-increment-2-measurement-log.md`. Values stayed in one
process; the record holds status codes, field names, sha256 prefixes and lengths only.

Each answer below rests on a probe that answered differently for a known-good and a known-bad case
in the same run, except where the row says otherwise: question 2's management-key expiry is a
console observation, and every statement labelled "source" is a reading of Coolify's code, not a
measurement.

### Answers

| # | Question | Answer | Command shape and discrimination | Changes |
|---|---|---|---|---|
| 1 | Can a Coolify token with `write` and `deploy` but not `read:sensitive` change an env var and restart, and does it see `real_value`? | **Yes, it writes and restarts, and it never sees the value**: its envs response has no `value` or `real_value` field at all. Measured with `read` + `write` + `deploy`; a token without `read` wasn't tried. Each variable is stored twice (`is_preview` false and true), and a PATCH changes one row. **Dev only** (beta.470); production runs beta.473, whose permission and sensitive-data code is byte-identical (source). | `POST`/`PATCH /applications/{uuid}/envs`, `GET …/envs`, `POST …/restart`; the restarted container's value hashed on the VM. Tokens without `write` get 403 `Missing required permissions: write`; without `deploy`, 403 `…: deploy`. A `read` + `read:sensitive` token sees both fields; the candidate sees neither. | The Coolify decision (following section); decision 7's retire-old; increment 6 |
| 2 | Can an OpenRouter management key carry an expiry? Does key-info return the revoke id? | **Expiry: the console offers one** (set to one hour; enforcement not probed). **A minted key accepts `expires_at`** as `…Z` (stored and returned) and rejects `…+00:00` with 400. **Revoke id: no.** `GET /api/v1/key` returns 23 fields, `label` among them, and no `hash`. The hash equals sha256 of the key value (undocumented; two keys). `/api/v1/auth/key` answers identically to `/api/v1/key`. | `POST /api/v1/keys` with `limit: 0.01`, then key-info with the minted value. A malformed key gets 401 `User not found.` | Decision 1 and T8 (revoke id); confirms decision 4's `expires_at` and `limit`; increment 5 |
| 3 | Does a disabled or deleted OpenRouter key probe 401? | **Yes. Key-info lags; inference doesn't.** `/key` and `/auth/key`: 200 at +5 s after delete and at +0 s after disable; 401 at +66 s (deleted) and +60 s (disabled). Those are the only probe times, so the lag is somewhere under about a minute. A chat completion on a `:free` model answers 401 at the first probe after each. | Live baseline in the same run: the key answers 200 on `/key`, `/auth/key` and the completion. Then `PATCH /keys/{hash} {disabled: true}` or `DELETE /keys/{hash}`. A malformed key gets 401 on all three, at the start and end. | Increment 5's confirm-dead step; decision 3 |
| 4 | What does a revoked BWS machine token return, and how long after the revoke? | **A new login failed at the first probe after the revoke**, with exit 1 and `[400 Bad Request] {"error":"invalid_client"}`, the same answer a malformed token gets. **Sessions logged in before the revoke kept working until +50 min and failed by +55 min** (the `bws` CLI's cached state: then `invalid_client`; a held SDK client: then 401). Both had logged in 2 to 6 minutes before the revoke, so this doesn't separate "a session lives an hour from login" from "a revoke reaches open sessions after about 55 minutes". The probe was `project list`; secret reads weren't probed. | `bws project list --color no`, token in the environment, with the real `HOME` (cached state) and with an empty `HOME`; Python SDK `bitwarden-sdk` 2.1.0 with a held client and a new client. Every path read at the baseline and failed after. The control, the same token with its secret part altered, got `invalid_client` on every fresh path and **succeeded** through the cached state, which is how the cache was found. | Decision 3 ("dead is exactly 401"); decision 9 (split confirm-dead unit); increments 4 and 8 |
| 5 | Does moving a BWS secret to another project keep its UUID? | **Yes.** Access follows the project: after the move, an account with read only on the old project gets the same 404 as for a UUID that doesn't exist. Moving it back wasn't tried. | `bws secret edit --project-id <b> <id>` as a writer on both projects; list both projects; read by id as a reader of the first project only, before and after, beside a random-UUID control. | Decision 4's keeper moves; increments 5 to 7 |
| 6 | Can a GitHub App installation token write Actions secrets when the App has `secrets`? | **Yes.** The dispatch App can't: its installation had no `secrets` permission when measured on 2026-09-02 (`credentials.md` #274). | Throwaway App (Secrets: read and write; Metadata: read) installed on one throwaway repository. Mint for that repository with `{secrets: write, metadata: read}`; the mint response's `permissions` match. Seal with the repository's public key (PyNaCl); `PUT …/actions/secrets/{name}` 201; the list shows a fresh `updated_at`. A token minted with `{metadata: read}` gets 403 `Resource not accessible by integration` on the same PUT. | Threat-model asset row; per-class GitHub row; increment 7 |
| 7 | Does `orb -m <machine> -u root` work without a password on an isolated machine? | **Yes.** | `orb create --isolated --isolate-network ubuntu:noble`, then `orb -m <machine> -u root id -u` with stdin closed: rc 0, `0`. The default user prints `501`. | None: decision 2's residual-risk statement stands |

### What the answers change

- **Coolify writes are blind, so verification stays with the consumer** (question 1). A token
  without `read:sensitive` can't read back what it wrote. "How each consumer kind is verified"
  already verifies a `coolify-env-hash` or `coolify-env` consumer by authenticating to the service,
  so no second Coolify token is needed for verification.
- **A Coolify write sets both rows of a variable** (question 1): one PATCH without `is_preview` and
  one with `is_preview: true`, or the retired value stays stored in the preview row. Measured through
  the API; whether the Coolify UI updates both rows wasn't checked, so a human write is checked
  through the API afterwards.
- **The OpenRouter revoke id is derived, then confirmed by the provider** (question 2). This replaces
  decision 1's "derived at revoke time from the quarantined value itself, through the provider" and
  T8's containment: compute sha256 of the quarantined value; `GET /api/v1/keys/{that hash}` with the
  management key must return 200 with the same `label` that key-info returns for the value; only then
  revoke. A hash stored from the create response is evidence the orchestrator holds, which T3 says
  isn't trusted, so it's never the revoke id; deriving it from the value is. Matching by label alone
  stays forbidden.
- **OpenRouter confirm-dead uses inference** (question 3). The dead probe is a zero-cost completion
  (a `:free` model chosen at probe time from the model list) with the quarantined value, beside the same completion with the new value (must answer 200) and a
  malformed key (must answer 401), all in one step. Key-info polled until 401 is the fallback, with a
  deadline of a few minutes.
- **BWS confirm-dead logs in fresh, and its control is a live token, not a malformed one** (question
  4). A probe that reuses the `bws` CLI's state (`~/.config/bws/state/`) or a held SDK client reports a
  revoked token as live, so the probe uses a new SDK client with no state. A malformed token returns
  the same `invalid_client` as a revoked one, so a failure proves nothing unless the same value was
  shown to work. Two halves, both fingerprinted: **before** the revoke, a fresh login with the old
  value succeeds; **after**, a fresh login with the same fingerprint fails with `invalid_client` while
  a fresh login with the replacement succeeds. Each login uses an empty, throwaway `HOME` that is
  deleted afterwards; the before-half's session stays usable for up to about an hour, so the exposure
  window counts from it. **This is Devon's procedure in increment 8, which stays all human:** running
  it as a machine unit would put live consumer BWS tokens, the old one and its replacement, inside
  the executor's reach, which decision 4 rules out. Decision 3's "dead is exactly 401" becomes, for
  BWS, Devon's attestation of that pair. The same holds
  for OpenRouter, where malformed and revoked both answer `401 User not found.`: the revoke step's
  key-info 200 on the quarantined value is the live-before half, and increment 5 must keep it.
- **Confirm-dead for BWS needn't wait an hour, but the exposure lasts one** (question 4). New logins
  failed at the first probe, so Devon's confirm-dead check can run straight after the revoke.
  Sessions already open kept working for about 55 minutes, and nothing in this increment shortened
  that. Decision 9's split confirm-dead unit isn't needed for confirm-dead. Devon attests the exposure
  window closed 75 minutes after the revoke, later than the before-half session's hour; the
  executor files that attestation as evidence. It is time-based, not an observation, and whether a held
  SDK client renews its session wasn't measured.
- **Keeper moves keep UUIDs, and consumers are proven before the move with a canary** (question 5).
  The `uuid` in every `.bws-secrets.toml` stays; its `project` field changes. A consumer account loses
  read the moment the secret moves unless it can already read the target project. So: grant every
  consumer account read on the keeper project, create a canary secret there and prove each consumer
  reads it with only `PATH` and `HOME` set (decision 4's "proven before a keeper moves"), delete the
  canary, then move the secret. The canary proves only the registry's consumer list, so the consumer
  set is first checked by grepping the portfolio for the secret's UUID and name. The move needs write
  on both projects, and the root has none on `Ops / Platform` or `SDS Operator`, so moving a keeper out
  of them is a human act (Devon's account), a dependency like any other. Moving a secret back was not
  measured; don't rely on it as the undo.
- **The GitHub secret writer is a dedicated rotation App** (question 6). On 2026-10-09 Devon chose,
  of the options for question 6, to record the dispatch App's earlier measurement and measure with a
  throwaway App (options 1 and 3), and to build a dedicated App for increment 7: "I think 1 +3, and we
  create a dedicated rotation app". The measurement holds, so the asset table, the per-class GitHub
  row and increment 7 are updated. The App holds Secrets: write and the mandatory Metadata: read, and
  is installed only on the repositories that hold a registered Actions-secret copy. The executor
  mints each installation token for one repository with `{secrets: write, metadata: read}`, as
  measured. GitHub shows a notice that installation tokens are moving to a longer format (up to about
  520 characters); nothing should assume their length.

### The Coolify decision

Put to Devon on 2026-10-09 as a standing-authority decision. **Every Coolify answer above was
measured on dev (beta.470).** Production runs beta.473, whose permission and sensitive-data code is
byte-identical to dev's (source), so the dev answer is a strong hypothesis for production, not a
measurement of it. Whichever option is chosen, the first step of the increment that first uses a
Coolify token repeats question 1's probes on production with a throwaway application.

Coolify tokens belong to a team and carry abilities that name actions, not resources (source): no
token can be limited to particular applications. The options, least machinery first:

1. **No Coolify token in the executor** (the current design). Every hosted deploy, restart and
   retire-old stays a human act, for ten of the eighteen live credentials (Consequences). Nothing new
   is exposed.
2. **A `read` + `write` + `deploy` token without `read:sensitive`**, in its own `Rotation / Coolify`
   project that only the root reads, and in the hand-rotated set. `read` is included because that is
   what was measured; whether `write` + `deploy` alone works wasn't tried. The executor writes both
   rows of each variable, restarts, verifies by authenticating to the service, and ends the overlap,
   without seeing a stored value. It removes the Coolify writes and restarts from those ten
   credentials' rotations. It doesn't remove the human attestation for a third-party key used by a
   hosted app ("How each consumer kind is verified"). Decision 7's check that no dispatched run is
   live before an orchestrator restart is an orchestrator read, not a Coolify one, and increment 6
   must name it. What the token can do, team-wide, on production:
   - **Measured on dev:** create a project; create an application from any public image and start
     it; write any application's env vars; restart; delete an application and a project.
   - **From the source (beta.473):** `write` guards 73 routes, including deleting servers, databases
     and services and changing or deleting SSH keys and GitHub App connections; `deploy` guards 11
     (start, stop, restart, deploy and cancel).
   - **Not measured, but likely:** a token that can run any image on the production host can probably
     read the host's secrets from inside that container, so "without `read:sensitive`" shouldn't be
     read as "can't reach secrets".

   This is near-full control of production Coolify. It widens T1's accepted residual from "every
   asset in the VM" to that plus control of production Coolify; today no Coolify credential is
   within the root's reach.
3. **A narrower holder** would need a separate Coolify team holding only the rotated applications, so
   a team-bound token reaches nothing else. That wasn't measured, and moving production applications
   between teams is its own change.

If Devon chooses option 2, the same change updates: the asset table (a `Rotation / Coolify` row);
T1's "The Coolify token… outside the root's reach"; decision 4's Coolify bullet; the per-class rows
that say "human until the Coolify decision"; Consequences bullets 2, 3 and 5; and increment 6.

Devon's answer: ANSWER_PLACEHOLDER
