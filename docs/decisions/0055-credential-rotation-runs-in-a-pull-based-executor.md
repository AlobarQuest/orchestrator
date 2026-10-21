# ADR-0055 — Credential rotation runs in a pull-based executor through one contract

- **Status:** Accepted
- **Date:** 2026-10-08
- **Decided by:** Devon, decisions recorded 2026-10-08 in
  `docs/superpowers/specs/2026-10-08-credential-rotation-requirements.md`. Accepted 2026-10-08 ("Yes, I accept ADR-0055").
- **Amended:** 2026-10-09, amendment 1 (increment 2's measurements; the Coolify token decision);
  2026-10-09, amendment 2 (increment 3's code read; where destinations are reviewed, which window
  applies at claim, and per-unit completion).
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
  Both `claim_unit` and the reclaim path (`_acquire_reclaimed_claim`) enforce it (amendment 2:
  reclaim never grants a rotation unit); renewal is already
  bound to the claim's owner. Each check ships with a mutation test.
- **What it acts on is fixed by the human approval, not by the unit.** The package revision carries
  the credential's destinations in full: provider, endpoints, and every consumer's kind and
  destination, as a new `non-software-operational` profile field that the package author writes.
  Devon reads the list's change in the revision's diff before approving it by name (amendment 2).
  The executor then:
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
  transaction. This is a general orchestrator change (increment 3). Completion itself stays a
  human click until gates graduate (amendment 2).
- **Each step that writes to a hosted consumer is its own unit**, so its first claim is windowed at
  the server (decision 8).
- **After confirm-dead, the quarantine and staging secrets are deleted**, and the deletion is
  recorded.
- **Values never enter a process argument list** (R1). The executor talks to BWS through its API
  in-process, not the `bws` CLI, whose `secret edit` takes the value as an argument. That adds one
  dependency, accepted for R1.

### 4. Where credentials live, and what the root can reach

- **Every rotated credential's keeper moves to one keeper project, `Rotation / Keepers`**, which
  only the root has access to. Each consumer account gets a direct read grant on its own keeper
  secrets, so it reads exactly the credentials it consumes and nothing else in the project
  (amendment 6). A shared keeper project with project-wide read would give every consumer account
  every rotated credential, undoing the narrowing `sds-operator` has today.
- **Quarantine and staging secrets live in `Rotation / Work`**, which no consumer account can read.
- **Every provider's admin credential lives in one project, `Rotation / Admin`** (amendment 6), the
  executor's WORKER bearer in `Rotation / Executor`, and the read-only Healthchecks API key in
  `Rotation / Healthchecks`. The executor fetches only the one admin secret
  the claimed unit's adapter needs, by UUID, when it needs it.
- **The executor's BWS machine account is the root.** It has write on `Rotation / Keepers` and
  `Rotation / Work`, and read on `Rotation / Admin`, `Rotation / Executor` and
  `Rotation / Healthchecks`, and nothing else: not `Ops / Platform`, not `SDS Operator`. Its token lives only in the VM.
- **Before a keeper moves, each consumer account is proven to read it in its new project** (with a
  canary secret: amendment 1) with only `PATH` and `HOME` set, and every repository's `.bws-secrets.toml` that names the secret is updated
  in the same change.
- **The hand-rotated set** is the root token, every admin credential, the executor's own WORKER
  bearer, and the Healthchecks key. Devon rotates these by hand; the executor never rotates them (R8, R12).
- **The SYSTEM and VERIFIER bearers stay in `SDS Operator` and stay hand-rotated.** Rotating them
  by machine would give the root standing write over identities that outrank the executor, which
  the "never borrow another identity" rule forbids. They can join later by a separate decision.
- **The executor holds a production Coolify token with `read`, `write` and `deploy`, without
  `read:sensitive`** (Devon, 2026-10-09; amendment 1), in its own `Rotation / Coolify` project that only
  the root reads. It is in the hand-rotated set. It is created in the increment that first uses it,
  after question 1's probes are repeated on production. Until then every Coolify write and hosted
  restart is a human act.
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
any credential that shares the value. Devon added two more on 2026-10-13 (amendment 7): any use of
the credential since the exposure, as far as the provider reports it, and whether the credential
carries a spending limit or an expiry.

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
  package declares a reach whose window is closed. (Amendment 2 supersedes this bullet and the
  next: the check applies to rotation units only, asks the `live_estate` window whatever reach is
  declared, skips units marked non-hosted, and redefines start and continuation; reclaim no longer
  grants a rotation unit, and only the executor claims one.) Unlike `window_refusal` at admission, it fails
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
- **A lapsed claim** (a crash, a sleeping Mac) is recovered by the SYSTEM recovery path on
  request (amendment 2: released without a grant, then claimed again by the executor); that is rare enough to stay a human-initiated act. Rotation units are created with
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
| `gh-actions-secret` | Its repository's hourly `secret-fingerprint.yml` report showing the keeper's fingerprint, read with the rotation App (amendment 10) | No |
| `keychain` | Not a machine kind. `openrouter-generic`'s Keychain copy is dropped (below); BWS machine tokens' Keychain consumers are human-rotated | — |

## Threat model

### Assets

| Asset | Where it lives | Who can read it | Scope | Rotated by |
|---|---|---|---|---|
| Executor BWS token (**root**) | The VM only | The executor; anything with root in the VM | Write on `Rotation / Keepers` and `Rotation / Work`; read on `Rotation / Admin`, `Rotation / Executor` and `Rotation / Healthchecks` (amendment 6) | Devon, by hand |
| OpenRouter management key | `Rotation / Admin` | The root | Account-wide, unscoped (provider limit) | Devon, by hand |
| Rotation GitHub App private key (increment 7; amendments 1 and 10) | `Rotation / Admin` | The root | Mints installation tokens with Secrets: write or Actions: read (and Metadata: read) on only the repositories where the App is installed: those holding a registered Actions-secret copy | Devon, by hand |
| Production Coolify token (amendment 1) | `Rotation / Coolify` | The root | Team-wide `read`, `write` and `deploy`, no `read:sensitive`: env writes, restarts, creating and deleting applications, projects, servers, databases and SSH keys; can run any image, so probably reaches secrets from a container | Devon, by hand |
| Read-only Healthchecks API key | `Rotation / Healthchecks` | The root | Read check status only | Devon, by hand |
| Executor WORKER bearer | `Rotation / Executor`, readable by the root | The root | Claim and file evidence on rotation units only (decision 1) | Devon, by hand |
| Rotated credentials and values in flight | `Rotation / Keepers`; `Rotation / Work`; the executor's memory | The root, and each credential's consumers' accounts, by direct grant on that secret | That credential's scope | The executor |

### Threats

| # | Threat | What it gains | Containment |
|---|---|---|---|
| T1 | An agent session on the Mac reads the VM | Every asset above | **Accepted residual** (decision 2). No value is in the Mac's Keychain, files or environment, so it takes a deliberate act. SYSTEM and VERIFIER bearers are outside the root's reach. The rotation Coolify token is inside it (amendment 1); an agent on the Mac already controls production Coolify through infraops, so this doesn't widen what such an agent can do. |
| T1a | A consumer account is compromised | The credentials it consumes, plus nothing else | One keeper project per credential (decision 4); quarantined and staged values are in a project no consumer reads. |
| T2 | A forged trigger from fetched content | Unwanted rotations | Triggers are the registry and `cred-findings` only; every rotation needs a human-approved record. |
| T3 | The orchestrator is compromised | It can mark units approved and supply evidence | The executor checks the approved revision against intent-packages' git and the registry against its repository's `main`, compares destinations in full, re-probes rather than trusts evidence, and derives revoke ids from the quarantined value. Result: churn, not exposure. |
| T4 | A registry change adds a hostile consumer (no review is required to merge) | A value written where an attacker reads it | The revision carries the full destination list, and its change is in the diff Devon approves; the list is also in each unit's authority, shown at `/review` (amendment 2, which states the residual); the executor refuses any registry that differs from the approved list. |
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
| OpenRouter key | 1 | machine | machine (BWS) | machine, today's key included (amendment 5) | |
| GitHub PAT (fine-grained) | 2 | human | machine for BWS and Actions secrets, through the rotation App's installation tokens; no Coolify copy once the mirror fetches from BWS (amendment 10) | machine (`POST /credentials/revoke`, by value) | |
| Atlassian API token | 1 | human | machine for BWS; no Coolify copy once the mirror fetches from BWS (amendment 10) | human, gated by a `verify` unit | Revoke API unverified |
| BWS machine token | 4 | human | human (Keychain consumers on the Mac) | human | Executor records evidence only; confirm-dead is Devon's fresh-login check (amendment 1) |
| change-manager bearer | 4 | machine | machine through the Coolify token, from increment 6 (amendment 1) | ends the overlap | Each variable holds the bearer's sha256 hash with an optional `previous` (amendment 9, decision 6), so no value passes through Coolify |
| orchestrator bearer | 4 of 6 | machine | machine through the Coolify token, from increment 6 (amendment 1) | ends the overlap | SYSTEM and VERIFIER stay hand-rotated (decision 4); observer and drift-reporter rotate together; factory-runner's bearer distributes its Actions secrets through the rotation App (`orchestrator-distributed`, amendment 10) |

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
3. **Orchestrator and package changes, shipped and deployed before any rotation uses them**
   (amendment 2 revises this list):
   - claim and reclaim confinement both ways, with the rotation-unit predicate;
   - the operational claim window check;
   - a unit's completion resolving the `work_unit` dependencies that name it;
   - a `/review` form for Devon to resolve a human-act dependency (production `/api` accepts only
     machine bearers, so no human route exists today), added to the `test_scope_guards.py`
     inventories, with the resolution `detail` given a strict schema and the evidence secret scan;
   - the rotation evidence schema and its secret scan;
   - the bearer `previous` parser with its eight-day cap, verified on production;
   - the destination-list profile field (intent-packages schema), the proposer writing it, and
     `/review` showing its difference from the last approved revision (amendment 2 drops the
     proposer change and the `/review` diff);
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
   the drift-reporter bearer. First, repeat increment 2's question 1 probes on production with a
   throwaway application, then create the `Rotation / Coolify` token (amendment 1). The executor
   writes both rows of each variable, restarts, and ends the overlap; this increment names the
   orchestrator read that confirms no dispatched run is live before an orchestrator restart.
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
  inside the VM can still reach them; that risk is accepted and stated. The SYSTEM and VERIFIER
  bearers are outside the executor's reach; from increment 6 a team-wide production Coolify token is
  inside it (amendment 1).
- Until increment 6 creates the Coolify token, every hosted consumer deploy is a human step, which
  is ten of the eighteen live credentials. After it, those deploys and restarts are machine work.
- The orchestrator's claim path and authentication path both change (decisions 1, 7 and 8), and
  each ships with mutation review.
- Every human act is a dependency Devon resolves at `/review`. Until the Coolify token exists and the
  gate graduations, a hosted rotation still has several of them; the design removes the human
  mechanics where a provider has an API, not the human decisions.
- Direct secret grants, not projects, keep a consumer account from reading every rotated
  credential. A new provider or credential needs no new project (amendment 6).
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
| B (2nd) | The registry fingerprint had no author and didn't catch an earlier hostile edit | Decision 1: full destination list in the revision, revision checked against git; amendment 2 moves the diff to the package checkout and binds the list into the unit's authority, shown at `/review` |
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
| 2 | Can an OpenRouter management key carry an expiry? Does key-info return the revoke id? | **Expiry: the console offers one** (set to one hour; two hours later the console shows the key as Expired, an observation, not a probe). **A minted key accepts `expires_at`** as `…Z` (stored and returned) and rejects `…+00:00` with 400. **Revoke id: no.** `GET /api/v1/key` returns 23 fields, `label` among them, and no `hash`. The hash equals sha256 of the key value (undocumented; two keys). `/api/v1/auth/key` answers identically to `/api/v1/key`. | `POST /api/v1/keys` with `limit: 0.01`, then key-info with the minted value. A malformed key gets 401 `User not found.` | Decision 1 and T8 (revoke id); confirms decision 4's `expires_at` and `limit`; increment 5 |
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

**Devon's answer (2026-10-09): option 2.** "I agree with Option 2 as the best option." The edits
listed in the preceding paragraph are made in the same change.

## Amendment 2 — Increment 3 code read (2026-10-09)

Before writing increment 3's build handoff, the orchestrator and intent-packages code its items
touch was read. Three of the ADR's assumptions don't hold, and each needed a decision. An
independent review of the first draft of this amendment confirmed every factual claim and found two
design defects (where the destination list is authenticated, and how a lapsed rotation claim is
recovered) plus gaps; the following sections include the fixes.

### What the code shows

- **Approval happens before `/review`, and it isn't authenticated.** A rotation package revision is
  approved by name in the intent-packages checkout, and that commit is published before the
  orchestrator sees it (`docs/operations/rotation-proposer.md`, step 3). The approval is a line in
  `lineage.yaml` written by the CLI's `approve`; nothing authenticates it, and intent-packages'
  `main` requires status checks but no review. Decision 1's "`/review` shows the destination list's
  difference from the last approved revision" has no approval screen to appear on. The rotation
  proposer rewrites only the `occurrence` line of a standing package and its status
  (`src/rotation_proposer/standing.py`), so every other field is carried forward unchanged.
- **Every rotation unit ends at a human click.** Rotation criteria are `human_review`,
  `external_attestation` or `observation`, all judgment types (`verifier_evaluators.py`), so a unit
  stops in `AWAITING_REVIEW`, and `AWAITING_REVIEW -> COMPLETED` is a separate human gate. Decision
  3's "without it every hand-off between units is another human act" overclaimed: with completion
  resolving dependencies, each hand-off is still the human's completion click, though no longer a
  separate dependency resolution as well.
- **Reach is declared per package, not per unit.** `reach_from_snapshot()` reads the revision's
  enforcement snapshot. `rotation-openrouter-generic` declares `external_system` and
  `operator_machine`, and `operator_machine` has a change window in `factory-policy.toml`, so
  decision 8's "a claim refuses when the unit's package declares a reach whose window is closed"
  would window every unit of that package, including steps that touch nothing hosted.
- **A reclaimed claim can't reach a pulling executor.** The reclaim route is called by SYSTEM, takes
  the new owner from the request body, and returns the new lease token to the SYSTEM caller
  (`api/routes/lifecycle.py`); a replay returns an empty token. The executor pulls and never
  receives that token, so a claim reclaimed to it can't be renewed or have evidence filed. Decision
  9's recovery path doesn't work for the executor as written.

### Devon's answers (2026-10-09)

The three forks were put with the least machinery first. Devon answered "1,1,1".

- **A. The destination list is reviewed in the package diff, and its author writes it.** Devon
  reviews the destination list in the revision's diff in the intent-packages checkout, where he
  already approves by name. The package author writes the list; the proposer doesn't read the
  registry and needs no change, because it carries the field forward. The executor's act-time
  refusal of any registry that differs from the approved list stands. No `/review` code is added
  for this.
- **B. Only the `live_estate` window applies at claim, and only to units that touch a hosted
  service.** Other reach rows' windows aren't asked at claim. Non-hosted steps run at any hour.
- **C. Per-unit completion clicks are accepted for now.** Decision 10 already has Devon click every
  gate on each class's first rotation. A deterministic rotation evidence type, which would let a
  rotation unit complete without him, is part of the later gate-graduation decision; adding a type
  to `DETERMINISTIC_TYPES` changes when judgment evidence reaches a human, so it's his.

### What the answers change

- **Decision 1, destinations.** The package revision carries the destination list as a new
  `non-software-operational` profile field, written by the package author. Before approving, Devon
  reads that field's change against the **last approved** revision (the commit named by the latest
  approval in `lineage.yaml`), not against the proposer's latest commit.
- **The list is also bound into each rotation unit's authority, which is authenticated.** Because
  the intent-packages approval is unauthenticated, an actor that can push there could publish a
  hostile list with a forged approval line and make the registry match. So the operator who writes a
  rotation's decomposition (HQ, by hand, as for every operational decomposition) puts the full
  destination list in each rotation unit's envelope `constraints`. `/review` already shows every
  non-repository constraint on the decomposition proposal and unit pages, so Devon reads the list
  on the screen where his approval is authenticated, with no new `/review` code. Each destination is
  one flat string (consumer kind and destination), and the list is sorted, so the page renders it
  readably. The executor refuses unless the envelope's list, the package revision's list and the registry are
  equal.
- **Destination drift is found before approval, not at act time.** A registry change that adds a
  legitimate consumer would otherwise surface only when the executor refuses at its first claim,
  after a unit has been claimed and ADR-0052's supersede is closed, leaving fail-then-cancel and a
  re-approval. So the approval runbook compares the registry with the list before Devon approves:
  increment 3 adds the step, and increment 4 exposes the executor's own comparison as a read-only
  command for it.
- **T4.** A hostile registry consumer is caught by the executor's refusal, because the approved list
  doesn't contain it. Getting it into the list takes a package edit, which is in the diff Devon reads,
  and an envelope edit, which is on the authenticated `/review` page he approves. **Residual:** the
  diff's baseline comes from `lineage.yaml`, which anyone who can push to intent-packages can
  rewrite, and HQ writes the envelope, so an actor holding both can make the diff empty. The
  remaining defence is Devon reading the whole list on `/review` and recognising every entry; the
  lists are short. That actor is an agent on the Mac, already T1's accepted residual. Showing the
  list's difference from the last completed rotation of the same credential on `/review` would close
  it, and is left to the gate-graduation decision.
- **Decision 8, which window.** At a claim on a rotation unit (decision 1's predicate, not every
  `operational_action` unit), the server asks only the `live_estate` row's window, and asks it
  whatever reach the package declares. It is skipped only for a unit whose approved envelope
  `constraints` mark it as touching no hosted service, so the default is windowed. The marker needs
  no schema change: `constraints` is an open, known envelope field, enters the authority fingerprint
  by value, is write-once and is covered by the authority approval; rotation units are never
  dispatched, so no runner contract sees it. If the policy file can't be read, the claim refuses
  (unlike `lease_policy.py`, which falls back to a default lease). This is a new claim-side function:
  the admission check (`reach_admission.change_window_refusal`) is asked once, at admission, and
  stays as it is. Operational units that aren't rotation units keep today's behavior.
- **The marker must agree with the step.** The server can't see what a step writes, so the executor
  refuses a unit marked non-hosted whose step writes a hosted consumer kind (`coolify-env`,
  `coolify-env-hash`) in the approved destination list.
- **Decision 3, hand-offs.** A unit's completion still resolves the `work_unit` dependencies that
  name it, in the same transaction, which removes the separate resolution act. The completion click
  remains until gates graduate.

### Corrections from the code read

These needed no decision.

- **A lapsed rotation claim is released, not reclaimed to a new owner.** For a rotation unit, the
  SYSTEM recovery path releases the lapsed claim and returns the unit to `READY` with no grant; the
  executor then claims it through `claim_unit`, where confinement and the window apply. This is
  new code: `_perform_reclaim` without the grant (release, `FAILED`, the shared
  `_readiness_eligibility_error`, then `READY`). A unit that fails the eligibility check stays
  `FAILED`, committed, and the error is returned. The path needs its own replay predicate, because
  the existing claim and reclaim replays look for a grant. A reclaim that would grant a rotation
  unit, or grant any unit to the executor's `agent_id`, is refused.
  This replaces decision 9's "reclaimed by the existing SYSTEM recovery path".
- **Claim confinement compares a configured identity.** `claim_unit` compares the authenticated
  WORKER's `agent_id` with a configured executor `agent_id`, both ways. When that setting is unset,
  every claim on a rotation unit is refused.
- **Start and continuation.** A claim on a rotation unit is a continuation, and isn't windowed,
  exactly when the unit's previous claim was released because its lease lapsed (`terminal_reason`
  `lease_expired`) and that claim's holder filed evidence on that attempt (rows `recorded_by` the
  holder, which excludes rows recovered by SYSTEM or a human). That holds however the unit got
  back to `READY`, including through a requeue after a lapse. Every other claim is a start and is
  windowed, including a claim after a worker-reported failure (`work_unit_failed`) and its requeue
  or retry: a failure is a stop condition, and Devon's 2026-10-08 ruling covered resuming a paused
  rotation, not retrying a failed one. Renewal is never
  windowed. A refusal is raised before the transaction commits, so it leaves no partial state.
  CLAUDE.md's rule that a per-claim rule covers all three lease writers is amended in increment 3 to
  name the window as the deliberate exception for renewal.
- **Evidence field names.** Rotation evidence fields must pass `secret_metadata_path`, which refuses
  any key containing one of `SECRET_KEY_PARTS`: `api_key`, `authorization`, `bearer`, `body`,
  `credential`, `instruction`, `log`, `password`, `response`, `secret` and `token`. Names such as
  `credential_id`, `fresh_login`, `token_fingerprint`, `response_status` and `quarantine_secret_id`
  would be refused; the schema uses names that avoid every part. CLAUDE.md's
  `deployment_observation` rule, which names only credential/token/key, is corrected in increment 3
  to point at `SECRET_KEY_PARTS`.
- **The executor needs its own identity and authority profile.** No executor identity exists in
  security-standards' registry, and `security-executor`'s profile prohibits creating and revoking
  credentials, so it can't be reused. Increment 3 adds an agent file and a profile, then rebuilds
  the image so the bundle carries them. The executor's bearer is added to the orchestrator's M2M
  settings with credentials written before roles, when increment 4 creates it.
- **Mutation review is manual.** The repository has no mutation-testing tool. Each guard's
  mutations are made by hand and run against its tests, and the count and survivors are reported.

### Increment 3, as amended

- Claim confinement both ways on `claim_unit` against a configured executor `agent_id`, with the
  rotation-unit predicate; refusal of every rotation claim when the setting is unset.
- Recovery of a lapsed rotation claim by release without a grant; reclaim refuses to grant a
  rotation unit or to grant to the executor.
- The claim-time `live_estate` window for rotation units not marked non-hosted, with the start and
  continuation definition above.
- A unit's completion resolving the `work_unit` dependencies that name it.
- A `/review` form for Devon to resolve a human-act dependency, with a strict `detail` schema and
  the evidence secret scan, added to the route inventories.
- The rotation evidence schema and its secret scan.
- The bearer `previous` parser with its eight-day cap, verified on production.
- The destination-list profile field in intent-packages' schema; the runbook steps for reading its
  change against the last approved revision and for comparing it with the registry before approval.
- The executor's WORKER identity and authority profile (security-standards commit and image
  rebuild).
- The two CLAUDE.md corrections named above.

Each guard is reviewed by mutation, by hand.

## Amendment 5 — Increment 5 decisions (2026-10-12)

Amendments 3 and 4 are proposed in the increment 3 and increment 4 reports and aren't applied;
this one takes the next number. Increment 5's consumer inventory came first, and it changed the
questions put to Devon.

### What the inventory found

`openrouter-generic` had one reader outside BWS: the octo plugin's OpenRouter seat, which reads
`OPENROUTER_API_KEY` from the environment that Claude Code inherits from `~/.zshrc`. The key's own
record showed no use at all (key-info: zero usage, paid, free-model and BYOK). No hosted service
holds it: `brain-app` and `brain-open` have an `OPENROUTER_API_KEY` variable whose values are the
brains' separate key. The full search and its exclusions are in the increment 5 report.

### Devon's answers (2026-10-12)

- **The shell export is dropped with no replacement**, and octo's OpenRouter seat is disabled. So
  the keeper is the credential's only consumer, no person attests a consumer, and the per-class
  section's "fetches it from BWS" needs no Mac identity: nothing on the Mac reads the key.
- **The executor retires today's key by machine.** The per-class row said human for a key the
  executor didn't mint, a rule written when the revoke id had to be pinned at creation. Amendment
  1's derive-then-confirm revoke works for any key on the account, and only a machine revoke files
  the key-info 200 that is the live-before half. The row now says machine.
- **Every minted key carries a $5 credit limit and expires 400 days after minting**: past the
  class's 365-day rotation age, with about five weeks' grace before a stalled rotation lets it
  lapse.
- **The session clicks the `/review` gates in Devon's signed-in browser** for this run, with his
  consent, recorded in the run's report. Decision 10's default is unchanged.

### What the contract gained

- **A provider has two probes.** `probe` asks what a consumer would (for OpenRouter, a zero-cost
  completion on a `:free` model); `probe_before_revoke` asks the endpoint the revoke relies on
  (key-info). Retire-old uses the second, verify-new and confirm-dead the first. The control and
  the subject in each step go through the same probe, so a refused control proves the target the
  subject's answer came from.
- **`revoke` returns a note** for the retire step's evidence. It names what was checked, never a
  label, an id or a value.
- **An adapter is built with the executor's BWS access and its provider's admin secret id**, from
  `config.toml`'s `admin_secrets`. The OpenRouter adapter reads the management key only when a
  step mints or revokes.

### Where the build departs from amendment 1

Amendment 1 names key-info polled until 401 as confirm-dead's fallback. The adapter has no
fallback: when no `:free` model answers a completion determinately, the probe is indeterminate,
the unit fails, and a requeue retries it. A fallback would need a deadline primitive the contract
doesn't have, and the requeue already recovers the same case.

## Amendment 6 — Shared rotation projects with direct secret grants (2026-10-13)

Decision 4 gave each rotated credential its own keeper project and each provider its own admin
project. Before creating the first of them, Devon asked whether every new provider would need its
own projects. It doesn't: Secrets Manager can grant a machine account read, or read and write, on a
single secret, in addition to grants on projects (Devon confirmed both levels in the console). A
secret belongs to one project at a time, so a keeper still moves out of `Ops / Platform`.

### What changes

- **One keeper project, `Rotation / Keepers`.** Only the root has access, with read and write.
  Each consumer account gets a direct **Can read** grant on its own keeper secrets. Review finding
  C's requirement holds: a consumer account reads exactly the credentials it consumes.
- **One admin project, `Rotation / Admin`.** Only the root reads it. A project per provider
  narrowed nothing: the root read every one of them, so a compromised root reaches every admin key
  either way. "Fetch only the one admin secret the unit needs" is the executor's code, not a BWS
  boundary, and it's unchanged.
- **`Rotation / Work`, `Rotation / Executor` and `Rotation / Healthchecks` are unchanged.**

### What a new provider costs

The provider's admin key, created by hand in its console and stored in `Rotation / Admin`; then,
once per credential, moving its keeper into `Rotation / Keepers` and giving each consumer account a
direct read grant on it. No new project and no new project grant.

### What is proven, and what isn't

- **Proven from the VM, 2026-10-13** (fresh login, only `PATH` and `HOME` set): the root lists
  both projects, reads the OpenRouter management key in `Rotation / Admin` (`GET /keys` answers 200
  for it and 401 for a malformed key), and creates, writes, reads back and deletes a canary secret
  in `Rotation / Keepers`.
- **Not yet proven: a direct grant.** `openrouter-generic` has no consumer account, so increment 5
  uses none. The first credential that has one proves, with a canary and an empty `HOME`, that a
  directly granted account reads its secret and can't read a second secret in the same project,
  before its keeper moves.

## Amendment 7 — The exposure-response factors (2026-10-13)

Decision 6 left Devon to name further exposure-response factors before increment 5. On 2026-10-13
he added two, so an exposure rotation's record shows six:

1. Where the value was exposed (the scanner's exposure record).
2. Every consumer, and whether it is hosted.
3. Whether the provider can disable reversibly.
4. Any credential that shares the value.
5. **Any use since the exposure**, as far as the provider reports it (for OpenRouter, key-info's
   usage). Use Devon didn't make means containing a misuse, not preventing one.
6. **Whether the credential carries a spending limit or an expiry.** A capped key bounds what an
   exposure can cost; an unlimited one doesn't.

Automatic disable or revoke stays out (decision 6). Increment 5 is a requested rotation, not an
exposure, so these factors don't apply to its run.

## Amendment 8 — Confirm-dead waits out OpenRouter's inference lag (2026-10-13)

Increment 5's production run deleted the old key at 15:38:33 UTC, and confirm_dead probed it three
seconds later. OpenRouter's completion endpoint still accepted it, so the unit failed, and a requeue
was needed. The key was refused by 15:39:09, beside a malformed key refused throughout. key-info
refused it within about 20 seconds, the reverse of amendment 1's measurement that key-info lags.
The increment 5 report has the timeline.

Devon chose a retry in the adapter (2026-10-13), not a contract change. The OpenRouter adapter
treats a key as known deleted when it deleted the key itself, when the provider holds no key at the
key's derived id, or when key-info already refuses the key before a revoke. The last case covers a
later attempt: it runs in a new process, and with key-info refusing first, retire_old files
"already refused" without revoking. When a known-deleted key still probes live, the adapter probes
it again every 10 seconds until it is refused or two minutes have passed (`REVOKE_LAG`, four times
the measured lag). Any other key is probed once, so verify_new never waits. The contract still
decides: a key still live after the bound fails the step as before.

Amendment 5's statement that the adapter has no fallback still holds for an indeterminate probe
(no `:free` model answering): the recheck stops on any answer but 200. What changes is only that a
deleted key still answering 200 is asked again. A recheck makes confirm_dead take up to two minutes
longer, well inside a rotation claim's lease, and the known-bad control's answer can then be up to
two minutes older than the old key's.

## Amendment 9 — Increment 6 decisions (2026-10-14)

Increment 6's plan (`docs/superpowers/plans/2026-10-14-rotation-increment-6-plan.md`) came from
three read-only recon passes and put four decisions to Devon on 2026-10-14.

### Devon's answers

1. **The operator bearer is the first self-issued bearer rotated.** It has no scheduled reader and
   shares no value. Its reader is `sds-operator`, the BWS account interactive sessions use, so
   moving its keeper to `Rotation / Keepers` gives that account a direct read grant on that one
   secret. Increment 5's handoff said no session identity gets a grant in a `Rotation /` project;
   Devon allowed this one, which amendment 6's per-secret grants keep to the one value that
   account already reads.
2. **The executor reads the credentials setting from the orchestrator**, through
   `GET /api/v1/m2m-identities`, which returns each loaded key id's agent id, hash and optional
   `previous`, and refuses every caller but the configured executor. Devon chose it over a
   hashes-only copy in BWS: a copy goes stale silently when someone edits the variable by hand (for
   example rotating SYSTEM or VERIFIER), and the executor's next write would then make a retired
   bearer valid again. The route also turns amendment 1's blind Coolify write into a checked one:
   after the restart, the executor reads the loaded hashes and confirms its write took effect.
   The route shows what the process loaded, not what Coolify stores, so a hand edit to the
   variable is followed by a restart at once (`docs/operations/credentials.md`); an unrestarted
   hand edit would be overwritten.
3. **The observer and drift-reporter bearers are split** into two values, in one coordinated
   rotation that writes both entries in one setting. After that rotation each rotates alone, and
   decision 6's "rotated together" no longer applies to them.
4. **Devon consented to the session driving `/review` and the change record** for the operator
   bearer's first rotation only. Decision 10's default is unchanged.

### What the plan settles without a decision

- **The executor reaches production Coolify over the tailnet** (`100.112.188.96:8000`), which
  WireGuard encrypts. Production Coolify's API also answers plain HTTP on its public address and
  domain, so a token sent that way crosses the internet unencrypted. That is a separate finding,
  outside this ADR.
- **Evidence carries a fingerprint of the generated setting**, not the setting. Its hashes are
  64-character runs, which rotation evidence refuses. This departs from decision 7's "records it as
  evidence".
- **The check that no dispatched run is live excludes rotation units.** The deploy script's check
  refuses on any unit in a live state, which would include the executor's own claim.

Devon answered the two remaining decisions on 2026-10-16, after the operator bearer's rotation:

5. **The executor verifies a scheduled consumer by reading Healthchecks**, with the
   `Rotation / Healthchecks` key: the consumer counts as switched once its check shows a
   successful run after the new value was stored. This is the verification table's rule as
   written, so it adds no exception, and the reads are built before the observer and
   drift-reporter rotation. A person attesting the consumer was the rejected alternative.
6. **change-manager holds the old bearer in the orchestrator's form**: each bearer's variable
   becomes JSON with an optional `previous` and its `until`, modelled on the orchestrator's
   `ORCHESTRATOR_M2M_CREDENTIALS` entries (decision 7; read the shape from the orchestrator's
   parser, not from this text). A second variable per bearer was the rejected
   alternative. The change ships, deploys and is verified on production before any
   change-manager rotation.

### Proven before the operator bearer's run (2026-10-15)

- **A direct grant**, which amendment 6 left unproven. The root created two canary secrets in
  `Rotation / Keepers`, and Devon gave `sds-operator` **Can read** on one. With a fresh login and
  an empty `HOME`, `sds-operator` read the granted canary at the fingerprint the root wrote, and
  the other answered 404, the same as a random UUID. The keeper then moved, and `sds-operator`
  and the root each read it there at the same fingerprint, which hashes to the
  `orchestrator-operator` entry the running orchestrator loaded. The canaries were deleted.
- **Production Coolify, through the executor's own client**, against a throwaway application
  that was deleted afterwards: the token reads, writes and restarts, and can't read a value
  (`read:sensitive` absent). PATCH changes each `is_preview` row in place, keeping its uuid and
  flags. POST restart applies a changed variable. A JSON value shaped like the credentials
  setting arrives byte-for-byte. A value that stops the new container booting leaves the
  application down, not degraded: Coolify removed the running container and left the new one
  restart-looping, and writing the value back and restarting recovered it
  (`docs/operations/rotation-executor.md`).

### change-manager's overlap, built and verified (2026-10-16)

Decision 6's precondition is met. Each of change-manager's four bearer variables holds one JSON
entry keyed by its scope (`full`, `read`, `propose`, `observe`): the bearer's sha256 hash and an
optional `previous` with its `until`, in `ORCHESTRATOR_M2M_CREDENTIALS`' entry form without
`agent_id`. The rules are the orchestrator's: a previous hash is accepted only before `until`, and
boot refuses one equal to any scope's current hash or an `until` more than eight days ahead. One
difference is deliberate: a variable that doesn't parse grants nothing for its scope rather than
stopping boot, because a failed boot takes the whole application down (the preceding section).
`tests/fixtures/m2m_bearer_setting.json` pins the form in both repositories, and this side
recomputes its verdicts with the orchestrator's parser.

- **The destination kind is unchanged.** Each bearer is `coolify-env-hash` with its scope as the
  `entry`, in change-manager's registry.
- **The executor confirms a write by probe.** change-manager has no route reporting loaded hashes.
  A new bearer answers 401 before the restart and 200 after, and the old one 401 after retire-old.
- **A constraint on the change-manager adapter:** `m2m_setting.build` can't write this form, since
  it requires `agent_id`. Each variable is one entry, so the adapter builds the value from the
  quarantined old value and the new one, without reading the setting.
- **The cutover** ran in three deploys, each safe under an unplanned redeploy. change-manager #115
  read both forms. The variables were rewritten (both rows each) and restarted at 15:27 UTC. #116
  removed the plaintext form after every stored row was read back. Rolling back past #115 needs
  the variables rewritten to plaintext first (change-manager `docs/operations/bearer-settings.md`).


### Decision 5 for the drift-reporter's readers (2026-10-16)

The inventory (#430) found that a successful Healthchecks run doesn't show a drift-reporter reader
worked. All eight readers (infraops' `drift-audit.sh`, security-standards'
`factory-events-nightly.sh`, and six vps-backup jobs) post their observation through a helper that
turns a failed secret fetch or post into a logged warning, and the job still pings success. Three
of them (`sync-gdrive.sh`, `verify-backup.sh`, `hetzner-snapshot.sh`) have no check at all.

Devon chose on 2026-10-16 to verify those eight by reading the orchestrator's observations: a
reader counts as switched once it posts an observation for a run that started after the new value
was stored. Every reader already posts one per run, with its own subject. The observer's eight
readers keep decision 5 as written: each exits non-zero on an empty bearer, so its check receives
`/fail`. Making the drift-reporter's readers fail their checks was the rejected alternative: it
changes three repositories and makes a backup's check fail for a reporting fault.

Neither read tells the old value from the new. The orchestrator accepts both during the overlap
and records neither (`identity/auth.py::_hash_accepted`). Both reads rest on the readers being
`runtime-fetch`: a reader fetches the keeper each run, and the store step has read the keeper
back, so a run that started after the store used the new value.

### How verify-consumers waits for the scheduled readers (2026-10-16)

The reads in decision 5 can't finish inside one pass or one lease: the readers run nightly or
hourly, and `verify-backup.sh` and `hetzner-snapshot.sh` run only on Sundays at 03:00 New York
time. As built before this section, a claimed unit had no "not yet" exit: `retire_old` refused on
any unverified consumer, failing the unit and spending an attempt. A lapsed lease didn't help:
only SYSTEM may release one (decision 9), no schedule does, and each re-claim spends an attempt.

**The worst case passes `until`.** `until` is the host step's instant plus seven days (`OVERLAP`),
and host runs minutes before store. A mint claimed on a Sunday that stores after 03:00 has both
Sunday readers' runs already behind it, so their next runs start the following Sunday at 03:00,
minutes after `until`, and they post only when they finish. A mint that fails after host and
stores on a later night moves the store later while `until` stays put. A mint on Monday through
Saturday leaves every reader at least a day. Reaching `until` doesn't break a reader that fetches
the keeper each run; it means the backstop, not retire-old, ends the overlap.

**Devon chose on 2026-10-16 that weekly readers gate retire.** The rejected alternatives were
keeping mints off Sundays, an eight-day overlap for this rotation (`MAX_OVERLAP` is the ceiling,
and it clears a Sunday mint only if the Sunday jobs post before the window closes), and letting
weekly readers report without gating.

What was built:

- **The wait is before the claim.** A unit that retires the old value stays ready, spending
  nothing, until every scheduled reader has run since the keeper's revision date. Each pass logs
  the readers it waits for and `until`. That log line is the only report before the backstop;
  after it, retire-old files "the old value was already refused". Nothing reports a ready unit
  in the dead-letter view, so a waiting retire is visible in the executor's log and at
  `/review`, not as a finding.
- **Each read is named in the registry**, in parsed fields beside `note`
  (`healthchecks_check`, or `observation_source` with `observation_subject`), outside the
  destination string, so intent-packages' pin is unchanged. infraops' parser stores extra
  consumer fields, proven by `security-drift-cli cred-findings` against a scratch
  `INFRADRIFT_CONFIG_DIR`. These fields merge without review and the three-way destination
  comparison doesn't cover them: a wrong name can make a reader look switched early, never move a
  value.
- **A check's run start is `last_ping - last_duration`.** Healthchecks doesn't document
  `last_duration`; its source (`Check.ping`, at b932fc4) sets it only for a success or failure
  that follows its own `/start` and resets it otherwise, and the read-only key returns it. All
  sixteen readers fetch the keeper after their run starts and keep no copy, confirmed in each
  script and helper on 2026-10-16. A two-minute margin covers the three clocks involved.
- **A drift-reporter reader is refused once it has posted nothing for 15 days.** That holds a
  weekly reader that missed one run; two missed runs means the job is broken, not late. Devon
  chose 15 days on 2026-10-18, over the first build's eight, which a single missed Sunday
  turned from a wait into a refusal.
- **`Rotation / Healthchecks` already held a read-only key** (`Rotation / Healthcheck`) for the
  project with the twenty checks, so no new credential was needed.

### Decision 3 revised: the split is two ordinary rotations (2026-10-18)

Decision 3 said the observer and drift-reporter split "in one coordinated rotation". The executor
can't do that as built: `Rotation` holds one credential, one keeper and one value throughout, and
`m2m_setting.build` changes one entry. Hosting the observer alone with an overlap is refused too.
Its `previous` would be the shared hash, which the drift-reporter's entry still holds as current,
and `identity/auth.py::previous_hash_valid` refuses a previous hash equal to any identity's
current hash, at boot and on every request. A two-entry rotation would rewrite most of
`contract.py` for a single use, because each bearer rotates alone after the split.

Devon chose on 2026-10-18 to rotate the observer first **without an overlap**: its host writes the
new hash and no `previous`, so the shared value stops authenticating as the observer when the
orchestrator restarts. The drift-reporter then rotates on its own, with the usual overlap; by then
the observer's current hash is its new one, so the conflict is gone. The boot rule is unchanged,
and the orchestrator isn't redeployed. The rejected alternatives were building the two-entry
rotation, and relaxing the boot rule for the split and restoring it afterwards (two deploys of
authentication code).

The cost is the overlap's protection, for this one run. An observer lane that runs between the
restart and `store` fails that run and pings `/fail`. If the unit stops after host, the observer's
eight lanes fail until a retry stores the staged value, and the runbook's undo, which restores the
entry from `previous`, has no `previous` to restore from.

#### The recovery after a stop at a host without an overlap (2026-10-18)

The build (#440) answered the open undo. A unit that stops after such a host is finished by a
retry, which is claimed only in the `live_estate` window, finds the entry already written, and goes
on to `store`. Or it's undone by copying a hash, not a value: the entry's `token_hash` is set to
the sharing entry's, which the keeper's old value still matches. The undo is allowed only while
no quarantine secret exists for the occurrence and the sharing entry hasn't rotated since host;
otherwise a retry is the only recovery. Host refuses an entry that still accepts an earlier value
as `previous`, so the old hash is the only one an undo can restore. The procedure is in
`docs/operations/rotation-executor.md`, "If a rotation stalls after host".

The declaration is the mint unit's approved envelope constraint `host_without_overlap: true`, so
it's covered by the authority fingerprint and changes no contract pinned with intent-packages or
factory-runner. Because the two keepers hold one value of one age, both may come due the same
night; the drift-reporter's package is approved, and its registry entry marked `rotated_by_sds`,
only after the observer's mint unit has completed. A drift-reporter host before then would write
the shared hash as `previous` while it is still the observer's current hash, which the executor
refuses as boot would.

#### Proven before the observer's run (2026-10-18)

- **The broad account's direct grant.** The root created two canaries in `Rotation / Keepers`, and
  Devon gave the broad account (the Developer Machines account behind
  `BWS_ACCESS_TOKEN_VPS_BACKUP` and `BWS_ACCESS_TOKEN_INFRA_DRIFT`) **Can read** on one. With a
  fresh login and an empty `HOME`, it read the granted canary at the fingerprint the root wrote;
  the other canary and the operator's keeper answered 404. The canaries were deleted.
- **The moves.** Devon granted each keeper to its one account and moved both to
  `Rotation / Keepers`. Afterwards, with an empty `HOME`, `sds-operator` read the observer's keeper
  and got 404 on the drift-reporter's, and the broad account (through both Keychain names) read the
  drift-reporter's and got 404 on the observer's. The root read both, and each keeper's sha256
  equals the hash its entry loaded in the running orchestrator, with no `previous` on either. A
  portfolio search for both UUIDs and names found no reader outside the registry's rows.

### The change-manager adapter (2026-10-18)

The inventory for change-manager's four bearers (handoff
`2026-10-18-handoff-change-manager-adapter.md`) found every scheduled reader fetches its keeper with
the broad account, the four keepers hold four values, and `GET /api/landing-policy` answers 401 to a
random bearer and 200 to each scope's. It also found four things the plan couldn't settle alone,
and Devon answered them on 2026-10-18:

1. **`drift-audit.sh` has no read that shows it switched.** It reads the `full` bearer for two
   change-manager calls that only log a warning when they fail, so its check pings success either
   way. Devon chose a human act: the `full` bearer rotates with the `change-manager-attested`
   plan, whose person attests that reader at `/review`. The other three bearers use the
   machine-only `change-manager` plan. Changing infraops so the failure reaches the check was the
   rejected alternative.
2. **factory-events held a static copy of the `full` bearer** in `~/.factory/env`, unregistered, so
   a rotation would have broken it at retire. Devon chose to convert it to a fetch from BWS on each
   run, after which it is an ordinary `runtime-fetch` reader with a check. Registering the file as
   a person-attested `shell-export` was the rejected alternative.
3. **Readers run throughout the `live_estate` window**, and the overlap covers the value, not a
   restart's outage. Devon chose that the restart waits: a unit that writes waits unclaimed while
   any registered reader of any change-manager bearer shows as started in Healthchecks, and host
   and retire ask again before writing and before restarting. Accepting one failed run per restart
   was the rejected alternative.
4. **change-manager doesn't boot with a `previous` equal to any scope's current hash**, and has no
   route reporting its hashes. Devon chose that host and retire read the other bearers' keepers and
   refuse a value one of them holds, so all four keepers move to `Rotation / Keepers` before the
   first change-manager rotation. Relying on the inventory's one-time check was the rejected
   alternative. A hand edit in Coolify that no keeper holds stays invisible.

### The observer's first mint and an expired tailnet key (2026-10-19)

The observer bearer's mint unit was claimed at 02:00 on 2026-10-19 and failed at host after mint
and stage: `Coolify PATCH got no answer: ConnectTimeout`. The VPS's Tailscale node,
`ubuntu-2gb-ash-1`, had reached its key expiry at 16:40 on 2026-10-18 and logged out, so the
executor's tailnet address for Coolify answered nothing. The write never connected: the keeper
and the orchestrator's loaded hash still held the old value, and every reader kept working.
Devon logged the node back in and disabled its key expiry. HQ requeued the unit as SYSTEM, since
it had used one of its three attempts, for the next `live_estate` window.

### The observer's second mint, a BWS rate limit and a re-enabled health check (2026-10-20)

The requeued observer mint ran at 02:07 ET on 2026-10-20, straight after change-manager's observe
bearer completed its own six steps. Mint, stage and host succeeded, the host without an overlap
(amendment 9), and `verify_new` proved the new value live. `store` then failed on BWS's
`429 Too Many Requests`, before it created the quarantine secret, so the keeper still held the old
value while the orchestrator accepted only the new one as `orchestrator-observer`. Seven of the
observer's eight scheduled readers failed from then until the undo loaded, about seven hours later.

HQ undid the host by the runbook ("If a rotation stalls after host"): the observer's entry took
the drift-reporter entry's hash. The first two restarts didn't take effect. At 04:00 ET infraops'
change window had applied drift rule #570 to the orchestrator, enabling Coolify's health check at
its defaults (port 80, path `/`), which the image can't answer. Coolify judged each new container
unhealthy and kept the old one, and the window reported the change done because the old
container stayed healthy. HQ disabled the health check again, as the orchestrator's invariants
require, and the third restart loaded the undo. The unit used its second attempt.

The executor's BWS store retries a 429 or a 5xx for about three minutes before a step fails, and a
`create` or `delete` retry first checks whether BWS already did it, so a 5xx that came late
doesn't make a second secret or fail retire's deletion of the quarantine.

## Amendment 10 — Increment 7 decisions (2026-10-19)

Increment 7's handoff (`docs/superpowers/plans/2026-10-19-handoff-increment-7-github-atlassian.md`)
inventoried the GitHub PATs, the Atlassian token and factory-runner's bearer, and put its open
decisions to Devon. The build plan is `docs/superpowers/plans/2026-10-19-increment-7-build-plan.md`.

### What the inventory found

- **Four live keepers hold four values.** `factory-pr-token` (in `SDS Operator`) and the
  factory-runner bearer, the mirror PAT and the Bitbucket token (all three in `Ops / Platform`).
  No keeper of the four has a scheduled BWS reader.
- **The two classic PATs were already revoked**, by infraops' legacy executor on 2026-07-02, which
  recorded "revoke confirmed dead (401)" for both. Their keepers answer 404 to both Mac accounts,
  which in the same run read three other secrets in the same project, so the keepers are deleted.
- **The factory-runner bearer's entry is `factory-runner-github`**, read through the executor's own
  client from the identities route; its loaded hash equals the keeper's sha256 and no other entry
  shares it.
- **Copies the registry didn't list:** a plaintext `FACTORY_PR_TOKEN` in `~/.portfolio/credentials.env`,
  read nightly by project-standards' portfolio scan; and `FACTORY_RUNNER_CREDENTIAL_KEY_ID` beside
  each `FACTORY_RUNNER_TOKEN`, which is the entry's key id and isn't a secret.
- **Copies with no reader:** project-standards holds `FACTORY_PR_TOKEN`, `FACTORY_RUNNER_TOKEN` and
  the key id but isn't a factory target; factory-runner's own `FACTORY_PR_TOKEN` has never run. The
  factory-runner bearer's `runtime-fetch` row named code on an unmerged branch with no schedule.

### Devon's answers (2026-10-19)

1. **Actions-secret copies are verified by machine.** Devon rejected human attestation: "What we are
   not going to do is automate this work via a human doing the tasks." Each holding repository runs
   `.github/workflows/secret-fingerprint.yml` hourly, with no permissions, printing each secret's
   sha256 prefix and length. The executor reads the latest successful run's job log with a token
   minted `{actions: read, metadata: read}` and counts a copy switched when the report matches the
   keeper. The rejected alternative was the executor dispatching the probe, which needs
   `actions: write` over every workflow in those repositories.
2. **The classic PATs stay as they are.** No revoke-only path; their registry entries stay as the
   record infraops reads.
3. **The mirror fetches both tokens from BWS each cycle** and pings a Healthchecks check, so its
   Coolify copies go away and it is an ordinary scheduled `runtime-fetch` reader. It reads them
   with its own BWS machine account, directly granted the two keepers. The rejected alternative,
   the executor writing the mirror's Coolify env, would have had to count the mirror switched from
   its restart, which the executor has no trusted record of.
4. **A person mints a PAT or an Atlassian token in the console** and pastes it into the rotation's
   staging secret in `Rotation / Work`, then resolves the human act at `/review`.
   `factory-pr-token`'s two `rotation_preconditions` are met at that mint: a new PAT (not a
   regenerated one, which would kill the old value at once) with the factory targets and
   factory-runner in its repository list and the current PAT's permissions.
5. **The App's private key lives in `Rotation / Admin`**, correcting the asset row's
   `Rotation / GitHub`, which the root has no grant on.
6. **The portfolio scan fetches `FACTORY_PR_TOKEN` from BWS each run** through `sds-operator`, with a
   Healthchecks check that fails when the fetch does. This reverses project-standards' recorded
   choice to stay out of BWS, knowingly.
7. **The copies with no reader are deleted**, with their registry rows.
8. **The factory-runner bearer's `runtime-fetch` row is dropped.**

### Measured before the build (2026-10-19)

- **`POST /credentials/revoke`** with a throwaway fine-grained PAT, from the VM: the throwaway
  answered `GET /user` 200 and a malformed control 401; the revoke, sent with no credential,
  answered 202 with an empty body; the throwaway answered 401 within six seconds and still at +30 s.
  GitHub's documentation says the endpoint refuses an authenticated request with 403 and doesn't
  report what it revoked, so death is shown by the probe alone.
- **Question 6, repeated against the real App** (`alobar-sds-rotation`, installation `173083952`,
  selected repositories only: brain, change-manager, infraops-mcp-server, intent-packages,
  orchestrator, security-standards). A mint for orchestrator with `{secrets: write, metadata: read}`
  answered 201 with exactly those permissions and that one repository; a mint asking
  `actions: write`, or for a repository outside the installation, answered 422. A token minted with
  `{metadata: read}` got 403 `Resource not accessible by integration` on the write; the
  `secrets: write` token wrote a throwaway secret (201, `updated_at` set) and deleted it (204, then
  404). The token is longer than 100 characters, as GitHub announced.
- **GitHub answers a job's log with a 302** to a signed URL on
  `productionresultssa<n>.blob.core.windows.net`, so the report read follows that one hop with a
  client bounded to that host and carrying no credential.

### What the contract gained

- **`_distribute` writes Actions secrets** through a writer the unit run is given, one repository
  and secret name per approved destination. `gh-actions-secret` leaves `ATTESTED_KINDS`: it is
  verified by its report, read like a scheduled reader, never attested.
- **A plan that has Actions-secret destinations must have a distribute step**, or the unit is
  refused before its claim.
- **The wait before the claim also gates a person's revoke**: a unit straight before a human
  retire-old act waits unclaimed, as a retiring unit does, until every reader has switched. Such a
  plan is refused unless every consumer is machine-verified, and only a human act that verifies
  consumers counts as attesting a reader.
- **A PAT's retire waits unclaimed while a dispatched run is live**, the way change-manager's
  restarts wait for its readers: a revoke would fail that run's push.
- **One new dependency, PyNaCl**, locked by hash in `uv.lock`. The App's login uses `pyjwt[crypto]`,
  which the orchestrator already had.

### The per-class rows, as amended

| Class | Mint | Deploy | Retire-old |
|---|---|---|---|
| GitHub PAT (fine-grained) | human (console, then the staging secret) | machine: BWS and Actions secrets through the rotation App; no Coolify copy after the mirror's cutover | machine, by value at `POST /credentials/revoke`, then confirm-dead rechecks until 401 |
| Atlassian API token | human | machine: BWS; no Coolify copy after the mirror's cutover | human (console), gated by the `verify` unit; confirm-dead machine |
| orchestrator bearer | machine | machine through the Coolify token, and the factory-runner bearer's Actions secrets through the rotation App (`orchestrator-distributed`) | ends the overlap |

The verification table's `gh-actions-secret` row is no longer an exception to R15: a copy is
verified by its repository's fingerprint report.

## Amendment 11 — A rotation unit's authority approval graduates to a declared pattern (2026-10-21)

**Status: accepted by Devon on 2026-10-21** ("amendment accepted"). It takes effect when the
schema 8 release that implements it is deployed.

On 2026-10-20 Devon approved seven rotation units' authority envelopes and called it "that bit of
theater". The six rotations that finished on 2026-10-20 and 2026-10-21 had thirteen units, and
each needed an approval that re-attested an envelope he had already approved. Threat T4 left this
to "the gate-graduation decision". This amendment is that decision.

### What enforces the approval today

A unit's readiness reports `authority_not_approved` until a human approval bound to its authority
fingerprint is recorded (`kernel/readiness.py`). There is already one exit: ADR-0011's known-good
patterns. When every reach member's row in `factory-policy.toml` declares a pattern that
recognises the envelope completely, `authority_gate.human_authority_gate` returns no refusals and
readiness sets `authority_recognised_by_policy`.

That exit can't recognise a rotation envelope, and no edit to the policy file alone can change
that. The schema 7 pattern describes one envelope shape, a command list run against one
repository, and `_recognises` rejects a rotation envelope four ways:

- `change_class` is `None`, and a pattern names a string.
- `conformance` is `None`, and `_conformance_recognised` requires a complete green claim.
- `max_llm_calls` is `None`, which `_within` reads as unbounded.
- The constraint keys are `destinations`, `rotation_adapter`, `rotation_unit` and `work_unit_id`,
  sometimes with `touches_no_hosted_service` or `host_without_overlap`, and
  `_constraints_recognised` requires exactly `allowed_commands`, `mutation_commands`,
  `target_repository` and `work_unit_id`.

`factory_policy.py` says so at `_CONSTRAINT_FIELDS`: a unit that runs no commands "is a different
schema version, not a looser check here". So graduation is a schema bump with the code that
reads it, which is a release.

### Why the per-unit approval adds nothing for a rotation unit

1. **The decomposition approval shows the same bytes.** `decomposition_proposal.html` renders
   every unit's full envelope: capabilities, budgets, change class, conformance, every constraint
   and value, unknown fields and the authority fingerprint. `work_unit_id` is stamped when the
   proposal is submitted (ADR-0001 decision 3), and envelopes are write-once
   (`tests/architecture/test_authority_write_once.py`). The unit's approval page shows the
   fingerprint the decomposition approver already saw.
2. **Destinations are held equal by machine.** Before claiming, the executor refuses a unit
   unless the registry's destinations (read at its repository's `main`), the approved package
   revision's (verified against intent-packages' `main` by content hash) and the envelope's are
   exactly equal (`rotation_worker/checks.py`). An envelope can't name a destination that the
   package Devon approved doesn't.
3. **`touches_no_hosted_service` is checked by machine.** The same checks refuse a unit carrying
   it that writes a hosted destination, so a false marker can't move a hosted write out of the
   `live_estate` window.
4. **`host_without_overlap` is checked by machine, but only partly.** The executor requires the
   unit to have a host step, and `m2m_setting.build` refuses unless another served entry holds
   the old hash as current. The flag stays the one place where a person declares that a host
   takes no overlap, and it's rare: one unit in thirteen.

T4's remaining defence was Devon reading the destination list on `/review`. Under this amendment
that read happens once, on the decomposition page, which shows the same list.

### Decision proposed

1. **Policy schema 8 adds a rotation pattern.** It's a second kind of `known_good` entry, and each
   of its fields only narrows:
   - `rotation_adapters`: the adapter names it recognises;
   - `rotation_units`: the unit names it recognises;
   - `capabilities`: at most `{ operational_action = "allowed" }`;
   - `max_attempts`: a ceiling of 3.

   It recognises an envelope only when all of the following hold:
   - the unit is a rotation unit (`is_rotation_unit`, read from the unit and its package
     revision, never from the envelope);
   - `unknown_fields` is empty;
   - the constraint keys are exactly `destinations`, `rotation_adapter`, `rotation_unit` and
     `work_unit_id`, optionally with `touches_no_hosted_service = true`;
   - `work_unit_id` is the unit's own id.

   Three `None` values are the recognised shape here, not unbounded or missing values:
   `change_class`, `conformance` and `max_llm_calls`. The executor makes no model calls, and a
   rotation unit is never dispatched.
2. **`host_without_overlap` is never recognised.** A mint unit declaring it keeps the per-unit
   approval.
3. **The pattern is declared under `external_system` and `live_estate`.** Every rotation package
   declares `external_system`, and the five bearer packages also declare `live_estate`.
   Recognition needs a pattern under every member.
4. **The lifted requirement leaves a trace at the claim.** Today `authority.human_gate_not_required`
   is written only at dispatch (`dispatch.py`), and rotation units are never dispatched. The
   rotation claim writes the same event, attributed to the claiming executor, with the
   recognising patterns, the policy version and source, and the authority fingerprint. "The gate didn't fire" and "a human
   approved" stay different records, and `authority_approval_id` stays null.
5. **Nothing else changes.** The package revision approval, the decomposition approval, the
   human-act dependencies (mint and revoke), adjudication and the `AWAITING_REVIEW → COMPLETED`
   gate are untouched.

The 21 envelopes of this shape that had cleared the gate by 2026-10-21 are evidence about the shape only
(ADR-0014). They aren't the justification; the four machine checks are.

### Alternatives

- **Keep the click.** It costs one approval per unit, three or four per rotation, and the
  every later rotation needs it too.
- **Count the decomposition approval as each unit's authority approval.** It's a real human
  approval of the same bytes, but it would add a second way to write an authority approval,
  beside the one ADR-0011 already gives policy. Rejected in favour of reusing the pattern route.

### Cost

One increment:
- schema 8 in `factory_policy.py`, with its loader, matcher and `SUPPORTED_SCHEMA_VERSIONS`;
- the two pattern rows;
- the claim-side trace;
- tests that the pattern refuses each excluded shape (`host_without_overlap`, an extra
  constraint, a second capability, an unknown field);
- a deploy.

Rotation envelopes take their budgets from the decomposition author, not from an intent-packages
profile, so `check_profile_budget_agreement.py` has nothing to hold the ceiling to. The enforced
attempt cap stays the `work_units.max_attempts` column.

## Amendment 12 — BWS machine tokens rotate through a second executor on the Mac (proposed 2026-10-21)

**Status: proposed, awaiting Devon's decision.** Nothing in this amendment is in force until he
accepts it.

Increment 8 and the per-class table said every step of a BWS machine token's rotation is human,
and the executor only records the evidence. On 2026-10-21 Devon rejected that: "SDS can not do an
all human rotation." This amendment replaces it. Two steps stay human, because nothing else can
do them, and a second instance of the executor on the Mac does the rest.

### What forces two human steps

- **Bitwarden offers no machine path to create or revoke an access token.** Its documentation
  describes both only in the web app ([Access tokens](https://bitwarden.com/help/access-tokens/)).
  A search found no API for either, and `bws` 2.0.0's commands cover only projects and secrets
  (checked 2026-10-21).
- That's the Atlassian token's position (amendment 10): a person creates the token, and a person
  revokes the old one. The contract already supports both as human acts.

### Why the VM executor doesn't do the rest

Decision 4 keeps live BWS consumer tokens out of the `rotation-executor` VM's reach. The
consumers are also Keychain items on the Mac, which the VM can't reach. The original increment 8
read those two facts as "a person does everything". The real limit is only "not from the VM": the
tokens an executor on the Mac would handle are already in the Mac's Keychain, so decision 4's
reason doesn't apply to them. The Mac does gain one new asset, the instance's own orchestrator
identity, which decision 7 below accounts for.

### Decision proposed

1. **The plan is the Atlassian token's shape.** A human act creates the token in the web app and
   puts it in a staging Keychain item through a hidden prompt; a machine unit proves it and
   switches every consumer; a human act revokes the old token; a machine unit proves it dead.
   - **Proving the new value:** a fresh login, with a new SDK client and an empty throwaway
     `HOME` (amendment 1). Amendment 1 measured fresh logins through the CLI and the executor's
     code path; increment 8's measurement repeats it for the Mac instance's own client before
     anything depends on it.
   - **The switch:** write the new value into every Keychain item that held the old value,
     keeping the old value in a quarantine Keychain item until confirm-dead. Which step kinds
     carry this is the build's to fit to `contract.py`.
   - **Consumer verification:** every registered Keychain item hashes to the new fingerprint; each
     long-running holder has restarted after the switch, proved by its process start time; and
     each scheduled reader has run since the switch, read from its Healthchecks check as today.
     Because every item already holds the new value, a reader's run after the switch is a
     discriminating read, which a green check alone was not.
   - **Confirm-dead:** the pair amendment 1 defines. Before the revoke, the old value logs in
     fresh; after it, the same fingerprint fails with `invalid_client` while the new value logs
     in. Then the quarantine and staging items are deleted.
   - **The exposure window:** the unit records the revoke instant, and evidence that the window
     closed is filed no earlier than 75 minutes after it (amendment 1).
2. **A second executor instance runs on the Mac**, from the same code at the same revision. Every
   adapter step still runs through `contract.py`. It claims only units of the BWS-token adapter,
   and the VM instance never claims them.
3. **It's a second orchestrator identity.** The orchestrator lets exactly one identity
   (`rotation_executor_agent_id`) claim rotation units, so it learns a second identity confined
   to the BWS-token adapter's units, and confinement holds both ways for each identity. The
   identity needs a security-standards registry entry and an orchestrator release.
4. **Its state stays off BWS.** The VM instance's own bearer and staging live in BWS projects
   under a root token. The Mac instance's live in Keychain items: its orchestrator bearer, the
   staging item, the quarantine item and its own copy of the read-only Healthchecks key, which the
   VM instance reads from `Rotation / Healthchecks`. It reads no BWS project; its only BWS calls
   are the fresh logins with the tokens it rotates.
5. **The keeper is a Keychain item.** A machine token has no BWS copy of itself, so this class's
   registry entry names one Keychain item as the keeper. The checks that require the keeper among
   the destinations, and that refuse a person-revoked plan whose Keychain destinations have no
   scheduled read, learn this class.
6. **Everything else is unchanged.** Each token is a standing package in intent-packages, marked
   `rotated_by_sds` and proposed like every other class, with the same human gates and evidence
   schema.
7. **The threat model gains one identity on the Mac.** The VM exists so an agent session on the
   Mac can't act as the executor (decision 2, T1). After this amendment, a session that reads the
   Mac instance's Keychain bearer can claim BWS-token units and file their evidence, and under
   amendment 11 that claim needs no per-unit approval. T1's accepted residual grows by that
   identity, and the confinement bounds it: the identity claims only this adapter's units, and
   the most it can do is write Keychain items the session could already read and file evidence.
   It can't create or revoke a token, because only the web app can.

### Open before building

- **The Mac instance's bearer.** The VM's is minted by `rotation-worker mint-bearer` into BWS;
  the Mac's needs a Keychain path, and its orchestrator entry follows the rule that
  `ORCHESTRATOR_M2M_CREDENTIALS` is written before `ORCHESTRATOR_M2M_ROLES`.
- **`bws-cred-rotation-token`:** proposed not to rotate; revoke it once increment 9 retires its
  only reader.
- **Holders no machine can restart.** The infraops MCP server reads its token once, when a Claude
  Code session starts it, so sessions open across a switch keep the old token until they end.
- **The two unidentified tokens.** The github-to-bitbucket mirror and the `facelesstt-admin`
  OrbStack application hold BWS tokens from Coolify. If one of them is a rotated value, it is a
  hosted destination: its write goes through Coolify, which only the VM instance reaches.
- **`BRAIN_OPS` has no reader**, and `CLAUDE_CODING` and `LISTING_PREP` are unregistered.
- **Switch timing:** no switch while another rotation is between `store` and `retire_old`, since
  the broad token starts the readers those rotations verify, and none between 02:00 and 07:35,
  when the scheduled lanes run.

### What this changes

- **Per-class table:** the BWS machine token's Mint and Retire-old become "human (web app only)",
  and Deploy becomes "machine, from the Mac instance".
- **Increment 8** is replaced by: the BWS-token adapter and the Mac instance; the orchestrator's
  second identity (a release); the registry and package changes; then each token's first
  rotation. The 2026-10-20 increment 8 handoff is superseded except its inventory and
  value-handling rules.
- **Decision 4** gains one sentence: a BWS consumer token may be handled by an executor instance
  on the machine that already holds it, and never by the VM.
