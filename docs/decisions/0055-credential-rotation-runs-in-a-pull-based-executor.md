# ADR-0055 — Credential rotation runs in a pull-based executor through one contract

- **Status:** Accepted
- **Date:** 2026-10-08
- **Decided by:** Devon, decisions recorded 2026-10-08 in
  `docs/superpowers/specs/2026-10-08-credential-rotation-requirements.md`. Accepted 2026-10-08 ("Yes, I accept ADR-0055").
- **Amended:**
  - 2026-10-09, amendment 1 (increment 2's measurements; the Coolify token decision).
  - 2026-10-09, amendment 2 (increment 3's code read; where destinations are reviewed, which window
    applies at claim, and per-unit completion).
  - Amendments 3 and 4 were proposed in the increment 3 and increment 4 reports (amendment 5 says
    how).
  - 2026-10-12, amendment 5 (increment 5 decisions).
  - 2026-10-13, amendment 6 (shared rotation projects with direct secret grants).
  - 2026-10-13, amendment 7 (the exposure-response factors).
  - 2026-10-13, amendment 8 (confirm-dead waits out OpenRouter's inference lag).
  - 2026-10-14, amendment 9 (increment 6 decisions).
  - 2026-10-19, amendment 10 (increment 7 decisions).
  - Amendment 11 (a rotation unit's authority approval graduates to a declared pattern).
  - Amendment 12 (BWS machine tokens rotate through a second executor on the Mac).
  - Amendment 13 (a credential the orchestrator holds for another service is a destination).
  - 2026-10-26, amendment 14 (a rotation unit's step evidence completes it).
  - 2026-10-27, amendment 15 (one mechanism; a credential type is only its provider).
  - 2026-10-29, amendment 16 (one click per rotation: the revision approval).
  - 2026-10-30, amendment 17 (a type with no overlap; the VPS helper as the executor's reach).
  - 2026-11-01, amendment 18 (the rotation layer's outcomes; the 04:00 window retires).
  - 2026-11-07, amendment 19, accepted (the VM executor issues lane tokens and ping URLs from its
    inbox).
  - 2026-11-07, amendment 20 (a revision re-renders a generated package whose derived plan
    changed).
  - 2026-11-07, amendment 21 (a read-only lookup finds a ping URL's check, beside a random
    one's; cut-over step 4 still proves the full URL).
  - 2026-11-10, amendment 22, accepted (a standing answer approves revision 1 of a lane
    credential's generated package).
  - 2026-11-10, amendment 23, accepted (the executor reads a lane check's ping URL from the
    Healthchecks API; no paste).
  - 2026-11-13, amendment 24, proposed (the factory-events chain of record is a VPS database;
    approvers append, the carrier verifies there).
  - 2026-11-13, amendment 25, proposed (only the work carrier reads the factory-events chain, and
    its project and accounts are named).
  - 2026-11-14, amendment 26, proposed (each factory-events secret holds only its role's
    password).
  - 2026-11-14, amendment 27, proposed (one owner act resets the factory-events chain before an
    FE-5 import retry).
  - 2026-11-14, amendment 28, proposed (no FE-5 import retry after a successful import).
  - 2026-11-18, amendment 29, proposed (the rotation executor runs on the VPS host, and a lane
    token's revoke waits on the loaded fingerprint).
  - 2026-11-20, amendment 30, proposed (the lane secrets broker: a lane reads its secrets over a
    socket and holds no BWS token).
  - 2026-11-23, amendment 31, proposed (the executor's root paths stay root for development:
    D-73 B and D-74 A; the `loaded` proof as built).
  - 2026-11-24, amendment 32, proposed (the lane secrets broker's Phase C: the 12 earlier lanes'
    route, grants and batches, and the one revoke sitting; its open items are Devon's).
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

## Amendment 12 — BWS machine tokens rotate through a second executor on the Mac (2026-10-21)

**Status: accepted by Devon on 2026-10-21** ("amendment 12 accepted"), including decision 7's
threat-model change. It takes effect as increment 8 builds it.

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
- **`bws-cred-rotation-token`:** not rotated. Increment 9 retired its only reader, and it holds no
  project access (amendment 18).
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

### As built: switch timing (2026-10-23)

Both halves of "Switch timing" hold the BWS-token plan's `store` unit unclaimed. The 02:00 to 07:35
window was built with the adapter. The other half is `overlaps.py`: no `store` while another
rotation's listed units show it has stored and not finished. A rotation counts as stored once its
unit holding `store` has been claimed, because the orchestrator and change-manager plans store
inside their first unit, and it stops counting when its last unit completes or is cancelled. That
is later than `retire_old` for a plan with a `confirm` unit.

The wait is one-way. The VM instance doesn't ask whether a BWS `store` unit is running, so it can
claim a unit that stores while the Keychain instance is between its read of the ledger and the end
of its `store` unit. A hosted unit can't, because it claims only between 02:00 and 06:00, when no
BWS `store` unit is claimed. A rotation that touches no hosted service can, such as an OpenRouter or
GitHub PAT rotation.

### Holders no machine can restart (2026-10-23)

The infraops MCP server is the one long-running holder left among the remaining BWS tokens: it reads
`ops-mini`'s Keychain item once, when a Claude Code session starts it. VideoCreator's launch agent,
the other registered holder, is disabled. Devon chose a person's attestation: before revoking the
old token, the person ends every Claude Code session started before the switch, and resolving the
revoke act at `/review` attests it. The executor gains nothing for this. The rejected alternative
was a machine read that waits until every MCP server process started after the switch, which could
wait days while one session stays open.

## Amendment 13 — A credential the orchestrator holds for another service is a destination (2026-10-22)

**Status: accepted by Devon on 2026-10-22** ("amend #13 approved"), after choosing this direction
("1", answering option 1 of two). It takes effect as it is built.

### What happened

The orchestrator calls change-manager with change-manager's read bearer, which it holds in its
own Coolify setting `ORCHESTRATOR_CHANGE_RECORD_TOKEN`. The registry didn't list that copy. The
read bearer's rotation (2026-10-20 to 2026-10-21, units `590670f9` and `9bda584e`) wrote every
registered destination, proved the old value (`4cffba19`) dead, and left the orchestrator holding
it. From the retire on, change-manager refused the orchestrator: the inert landing lane held every
pull request with `inert_landing_policy_source_unreadable`, and every change-record read through
that setting failed. A session working on fork-sync found it on 2026-10-22. HQ wrote the current
read bearer into both rows of the setting and restarted the orchestrator, after which
change-manager answered it 200.

The rotation did what the contract says. The defect is that no destination kind can name a value
one application holds to call another, so the registry couldn't have listed it, and the closest
kind (`coolify-env`) is verified by probing the provider, which would pass while this copy held
the old value.

### Decision proposed

1. **A new destination kind, `coolify-env-held`,** names a value an application holds in its
   Coolify settings to call another service: `instance`, `resource_type`, `uuid` and `key`, in the
   one destination format (`destinations.py`, pinned with intent-packages by
   `tests/fixtures/rotation_destination_format.json`). It is not a hosted kind: the provider's
   probe proves nothing about it.
2. **Distribute writes it.** The executor writes both rows of the named setting through Coolify
   and restarts the application, under the same rules as the orchestrator adapter's host step: the
   one application and variable the approved destination names, never while a dispatched run is
   live. A plan with a `coolify-env-held` destination needs a distribute step, as a plan with an
   Actions secret does.
3. **Verify-consumers asks the holding application.** A `coolify-env-held` destination counts as
   switched only when the application reports the fingerprint of the value it loaded for that
   setting, and it equals the keeper's. The orchestrator reports it on the executor-only identities
   surface, beside the bearer hashes it already serves. An application with no such report is a
   destination the checks refuse before the claim: nothing verifies it, so nothing may rotate it.
4. **The read bearer's registry entry lists the orchestrator's copy,** and its standing package
   gains the destination by revision. Until this amendment is built, the checks refuse the read
   bearer's next rotation, because the registry's and the package's destinations then differ.
5. **Everything else is unchanged.**

### Open before building

- **Which units carry distribute** for the change-manager adapter, whose plan has none today.

### The audit (2026-10-22)

Devon asked for it before the build. It fingerprinted values in-process and printed names only,
comparing whole values with the old and new fingerprints in the evidence of every rotation so far:
twelve credentials and the canary.

- **Coolify:** 458 production settings and 67 dev settings, applications and services. One setting
  holds a rotated credential's value: the orchestrator's `ORCHESTRATOR_CHANGE_RECORD_TOKEN`, now
  the read bearer's current value. It would have matched the old value before HQ's fix, so it is
  also the scan's control. Every other match is a stored hash in a registered `coolify-env-hash`
  destination, including the drift-reporter's previous hash, valid until its overlap ends.
- **The Mac:** 295 values in `.env` files under `~/Projects`, `~/.claude` and `~/Developer`, and in
  the LaunchAgents' environments. None matches.
- **Not scanned:** Keychain items, which reading could make macOS prompt a person for; Actions
  secrets, which can't be read; and files on the VPS.

So the orchestrator is the only application that needs decision 3's report today, and the
destination kind stays general so the next one is a registry entry.

### As built (2026-10-22)

- **Mint carries distribute.** The adapter `change-manager-distributed` runs change-manager's
  plan with a distribute step after store, AC-001 to AC-010: `mint` is mint, stage, host,
  verify-new, store, distribute and verify-consumers (AC-001 to AC-007); `retire` is
  verify-consumers, retire-old and confirm-dead (AC-008 to AC-010). The `change-manager` and
  `change-manager-attested` plans are unchanged, so a bearer with no held copy keeps its approved
  package.
- **The report is its own route, not the identities surface.** `GET /api/v1/held-credentials`
  answers each held setting's sha256 prefix and length by variable name, only to the configured
  VM executor. The identities surface answers the bearer hashes the orchestrator verifies; a held
  copy is a value it presents, and keeping them apart keeps each route's response one thing. A
  setting the process didn't load is left out.
- **The checks refuse before the claim** a held destination outside the production orchestrator
  application, or whose variable the orchestrator doesn't report. An executor without the
  production Coolify token, URL or orchestrator application leaves the unit unclaimed and logs it:
  that is the executor's configuration, not the unit, so no attempt is spent.
- **Distribute's write follows the orchestrator adapter's restart rules:** it asks whether a
  restart would strand a dispatched run before writing and again before restarting, then waits up
  to ten minutes for the route to report the new fingerprint. A copy that already reports it is
  not written again, so a retried unit restarts nothing.

## Amendment 14 — A rotation unit's step evidence completes it (2026-10-26)

**Status: accepted by Devon on 2026-10-26** ("I agree with your recommendation for graduating the
gate"), with three decisions he made the same day: a separate lane drives the verifier, the
graduation covers every rotation adapter, and consumer coverage is read from the executor's note,
with full coverage required only before a person's revoke. It takes effect when the release that
implements it is deployed and the `rotation-verifier` lane is installed.

On 2026-10-26 the drift-reporter bearer's retire unit waited at `/review` for three adjudications
and a completion click, every one of them a reading of evidence the executor had filed. The
permission classifier refused each attempt to record them on Devon's behalf, and Devon declined to
keep doing the clicks by hand. Amendment 11 left adjudication and the `AWAITING_REVIEW ->
COMPLETED` gate untouched (its decision 5), and amendment 2's answer C left a deterministic
rotation evidence type to "the later gate-graduation decision". This amendment is that decision.

### What enforces the gate today

Every standing rotation package states one `external_attestation` criterion per machine step,
whose evidence is "the <step> step's rotation-step/1 evidence, filed by the rotation executor".
`external_attestation` is a human-floored type (`verifier_evaluators.JUDGMENT_TYPES`), so the
verifier reports each one `judgment_required` and the unit goes to `awaiting_review`. A person then
adjudicates each criterion and records the completion.

### Why the person adds nothing for a clean step

What the person checks is a property of the payload: the step is the one the criterion names, the
fingerprints are of two different values, each probe stands beside a refused known-bad control on
the same target, and the probes answered as the step requires. The executor already refuses to
file a step whose control was accepted or whose probe answered wrongly (`contract.py`), so a filed
row is self-consistent by construction, and a person reading it adds no observation of their own.
Run over all 113 steps the 13 completed rotations filed, the machine's reading passes every one.

### Decision

1. **The verifier decides a rotation step from its evidence.** When a criterion would go to a
   person, the unit is a rotation unit (`is_rotation_unit`), the criterion is
   `external_attestation`, and the current evidence was filed by the executor that owns the unit
   (`RotationExecutors.owner`), the verifier asks `rotation_verification.rotation_step_verdict`.
   A clean verdict is a verifier pass, recorded through the one adjudication path that may assert
   it, and the unit completes by the verifier's existing `SUBMITTED -> COMPLETED` edge, resolving
   the next unit's dependency in the same transaction.
2. **The floor stays human.** `external_attestation` stays in `JUDGMENT_TYPES` and
   `evaluate_criterion` is unchanged, so evidence on any other unit, evidence another identity
   filed, and a row the orchestrator recovered after a lapsed lease all still go to a person.
   Nothing in the rotation branch fails a criterion: what it can't prove, a person decides. A
   decision a person already recorded on the criterion, at `/review` before the verifier ran, is
   never replaced.
3. **Coverage.** verify_consumers passes on any well-formed "n of m" note, and needs n = m, with
   the old value probed live, only when a person revokes next. Partial coverage on a mint or store unit is by design (every one of
   the 13 completed rotations filed some), and `retire_old` refuses any consumer still missing.
4. **A lane drives it.** `rotation-verifier` runs every ten minutes as VERIFIER, reads the status
   ledger's submitted units and any a failed verification left in `verifying`, and runs `/verify` on each whose envelope names a `rotation_unit`. The
   executor never holds the VERIFIER bearer (decision 4).
5. **What stays human:** every human act in a plan (console mints, revokes, attestations), each
   resolved at `/review`; the authority approval of any envelope `rotation_known_good` doesn't
   recognise; and every unit the verifier sends to `awaiting_review`.

### Alternatives

- **A deterministic criterion type.** Packages would declare a new type, which means a vocabulary
  change in both repositories and a re-approved revision of every standing package, for the same
  reading this amendment does in the verifier.
- **"N of N" coverage only.** It would have sent every mint and store unit back to a person.

### Cost

- Trust in a clean step moves from a person's reading to the executor's own report. That report
  was already what the person read, and claim confinement still limits who may file it.
- The criterion's condition text can say more than its step proves (a store step's condition
  names the quarantine). The verifier checks what the step does, not the prose.
- A rotation unit already at `awaiting_review` when this ships isn't revisited; it still needs a
  person.

## Amendment 15 — One rotation mechanism, and a credential type is only its provider (2026-10-27)

**Status: accepted by Devon on 2026-10-27** ("amentment 15 approved"). It takes effect
increment by increment, as each ships. Devon set the direction the same day: "separate this process into
two pieces. One is the rotation mechanism, and the other is necessary customization for a cred
type", with the end state "the human putting a new cred in the exact same temp BWS secret, and
also revoking" the credentials a machine can't revoke. He chose that a person revokes only what no
machine can ("Human revokes only what the machine can't").

By 2026-10-27, SDS rotates 14 of the 25 registered credentials. Each new one still costs a design
session, and the cost isn't in `contract.py`, which already implements every step once. It's in
three places around it.

### Where each credential is still custom

1. **Plans are hand-written per adapter, with named variants.** Nine adapter names cover six
   providers: `change-manager`, `change-manager-attested` and `change-manager-distributed` differ
   only in steps the registry already determines. An Actions-secret destination adds `distribute`,
   and a reader with no read adds a person's attestation. Choosing the variant by hand is how the
   full bearer's first attempt (2026-10-26) ran the wrong adapter and spent an attempt.
2. **Two providers carry the mechanism of hosting.** `change_manager.py` and
   `orchestrator_bearer.py` (about 800 lines together) each write a Coolify variable, restart the
   application and wait for it to load. Only the variable's encoding differs.
3. **Onboarding a credential happens outside the code.** The full bearer took five pull requests
   across four repositories: registry flags, a reader made verifiable, a work-carrier opt-in entry
   (`STANDING_OPERATIONAL_PACKAGES`), a package revision whose occurrence had to match by hand, and
   its approvals.

And a person's part differs per credential: each human-minted value goes into a secret named for
its credential and occurrence (`rotation/<credential>/<occurrence>/staging`), or a Keychain prompt
on the Mac, and each human act is a separate click at `/review`. Nothing tells the person when an
act is due.

### End state

**A person does at most two things for any credential, both outside SDS, and the same two every
time:**

1. **Put the new value in the inbox.** One fixed BWS secret, the same name for every credential
   and every rotation. Only for a type whose provider can't mint by machine.
2. **Revoke the old value in the provider's console.** Only for a type whose provider can't revoke
   by machine.

Everything else is the mechanism's: SDS tells the person when each act is due and which value it
concerns, notices when it's done, and carries on. No `/review` click is part of a rotation.

| Type | Mint | Revoke |
|---|---|---|
| OpenRouter key, change-manager and orchestrator bearers | machine | machine |
| GitHub PAT (fine-grained or classic) | person: inbox | machine |
| Bitbucket token, BWS machine token | person: inbox | person: console |
| Anthropic and OpenAI keys, GitHub App private key | not yet measured | not yet measured |

### Decision proposed

1. **A credential type is a provider and a declaration.** The provider keeps today's methods
   (`mint`, `probe`, `known_bad`, `revoke`) plus one new one, `same_identity(new, old)`, and
   declares which of mint and revoke it does by machine and, for a hosted service, how its setting
   encodes a value. Nothing else in a type is per-credential.
2. **The plan is derived, never chosen.** From the type's declaration and the credential's registry
   row (destinations, readers and their reads), one function builds the plan: human or machine
   mint, `host` for a hosted type, `distribute` for Actions-secret and held destinations, a
   person's attestation only for a reader with no read, human or machine revoke. The adapter names
   reduce to one per type. Approved packages pin their criteria, so a rotation in flight keeps its
   pinned plan, and the derived plan applies from each package's next revision.
3. **Hosting is mechanism.** Writing a Coolify setting, restarting, refusing a shared `previous`,
   and confirming the application loaded the new value move into the contract. A hosted type
   supplies only its encoding.
4. **The inbox replaces per-rotation staging names.** The executor reads the inbox each pass. A
   value there belongs to the one rotation waiting on a person's mint; the executor moves it into
   that rotation's staging secret only after `verify_new` answers live and `same_identity` holds
   against the old value, then empties the inbox. With two rotations waiting on a mint, or a value
   that fails either check, it binds nothing and reports. So the proposer opens at most one
   person-minted rotation at a time.
5. **A person's acts are observed, not clicked.** A human mint is no longer a dependency: the unit
   after it waits, unclaimed, until the inbox is bound (the existing wait before the claim,
   `worker.py::_take`). A human revoke is no longer a dependency either: `confirm` waits until a
   fresh probe of the old value is refused, and type-specific waits (BWS's 75 minutes) count from
   that observation. Until then, a revoke at the wrong time is caught as it is today: `confirm`
   refuses while the old value is live, and `verify` must complete first.
6. **SDS tells the person what to do.** When a rotation waits on a person, it sends an alert-hub
   notification naming the act, the credential, how to find the value in the provider's console
   (a per-type `console_hint` in the registry row), and nothing secret. When the act is observed,
   the notification closes.
7. **Onboarding is one registry row.** A row that names its type and sets `rotated_by_sds` is
   enough: the standing package is generated from it, and the work carrier reads rotation packages
   from the registry instead of `STANDING_OPERATIONAL_PACKAGES`. Approving the generated package
   stays a person's act.

### Open before building

- **The inbox on the Mac.** Amendment 12 decision 4 keeps the Mac instance off BWS. Reading one
  BWS secret needs a BWS identity on the Mac scoped to the inbox's project alone, which changes
  that decision's threat model by one narrow identity. The alternative, a fixed Keychain item
  filled through a prompt, isn't "the exact same BWS secret".
- **`same_identity` per type.** Each type needs a discriminating answer to "is this value for the
  same account and scope as the old one". For BWS machine tokens the obvious read (the projects a
  login can see) has to be measured, because a wrong-account token logs in as well as the right
  one.
- **Observing a revoke without a race.** `confirm` already probes freshly; using that probe as the
  trigger needs the same discrimination check for each human-revoked type.
- **The notification path.** The executor has no outbound route to alert-hub today, and its
  allowlist would gain one.

### Increments

1. Derive the plan; reduce the adapter names (executor only, behind new package revisions).
2. Move hosting into the contract.
3. The inbox, `same_identity`, and observed acts, with the alert-hub notification.
4. Generated packages and registry-driven onboarding, proved by onboarding `bws-tok-ops-mini`
   through it.

Each increment ships on its own, and the system rotates every current credential between them.

### Cost

- One more BWS identity on the Mac (open item 1).
- The plan stops being readable in one adapter file; it's read from the derivation, and each
  package records the plan it was generated with.
- Every standing package needs one re-approved revision to move onto the derived plan.

### As built: increment 1 (2026-10-27)

- `plans.derive` builds a type's plan from `Adapter.capabilities` (machine mint, machine revoke,
  hosted) and the credential's destinations. Run over the 13 credentials SDS rotates, it equals
  the plan the approved package pins for 10. The other 3 (`github-finegrained-mirror` and two BWS
  tokens) were approved with a `distribute` step that writes nothing, which the derivation leaves
  out.
- **The package's criteria choose the plan.** Each criterion's evidence names its step, so the
  executor runs the derived plan when the approved package describes it step for step, and
  otherwise the type's pre-derivation plan when the package describes that one. Anything else is
  refused before the claim. No package needs a new revision for this increment, and no name
  changes in `factory-policy.toml`: the type names were already recognised.
- **A derived plan has no person attesting consumers** (decision 2 said "a person's attestation
  only for a reader with no read"). An attestation would be a third kind of human act, which the
  end state rules out, and the criteria can't show whether a package was approved with one. A
  reader with no read is refused before the claim, as it was for every plan without an attesting
  act, until its registry row names a read.
- `orchestrator-distributed`, `change-manager-distributed` and `change-manager-attested` stay as
  fixed-plan names for packages approved under them, until no package uses them.


### Increment 2 skipped (2026-10-27)

Read side by side, the two hosted types share less than decision 3 assumed. Beyond the encoding,
they differ in how a load is confirmed (the orchestrator reports what it loaded; change-manager can
only be probed, through a container swap), in what a restart waits for (a dispatched run; a
mid-run reader), and in their safety rules (a host with no overlap; a value another bearer holds).
The shared skeleton is about 150 lines, and no remaining credential is a new hosted type. Devon
agreed to skip it ("Skip 2 and move to inc 3"); it comes back if a new hosted type does.

### Increment 3 design (2026-10-27)

**A person's acts are announced, observed, and never clicked.** It ships as three pull requests,
each a working system on its own:

1. **Notifications.** When a unit waits on a person, the wait that holds it sends an alert-hub
   alert (`POST /v1/alerts`, keyed `rotation:<credential>:<occurrence>:<act>`), resent each pass
   (alert-hub dedups by key) and resolved when the act is observed. "Revoke the old value" is sent
   only by the wait before `confirm`, which exists only after `verify` completed, so it can't come
   early. Each executor instance is its own alert-hub sender. A send that fails is logged
   `notify_failed`; it never fails a unit.
2. **The revoke is observed.** `confirm` waits, unclaimed, until a fresh probe of the old value is
   refused beside a refused known-bad control. A type's own wait after a revoke (BWS's 75 minutes,
   amendment 1) counts from the first observed refusal, recorded beside the rotation's staging and
   quarantine; losing the record restarts the count, so a loss only lengthens the wait. A
   decomposition approved with the revoke as a human act still works: the act's resolution is a
   second source for the same instant, and the earlier of the two is never used.
3. **The inbox.** Each instance reads a secret named `rotation-inbox` in its own BWS project
   (Devon, 2026-10-27: "Same name, two projects"). A BWS machine token goes to the Mac instance's
   project, and anything else to the VM's; the notification names the project. Amendment 12's
   decision 4 holds, so no BWS consumer token is ever where the VM can read it. The Mac instance
   gains one BWS identity, scoped to its inbox project alone. Binding:
   - The value belongs to the one rotation waiting on a person's mint. At most one person-minted
     rotation is open across the estate: the proposer refuses to open another, and the executor
     re-checks it from the status ledger, where both instances see every unit's
     `rotation_adapter`.
   - It binds only when `verify_new` answers live beside a refused known-bad control, the new
     value reaches what the old one reaches (`same_identity`), and it matches no other keeper of
     the same type in the store. Measured 2026-10-27: two of the four BWS machine tokens
     (`ops-mini` and `content-mini`) see the same 4 projects and 125 secrets, so access alone
     can't tell them apart, and the keeper comparison is what refuses one pasted for the other.
     GitHub and Bitbucket name the account at `GET /user`.
   - It writes the staging secret, then empties the inbox. A value that fails to bind stays where
     it is, fails nothing and alerts only once it has failed on a later pass too. An inbox value
     that differs from a staging value already written is reported, not acted on.
   - `rotation/<credential>/<occurrence>/staging` and `rotation-worker stage` keep working for
     packages approved with a human mint act.

New decompositions carry no `external_system` dependency for a mint or a revoke.

### As built: increment 3 (2026-10-27)

- **3a, notifications:** both instances announce every pending `external_system` act on their own
  units, read from the status ledger, so today's decompositions are covered too. Each instance is
  its own alert-hub sender (`rotation-executor`, `rotation-executor-keychain`); the path was
  proved end to end from both on 2026-10-27.
- **3b, the observed revoke:** the first sighting of the refusal is kept in the executor's state
  directory, not beside staging and quarantine as the design said: it is not a secret, and a
  store item would need `confirm_dead`'s cleanup changed. A lost record only lengthens the wait.
- **3c, the inbox:** built as designed, with these differences:
  - The executor names the inbox by secret id, not by name in a project, so the Keychain
    instance's inbox identity needs no organization to search in. It still needs the
    organization: `same_identity` for a BWS token lists projects and secrets, and listing takes
    one. The Keychain instance's configuration names it beside the inbox, a non-secret id.
  - An empty inbox holds the word `empty`, since BWS keeps no secret without a value.
  - Bitbucket's `GET /2.0/user` answers 403 to these tokens (measured 2026-10-28), so its
    `same_identity` is both values signing in with the registry's `probe_email`, which an
    Atlassian token for another account fails.
  - The proposer doesn't refuse to open a second person-minted rotation. The executor's own
    count is the guard: with two waiting, nothing binds, and each can be staged by hand.
  - `same_identity` is an optional provider method (`inbox.ConfirmsIdentity`), not a member of
    `Provider`: only the person-minted types implement it, and a type without it binds nothing.


### Increment 4 design (2026-10-28)

Devon, 2026-10-28: the scope as written, with handing over `bws-tok-ops-mini-20260730` as the
proof; generated decompositions are the next increment. It waited for the bridge layer's member
record work (#537, #538), which kept the work carrier's list of standing packages because a package
has no repository to grant.

1. **A registry `class` names its type.** One table (`rotation_worker.types.CLASS_TYPES`)
   maps each class to a type declared in the same module, which the adapters read their capabilities from. A class without an entry is a type the SDS
   doesn't rotate, and nothing is generated for it.
2. **The opt-in is the registry and the member record.** The work carrier carries a standing
   rotation package when its credential's registry row sets `rotated_by_sds` and the member record
   grants `credential_rotation` on the repository holding that row. Both are reviewed records, so
   the list in the carrier's code goes. The membership check already holds every registry
   repository granted.
3. **The proposer writes the registry's destinations.** A revision it writes for a due credential
   carries the registry's destination list, not the previous revision's. An approved revision whose
   destinations differ from the registry is revised again rather than proposed, so drift reaches
   Devon as a revision to approve instead of a unit the executor refuses. (Amendment 20 narrows
   this for a package the proposer generated: when its criteria describe no plan its row derives,
   for example after a destination change adds a `distribute` step, the pass reports that
   credential's `error` and writes nothing, and an operator hand-revises the package onto the
   derived plan.)
4. **The proposer generates a missing package.** A due credential that the registry hands to the
   SDS, with no standing package, gets revision 1 generated from its row and its type's derived
   plan (`plans.derive`): reach, authority and one criterion per machine step follow from the
   type's capabilities and its store, and the prose from one template. `intent-packages`
   validates and writes it with its first lineage entry. Devon approves it by name, as every
   revision. The proposer sees only due credentials, so a newly flagged credential gets its package
   when it first comes due.
5. **Approved wording is never rewritten.** Only a new package is generated; an existing package
   is revised only for its occurrence and destinations. (Amendment 20 narrows this for a package
   the proposer generated, when the plan its row derives changes: the criteria, prose and
   authority are re-rendered, and the person approves them in that revision's one click.)

Measured before building: rendered for each of the 16 standing packages' registry rows, the
generator's criteria describe the derived plan for all 15 rotated types (the canary is no type),
and for the 11 packages already on derived plans they are the approved criteria by
`select_plan`'s reading. The 4 others (three BWS tokens and the mirror PAT) run pinned
pre-derivation plans and are never regenerated.

### As built: increment 4 (2026-10-28)

- **The type declaration moved out of the adapters.** `rotation_worker/types.py` holds each type's
  capabilities and store (`TYPES`) and the registry `class` table (`CLASS_TYPES`); the adapters read
  theirs from it. The proposer imports it, the plan and the registry, never a provider: the
  out-of-process guards refuse a proposer that reaches a provider's network client.
- **A record pins its revision.** Drift never revises a revision a change record already names: its
  rotation is under way or done, and revising would skip `record-rotation` and rotate the
  credential again. The registry's change reaches the next rotation's revision.
- **Reach follows the destinations too.** A type that doesn't host is still `live_estate`, with
  `infra_mutation` allowed, when a destination is a Coolify setting (a held copy).
- **The carrier carries only a type the SDS rotates.** The canary's row sets `rotated_by_sds` but
  its class has no type, so it is never carried, as the list it replaces never named it. An
  unreadable registry holds each rotation record undecided and carries everything else.
- **Each program has its own mirrors** (`git_mirror.default_mirror_root(program)`), so the
  proposer and the carrier don't race the executor's fetches.

### Increment 5 design: generated decompositions (2026-10-29)

After increment 4, a session still wrote each rotation's decomposition by hand once Devon approved
its change record and the carrier registered the intake. Devon, 2026-10-29: generate it; keep the
approval click; propose as the SYSTEM bearer.

1. **A lane of its own proposes it.** `rotation-decomposer` (`src/rotation_decomposer/`), hourly, reads change-manager's `work` records for standing rotation packages, asks the
   orchestrator what work each caused (`GET /change-records/{id}/work`), and for a registered
   revision with no units and no decomposition proposal, proposes one as `orchestrator-system`, the
   identity that already proposed three of them. It never decides one: decomposition approval
   accepts only a human actor (`_require_decision_actor`), and graduating that gate belongs to the
   Admission pass (bridge layer design). It never proposes over a proposal a person rejected or sent
   back.
2. **The decomposition is the package's derived plan.** One unit per machine segment of the plan
   the intake's own criteria describe (`select_plan` over `plans.derive`), named
   `<credential>-<unit>`; each criterion maps to the unit holding its step, by the criterion's
   database id. The envelope is exactly what `rotation_known_good` recognises:
   `operational_action`, three attempts, no change class, conformance or model-call budget, and
   the constraints `rotation_adapter` (the type), `rotation_unit`, `destinations` (the package's)
   and `touches_no_hosted_service` wherever the unit writes no hosted service, by the executor's
   own rule. `host_without_overlap` is never generated: it is a person's declaration. Each unit
   after the first depends on the one before it completing; a person's acts are no dependencies
   (increment 3).
3. **A package on a pre-derivation plan is a finding, not a guess.** The lane imports no provider,
   so it can't read a type's fixed plan; it reports a package whose criteria describe no derived
   plan. The four such packages are revised once onto their derived plans, which Devon approves.

## Amendment 16 — One click per rotation: the revision approval (2026-10-29)

**Status:** accepted (Devon, 2026-10-29: "keep the revision approval, one click every time").

### Context

After amendment 15 a rotation took three approvals: the package revision by name, its `work`
record in change-manager, and its decomposition on `/review`. Only the first carries content: which
credential rotates, why (the occurrence), and where the new value goes (the registry's
destinations, as a diff against the last approval). The record restates that decision, and the
decomposition is derived from the approved package by machine (increment 5).

### Decision

1. **The revision approval is the one click, every time.** No approval policy approves a rotation
   revision; intent-packages refuses the profile, and the rotation proposer never approves.
2. **change-manager approves a rotation's `work` record by policy**, as `work-policy`, inside the
   proposal transaction, as it already does for standing dependency updates. A record conforms when
   it names a standing rotation package (`rotation-<credential>`), intent-packages as its source,
   risk `caution`, change class `credential-rotation` and no target repository; the carrier
   refuses that class on a package whose profile isn't the rotation profile. The safety rests on the carrier: it
   carries a record only when its package revision is approved, the approval's hash verified
   against the tamper-evident chain, and only a named person can approve a rotation revision.
3. **The orchestrator approves a rotation decomposition by policy** when SYSTEM proposes it, inside
   the proposal transaction: the revision is a standing rotation package with a recorded approval,
   every dependency is a unit-on-unit `completed` one, and every created unit is a rotation unit
   whose envelope the rotation pattern recognises (the claim's own gate, `human_authority_gate`,
   evaluated on the created units). Anything else stays proposed for a person. A decomposition
   proposed by a human or a WORKER (a session's, by hand) is never approved by policy. The decision
   is attributed to the proposing SYSTEM actor, with the pattern and the policy version in its
   reason, as `authority.human_gate_not_required` is.
4. **The rotation pattern's basis moves.** It lifted the per-unit authority click because a person
   approved the decomposition page showing every envelope. Its basis is now the named revision
   approval, which fixes the package, the destinations and the criteria the envelopes are derived
   from, and the executor's checks before it acts (registry, package and envelope destinations
   equal; a unit marked as touching no hosted service refused if it writes one).

### Consequences

After the click everything is machine until a provider needs a person: the proposer's next pass
proposes the record, change-manager approves it, the carrier registers it at its next pass, the
decomposer proposes and the orchestrator approves the decomposition within the hour, and the
executor claims within five minutes. Approving a rotation revision by name now starts a production
credential change. A policy-approved decomposition's first unit is ready at once, so the window ADR-0052 leaves
for superseding a decomposition before any unit is claimed is gone for these: a wrong derived
plan is retired by letting its unit fail and cancelling it. Every other kind of work keeps its gates: the policies key on the rotation
profile alone. Graduating decomposition approval in general stays with the Admission pass.

### As built (2026-10-29)

- **Shipped:** orchestrator #549 (deployed `2d1b7ff`) and change-manager #144 (deployed `2896f96`).
  Review changed the decision in three places, each now stated above:
  - change-manager keys a rotation record on its change class, `credential-rotation`, which the
    rotation proposer sends and the carrier holds to the rotation profile. A package id alone
    would have let a dependency-update package named `rotation-…` skip its population check.
  - The orchestrator requires each unit to be recognised only by a rotation pattern, and to have
    no more attempts than its envelope budgets.
  - A policy approval that can't complete is rolled back to a savepoint, so the proposal waits for
    a person instead of being refused.
- **SYSTEM's registration exemption** in `register_approved_unit` is read from the stored
  decomposition rows (SYSTEM both proposed and approved it), never from a caller flag.
- **`bws-sds-operator-token` was handed to the SDS** (#550, Devon). Amendment 12's Keychain
  instance had no Healthchecks key, so it would have refused any BWS-token unit whose readers name
  a check, including `bws-tok-ops-mini`'s. It now holds a copy of the self-hosted project's
  read-only key: a separate project couldn't help, because the VM's rotations read the same
  checks.
- **Read-only checks passed without a rotation** for that token: registry and package
  destinations equal (`compare-destinations`), every reader seen through the copied key with a
  "now" control that answers not yet (`read-consumers`), the decomposer's derived plan (store,
  verify, confirm), and the factory policy recognising each step.
- **One reader showed no run:** `sds-rotation-proposer`. The executor ignores a run Healthchecks
  records as zero seconds, and a hand-run dry pass took 0.64 s. Its scheduled runs take 1.5 to
  6.5 s and count.

## Amendment 17 — A type with no overlap, and the VPS helper as the executor's reach (2026-10-30)

### Context

Postgres keeps one password per role. Once the role takes the new value the old one fails for every
new connection, and the service whose database it is cannot reach it until it restarts with the new
one. Amendment 15's host step assumed the service accepts the new value beside the old, and
`host_without_overlap` (amendment 9) is a per-envelope declaration about a value another identity
shares, which only a person makes. Neither describes a role password. Authentik's Postgres password
(`alobar-id`) is the first credential of the kind. The role lives in a container with no published
port, and nothing the executor held could set a role's password or test a login against that container.

### Decision

1. **A type with no overlap.** `Capabilities.overlap` is true by default and false only for
   `postgres-role` (`rotation_worker/types.py`). It changes no derived step, so every existing plan,
   criterion and generated package is byte-identical (pinned by
   `tests/fixtures/rotation_packages_before_overlap.json`). For an `overlap=False` type the generator
   states the host step as "The hosted service replaces the old value with the new one, and its
   dependents restart; they're unavailable until the restart finishes." and says in the package's risk
   section that the restart can happen at any hour of the change window. The person approving the
   revision is approving that outage. `rotation_known_good` lists `postgres-role` under both reach
   members its package uses (`live_estate`, `external_system`) and still never recognises
   `host_without_overlap`: the no-overlap is the type's, stated in the approved package, and not an
   envelope's declaration.
2. **The helper is the executor's new reach.** `deploy/vps/sds-pg-rotate` runs as root behind an SSH
   forced command, called by the executor through `helper_client.py`. It takes one JSON line and
   answers three verbs: `set` changes a role's password, `probe` logs in over the container's network
   address and answers 200, 401 or null, and `loaded` reports the fingerprint of the environment
   variable each consumer loaded. Targets come from the root-owned `/etc/sds-pg-rotate/targets.toml`,
   never from the request. A password must be 64 lowercase hex characters. No verb returns a password,
   logs one, or puts one in an argument vector, and `set` sends Postgres only a SCRAM-SHA-256
   verifier. The alternatives were worse. A Coolify scheduled task would store the password in a
   command string, which Coolify keeps and displays. Publishing the database on a host port would
   remove the protection the container's network isolation gives it, for every client of that port
   and not only for the rotation.
3. **Host undoes itself.** `/review` sits behind Authentik, the service this type restarts, and nothing
   requeues a failed unit by itself. A host step that failed and left the service down would leave the
   person without the page that recovers it. So any failure in host's own writes runs the same writes
   with the old value and then raises, leaving the keeper unchanged. This covers host only: once host
   finishes, recovery is forward. If the undo fails too, the error names
   both failures and recovery is `rotation-worker pg-restore`.

Devon approved the no-overlap decision and the helper as the executor's reach on 2026-10-30.

### Consequences

- A rotation of this type is an outage by design, lasting the restart, at an hour of the change window
  the executor picks. The first one runs under the one click every rotation has (amendment 16).
- The executor's reach now includes one SSH path to the VPS and the helper's three verbs, and nothing
  else on that host. The helper's targets file is the only place a role, database or consumer is named.
- Registering the credential is an infraops change (a `.cred-consumers.toml` row and a `cred-rotation`
  table entry) made separately from this one; this repository holds the type, the helper, the
  generated package text and the policy recognition.
- The policy's pattern for rotation units carries `postgres-role` and a new `decided` date; its schema
  is unchanged.

### As built (2026-10-30)

Shipped in #556 and deployed as `06d7bbf`, with infraops #155 and #156 registering
`alobar-id-postgres`. Two details moved from the decision. The helper's SSH key lives in `Rotation /
Keepers`, because the root can write that project and only reads `Rotation / Admin`; the VM created
it and stored it directly, so it never reached a clipboard. The registry row landed in two steps,
because `adopt-keeper` reads the row and the row needs the keeper's id.

The first rotation ran on 2026-10-30 after Devon approved revision 1 (intent-packages #160). The
record and the decomposition were approved by policy. Mint through `verify_consumers` took 36
seconds, of which `host` took about 23, and the containers restarted together at 18:29:37 UTC.
`retire_old` found the old value already refused and `confirm_dead` proved it. Authentik logged no
password failure. Nothing failed, so the undo, a second restart during a first, and ssh teardown on
client exit stay unmeasured on the live system.

## Amendment 18 — The rotation layer's outcomes, and the 04:00 window retires (2026-11-01)

The rotation layer pass (`docs/superpowers/specs/2026-10-31-rotation-layer-design.md`) judged this
system against four outcomes and shipped in five parts. Devon made these decisions:

- **The grant is per registry repository.** `rotated_by_sds` stays the per-credential switch, and
  `REGISTRY_REPOSITORIES` stays in the executor as a ceiling the orchestrator can't widen. The
  member record's `credential_rotation` grant only narrows it. An Actions-secret copy in a member
  with no rotation grant is allowed: the rotation App's installation there is the deliberate act.
- **The infraops window retires.** Its 134 nightly reports held no applied `exec:` plan: 132 were
  empty, and the other two were a failed rotation and a blocked manual item, the last on
  2026-07-02. The rows it could execute, `github-classic-aihelper`, `github-classic-lifeops`, and
  `openai-project`, were revoked in July and leave the registry. This closes increment 9 and
  ADR-0054's increment 5.
- **A credential not handed over is a person's.** The 03:00 scan keeps reporting it to change-manager
  as a manual item, and no security record promises execution, so `exec:` plans became manual
  steps. The `plan_hash` state had the window as its only reader and went with it. The file-permission
  auto-fix isn't part of the window and stays.
- **`orchestrator-factory-runner-bearer` is handed over by flag** and first rotates when due,
  2027-07-08 by age. A dry run rendered its package and intent-packages' `create-rotation` validated it. That
  proves generation only. Nothing has proved its first rotation. Its keeper moved from
  `Ops / Platform` to `Rotation / Keepers`, because the root couldn't read it there and
  `BwsStore.locate` can't see that before a claim. With an empty `HOME`, the broad account
  (Developer Machines, by a direct grant) and the root read the same hash.
- **`bws-cred-rotation-token` loses its access and stays unrevoked.** The window was its only reader.
  Devon chose to remove its one project, `Ops / Platform` (read and write), over revoking it, so the
  change can be undone by granting the project again. A fresh login succeeds and lists no project.
  The token, its Keychain item, and its registry row stay, and its age finding of 2027-07-02 is the
  point to decide whether to revoke it.
- **Manual checklists replace the plans.** They carry each row's preconditions, and an exposure's
  closes it with `resolve-exposure`. change-manager shows them on the item page.

The change-manager `rotation` source was a free string that no producer posted after its June seed,
so retiring it removed the tab, the seeder, and the mentions. Its two resolved rows stay. The
`security-executor` identity stays in the agent registry as `retired`, so recorded events still
resolve.

## Amendment 19 — The VM executor issues lane tokens and ping URLs from its inbox (2026-11-07)

**Status: Accepted (2026-11-07).** It met HQ's bar, under HQ's standing answer of 2026-11-06
(limit 3): a clean Opus whole-branch review of PR #626 at `f17ee687` and of PR #628, which accepts
it. ADR-0059 increment 2 waits on it (ADR-0059, "Increments", item 2, and "Dependencies", ADR-0055).

ADR-0059 moves the scheduled lanes off the Mac, one BWS identity per lane, each lane its own
Coolify compose resource, `sds-lane-<lane>` (ADR-0059 Amendment 1). Devon decided that the VM
rotation executor writes each lane's token into its `<LANE>_BWS_ACCESS_TOKEN` variable from its
inbox (ADR-0059 Q8, HQ D-10, option (b)), that it writes each lane's Healthchecks ping URL into the
lane's ping-URL secret the same way, after Devon pastes it by hand (Q10, D-13), and that the VM
executor later rotates lane tokens (Q3, D-5, option (a)). D-5 placed decision 4's lift for lane
tokens in increment 7; D-10 (b) moved it to increment 2, so that the executor writes the first lane
token. This amendment states that lift and the executor extension it needs.

Code reads cite orchestrator `8875e1bf`, Coolify `v4.0.0-beta.473` (`57ea0764`), and
infraops-mcp-server `main` at `9cdaab1f`.

### What the executor can't do today

- **It binds a pasted value only to a rotation that replaces one.** `bind` reads the keeper's
  value (`rotation_worker/inbox.py`, line 135) and requires the provider's `same_identity` to hold
  against it (lines 140-146), which for a BWS token is a fresh login with each value
  (`adapters/bws_machine_token.py`, lines 157-170). A first lane token and a first ping URL have no
  old value, and a lane token revoked after a leak no longer answers.
- **Its contract has no plan without a revoke.** `validate_plan` requires exactly one
  `retire_old` and one `confirm_dead` (`rotation_worker/contract.py`, lines 217-230), and only
  `confirm_dead` may follow `retire_old` (lines 236-238). A first issue has nothing to retire.
- **BWS machine tokens belong to the Keychain instance.** `CLASS_TYPES` maps the class
  `bws-machine-token` to the one type of that name (`rotation_worker/types.py`, lines 49-57), and
  that adapter is in `KEYCHAIN_ADAPTERS` (`rotation_worker/envelope.py`, line 13), which the VM
  instance never claims. Its provider hosts no destination (`bws_machine_token.py`, line 133).
- **Two waiting mints block each other.** `other_mints` lists every unclaimed unit of another
  rotation that comes straight after a person's mint (`inbox.py`, lines 101-116), and `bind`
  refuses while that list isn't empty (lines 130-134). If a lane's token issue and its ping-URL
  issue were both listed, neither would ever bind.

### Decision proposed

1. **Decision 4 is lifted for two kinds of credential, on the VM instance only.** A lane's BWS
   access token and a lane's Healthchecks ping URL may transit the VM instance's inbox
   (`Rotation / Inbox`) and staging. Where each is kept at rest differs:
   - **A ping URL's keeper is the lane's own ping-URL secret**, already approved (ADR-0059
     decision 3, Q10, D-13). The keeper command (item 3) creates it in `Rotation / Keepers`, which
     only the root writes, and the lane's account reads it by a direct grant (amendment 6).
   - **Each lane token keeps a keeper in `Rotation / Keepers`** (Devon, 2026-11-07, HQ D-24: "Keep
     a stored copy"). ADR-0059 accepted the token's transit (Q3, Q8); D-24 adds the value at rest
     on the VM, and it is a standing answer for every later lane token. Items 3, 4, 7 and 10 rest
     on this keeper.

   Every other BWS consumer token stays as amendment 12 left it: handled only by an executor
   instance on the machine that already holds it.
2. **Two new classes, each with its own type.** A lane token's registry row takes the class
   `bws-lane-token`, and a ping URL's the class `healthchecks-ping-url`. Each maps to a type of
   the same name in `CLASS_TYPES`, with store kind `bws-secret`, so the VM instance claims it and
   the Keychain instance never does. `bws-lane-token`'s provider hosts destinations, and its one
   hosted destination is a `coolify-env` on the lane's resource with `redeploy` false.
   `healthchecks-ping-url` has no hosted destination: its keeper is the lane's ping-URL secret,
   which the lane's account reads by a direct grant (amendment 6).
3. **An issue is a rotation's first half.** A type may declare an issue plan: a person's mint and
   stage, then `verify_new`, `store`, `distribute` (for a type with a hosted destination) and
   `verify_consumers`, with no `retire_old` or `confirm_dead`. The contract validates it by its own
   rule, and `validate_plan` is unchanged for every rotation. Every other decision 3 invariant
   holds, with one exception: for a `healthchecks-ping-url` issue, `verify_new` is item 5's shape
   check, with no probe and no known-bad control. That departs from decision 3's R14 rule, and
   Devon accepted it (2026-11-07, HQ D-30: "Yes"); item 5 states it. (Amendment 21 withdraws this
   exception: a ping URL's `verify_new` is a read-only lookup of its check beside a refused
   random one's, so R14 holds for every issue.) The issue plan runs only
   when the keeper holds exactly the inbox's empty marker (`EMPTY`, `inbox.py`, line 46), so
   "nothing to retire" is a fact the executor reads, not a declaration. A sibling of `adopt-keeper` creates
   that keeper on the VM, holding the marker, and prints its secret id, so the registry row names
   the keeper before any package does. The row lands in two changes, as amendment 17's did.
   Decision 3's invariants, on a keeper that holds only the marker:
   - **Quarantine before the keeper changes.** There's no old value, so `store` quarantines
     nothing. It writes the keeper only after reading the marker there. A retried `store` that
     finds the keeper's fingerprint equal to the staged value's treats the write as done; any other
     value fails the unit, recorded as its failure ("never guess").
   - **New is never old.** The old fingerprint is the marker's, so every step after the mint
     refuses a value equal to the marker, and the guard is otherwise unchanged.
   - **Staging is deleted after the plan's last step.** An issue plan has no confirm-dead, so the
     staging secret in `Rotation / Work` is deleted once `verify_consumers` passes, and the
     deletion is recorded, as decision 3 records it after confirm-dead.

   Item 7's departure from decision 3's R10 rule applies to a lane token's reissue, not its issue,
   which has no `retire_old`; Devon accepted it (D-27).
4. **A lane token binds by its declared reach, never by `same_identity`.** The registry row lists
   the BWS secrets the lane's account must see: the UUIDs `.bws-secrets.toml` lists for that lane.
   `bind` takes a pasted token for a `bws-lane-token` issue or rotation only when all of these
   hold:
   - no other rotation waits on a person's mint (`other_mints`, unchanged);
   - it isn't the keeper's value, and a fresh login accepts it beside a refused known-bad control
     (`accepts`, unchanged);
   - what it can see, which `_REACH` reads (`bws_machine_token.py`, lines 100-116), is exactly the
     declared set (see "Open before building" for what `_REACH` lists for such an account);
   - no other keeper of its type holds it.

   This is stronger than `same_identity`, and it holds after a leak's early revoke. Each lane's
   set is distinct, because each holds that lane's own ping-URL secret, and the checks refuse a
   registry in which two rows declare the same set. The lane's account must already have its grant
   on the ping-URL secret, holding the marker, when the token binds, or the digests differ. The
   second and fourth checks compare with keepers' values (item 1, D-24): the reach check alone
   can't tell the lane's current token from a new one, because both see the same set.
5. **A ping URL binds by shape, never by a ping.** A ping marks the check up, so
   no probe may send one. `bind` takes a pasted ping URL for a `healthchecks-ping-url` issue only
   when the keeper holds the marker, the URL is exactly the self-hosted instance's ping base
   followed by one UUID, and no other keeper of its type holds it. The bind is by shape alone
   (Devon, 2026-11-07, HQ D-28: "No Key"): the VM gets no Healthchecks key that identifies a check.
   Its read-only key (decision 4) gets no `ping_url` or `uuid` in any Management API response
   (ADR-0059 decision 3), and the Management key manages every check in the project. The issue
   plan's `verify_new` for a ping URL repeats this shape check and sends no ping; it departs from
   decision 3's R14 rule for ping URLs only (Devon, 2026-11-07, HQ D-30: "Yes"), and cut-over
   step 4 is the proof. (Amendment 21 replaces the bind and the `verify_new` of this item: a ping
   URL binds by its shape and by the read-only key finding its check by `unique_key` beside a
   random UUID's that it refuses, still sending no ping, under Devon's D-31, which supersedes D-28
   and D-30. The lookup covers only the UUID's first half, so cut-over step 4 still proves the
   full URL.)

   Cut-over step 4 proves the URL, not the `--add`. The `--add` runs the lane with
   `LANE_SCHEDULE_ENABLED=false`, and the launcher's `--dry-run` after it pings nothing (ADR-0059,
   "Cut-over for one lane", step 1 and step 2's first bullet). The container's first scheduled
   pass pings the check (step 3), and step 4 verifies that the check's last ping came from the
   container. A wrong URL shows as the lane's own check going overdue, its dead-man alarm, so a
   bad paste fails visibly and never passes silently.
6. **The executor never redeploys a lane.** A `bws-lane-token` destination with `redeploy` true is
   refused before a claim, and its distribute step writes the variable with the client's one
   `PATCH` and never calls `restart()`. `redeploy` is part of the destination's identity
   (`rotation_worker/destinations.py`, lines 17-19 and 38), so a row that changes it is a change
   Devon approves. The lane's container takes the new value only at its next start, which is a
   lane phases' redeploy in that lane's quiet slot (ADR-0059 decision 1).
   - **A token-change mode for the lane phases, in ADR-0059 increment 2's code (amends ADR-0059's
     lane phases).** As accepted, the phases take only `--tag`, `--schedule` and `--add`, and
     `lanes-finish` passes only "the change and no other", so none redeploys a lane after a reissue.
     ADR-0059 increment 7's amendment decides whether the executor runs the redeploy or hands it
     to the deploying session; before it, increment 2 adds `--token`, carried as a
     verify item in that increment and built before `execution-watch` holds a real token, so a
     leak's path works from the first lane. It writes the lane's compose at its running tag, as
     `--schedule` does, and no variable, because the executor has written the token. The command
     prints the calls and the deploying session makes them (`docs/operations/deploy.md`, "The
     lane phases"): the existing `coolify_update_service` and `coolify_deploy`, under the same
     quiet-slot reads. Its `lanes-finish` passes when the image digest, schedule and volume are
     unchanged, the container's `BWS_ACCESS_TOKEN` is non-empty, and `lane-run --read` reads every
     listed secret `ok`. The `lane-run --read` result is a new `lanes-finish` input
     (`finish_mismatches` checks only the digest, schedule, token presence and volume), and
     increment 2's code settles its form.
   - **How a failed `--token` run closes.** A mismatch at `lanes-finish` exits 2 and leaves the run
     `unfinished`, and an `unfinished` run refuses the lane's next `lanes-prepare` and every
     orchestrator release (`require_lanes_current`). `--token` has nothing to undo, because its
     compose is the one the lane already ran, so its `lanes-finish --restored` form needs no
     inverse write: it closes the run as failed, with the mismatches recorded as its reason in the
     run file, and the run never becomes the lane's record. The lane can then take a retry
     `--token`, a `--tag`, or a rollback to the Mac. HQ records the failure in its report of the
     reads (item 7), and the reissue's revoke stays unresolved.
7. **What the executor proves, and what the lane phases prove.** The executor can't read a
   service variable back: its Coolify token has no `read:sensitive` (amendment 1). So its
   `verify_consumers` for the Coolify destination is the `PATCH` answering 2xx, and for a BWS
   destination it reads the secret back by fingerprint (`SecretStore.locate`). The proof that the
   lane loaded the new token is the lane phases': `lanes-finish` reads that the container's
   `BWS_ACCESS_TOKEN` is non-empty, and `lane-run --read` reads every secret the lane lists, each
   `ok` (ADR-0059, "Cut-over for one lane"; for a reissue, item 6's `--token`). A read of the
   lane's check since the keeper's revision doesn't discriminate, because a pass between the write
   and the redeploy runs on the old token and still pings, so no plan uses it.

   On a non-leak reissue the old token still answers, so `lane-run --read` proves less: that the
   container redeployed after the `PATCH`'s 2xx starts and reads every listed secret with the
   token it holds, not that it holds the new one. The revoke is what tells them apart: if the
   container held the old token, its next pass after the revoke fails, and the lane's dead-man
   check reports it. After a leak's early revoke, the same read does prove the new token, because
   the old one no longer answers.

   This departs from decision 3's rule that `retire_old` follows a machine `verify_consumers` for
   every registered consumer (R10): for `bws-lane-token`, until ADR-0059 increment 7, that rule is
   met by the lane phases' reads outside the plan, and the revoke act's precondition is HQ's report
   of them. Amendment 12's "Holders no machine can restart" is the precedent for a person's
   attestation at the revoke. If increment 7's amendment gives the executor the redeploy, it
   restores a machine read; otherwise that amendment states how R10 is met. Devon accepted the
   departure, for lane tokens only (2026-11-07, HQ D-27: "Accepted"). `retire_old` and
   confirm-dead test the old token against its keeper (item 1, D-24).
8. **The order is a mechanism, not a sequence.** HQ changes a lane's ping-URL row to make it due
   only after the lane token's `store` unit completes, so the ping URL's issue unit doesn't exist
   while the token's waits and `other_mints` never sees both. The order rests on HQ making that
   change in that order. A mistake fails safe: if both units wait on a person's mint at once,
   `other_mints` refuses both binds, nothing is written, and the pasted value stays in the inbox
   with its reason (item 11) until HQ fails and cancels the ping URL's unit, as a stranded unit
   is. The lane's cut-over is then as follows.
   1. The root runs the keeper command (item 3) for the lane's ping-URL secret and for the lane
      token's keeper (D-24). Each holds the marker, and the command prints each secret id.
   2. HQ lands the lane's registry rows naming those keepers, in two changes as amendment 17's did:
      the token's row due for its issue, and the ping URL's row not yet due.
   3. The identity's creator grants the lane's account its secrets, including the ping-URL
      secret, which holds the marker.
   4. Devon mints the lane token and pastes it into the inbox. It binds, and the executor stores it
      and writes `<LANE>_BWS_ACCESS_TOKEN` on `sds-lane-<lane>`.
   5. After the token's `store` unit completes, HQ changes the ping URL's row to make it due. Devon
      copies the URL from the check's page and pastes it into the inbox (Q10). It binds, and the
      executor writes the ping-URL secret.
   6. The lane phases' `--add` starts the lane in its quiet slot.
9. **The executor still can't mint or revoke** (amendment 12). Devon mints every lane token and
   revokes every old one, in the Bitwarden web app. A lane token's row is `rotated_by_sds` from
   its first issue, so the age rule applies; at `maxAgeDays` 365, a token first issued in
   November 2026 isn't due by age until November 2027. The rotations this amendment allows before
   then are the early and leak reissues in the following item, and HQ asks Devon before the age
   interval passes, as ADR-0059's Consequences say.
10. **Early and leak reissue before increment 7.** The actor is the rotation proposer (Devon,
    2026-11-07, HQ D-25: "The proposer as recommended"), from infraops findings it already reads:
    an exposure recorded on the lane token's row (`cred.exposure-rotate`) or a `rotate_requested`
    date (`cred.rotation-requested`). HQ records the trigger in infraops's registry, and Devon's
    revision approval is the only person step (amendment 16). The order of the revoke (Devon,
    2026-11-07, HQ D-26: "I approve the proposed answer"):
    - **After a leak, Devon revokes first**, as the exposure response decision 6 already makes
      his, then the reissue runs and its `retire_old` finds the old token refused against its
      keeper (item 7, D-24). The token binds by its reach (item 4), so it needs no
      hand staging. Until item 6's `--token` redeploy in the lane's next quiet slot, the lane
      fails every pass on a dead token, and its dead-man check reports it. A leaked token that
      stayed live through the redeploy's 6-hour wait would leave an exposure open for that long,
      and Infra Brain's `cred.exposure-rotate` rule ends a leak in revoke and reissue.
    - **Otherwise, Devon revokes after the `--token` run's `lanes-finish` passes and
      `lane-run --read` reads every listed secret `ok`** on the redeployed container (item 6;
      item 7 says what that read proves while the old token answers). HQ reports that read, and
      Devon resolves the revoke act at `/review` after it.

    This amends ADR-0059's Consequences, which say a leak reissue is "staged by hand" because the
    inbox can't bind a token once the old one is revoked: item 4's reach bind takes it from the
    inbox, so no hand stage is needed.
11. **What a bind records.** Every bind and refusal logs as the executor logs today: structured
    JSON on stdout (`rotation_worker/logs.py`), naming the unit, the keeper's or staging secret's
    UUID, the destination string and the value's fingerprint, never the value. The Coolify
    `PATCH` response echoes the written row (Coolify `ServicesController.php`, line 1258), and the
    client keeps only the status (`rotation_worker/coolify.py`, lines 99-108). A value that doesn't
    bind stays in the inbox with its reason, as today (`inbox.py`, lines 130-151); a failure after
    the claim fails the unit and is recorded as the unit's failure, never skipped.

### What the code settles

- **Who creates `<LANE>_BWS_ACCESS_TOKEN`: the compose parse, at create.** The executor's client
  allows only `PATCH /api/v1/services/{uuid}/envs` and `POST .../restart` (`rotation_worker/coolify.py`,
  lines 63-65 and 82-83), and Coolify's `PATCH` answers 404 for a key with no row
  (`ServicesController.php`, lines 1238-1241), which the client raises as `CoolifyRefused`. Coolify's
  create saves the raw compose and parses it (`ServicesController.php`, lines 616 and 627), and
  `serviceParser` (from `bootstrap/helpers/parsers.php`, line 1504) runs `firstOrCreate` for a
  `${VAR}` with no default in `environment:` (lines 2447-2459), making the row with no value. A
  lane's tracked compose maps `BWS_ACCESS_TOKEN: ${<LANE>_BWS_ACCESS_TOKEN}` (ADR-0059, decision
  1), so HQ's create of `sds-lane-<lane>` makes the row, and a later parse doesn't overwrite the
  executor's value, because `firstOrCreate` never updates. The placeholder's compose maps no token,
  so it can't confirm this; the first lane's create does (see "Open before building").
- **`postgres_role` requires `redeploy` true and restarts.** `hosted_destination` refuses a
  `coolify-env` without `redeploy` true (`adapters/postgres_role.py`, lines 169-182), and `host`
  calls `restart()` (line 316), which is `POST /services/{uuid}/restart`
  (`rotation_worker/coolify.py`, lines 65 and 96-97). Item 6 is the inverse for lane tokens.
- **infraops has no class for a ping URL.** `CLASS_POLICY` (`src/security-drift/cred-rotation.ts`,
  line 31) has `bws-machine-token` (lines 88-93) and no Healthchecks class, and a row whose class
  isn't there raises `cred.unknown-class` (lines 242-253). The infraops change adds two classes:
  `bws-lane-token` with `maxAgeDays` 365, beside `bws-machine-token`, and `healthchecks-ping-url`
  with `maxAgeDays` infinite and a landmine saying the URL can't be rotated, only replaced by
  recreating the check, which starts a new history. That change lands first, because
  `test_every_type_names_a_class_infraops_knows` holds `CLASS_TYPES` to infraops's classes
  (`tests/architecture/test_rotation_agreement.py`, lines 55-56).

### Open before building

Each item is settled in ADR-0059 increment 2, before its code merges.

- **The ping URL's check read (increment 2).** The VM's read-only Healthchecks key gets no
  `ping_url` or `uuid` (item 5), so whether any field a read-only response does return ties a
  pasted UUID to the lane's check, whether `/pings/` needs the Management key, the flips endpoint
  and the ping-log limit are Healthchecks reads that this amendment doesn't settle. The VM gets no
  key that identifies a check (D-28), so item 5 binds by shape alone, and cut-over step 4 proves
  the URL: the container's first scheduled pass pings the check, and step 4 verifies that the
  check's last ping came from the container (ADR-0059, "Cut-over for one lane", steps 3 and 4). A
  wrong URL shows as the lane's check going overdue. (Amendment 21 settles it: a read-only key
  reads a check by its `unique_key`, which the executor derives from the pasted UUID. That key
  covers only the UUID's first half, so cut-over step 4 still proves the full URL.)
- **The token's row at create (increment 2).** HQ's create of the first lane's resource confirms
  the empty `<LANE>_BWS_ACCESS_TOKEN` row by the executor's `PATCH` answering 2xx; a 404 means
  another actor must create the row, and the issue unit fails with `CoolifyRefused` rather than
  skipping.
- **What `_REACH` lists for a lane account (increment 2).** A lane's account has only direct
  secret grants (amendment 6), and `_REACH` hashes both the projects and the secrets a token lists.
  Measure what it prints for such an account, and declare the set as secrets alone or secrets and
  projects to match.
- **What makes a first issue due (increment 2).** Whether infraops raises
  `cred.rotation-requested` for a row with a `rotate_requested` date and no recorded rotation is
  read in the build.
- **The lane phases' `--token` mode (increment 2, carried verify item).** Item 6's mode is built
  and verified in increment 2, before `execution-watch` holds a real token, using only the deploy
  command's existing calls. It amends ADR-0059's lane phases. The build settles each of these:
  - its own `change_key`, beside `tag:`, `add:` and `schedule:`;
  - its `lanes-finish --restored` form, which closes a failed run with no inverse write (item 6);
  - the `lane-run --read` result as a `lanes-finish` input (item 6);
  - a `--tag` run after the executor's `PATCH` also loads the new token, without `--token`'s
    reads, so the revoke's precondition needs those reads re-run on that container;
  - `--token` refuses `sds-lane-placeholder`, which holds no token.
- **A pass on a dead token and the quiet slot (increment 2).** Whether a lane pass that fails on a
  revoked token leaves its check `started`, which would hold off the leak's `--token` redeploy
  (the quiet slot waits while `started` is `true` and `status` isn't `down`), is read in the build.
- **Questions for Devon.** None open: D-24 to D-28 and D-30 are answered and recorded in this
  amendment.

### Consequences

- **Threat model.** The VM root gains every lane token at rest, in `Rotation / Keepers`, beside
  every other rotated credential's keeper, and every ping URL at rest (ADR-0059 decision 3). A
  compromised executor could use any lane's token, write any lane's token variable, and overwrite
  any lane's ping-URL secret, which blinds that lane's alarm. ADR-0059 accepted transit (Q3, Q8);
  the value at rest is accepted by D-24, and a reissue's quarantine, `retire_old` and confirm-dead
  need it.
- **Builds.** An infraops change (the two classes in `CLASS_POLICY` and its class fixture), then
  one orchestrator change: the two types, the issue plan's validation, the reach and shape binds,
  the keeper command, the `redeploy` refusal, the agreement fixture that
  `test_rotation_agreement.py` reads, and the policy pattern that recognizes rotation units by
  type (amendment 17), with a new `decided` date; and in the deploy command, the lane phases'
  `--token` mode (item 6). intent-packages' `create-rotation` and the
  proposer's `acceptance()` learn an issue plan with no retire. Each lane's registry rows land in
  two changes, as amendment 17's row did.
- **Unchanged.** The Keychain instance, its adapter and its inbox; every existing plan and
  generated package; the one click per rotation (amendment 16).

## Amendment 20 — A revision re-renders a generated package whose derived plan changed (2026-11-07)

**Status: Accepted on merge.** It's accepted when the pull request that carries it merges after a
clean Opus whole-branch review, the bar HQ set for this change.

### Context

Amendment 19 gives a lane token two plans: an issue plan for its first value and the full plan for
every reissue (items 3 and 10). The proposer generates the issue package when infraops'
`last_rotated` for the token is null, and `_record_retired` dates it once `work-watcher` retires
the issue's record. Every later revision then went through intent-packages' `revise`, which writes
only the occurrence and destinations (amendment 15 decision 5). So an early or leak reissue still
stated the issue plan's criteria, and the executor refused it: the keeper holds the issued value,
not the empty marker (`checks._check_issue_fact`).

HQ put the fork to Devon as D-32 on 2026-11-07: (A) the proposer re-renders a generated package's
criteria, prose and authority at revision time when the plan its row derives differs, and Devon
approves the new criteria in that same one revision click; or (B) a person hand-revises each lane
token's first reissue. **Devon's answer (2026-11-07T21:31 UTC, HQ D-32): A.**

### Decision

1. **A generated package is re-rendered at revision only when its derived plan changes.** When the
   proposer revises a package it generated (`created_by: rotation-proposer`), it derives the plan
   the row calls for (`generate.plan_for`, with the `last_rotated` signal) and the plan the package
   states (`select_plan`'s match among the row's candidates). Only when the two differ does the
   revision re-render the package's criteria, prose and authority from the row
   (`generate.reissue_body`). The re-render keeps the package's identity, lifecycle and
   `created_at`, which `revise` and `transition` own, and it writes the due occurrence and the
   registry's destinations, as every revision does. One case changes plan: a lane token's
   issue package once infraops dates its `last_rotated`.
2. **Every other revision is unchanged.** A generated package whose plan is unchanged keeps its
   approved wording, and a revision writes only its occurrence and destinations. A hand-authored
   package, including the four that run pinned pre-derivation plans, is never re-rendered.
3. **Nothing is guessed.** A type with two plans and no `last_rotated` for the credential is an
   `unlaned` finding, and the pass writes nothing for it. A generated package whose criteria
   describe no plan its row derives is that credential's `error`. Neither falls back to a plan.
4. **No new person step.** The re-render rides the revision Devon approves by name (amendment 16;
   amendment 19 item 10). The approval shows the new criteria as part of that revision's change.

### Consequences

- **Infra Brain rule 1032.** A re-rendered full plan keeps `verify_consumers` (AC-004) before
  `confirm_dead` (AC-005). A lane token's authority still prohibits `credential_revoke` in both
  plans, because a person revokes it.
- **Decisions 3 and 5 of amendment 15 are narrowed**, not withdrawn. Decision 5: approved wording
  is rewritten only when the plan it describes is no longer the one the row derives, and only in a
  revision a person approves. Decision 3: a generated package whose criteria describe no plan its
  row derives isn't revised for destination drift. The pass refuses it as that credential's `error`
  (decision 3 here) and writes nothing, and an operator hand-revises it onto the derived plan.
- **Unchanged.** The executor, the decomposer, `select_plan`, every existing generated package's
  bytes, and the ping URL's `verify_new` criterion and adapter.

## Amendment 21 — A read-only lookup finds a ping URL's check (2026-11-07)

**Status: Accepted on merge.** It's accepted when the pull request that carries it merges after a
clean Opus whole-branch review, the bar HQ set for this change.

### Context

Amendment 19 bound a ping URL by its shape alone and had its `verify_new` repeat that shape check,
with no probe and no known-bad control (items 3 and 5). That rested on two answers: the VM gets no
Healthchecks key that identifies a check (HQ D-28, "No Key"), and a ping URL's `verify_new` may
depart from decision 3's R14 rule (HQ D-30, "Yes"). The read Amendment 19 left open ("Open before
building", "The ping URL's check read") shows that the read-only key the executor already holds
does identify a check, without ever being shown its UUID.

Healthchecks `v4.4`, the self-hosted instance's image tag, reads at that tag as follows:

- `hc/api/urls.py` line 51 routes `checks/<sha1:unique_key>` to `get_check_by_unique_key`, and the
  `sha1` converter (`SHA1Converter`, line 21) matches 40 characters.
- `get_check_by_unique_key` (`hc/api/views.py` lines 489-496) is `@authorize_read`, so a read-only
  key may call it. It searches only the key's own project, and answers 404 for any other key.
- `Check.unique_key` (`hc/api/models.py` lines 432-434) is
  `hashlib.sha1(self.code.hex[:16].encode()).hexdigest()`: the SHA-1 hex digest of the first 16
  characters of the check UUID's hex form, which has no hyphens.

So the executor can derive a pasted URL's `unique_key` itself, and ask the read-only key whether
that check exists, without sending anything to the URL. The lookup proves less than the whole
URL: `unique_key` hashes only the first half of the UUID, so a URL whose last 16 hex characters
are wrong has the same `unique_key` and the same 200. It proves that the check exists in the
read-only key's project and that the URL's UUID matches it in its first half.

HQ put this to Devon as D-31 on 2026-11-07. **Devon's answer (2026-11-07, HQ D-31): C**, the
read-only lookup beside a random UUID's lookup as the known-bad control, with the verifier
deciding the step. D-31 supersedes D-28 and D-30.

### Decision

1. **A ping URL binds by its shape and by a read-only lookup of its check.** `bind` takes a pasted
   ping URL for a `healthchecks-ping-url` issue only when the keeper holds the marker, the URL is
   exactly the self-hosted instance's ping base followed by one UUID, and the read-only key's
   lookup of a random UUID's `unique_key` answers 404 while the lookup of the URL's answers 200,
   and no other keeper of its type holds it. A value that doesn't bind stays in the inbox with its
   reason, as amendment 19 item 11 says.
2. **A ping URL's `verify_new` is that lookup beside that control, and R14 holds with no
   exception.** The contract checks the shape first, so nothing malformed is looked up, then runs
   decision 3's probe: the control, then the subject, on one target. Lookup 200 with control 404
   passes. A lookup answering 404 (a URL wrong in its UUID's first half, or a check in another
   project) fails the step; a URL wrong only in its second half answers 200 and passes. A
   control answering anything but 404 fails it, because the probe then can't tell a good URL from
   a bad one. Any other status, or no answer, fails it. No ping is sent: the probe reaches
   Healthchecks' Management API, never the URL.
3. **The verifier decides the step: one click per issue.** The step files the two probes on one
   target, the control refused and the new value live, which `rotation_step_verdict` already
   decides. The one click left is the package revision approval (amendment 16).
4. **What is filed and logged.** The evidence names the check by the first 12 characters of its
   `unique_key`, in the step's note, and records the two HTTP statuses. It never holds the URL, its
   UUID, the whole `unique_key` or the key. The whole `unique_key` is 40 hex characters, and the
   orchestrator's rotation evidence scan refuses any run of 32 or more (`rotation_evidence.py`,
   `LONG_RUN`), so a prefix is the form that can be stored. Healthchecks shows a read-only key the
   `unique_key` in place of the UUID, so neither it nor its prefix is a credential. A lookup with
   no answer logs the same prefix and the transport error's type, as structured JSON on stdout.
5. **The read-only key's reach widens by one read.** The executor reads the read-only key in
   `Rotation / Healthchecks` while it reads a scheduled reader's check, and also while it binds or
   verifies a ping URL. Its client permits the check listing and `GET /api/v3/checks/<unique_key>`
   for a 40-character lowercase hex key, nothing else. No new secret, grant or rotation is needed.
6. **No revoke.** A ping URL is still issued only, never rotated or revoked; this amendment adds no
   revoke, and the issue package's authority keeps `credential_revoke` prohibited (Infra Brain rule
   1032).

### Verify at cut-over

- **The `sds-execution-watch` check sits in the read-only key's project.** The lookup searches
  only that project, so a check in any other answers 404 and the issue refuses, failing closed.
  The first lane's ping-URL bind proves it; a refusal reading "finds no such check" for a URL
  copied from the right check means the check is in another project.

### Consequences

- **Amendment 19 items 3 and 5 are narrowed**, not withdrawn: item 3's exception to R14 is gone,
  and item 5's bind and `verify_new` are this amendment's. Item 5's other rules stand: the marker,
  the shape and no other keeper holding the URL. Cut-over step 4 still proves that the lane's
  container pings the check, and it's still the only proof of the full URL: the lookup reaches
  only the UUID's first half.
- **The generated issue package's wording is unchanged.** Its `verify_new` criterion states the
  shape, that no other keeper holds the URL and that nothing pings it, all still true; the lookup
  is in the evidence the verifier reads. Its bytes are pinned with intent-packages
  (`rotation_package_issue_healthchecks-ping-url.json`), so a wording that names the lookup is a
  change to both repositories together.
- **Unchanged.** The issue plan, the lane token's types and binds, the verifier's code, every
  generated package's bytes, and the one click per issue.

## Amendment 22 — A standing answer approves revision 1 of a lane credential's generated package (2026-11-10)

**Status: Accepted (2026-11-10).** Devon accepted it from Slack ("G-23: accept", recorded
2026-11-10 15:51 UTC), after a clean Opus whole-branch review of PR #647 at `d21a43cc` (HQ's D-12
standing answer, limit 3). It rests on Devon's answer to HQ decision D-34 (2026-11-10T12:37 UTC, sds-hq `state/decisions.md`). The question offered: "(A) A
standing answer approves a proposer-generated revision 1 of a lane credential whose
compare-destinations is equal and whose authority is the type's standard set; a cycle records the
approval in a PR that cites it. Reissues (revision 2 and later) still need you. You keep only the
mint or paste, from the Slack alert." **Devon's answer: "A. Lets not add me approving every
package. The first idea was better than that."**

Code reads cite orchestrator `5145adff` and intent-packages `c5c9225`.

### Context

A lane credential's revision 1 is the same package every time apart from its ids. The proposer
renders it from the credential's registry row (`rotation_proposer/generate.py`, `render`, lines
248-273, which writes `created_by: rotation-proposer`, line 48), and intent-packages'
`create-rotation` writes it (`factory/scaffolds.py`, lines 514-555). Yet each revision 1 waits for
an interactive session in which Devon approves it by name. On 2026-11-10 that happened twice:

- **The landing ledger's token**, `rotation-landing-ledger-bws-token` revision 1, generated in
  intent-packages `d6440e5` and approved by `devon` in `fff7fde` (#167). Its authority allows
  `secret_read`, `secret_write` and `infra_mutation`, because its Coolify destination is hosted.
- **The landing ledger's ping URL**, `rotation-landing-ledger-ping-url` revision 1, generated in
  `0d09201` and approved by `devon` in `c5c9225` (#168), change record 198. Its authority allows
  `secret_read` and `secret_write`.

Rendered again from their rows at orchestrator `5145adff`, with each package's own occurrence and
`created_at` and the issue plan, both packages equal the stored ones field for field apart from
`status`. A copy with `infra_mutation` added to the ping URL's authority differs, in `authority`
and in `dependencies.required_capabilities`. So the person's click on these packages re-reads text
a machine can reproduce.

Where "only a named person approves a rotation revision" is enforced today:

1. **intent-packages refuses an approval policy for the rotation profile.** A rotation package's
   profile is `non-software-operational` (`generate.py`, line 47; `work_carrier/prepare.py`, line
   214). Its row in `approval-policy.toml` (line 112) has no grant table, so
   `ApprovalPolicy.refusals_for` returns `profile_not_policy_approvable`
   (`approval_policy.py`, lines 152-169, the refusal at lines 166-167), and
   `operations._authorize_approver` (lines 174-209) admits a human operator first and a policy
   approver only when the policy raises no objection. `do_approve` emits `package.approved` to the
   factory-events chain before it writes the lineage approval (`operations.py`, lines 288-305), and
   an emit failure writes nothing.
2. **The carrier checks the approval through the emitter, and doesn't check for a person.**
   `work_carrier/prepare.py` (lines 139-145) runs `intent-packages emit-intake-payload`, behind
   which every approval requirement lives: the lineage approval's hash equals the package's, and
   `verify_approval` (`operations.py`, line 529) finds a `package.approved` event for that hash
   and revision in a verified chain (`_events_file_has_matching_approval`, line 472, matching on
   hash and revision only). The orchestrator verifies again at intake, and it also binds the
   approver: `package_sources._matching_event_evidence` (lines 256-273) requires the event's
   `approver` to equal the lineage's. `_is_recognised_approver` (lines 199-214) accepts a human
   operator or the policy shape `policy:<profile>@v<version>` (line 26). The carrier's own
   rotation check is the class-to-profile match (`prepare.py`, lines 217-250). The person
   requirement therefore rests on two places only: intent-packages' missing grant, and the
   orchestrator's refusal to approve a rotation decomposition by policy when its revision was
   approved by a policy (`services/intake/decomposition.py`, lines 357-358,
   `revision_approved_by_policy`). A `policy:non-software-operational@` approver reaches that
   refusal: `is_bump_revision` routes to the bump branch only the `dependency-update` profile or
   the `policy:dependency-update@` prefix (`services/intake/bump_policy.py`, lines 20 and 31-32).
   The executor reads only that the latest lineage approval's hash is the revision's
   (`rotation_worker/checks.py`, lines 257-260), never who approved it.
3. **`compare-destinations`** (`rotation_worker/cli.py`, lines 91-112) reads the registry row at
   each registry repository's `main` (`registry.find_credential`, lines 99-104) and the standing
   package's `profile_fields.destinations` at intent-packages' `main`, prints the registry commit
   it read, and exits 0 for equal, 1 for different and 2 for unreadable, including any unforeseen
   exception (lines 101-107). "Equal" is `destinations.same` (lines 113-116): the same strings in
   any order, a repeat counting (`compare.py`, lines 16-23).
4. **A type's standard authority is a function, not a constant.** `generate.authority` (lines
   201-219) builds it from the type's capabilities, the row's destinations and whether the plan is
   an issue: `secret_read` and `secret_write` always, `credential_create` for a machine mint,
   `credential_revoke` for a machine revoke outside an issue, and `infra_mutation` when a step
   writes a hosted setting; every other capability is prohibited, and the budgets are three
   attempts with `max_llm_calls` null. `bws-lane-token` and `healthchecks-ping-url` mint and revoke by
   person (`rotation_worker/types.py`, lines 53-59, classes at lines 66 and 70), so their standard
   sets differ only by `infra_mutation`.

### Decision

1. **What it narrows.** Amendment 16 decision 1 ("No approval policy approves a rotation
   revision") is narrowed: one standing approval approves revision 1 of a lane credential's
   generated package when every check in item 2 passes. A **rotation revision** here is a revision
   whose package `profile` is `non-software-operational`, whatever door it entered by and whatever
   its change-manager record's `change_class` says (a hand-proposed record has none). For a lane
   credential's revision 1, the basis decisions 2 and 4 rest on, "only a named person can approve
   a rotation revision", becomes: a fixed command reproduced the package from the registry at a
   named commit and refused nothing, intent-packages' policy re-checked its shape and raised no
   objection, the chain holds the approval under the policy's approver string, and the carrier's
   own identity recorded that its checks passed on that revision, which the decomposition gate
   requires (item 3). Every other rotation revision keeps the named person's click. These
   statements are **narrowed, not withdrawn**, in the same way:
   - amendment 2's "What the answers change", decision 1: Devon's read of a revision's
     destinations against the last approved revision, before approving. A lane credential's
     revision 1 has no earlier approval, and `compare-destinations` stands in for the read;
   - amendment 11 decision 5 ("The package revision approval ... untouched");
   - amendment 15 decision 7 ("Approving the generated package stays a person's act");
   - amendment 15 increment 4's design item 4 ("Devon approves it by name, as every revision"),
     which states the click for the generated revision 1 this amendment covers;
   - amendment 15 increment 4's design item 5, which concerns reissues and so is unchanged in
     effect;
   - amendment 16 decisions 1 to 4 and its Consequences;
   - amendment 17's Consequences ("the one click every rotation has");
   - amendment 19 item 10 ("Devon's revision approval is the only person step") and its
     "Unchanged" line;
   - amendment 20 decision 4 ("No new person step"), which concerns reissues and so is unchanged in
     effect;
   - amendment 21 decision 3 and its "Unchanged" line ("one click per issue"): for a lane
     credential's revision 1 the click is the standing approval's.
2. **The conditions are refusals a fixed command makes.** One command,
   `rotation-proposer standing-approval <credential_id>` (increment 2), reads the registry and
   intent-packages into the executor's mirrors and refuses unless each of these holds. It names the
   registry commit and the package commit it read. Any read it can't make, and any exception, is a
   refusal (exit 2), never a pass, as `compare-destinations` treats an unforeseen exception
   (`cli.py`, lines 103-107). Any refusal leaves the revision for Devon's named approval, and the
   command prints why.
   - **Proposer-generated.** `created_by` is the author's word, and `approval-policy.toml` already
     rejects checking a self-asserted field. So the command re-renders the package with
     `generate.render` from the row at the named registry commit, with the package's own
     occurrence and `created_at` and the plan the proposer would choose (`plan_for`, with the
     `last_rotated` infraops serves), and refuses unless the result equals the stored package in
     every field apart from `status`. It also refuses a `created_by` other than `rotation-proposer`.
   - **Revision 1, with no earlier approval.** The package's `revision` is 1, its lineage lists
     one revision and no approval, and its `current_state` is `ready_for_review`. The carrier's
     form of the command (item 3) instead requires exactly one approval, on revision 1, by the
     policy approver, whose hash is the package's.
   - **The issue plan.** The plan `plan_for` chooses for the row, and the plan the stored package
     describes, is the issue plan: the `last_rotated` infraops serves for the credential is null
     (`plan_for`'s `first_issue`, lines 125-129). A revision 1 that would carry the full rotation
     plan (a credential infraops already records rotated) is refused and waits for Devon. This is
     the amendment's default until Devon says otherwise ("Open before building").
   - **One of the two types.** The row's `class` is `bws-lane-token` or `healthchecks-ping-url`,
     and the package's destinations hold the matching `provider name="<type>"` destination.
   - **Destinations equal.** `compare-destinations`' comparison is equal for the credential, with
     the registry read at the commit the command names, which is also the commit the re-render
     used. The grant (item 3) also limits the destination kinds: for `bws-lane-token`, one
     `bws-secret`, one `coolify-env` with `redeploy` false, and the `provider`; for
     `healthchecks-ping-url`, one `bws-secret` and the `provider`.
   - **Standard authority, exactly.** The package's `authority` equals `generate.authority` for
     the type, the row's destinations and the plan, compared whole: allowed, requires-approval,
     prohibited and budgets. The re-render already implies it; the command reports it separately
     so a refusal says which condition failed.
3. **Who records the approval, and how it's attributed.** When the command exits 0, an HQ cycle
   runs intent-packages `approve` in a packages checkout with the approver
   `policy:non-software-operational@v4`, and opens a pull request carrying the lineage change. The
   PR body cites the standing answer by its file and date (sds-hq `standing-answers.md`, the
   2026-11-10 entry of item 4), the command's output, and both commits it named.
   - **The approver is the policy, never `devon`.** It's ADR-0028's shape, which no registry agent
     id can take, so the lineage, the chain event's evidence and the orchestrator's
     `VerifiedApproval.approved_by` all show that the policy approved, not a person.
   - **intent-packages re-checks what it can see.** `approval-policy.toml` schema 4 gives
     `non-software-operational` a grant that refuses unless the package is a standing revision 1
     with no earlier approval in its lineage, `created_by` is `rotation-proposer`, it names
     exactly one of the two types by its `provider name=` destination, its destinations are of
     that type's kinds (item 2), its authority equals one of the grant's listed sets for that
     type, and no criterion is a `human_review`. It can't read
     the registry, so it can't re-check the re-render or the destinations; the carrier does (the
     next bullet). The grant names only shapes, so every other `non-software-operational` package
     still draws an objection.
   - **The chain records** the `package.approved` event with the hash, the revision, the policy
     approver and the commit, emitted by the cycle's `FACTORY_AGENT_ID`, before the lineage is
     written.
   - **The merger's identity is not relied on.** HQ's merge shows on GitHub as Devon's login,
     like every approval PR before this one. The carrier relies instead on the policy approver in
     a verified chain event that matches the lineage, and on running item 2's checks itself: a
     rotation revision (item 1: keyed on the package's `profile`, not on the record's
     `change_class`, which the carrier's own rotation check keys on at `prepare.py`, lines
     240-250) approved by `policy:` is carried only when the command's carry form refuses nothing.
     Otherwise the record is refused as `standing_approval_refused` and waits for a person. The
     carry form differs from the approving form in three ways:
     - it requires exactly the one policy approval on revision 1 (item 2), not none;
     - it reads the registry at `main`, as the executor does, because the lineage records only
       the package commit. A registry that moved since the approval makes the re-render differ,
       so the carrier refuses and Devon decides, failing closed;
     - it doesn't read infraops, so it can't read `last_rotated`; it accepts only the re-render
       under the issue plan, which item 2 already required of the approval. A credential issued
       after the approval is still the executor's to catch: `checks._check_issue_fact` (lines
       178-198) refuses an issue plan whose keeper holds a value other than the marker and this
       rotation's staged value, and an issue writes only after reading the marker there.
   - **The carrier records that its checks passed.** When the carry form refuses nothing, the
     carrier records an observation on the revision (subject type `package_revision`, which the
     schema already holds) whose facts bind the package id, the revision number, the package
     hash (the revision's `content_hash` once registered), the package commit the lineage names
     and the registry commit the carry form read. It records it before it posts the intake,
     because the gate runs in the decomposition proposal's own transaction and a record written
     later can't help it. Its `source_reference` names the revision and the registry commit, so a
     later pass at a moved registry is a new reference, not an `observation_conflict`. The orchestrator stores the
     authenticated actor as `recorded_by` (`services/release/observations.py`, line 210), so the
     record is the carrier's only if the carrier holds an identity no other caller holds. It
     doesn't today: it posts as the shared SYSTEM key `orchestrator-system`
     (`work_carrier/cli.py`, lines 162-167), and any SYSTEM or OBSERVER caller may record an
     observation (`OBSERVATION_ROLES`, line 239). So the carrier gets its own registry agent id
     and M2M credential, and the orchestrator a setting that names it, as the rotation executor
     identities are named; an unset setting refuses every standing approval. This departs from
     ADR-0027's recommendation that the carrier act as SYSTEM, which the comment at
     `work_carrier/cli.py` cites: a distinct identity is now load-bearing, not only more precise
     attribution.
   - **The orchestrator's decomposition gate runs no checks of its own, and requires the
     carrier's record.** A revision reaches intake without the carrier: `POST
     /api/v1/package-intakes` (`api/routes/intake.py`, lines 118-124) admits HUMAN and SYSTEM
     (`package_intake.py`, line 34), the `/review` intake form registers one
     (`web.py`, line 753), and SYSTEM stages one for a person to confirm
     (`api/routes/intake.py`, line 142). Every door converges on the decomposition gate, which
     sends any non-bump SYSTEM-proposed revision to the rotation branch (`decomposition.py`,
     lines 332-335). So the exception lives in `_rotation_policy_outcome`, whose refusal at lines
     357-358 refuses a `policy:` approver today. It still refuses one, except when all of these
     hold, and on any other input, an absent or mismatched record, or a read that fails it
     refuses and a person decides:
     - the approver has the prefix `policy:non-software-operational@`. It keys on the prefix, as
       `bump_policy.py` keys on `policy:dependency-update@` (line 20), so a later schema version
       doesn't stop every standing approval; intent-packages already refuses any version but the
       loaded policy's (`_authorize_approver`, lines 200-206);
     - the revision is a rotation revision (`revision.profile`, item 1), its number is 1, and its
       package names one of the two types;
     - an observation recorded by the configured carrier identity binds this revision's package
       id, revision number and `content_hash`, and records that the carry form refused nothing.

     The gate doesn't re-run the checks because it can't read the registry: nothing under
     `src/orchestrator/` reads it today, and adding a read would add an outbound path and a token mint that the
     dispatch App's grant bounds would have to cover (`test_platform_edges.py`). The carrier
     already reads the registry. A decomposition the policy then approves stays attributed as
     amendment 16 decision 3 says.
4. **The standing answer is a precondition.** The approval rests on an entry in sds-hq
   `standing-answers.md`, which changes only through Devon's own words in an interactive session.
   This amendment doesn't write it. Until that entry exists, and while it stands, no cycle runs
   item 3; if it's removed or superseded, cycles stop at once and every revision 1 waits for
   Devon's named approval again. Nothing in the code reads the file: the code's switch is the
   schema-4 grant, and removing that grant is the machine-side withdrawal.
5. **Reissues and every other type stay Devon's.** Revision 2 and later of any package, including
   a lane token's early or leak reissue (amendment 19 item 10, amendment 20), and every revision of
   any other credential type, still need Devon's named approval. A revision 1 the proposer didn't
   generate, or one that a person edited after generation, fails the re-render and needs it too.
6. **What stays a person's.** Devon still mints each lane token and pastes it, or pastes each ping
   URL, from the alert (amendment 15 decision 6). An issue writes nothing until a value binds
   (amendment 19 items 4 and 5; amendment 21 decision 1), so an approved revision 1 with no paste
   changes nothing in production. HQ already makes a ping URL's row due (amendment 19 item 8).

### Increments

None is built by this amendment. They don't ship independently. They ship in the following
order, because the grant in increment 1 is what makes a policy approval possible, and it must
land after every check that guards it:

1. **First, increment 2,** because it changes nothing that approves: the command only reads and
   reports.
2. **Then increment 3, in one change.** The carrier's checks, its record, its identity and the
   gate's exception land together, and the gate's exception never lands alone: a gate that
   admitted the policy approver without requiring the carrier's record would let any revision
   that reached intake by another door through on a prefix, a revision number and a type name.
   The gate's exception also depends on the record in code (item 3), so even out of order it
   admits nothing the carrier didn't check.
3. **Then increment 1.** Until the grant exists, intent-packages refuses the policy approver, so
   no policy-approved rotation revision can exist while the orchestrator's side is incomplete.
   Shipping the grant last means the approval becomes possible only once every check that guards
   it is live.
4. **Last, increment 4,** and only after Devon writes the standing answer.

The increments, by number:

1. **intent-packages: the schema-4 grant.** `approval_policy.py` learns schema 4 (the version set,
   line 58, and a grant shape for the rotation profile's checks), `approval-policy.toml` gains
   `[profile.non-software-operational.grant]` with the two types' authority sets and the
   standing answer's date in its rationale, and its tests pin every refusal. `operations.py`
   changes too: `refusals_for` and `_authorize_approver` (lines 174-209) see only the package, so
   the lineage check needs `_authorize_approver` to pass the lineage `do_approve` already reads
   (line 256) into the policy's refusal. The exact-approver check is unchanged.
2. **orchestrator: the fixed command.** `rotation-proposer standing-approval <credential_id>` in
   `src/rotation_proposer/`, reusing `generate.render`, `generate.authority`, `plan_for` and
   `rotation_worker.compare`. It logs as the proposer logs today: structured JSON on stdout,
   naming the credential, the commits, each condition and its result, and no value. It exits 0
   (no refusal), 1 (a refusal, with every failed condition listed) or 2 (unreadable).
3. **orchestrator: the carrier, its record and the decomposition gate.** `work_carrier/prepare.py`
   runs increment 2's carry form on any rotation revision (keyed on the package's `profile`)
   whose approver is a `policy:` string, refuses `standing_approval_refused` otherwise, and on a
   pass records the observation of item 3. The carrier gets its own registry agent id and M2M
   credential (a merged security-standards commit and an image rebuild, as every new actor
   needs), and the orchestrator a setting naming it. The observation needs a new
   `observation_type` and a new `source_system` for the carrier, both DB CHECK values, so the
   change carries a migration and their vocabulary registration. `_rotation_policy_outcome` in `decomposition.py` admits the one
   approver prefix of item 3 only with that record, and refuses with a new reason otherwise.
   `package_sources.py` needs no change, because its policy arm already recognises the shape
   (line 26). The root, `src/orchestrator/services/intake/CLAUDE.md` and
   `src/rotation_proposer/CLAUDE.md` invariants that say the proposer never approves and that a
   named human approves each rotation revision are rewritten in that change, and so is
   `docs/operations/rotation-proposer.md` step 3 ("No approval policy grants this profile").
4. **sds-hq: the cycle step.** After Devon writes the standing answer, a cycle runs increment 2's
   command for each lane credential's revision 1 in `ready_for_review`, and on exit 0 approves it
   as item 3 says and opens the PR. A refusal leaves the revision for Devon and is reported to
   him with the command's reason.

### Open before building

- **A revision 1 that is in effect a reissue.** D-34 says "revision 1". Two cases are not first
  issues: a package deleted and generated again for a credential already issued, and a new row on
  a credential whose `last_rotated` infraops already serves, which `plan_for` (lines 122-153)
  gives the full rotation plan as revision 1. Devon decides whether the standing answer covers either. Until
  he does, the default is the narrower one: the standing answer covers only the issue plan
  (item 2's issue-plan condition), so the second case waits for him. The first case is covered by
  that condition only when the earlier issue set `last_rotated`; whether every issue does is
  checked in increment 2.
  Devon accepted the amendment without widening it (G-23), so the default stands: the standing
  answer covers only the issue plan.
- **The carrier importing the generator.** `work_carrier` imports `rotation_worker` today
  (`work_carrier/cli.py`, lines 82-84) but not `rotation_proposer`. Whether the carrier may import
  `generate`, or the check moves into a module both lanes may import, is the architecture guards'
  question (`layers.toml`, `test_unreachable_guards.py`).
- **The chain the carrier reads.** The approval event is written to the chain of the machine the
  cycle runs on (`FACTORY_EVENTS_HOME`), and the carrier verifies the chain of the machine it runs
  on. Both are the Mac today. If ADR-0059 moves the carrier off the Mac, the two must still read
  one chain.
- **The cycle's emitting identity.** Which registry agent id the cycle sets as `FACTORY_AGENT_ID`,
  and whether `factory_events` requires it to resolve in security-standards' registry.
- **The carry form's plan.** The carry form accepts only the issue-plan re-render and doesn't
  read `last_rotated`, leaving a row rotated since the approval to the executor's issue-fact
  check. Whether that's enough, or the carrier needs infraops' `last_rotated` too, is settled in
  increment 3.
- **The carrier's identity.** Which agent id the carrier takes, whether it keeps the SYSTEM role
  for intake or holds two credentials, and how the rotation executor's rewrite of
  `ORCHESTRATOR_M2M_CREDENTIALS` treats the new entry, are settled in increment 3.
- **Generator drift.** The re-render uses the generator at the orchestrator revision the command
  runs at. A wording change in `generate.py` after a package was generated makes the re-render
  differ, which refuses the standing approval and leaves the revision for Devon. That fails
  closed, and it's the expected cost of a wording change.
- **Ping URLs as configuration (related, separate).** The 2026-11-10 standing answer that a
  Healthchecks ping URL is configuration means a later amendment has the SDS read a check's
  `ping_url` from the Healthchecks API, with no paste. If that retires the `healthchecks-ping-url`
  type's issue package, item 2's type condition narrows to `bws-lane-token`; this amendment doesn't
  decide it.

### Consequences

- **Devon's acts per lane** fall to the mint and the paste; neither lane credential's revision 1
  on the issue plan needs an interactive session.
- **Threat model.** A cycle can now approve a production credential issue, but only for a package
  a machine reproduces exactly from the registry, for two types whose issue writes nothing until a
  person pastes a value. That holds whichever door the revision enters by, because the
  decomposition gate admits the policy approver only beside the carrier identity's record of its
  checks. A caller holding that identity's credential could forge the record, so the credential
  must be held as narrowly as the rotation executors' credentials. Anyone able to change a
  registry row can already make the proposer generate a revision 1 for it; the paste stays a
  person's, and the executor's pre-claim check that registry, package and envelope destinations
  are equal still refuses a mismatch.
- **No person reads a lane credential's revision 1 destinations.** Amendment 2 had Devon read
  them before approving, and T4's defence against a hostile registry consumer rested on that
  read. For these revisions the registry is the only source, so an actor who can land a registry
  row can choose where a lane credential is written. The grant's per-type destination kinds
  (item 2) limit the choice to one keeper and, for a lane token, one Coolify variable that is
  never redeployed. This residual is accepted under D-34.
- **Code Brain rule 4.** Every condition refuses on error, and the command exits 2 for anything
  it can't read. The carrier refuses unless its carry form refuses nothing. The decomposition gate
  runs none of the checks: it refuses unless the carrier's record of them is present, recorded by
  the configured carrier identity, and bound to the revision's package hash, so an absent,
  mismatched or unreadable record sends the revision to a person.
- **Unchanged.** The executor, the decomposer, the verifier, every generated package's bytes, the
  inbox binds, and every revision other than a lane credential's revision 1 on the issue plan.
  New: the carrier's identity, its observation type and source system, and their migration
  (increment 3).

## Amendment 23 — The executor reads a lane check's ping URL from the Healthchecks API (2026-11-10)

**Status: Accepted (2026-11-10).** Devon accepted it from Slack ("G-25: approved", recorded
2026-11-10 17:04 UTC), after a clean Opus whole-branch review of PR #649 at `9b54674d` (HQ's D-12
standing answer, limit 3). It rests on Devon's standing answer of 2026-11-10 (sds-hq
`standing-answers.md`, "a Healthchecks ping URL is configuration, not a secret"):

> This is still in development. There is almost zero risk of a public URL being used against me,
> and its not really a secret that could not be found either. Please make the change so that I am
> not copy/pasting a public URL between web pages.

The rule recorded with it: while the estate is in development, a Healthchecks ping URL is
configuration. Devon never copies one by hand: the SDS reads the check's `ping_url` from the
Healthchecks API and writes it where a lane needs it, with no mint act and no inbox paste. It
needn't be kept out of a transcript or log, though nothing has a reason to print it. It's revisited
when the estate leaves development.

Code reads cite orchestrator `6d32e881` and Healthchecks `v4.4`, the self-hosted instance's image
tag.

### Context

Amendment 19 made a ping URL a person's mint: Devon copies it from the check's page and pastes it
into the VM inbox, which binds it (items 5 and 8, step 5), and amendment 21 gave the bind and
`verify_new` a read-only lookup of the check. As built:

- **The type says a person mints.** `healthchecks-ping-url` declares `machine_mint=False`
  (`rotation_worker/types.py`, lines 56-59). `derive_issue` refuses an issue for a type that mints
  by machine (`plans.py`, line 135) and always opens the plan with a person's act (line 144), and
  `validate_issue_plan` refuses any other first segment ("a person mints and stages an issue",
  `contract.py`, line 334). The adapter's `mint` raises ("a person copies a ping URL from its
  check's page", `adapters/healthchecks_ping_url.py`, lines 123-124).
- **The value arrives only through the inbox.** The unit after a person's mint waits unclaimed
  until a value is staged (`worker._mint_waits`, lines 630-665), raising the mint act each pass
  until then (`_mint_act`). The VM has no hand path: `rotation-worker stage` refuses on the VM
  ("only the Keychain executor stages by prompt", `cli.py`, line 304). A pasted URL binds through
  `BindsByItself.bind_refusal` (`adapters/healthchecks_ping_url.py`, lines 94-116): the marker in
  the keeper, the shape, the random-UUID control answering 404 and the URL's lookup answering 200;
  `inbox.bind` then refuses one another keeper holds (`inbox.py`, lines 129-165).
- **Only a read-write key is shown `ping_url`.** `Check.to_dict` (Healthchecks `v4.4`,
  `hc/api/models.py`, lines 439-484) adds `unique_key` for a read-only key (lines 471-472) and
  otherwise `uuid` and `ping_url`, the instance's `PING_ENDPOINT` followed by the check's UUID
  (lines 474-475). The listing (`get_checks`, `hc/api/views.py`, lines 414-435) and the single-check
  reads (lines 486 and 495) all pass the key's `readonly` flag. The hosted Management API docs
  ("List Existing Checks") say the same: a read-only key's responses omit `uuid`, `ping_url`,
  `update_url`, `pause_url`, `resume_url` and `channels`. So the executor's read-only key
  (`f07b1368-…`, `Rotation / Healthchecks`, `healthchecks_key_id`) can't read a URL.
- **The read-write key is the project's Management key.** It's `51509d5e-…`
  (`healthchecks_selfhosted_api_key`), which `.bws-secrets.toml` (lines 174-177) places in BWS
  project `Ops / Platform`, the key `sds_upsert_check` creates and updates checks with (ADR-0059,
  "A lane holds only its own check's ping URL"). It manages every check in its project. The VM
  root reads `Rotation / Healthchecks` and no `Ops / Platform` secret
  (`docs/operations/rotation-executor.md`, the access table), so the executor holds no grant on it.
- **The worked example.** The landing ledger's ping URL (HQ gate G-20, change record 198, unit
  `2695999c-fae9-5454-ba4e-83e04f0ca028`) was read by an interactive session from that key's
  listing and pasted into the inbox by Devon. The pass that bound it logged a fingerprint equal to
  the one the session computed from the API (sds-hq `state/progress.md`, 2026-11-10 ~12:55 UTC).
  The paste added nothing the API didn't already hold.

### Decision

1. **Who reads `ping_url`, and with which key.** Three choices; this amendment recommends (a) and
   grants nothing ("Open before building" asks Devon).
   - **(a) The VM executor's mint, with the Management key (recommended).** The type mints by
     machine, and its mint reads the lane's check from the Management key's listing and takes its
     `ping_url`. The executor already holds every lane's ping-URL keeper and can blind any lane by
     overwriting it (amendment 19, Consequences), and it runs the issue plan's invariants, evidence
     and verifier step. It needs one new read for the executor, and no new resource.
   - **(b) A narrower key, in a Healthchecks project holding only the lane checks.** Its
     read-write key manages only the lane checks, which is close to the reach the executor already
     has over the lanes' alarms. It costs more than (a): moving a check keeps its UUID and history,
     because `transfer` saves only `project` (Healthchecks `v4.4`, `hc/front/views.py`, lines
     1063-1064), but it then reassigns the check's notification channels to the target project's
     (`assign_all_channels`, line 1065). The read-only lookup searches only its key's project
     (amendment 21), so the lookups and the scheduled readers' check reads would need that
     project's read-only key too, and each lane's `sds_upsert_check` would need its read-write key.
     A new key, a new BWS secret and a check move per lane are more than the standing answer's risk
     calls for. It's the narrowing named for the revisit (item 5).
   - **(c) The lane setup, in the deploy command's phases.** The deploying session reads `ping_url`
     and writes the value. Only the root writes `Rotation / Keepers` (amendment 19 item 1), so the
     session either writes the inbox, leaving a person's mint in the plan and its alert, with a
     session doing the paste, or is granted write on the keepers and skips the executor's issue
     invariants and evidence. HQ holds no grant on the Management key either (ADR-0059, "A lane
     holds only its own check's ping URL"). Not recommended.
2. **What replaces the inbox paste, under (a).** For a `healthchecks-ping-url` issue:
   1. HQ makes the ping URL's row due, as today. The proposer generates revision 1 and a standing
      approval approves it (amendment 22).
   2. **Before the claim**, the executor reads the Management key's listing (`GET
      /api/v3/checks/`, over HTTPS at `https://healthcheck.alobar.net`) and finds the one check
      whose name is the row's `healthchecks_check` field. Each claimed step fails the unit and
      spends an attempt on any exception (`src/rotation_worker/CLAUDE.md`), so a step that must
      wait waits before the claim, as `_mid_run_waits` does (`worker.py`, lines 544-562). No
      answer, or a status other than 200, is `ReadUnavailable`: the unit waits unclaimed, logged
      and on the 48-hour wait clock; so is a key BWS won't give the root (`StoreError`, which the
      executor treats as transient, `worker.py`, line 119). No key configured, no field on the row, no check or two
      checks of that name, a `ping_url` not of the self-hosted ping shape (`SHAPE`), or a URL
      another `healthchecks-ping-url` keeper holds is `ReadError`: the unit is refused before the
      claim, and nothing is written. Neither case passes.
   3. **The claimed unit's `mint` and `stage`** take the URL from that pass's listing, which the
      client keeps for the pass (`readers.Healthchecks._checks`, lines 215-219), so nothing is read
      again after the claim. `contract._mint` (lines 609-626) and `_stage` are unchanged: the old
      value is the marker, a mint equal to it is refused, and a retry re-proves what an earlier
      attempt staged.
   4. **`verify_new`, `store` and `verify_consumers`** run as amendments 19 and 21 say. No mint act
      is raised and the inbox isn't read for this type.

   **`verify_new` keeps amendment 21's lookup, unchanged**: the shape, then the random UUID's
   `unique_key` answering 404 and the URL's answering 200, with the read-only key. It isn't
   subsumed. It keeps decision 3's R14 rule with no new exception, keeps the Management key to the
   one listing read before the claim, and keeps the evidence and the verifier's decision as built.
   The lookup still reaches only the UUID's first half, but on this path nobody types the URL:
   Healthchecks builds `ping_url` from its own `PING_ENDPOINT` and the check's UUID, so the gap
   amendment 21 named (a URL wrong only in its second half) has no source. Cut-over step 4 stays
   the proof that the lane's container pings that check.
3. **What it narrows.** These are **narrowed, not withdrawn**:
   - **Amendment 19 item 5:** a ping URL is no longer pasted or bound from the inbox; it's read by
     the executor's mint (item 2). The marker, the shape, no other keeper holding the URL, and
     never sending the URL anything stand.
   - **Amendment 19 item 8:** the ping URL's unit no longer waits on a person's mint, so
     `other_mints` (`inbox.py`, lines 111-126) never lists it, and the order that kept the two units
     from waiting together is no longer needed. HQ still makes the row due, and may make both rows
     due in one change. Step 5 becomes: HQ makes the ping URL's row due, and the executor reads and
     writes the ping-URL secret.
   - **Amendment 19 item 9's "Devon mints every lane token"** is unchanged; its sense that a person
     mints every lane credential no longer covers ping URLs.
   - **Amendment 21 decision 1:** the bind it describes is retired with the paste. Decisions 2 to
     6 stand.
   - **Amendment 22 decision 6:** "or pastes each ping URL, from the alert" is gone, and so, for a
     ping URL, is its statement that an approved revision 1 with no paste changes nothing in
     production: the executor's read is the value. The type condition of its item 2 is unchanged,
     because the ping URL keeps its generated issue package. The related open item ("Ping URLs as
     configuration") is settled by this amendment without narrowing that condition.
   - **Amendment 22's Consequences ("Threat model"):** its bound, "two types whose issue writes
     nothing until a person pastes a value" and "the paste stays a person's", now holds for the
     lane token only. This amendment's Consequences state the path for a ping URL.
   - **The mint alert** (`rotation-mint`, `_mint_act`) is no longer raised for a ping URL.

   A lane's credentials then need a person for one act: the token's mint and paste, from its alert,
   with amendment 22's standing approval in place.
4. **Logging.** The standing answer lets a ping URL appear in a log; nothing has a reason to print
   one, and Infra Brain rule 2890 still forbids committing one. The executor logs as it logs today,
   structured JSON on stdout (`rotation_worker/logs.py`): `ping_url_read` names the unit, the check
   by its name and the URL's fingerprint (SHA-256 prefix and length), and `ping_url_unreadable`
   names the unit, the check and the error's type. The URL is held only as a `Value` and never in a
   message, evidence or exception text. The listing's other fields (other checks' URLs,
   `update_url`, `pause_url`) are dropped as the response is parsed, and the key is never logged.
   Evidence is as amendment 21 decision 4 says: a 12-character `unique_key` prefix and two statuses.
5. **Revisit trigger.** The standing answer holds only while the estate is in development. When it
   leaves development, the ping URL is treated as a secret again, and the path falls back in one of
   two ways, which Devon chooses then:
   - **To the paste.** The executor's grant on the Management key is withdrawn, the key's id
     leaves the root's `config.toml`, and the type's capability goes back to a person's mint,
     restoring amendment 19 item 5 and amendment 21 decision 1 by reverting increment 1. Between
     those acts nothing is written: with the grant gone and the id still configured, the key's
     read is a `StoreError` and the unit waits, raising the wait alert; with the id removed, every
     ping-URL unit is refused before the claim ("no key configured"). The withdrawal fails closed,
     never into a silent paste.
   - **To option (b),** keeping the machine read with a key narrowed to the lane checks.
6. **Code impact, as later increments.** None is built by this amendment ("Increments").

### Increments

They ship after Devon's grant, in this order; 1 and 2 land together, because the issue package's
bytes are pinned with intent-packages.

1. **orchestrator: the machine-read mint.**
   - `rotation_worker/types.py` (lines 56-59): `healthchecks-ping-url` mints by machine.
     `generate.authority` (`rotation_proposer/generate.py`, line 208) adds `credential_create` for
     every machine mint, but this mint creates nothing and its client can't create a check, so a
     `Capabilities` field says whether a machine mint creates, and the type's authority stays
     `secret_read` and `secret_write`. Nothing else requires `credential_create` for a machine
     `mint` step: `generate.py` (lines 53 and 209) is its only use under orchestrator `src/`, and
     intent-packages `c5c9225` names it only in package data. The capability vocabulary isn't
     widened, and amendment 22's authority set for the type is unchanged.
   - `plans.derive_issue` (lines 135 and 144) derives a machine-minted issue: one unit of `mint`,
     `stage`, `verify_new`, `store` and `verify_consumers`, each with its criterion.
     `contract.validate_issue_plan` (lines 313-336) accepts that shape beside today's.
   - `adapters/healthchecks_ping_url.py`: `mint` (lines 123-124) returns the URL found in the pass's
     listing; `bind_refusal` (lines 94-116) and the module docstring's inbox text go.
   - `readers.py`: a second `Healthchecks` client on the Management key whose `_permits` (the
     pattern at lines 250-255) allows only `GET /api/v3/checks/`. `config.py` gains
     `healthchecks_management_key_id` (beside `healthchecks_key_id`, lines 83 and 124), read only
     while a ping-URL unit is about to be claimed or runs.
   - `worker._take` (lines 428-470): the pre-claim read of item 2, step 2, beside `_mint_waits` and
     `_mid_run_waits`.
   - `registry.py` and the checks: a ping-URL row must carry `healthchecks_check`, refused before
     the claim otherwise; the landing ledger's and execution-watch's rows gain it.
   - `rotation_proposer/generate.py` (`acceptance`, line 156): criteria for the machine mint and
     stage.
   - Docs in the same change: `docs/operations/rotation-executor.md` ("Lane tokens and ping URLs"
     and the access table), `src/rotation_worker/CLAUDE.md` (the inbox and issue bullets),
     ADR-0059's ping-URL lines ("A lane holds only its own check's ping URL" and "Cut-over for one
     lane", step 1).
   - Tests: the pre-claim wait and each refusal, a mutation that drops each, and a listing whose
     log output holds no URL.
2. **intent-packages: the issue package.** The pinned `rotation_package_issue_healthchecks-ping-url.json`
   fixture and `create-rotation`'s issue scaffold learn the machine-minted issue.
3. **VM configuration.** After Devon's grant, the root's `config.toml` names the key's id, and the
   key's registry consumer rows list the executor as a reader.

An open ping-URL package written for the person's mint plan matches no derived plan once
increment 1 lands, and the executor refuses it, so increment 1 lands only while no ping-URL
package is open.

### Open before building

- **The grant (Devon's).** Grant the VM rotation executor's BWS machine account (`rotation-executor`,
  the root) a direct read on BWS secret `51509d5e-64f5-4781-ae82-b4ed00b644e1`
  (`healthchecks_selfhosted_api_key`, the self-hosted instance's Management key), in project
  `Ops / Platform` as `.bws-secrets.toml` (line 177) lists it? It would be the root's first
  `Ops / Platform` read. The project is confirmed in BWS before the grant: if the key is in fact in
  `Rotation / Healthchecks`, which the root already reads, the question is whether the executor may
  use it, not a new grant. Copying the key into `Rotation / Healthchecks` instead would be a second
  copy of a secret, which its other consumers would have to track. Under (b) the question is a new
  key instead, in a new project.
  Answered yes by Devon (sds-hq D-36, "yes", from Slack, recorded 2026-11-10 17:14 UTC). The grant
  itself is a person's act in BWS, done before increment 3 reads the key; this item's check
  (confirm the key's project in BWS first) still applies.
- **Whether the package names the check.** The `healthchecks_check` field sits in the registry,
  beside `note`, outside the three-way destination comparison, like the scheduled readers' fields
  (`readers.py`, module docstring), and registry rows merge without review. So amendment 22's
  residual, that an actor who can land a registry row chooses where a lane credential goes, extends
  to which check's URL a lane gets. A wrong name points the lane at another check, and the lane's
  own check goes overdue, its dead-man alarm. Increment 1 settles whether the generator writes the
  check name into the package's criteria, so the standing approval's re-render and any person's
  read cover it.
  Settled by increment 1: the generator writes the row's `healthchecks_check` into the `mint`
  criterion's condition, never its evidence text (`rotation_proposer/generate.py`, `_READS_CHECK`
  and `_read_check`), and refuses a ping-URL row that names no check.
- **The key's registry rows.** Neither Healthchecks key has a row in this repository's
  `.cred-consumers.toml`; increment 3 finds where each is registered and adds the executor there.
  Increment 3 found (2026-11-10) that neither key (`51509d5e-…`, `f07b1368-…`) has a
  `.cred-consumers.toml` row in any registry file `~/.config/infra-drift/cred-consumers.list` names
  (infraops-mcp-server, change-manager, factory-runner, orchestrator), so it added the executor to
  `.bws-secrets.toml`'s reader comment only; whether the keys get registry rows is open.

### Consequences

- **Threat model.** The standing answer covers the URL, not the key. Under (a) the VM root gains
  the Management key, which can create, change, pause and delete every check in its project, not
  only the lanes'. ADR-0059 says that key "is no lane's credential", and D-28's "No Key" was lifted
  only for the read-only lookup (amendment 21). The executor's client allows only the listing
  `GET`, but a compromised root isn't bound by its client: it could silence every self-hosted
  check, where today it can silence only the lanes'. Devon accepted that widening by answering the
  grant yes (sds-hq D-36); (b) is the narrower option.
- **The path with no person, for a ping URL.** With amendment 22, a registry row that merges
  without review becomes, through the proposer's revision 1, the standing approval, the carrier's
  record and the executor's read, a write to the row's keeper with no person in the path. Its
  bounds: the keeper must hold the marker (an issue only, never a rotation); amendment 22's grant
  limits the destinations to one keeper; and the value is only what the Management key's listing
  gives for the named check, of the self-hosted ping shape, that the read-only lookup finds in the
  key's project. The worst a hostile or mistaken row can do is point a lane at another check in
  that project, and the lane's own check then goes overdue, its dead-man alarm.
- **Code Brain rule 4.** A failed read waits or refuses before the claim, logged, and nothing is
  written; nothing passes on an unread listing.
- **Devon's acts per lane** fall to the token's mint and paste.
- **Unchanged.** The Keychain instance, the lane token's type and binds, the issue-fact check, the
  verifier, amendment 21's lookup and its evidence, and every other type.

## Amendment 24 — The factory-events chain of record is a VPS database (2026-11-13)

**Status: Proposed (2026-11-13).** Accepted on merge. HQ merges it only after a clean Opus
whole-branch review (HQ D-12, limit 3). It closes amendment 22's open item "The chain the carrier
reads". It rests on four of Devon's answers, all given in interactive HQ sessions on 2026-11-13 and
recorded in sds-hq `state/decisions.md`:

- **D-40 B** (11:40 UTC): "Approvals write their package.approved event straight into the shared
  factory-events database chain, and the carrier verifies there; append-only credentials for the
  approvers, read-only for the carrier."
- **D-42 A** (13:37 UTC): "A new Postgres resource on the VPS (Coolify, Flavor B pattern) becomes
  the factory-events chain of record: approvers append each event through a new append function in
  factory_events (append-only role), and the work carrier reads it from the VPS (read-only role).
  The Mac nightly factory-events job ships its other events into the same VPS chain, so there is
  one chain of record, and the Mac database retires once it does."
- **D-43 B** (about 17:03 UTC): only the approvers (Devon's `approve`, the cycle's
  `standing-approve`, the bump proposer's `approve --by-policy`) and the Mac nightly hold the
  append-only role. The moved rotation proposer holds no chain credential: its `revise` and
  `transition` write no chain event and record `event_id` None in the lineage, as a failed emit
  does today.
- **D-44 A** (about 17:03 UTC): the read-only role also serves Devon's hand paths on the Mac
  (`orchestrator intake-package`, the `factory` journey's `emit-intake-payload`, and
  intent-packages `verify-approval` run by hand), not only the work carrier. It can't append, so
  no write path widens. **Superseded by amendment 25:** D-47 revised it to D-44 B, so only the
  work carrier reads the chain.

ADR-0059 amendment 7 brings factory-events into Phase 1 to the same extent and orders the
increments. Code reads cite orchestrator `2cf43757`, intent-packages `78bb302`, security-standards
`1baec77` and sds-hq `e5c4547`.

### Context

**One emitter writes every lifecycle event, and only `approve` stops on a failed emit.** Each
intent-packages lifecycle command passes `FactoryEventsEmitter` (`cli.py`, lines 129, 171, 189 and
203), which runs `python -m factory_events emit` as a subprocess with the actor from
`FACTORY_AGENT_ID` (`emitter.py`, lines 87-128). `do_approve` emits `package.approved` before it
writes the lineage, and an `EmitError` aborts it with nothing written (`operations.py`, lines
248-249 and 292-305). `do_transition` and `do_revise` catch `EmitError` and record `event_id`
None (lines 158-165 and 351-358).

**Three callers emit `package.approved`, all through `intent-packages approve`.** No other code in
the three repositories emits the action:

| Caller | Where | Approver |
|---|---|---|
| Devon's `approve` | intent-packages `cli.py`, `_run_approve` (lines 137-180), calling `do_approve` (line 169) | `devon` |
| The cycle's standing approval | sds-hq `src/sds_hq/standing_approve.py`, `approve` (lines 540-547), with `FACTORY_AGENT_ID` `lane-credential-policy` (line 36) | `policy:non-software-operational@v<N>` |
| The bump proposer | orchestrator `src/bump_proposer/standing.py`, line 397 (`approve --by-policy`) | `policy:dependency-update@v<N>` |

The event log shows who runs Devon's `approve`: interactive Claude Code sessions, on his word, as
actor `claude-code-interactive`, with approver `devon` (amendment 25, "Consequences"). Under
D-45 A as revised by D-47, they fetch the append DSN through `factory-events-session`
(amendment 25 item 3).

**Four readers match it.** intent-packages' `_events_file_has_matching_approval` (`operations.py`,
lines 476-508, the match at 503) under `default_chain_checker` (511-530) and `verify_approval`
(533); the orchestrator's `_verify_factory_chain` (`package_sources.py`, lines 231-253),
`_iter_package_approval_events` (276-296, the match at 294) and `_verified_approval_from_events`
(337-361), reached through `_verify_current_approval` (364-395), which also runs intent-packages'
`verify-approval` as a subprocess (line 168). Only `orchestrator` CLI commands import
`package_sources` (`cli.py`, line 13): the carrier's `emit-intake-payload` (`work_carrier/prepare.py`,
lines 117-126), a person's `intake-package` (`cli.py`, line 254) and the `factory` journey
(intent-packages `factory/journey.py`, lines 141-148). `work_carrier/standing.py` (lines 16-22)
trusts the payload the emitter verified and reads no chain itself.

**The chain is a file on each machine.** `store.append_event` (security-standards
`factory_events/store.py`, lines 60-80) appends one line under an `flock`, linking it by
`seq` and `prev_hash`; a line's `hash` is SHA-256 over the canonical JSON of `seq`, `prev_hash`
and the event (lines 32-35). `verify_chain` (122-155) walks every line. The path is
`$FACTORY_EVENTS_HOME/events.jsonl` (lines 20-25), and both readers resolve it the same way
(intent-packages `operations.py`, line 419; orchestrator `package_sources.py`, lines 226-228).
In a VPS container that file is empty, so a moved carrier would refuse every intake.

**The Mac's database is a projection with one owner.** `ship` (security-standards `ship.py`,
lines 67-102) bulk-inserts the file into `factory_events` once a night, runs its DDL on every call
(lines 60 and 72), may `TRUNCATE` (line 74), and anchors the file's head in `chain_heads` (lines
3-5 and 100), which `verify --against-anchor` reads (`cli.py`, lines 47-60). It connects with
`FACTORY_DB_DSN`, the database's one role (`deploy/factory-events-db/docker-compose.yml`, lines
7-11, port 5545 on the Mac's OrbStack host engine). It has no single-event append and no verify of
its own.

### Decision

1. **The chain of record is a Postgres database on the VPS.** A new Coolify database resource on
   `prod-vps` (Flavor B pattern: a separate database resource) holds the factory-events chain.
   Postgres is its primary store; no cache or queue holds an event that the database doesn't.
   After the cut-over (ADR-0059 amendment 7, step FE-5), no writer and no verifier uses a local
   file.
2. **The table links events as the file does.** Each row carries `seq`, `prev_hash`, `hash` and
   the event, and `hash` uses `store._line_hash`'s formula unchanged. `event_id` is the primary
   key, and `seq` and `prev_hash` are each unique. A trigger refuses every `UPDATE`, `DELETE` and
   `TRUNCATE` on it, whatever the role. Because the formula is unchanged, a record imported from
   the Mac's file keeps its `seq`, `prev_hash` and `hash`, and one walker verifies both. Who
   runs that import, and under which role, is Devon's named decision ("Open before building").
   Amendment 25 item 4 names them (D-46 A).
3. **Three roles, two of them held.**
   - **Append-only role.** `SELECT` and `INSERT` on the chain table, nothing else: no `UPDATE`,
     `DELETE`, `TRUNCATE` or DDL. It reads the head it links to.
   - **Read-only role.** `SELECT` on the chain table, nothing else.
   - **The owner.** The resource's own superuser, kept in Coolify. Nothing holds it at run time.
     It's used only in the announced acts that set up the schema and the two roles (ADR-0059
     amendment 7, step FE-4). Amendment 27 names one more, for an FE-5 import retry only (D-51 A);
     amendment 28 withdraws it (D-53 C).
4. **Who appends, through what.** A new `factory_events` append function writes one event under
   the append-only role. Its holders are exactly these (D-43 B):
   - **Devon's `approve`**, on the Mac.
   - **The cycle's `standing-approve`** (sds-hq), on the Mac.
   - **The bump proposer's `approve --by-policy`**, on the Mac and, after ADR-0059 step 3f-4, in
     its container.
   - **The Mac nightly**, whose adapters append the high-power and change-manager events through
     the same function (ADR-0059 amendment 7).

   Each reaches the function through the intent-packages emitter, unchanged in shape, or the
   nightly's `adapt`. Because the emitter serves every lifecycle command, a holder's `transition`,
   `revise` and `supersede` events land in the chain too.
5. **What a non-holder's lifecycle command does.** The rotation proposer runs `revise` and
   `transition` (`rotation_proposer/standing.py`, lines 281 and 290) and never `approve`. It holds
   no chain credential (D-43 B), on the Mac after the cut-over and in its container after ADR-0059
   step 3f-5. Its emits fail, and `do_transition` and `do_revise` record `event_id` None in the
   lineage, as a failed emit does today. The chain then holds none of that lane's
   `package.transitioned` or `package.revised` events. No verifier reads either action. The same
   holds for any other caller that runs a lifecycle command with no append credential.
6. **How an append keeps the chain linear.** The append function runs one transaction: it takes a
   transaction-level advisory lock that every append takes, reads the row with the highest `seq`,
   inserts the next row (`seq` plus 1, `prev_hash` the head's `hash`), and commits, which releases
   the lock. Two concurrent appends therefore run one after the other. A writer that skipped the
   lock and linked to the same head would break the `seq` or `prev_hash` uniqueness, so its
   insert fails rather than forking the chain. An event whose `event_id` is already present fails
   on the primary key.
7. **An append failure writes nothing.** Any failure is an append failure: the database
   unreachable, a refused role, a lock or uniqueness conflict, or any exception. The function
   rolls back, logs it, and raises, so `factory_events emit` exits non-zero, the emitter raises
   `EmitError`, and `do_approve` aborts before the lineage is written, as it does today. There's
   no retry into another store and no fallback to a file. The person or lane runs `approve` again.
8. **How the carrier verifies.** Each verifier reads under the read-only role in one read-only
   transaction at repeatable read, so the walk and the match see one snapshot. It walks every row
   in `seq` order from 1, recomputing each `hash` and validating each event as `verify_chain` does
   today, and refuses on the first break. Then it looks for the matching `package.approved` event:
   - intent-packages' `default_chain_checker` matches hash and revision, as it does today.
   - The orchestrator's verifier matches event id, package id, hash, revision, approver and
     commit, as `_approval_event_to_verified` does today (`package_sources.py`, lines 299-330).

   An unreachable database, a refused role or any error is not a "no": intent-packages raises
   `ChainUnavailable`, so `verify_approval` returns False (lines 586-592), and the orchestrator
   raises `PackageSourceError` ("approval verification failed: ..."). The carrier then refuses the
   record with `package_not_intakeable`. A verified chain with no matching event refuses as today.
   No verifier falls back to a file.
9. **Who reads.** The read-only role's holders are the work carrier, on the Mac until ADR-0059
   step 3f-2 and in its container after, and Devon's hand paths on the Mac (D-44 A): `orchestrator
   intake-package`, the `factory` journey's `emit-intake-payload`, and intent-packages
   `verify-approval` run by hand. Each carry makes three chain reads (the two orchestrator reads and
   intent-packages' subprocess), and all three use the read-only role. **Superseded by amendment 25
   item 1 (D-44 B):** the work carrier is the only holder, fetching through `sds-operator` on the
   Mac until step 3f-2, and a hand intake fails closed after the cut-over.
10. **What the local file becomes.** At the cut-over, the Mac's `~/.factory/events.jsonl` is
    imported into the VPS chain (item 2, by the actor and role Devon names) and then sealed: renamed
    to an archive name, made read-only, and kept in the Mac's nightly backup as the record of the
    chain before the cut-over. It's no longer written and no longer a cache; a cache a verifier
    could fall back to would be a second chain, and a fallback on an unreachable database would fail
    open. The cut-over's last step lands the changes that remove the file path from
    `factory_events`' `emit`, `adapt` and `verify`, from intent-packages' chain checker, and from
    the orchestrator's verifier (ADR-0059 amendment 7, step FE-5). Amendment 25 item 4 names the
    import's actor and role (D-46 A).
11. **The credentials.** Two BWS secrets, each a DSN for one role. Both are **to be created** in
    step FE-4 and recorded by UUID, never by name. Each secret's BWS project, and each BWS
    machine account granted a read of it (on the Mac and in each lane container), is Devon's
    named decision, taken before ADR-0059 amendment 7's act 3 runs. This amendment chooses none
    of them, and act 3 stops until he has named them ("Open before building", "Each DSN's BWS
    project and reading accounts"). The append DSN can't go to a shared or broad identity without
    his decision, because every process that identity serves could then append, which widens D-43
    B's holders. Two such identities are the nightly's `BWS_ACCESS_TOKEN_BROAD` and its Keychain
    item `BWS_ACCESS_TOKEN_VPS_BACKUP` (security-standards `factory-events-nightly.sh`, lines
    104-105). The table's "Read by" column names the processes D-43 B and D-44 A give each role,
    not the accounts that fetch it. **Amendment 25 names them:** the project in its item 2 and the
    accounts in its item 3 (D-45 A as revised by D-47). Its item 1 drops Devon's hand paths from
    the read DSN, and the append DSN's "Devon's `approve`" reads through `factory-events-session`.

    | Secret (to be created) | Role | Read by | `.bws-secrets.toml` | Registry row |
    |---|---|---|---|---|
    | The append DSN | Append-only | Devon's `approve` on the Mac; the sds-hq cycle; the bump proposer lane; the Mac nightly | orchestrator (the bump proposer lane), security-standards (the nightly), sds-hq (the cycle) | One row in orchestrator `.cred-consumers.toml` listing every reader |
    | The read DSN | Read-only | The work carrier lane; Devon's hand paths on the Mac (D-44 A) | orchestrator (the work carrier lane) | One row in orchestrator `.cred-consumers.toml` listing every reader |

    No existing rotation class fits either secret, so neither row names a class yet ("Open before
    building"). The resource's owner password stays in Coolify and isn't a lane's credential.
12. **Network.** The database has no public FQDN and no public port. The work carrier's and the
    bump proposer's containers reach it over Coolify's internal network, joining the external
    `coolify` network. The Mac reaches it over the tailnet, at `prod-vps`'s tailnet address
    (`ovh-vps1`), through a port reachable only on the tailnet ("Open before building").
13. **Logging.** The append function and both verifiers log structured JSON to stdout, never to a
    file. A record names the event id, the `seq`, a prefix of the head hash, the outcome, and an
    error's type. No record holds the DSN, a password or a role's credential, and an error's text
    from the driver isn't logged, because a connection error can repeat its DSN. Today `emit`
    prints the bare event id on stdout (security-standards `cli.py`, line 33) and `seq` and the
    head hash on stderr (line 34). The emitter's `_parse_event_id` (`emitter.py`, lines 40-67)
    finds no JSON line with an `event_id` (lines 52-58) and takes the last whitespace-delimited
    token of the last stdout line (lines 63-67). A JSON log record on stdout that carries an
    `event_id` key is what the emitter reads first, so FE-1 must keep `emit`'s stdout within the
    parser's contract ("Open before building", "`emit`'s stdout").
14. **Schema setup.** The schema, the trigger and the two roles are created by a security-standards
    script that the owner runs once, as an announced production act (step FE-4). They're never set
    up in an image build step, and neither the append function nor a verifier runs DDL; `ship`'s
    per-call DDL (lines 60 and 72) isn't carried over.
15. **No exposure.** Nothing in this amendment declares a credential exposed (Infra Brain rule
    1032). Both secrets are new, and none exists to rotate.
16. **What it grants, and nothing more.** The two roles and their holders in items 3, 4 and 9, as
    D-40 B, D-42 A, D-43 B and D-44 A give them. No other identity gets either credential, and the
    owner is held by no one at run time. It names no BWS project, no machine account and no
    import actor: each is Devon's named decision before the act that needs it. **Superseded by
    amendment 25:** D-44 B replaces D-44 A, and amendment 25 names the project, the accounts and
    the import actor.

### Increments

The order and the production acts are ADR-0059 amendment 7's: FE-1 (security-standards: the store,
the import, the roles script), FE-2 (intent-packages: the emitter and the chain checker), FE-3
(orchestrator: the verifier and the bump proposer's DSN), FE-4 (the database resource, its schema
and roles, its two secrets and its backup), FE-5 (the cut-over: the import, the nightly's target
switch, every holder's DSN, the seal and the file path's removal) and FE-6 (the Mac database's
retirement). The work carrier's lane code (ADR-0059 step 3f-2) follows them. FE-4's act 3 waits
for Devon's named decision on each DSN's BWS project and reading accounts, and FE-5's import for
his named decision on its actor and role ("Open before building"). Amendment 25 records both
decisions, so neither act waits for them.

### Open before building

Each item names the step that settles it.

- **Where the anchor lives (FE-1).** `chain_heads` in the Mac database evidences a rewrite of the
  file (`ship.py`, lines 3-5). Once the chain of record is a database, an anchor in the same
  database guards nothing against its owner. FE-1 decides where the head is anchored outside it
  and how `verify --against-anchor` reads it. One candidate is the head the nightly already
  reports in its `chain:factory-events` observation (`factory-events-nightly.sh`, line 67). FE-6
  waits for it.
- **The adapters' dedupe (FE-1).** The adapters derive event ids deterministically
  (`high_power.py`, line 93; `change_manager.py`, line 130) behind file watermarks. FE-1 checks that
  a re-run against the database skips an event it already holds rather than failing the night on
  the primary key.
- **The verify walk's cost (FE-1).** Each carry walks the whole chain three times. The Mac file held
  3,751 events on 2026-11-13. FE-1 measures the walk against a copy that size and states the bound;
  a walk that ends at an anchored head is the narrowing if it's needed.
- **A rotation class for each secret (FE-4).** The nearest class, `postgres-role-password` (type
  `postgres-role`, `rotation_worker/types.py`, lines 46-48 and 76), rotates a role whose consumers
  are one Coolify service's containers, through the VPS helper and a restart
  (`adapters/postgres_role.py`, lines 9-11 and 13-20). These readers include Mac processes that
  read the DSN from BWS fresh each run. FE-4 decides whether that type fits or a new one is needed;
  a new type is its own amendment, and until one is decided each row is hand-rotated.
- **The tailnet-only port (FE-4).** Coolify's public port for a database binds every interface. FE-4
  confirms the port is reachable only on the tailnet (a bind on the tailnet address or a host
  firewall rule) before any secret is created; if neither holds, the act stops for a decision.
- **Each DSN's BWS project and reading accounts (FE-4, Devon's named decision).** **Answered:**
  D-45 A as revised by D-47 (amendment 25 items 2 and 3). The original question
  follows. For each of the two secrets: the BWS project it's created in, and every machine account granted a read of it, for
  each reader in item 11's table: Devon's shell, the sds-hq cycle and the nightly on the Mac; the
  bump proposer on the Mac until ADR-0059 step 3f-4 and in its container after (item 4); and the
  work carrier on the Mac until step 3f-2 and in its container after (item 9). Devon names each
  before ADR-0059 amendment 7's act 3 runs; until he has, act 3 stops. Neither this amendment nor HQ
  chooses them. An account that serves processes beyond item 4's holders, such as the nightly's
  `BWS_ACCESS_TOKEN_BROAD` or Keychain `BWS_ACCESS_TOKEN_VPS_BACKUP` identity (security-standards
  `factory-events-nightly.sh`, lines 104-105), widens D-43 B's holders, so it reads the append DSN
  only if his decision says so.
- **The history import's actor and role (FE-5, Devon's named decision).** **Answered:** D-46 A
  (amendment 25 item 4). The original question follows. The import (item 2; ADR-0059 amendment 7,
  FE-1 and act 5) writes the Mac file's events into the VPS chain. Item 4
  names exactly four append holders and item 3 limits the owner to FE-4's setup acts, so
  neither covers the import. Devon names who runs it and under which role, before act 5's
  import step runs; until he has, act 5 stops before the import. His decision settles how it
  stands with items 3 and 4; this amendment doesn't. Amendment 27 names the owner act a retry of
  the import needs (D-51 A); amendment 28 withdraws it, and there is no post-import retry (D-53 C).
- **`emit`'s stdout (FE-1).** Item 13's JSON logs and `emit`'s event id share stdout.
  `_parse_event_id`'s contract: the first stdout line that parses as a JSON object with an
  `event_id` key, if any, must carry the emitted id (lines 52-58); if no line does, the emitted id
  must be the last token of the last stdout line (lines 63-67). FE-1 keeps `emit`'s stdout within
  that contract, and FE-1's tests assert that the emitter parses the id `emit` printed.
- **The approval cycle's emitting identity.** Amendment 22's open item "The cycle's emitting
  identity" is settled: the cycle sets `FACTORY_AGENT_ID` to `lane-credential-policy` (sds-hq
  `standing_approve.py`, lines 36 and 542), and `make_event` refuses an actor the registry doesn't
  hold (security-standards `envelope.py`, lines 49-54 and 87). This amendment doesn't change it.

### Consequences

- **The carrier can move.** A carrier on the VPS verifies the approval its approver wrote from the
  Mac, in one chain, so ADR-0059 step 3f-2 is unblocked once FE-5 is done.
- **Every append depends on the VPS.** An approval made while the database is unreachable fails and
  writes nothing, and is run again. That holds for Devon's `approve` and for both policy approvers.
- **Threat model.** A holder of the append role can append any event, a `package.approved` naming
  any approver included: the event's `actor` is self-declared (`emitter.py`, line 92). That's
  today's position, where any process on the Mac can write the file. The role narrows it to four
  named holders and adds the database's refusal to change a row. The read role can't append.
- **The rotation proposer's lifecycle events leave the chain** after the cut-over (item 5). Its
  lineage still records each transition and revision, with `event_id` None.
- **Code Brain rule 4.** An append failure raises and writes nothing; a verify failure refuses; an
  unreachable database is never read as "not approved" or as "approved".
- **Unchanged.** The event envelope, the hash formula, `do_approve`'s order, the approval checks in
  amendment 22 and in `package_sources`, and the work carrier's refusals.

## Amendment 25 — Only the work carrier reads the factory-events chain, and its project and accounts are named (2026-11-13)

**Status: Proposed (2026-11-13).** Accepted on merge. HQ merges it only after a clean Opus
whole-branch review (HQ D-12, limit 3). It records Devon's answers to the two named decisions
amendment 24 left open, and his revision of D-44 A. Each answer was given in an interactive HQ
session on 2026-11-13 and is quoted from sds-hq `state/decisions.md` at `74c3507`:

- **D-47** (19:19 UTC) revises D-44 and D-45. The record's facts: "the Mac's
  ~/.factory/events.jsonl has 92 package.approved by claude-code-interactive, 11 by
  lane-credential-policy, and 0 by devon; Devon says he has never run intake-package or
  verify-approval by hand." Devon: "Lets update D-44 as you recommend, and I agree with your take
  on D-45." The recorded answer: "D-44 becomes B (only the work carrier reads the chain); D-45
  append-DSN reader for approvals is a new narrow account factory-events-session (own Keychain
  item, append DSN only, no read-DSN grant)."
- **D-44 B**, as D-47 sets it: "Only the carrier reads the chain; after the cut-over each hand
  intake can't verify an approval and fails closed."
- **D-45 A** (19:10 UTC), as D-47 revises it. As first answered: "new BWS project Factory Events
  with only the two DSNs; append DSN readers the SDS HQ account, Devon's shell identity, the
  bump-proposer lane account, and a new narrow factory-events-nightly account (one more Mac
  Keychain item); read DSN readers the work-carrier lane account, sds-operator for the Mac carrier
  until it moves, and Devon's shell identity." D-47 replaces "Devon's shell identity" as an append
  reader with `factory-events-session`, "used by interactive Claude Code sessions to run
  intent-packages approve on Devon's word", and, with D-44 B, gives it "no read-DSN grant". So no
  shell identity reads either DSN.
- **D-46 A** (19:10 UTC): "an interactive session runs the import once under the append-only role,
  reading the append DSN by UUID for that one command."

Line numbers for amendment 24 are this file's at orchestrator `da044d6d`, before this amendment
was appended. Code reads cite orchestrator `da044d6d` and intent-packages `97e817d`.

### What it supersedes in amendment 24

- **D-44 A** (amendment 24's fourth answer) and every rule resting on it: item 9's hand-path
  holders, item 11's "Read by" entry for Devon's hand paths, and item 16's "D-44 A".
- **The two open named decisions**: item 2's and item 10's import actor, item 11's project and
  reading accounts, item 16's "It names no BWS project, no machine account and no import actor",
  the "Increments" paragraph's waits, and the two "Open before building" entries "Each DSN's BWS
  project and reading accounts" and "The history import's actor and role".

The rest of amendment 24 stands. Each passage named here carries a one-line pointer to this
amendment.

### Decision

1. **Only the work carrier reads the chain (D-44 B).** The read-only role's one holder is the work
   carrier: on the Mac until ADR-0059 step 3f-2, where its launcher fetches the read DSN through
   `sds-operator`, and in its container after, through the work-carrier lane account. Each carry
   still makes amendment 24 item 9's three chain reads, all under the read-only role. No hand path
   holds the read DSN: `orchestrator intake-package`, the `factory` journey's
   `emit-intake-payload`, and intent-packages `verify-approval` run by hand. After the cut-over
   (FE-5), each one fails closed. It has no read DSN, and amendment 24 item 8 makes an unreachable
   database or a refused role a refusal (`ChainUnavailable`, `PackageSourceError`), and item 10
   removes the file. The refusal comes from that, not from a check on who's calling.
2. **The project (D-45 A).** A new BWS project, `Factory Events`, holds only the two DSN secrets.
   Both are created in it in ADR-0059 amendment 7's act 3 and recorded by UUID. Neither has a UUID
   yet.
3. **The accounts (D-45 A as revised by D-47).** These are the only machine accounts granted a read
   of either secret:

   | Account | Reads | Serves (amendment 24 items 4 and 9) |
   |---|---|---|
   | The SDS HQ account | The append DSN | The cycle's `standing-approve`, on the Mac |
   | `factory-events-session`, a new narrow account with its own Mac Keychain item | The append DSN only; no read-DSN grant | Interactive Claude Code sessions running `intent-packages approve` on Devon's word (item 4's "Devon's `approve`") |
   | The bump-proposer lane account | The append DSN | The bump proposer's `approve --by-policy`. The Mac launcher reads the account's token from its own Keychain item until the lane moves (ADR-0059 step 3f-4). |
   | `factory-events-nightly`, a new narrow account with one more Mac Keychain item | The append DSN | The Mac nightly's `adapt` |
   | The work-carrier lane account | The read DSN | The work carrier, in its container after ADR-0059 step 3f-2 |
   | `sds-operator` | The read DSN, until the Mac carrier moves | The work carrier on the Mac, until step 3f-2 |

   The append DSN is never granted to `sds-operator`, `BWS_ACCESS_TOKEN_BROAD` or
   `BWS_ACCESS_TOKEN_VPS_BACKUP`: each serves processes beyond item 4's holders, so a grant would
   widen D-43 B. Item 4's four append holders are unchanged, and each now has its own account.
4. **The history import (D-46 A).** An interactive session runs FE-1's import once, in ADR-0059
   amendment 7's act 5, under the append-only role, reading the append DSN by UUID for that one
   command. It isn't a standing holder: item 4's "exactly these" still names four holders, and
   the import is one command run while act 5 has the writers paused. D-46 A names no account for
   the session's fetch. The session can read the append DSN only through an account item 3
   already grants, so the import adds no grant. The command doesn't log the DSN (amendment 24 item
   13).
5. **What D-44 B means for FE-3.** FE-3's code scope is unchanged. Only the read DSN's holders
   shrink. The orchestrator's read path still serves the carrier:
   - The carrier runs `orchestrator emit-intake-payload` as a subprocess (`work_carrier/prepare.py`,
     `EMITTER` at line 47, the command at lines 117-126).
   - `emit-intake-payload` (`cli.py`, line 290) builds its payload through `_build_intake_payload`
     (line 322), which calls `package_sources.load_package_intake_payload` (`_load_intake_payload`,
     lines 192-206). `intake-package` (line 254) takes the same path (line 283).
   - In `package_sources.py`, the payload loader calls `_verify_current_approval` (line 558). That
     runs intent-packages `verify-approval` (`_verify_with_intent_packages_cli`, line 382), then
     `_verify_factory_chain` (line 387), then `_verified_approval_from_events` (line 388), which
     reads `_iter_package_approval_events` (line 346).

   So FE-3 changes `_verify_factory_chain` and `_iter_package_approval_events` to read the database
   under the read DSN, as ADR-0059 amendment 7 says. The hand commands share the same code, and
   they fail closed because their callers hold no DSN, not because the code differs.
6. **What it grants, and nothing more.** The project in item 2 and the six grants in item 3, as
   D-45 A and D-47 give them, and the one-time import in item 4, as D-46 A gives it. No other
   account reads either secret, and no Keychain item or account is created beyond the two new
   accounts and the bump-proposer launcher's Keychain item that D-45 A names.

### Consequences

- **A hand intake fails closed after FE-5.** Devon has never run `intake-package` or
  `verify-approval` by hand (D-47), so no path he uses is lost. A person who registers through
  `/review/intakes/new` pastes the output of `orchestrator emit-intake-payload` (`web.py`, lines
  696-807), and after the cut-over no hand run of that command can produce it. The form itself
  doesn't read the chain and is unchanged.
- **Who appears as the actor.** The event log's actor isn't its approver. Of the 92
  `package.approved` events by `claude-code-interactive`, 82 name approver `devon` and 10 name
  `policy:dependency-update@v1` to `@v3`; the bump proposer sets no `FACTORY_AGENT_ID`
  (`src/bump_proposer` names none), so its emits take the emitter's default actor
  `claude-code-interactive` (intent-packages `emitter.py`, lines 21 and 92). This amendment
  doesn't change any actor. Amendment 24's "Three callers" table stands as a code claim.
- **`sds-operator` reads the chain until step 3f-2.** Every Mac process that fetches through
  `sds-operator` can read the read DSN until then. The read role can't append, so no write path
  widens. D-45 A gives `sds-operator` the read DSN only until the Mac carrier moves.
- **Unchanged.** Amendment 24's mechanism (items 1 to 8 and 10 to 15), the four append holders,
  the network, the logging rules and FE-1 to FE-6's order.

## Amendment 26 — Each factory-events secret holds only its role's password (2026-11-14)

**Status: Proposed (2026-11-14).** Accepted on merge. HQ merges it only after a clean Opus
whole-branch review (HQ D-12, limit 3). It amends amendment 24 item 11 and the word "DSN" wherever
amendments 24 and 25 use it for a secret's value. It rests on Devon's plan amendment at gate G-57,
given in an interactive HQ session on 2026-11-14 and recorded in sds-hq `state/goal.md` (line 59 at
`f4b3a18`):

- Devon: "I am not liking that we are storing code, effectively, in BWS as a secret."
- Then "Yes to the change" to HQ's proposal: "each secret holds only its role's password; host
  `100.92.138.19`, port `15432`, database `postgres` and the role names become non-secret config,
  and `factory_events` builds the DSN at run time."

It also applies the estate secrets rule Devon set, as HQ's handoff for this amendment states it:
secrets hold values (passwords, tokens), not configuration (no DSNs or configuration blobs).

The same line of `state/goal.md` records FE-4's secrets as created: BWS project `Factory Events`
(`5e30c8b7-2b93-4a63-be26-b50200c8f070`), the append secret `2ce0dbc7-eeac-4bf0-8204-b50200c97516`
and the read secret `2f708d08-ea5d-4d11-bde0-b50200c9a60b`. Amendment 25 item 2 ("Neither has a UUID
yet") predates them. No manifest in the four repositories names either UUID at the commits read
here.

**One grant exists, and it's temporary.** G-57's step 7 checked both secrets' values by reading them
as `sds-operator`, and `sds-operator` was granted a read on both secrets for that check (sds-hq
`state/progress.md`, line 519 at `2b35ab4`, G-57's interactive entry of 2026-11-14 about 12:30 UTC).
Its read on the append secret is outside amendment 25 item 3, which says the append secret is never
granted to `sds-operator`, and the FE-4 accounts gate removes it. Whether its read on the read
secret stays is the grant amendment 25 item 3 already names for the Mac carrier (D-45 A as revised
by D-47). This amendment neither adds, keeps nor uses any grant.

Code reads cite security-standards `e89f69b` (FE-1, #90), intent-packages `3705e0c`, orchestrator
`c8be2f89` and sds-hq `f4b3a18`.

### What it amends

Line numbers are this file's at orchestrator `c8be2f89`, before this amendment was appended. Earlier
text isn't edited, so these passages carry no pointer.

- Amendment 24 item 11, "Two BWS secrets, each a DSN for one role", and its table (lines 3439-3459).
- Amendment 24's "Increments" paragraph, "the bump proposer's DSN" and "every holder's DSN" (lines
  3489-3497).
- Amendment 25 items 1 to 4, wherever they say "the append DSN" or "the read DSN" (lines 3614-3647).
- ADR-0059 amendment 7's FE-1 to FE-3 and 3f-2 bullets (lines 2804-2828) and acts 3 and 5, and
  amendment 8, where they say "DSN" for a secret. That ADR is read the same way, and this amendment
  doesn't edit it.

The rest of amendments 24 and 25 stands.

### Context

**Every reader takes a whole DSN from the environment, and its presence selects the database
chain.**

| Repository | Where | Variables |
|---|---|---|
| security-standards | `factory_events/chain.py`: the names (lines 15-19); `database_mode` (26-27); `_append_dsn` (30-34); `verify_dsn`, the read DSN else the append DSN (37-40); `import_dsn` (43-47) | `FACTORY_EVENTS_APPEND_DSN`, `FACTORY_EVENTS_READ_DSN`, `FACTORY_EVENTS_IMPORT_DSN` |
| security-standards | `factory_events/db.py`, `_connect(dsn)` (lines 45-50), called by `append_event`, `event_ids`, `verify` and `import_file` (76, 97, 123, 167); `cli.py`, `import-file` (line 162); `db_setup.py` (lines 17 and 29) | the three above, and `FACTORY_EVENTS_OWNER_DSN` |
| intent-packages | `chain_db.py`, `READ_DSN_VAR` (line 28), read in `main` (113-115) and passed to `psycopg.connect` (96-98); `operations.py`, the mode check in `default_chain_checker` (lines 40-41 and 556-560) | `FACTORY_EVENTS_READ_DSN`, `FACTORY_EVENTS_APPEND_DSN` |
| intent-packages | `emitter.py`, `CHAIN_DSN_VARS` (line 27) and `redact_chain_dsns`, which parses each DSN for its password (76-102); `_events_env` copies the whole environment to the child (122-131) | both |
| orchestrator | `factory_chain_read.py`, `main` (lines 86-88) and `_walk` (73-75); `package_sources.py`, the mode check in `_verified_package_approval_events` (lines 365-373); `_security_standards_env` copies the whole environment (233-243) | both |
| orchestrator | `bump_proposer/standing.py`, `_redact_append_dsn` (lines 201-209); `scripts/run-bump-proposer.sh`, which passes the append DSN through and fetches none (lines 264-269) | `FACTORY_EVENTS_APPEND_DSN` |

sds-hq's code reads no chain variable, and its approve child inherits none. `approve` builds the
child's environment from `PATH`, `HOME`, `FACTORY_AGENT_ID`, `SECURITY_STANDARDS_DIR` and, in tests,
`FACTORY_EVENTS_HOME` (`standing_approve.py`, lines 540-547), on top of `_child_env`'s minimal base
(lines 161-164). So no chain setting in the cycle's environment reaches the approve child unless
sds-hq passes it.

**The role names are fixed by the schema.** FE-1's roles script creates exactly
`factory_events_append` and `factory_events_read` (security-standards `chain_schema.sql`, lines
79-92), and the orchestrator's integration tests create the same two through it
(`tests/cli/test_package_chain_database_integration.py`, line 99).

**Host and port differ by place.** The Mac reaches the database over the tailnet, and a container on
`prod-vps` over Coolify's internal network (amendment 24 item 12). The tailnet address and port in
Devon's answer are the Mac's values only.

### Decision

1. **What each secret holds.** Each of the two secrets holds only its role's password: the append
   secret (`2ce0dbc7-eeac-4bf0-8204-b50200c97516`) the `factory_events_append` role's, and the read
   secret (`2f708d08-ea5d-4d11-bde0-b50200c9a60b`) the `factory_events_read` role's. Same project
   and UUIDs, and the same grants amendment 25 item 3 names (see "One grant exists" for the one made
   so far). Wherever amendments 24 and 25 say "the append DSN" or "the read DSN" for what a holder
   fetches, read "the append password" or "the read password". Who holds each is unchanged.
2. **The secrets' names.** BWS names them `factory-events-append-dsn` and `factory-events-read-dsn`
   (sds-hq `state/gates/factory-events-db.md`, lines 42-44). No code reads a secret by name: every
   reference is by UUID. **Decided (HQ's cycle, 2026-11-14):** they're renamed
   `factory-events-append-password` and `factory-events-read-password`, in the same act that
   rewrites their values (increment 5), so a name doesn't describe a value the secret no longer
   holds. The rename changes no reader, and the UUIDs, project and grants stay the same.
3. **The settings, and the one place that builds the connection.** `factory_events.chain` owns every
   chain setting and builds each connection from parts. It passes them to `psycopg.connect` as
   keyword parameters (`host`, `port`, `dbname`, `user`, `password`), never as a formatted URL, so
   no password character needs escaping and no string holds password and address together.

   | Variable | Secret | Value |
   |---|---|---|
   | `FACTORY_EVENTS_APPEND_PASSWORD` | Yes | The append secret's value |
   | `FACTORY_EVENTS_READ_PASSWORD` | Yes | The read secret's value |
   | `FACTORY_EVENTS_DB_HOST` | No | On the Mac, `100.92.138.19`; in a container, the resource's address on the `coolify` network |
   | `FACTORY_EVENTS_DB_PORT` | No | On the Mac, `15432`; in a container, the resource's internal port |
   | `FACTORY_EVENTS_DB_NAME` | No | `postgres` |

   The role names aren't variables. They're constants in `factory_events.chain`, beside the schema
   that creates them: a variable could only name a role the schema didn't create. The append
   password always connects as `factory_events_append`, and the read password as
   `factory_events_read`.

   The three non-secret settings have no default in code. Each holder's environment sets them where
   it already sets its others: each Mac launcher, and an interactive session's command, beside the
   fetch of its password; each lane container in its Coolify environment, as plain variables.

   The readers in other repositories use `factory_events.chain` rather than read a variable.
   intent-packages `chain_db` runs under the security-standards events interpreter (`operations.py`,
   lines 510-517), and orchestrator `factory_chain_read` under the orchestrator's own
   (`package_sources.py`, line 319), each with security-standards' `src` on its path. Each already
   imports `factory_events.store` lazily (`chain_db.py`, line 94; `factory_chain_read.py`, line 71).
   `factory_events.chain` loads only `db` and `store` at its top (`chain.py`, line 13), and `db`
   only `store`, `envelope` and `jsonlog`, with psycopg lazy (`db.py`, lines 17-19 and 46), so it
   loads wherever `store` does. Their parents (`default_chain_checker`,
   `_verified_package_approval_events`) only test which password is set, and keep the names as
   literals.
4. **What selects the database chain.** A set `FACTORY_EVENTS_APPEND_PASSWORD` or
   `FACTORY_EVENTS_READ_PASSWORD` selects it, as a set DSN does today. Each existing rule keeps its
   shape under the new names: an append with only the read password refuses (`chain.py`, lines
   30-34); a verify reads under the read password, else the append password (37-40); and a reader
   with only the append password refuses rather than match the file (`operations.py`, 558-560;
   `package_sources.py`, 369-373).
5. **A missing or retired setting fails closed, by name.** With a password set, each missing
   non-secret setting is a refusal that names its variable (for example "`FACTORY_EVENTS_DB_HOST` is
   not set"), raised as `ChainError` in `factory_events` and reported as `UNAVAILABLE` by both
   external readers, so it's a refusal, never "not approved". A set retired variable
   (`FACTORY_EVENTS_APPEND_DSN`, `FACTORY_EVENTS_READ_DSN` or `FACTORY_EVENTS_IMPORT_DSN`) is a
   refusal that names it, in `factory_events` and in both parents' mode checks. Ignoring it would
   drop that process to the file chain without a word, which amendment 24 items 8 and 10 forbid.
   Neither message carries a value.
6. **No whole-DSN variable is accepted for the append or read role, tests included.** A variable
   that takes a whole DSN would let a DSN back into a secret. The integration tests set the parts
   instead: they already build each role's connection from `FACTORY_TEST_DSN`, a disposable
   superuser (orchestrator `test_package_chain_database_integration.py`, lines 90-111), and their
   wrong-port and wrong-password cases (lines 197 and 210) become one changed part each.
   `FACTORY_TEST_DSN` stays: it's a test fixture's address for a throwaway database, never a
   production setting, and no BWS secret holds it.
7. **The import runs under the append role's settings.** Amendment 25 item 4 (D-46 A) puts the
   one-time import under the append-only role, so `import-file` connects with
   `FACTORY_EVENTS_APPEND_PASSWORD` and the three settings, and `FACTORY_EVENTS_IMPORT_DSN` retires
   (`chain.py`, lines 17-19, kept it separate only while the role was undecided).
8. **Redaction follows the value.** `redact_chain_dsns` (intent-packages `emitter.py`, lines 90-102)
   and `_redact_append_dsn` (orchestrator `standing.py`, lines 201-209) redact each set password
   variable's value instead of a DSN and its parsed password. Logging is unchanged (amendment 24
   item 13): no record holds a password, and a driver's text isn't logged.
9. **Out of scope.** `FACTORY_EVENTS_OWNER_DSN` (`db_setup.py`, line 17) is typed for FE-4's
   one-time owner act and isn't a BWS secret; the owner's password stays in Coolify (amendment 24
   item 3). `FACTORY_DB_DSN`, the Mac database's, retires in FE-6 (ADR-0059 amendment 7, act 6).

### Increments

All of these land before FE-5 (ADR-0059 amendment 7, act 5). No tracked launcher or manifest sets a
chain variable or names either secret's UUID (`run-bump-proposer.sh` fetches none, lines 264-269),
so none changes what a scheduled pass runs.

Between increment 1 and the increments after it, the intent-packages and orchestrator integration
suites fail against an updated security-standards checkout, because they still set the retired
variables. Each runs against whatever checkout `SECURITY_STANDARDS_DIR` names
(`scripts/check-integration.sh`, line 16 in intent-packages, line 19 in the orchestrator), by hand
and not in `make check`. Each increment's own suite runs against the checkout it targets.

1. **security-standards.** `factory_events.chain` gains the settings, the role constants, the
   connection-parameter builder and the retired-variable refusal; `db.py` and `cli.py` connect from
   parameters; `import-file` uses the append settings; the README's table (lines 37-42) changes with
   it. It lands first, because intent-packages' and the orchestrator's chain readers import
   `factory_events` from the security-standards checkout (intent-packages `emitter.py`, lines
   105-131; orchestrator `package_sources.py`, lines 233-243).
2. **intent-packages.** `chain_db` connects through `factory_events.chain`; `operations` and the
   emitter's redaction read the password names.
3. **orchestrator.** `factory_chain_read` connects through `factory_events.chain`;
   `package_sources`, `bump_proposer/standing.py`, the launcher's comment,
   `tests/architecture/test_intent_edges.py` (`ENVIRONMENT`, lines 46-54; `CHAIN_READ_ENVIRONMENT`,
   line 60; `CHAIN_READ_LAZY_IMPORTS`, line 61) and the integration tests change with it.
4. **sds-hq.** `approve` passes `FACTORY_EVENTS_APPEND_PASSWORD` and the three settings into the
   approve child's environment (`standing_approve.py`, line 542), because `_child_env` passes
   nothing else. A whole DSN would have needed the same change.
5. **The secrets' values (an act on BWS).** Each secret's value becomes its role's bare password,
   decoded (a URL-form DSN can hold it percent-encoded), under the same UUID, and each secret takes
   its new name (item 2). It's Devon's act in the Bitwarden web app, where G-57 created them. A
   session does it only with his OK, and only through an identity he names that can write the
   secret: amendment 25 item 3's grants are reads. A session works in-process: it reads the value,
   writes back only the password, and prints neither, only a hash. The role passwords in the
   database don't change. It can run any time before FE-5, since no process fetches either secret
   yet.

Then FE-5 sets the password variables and the three settings for each holder, where it set DSNs
before (ADR-0059 amendment 7, act 5, as amendment 8 names the holders).

### What it grants, and nothing more

Nothing. No credential, role, scope, project, account or grant changes: the same two secrets, the
same two roles and the same six grants (amendment 25 item 3). The temporary `sds-operator` reads
from G-57's step 7 are neither added, kept nor used here; the FE-4 accounts gate settles them under
amendment 25 item 3 ("One grant exists"). It moves non-secret configuration out of two secrets'
values and into each holder's environment. Nothing here declares a credential exposed (Infra Brain
rule 1032).

### Consequences

- **A host move is a configuration change.** Moving the database changes the host and port settings,
  not a secret's value.
- **A holder needs four variables, not one.** A holder's environment that sets the password but
  misses a setting refuses by name rather than connect somewhere else.
- **ADR-0059 amendments 7 and 8 say "DSN"** for the secrets: amendment 7's FE-1 to FE-3 and 3f-2
  bullets (lines 2804-2828) and acts 3 and 5, and amendment 8. Read them as this amendment's item 1
  does. This amendment doesn't edit them.
- **Unchanged.** Amendment 24's mechanism, the four append holders, the one reader, the import's
  actor (amendment 25 item 4), the network, the logging rules and FE-1 to FE-6's order.

## Amendment 27 — One owner act resets the factory-events chain before an FE-5 import retry (2026-11-14)

**Status: Proposed (2026-11-14).** Accepted on merge. HQ merges it only after a clean Opus
whole-branch review (HQ D-12, limit 3). It records Devon's answers to the FE-5 plan's open
questions 1 to 3 (`docs/superpowers/plans/2026-11-14-fe-5-cutover.md`), each given as option A in
an interactive HQ session on 2026-11-14 and quoted from sds-hq `state/decisions.md` (lines 13-15 at
`b87452e`):

- **D-50 A:** "The guard reads the database under the append role."
- **D-51 A:** "Allow one named owner act for this case only: an announced act that drops the chain
  schema and re-runs the database setup, then the session re-imports; HQ first proves the setup
  re-runs cleanly on a populated schema in a test database."
- **D-52 A:** "Make the VPS verify part of the carrier's cut-over: FE-5 verifies on the Mac only,
  and the carrier's first VPS pass carries the VPS verify."

### Decision

1. **One more owner act (D-51 A).** Amendment 24 item 3 limits the owner to FE-4's setup acts. It
   gains one act, `fe-5-chain-reset`, for one case: a rollback of ADR-0059 amendment 7's act 5
   after a successful import and before any append. The chain's table lives in `public`, so
   "drops the chain schema" is the owner's `DROP TABLE public.factory_events`. The owner then runs
   `python -m factory_events.db_setup`, and the session runs the import again (amendment 25 item
   4). D-51 A names the session only for the re-import; who runs the drop and the setup is open
   (the next section). HQ announces the act. security-standards PR #94 (`237e422`) proves the setup re-runs
   cleanly on a populated schema in a test database. The FE-5 plan's "Reset the chain before a
   retry" section held its steps. Amendment 28 (D-53 C) withdraws the act, and the plan's "No retry
   after the import" section replaces that section.
2. **The cycle's guard reads under the append role (D-50 A).** The cycle's `check_chain` reads the
   chain under the append role it already holds (amendment 25 item 3). Amendment 25 item 1 names
   the read-only role's one holder, and that's unchanged.
3. **FE-5 verifies on the Mac only (D-52 A).** ADR-0059 amendment 7 act 5's "one `verify` on each
   machine" is the Mac nightly's `verify` in FE-5. The VPS verify moves to step 3f-2's cut-over, as
   the work carrier's first VPS pass.

### Open before the act

- **Who runs the owner steps, and how the act reads the head (FE-5 plan, open question 9).**
  Answered by amendment 28 (D-53 C): there is no post-import retry, so `fe-5-chain-reset` is never
  built or run, and item 1's act is withdrawn. The question as it stood: D-51 A names no actor for
  item 1's drop and setup, and no path for the owner password. FE-4's owner step, Devon at a hidden
  prompt, is the only approved path. The act's precondition reads the chain's head and event
  count, which is a chain read; amendment 25 item 1 and D-44 B refuse that to sessions.

### What it grants, and nothing more

The one owner act in item 1, for that case only, with its actor unnamed (amendment 28 withdraws
it). No credential, role, account, grant or reader is added, and no actor gains the owner
password: it stays in Coolify, and nothing holds it at run time. The cycle reads under a role it
already holds, and no session gains a `verify` or any other chain read.

## Amendment 28 — No FE-5 import retry after a successful import (2026-11-14)

**Status: Proposed (2026-11-14).** Accepted on merge. It records Devon's answer to the FE-5 plan's
open question 9 (`docs/superpowers/plans/2026-11-14-fe-5-cutover.md`), given as option C in an
interactive HQ session on 2026-11-14 and quoted from sds-hq `state/decisions.md` (the D-53 line,
sds-hq commit `9d97574`):

- **D-53 C:** "No retry after the import: a post-import rollback ends FE-5 until a new amendment
  plans the next try (D-51 B)." Devon: "I think C is the cleanest answer. I think it's a low
  probability of being encountered."

### Decision

1. **No post-import retry.** A rollback of ADR-0059 amendment 7's act 5 after a successful import
   (FE-5 plan step 6) and before any append, that is, from the plan's rollback point or from its
   steps 7 to 9, ends FE-5. The database keeps the imported copy: the trigger refuses to delete
   it, and a later import refuses a non-empty chain. The next try needs a new ADR-0055 amendment
   that plans it.
2. **What this replaces in amendment 27.** Amendment 27 item 1 (D-51 A's owner act,
   `fe-5-chain-reset`) is withdrawn for the post-import case, the only case it covered, so the act
   is never built or run. D-51 B's path, quoted in D-53 C, takes its place. Amendment 27's record
   of D-51 A stands as the record of that answer; this amendment supersedes its effect. Amendment
   27's open item (open question 9) is answered here. Amendment 24's two mentions of the act (item
   3, and its import open item) say amendment 28 withdraws it: the owner keeps only FE-4's setup
   acts.
3. **Unchanged.** A rollback before step 6 succeeds is unchanged: the import commits only past its
   head check, so the database holds nothing from it. Amendment 27 items 2 and 3 (D-50 A, D-52 A)
   stand. Nothing else in FE-5 changes.

### Consequences

- **A post-import rollback costs a planning round.** FE-5 waits for a new amendment, in exchange
  for no owner act, no owner-password path and no session chain read. Devon judged the case
  unlikely.
- **The evidence stays.** security-standards PR #94 (`237e422`) proves the setup re-runs cleanly on
  a populated schema in a test database. A later amendment that plans a retry can rest on it. The
  FE-5 act records step 4's file head and step 6's event count in its progress entry for the same
  reason.

### What it grants, and nothing more

Nothing. It removes amendment 27's one owner act. No credential, role, account, grant or reader is
added, the owner password stays in Coolify, and nothing holds it at run time.

## Amendment 29 — The rotation executor runs on the VPS host, and a lane token's revoke waits on the loaded fingerprint (2026-11-18)

**Status: Proposed (2026-11-18).** Accepted on merge, as amendment 28 is. **Relates to:** decision
2 and its accepted residual, T1, decision 3's R10 rule, amendment 17 (the VPS helper), amendment 19
items 6, 7 and 10 with D-26 and D-27 (lane tokens and the departure), and ADR-0059 decision 4,
increment 7 and Amendment 6 decision item 4.

This is the design of ADR-0059 increment 7, written ahead of it. Increment 7 is built after
increment 6 (ADR-0059, lines 1282 and 2692-2693, and its table row at line 2734). This amendment
builds nothing and changes no resource, identity, token or grant. W-215's options report
(`docs/superpowers/reports/rotation-executor-move-options-report.md`, at `68beef6a`) is its map.
Devon answered three questions in interactive HQ sessions on 2026-11-18. They are quoted from sds-hq
`state/decisions.md`: D-63 and D-64 at commit `902da11`, and D-65 at commit `7e43bc8`. None was
made standing, so none is a general rule.

- **D-63** (question): "Rotation executor's move to the VPS (ADR-0059 increment 7, ADR-0055
  amendment 29; built after increment 6, designed now): when a lane's BWS token is reissued, who
  restarts the lane so it loads the new token, and how is the old token's revoke proven safe
  (ADR-0055 rule R10: no revoke until every consumer is shown to hold the new value)? Today the
  deploying session restarts the lane, and the revoke rests on HQ's report of the lane's reads
  (your D-27 departure, 'until increment 7'). (A) The executor restarts each lane itself: reverses
  ADR-0055's 'the executor never redeploys a lane', changes every lane row's redeploy field, and
  needs a quiet-slot reader and a slot wait once the change window narrows. (B) The session keeps
  the restart, and the D-27 departure becomes permanent. (C) The session keeps the restart, and the
  executor proves the new token before the revoke by reading the fingerprint (a short hash and
  length, never the value) of the token the restarted lane container loaded, through the existing
  VPS helper's loaded verb. Cost of C: a small helper change (a target with no database, only
  consumers), one helper target per lane, and the lane-token adapter calling the helper; unchecked:
  whether Coolify's container names are stable enough for the helper's exact-name match."
  **Answer (2026-11-18T11:18:24Z):** "C. Devon, in an interactive session, chose C (the session
  keeps the restart; the executor proves the new token before the revoke by reading the loaded
  token's fingerprint, a short hash and length, never the value, through the VPS helper's loaded
  verb)."
- **D-64** (question): "Rotation executor's move to the VPS (ADR-0059 increment 7, ADR-0055
  amendment 29): where does it run on the VPS, and do you accept what that does to its main
  residual risk (T1: whoever gets root on its host gets its root identity, which reads the BWS
  project Rotation / Admin and every lane token keeper in Rotation / Keepers)? Today it runs in its
  own VM, so VPS root alone doesn't reach it. (A) A Coolify resource on the VPS: its root token sits
  in a Coolify variable and in Coolify's /data/coolify backups, and Coolify's API holders gain a
  path to it. (B) A systemd service on the VPS host under its own user, outside Docker and Coolify:
  no Coolify path; VPS root gains Rotation / Admin and the keepers (no vps-backup recipe was found
  that copies /etc, so the token doesn't land in backups, read by grep only). (C) Keep a dedicated
  host or VM for it (the residual stays as today, plus a host to run)." **Answer
  (2026-11-18T11:18:24Z):** "B. Devon, in an interactive session, chose B (a systemd service on the
  VPS host under its own user, outside Docker and Coolify) and accepts the widened T1: VPS root
  reaches Rotation / Admin and the keepers; the amendment's T1 text says so."
- **D-65** (question): "D-64 follow-up (rotation executor on the VPS, ADR-0055 amendment 29): your
  D-64 B (a systemd service on the VPS host) was offered as having no Coolify path. The amendment
  worker found that false. Coolify logs in to its own host as root over SSH with its web terminal
  on, and every session with infraops connected runs any shell command as root through vps_exec
  (infraops ssh-client.ts: VPS_USER defaults to root; HQ checked). Root reads the executor's token
  file, so Coolify admins and every infraops session would reach Rotation / Admin and every lane
  token keeper. Which do you want? (1) Accept as found: the amendment's T1 says VPS root includes
  Coolify's terminal and key holders and every infraops vps_exec holder. (2) Accept, and narrow the
  root paths first: as (1), plus named cut-over prerequisites (for example infraops and Coolify off
  the root user), each its own later decision. (3) Reopen D-64 and choose C, a dedicated host or VM,
  as today." **Answer (2026-11-18T13:05:43Z):** "2. Devon, in an interactive session, chose option
  2: accept D-64 B as found (VPS root includes Coolify's terminal and key holders and every infraops
  vps_exec holder), and narrow the root paths first: named cut-over prerequisites for increment 7
  (for example infraops and Coolify off the root user), each its own later decision."

D-64 B is the choice for the development phase. Devon will stress-test it in a later security
phase, before the SDS goes into production.

### Decision

1. **Placement (D-64 B).** The executor runs on the VPS host (`prod-vps`) as a systemd service and
   timer under its own host user, `rotation`, outside Docker and Coolify. The user has no login
   shell, no sudoers rule and no membership in the `docker` group or any other group that reaches
   root. Its start path is proven with only `PATH` and `HOME` set, as decision 2 requires (R16).
   - **What moves:** the service and its timer; the installed code (`/opt/rotation-worker`); the
     state directory, `/var/lib/rotation-worker`; the configuration,
     `/etc/rotation-worker/config.toml` (ids and paths, no value; root-owned, group `rotation`,
     mode `0640`); and the root token's file, `/etc/rotation-worker/root-token` (owner
     `rotation`, mode `0400`, holding the token and nothing else).
   - **What doesn't move:** the `rotation-executor` BWS machine account and every grant it holds;
     the WORKER bearer in `Rotation / Executor`, its key id and its agent id, so the orchestrator
     needs no restart; the inbox (`Rotation / Inbox`); every keeper in `Rotation / Keepers`;
     staging and quarantine in `Rotation / Work`; and the store kind, so the heartbeat key
     `rotation-heartbeat:<store kind>` stays the same (W-215 Q5, and Q6 steps 3 and 9). The
     Keychain instance on the Mac is unchanged.
   - **How it reaches the helper.** It uses the same path as before: SSH to
     `root@<VPS tailnet address>`, with the key it already holds in `Rotation / Keepers`
     (amendment 17's "As built", lines 2152-2155), into the same `authorized_keys` line with its
     forced command `/usr/local/sbin/sds-pg-rotate` and `restrict`. The connection now starts and
     ends on one host, through that host's own tailnet address. The bound is the forced command and
     the root-owned `targets.toml`, not the network hop (lines 2117-2127 and 2141-2142). Reusing
     that key and path grants nothing new. The executor gets no other way into root or Docker.
   - **Coolify.** `coolify_url` stays at the tailnet address, which the client's TLS-or-tailnet
     rule already allows (`rotation_worker/coolify.py`, lines 16-17 and 35-37). The executor keeps
     its own Coolify token in `Rotation / Coolify`, with the same abilities.
2. **Decision 2 and T1, for the VPS host.** This item replaces decision 2's first bullet and
   accepted residual (lines 133-149), the root's row in the asset table (line 338) and T1 (line
   363), from increment 7's cut-over on.
   - **Decision 2, as amended.** The executor runs on the VPS host as a systemd service under its
     own user (item 1). The root token is in a file only the service's user reads, and decision
     9's rule stands: the host holds nothing of the executor's that can't be rebuilt.
   - **Who reaches the root token's file.** Anyone with root on the VPS host. A systemd user
     isolates the file from other non-root users on the host, and from a container that has no
     route to host root, and from nothing else. Root on that host is held by at least these paths,
     each read from its source on 2026-11-18:
     - **Coolify.** Production Coolify's one server, `localhost` (`is_coolify_host` true), logs in
       to its own host over SSH as `root`, and its web terminal is enabled
       (`coolify_get_server`: `user` `root`, `is_terminal_enabled` true). So anyone who can open
       that terminal, and anyone who can read the SSH private key Coolify keeps for that server,
       has root on the host. A Coolify API token with `write` or `deploy` can probably run a
       container that mounts a host path, as the asset table says of the executor's own Coolify
       token (line 341, and amendment 1, lines 623-625); that path isn't tested.
     - **infraops `vps_exec`.** It runs any shell command on prod over SSH as `VPS_USER`, which
       defaults to `root` (infraops-mcp-server `src/services/ssh-client.ts`, lines 30-33;
       `src/tools/vps.ts`, lines 34-72), and no file in that repository sets another user. Every
       session with infraops connected holds it, through the infraops SSH key on the Mac. So any
       process that can use that key reaches the root token's file, as a process running as
       Devon's user reaches the VM today.
     - **Any other root login to the VPS host.**
   - **T1, as amended.** Whoever has root on the VPS host gets the executor's root identity: write
     on `Rotation / Keepers`, `Rotation / Work` and `Rotation / Inbox`, read on `Rotation / Admin`
     (the OpenRouter management key and the rotation App's private key), `Rotation / Coolify`,
     `Rotation / Executor` and `Rotation / Healthchecks`, and its direct read on the Healthchecks
     Management key's secret. That includes every lane-token keeper and the keepers of credentials
     that never touch the VPS. Root on that host already held every lane token in the lane
     containers' environments, so the widening is `Rotation / Admin`, the rotation Coolify token,
     and the keepers of credentials that never reach the host. Holders of Coolify's terminal and
     SSH key and every infraops `vps_exec` holder are among those root holders. Devon accepts
     this residual (D-64 B, D-65 option 2). Today's T1, an agent session on the Mac reaching what
     the executor holds, stays inside it, through `vps_exec` instead of `orb`. The SYSTEM and
     VERIFIER bearers stay outside the executor's BWS identity's reach.
   - **Cut-over prerequisites: the root paths narrow first (D-65).** Before increment 7's cut-over,
     each of the following is its own decision for Devon, made later and not here: whether and how
     infraops `vps_exec` stops logging in as root, and whether and how Coolify's `localhost` server
     stops logging in as root (with its terminal). The cut-over doesn't start until each has a
     recorded answer, and a narrowing he chooses is done and verified before it starts. This
     amendment decides neither.
   - **No standard covers this.** Neither Code Brain nor Infra Brain has a rule on where a root
     credential rests on a host or on host-user isolation. This item rests on this ADR's threat
     model and on D-64 and D-65.
3. **A lane token's restart stays with the session, and R10 is met by the loaded fingerprint
   (D-63 C).**
   - **The session keeps the restart.** The deploying session still restarts a lane after a
     reissue, with the lane phases' `--token` mode in the lane's quiet slot. The executor still
     never redeploys a lane: amendment 19 item 6 (lines 2324-2352) stands, and its client for a
     lane is built with `restarts=False` (`rotation_worker/coolify.py`, lines 57-60 and 88-91).
   - **The read.** Before the old token's revoke, the executor reads the fingerprint of the
     `BWS_ACCESS_TOKEN` that the restarted lane container loaded. The fingerprint is the sha256
     prefix and length, never the value. The executor reads it through the helper's `loaded` verb
     (`deploy/vps/sds-pg-rotate`, lines 1-15, 103-119 and 179-195). It proceeds only when that
     fingerprint equals the new keeper's. Both use the same form: 16 hex characters and a length
     (`FINGERPRINT_PREFIX`, and `rotation_worker/values.py`'s `PREFIX_LENGTH`).
   - **The changes it needs, for increment 7's code step (not made here).**
     - A consumers-only helper target: a target with `consumer` tables and no `role` or
       `database`. `_target` refuses it for `set` and `probe`, and `loaded` accepts it. Today
       `_target` requires all three keys for every verb.
     - One target per lane in the root-owned `targets.toml`. Each names the lane's container and
       `BWS_ACCESS_TOKEN`. The lane token's registry row names it as its `helper_target`, as a
       `postgres-role` row does (`adapters/postgres_role.py`, line 158). That's a row field, not
       a destination identity field (`destinations.py`, `IDENTITY_FIELDS`).
     - `bws_lane_token`'s consumer check calls the helper's `loaded` for that target in a
       reissue's `verify` unit, and passes only on an equal fingerprint. An issue's `store` unit
       runs `verify_consumers` right after its `PATCH`, before any restart
       (`plans.derive_issue`), so it keeps amendment 19 item 7's proof, the `PATCH` answering
       2xx. An issue has no revoke, so R10 doesn't reach it.
   - **Where the executor waits for the restart.** It waits in the `verify` unit
     (`verify_consumers`), unclaimed, as the mint and reader waits do
     (`docs/operations/rotation-executor.md`, lines 134-145; `worker.py::_take`). Each pass reads
     `loaded` before it claims the unit, and the unit stays unclaimed until the fingerprint equals
     the new keeper's. An unreadable answer waits too. The wait is on the ordinary 48-hour
     `rotation-unit-waiting` clock. The claimed step reads `loaded` once more and fails the unit on
     any mismatch. The wait never sits in a claim, so the lease ceiling (decision 9, 2 hours)
     bounds only the one read.
   - **Fit with the narrowed window.** In operation, `live_estate`'s window narrows to 02:00-06:00
     (`factory-policy.toml`, lines 499-510), and the quiet slot is its complement (W-215 summary
     point 1). `verify` writes a hosted service (`plans.py`, lines 218-231), so its claim is
     windowed. The session restarts the lane in the quiet slot. The executor's pre-claim read then
     passes, and the claim waits for the next 02:00. No step waits past 06:00 inside a claim. The
     cost is up to a day between the restart and the revoke alert, with both tokens live, which
     decision 8 already accepts for a hosted unit.
   - **The revoke.** The "Revoke the old … value" alert is first sent only once `verify` completes
     (`docs/operations/rotation-executor.md`, lines 164-167), so Devon's revoke now follows a
     machine read. After a leak, Devon still revokes first (D-26, amendment 19 item 10). The
     `loaded` read then proves the restarted container holds the new token, as it does on a
     non-leak reissue. An issue's plan is unchanged.
   - **R10 is restored for lane tokens.** `retire_old` follows a machine `verify_consumers` for
     every registered consumer, as decision 3 requires (lines 160-161). That's Infra Brain 1032's
     fail-safe, no revoke without attested consumers, now held by machine evidence instead of
     HQ's report. Amendment 19 item 7's
     departure (lines 2370-2376, D-27) ends at increment 7's cut-over. The lane phases'
     `lanes-finish` and `lane-run --read` stay as the lane's own deploy checks, but they're no
     longer the revoke's precondition.
4. **The cut-over (W-215 Q6, steps 1 to 9, as HQ's configuration).**
   1. **Drain.** Start only when no VM-instance rotation unit is `executing`, no rotation is between `store` and `retire_old` with a hosted unit still to run,
      and no dispatched run is live. A unit waiting unclaimed, such as a mint or reader wait, may
      stay. The VPS instance picks it up with its wait clock restarted.
   2. **The root token at the move: a second token, Devon's choice (D-75 B).** The choices were to
      move the file, or to mint a second access token on the same machine account, store it on the
      VPS with `set-root-token`, prove one pass, and then revoke the VM's. W-215 recommended the
      second. Either way it's the same identity with the same grants. Devon chose the second, in an
      interactive session on 2026-11-23, recorded at 2026-11-23T12:07:23Z in sds-hq
      `state/decisions.md` (commit `a1b5c30b`), quoted from there:
      - **D-75** (question): "Rotation executor's move to the VPS host (increment 7 cut-over,
        ADR-0055 Amendment 29 item 4 step 2): how does its root token reach the host's file,
        /etc/rotation-worker/root-token, with no terminal step for you? set-root-token needs a TTY
        and vps_exec has none, so every route uses a session piping the value from the Mac over
        SSH into a stdin mode of set-root-token (inert code HQ builds first). (A) Move: the session
        pipes the VM's own token file to the host. No mint, no BWS change, nothing for you. The VM
        and host hold the same token, so the VM can't be revoked separately: after the item 5
        checks the session deletes the VM's file (that ends the rollback), and the old copy stays
        on the VM's disk until the token's next rotation. (B) Second token, stored where only the
        root reads: you mint a second access token on machine account rotation-executor and store
        it as a secret in Rotation / Admin (the runbook says only the root reads that project, so
        no grant changes). A session, as root in the VM, fetches it with the VM's token and pipes
        it to the host; one pass proves it; after the item 5 checks you revoke the VM's token and
        delete the BWS copy. (C) As B, but through a keeper in Rotation / Keepers with a temporary
        sds-operator read (the D-54 pattern): departs from the runbook's no-widened-grant rule, and
        sds-operator briefly holds the root identity." **Answer:** "B. Second token: Devon mints a
        second access token on machine account rotation-executor and stores it in Rotation /
        Admin (no grant change); a session, as root in the VM, fetches it with the VM's token and
        pipes it to the host's set-root-token --stdin; one pass proves it; after the item 5 checks
        Devon revokes the VM's token and the BWS copy is deleted."

      The VM has no `bws` CLI, so the fetch is a new executor verb, `rotation-worker emit-secret
      <secret id>`. It logs in with the VM's root token as every verb that reads the store does
      (`compare-destinations` reads only git and doesn't log in), reads the one secret, and writes only its value and a newline to stdout, refusing a terminal. It runs as
      `rotation`, started by root through `runuser`, because the root token file must belong to its
      reader. The pipe and its order in the cut-over are in `docs/operations/rotation-executor.md`,
      "Put the root token in its file" and "Cut over to the host".
   3. **The WORKER bearer** stays in `Rotation / Executor`, read at each pass. No orchestrator
      restart.
   4. **State.** Copy `announced-acts.json`, `wait-since.json` and `revokes-observed.json` from the
      VM's `/var/lib/rotation-worker`, or accept their loss. Losing them restarts a wait and never
      shortens it (`docs/operations/rotation-executor.md`, lines 114, 134 and 174).
   5. **The helper's host key.** Re-read the pin from the new host against the VPS's own host key,
      and keep `pg_helper_host` at the tailnet address (item 1).
   6. **One instance at a time.** Stop and disable the VM's timer before the VPS's first pass.
   7. **`--token` mode** stays the lane restart (item 3).
   8. **The Mac's Keychain instance** is unchanged.
   9. **The heartbeat.** The VPS instance sends the same `rotation-heartbeat:<store kind>` key.
      Confirm the schedule fires within one interval of the VM's last pass and clears on the VPS's
      first.
5. **Increment 7's cut-over checks (its exit, ADR-0059 Amendment 6 decision item 4).** W-215 Q6
   step 10:
   - One pass run with only `PATH` and `HOME` (`env -i`) succeeds.
   - The heartbeat clears from the new host.
   - `rotation-instance-behind` is clear.
   - The tailnet Coolify `PATCH` answers 2xx, on a canary or the next real issue.
   - The helper's `loaded` answers for its targets.
   - The VM's timer is disabled, and after its revoke its root token is refused on a fresh login
     with an empty `HOME`.
   - One lane token's `loaded` fingerprint equals its keeper's.

   These are the exit. No week is run.

   2026-11-23, D-77 C: the check "A ping-URL listing logs `ping_url_read`" is dropped, because
   no pass can meet it. `ping_url_read` is logged only inside an issue's mint step
   (`src/rotation_worker/worker.py`), a `healthchecks-ping-url` has no rotation plan once issued
   (`src/rotation_worker/plans.py`), and all 17 ping-URL rows in `.cred-consumers.toml` are
   already issued. The G-91 session's evidence stands in its place: the host's BWS login works in
   every pass, Healthchecks is reachable from the host, and the Management key's read is a BWS
   grant the move didn't change. The next new lane's first ping-URL issue on the host is the
   natural proof: if the read fails, its mint step fails visibly.

### Verify items

Each item names the step that verifies it.

- **Coolify's lane container names stay stable enough for the helper's exact-name match**, across
  a `--token`, `--tag` and `--schedule` redeploy. Increment 7's code step.
- **No vps-backup recipe copies the executor's root token or `/etc` off the host.** W-215 checked
  this by grep only. Increment 7's cut-over.
- **Where the `rotation-heartbeat:<store kind>` schedule is defined, and that it clears from the
  new host.** The cut-over (item 4, step 9).
- **The helper's `loaded` tells the new lane token from the old while both answer.** That includes
  a lane image having the `sh` and `printf` the verb runs in the container. Increment 7's code
  step.
- **Item 3's restart wait fits the lease ceiling and the narrowed window.** Increment 7's code
  step.

### What it grants, and nothing more

Nothing beyond D-63 C, D-64 B and D-65 option 2. No BWS identity, project, grant or scope is added or widened: the
root keeps exactly its scope (Infra Brain 598). The executor's new home is a host user with no sudo
and no `docker` group. It reaches root only through the existing helper key and forced command.
The helper's consumers-only targets let it read one thing more: the fingerprint of each lane
container's `BWS_ACCESS_TOKEN`, never the value. Each root-path narrowing in item 2 is a later
decision of Devon's and isn't granted or refused here. Nothing here declares a credential exposed
(Infra Brain 1032), and rotation ages are unchanged (Infra Brain 1031).

### Consequences

- **VPS root becomes the executor's root.** This includes Coolify's terminal and key holders and
  every infraops session, until the narrowings in item 2 say otherwise.
- **The lane-token revoke rests on a machine read again.** D-27's departure ends at the cut-over.
- **A lane restart can wait a night for its revoke alert** once the window narrows (item 3).
- **The executor stays outside Coolify**, so Infra Brain's Coolify-build, image-tag, sidecar,
  webhook, migration and branch rules don't apply to it.
- **Stale until updated, outside this amendment:** this ADR's header list, which stops at
  amendment 24; `docs/operations/rotation-executor.md`'s VM procedures (install, `set-root-token`,
  the helper's key and host pin, and the one-pass command); and ADR-0059's increment 7 text, which
  still names `lane-run --read` as the revoke's read.

### Passages it governs

Line numbers are at `ad721fc0`. This amendment edits none of them.

| Passage | Disposition |
|---|---|
| ADR-0055 decision 2, "Where it runs" (lines 133-143) | Amended by item 2: a systemd service on the VPS host under its own user. "Moves unchanged to a VPS" (line 136) is the move this amendment plans. |
| ADR-0055 decision 2, accepted residual (lines 145-149) | Superseded by item 2's T1 text from the cut-over. |
| ADR-0055 decision 3, R10 (lines 160-161) | Stands, and is restored for lane tokens by item 3. |
| ADR-0055 decision 9, a rebuilt VM (lines 298-300) and the lease ceiling (lines 301-302) | Stand. Read "VM" as the executor's host. The lease bounds item 3's one claimed read. |
| ADR-0055 asset table, the root's row (line 338) | Amended by item 2: the file is on the VPS host, readable by the executor and anything with root on that host. |
| ADR-0055 T1 (line 363) | Amended by item 2. |
| Amendment 1, T1 widened to control of production Coolify (lines 627-629) | Stands, inside item 2's T1. |
| Amendment 2, the destination-list residual, "an agent on the Mac, already T1's accepted residual" (line 721) | Stands: that agent stays inside item 2's T1, through `vps_exec`. |
| Amendment 12 item 7, the Mac instance's identity (lines 1495-1500) | Stands. The Mac's Keychain instance doesn't move. "The VM exists so an agent session on the Mac can't act as the executor" no longer holds after the cut-over, because `vps_exec` reaches the VPS host; item 2's T1 states that. |
| Amendment 17 item 2, the helper (lines 2117-2127) | Stands, plus a consumers-only target for `loaded` (item 3). |
| Amendment 17, the executor's reach (lines 2141-2142) and the helper key in `Rotation / Keepers` (lines 2152-2155) | Stand. The same key and path, from the same host (item 1). |
| Amendment 19 item 1, "on the VM instance only" (line 2240), and every later "the VM instance" or "the VM executor" in this ADR | Read as the BWS-store instance, which runs on the VPS host after the cut-over. Nothing else changes. |
| Amendment 19 item 3, the departure for a reissue (lines 2284-2285) | Ends at the cut-over (item 3). |
| Amendment 19 item 6, the executor never redeploys a lane (lines 2324-2352) | Stands. The session keeps `--token` (D-63 C). |
| Amendment 19 item 7, what each side proves, and the departure (lines 2353-2376) | The executor's proof becomes the `loaded` fingerprint. The departure (lines 2370-2376) ends at the cut-over. |
| Amendment 19 item 10, the revoke's order (lines 2403-2422) | A leak still revokes first. Otherwise the revoke follows `verify`'s `loaded` match, not HQ's report of `lane-run --read`. |
| ADR-0059 decision 4, the executor (lines 763-766) | Governed: this is the amendment it names. ADR-0059 isn't edited. |
| ADR-0059 increment 7 (lines 1050-1070) | Governed: the session runs the restart, and R10 is met by `loaded` (item 3). |
| ADR-0059 Dependencies, ADR-0055 (lines 1420-1426) | Governed: this is amendment 29. |
| ADR-0059 Amendment 2, the lane-token restart (lines 1782-1787) | Governed as increment 7 is. |
| ADR-0059 Amendment 6 decision item 4 (lines 2694-2696) | Governed: item 5 states the executor's cut-over checks. |
| ADR-0059 Amendment 6 decision item 6, the heartbeat as an alarm (lines 2709-2710) | Stands. Item 4, step 9 keeps the key. |

## Amendment 30 — The lane secrets broker (2026-11-20)

**Status: Proposed (2026-11-20).** Accepted on merge, as amendment 29 is. **Relates to:** amendment
19 (lane tokens and ping URLs), amendment 12 (BWS machine tokens), decision 3's R10 rule, and
ADR-0059 decision 1, decision 2 and "Cut-over for one lane".

### Context

Each lane holds its own BWS access token, and Bitwarden has no API to create or revoke one. Every
lane token costs Devon a browser sitting to mint (G-79) and another to revoke, and six lanes still
have to move off the Mac. About 40 of the SDS's roughly 58 credentials need his hands, and the BWS
access tokens are the largest group. The design is `docs/superpowers/specs/2026-11-20-lane-secrets-broker-design.md`
in sds-hq, Phase A of the credential-rotation redesign.

Devon answered two questions on 2026-11-20, in an interactive HQ session. They are quoted:

- **The broker's read grants.** Devon: "Project by project". The broker's account is granted read on
  each BWS project that holds a lane-read secret, not on each secret.
- **How the broker's first token reaches its resource.** Devon: "past straight into the broker's
  coolify variable". Devon mints the token and pastes it into the broker resource's
  `SECRETS_BROKER_BWS_ACCESS_TOKEN` variable in Coolify, in the same browser sitting, and stores the same token in a keeper secret in
  the BWS project `Rotation / Keepers`, as every lane token keeps a stored copy. The executor
  writes none of it, so the first token needs no package, approval or mint gate.

### Decision

1. **One broker serves every lane that is cut over to it.** `sds-lane-secrets-broker` is one Coolify
   resource on `prod-vps`, built from the lane image with its own entry command,
   `scripts/secrets_broker.py` (standard library, like `lane_run.py`). It holds one BWS identity,
   the machine account `lane-secrets-broker`, and serves no network: no port, no domain. A lane cut
   over to it has no BWS token, no token keeper and no mint gate, so this amendment replaces the
   `bws-lane-token` issue for those lanes with an allowlist entry and a socket mount.
2. **A lane's identity is the socket its request arrives on.** The broker binds
   `/data/sds/broker/<lane>/broker.sock` (mode 0600, in a directory of mode 0750) for each lane in
   its allowlist. A lane's compose mounts only its own directory, read-only at `/run/sds-broker`, so
   a lane can't reach another's socket. The request carries no token, header or claim.
3. **The allowlist is code.** `lanes/secrets-broker/allowlist.toml` lists, per lane, every BWS secret
   UUID the lane may read. It is baked into the image, so a change takes effect only through a
   reviewed orchestrator PR and a release. A UUID outside a lane's list is refused before `bws`
   runs. The broker refuses to start when the file is missing, unreadable, names a lane twice, or
   holds a malformed lane name or UUID. A test holds each lane's list equal to the UUIDs its
   launcher reads plus its ping-URL secret.
4. **The protocol returns the value or a code, never `bws`'s output.** One request per connection: a
   UUID and a newline. The reply is one JSON line, `bws secret get --output json`'s shape or
   `{"error": "<code>"}` with `not_allowed`, `bad_request` or `bws_failed`. Each read is fresh, under
   a new empty `HOME` and with `--color no` (workspace invariants 22 and 23). The broker caches and
   writes nothing, and logs one line per lane once its socket listens (outcome `listening`) and one
   per request: lane, UUID and outcome, never a value.
5. **The broker refuses to start without its token.** With `BWS_ACCESS_TOKEN` unset or empty, a bad
   allowlist, or a lane whose directory or socket can't be set up (named with its errno's name, and
   fail-fast), it prints one JSON error line and exits 2. Before the broker's first start, the
   deploy creates `/data/sds/broker`, or chowns it, to the image's `orchestrator` uid and gid and
   checks it with `ls -ln`: Coolify or Docker may create a bind directory root-owned, and the broker
   couldn't make its lanes' directories.
6. **The lane side.** `sds_bws_value` and `lane-run --read` read through `/run/sds-broker/broker.sock`
   when `lane-run` finds it mounted, and set `SDS_SECRETS_SOCKET` to name it. A brokered read never
   falls back to `bws`. A launcher's "am I in my container" test is `sds_in_lane_container`, true on
   a token or a socket. The two-argument form of `sds_bws_value` names an identity and always runs
   `bws`. The 12 lanes already on the VPS and every Mac launcher run unchanged.
7. **The deploy checks know a broker lane.** A lane is a broker lane when its compose maps no
   `BWS_ACCESS_TOKEN`, mounts exactly `/data/sds/broker/<lane>:/run/sds-broker:ro` and nothing else
   at, under or above `/data/sds/broker`, and the allowlist at the run's commit lists the lane.
   Every planned change refuses a compose with any other route to that directory on any service
   (a stray mount, `volumes_from`, a privileged service, the Docker socket, or a named volume's
   device), on a token lane too. A lane that only lost its token is none. A broker lane's `--add`
   run finishes with `--token-present false`; a token lane's with `true`.
8. **The unlock tier.** Anything holding the broker's token, or the broker's mount, can read for any
   lane, so the broker's token is in the unlock tier: the one BWS token that stays manual. Phase C
   settles how it rotates. The token lives in the resource's Coolify variable and its keeper secret in
   `Rotation / Keepers`. Its registry row, `secrets-broker-bws-token` in `.cred-consumers.toml`, is
   `rotated_by_sds = false` until then.
9. **Accepted exposure.** A mounted socket stays reachable to a lane's child process after the
   launcher drops the marker or unsets `BWS_ACCESS_TOKEN`. This is no new exposure: the child
   already could read the launcher's starting environment, token included, through `/proc`, and the
   lane's allowlist bounds what it gets.
10. **Open: a `--token` run on a broker lane.** A broker lane has no token to issue, so its
    `--token` run is refused at plan time, and Phase B settles what the run does for one.

### Phases

- **A (this amendment).** The broker, the read path, and inert landing moved onto it. Inert landing
  keeps its G-79 token as a fallback, mapped to nothing in its compose.
- **B.** HQ resumes and cuts the remaining lanes over. HQ holds every new
  `rotation-<lane>-bws-token` package until Phase A's release ships.
- **C.** The 12 earlier lanes move in batches and their tokens retire in one revoke sitting. The
  unlock tier, adapters for Coolify tokens, GitHub App keys and possibly Anthropic, and the broker
  token's own rotation arrive here, and `rotated_by_sds` becomes true for its row.

### What it grants, and nothing more

The broker's account reads each BWS project that holds a lane-read secret (Devon, "Project by
project"). It could read every secret in those projects, including ones no lane uses; the allowlist
limits what any lane gets. No lane gains a grant. No existing identity, token or grant is widened,
and no rotation age changes.

### Consequences

- **A broker lane costs no mint sitting**, and its retirement costs no revoke sitting.
- **The broker is a shared point of failure.** A restart re-binds the sockets, and a lane pass that
  starts during it fails one read and runs again on its next schedule. The lane's dead-man check
  and alerts cover a refusing or absent broker the way they cover a failed `bws` read.
- **ADR-0059's "Cut-over for one lane"** takes the broker-lane variation that section states.

## Amendment 31 — The executor's root paths stay root for development (2026-11-23)

**Status: Proposed (2026-11-23).** Accepted on merge, as amendment 30 is. **Relates to:** amendment
29 item 2 (T1, as amended, and its cut-over prerequisites), item 3 (the consumers-only helper
target and the `loaded` proof) and item 4 (the cut-over), with D-64 and D-65.

This amendment answers amendment 29 item 2's bullet "Cut-over prerequisites: the root paths narrow
first (D-65)" (lines 4128-4133; every line number here is at `4d8ca873`). It records how
orchestrator #789 (`4d8ca873`) built item 3's helper change. It changes no resource, identity,
token or grant. Devon answered two questions in an interactive session on 2026-11-22, recorded
at 2026-11-23T00:49:54Z. They are quoted from sds-hq `state/decisions.md` at commit `effa6ea`,
which records both. Each question carried its own
findings, and this amendment cites them there rather than restating them. Neither answer was made
standing, so neither is a general rule.

- **D-73** (question): "Rotation executor's move to the VPS host (increment 7), cut-over
  prerequisite 1 of 2 from your D-65 option 2 ('narrow the root paths first'): Coolify off the
  root user. New finding: on this host it doesn't narrow T1 (who can reach Rotation / Admin and the
  lane token keepers). Coolify's non-root mode is experimental and needs passwordless sudo for
  every command (its docs: 'cooluser ALL=(ALL) NOPASSWD: ALL'). And any Coolify admin or
  write-token holder can deploy a resource that binds a host path (the lane composes already bind
  /data/sds/broker), which reaches the executor's token file. The web terminal, on today for the
  localhost server, is one more such path. Which? (A) Accept for development: Coolify stays root,
  and T1 names Coolify admins and write-token holders; the security phase reopens it with D-64. (B)
  As A, plus turn off Coolify's web terminal on the localhost server (reversible hygiene; the API
  path stays). (C) Reopen D-64 now and choose a dedicated host or VM for the executor."
  **Answer (2026-11-23T00:49:54Z):** "B. Accept for development: Coolify stays root, and the
  ADR-0055 T1 text names Coolify admins and write-token holders; also turn off Coolify's web
  terminal on the localhost server (its own act). The security phase reopens it with D-64."
- **D-74** (question): "Rotation executor's move to the VPS host (increment 7), cut-over
  prerequisite 2 of 2 from D-65 option 2: infraops' vps_exec off the root user (VPS_USER defaults
  to root, infraops src/services/ssh-client.ts:32). New finding: changing the SSH user removes no
  infraops holder from T1 by itself, because the same infraops connection holds Coolify write tools
  that can create or reconfigure a compose resource with a host bind. A docker-group user is
  root-equivalent, and a sudoers allowlist isn't a boundary either: sudoers wildcards match spaces,
  so 'docker exec * lane-run *' matches far more. The sound shape is a root-owned fixed helper (a
  verb and a lane, like sds-pg-rotate) behind a non-root user, and it narrows T1 only if the
  Coolify path is also closed (prerequisite 1, option C). Which? (A) Accept for development:
  infraops stays root, and T1 names every infraops session; the security phase reopens it. (B)
  Build the helper path now: infraops code (the prod docker prefix and the file tools), the deploy
  script's printed commands, the gate hook and deploy-gate.md (an interactive session), and an
  interactive root path for root-only steps such as the broker directory install. (C) Something
  else." **Answer (2026-11-23T00:49:54Z):** "A. Accept for development: infraops stays root, and
  T1 names every infraops session; the security phase reopens it. Devon, interactive session
  2026-11-22."

Both answers hold for the development phase. The security phase reopens both with D-64, as D-64
itself is to be stress-tested before the SDS goes into production (amendment 29, "D-64 B is the
choice for the development phase").

### Decision

1. **Coolify stays root (D-73 B).** Production Coolify's `localhost` server keeps logging in to its
   own host as `root`. T1 names every Coolify admin and every holder of a Coolify API token with
   write abilities as a root-equivalent holder: any of them can deploy a resource that binds a host
   path, which reaches the executor's token file (D-73's finding; the lane composes bind
   `/data/sds/broker`, amendment 30 item 2). That replaces item 2's "can probably run a container
   that mounts a host path ... that path isn't tested" (lines 4106-4108) with D-73's finding.
   - **The terminal is off.** Coolify's web terminal on the `localhost` server
     (`kgo8kgsk40kssk4g4s4ocw40`) was turned off as its own act, `coolify-terminal-off`, on
     2026-11-23 at 00:55:38 UTC: `server_settings.is_terminal_enabled` is `false`. HQ read `false`
     through `coolify_get_server` at about 01:10 UTC. Item 2's "its web terminal is enabled"
     (lines 4103-4104) no longer holds.
   - **The API path stays.** Turning the terminal off closes one path and leaves the Coolify API,
     and the SSH private key Coolify keeps for the server (lines 4104-4106), as they were.
2. **infraops stays root (D-74 A).** `vps_exec` keeps logging in as `VPS_USER`, which defaults to
   `root` (infraops-mcp-server `src/services/ssh-client.ts`, line 32). T1 names every session with
   infraops connected. Changing that SSH user alone would remove no such session from T1, because
   the same connection holds Coolify write tools (D-74's finding), so every infraops session is
   also a Coolify write-token holder under item 1.
3. **T1, as amended, names its holders.** In amendment 29 item 2's "T1, as amended" (lines
   4116-4127), the sentence "Holders of Coolify's terminal and SSH key and every infraops
   `vps_exec` holder are among those root holders" reads, from this amendment on: "Root on that
   host is held by at least every Coolify admin, every holder of a Coolify API write token, anyone
   who can read the SSH private key Coolify keeps for its `localhost` server, every session with
   infraops connected (through `vps_exec` and through its Coolify write tools), and any other root
   login. Coolify's web terminal on that server is off (amendment 31 item 1); turning it on again
   adds whoever can open it." Devon accepts this residual for development (D-64 B, D-65 option 2,
   D-73 B, D-74 A). The rest of that bullet stands.
4. **Amendment 29's cut-over prerequisite is met.** Item 2 required that each root-path decision
   has a recorded answer, and that a narrowing Devon chooses is done and verified, before the
   cut-over starts (lines 4128-4133). Each now has a recorded answer (D-73 and D-74). The one
   narrowing chosen, the terminal off, is done and verified (item 1). This amendment doesn't plan
   the cut-over: amendment 29 item 4 stays its outline, and no cut-over plan is written.
5. **Amendment 29 item 3's helper change, as built (#789, `4d8ca873`).**
   - **A consumers-only target refuses `set` and `probe` as `bad_target`.** `_target`
     (`deploy/vps/sds-pg-rotate`, lines 103-119) returns a target for `loaded` when it names none
     of `container`, `role` and `database` (lines 114-116). For every other case it checks each
     of the three with `_ident`, which refuses a missing or malformed key as `bad_target` (lines
     97-100 and 117-118). So `set` or `probe` on a consumers-only target is refused as
     `bad_target`, and a target naming only some of the three is refused for every verb. A
     request without a valid password is refused as `bad_password` first, before the target is
     read (lines 214-216). Item 3's "Today `_target` requires all three keys for every verb"
     (lines 4151-4152) no longer holds.
   - **The fingerprint comparison is exact.** The helper prints each consumer's fingerprint as
     the sha256's first 16 hex characters, a colon and the length, or an empty string when the
     container loaded nothing (lines 37 and 191-194). `LoadedRead.mismatch`
     (`src/rotation_worker/adapters/bws_lane_token.py`, lines 205-218) compares each consumer's
     string with the keeper's, printed in the same form (`printed`, lines 193-195), by string
     equality. Any consumer that differs, or a target that lists no consumer, is a mismatch. A
     malformed answer raises (`src/rotation_worker/helper_client.py`, lines 86-98).
   - **Neither a mismatch nor an unreadable answer passes.** Before the claim, either one keeps
     the `verify` unit waiting on the 48-hour clock (`src/rotation_worker/worker.py`, lines
     650-671). The claimed step reads once more and raises on a mismatch, failing the unit
     (`bws_lane_token.py`, lines 297-320).
   - **What isn't done.** Item 3's other changes, one helper target per lane in `targets.toml` and
     a lane-token row naming it as its `helper_target`, aren't made. No `bws-lane-token` row in
     `.cred-consumers.toml` names `helper_target`, so every lane token keeps the `PATCH` proof and
     D-27's departure (amendment 19 item 7) until a row names its target. The cut-over reinstalls
     the helper first (#789's report, follow-up 1).

### What it grants, and nothing more

Nothing. No BWS identity, project, grant, scope or Coolify ability is added or widened. D-73 B and
D-74 A accept paths that amendment 29 item 2 already found and Devon already accepted (D-65 option
2); this amendment names their holders in T1. Turning off the terminal removes a path and grants
none. Nothing here declares a credential exposed (Infra Brain 1032), and rotation ages are
unchanged (Infra Brain 1031).

### Consequences

- **T1 names Coolify admins, Coolify API write-token holders and every infraops session** as root
  holders on the VPS host, until the security phase reopens D-73 and D-74 with D-64.
- **Increment 7's cut-over has no open root-path prerequisite.** What remains is its plan and its
  checks (amendment 29 items 4 and 5).
- **The terminal stays off by its own act.** Turning it on again returns its users to T1 (item 3).
- **Stale until updated, outside this amendment:** amendment 29 item 3's helper line citations
  (line 4146), which predate #789.

### Passages it governs

Line numbers are at `4d8ca873`. This amendment edits none of them.

| Passage | Disposition |
|---|---|
| Amendment 29 item 2, "Who reaches the root token's file", Coolify (lines 4102-4108) | Amended by item 1: the web terminal is off; Coolify admins and API write-token holders are root-equivalent by D-73's finding, no longer an untested path. The SSH key path stands. |
| Amendment 29 item 2, infraops `vps_exec` (lines 4109-4114) | Stands. Item 2 adds that the same connection holds Coolify write tools (D-74's finding). |
| Amendment 29 item 2, "T1, as amended" (lines 4116-4127) | Amended by item 3: its list of root holders. The rest stands. |
| Amendment 29 item 2, "Cut-over prerequisites: the root paths narrow first (D-65)" (lines 4128-4133) | Answered by items 1 and 2, and met (item 4). |
| Amendment 29 item 3, "The read" (lines 4143-4148) | Stands. Item 5 records that the comparison is exact. |
| Amendment 29 item 3, the consumers-only helper target (lines 4150-4152) | Built at `4d8ca873` (item 5). "Today `_target` requires all three keys for every verb" no longer holds. |
| Amendment 29 item 3, one target per lane and the row's `helper_target` (lines 4153-4156) | Stands, not yet made (item 5). |
| Amendment 29 item 4, the cut-over | Stands, and isn't planned here. |
| Amendment 29, "What it grants, and nothing more", "Each root-path narrowing in item 2 is a later decision of Devon's" (lines 4246-4247) | Answered by D-73 B and D-74 A. |
| Amendment 29, Consequences, "VPS root becomes the executor's root" (lines 4252-4253) | Amended by item 3: the holders are named, and the narrowings are decided. |
| ADR-0055 T1 (line 363) | Amended by amendment 29 item 2, as item 3 here reads it. |

## Amendment 32 — The lane secrets broker's Phase C: the 12 earlier lanes move and their tokens retire (2026-11-24)

**Status: Proposed (2026-11-24).** Accepted on merge, as amendment 31 is. Of the items marked
"Open, for Devon", D-81 answers 6 to 9, 12 and 14, HQ settles 11 and 13 (see Answers), and item 10
stays open until HQ settles it. **Relates to:** amendment 30
(the broker; its item 8, the unlock tier; its item 10, a `--token` run on a broker lane; its
"Phases"), amendment 29 (a lane token's revoke), amendment 19 (lane tokens and ping URLs), and
ADR-0059's "Cut-over for one lane".

This amendment plans Phase C, which amendment 30's "Phases" names (lines 4424-4426; every line
number here is at `5ae929a9`) but no text plans. The lanes' later code pull requests cite it. It
changes nothing that runs: no allowlist, compose, launcher, script, registry row or code. Devon set
Phase C first after Phase 1 in an interactive session on 2026-11-24 ("Broker Phase C, which
finishes the credential-rotation redesign", sds-hq `state/goal.md` line 5, its `Source:` line).

### Context

Four lanes moved onto the broker in Phase B: estate landing (#761), the bump driver (#762),
follow-up minting (#769) and the change window (#784). Each pull request removed the compose's
`BWS_ACCESS_TOKEN` mapping before the lane's first `--add`, so each lane's `--add` ran on a broker
compose. Inert landing moved in Phase A and keeps its G-79 token as a fallback. Twelve lanes still
read with their own BWS token: execution-watch, landing-ledger, deploy-watcher, revision-watcher,
unit-verifier, pin-watcher, membership-check, change-proposer, rotation-decomposer, work-carrier,
bump-proposer and rotation-proposer. Each has a `<lane>-bws-token` row in `.cred-consumers.toml`
whose consumer is the resource variable `<LANE>_BWS_ACCESS_TOKEN`, and none has a table in
`lanes/secrets-broker/allowlist.toml`.

Three facts read from the code at `5ae929a9` shape the plan:

- **Registration is keyed on the token row.** `lanes_in_registry` (`scripts/deploy_orchestrator.py`,
  lines 1252-1276) registers a lane only by its `<LANE>_BWS_ACCESS_TOKEN` consumer, and skips the
  broker's own row. `registered_lanes` feeds `prepare`'s lag check (`require_lanes_current`, lines
  1318-1347) and `lanes_to_follow` (lines 1350-1356). So the four Phase B lanes, which have no token
  row, are outside both today, and inert landing is inside only through its fallback token's row.
  Removing a lane's token row removes the lane from both.
- **The conversion is a `--tag` run.** A lane with a passed `--add` changes compose only through
  `--tag` (`_plan_change`, lines 1648-1699). `_plan` (lines 1700-1738) runs
  `compose_broker_refusals` on every change but `add_refusals`, which refuses a compose that maps no
  token and isn't a broker lane, only on `--add`. So a `--tag` to a compose that dropped the token
  but lacks the broker mount isn't refused at plan time. `lanes-finish` checks a token's presence
  only for `--add` and `--token` (deploy.md, lines 325-327, the paragraph before "Before a lane
  token's revoke").
- **The broker serves each connection on its own thread.** `_Server` is a
  `socketserver.ThreadingUnixStreamServer` with daemon threads (`scripts/secrets_broker.py`, line
  217), with a 5-second request read timeout and a 15-second `bws` timeout per read (lines 60-61).
  One semaphore, `MAX_FETCHES = 8` (lines 62 and 280), caps the `bws` reads running at once across
  every lane. It has no per-lane limit, so one lane's burst can hold reads for the others.

### Decision

1. **Scope.** Phase C covers the following, and nothing else:
   - **The 12 lanes** named in the context, each onto the broker, and each lane's token retired.
   - **Inert landing's fallback.** Its G-79 token (row `inert-landing-bws-token`) and the resource
     variable `INERT_LANDING_BWS_ACCESS_TOKEN`, which its compose maps to nothing, retire with the
     12. That makes 13 tokens in the revoke sitting if every lane moves. A lane that Open item 9's
     answer keeps on its token leaves the sitting: if Devon refuses the append grant, bump-proposer
     keeps its token and the sitting revokes 12.
   - **The unlock tier** (amendment 30 item 8): its mechanism and the broker token's rotation, both
     "Open, for Devon" (items 6 and 7).
   - **The missing adapters.** Eleven rows carry no `rotated_by_sds` and so fall to the manual
     checklist (`src/rotation_worker/registry.py`, line 57: only `true` counts). Three of them, the
     orchestrator M2M bearers, have an adapter (`src/rotation_worker/adapters/orchestrator_bearer.py`).
     The other eight have no module in `src/rotation_worker/adapters/`. Six of the eight are the
     design's list ("adapters for Coolify tokens, GitHub App keys and possibly Anthropic", lines
     199-200):
     three `coolify-api-token` rows (`coolify-estate-lander-token`,
     `coolify-revision-watcher-read-token`, `coolify-change-window-token`), two
     `github-app-private-key` rows (`lanes-read-app-private-key`, `lanes-push-app-private-key`) and
     one `anthropic-api-key` row (`anthropic-change-window-key`). The other two are
     `alert-hub-sender-token` rows (`alert-hub-sds-orchestrator-sender`,
     `alert-hub-report-change-window-sender`), which the design doesn't name. Phase C takes the
     design's six. Each adapter is its own amendment, which states the provider's API and the
     adapter's proof. They don't depend on the lane batches and can run in parallel with them.
   - **HQ's unregistered secrets.** The design (sds-hq
     `docs/superpowers/specs/2026-11-20-lane-secrets-broker-design.md`, lines 198-200) counts six.
     No record this session could read names them, so this amendment doesn't list them (item 11).
   - **Per-lane fetch fairness**, which sds-hq's own list names (sds-hq
     `state/history/goal-phase1-2026-11-24.md` line 58, the broker-first plan amendment of
     2026-11-20), not amendment 30. The broker's behaviour is as the context states; what to change
     is item 15.
   - **Out of scope:** the Mac Keychain token `bws-bump-proposer-token`, which has no reader in this
     repository (Open item 14 names how the revoke sitting can end it), and the D-48 B rotation
     pass. That pass follows Phase C and doesn't rotate a lane token Phase C retires (sds-hq
     `state/goal.md` line 42, "Queued", Devon's 2026-11-24 order, item 3).
     The rotation-decomposer token that D-48 found is already handled: it was reissued under D-76 B
     on 2026-11-23 at HQ gate G-92 (host executor mint unit `7583f478`, verify unit `5df0107f`,
     confirm unit `de1da609`), and Devon revoked the 2026-11-13 token. The factory-events
     passwords, which the design also places in Phase C, are out of this plan pending Devon (item
     12).
2. **The secrets the 12 lanes read.** Each lane's list is the UUIDs its launcher
   `scripts/run-<lane>.sh` reads plus the ping-URL secret its compose names in
   `SDS_DEADMAN_PING_URL_UUID`, the rule `tests/scripts/test_secrets_broker_allowlist.py` holds.
   Each secret's project is the `project` field of `.bws-secrets.toml`, not a BWS read. The broker's
   reach is the `secrets-broker-bws-token` row in `.cred-consumers.toml` (lines 1462-1503): the
   projects `SDS Operator` and `SDS / Lane Apps`, plus 13 single-secret grants. The 12 lanes read 26
   distinct secrets. The broker already reads 8 of them: 6 through its project grants and 2
   through single-secret grants it holds. The other 18 each need a new grant (item 3).

   | Secret | Registry name | Project | Broker reads it | Lanes |
   |---|---|---|---|---|
   | `b415d03b-f92d-46b1-8a9d-b4f10103df49` | ALERT_HUB_SENDER_TOKEN_SDS_BRIDGE | SDS Operator | Yes, project | execution-watch, revision-watcher, unit-verifier, pin-watcher, rotation-decomposer, work-carrier, bump-proposer, rotation-proposer |
   | `660d5846-abcb-4751-be86-b483012899eb` | orchestrator-verifier-bearer | SDS Operator | Yes, project | unit-verifier |
   | `221a48d5-3f29-4898-b300-b4820140c880` | orchestrator-system-bearer | SDS Operator | Yes, project | rotation-decomposer, work-carrier |
   | `a9843d17-e599-4e80-ab1b-b4ff0164d8e2` | work-carrier-observer-bearer | SDS Operator | Yes, project | work-carrier |
   | `314a966c-5654-4e3c-8358-b4fd01268775` | lanes-read-app-private-key | SDS / Lane Apps | Yes, project | landing-ledger, deploy-watcher, revision-watcher, unit-verifier, pin-watcher, membership-check, change-proposer, work-carrier, bump-proposer |
   | `b8f547b9-67d7-4417-adc0-b4fd0126b0f0` | lanes-push-app-private-key | SDS / Lane Apps | Yes, project | bump-proposer, rotation-proposer |
   | `314f276d-55ca-4ddc-a24d-b4a3013508cd` | change-manager/M2M_TOKEN_READ | Rotation / Keepers | Yes, single-secret | membership-check, rotation-decomposer, work-carrier, bump-proposer |
   | `2f708d08-ea5d-4d11-bde0-b50200c9a60b` | factory-events-read-password | Factory Events | Yes, single-secret | work-carrier |
   | `f793576f-e9aa-4f9d-8089-b4a000b9e2d5` | orchestrator-observer-bearer | Rotation / Keepers | No | execution-watch, landing-ledger, deploy-watcher, revision-watcher, pin-watcher, membership-check, change-proposer, work-carrier, bump-proposer, rotation-proposer |
   | `acccb346-4baa-43ec-a1d4-b4a400c048ee` | change-manager/M2M_TOKEN_PROPOSE | Rotation / Keepers | No | change-proposer, work-carrier, bump-proposer, rotation-proposer |
   | `3b9503da-eb7e-401d-b4a7-b4a400c07efb` | change-manager/M2M_TOKEN_OBSERVE | Rotation / Keepers | No | deploy-watcher |
   | `2ce0dbc7-eeac-4bf0-8204-b50200c97516` | factory-events-append-password | Factory Events | No | bump-proposer |
   | `0bf066e9-28c1-4aa7-8c3d-b50300d492a6` | coolify-revision-watcher-read-token | Ops / Platform | No | revision-watcher |
   | `726a18ba-7a38-4ecc-aa03-b49a015fd302` | APP_BRAIN_READ_KEY | brains | No | revision-watcher |
   | `97802aac-5a7f-45e8-8185-b4fc00cd318e` | execution-watch-ping-url | Rotation / Keepers | No | execution-watch |
   | `ebabdd63-2eff-4b8e-99b7-b4fd01754eae` | landing-ledger-ping-url | Rotation / Keepers | No | landing-ledger |
   | `4f7f3d8b-a83e-4ae5-a665-b4ff0004b875` | deploy-watcher-ping-url | Rotation / Keepers | No | deploy-watcher |
   | `9b2722ea-8ec1-425b-ac2e-b4ff00cba931` | revision-watcher-ping-url | Rotation / Keepers | No | revision-watcher |
   | `1cfc8352-c960-4afc-bd49-b4ff00e8783e` | unit-verifier-ping-url | Rotation / Keepers | No | unit-verifier |
   | `872684c0-899e-43b7-9c2d-b50000e88b21` | pin-watcher-ping-url | Rotation / Keepers | No | pin-watcher |
   | `5fc44897-b4df-44ae-8491-b5000129d9be` | membership-check-ping-url | Rotation / Keepers | No | membership-check |
   | `7651ccc2-7e13-417b-86da-b50100c0d4b2` | change-proposer-ping-url | Rotation / Keepers | No | change-proposer |
   | `936bbbf2-dc3b-4343-979a-b501013afc87` | rotation-decomposer-ping-url | Rotation / Keepers | No | rotation-decomposer |
   | `55fbae20-e503-4eb5-b75c-b5040164d394` | work-carrier-ping-url | Rotation / Keepers | No | work-carrier |
   | `786f75e2-3d02-4667-8607-b50601312a9d` | bump-proposer-ping-url | Rotation / Keepers | No | bump-proposer |
   | `506a5436-4643-4040-8a8b-b50601312be2` | rotation-proposer-ping-url | Rotation / Keepers | No | rotation-proposer |

   The registry name is the row id in `.cred-consumers.toml`, or the `.bws-secrets.toml` name
   where no row carries the UUID as its `bws_uuid`. The same lists, counted per lane, are as follows:

   | Lane | Secrets (launcher + ping URL) | Broker reads already | New grants |
   |---|---|---|---|
   | execution-watch | 3 (2 + 1) | 1 | 2 |
   | landing-ledger | 3 (2 + 1) | 1 | 2 |
   | deploy-watcher | 4 (3 + 1) | 1 | 3 |
   | revision-watcher | 6 (5 + 1) | 2 | 4 |
   | unit-verifier | 4 (3 + 1) | 3 | 1 |
   | pin-watcher | 4 (3 + 1) | 2 | 2 |
   | membership-check | 4 (3 + 1) | 2 | 2 |
   | change-proposer | 4 (3 + 1) | 1 | 3 |
   | rotation-decomposer | 4 (3 + 1) | 3 | 1 |
   | work-carrier | 9 (8 + 1) | 6 | 3 |
   | bump-proposer | 8 (7 + 1) | 4 | 4 |
   | rotation-proposer | 5 (4 + 1) | 2 | 3 |

   The lanes' lists hold 58 entries over 26 distinct secrets. A grant counts once however many lanes
   need it: the observer bearer's alone serves 10 lanes.
3. **The grants are Devon's, and none is granted here.** Each of the 18 is a read grant to machine
   account `lane-secrets-broker` on one secret, given in the BWS web app. Seventeen are gate steps;
   the append password waits for Open item 9:
   - **In `Rotation / Keepers`, 15:** the 12 ping-URL secrets, `orchestrator-observer-bearer`
     (`f793576f-…`), `change-manager/M2M_TOKEN_PROPOSE` (`acccb346-…`) and
     `change-manager/M2M_TOKEN_OBSERVE` (`3b9503da-…`). The single-secret form follows Devon's
     choice that the broker can't read every lane's keeper (the broker row's comment,
     `.cred-consumers.toml` lines 1480-1481).
   - **In `Ops / Platform` and `brains`, 2:** `coolify-revision-watcher-read-token` (`0bf066e9-…`)
     and `APP_BRAIN_READ_KEY` (`726a18ba-…`). The broker reaches neither project. Each is a
     single-secret grant, not a project grant, under the narrowest-credential standing answer
     (2026-11-18, sds-hq `standing-answers.md` lines 136-152): HQ plans the narrowest credential
     that does the lane's job and doesn't raise a decision to choose between a broad and a narrow
     one.
   - **In `Factory Events`, 1:** `factory-events-append-password` (`2ce0dbc7-…`), and whether the
     broker may hold it at all is Devon's decision (Open item 9). Only the approvers hold the
     append role (D-43 B; amendment 24 item 4, lines 3393-3404), and D-45 and D-47 list its holders
     by name (amendment 25 item 3, lines 3641-3655). That item refuses the append password to
     `sds-operator`, `BWS_ACCESS_TOKEN_BROAD` and `BWS_ACCESS_TOKEN_VPS_BACKUP` because each serves
     processes beyond those holders, so a grant would widen D-43 B (lines 3653-3655). The broker
     reads for every lane on its allowlist, so it would be one more append holder of that kind,
     not a replacement for the bump-proposer lane account. Until Devon answers, the grant isn't a
     gate step.

   The read password (`2f708d08-…`) needs no new grant: the broker already holds it, and serves it
   to the bump driver (ADR-0059 Amendment 17). Work-carrier's move makes the broker its read holder
   for the carrier too, in place of the work-carrier lane account that D-47's read-holder record
   names (amendment 25 item 3). That change to the record is also Devon's to accept (Open item 9).

   The broker row's `reach_secrets` records each grant once Devon has given it, in a registry
   change after his gate.
4. **Each lane's route.** Each step is its own change or act, in this order. The templates are
   `docs/operations/secrets-broker.md`, "Add a lane" (lines 57-91), and the Phase B pull requests
   #761, #762, #769 and #784.
   1. **The broker directory.** On `prod-vps`, as root:
      `install -d -o 999 -g 999 -m 0750 /data/sds/broker/<lane>` (deploy.md, line 1230). It comes
      before the broker's move to a release that lists the lane and before the lane's redeploy, so
      Docker never creates the bind source root-owned.
   2. **The grants.** Devon's single-secret grants for the lane's secrets that the broker doesn't
      already read (item 2), as a gate.
   3. **One reviewed pull request** with, in this repository:
      - the lane's `[lanes.<lane>]` table in `lanes/secrets-broker/allowlist.toml`, every UUID with
        its registry name;
      - the lane's compose: no `BWS_ACCESS_TOKEN` mapping, and the mount
        `/data/sds/broker/<lane>:/run/sds-broker:ro` and nothing else at, under or above
        `/data/sds/broker`;
      - the launcher's host and empty-token checks through `sds_in_lane_container`, and
        `env -u BWS_ACCESS_TOKEN -u SDS_SECRETS_SOCKET` (or `unset`) before each child process that
        must not read, as #761 and #762 did;
      - the broker row's `reach_secrets`, and the comments in `.bws-secrets.toml` and
        `.cred-consumers.toml` that say which identity reads each secret in the lane's container;
      - the lane's `helper_target` removed from its `<lane>-bws-token` row: each of the 12 token
        lanes' rows names one (#793, `51a8bbe7`), inert landing's row names none, and
        `tests/rotation_worker/test_lane_token_helper_targets.py` (line 108 onward) refuses a broker
        lane that names one. The lane's target in the host helper's
        `/etc/sds-pg-rotate/targets.toml` is removed as its own root act on `prod-vps` after the
        merge.
   4. **The release.** The deploy command builds and serves it.
   5. **The broker's move** to that release ("Move the broker to a new release"), and its
      `listening` line for the lane.
   6. **The lane's conversion**, a `--tag` run to the release, then `lanes-finish`. For a
      registered lane this is the follow-up run that `finish` prints after the release
      (deploy.md, lines 154-157), so the pull request's merge is the commit point: steps 1, 2 and 5
      close before the lane's follow-up run. Until they do, the lane stays on its earlier tag, and in
      production mode `prepare`'s lag check refuses the next release.
   7. **The read proof.** In the lane's container, `lane-run --read` over every UUID in the lane's
      table prints `ok` for each, and over one UUID outside it prints `refused`. The run's report
      also records that the container's environment holds no `BWS_ACCESS_TOKEN`, read by name
      without printing a value.
   8. **The token's retirement** waits for the revoke sitting (item 5). Until then the resource
      keeps `<LANE>_BWS_ACCESS_TOKEN`, mapped to nothing, so a `--tag` back to a release before the
      conversion still finds the lane's token.

   Before batch 1, Open items 8 and 13 have recorded answers. Two code changes also come before the
   first lane's step 6, each in its own reviewed pull request:
   - **A broker lane stays registered without a token row.** Registration must not depend on a
     token row before any token row is removed. How it does is Open item 13.
   - **A conversion's compose is checked.** A `--tag` whose compose maps no token must be refused
     unless the compose is a broker lane's, as `add_refusals` refuses it for `--add`.
5. **The token's retirement, and the one revoke sitting.** After the last batch's step 8, and
   once Open items 10 and 14 have recorded answers. Each count of 13 in this item holds if every
   lane moves. A lane that Open item 9's answer keeps on its token is left out of the precondition,
   the sitting, the registry removal and the package hold, and its row keeps rotating as a lane
   token's does: if Devon refuses the append grant, each 13 here reads 12, and bump-proposer is the
   lane left out.
   1. **The precondition, restated for a broker cut-over.** deploy.md's "Before a lane token's
      revoke" (lines 334-343) asks for a `--token` run's `lanes-finish` on the redeployed container.
      A broker lane's `--token` run is refused at plan time (amendment 30 item 10), so the
      precondition for a retirement is the following, for each of the 13 lanes: its conversion's
      `lanes-finish` passed on the redeployed container, step 7's read proof printed `ok` for every
      listed UUID on that container, and the container holds no `BWS_ACCESS_TOKEN`. Inert
      landing's conversion is its Phase A move. No `--token` run is made on a broker lane unless
      Open item 10's answer defines one, and then the precondition adds its recorded run.
      Amendment 29's `loaded` match proves a reissue's new value and has no new value to prove here,
      so it doesn't apply.
   2. **The sitting.** Devon revokes the 13 lane tokens (the 12 and inert landing's) in one BWS web
      app sitting. Whether he also deletes each lane's machine account is Open item 14. After the
      sitting, a session deletes each resource's `<LANE>_BWS_ACCESS_TOKEN` variable through
      infraops, the 13 and any that the Phase B lanes' resources still hold mapped to nothing (#761
      and #762 kept theirs). The composes don't map them, so no container changes. From the
      sitting on, a lane can't roll back past its conversion release: the token it would map is
      revoked.
   3. **The registry.** One pull request removes the 13 `<lane>-bws-token` rows and updates the
      tests that read them (`tests/rotation_worker/test_*_rows.py` and the others that name the
      row ids). The keeper secrets in `Rotation / Keepers` stay: deleting a BWS secret is Devon's act
      and isn't planned here.
   4. **A leak still revokes first** (amendment 19 item 10; Infra Brain 1032). A token that a
      record names as exposed is rotated or revoked now, outside the sitting and outside this plan's
      order. The token D-48 found was already reissued and its old value revoked (item 1, "Out of
      scope").
   5. **No reissue between conversion and revoke.** Every one of the 13 rows is `rotated_by_sds =
      true` with a `rotate_requested` date. HQ holds every `rotation-<lane>-bws-token` package for a
      lane in a batch from that batch's start until the revoke, as amendment 30's Phase B held new
      ones, so no mint sitting is spent on a token that is about to retire.
6. **Open, for Devon: the unlock tier's mechanism** (amendment 30 item 8; the design's Touch ID
   idea, lines 198-200). This amendment doesn't settle it. **Blocks:** Phase C's close ("Phase C is
   done when").
   **Answered:** see Answers (2026-11-24).
7. **Open, for Devon: how the broker's own token rotates** (amendment 30 item 8). Bitwarden has no
   API to create or revoke an access token (amendment 30, Context), so every rotation has a mint by
   Devon. This amendment doesn't settle it, and `secrets-broker-bws-token` stays
   `rotated_by_sds = false` until it does. Amendment 30's Phase C expects the row to become
   `rotated_by_sds = true`. **Blocks:** Phase C's close.
   **Answered:** see Answers (2026-11-24).
8. **Open, for Devon: whether lanes may move before the broker's token can rotate.** Each batch
   widens what the broker's token reaches. This amendment doesn't settle it. **Blocks:** batch 1.
   If Devon answers no, batch 1 also waits for item 7's answer to be in place.
   **Answered:** see Answers (2026-11-24).
9. **Open, for Devon: the factory-events holders.** Whether the broker may hold the append password
   (`factory-events-append-password`) at all, and whether he accepts the broker as the carrier's
   read holder in place of the work-carrier lane account (item 3). **Blocks:** the append-password
   grant, and steps 3 to 8 of batch 4's bump-proposer (the append password) and work-carrier (the
   read holder). Their other step 2 grants, the ping URLs included, are among the 17 gate steps and
   don't wait for it. If Devon allows the append grant and accepts the read holder, both lanes move
   as the others do. If he refuses either, the lane it concerns stays on its token, and its token
   leaves the revoke sitting (item 5): refusing the append grant keeps bump-proposer's, and the
   sitting revokes 12. Every other grant in item 3 is a gate step for Devon, listed and not given.
   **Answered:** see Answers (2026-11-24).
10. **Open, for Devon: a `--token` run on a broker lane** (amendment 30 item 10, which Phase B
    didn't settle). Until it's answered, the run stays refused at plan time. **Blocks:** the revoke
    sitting (item 5), whose precondition depends on the answer.
    **HQ's to settle:** see Answers (2026-11-24); not settled yet.
11. **Open, for Devon: HQ's unregistered secrets.** Which secrets they are, and which repository's
    registry takes each row. **Blocks:** Phase C's close.
    **Settled by HQ:** see Answers (2026-11-24).
12. **Open, for Devon: the factory-events passwords.** The design places "the factory-events
    passwords turned on" in Phase C; amendment 30's Phases list and this plan's scope don't. Both
    rows stay `rotated_by_sds = false` (amendment 24's open item FE-4). They are out of Phase C
    pending Devon's answer. If he puts them in, they join "Phase C is done when". **Blocks:** Phase
    C's close, which waits for his answer either way.
    **Answered:** see Answers (2026-11-24).
13. **Open, for Devon: how a broker lane stays registered** (item 4). It decides whether
    `prepare`'s lag check and `lanes_to_follow` cover broker lanes. **Blocks:** batch 1, through the
    registration code change in item 4.
    **Settled by HQ:** see Answers (2026-11-24).
14. **Open, for Devon: the lane machine accounts at the revoke.** Whether the sitting deletes each
    lane's machine account and its grants, or revokes the tokens only. bump-proposer's lane token is
    a second token of the `bump-proposer` machine account, which also holds the Mac Keychain token
    `bws-bump-proposer-token` (`.cred-consumers.toml`, the bump-proposer block's comment, lines
    1229-1231, and that token's row, lines 1743-1750). Deleting that account also ends the Keychain
    token and its read of the append password, which removes one of amendment 25 item 3's named
    append holders. If Open item 9's answer keeps bump-proposer on its lane token, deleting the
    account ends that token too. **Blocks:** the revoke sitting (item 5).
    **Answered:** see Answers (2026-11-24).
15. **Per-lane fetch fairness.** No change is decided here. The broker's limits are as the context
    states, the shared `MAX_FETCHES = 8` cap included; a change to them is a code pull request that
    cites a measured wait in the broker's log.

### Answers (2026-11-24)

Devon answered Open items 6 to 9, 12 and 14 as decision D-81 in an interactive session on
2026-11-24 (recorded 23:32 UTC): "Please write up the D18 answer, as well as exactly HQ's
recommendation for D-81." HQ's recommendation, and so the answer, was "A, B, A, A, A, B (A for
bump-proposer's account), B", over seven parts. The options are in
`docs/superpowers/reports/broker-phase-c-adr-question-answered.md`.

| D-81 part | Open item | Answer |
|---|---|---|
| (1) | 6, the unlock tier's mechanism | A: a named tier, no mechanism; each use stays Devon's browser act. |
| (2) | 7, how the broker's token rotates | B: through the rotation executor's `bws-lane-token` adapter with the loaded-fingerprint proof (amendment 29); Devon still mints and revokes. |
| (3) | 8, lanes before the broker's token can rotate | A: yes, batches may start before (2) is built; the hand route exists. |
| (4) | 9, the append password | A: the broker may hold `factory-events-append-password`, for the bump proposer only. |
| (5) | 9, the carrier's read holder | A: the broker is the work carrier's chain read holder in place of its lane account. |
| (6) | 14, the accounts at the revoke | B with A for bump-proposer: the sitting revokes the tokens and deletes each lane machine account, except `bump-proposer`, whose lane token alone is revoked (the account keeps the Mac Keychain token `bws-bump-proposer-token`). |
| (7) | 12, the factory-events passwords | B: out of Phase C; FE-4 stays with the factory-events work. |

For (4), codebase-review D18, 2026-11-24, keeps session-relayed approvals and lets an approval
naming `devon` verify only when its event's actor is an interactive session. The broker serves
lanes, so it can't approve as Devon.

With (4) A and (5) A, all 12 lanes move, and the revoke sitting retires 13 tokens. The append
password's grant joins the other 17 as a gate step for Devon (item 3), named and not given.

HQ settled three items itself under build-mode rule 2 (mechanism, not what is built). These are
HQ's, not Devon's (construction-mode ruling,
`~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`):

- **Open item 13:** A. A broker lane is registered through its allowlist table, by an
  orchestrator code change on the branch `hq/broker-registration-allowlist`, which lands before
  batch 1.
- **Open item 11:** HQ lists each unregistered secret (name, BWS UUID, reader, owning repository)
  in its next handoff, and a worker registers each in that repository's `.cred-consumers.toml` (the
  question file's recommendation 8). Only the procedure is settled; the secrets aren't named yet.
- **Open item 10:** open, and HQ's to settle with a recorded read-proof run. It isn't settled yet.

### Phase C is done when

Phase C closes only when each of the following holds, and none can close without its Open item
answered:

- **The lanes:** every lane that Open item 9's answer moves runs on the broker: all 12 if Devon
  allows the append grant and accepts the read holder, and 11 if he refuses the append grant;
  D-81 (4) A and (5) A: all 12. Each passed step 7's read proof, and its token was revoked in the
  sitting (item 5). A lane his answer keeps on its token stays on it.
- **The revoke:** the sitting's tokens are revoked, their rows removed and their variables deleted
  (item 5): 13 if every lane moves, and 12 if Devon refuses the append grant; D-81 (4) A: 13.
- **The unlock tier** is the one Devon chose (Open item 6): D-81 (1) A, a named tier with no
  mechanism; each use stays Devon's browser act.
- **The broker's token** rotates by the route Devon chose (Open item 7), and its row says so. D-81
  (2) B: the row becomes `rotated_by_sds = true` when the `bws-lane-token` adapter route is built.
- **HQ's unregistered secrets** are named and registered (Open item 11).
- **The design's six adapters** (item 1) are each built or decided by their own amendment.
- **Fetch fairness** (item 15) is closed with no change, unless a wait is measured in the broker's
  log; then the code pull request that cites it has merged.
- **The factory-events passwords**, only if Devon puts them in Phase C (Open item 12). D-81 (7) B
  removes them from Phase C's close.

### Proposed batches and order

At most three lanes per batch is a suggestion, not a rule. The order puts read-only watchers first,
the lanes that act on change-manager next, and last the batch that holds the factory-events
secrets (Open item 9). Batch 1 starts only after Open items 8 and 13 are answered.

| Batch | Lanes | New grants | Which |
|---|---|---|---|
| 1 | execution-watch, landing-ledger, pin-watcher | 4 | `orchestrator-observer-bearer` and the 3 lanes' ping URLs |
| 2 | unit-verifier, membership-check, deploy-watcher | 4 | `change-manager/M2M_TOKEN_OBSERVE` and the 3 lanes' ping URLs |
| 3 | change-proposer, rotation-decomposer, rotation-proposer | 4 | `change-manager/M2M_TOKEN_PROPOSE` and the 3 lanes' ping URLs |
| 4 | work-carrier, bump-proposer, revision-watcher | 6 | `factory-events-append-password` (if Open item 9 allows it), `coolify-revision-watcher-read-token`, `APP_BRAIN_READ_KEY` and the 3 lanes' ping URLs |

The new grants total 18. Each batch counts a grant only where it is first needed: batch 2's
membership-check and deploy-watcher read the observer bearer that batch 1's grant gives. Devon can
give a batch's grants in one sitting, or 17 in one sitting before batch 1: the append password
waits for Open item 9's answer. Within a batch the
lanes share one release and one broker move, and each lane takes its own steps 6 to 8. The two code
changes in item 4 land before batch 1. The revoke sitting (item 5) comes once, after batch 4, and
retires the moved lanes' tokens and inert landing's together: 13, or 12 if Devon refuses the append
grant (Open item 9); D-81 (4) A: 13.

### What it grants, and nothing more

Nothing. No BWS identity, project, grant, scope, network path or Coolify ability is added or
widened. All 18 grants in item 3 are gate steps for Devon, named and not given; the append
password became one under D-81 (4) A. Open items 6 to 9, 12 and 14 are answered by D-81; Open items
10, 11 and 13 are HQ's under build-mode rule 2 (see Answers), and their answers add no grant. No
endpoint is added, so the HTTPS rule for public FQDNs (Infra Brain 212)
doesn't apply. Nothing here declares a credential exposed, and no rotation age changes.

### Consequences

- **When Phase C finishes, no scheduled lane holds a BWS token**, unless Open item 9's answer keeps
  one on its token: refusing the append grant keeps bump-proposer's. Otherwise the broker's token is
  the one BWS token the lanes depend on, which makes Open items 6 to 8 the remaining work on it.
  D-81 (4) A and (5) A: no lane keeps its token.
- **The broker's reach grows by up to 18 single-secret grants**: 17 gate steps, and the append
  password if Devon allows it under Open item 9; D-81 (4) A: 18.
- **The revoke is one sitting** for 13 tokens, or 12 if Devon refuses the append grant, after the
  last batch; D-81 (4) A: 13.
- **Registration changes** before any token row is removed (Open item 13).

### Passages it governs

Line numbers are at `5ae929a9`. This amendment edits none of them.

| Passage | Disposition |
|---|---|
| Amendment 30 item 8, the unlock tier (lines 4406-4410) | Stands. Its mechanism and the token's rotation are Open items 6 and 7, answered by D-81 (1) A and (2) B. |
| Amendment 30 item 10, a `--token` run on a broker lane (lines 4415-4416) | Stands, still open (Open item 10). Item 5 doesn't use the run. |
| Amendment 30, "Phases", C (lines 4424-4426) | Planned by this amendment, with a "done when". The factory-events passwords are Open item 12, out of Phase C under D-81 (7) B. |
| Amendment 30, "Phases", B, the held packages (lines 4422-4423) | Extended by item 5: HQ holds each batch's lanes' token packages until the revoke. |
| Amendment 29, a lane token's revoke waits on the loaded fingerprint | Stands for a reissue. A retirement has no new value, so item 5 states its own precondition. |
| deploy.md, "Before a lane token's revoke" (lines 334-343) | Restated for a broker cut-over by item 5. The page changes with the first lane's pull request. |
| deploy.md, "Registered lanes and the lane-token matcher" (lines 345-353) | Changes with Open item 13's answer. |
| Amendment 25 item 3, the accounts that read the factory-events secrets (lines 3641-3655), and D-43 B's append holders (amendment 24 item 4, lines 3393-3404) | Amended by D-81 (4) A and (5) A: the broker becomes one more append holder (bump proposer only) and the carrier's read holder in place of the work-carrier lane account; the grant stays a gate step. |
| `docs/operations/secrets-broker.md`, "Add a lane" (lines 57-91) | Stands as each lane's template (item 4). |
| `docs/operations/secrets-broker.md`, "What the broker doesn't change" (lines 138-142) | Each batch's pull request narrows it to the lanes still on a token. |

## Amendment 33 — A rotation adapter for Coolify API tokens: a person mints and revokes, the executor proves (2026-11-24)

**Status: Proposed (2026-11-24).** Accepted on merge, as amendment 31 is, except the items marked
"Open, for Devon", which stay open until he answers. **Relates to:** amendment 32 item 1 ("The
missing adapters"), amendment 1 (the Coolify measurements and decision), amendment 15 (a type is
only its provider), amendment 19 (lane tokens and the inbox), amendment 29 (the loaded
fingerprint) and amendment 30 (the lane secrets broker).

Amendment 32 item 1 puts the design's six missing adapters in Phase C and says each is "its own
amendment, which states the provider's API and the adapter's proof" (lines 4651-4664; every line
number in this amendment is at `7af40971` unless it names another file or commit). This amendment
covers the first class, `coolify-api-token`. It changes nothing that runs: no registry row,
adapter, type, compose, launcher, grant or package. The code pull request that builds the adapter
cites it.

### Context

#### The three rows

Each is read from `.cred-consumers.toml` at `7af40971`. None is `rotated_by_sds`, so each falls to
the manual checklist (`src/rotation_worker/registry.py`, line 57), and
`tests/architecture/test_rotation_agreement.py` (line 31) lists the class in `UNTYPED`, the
classes the SDS deliberately has no type for.

| Row (lines) | Scopes | Keeper (BWS project) | Consumers | Fingerprint recorded |
|---|---|---|---|---|
| `coolify-estate-lander-token` (622-647) | `read`, `write`, `deploy`; never `read:sensitive` (probed in-process 2026-10-30, the row's comment) | `f9f59233-…`, `COOLIFY_TOKEN_SDS_ESTATE_LANDER`, in `SDS Operator` | `runtime-fetch` by `scripts/run-estate-landing.sh`, on `--submit` passes only, not fatal if unreadable; in `sds-lane-estate-landing` through the broker (a Phase B lane, #761). Healthchecks check `sds-estate-landing`, pinged every pass. The note also names a Mac path, with the narrow `sds-operator` identity | Yes: `fingerprint_sha256_8 = "594d2c0d"`, 8 hex characters, not the executor's 16-character prefix and length (`rotation_worker/values.py`, `PREFIX_LENGTH`) |
| `coolify-revision-watcher-read-token` (649-674) | `read` only; never `read:sensitive` (Devon, HQ gate G-62, 2026-11-15) | `0bf066e9-…`, `prod-coolify-read-token`, in `Ops / Platform` | `runtime-fetch` by `scripts/run-revision-watcher.sh`, every pass, as `REVISION_WATCHER_PLATFORM_TOKEN`; in its container with the lane's own BWS token until Phase C batch 4 moves it onto the broker (amendment 32, "Proposed batches and order"). Check `sds-revision-watcher`. The note also names a Mac path, with the broad identity | No: no session has read the value |
| `coolify-change-window-token` (1784-1809) | `read`, `write`, `deploy`, team-wide; never `read:sensitive` (Devon, HQ gate G-88, 2026-11-22) | `3d41395a-…`, `change-window-coolify-token`, in `Rotation / Keepers` | `runtime-fetch` by `scripts/run-change-window.sh`, on `--submit` passes only (`lanes/change-window/crontab`, line 10: daily at 04:00), and fatal if unreadable (`run-change-window.sh`, lines 129-130); in `sds-lane-change-window` through the broker (#784). Check `Change Manager OPs Check` | No |

No row has a `coolify-env`, `coolify-env-held` or Actions-secret destination: each token's only
copies are its keeper and what a lane fetches per pass. `launchctl list` on `operator-mac`
(2026-11-24) shows no estate-landing or revision-watcher job, so the two Mac paths the notes name
have no scheduled reader there. The code pull request settles them (item 5).

The executor's own production Coolify token, in `Rotation / Coolify` (decision 4, lines 226-230;
the asset table, line 356), isn't one of these rows. It stays in the hand-rotated set, and this
amendment doesn't change that.

#### The provider's API

Production Coolify runs `4.0.0-beta.473` (infraops `coolify_version`, 2026-11-24). Everything in
this subsection is a reading of Coolify's source at tag `v4.0.0-beta.473` (commit
`57ea0764b8f0a491fd1d30bedc5cbe281744b36c`), not a measurement, as amendment 1 labels source
readings.

- **No REST route creates, lists or revokes an API token.** `routes/api.php` has no token route.
  Its two routes that sound like one are other things: `/security/keys` (lines 62-67) manages SSH
  private keys, and `/cloud-tokens` (lines 69-74) manages cloud-provider tokens. The API
  controllers at the tag (`app/Http/Controllers/Api/`) include no token controller.
- **A token is created and revoked only in the web UI**, by a signed-in user, through the Livewire
  component `app/Livewire/Security/ApiTokens.php`: `addNewToken` calls Sanctum's `createToken`
  with a description and the chosen abilities (lines 77-100), and `revoke` deletes one of the
  signed-in user's own tokens (lines 102-112). Its abilities are `read`, `read:sensitive`,
  `write`, `deploy` and `root` (lines 44-75). The component has no expiry and no per-resource
  access, which agrees with amendment 1's "no token can be limited to particular applications"
  (line 618).
- **Abilities are checked before the handler.** Each route names `api.ability:<ability>`
  (`app/Http/Middleware/ApiAbility.php`): a token without it gets 403
  `Missing required permissions: <ability>`, and an unknown or revoked token gets 401
  `Unauthenticated.`. `root` passes every check. `read:sensitive` is not checked by a route; it
  sets the request attribute `can_read_sensitive` (`app/Http/Middleware/ApiSensitiveData.php`),
  which handlers read to include or omit values. Amendment 1 question 1 measured that difference on
  dev: a `read` + `read:sensitive` token's envs response carries `value` and `real_value`, and a
  token without `read:sensitive` gets neither field (line 540).

Coolify's documentation at its repository's head (`coollabsio/coolify-docs`, commit
`7e97a720fbef2d6e5b9928d0a246c3035c0efbba`, 2026-11-24,
`content/docs/core/security/credentials/api-tokens.mdx`) describes a later version: tokens with an expiry, resource-limited access, a `delete` permission,
**Pause** and **Roll**. None of that is in the deployed component, and this amendment relies on
none of it. A Coolify upgrade that brings it is its own change.

So the API can't mint, and the shape is the existing one for a provider without a mint or revoke
API: a person mints into the inbox, the executor proves and stores, and a person revokes after the
executor's proof (`src/rotation_worker/adapters/bitbucket.py`, whose type is
`Capabilities(machine_mint=False, machine_revoke=False)`, `types.py` line 39).

### Decision

1. **The type.** `coolify-api-token` becomes a credential type with
   `Capabilities(machine_mint=False, machine_revoke=False)`: not hosted, with an overlap, no issue
   plan, no `env_at_distribute`. Its keepers are in the BWS store (`store_kind` `bws-secret`), so
   the VPS host executor takes its units. `plans.derive` (amendment 15) then gives, from each row's
   destinations (a keeper and `runtime-fetch` readers, so no `distribute`), the same plan
   Bitbucket's is:
   1. **mint** (person): mint and stage. Devon creates the token in Coolify's UI with the row's
      abilities, under the row's description, and pastes it into the inbox
      (`Rotation / Inbox`; `inbox.pasted`). He ticks `deploy` first: ticking it resets the
      selection to `deploy` alone (`ApiTokens.php`, lines 67-68), so `read` and `write` go after
      it.
   2. **store** (machine): `verify_new`, then `store`, which writes the keeper and reads it back.
   3. **verify** (machine): `verify_consumers`. The unit waits unclaimed until every
      `runtime-fetch` reader has run since the keeper's revision date (`worker._gates_retire`,
      `worker.py` line 954), and its completion sends the revoke alert.
   4. **revoke** (person): Devon revokes the old token in Coolify's UI.
   5. **confirm** (machine): `confirm_dead`.

   Devon still makes two browser acts per rotation, as every type without a mint API does. What
   leaves his hands is the keeper write, the proofs and the timing of the revoke.
2. **The probe and its known-bad control.** `probe` sends `GET /api/v1/version` (`routes/api.php`
   line 43, `api.ability:read`), which every row's token may call: live is exactly 200. The control
   is a random value of the token's shape, a numeric id, `|` and a random secret, which Sanctum
   refuses with 401 before any handler runs. `dead_statuses` is `{401}`. The control and the
   subject go through the same probe, so a refused control proves the 200 came from the token
   (amendment 5's rule). `probe_before_revoke` is the same request.
3. **The identity check.** `same_identity` holds when both values answer 200 and
   `GET /api/v1/teams/current` (line 46) names the same team id for each. That's the account check
   only. All three rows' tokens are in the one team, so it can't tell them apart. The inbox's
   existing rules do: it binds a value only while no other rotation waits on a person's mint
   (`inbox.other_mints`, `inbox.py` lines 12 and 120), and never a value another SDS-rotated keeper
   of the type holds (the other-keeper check, `inbox.py` lines 20 and 145-172). Two rows with the
   same abilities are told apart by those rules, not by item 4.
4. **The scope check, in `verify_new`.** A token's abilities can't be read back through the API, so
   the new value is probed against routes whose ability middleware answers before the handler. A
   request the token may make reaches a handler that refuses it without writing; a request it may
   not make gets 403 `Missing required permissions: …`. Each row declares its abilities in a new
   field (item 5), and `verify_new` refuses a value whose answers don't match the declaration:
   - **`write`:** `PATCH /api/v1/projects/<random UUID>` with a body holding only a field the
     handler doesn't allow. With `write`, `update_project` answers 422 `Validation failed.` before
     it looks up a project (`ProjectController.php`, lines 341-390), so it writes nothing even if
     the UUID existed. Without `write`, 403.
   - **`deploy`:** `GET /api/v1/deploy` with no `uuid` and no `tag`. With `deploy`, `deploy` answers
     400 `You must provide uuid or tag.` (`DeployController.php`, lines 351-385) and deploys
     nothing.
     Without `deploy`, 403.
   - **`read:sensitive` absent:** no route answers differently by status, so this needs a read whose
     response body differs. How is Open item 13.

   For the revision watcher's token, both probes must answer 403. That holds the read-only token's
   scope: a mint with `write` or `deploy` never reaches its keeper. These answers are read from
   source and are unmeasured; the code pull request measures them on `dev-coolify` first (Open item
   14). The adapter's confined client permits exactly the requests in items 2 to 4, and nothing
   else (`estate_clients.confined.ConfinedClient`, as `bitbucket.py` builds it).
5. **The consumers' proof, and rule R10.** Every consumer of these three rows fetches the keeper
   fresh on each pass, and none holds the value at rest:
   - The broker caches and writes nothing; each read is a new `bws` read (amendment 30 item 4,
     lines 4386-4389; `scripts/secrets_broker.py`, line 18).
   - The revision watcher, until batch 4, reads with its own lane token through `bws` each pass
     (`sds_bws_value`), and nothing in its container keeps the value between passes.

   So a consumer can't stay on the old value past a pass that started after the keeper changed.
   Amendment 29's `loaded` fingerprint proves a value a container loaded at start and keeps. These
   consumers keep none, so it doesn't apply, and no helper target is named. The proof is the
   existing one for a scheduled `runtime-fetch` reader ("How each consumer kind is verified", line
   342): the keeper readback in `store`, then each row's `healthchecks_check` reporting a run that
   began after the keeper's revision date, which the `verify` unit waits on before Devon is asked to
   revoke. That meets R10 and Infra Brain 1032 by machine evidence: the consumer set is the
   registry's, the readers are scheduled, and no revoke alert is sent before each has run.

   What a green ping proves differs per row, and the code pull request's tests state each:
   - **Change window:** the read is fatal, so a green `--submit` pass read the new value. The lane
     runs daily at 04:00, so the `verify` unit can wait up to a day.
   - **Revision watcher:** the read is every pass. A green pass read the new value. Its launcher
     refuses to run outside its container, with no `BWS_ACCESS_TOKEN`
     (`scripts/run-revision-watcher.sh`, lines 59-61).
   - **Estate landing:** the read is not fatal, and the check is pinged every pass. A green pass
     after the store proves that a pass began after the store, so any value it read was the new
     one, but not that the read succeeded. That isn't a revoke risk, because no consumer can be
     left on the old value. It is a gap in proving the lander can use the token: the lander uses
     it only to re-point or restore a landing, which `verify_new`'s `write` and `deploy` probes
     cover for the value itself. **Carried gap:** the lander's read is proven only by the broker's
     per-request log line (lane, UUID, outcome), which the executor can't read. Open item 15 names
     the options. The adapter's code pull request carries the gap until that item is answered.
   - **The Mac paths in two rows' notes:** no scheduled job reads them (Context). A reader run only
     when invoked is a human-attested exception ("How each consumer kind is verified"), which a
     derived plan refuses (`checks._check_runtime_fetch_verifiable`, `checks.py` line 311). The
     code pull request removes each from its row's note after confirming it has no reader, or the
     row stays out of the type. **Carried gap**, verified by that pull request.
6. **The revoke and confirm-dead.** Devon revokes the old token in Coolify's UI after the `verify`
   unit's alert. `confirm_dead` probes the old value and passes on 401 beside the control's 401
   and a 200 from the new value. No other credential is needed: the old value proves its own
   revocation. After an exposure, Devon may revoke first: exposure response is his decision
   (decision 6; Infra Brain 1032), and D-26 (amendment 19 item 10) is the precedent for a revoke
   before the rotation runs. So the adapter sets `revoke_may_come_first`, as the lane-token adapter
   does: the probe before the revoke takes either answer.
7. **The keepers' projects.** The `store` step writes the keeper, so the keeper must be in a
   project where the executor's identity can write. The executor's reach, from the freshest
   sources:

   | Project | Row whose keeper is there | Executor reads | Executor writes | Source |
   |---|---|---|---|---|
   | `Rotation / Keepers` | `coolify-change-window-token` | Yes | Yes | `docs/operations/rotation-executor.md`, line 677; decision 4 and amendment 6 |
   | `SDS Operator` | `coolify-estate-lander-token` | No | No | Decision 4, lines 215-217 ("not `SDS Operator`"); `rotation-executor.md`, line 677 |
   | `Ops / Platform` | `coolify-revision-watcher-read-token` | Only the Healthchecks Management key's secret (`51509d5e-…`), by direct grant (amendment 23, D-36) | No | `rotation-executor.md`, line 677; amendment 23, lines 3239-3248 |

   So the change window's row can rotate as soon as the adapter is built, and the other two need
   their keepers moved, or a grant, first. Each is Devon's act (Open item 11). A project grant on
   `SDS Operator` is ruled out: the SYSTEM and VERIFIER bearers are there, and decision 4 keeps them
   outside the executor's reach (lines 223-225). Moving a keeper into `Rotation / Keepers` widens
   what the executor's identity reads by two Coolify tokens. Its own Coolify token in
   `Rotation / Coolify` already holds every ability both have, so the widening adds no Coolify
   ability.
8. **No new network path.** The adapter calls production Coolify at the executor's existing
   `coolify_url`, the tailnet address (`rotation_worker/coolify.py`, lines 16-17 and 35-37;
   amendment 29 item 1), from the host its own Coolify token already calls from. It sends the consumer tokens only
   there.
9. **Logging and failure.** The adapter logs nothing of a value: evidence carries only fingerprints
   (the sha256 prefix and length, `rotation_worker/values.py`) through `contract.py`, as every
   adapter's does. A probe's transport failure returns no answer, so the step is indeterminate and
   fails the unit, which a requeue retries (amendment 5). An unexpected status fails `verify_new`
   with the status named. Nothing is swallowed.
10. **What the code pull request changes.** Named here, none changed by this amendment:
    - `src/rotation_worker/adapters/coolify_api_token.py`, the provider: `NAME` and `PROVIDER`
      (`coolify`, the rows' `provider`), a `PLAN` constant built with `derive(...)` from the type's
      capabilities, as `postgres_role.py` builds its own (line 153), the probes in items 2 to 4, `check_row` refusing a row
      without its declared abilities, and `mint`, `host`, `consumer_loaded` and `revoke` raising
      `ContractError`, as `bitbucket.py` does.
    - `src/rotation_worker/adapters/__init__.py`: the import and an `ADAPTERS` entry with
      `capabilities=TYPES[...]`, `check_row` and `revoke_may_come_first=True`.
    - `src/rotation_worker/types.py`: a `TYPES` entry with the capabilities in item 1, and
      `CLASS_TYPES["coolify-api-token"]`.
    - `tests/architecture/test_rotation_agreement.py`: `coolify-api-token` leaves `UNTYPED`.
    - `tests/architecture/test_rotation_edges.py`: a `PROVIDER_EDGES` entry for the adapter's
      reach.
    - `tests/architecture/test_wsp21_invariant_scan.py`: the module in `OUTBOUND_ALLOWLIST`, with
      its reason, beside `bitbucket.py` (line 835).
    - `layers.toml`, if no pattern there already names the new module.
    - `src/orchestrator/factory-policy.toml`: the adapter name in the `rotation_adapters` lists
      that name `bitbucket` (lines 464 and 575).
    - `.cred-consumers.toml`: each row's declared abilities field, the Mac notes settled
      (item 5), and `rotated_by_sds = true` with a `rotate_requested` date only for a row whose
      keeper the executor can write (item 7) and whose Open items are answered. Another worker
      edits that file, so the pull request rebases on it.
    - intent-packages: one `rotation-<row id>` package per row the rotation proposer generates.
      Its `approval-policy.toml` needs no entry. That file's only by-name type list is
      `grant.types.*`, the standing approval of a first issue (`bws-lane-token` and
      `healthchecks-ping-url`). The type has no issue plan, so it has no place there, and every
      revision of its packages stays Devon's to approve by name.
    - `docs/operations/rotation-executor.md`: a section for the type, with the mint note in item 1
      (tick `deploy` first).
11. **Open, for Devon: the keepers of the estate lander's and the revision watcher's tokens.** The
    executor can't write either (item 7). Options:
    - **A.** Move each keeper into `Rotation / Keepers` (his act: the move needs write on both
      projects, amendment 1 question 5), after each consumer account is given a single-secret read
      grant there and proven to read it, as decision 4 requires. For the estate lander that's
      `lane-secrets-broker`, which reads it today through its `SDS Operator` project grant. For the
      revision watcher, recommended after Phase C's revoke sitting, when its only reader is the
      broker: amendment 32 item 3's planned grant on `0bf066e9-…` in `Ops / Platform` is then given
      in `Rotation / Keepers` instead. Decision 4's same-change rule applies to each move: the
      secret's `project` in `.bws-secrets.toml` (the entries at lines 173 and 324) and the broker
      row's `reach_secrets` in `.cred-consumers.toml` change in the same change.
    - **B.** A direct read and write grant for `rotation-executor` on each secret where it is. That
      gives the executor its first write outside the rotation projects.
    - **C.** Leave both rows manual; only the change window's rotates.

    **Recommendation: A**, since decision 4 already says every rotated keeper moves to
    `Rotation / Keepers` and A adds no new kind of reach. **Blocks:** those two rows'
    `rotated_by_sds`.
12. **Open, for Devon: the executor creating or revoking Coolify tokens itself.** The deployed API
    has no route for it (Context). The only route is the web UI's Livewire component, which needs a
    signed-in user's session: Devon's, or a new Coolify user's. Options:
    - **A.** No. A person mints and revokes, as item 1 plans.
    - **B.** Give the executor a Coolify user session to drive the UI. That's token-admin reach,
      a new identity and a credential outside any API.

    **Recommendation: A.** It's re-askable if a Coolify upgrade adds a token API. **Blocks:**
    nothing in item 1.
13. **Open, for Devon: proving a new token lacks `read:sensitive`.** No route answers it by status
    (item 4). Options:
    - **A.** No machine proof. Devon's mint is the only record, and the `write` and `deploy` probes
      still run.

    A `root` mint passes item 4's probes as a `read` + `write` + `deploy` mint does, since `root`
    passes every ability check (`ApiAbility.php`). Only B or C catches it, because a `root` token
    reads sensitive values.
    - **B.** Read the envs of one named existing application and check that no `value` or
      `real_value` field is present (amendment 1 question 1's discriminator). A wrong mint would
      put that application's values in the executor's memory, never in a log.
    - **C.** As B, against a canary application Devon creates with one variable that holds no
      secret, used only by this probe. That's one new Coolify resource, and a wrong mint then reaches
      nothing secret through the probe.

    **Recommendation: C**, because the revision watcher's token must stay read-only and C proves
    it without exposing a secret. A is the fallback if Devon wants no new resource. **Blocks:** the
    adapter's `verify_new`, for the last of item 4's checks.
14. **Open, for Devon: the measurement tokens.** Item 4's answers are read from source. Measuring
    them on `dev-coolify` needs three throwaway tokens Devon mints there (`read`;
    `read` + `write` + `deploy`; `read` + `read:sensitive`), stored in a BWS project the measuring
    session reads, and revoked after the run. They are probe credentials, so they're his to mint.
    **Recommendation:** yes, before the adapter merges, as amendment 1 measured before its
    decision. **Blocks:** the code pull request's merge.
15. **Open, for Devon: proving the estate lander's read** (item 5's carried gap). Options:
    - **A.** Carry the gap. The lander's next re-point or restore is its own proof. A failed read
      alerts `landing-not-pointed-back` only when a bad landing needs re-pointing, so between such
      landings a failed read goes unseen.
    - **B.** The executor reads the broker's log lines for the lane and UUID. That's a new reach:
      the executor has no Docker access on the host (amendment 29 item 1).
    - **C.** The estate-landing lane files an observation each `--submit` pass naming the token's
      fingerprint, and the row names it as `observation_source` and `observation_subject` (the
      existing alternative to `healthchecks_check`). That's a lane change in its own pull request.

    **Recommendation: A** for the first rotation, since no consumer can stay on the old value, and
    C if the gap is to close. **Blocks:** nothing in the change window's row. The estate lander's
    row's `rotated_by_sds` doesn't wait on this item: under A the gap is carried. It waits on item
    11.

### What it grants, and nothing more

Nothing. No BWS identity, project, grant or scope, no Coolify token, user or ability, no network
path and no probe credential is added or widened. Every grant, move, resource and credential in
Open items 11 to 15 is a gate step for Devon, named and not given. The read-only token's scope
isn't changed, and item 4 adds the check that holds it. No endpoint is added, so the HTTPS rule for
public FQDNs (Infra Brain 212) doesn't apply. Nothing here declares a credential exposed, and no
rotation age changes.

### Consequences

- **One Coolify token can rotate once the adapter merges**: the change window's, whose keeper the
  executor already writes. The other two wait on Open item 11.
- **Each rotation still has two browser acts for Devon**, the mint and the revoke, because the
  deployed Coolify has no token API. The keeper write, the proofs and the revoke's timing become the
  executor's.
- **The revoke follows machine evidence** for every scheduled reader, by the existing
  `runtime-fetch` route. No helper target and no loaded-fingerprint read is added.
- **The executor's identity reads two more Coolify tokens** if Open item 11 is answered A, with no
  Coolify ability beyond its own token's.

### Passages it governs

This amendment edits none of them.

| Passage | Disposition |
|---|---|
| Amendment 32 item 1, "The missing adapters" (lines 4651-4664) | The `coolify-api-token` class is decided here. The `github-app-private-key` and `anthropic-api-key` classes stay for their own amendments. |
| Amendment 32 item 3, the `Ops / Platform` grant on `0bf066e9-…` (line 4747) | Stands. If Open item 11 is answered A after the revoke sitting, the grant is given in `Rotation / Keepers` instead. |
| Amendment 32's broker row, the `SDS Operator` project grant (`.cred-consumers.toml`, line 1489) | Stands. Under Open item 11 A, the estate lander's keeper leaves the project, and the broker reads it by a single-secret grant in `Rotation / Keepers`. |
| Decision 4, the keepers move to `Rotation / Keepers` (lines 205-217) | Applied to two more keepers, under Open item 11 A. |
| "How each consumer kind is verified", the scheduled `runtime-fetch` row (line 342) | Stands, and is the consumers' proof (item 5). |
| Amendment 29 item 3, a lane token's revoke waits on the loaded fingerprint | Stands for lane tokens. It doesn't apply to these rows, whose consumers keep no value (item 5). |
| Amendment 1, the Coolify decision (lines 610-656) | Stands. Its source readings are extended by this amendment's, at the same version. |
| `tests/architecture/test_rotation_agreement.py`, `UNTYPED` (line 31) | Changes with the code pull request (item 10). |

## Amendment 34 — The broker token's rotation route and the unlock tier (2026-11-26)

**Status: Proposed (2026-11-26).** Accepted on merge, as amendment 31 is, except the items marked
"Open, for Devon", which stay open until he answers, and the item marked "Open, for HQ", which HQ
settles under build-mode rule 2. **Relates to:** amendment 30 item 8 (the unlock tier), amendment
32 Open items 6 and 7 and their answers, D-81 (1) A and (2) B, amendment 29 items 2 and 3 (T1 and
the loaded fingerprint), amendment 19 items 6, 7 and 10 (lane tokens), and decision 4 (what the
root can reach).

Amendment 32 put two parts of Phase C to Devon (Open items 6 and 7, lines 4853-4862; every line
number in this amendment is at `8b326f2d` unless it names another file or commit). He answered both
as D-81 in an interactive session on 2026-11-24 (lines 4907-4923):

- **D-81 (1) A:** "a named tier, no mechanism; each use stays Devon's browser act." (line 4917)
- **D-81 (2) B:** "through the rotation executor's `bws-lane-token` adapter with the
  loaded-fingerprint proof (amendment 29); Devon still mints and revokes." (line 4918)

This amendment plans both. It changes nothing that runs: no registry row, adapter, test, helper
target, compose, grant, token or package. The code pull request that builds the route cites it.
The options D-81 answered are in `docs/superpowers/reports/broker-phase-c-adr-question-answered.md`
(lines 7-46). Option (2) B there reads: "The executor writes the variable and keeper, the session
redeploys and proves, and Devon revokes the old token after the `loaded` match" (lines 33-36).

### Context

#### The row

`secrets-broker-bws-token` (`.cred-consumers.toml`, lines 1488-1509) has class and provider
`bws-lane-token`, keeper `1d78f917-6065-4d40-ad1c-b508013d8c9a`, `rotated_by_sds = false`, 17
`reach_secrets` and two `reach_projects` (`SDS Operator` and `SDS / Lane Apps`). It has two
consumers: the keeper (`bws-secret`, "in Rotation / Keepers; only the executor reads it", lines
1497-1500) and a `coolify-env` with `instance = "prod"`, `resource_type = "service"`, `uuid =
"wogvks6obmvv9fdotlgnw7r3"`, `key = "SECRETS_BROKER_BWS_ACCESS_TOKEN"` and `redeploy = false` (lines
1502-1509). Its comment (lines 1465-1470) says the token "is in the unlock tier", that Devon minted
it and pasted it into the variable on 2026-11-20 with the keeper copy, and that the row carries no
`created`. It names no `helper_target`.

The broker's compose maps the variable into the container as `BWS_ACCESS_TOKEN`
(`lanes/secrets-broker/compose.yaml`, line 26), in the one service `secrets-broker` (line 15). The
broker reads it from its environment at start and refuses to start without it
(`scripts/secrets_broker.py`, lines 66 and 325), and passes it to each `bws` read
(lines 120-138).

#### What the adapter already accepts

- **The write fits a service.** `lane_variable` requires exactly one `coolify-env` besides the
  keeper, with `redeploy` false, `instance` `prod`, `resource_type` `service`, and a key matching
  `<LANE>_BWS_ACCESS_TOKEN` (`src/rotation_worker/adapters/bws_lane_token.py`, lines 72 and
  99-113). `SECRETS_BROKER_BWS_ACCESS_TOKEN` matches. The provider builds its Coolify client for
  a `service` with `restarts=False` (lines 187-189). For a service, `CoolifyClient` writes
  `PATCH /api/v1/services/<uuid>/envs` once and permits nothing else when `restarts` is false
  (`src/rotation_worker/coolify.py`, lines 62-74, 88-92 and 94-100). So no Coolify ability, path
  or client changes. `test_the_row_is_one_the_lane_token_adapter_accepts`
  (`tests/rotation_worker/test_secrets_broker_row.py`, lines 75-79) already runs `check_row` and
  `lane_variable` on the real row.
- **The plan.** For this row's two destinations the type derives two plans (`plans.candidates`,
  derived in-process at `8b326f2d`). The rotation plan is: mint (person: mint, stage); `store`
  (`verify_new`, `store`, `distribute`); `verify` (`verify_consumers`); revoke (person:
  `retire_old`); and `confirm` (`confirm_dead`). The issue plan is one `store` unit with
  `verify_consumers` and no revoke. The factory policy recognises `confirm`, `store` and `verify`
  for `bws-lane-token` (`src/orchestrator/factory-policy.toml`, lines 484-485 and 606-607).
- **The keeper.** The keeper is in `Rotation / Keepers` (the row's note, line 1500). The executor's
  root identity has write on that project (decision 4, lines 214-217), so it reads and writes the
  keeper with no new grant.
- **The executor never restarts it.** Amendment 19 item 6 stands (amendment 29 item 3, lines
  4152-4155).

#### What the adapter doesn't yet do for this row

1. **Which plan runs.** A row with no `created` and no `last_rotated` is served by infraops with a
   null `last_rotated` (`docs/operations/rotation-executor.md`, lines 1606-1612). The proposer
   then reads a first issue (`src/rotation_proposer/cli.py`, lines 436-441) and `plan_for` picks
   the issue plan (`src/rotation_proposer/generate.py`, lines 149-150). The keeper holds Devon's
   hand-pasted token, not the inbox's empty marker. With nothing staged, the executor's checks let
   the issue's mint wait (`src/rotation_worker/checks.py`, lines 184-196). Once Devon's new token
   is staged, `_check_issue_fact` refuses the unit, because the keeper holds neither the marker nor
   the staged value (lines 197-198). The issue's `store` would refuse it too
   (`src/rotation_worker/contract.py`, lines 710-724). So the issue fails closed after Devon's
   mint, and the mint sitting is wasted. The standing approval also
   covers only the issue plan (`src/rotation_proposer/standing_approval.py`, lines 160-180).
   Item 3 settles this.
2. **The bind on a project grant.** The inbox binds a pasted token before the mint's unit is
   claimed (`worker.py`, lines 12-13; `inbox.py`), through `bind_refusal`, which passes only when the pasted token's reach equals
   the row's declared reach (`bws_lane_token.py`, lines 270-283). The live side lists every project
   and every secret the token can see and digests them all (`_REACH`,
   `src/rotation_worker/adapters/bws_machine_token.py`, lines 101-117 and 175-182). The declared side
   digests `reach_projects` and `reach_secrets` (lines 120-124). This row is the only lane-token row
   with a non-empty `reach_projects` (`.cred-consumers.toml`, line 1495; every other lane-token row has
   `reach_projects = []`). Its `reach_secrets` lists the 17 single-secret grants and none of the
   secrets the two projects hold: `b415d03b-…` (`SDS Operator`, amendment 32 item 2's table, line
   4692) is absent. If the SDK's secret listing includes a project's secrets, the bind refuses
   every broker token as written. Whether it does is unmeasured: no bind has run on a token with a
   project grant, because Devon pasted the broker's first token by hand. Item 5 settles this.
3. **The loaded proof.** A reissue proves the restarted container's token only on a row that names
   `helper_target` (`bws_lane_token.py`, lines 116-124 and 170-181). This row names none, and
   `test_no_broker_lane_and_not_the_broker_s_own_token_names_one`
   (`tests/rotation_worker/test_lane_token_helper_targets.py`, lines 108-118) refuses one on it.
   `docs/operations/rotation-executor.md` (lines 1679-1683) says the same. Without it a reissue
   would count the `PATCH` answering 2xx, which proves nothing about the running broker. Item 4
   settles this.

#### What the root reaches through this keeper

The broker's token reads the project `SDS Operator` (the row's `reach_projects`, line 1495;
amendment 30's "Project by project"). That project holds the SYSTEM bearer
(row `orchestrator-system-bearer`, `221a48d5-…`, `.cred-consumers.toml` lines 176-181; in
`.bws-secrets.toml`, lines 28-30, the secret's name is `ORCHESTRATOR_M2M_CREDENTIALS`) and the
VERIFIER bearer (`orchestrator-verifier-bearer`, `660d5846-…`, `.bws-secrets.toml` lines 66-68;
`.cred-consumers.toml` lines 214-219). At `8b326f2d`, four broker lanes read the SYSTEM bearer
through the broker: inert landing, estate landing, the bump driver and follow-up minting
(`lanes/secrets-broker/allowlist.toml`, lines 23, 32, 40 and 52). No broker lane reads the VERIFIER
bearer yet. Amendment 32 moves unit-verifier (the VERIFIER bearer) and rotation-decomposer and
work-carrier (the SYSTEM bearer) onto the broker in batches 2 to 4 (item 2's table, lines 4693-4694).
The reach that follows doesn't depend on those lanes: the project grant already reads both bearers.

The executor's root reads every keeper in `Rotation / Keepers`, the broker's among them. Since
Devon stored that keeper on 2026-11-20 (amendment 30's Context, lines 4360-4364), whoever holds
the root can read the broker's token and, with it, the SYSTEM and VERIFIER bearers. Several passages
say those bearers are outside the root's reach: decision 4 (lines 217 and 223-225), T1 (line 365),
amendment 29 item 2 (lines 4139-4140), and amendment 33 item 7 (lines 5212-5214). They hold for the
root's own grants, and not once the reach through this keeper is counted. D-81 (2) B's option text
(above) doesn't name it. The route makes the executor log in with the broker's token on each
rotation, which lists ids and reads no value, and write it to the broker's variable. It adds no
grant: the reach exists today because of where the keeper is. Open item 10 puts it to Devon.

### Decision

1. **The unlock tier (D-81 (1) A).** The tier is a name with no mechanism. Nothing is built for it:
   no registry field, Keychain item, Touch ID prompt or code.
   - **What it names.** A credential is in the tier when anything holding it can read for others:
     amendment 30 item 8's reason, "Anything holding the broker's token, or the broker's mount, can
     read for any lane" (lines 4408-4409). The tier is about who acts, not about how a rotation
     runs. A credential in it can still rotate through the executor (item 2), and each use of it
     stays Devon's act in the provider's web app.
   - **Which credentials are in it.** The broker's token, `secrets-broker-bws-token`. In this
     repository and in sds-hq's `state/` files, it is the only credential named as unlock-tier: the
     row's comment (`.cred-consumers.toml`, line 1467), amendment 30 item 8, and
     `docs/operations/secrets-broker.md` (lines 140-142). D-81 (1)'s option A says "the registry
     marks" each unlock-tier key; the row's comment is that mark, and no field is added. Its text
     also says "and any other key that mints or reads for others"
     (`broker-phase-c-adr-question-answered.md`, lines 11-13). The executor's root token reads
     every keeper, and so reads for others. It is in decision 4's hand-rotated set (lines
     220-221), and every use of it is already Devon's or the executor's own. Whether the tier also
     names it is Open item 11.
   - **What "each use is Devon's browser act" means.** A use is an act on the credential's
     identity at its provider:
     - **Mint:** Devon creates each new access token for machine account `lane-secrets-broker` in
       the BWS web app. Under item 2 he pastes it into the inbox (`Rotation / Inbox`), not into
       the Coolify variable or the keeper. The executor never mints one: Bitwarden has no API for
       it (amendment 30's Context, lines 4350-4351), and `BwsLaneTokenProvider.mint` raises
       (`bws_lane_token.py`, lines 258-259).
     - **Grant:** every read grant to `lane-secrets-broker`, by project or by secret, is Devon's
       act in the web app, as each of amendment 32 item 3's 18 is a gate step (lines 4739-4769).
       No machine path gives or removes one.
     - **Revoke:** Devon revokes each old token in the web app, after the `verify` unit's revoke
       alert, or first after a leak (item 7). `revoke` raises (`bws_lane_token.py`, lines
       339-340).
     - **Approval:** each revision of the broker's package is approved by Devon by name (item 3).
   - **What becomes the executor's under item 2.** Binding the pasted token, the keeper write and
     readback, the variable's `PATCH`, the loaded-fingerprint read, the revoke alert's timing and
     `confirm_dead`.
2. **The route (D-81 (2) B).** The broker's token rotates through the `bws-lane-token` adapter's
   rotation plan, as a lane token does, with these differences: the broker is restarted by the
   move route (item 6), its proof reads the broker's own container (item 4), and its bind reads a
   project grant (item 5).
   1. **Mint** (Devon): he mints the token in the BWS web app and pastes it into the inbox.
   2. **The bind** (executor, before the claim): the inbox binds the pasted token (item 5).
   3. **`store`** (executor): `verify_new`, `store` (keeper write and readback), and
      `distribute`, one `PATCH` of `SECRETS_BROKER_BWS_ACCESS_TOKEN` on service
      `wogvks6obmvv9fdotlgnw7r3` with no restart. The broker keeps running on the old token,
      which stays live. The unit writes a hosted service (`plans.py`, lines 215 and 218-231), so
      its claim asks the `live_estate` window.
   4. **Restart** (session, item 6): `coolify_deploy` on the broker at its current tag. A lane's
      `--token` run relies on the same thing: a `coolify_deploy` after the executor's `PATCH`
      starts the container on the written variable (`docs/operations/deploy.md`, lines 203-205 and
      289-291). For the broker this is unmeasured until the first rotation's `loaded` read.
   5. **`verify`** (executor): it waits unclaimed until the helper's `loaded` for target
      `secrets-broker` equals the keeper's fingerprint, then claims and reads once more
      (amendment 29 item 3, lines 4175-4182). Its completion sends the revoke alert.
   6. **Revoke** (Devon): in the BWS web app.
   7. **`confirm`** (executor): `confirm_dead` on the old token.
3. **The row's anchor, and who approves.** The code pull request adds `created = "2026-11-20"`, the
   date the row's comment gives for Devon's paste. infraops reads it as the rotation anchor and
   serves it as `last_rotated` (`rotation-executor.md`, lines 1606-1611). The proposer then reads
   the credential as issued, and `plan_for` picks the rotation plan (`generate.py`, lines 157-162).
   The standing approval's `issue_plan` condition refuses a rotation plan (`standing_approval.py`,
   lines 160-163 and 177-179), so no policy approves the package: each revision is Devon's to approve by
   name. That keeps D-81 (2) B's "Devon still mints and revokes", and adds his approval click,
   which every rotation package outside amendment 22 needs. The row's comment changes with the
   field: it gives `created` here, where the landing ledger's block gives the reason for having
   none (lines 764-766), which doesn't apply to a token already issued by hand.
4. **The loaded-fingerprint proof.**
   - **The target.** A consumers-only target in the host helper's root-owned
     `/etc/sds-pg-rotate/targets.toml` (`docs/operations/rotation-executor.md`, lines 1360-1374):
     `[target.secrets-broker]` with one `consumer` whose `container` is the broker's container and
     whose `env` is `BWS_ACCESS_TOKEN`, the name the container loads (compose line 26), not the
     Coolify key. The helper accepts a consumers-only target for `loaded` (`deploy/vps/sds-pg-rotate`,
     lines 103-119) and answers each consumer's sha256 first 16 hex characters and length, never
     the value (lines 179-195).
   - **The container's name.** By this repository's rule, `<service>-<resource uuid>`
     (`rotation-executor.md`, lines 1379-1382): `secrets-broker-wogvks6obmvv9fdotlgnw7r3`. It is
     read from the repository and isn't checked live.
   - **Whether the name is stable.** D-63's unchecked cost (amendment 29, lines 4041-4042) is still
     a verify item there (line 4278-4279), and no record at `8b326f2d` closes it. The broker is a
     Coolify service, as each lane is, so the same naming applies. Its only redeploy is
     `coolify_deploy` (item 6), never a lane's `--add`, `--tag`, `--token` or `--schedule`. If a
     redeploy renamed the container, `loaded` would answer for no container. The helper's `docker exec`
     fails, it prints an empty fingerprint (lines 191-195), the unit waits, and `rotation-unit-waiting`
     fires at 48 hours. It fails safe and never passes. The code pull request's first exit check
     (item 9) reads the name live.
   - **The image.** The helper runs `sh -c printf` in the container (line 191). The broker runs the
     lane image (`compose.yaml`, line 16) with its own entrypoint, and `docker exec` doesn't use
     the entrypoint. So the shell is the lane image's, which lane targets already rely on.
   - **The row.** `helper_target = "secrets-broker"`. The
     `test_each_target_is_the_service_that_loads_the_row_s_variable` derivation (lines 52-70 and
     121-127) already maps this row's key to lane name `secrets-broker` and to compose service
     `secrets-broker`, whose `BWS_ACCESS_TOKEN` is the row's variable, so it holds without change.
   - **What it grants.** The helper key and forced command are unchanged (amendment 29 item 1). The
     target lets the executor read one more fingerprint, the broker's loaded token, never a value.
     D-81 (2)'s option text names "its own `helper_target` and a target in the host helper"
     (`broker-phase-c-adr-question-answered.md`, lines 39-45).
5. **Open, for HQ: the bind on a project grant** (Context, "What the adapter doesn't yet do",
   point 2). This is a check's form, not an authority, so HQ settles it under build-mode rule 2
   (construction-mode ruling, `~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`).
   It turns on one measurement: whether a project-granted token's listing includes the project's
   secrets, read as ids and counts only. The credential for that measurement is Open item 13.
   - **A.** If the listing doesn't include them, nothing changes.
   - **B.** If it does, `reach_secrets` lists every secret in both projects. Any secret Devon adds
     to either project then makes every bind refuse until the row is updated. It fails closed, but
     it costs a mint sitting each time.
   - **C.** If it does, a row with a non-empty `reach_projects` binds when the listed projects equal
     the declared ones exactly and every declared secret is listed. The inbox's other-keeper check
     and `sharing_reach` still apply. That's weaker than an exact match: a token with one extra
     single-secret grant would bind.

   **Recommendation: measure first, then A if it holds, otherwise C.** Among the registry's
   lane-token rows, only this one declares project reach (Context), so the project set tells its
   token from every lane token's. The change is to `_REACH` or `bind_refusal`, and it never prints
   or digests a value. **Blocks:** the row's `rotated_by_sds`.
6. **The restart, and what it costs each broker lane.**
   - **Who.** The session, as for lane tokens (D-63 C, amendment 29 item 3). The executor keeps
     `restarts=False`. The command is the second call of the broker's move route,
     `coolify_deploy` on `wogvks6obmvv9fdotlgnw7r3`, at the tag the broker already runs
     (`docs/operations/secrets-broker.md`, lines 41-50). `deploy_orchestrator.py` has no
     `--lane secrets-broker` (line 43 there), and the deploy command skips the broker's row
     (`scripts/deploy_orchestrator.py`, lines 1038-1041 and 1263). Never `coolify_control`
     `restart`: it prunes images while starting (line 48 there; workspace invariant #3). The
     `coolify_deploy` is announced as the move is, and the deploy gate pass already admits it on
     the broker (line 47 there). So the session gains no new Coolify ability.
   - **When.** After the `store` unit completes, and before the `verify` unit's 48-hour clock runs
     out. Before the deploy, the session reads Healthchecks and holds the restart while any broker
     lane's check shows `started`, as the deploy command waits for a lane's quiet slot (lines
     1043-1046). It also avoids the change window's daily 04:00 `--submit` pass, whose read is fatal
     (amendment 33, line 5054).
   - **What it costs.** While the broker restarts, which takes seconds, no lane has a socket. A
     lane pass that starts in that gap fails its read and runs again on its next schedule
     (`secrets-broker.md`, lines 52-55; amendment 30's Consequences, lines 4440-4442). After the
     restart the session checks for one `listening` line per allowlisted lane and runs one lane's
     `lane-run --read`, as the move route asks.
   - **A failed start.** If the broker can't read with the new token, it exits 2 or answers
     `bws_failed`, and every broker lane's reads fail. The bind has already proven that the token
     logs in and sees the declared reach, so a failed start means the token was revoked since, or
     a Coolify fault. Between `store` and `confirm`, the old value is in the rotation's quarantine
     secret in `Rotation / Work` (`contract.py`, lines 15-16 and 399-400; decision 4, line 210).
     This adapter has no verb that writes it back: the executor's one restore verb, `pg-restore`,
     serves only `postgres-role` rows (`src/rotation_worker/cli.py`, lines 483-489). Open item 12
     puts the rollback to Devon.
7. **A leak revokes first, and costs every broker lane.** After an exposure Devon may revoke first
   (amendment 19 item 10; D-26; Infra Brain 1032), and the adapter accepts the old token refused
   (`bws_lane_token.py`, lines 33-35). For the broker, the running token is then dead, so every
   broker lane's reads fail (`bws_failed`, amendment 30 item 4) until `store` completes and the
   session restarts the broker. The bound is set by three things:
   - **The executor's pass:** a systemd timer every five minutes (`rotation-executor.md`, line 10)
     picks up the paste.
   - **The window:** the `store` claim waits for the `live_estate` window (item 2, step 3). Once
     the window narrows to 02:00-06:00 (amendment 29 item 3, lines 4183-4189), a revoke-first in
     the day leaves every broker lane without reads until the next 02:00, plus the restart.
   - **Devon's mint:** the outage lasts at least until he mints and pastes the new token.

   An exposure response may therefore need the hand route of D-81 (2) option A for the broker's
   token, outside the window. That is Devon's call at the time, as exposure response is
   (decision 6).
8. **Logging and failure.** The route adds no logging of its own. Every step logs through the
   executor's structured logging and `contract.py`, whose evidence holds fingerprints only, the
   sha256 prefix and length (`rotation_worker/values.py`). The `loaded` answer is a fingerprint,
   and the helper never prints a value (`sds-pg-rotate`, lines 9-15). Every failed step fails
   closed: a refused bind writes nothing, a `PATCH` answering 4xx raises `CoolifyRefused`
   (`coolify.py`, lines 40-45 and 113-114), and a mismatched or unreadable `loaded` waits before
   the claim and fails the claimed step (`bws_lane_token.py`, lines 214-227 and 320-329). No
   revoke alert is sent before `verify` passes, so the old token is never revoked before the
   broker is shown to hold the new one (R10, Infra Brain 1032).
9. **The code pull request.** Named here, none changed by this amendment:
   - `.cred-consumers.toml`, row `secrets-broker-bws-token`: `helper_target = "secrets-broker"`
     (item 4); `created = "2026-11-20"` (item 3); `reach_secrets` as item 5's answer needs; the
     comment's "stays manual" text rewritten to state the route and that the token is still in the
     unlock tier. `rotated_by_sds = true` and a `rotate_requested` date are set only when every
     exit check passes and Open items 10, 12 and 13 are answered. Another worker edits that file, so
     the pull request rebases on it. The exposure and consumer fields keep their form: no
     `[[credential.exposure]]` is added, and the two consumers are unchanged.
   - `tests/rotation_worker/test_lane_token_helper_targets.py`:
     `test_no_broker_lane_and_not_the_broker_s_own_token_names_one` (lines 108-118) keeps its
     refusal for broker lanes, and requires the broker's own token to name `secrets-broker`.
     `test_the_install_page_lists_each_target_s_container_as_derived` (lines 179-192) then requires
     the page's table to list the broker's target.
   - `tests/rotation_worker/test_secrets_broker_row.py`: `test_the_row_has_no_age_anchor` (lines
     99-101) becomes a check that `created` is the paste date and that the proposer derives the
     rotation plan. `test_the_row_is_not_handed_to_the_sds` (lines 70-72) changes only in the
     commit that sets `rotated_by_sds`.
   - `src/rotation_worker/adapters/bws_lane_token.py` and `bws_machine_token.py`, only if item 5's
     measurement needs C, with tests that a project-reach token binds and a lane token still binds
     only on an exact match. The module docstrings change with it.
   - `src/rotation_worker/CLAUDE.md`: the `helper_target` invariant names the broker's own token as
     the one row that isn't a lane and names a target.
   - `docs/operations/rotation-executor.md`: the target table (lines 1384-1394) gains
     `secrets-broker`; "Lane tokens and ping URLs" (lines 1676-1683) states the broker's route.
   - `docs/operations/secrets-broker.md`: a "Rotate the broker's token" section with item 2's
     steps, item 6's restart and item 7's leak case. "What the broker doesn't change" (lines
     138-142) stops calling the token manual. Another worker edits this page, so the pull request
     rebases on it.
   - **A root act, not in the pull request:** the `[target.secrets-broker]` entry in
     `/etc/sds-pg-rotate/targets.toml` on `prod-vps`, written as root after a `docker ps` confirms the
     name, as increment 7's cut-over did for the lane targets.

   **Exit checks**, each before `rotated_by_sds = true`:
   1. **The target answers for the running broker, with no mint.** As root on `prod-vps`, the
      helper's `loaded` for `secrets-broker` answers one consumer,
      `secrets-broker-wogvks6obmvv9fdotlgnw7r3/BWS_ACCESS_TOKEN`, with a non-empty fingerprint. That
      proves the target and the name. Whether it equals the keeper's is shown by the first
      rotation, since only the executor reads the keeper.
   2. **The name survives a redeploy.** The same read after the next broker move
      (`coolify_deploy`) answers the same consumer, non-empty. That closes amendment 29's verify
      item for the broker.
   3. **Item 5's measurement** is recorded in the pull request's report, as ids and counts only.
   4. **One real rotation of the broker's token** is the proof. The bind passes, `verify` passes on
      the `loaded` match, Devon revokes, and `confirm_dead` passes. After the restart every
      allowlisted lane logs `listening`, and one lane's `lane-run --read` prints `ok`. A canary
      can't stand in: no other resource loads the broker's token, and a second broker would be a
      new identity.
10. **Open, for Devon: the root's reach to the SYSTEM and VERIFIER bearers through this keeper**
    (Context, "What the root reaches through this keeper"). It exists today. The route doesn't
    create it, but it uses the token on each rotation. Options:
    - **A. Accept as found.** T1 states that whoever holds the root holds the broker's token, and
      through it every secret the broker reads, the SYSTEM and VERIFIER bearers included. The
      passages that call those bearers outside the root's reach are read as the root's own grants.
      This follows the D-65 precedent (amendment 29, lines 4060-4073).
    - **B. Keep the keeper out of the root's reach.** Move the broker's keeper to a project only
      Devon reads. The executor can't then store the token, so D-81 (2) B can't be built, and the
      token rotates by hand (D-81 (2) option A). This also ends today's reach, which exists
      without the route.
    - **C. Narrow the broker's `SDS Operator` grant to single secrets.** This reverses Devon's
      "Project by project" for that project. It removes the project's other secrets from the reach,
      but not the two bearers: four broker lanes read the SYSTEM bearer today, and unit-verifier
      needs the VERIFIER bearer from batch 2. Keeping both bearers out would also keep
      unit-verifier, rotation-decomposer, work-carrier and the four Phase B lanes on tokens of their
      own, which reverses amendment 32 and amendment 30's Phase B.

    **Recommendation: A for the development phase**, with T1 saying so, and the question reopened
    in the security phase that D-64 already names (amendment 29, lines 4075-4076). B and C each
    cost more than the reach they remove while the root's host is already root-reachable by
    Coolify and infraops (amendment 29 item 2). **Blocks:** the row's `rotated_by_sds`. It doesn't
    block this pull request, because the amendment grants nothing.
11. **Open, for Devon: whether the unlock tier also names the executor's root token** (item 1).
    - **A.** The tier names only the broker's token.
    - **B.** It also names the root token, as a key that reads for others. Nothing changes in how
      the root token rotates: it stays in decision 4's hand-rotated set.

    **Recommendation: B**, because D-81 (1)'s option text names "any other key that mints or reads
    for others", and the root reads every keeper. It is a name only. **Blocks:** nothing in item 2.
12. **Open, for Devon: the rollback after a failed broker start** (item 6). The old value is in
    the rotation's quarantine secret until `confirm`, and this adapter has no verb that writes it
    back.
    - **A.** No machine rollback. A failed start is fixed forward: Devon mints again and the
      rotation is retried, and every broker lane's reads fail until it completes.
    - **B.** A restore verb for this adapter, like `pg-restore`, that `PATCH`es the variable from
      the quarantine. That's a new executor ability.
    - **C.** Devon copies the quarantined value into the Coolify variable by hand, and a session
      restarts the broker. That's a browser act on the tier's credential, as item 1 describes,
      and the keeper then disagrees with the variable until the rotation is retried.

    **Recommendation: A, with C as Devon's choice at the time.** The bind proves the token works
    before the `PATCH`, so a failed start means the token was revoked since or Coolify is at fault.
    A restore can't help with the first. **Blocks:** the row's `rotated_by_sds`.
13. **Open, for Devon: the credential for item 5's measurement.** Listing what a project-granted
    token sees needs a login with such a token. Options:
    - **A.** The first real rotation is the measurement. If the bind refuses, nothing is written:
      the unit waits, and Devon revokes the unused token. That costs one mint and one revoke.
    - **B.** A throwaway token that Devon mints on a machine account with a project grant on a
      project holding no secret of value, stored where the measuring session reads it, and
      revoked after the run. This follows amendment 33 item 14's measurement tokens.
    - **C.** A session uses the broker's own token from the keeper. Only the root reads it, so
      this means a new executor verb, or a person reading the keeper, for a listing.

    **Recommendation: B**, before the code pull request merges. It measures the SDK's behaviour
    without the broker's reach, and the result decides item 5 before the first rotation.
    **Blocks:** item 5.

### What it grants, and nothing more

Nothing. No BWS identity, project, grant or scope, no Coolify token, user or ability, no network
path and no executor ability is added or widened. The executor writes the broker's variable through
the client and path it already uses for lane tokens, and the session restarts the broker through
the move route that already exists. The helper target in item 4 lets the executor read one more
fingerprint, never a value. Open items 10 to 13 are Devon's, named and not given. Item 5 is HQ's and
grants nothing. No endpoint is added, so the HTTPS rule for public FQDNs (Infra Brain 212) doesn't
apply. Nothing here declares a credential exposed, and no rotation age changes.

### Consequences

- **The broker's token rotates by machine except for Devon's acts.** Those acts are the mint, the
  revoke, each grant and the approval. The keeper write, the variable's `PATCH`, the proof and the
  revoke's timing become the executor's, once the code pull request merges and its checks pass.
- **Each rotation restarts the broker once**, and every broker lane can miss one read.
- **A leak's revoke-first stops every broker lane** until `store` and the restart finish.
- **The root's reach through the broker's keeper is stated**, and Open item 10 decides how T1
  carries it.
- **The unlock tier stays a name.** It changes no code path.
- **Stale until the code pull request:** `.cred-consumers.toml`'s comment on the row, the two row
  tests, `src/rotation_worker/CLAUDE.md`'s `helper_target` invariant, and the two operations
  pages, as item 9 lists.

### Passages it governs

This amendment edits none of them.

| Passage | Disposition |
|---|---|
| Amendment 30 item 8, the unlock tier and the token's rotation (lines 4408-4412) | Planned: the tier by item 1, the rotation by item 2. The row stays `rotated_by_sds = false` until item 9's exit checks pass. |
| Amendment 32 Open items 6 and 7 (lines 4853-4862), and "Phase C is done when", the unlock tier and the broker's token (lines 4955-4958) | Planned by this amendment under D-81 (1) A and (2) B. |
| Decision 4, the root's reach, "not `SDS Operator`" (line 217), and the SYSTEM and VERIFIER bearers (lines 223-225) | Hold for the root's own grants. The reach through the broker's keeper is Open item 10. |
| T1 (line 365) and amendment 29 item 2's T1, "The SYSTEM and VERIFIER bearers stay outside the executor's BWS identity's reach" (lines 4139-4140) | As for decision 4. Open item 10 decides how T1 states it. |
| Amendment 33 item 7, a grant on `SDS Operator` ruled out (lines 5212-5214) | Stands. It is about a grant, which this amendment doesn't add. |
| Amendment 29 item 3, the loaded fingerprint (lines 4150-4201) | Applied to the broker's own container (item 4). |
| Amendment 29 Verify items, container names (lines 4278-4279) | Extended to the broker's container. Item 9's exit checks 1 and 2 close it for the broker. |
| Amendment 19 item 6, the executor never redeploys a lane | Stands, and is applied to the broker. The session restarts it (item 6). |
| `docs/operations/secrets-broker.md`, "Move the broker to a new release" (lines 41-55) | The route's restart (item 6), at the running tag. |
| `docs/operations/rotation-executor.md`, the helper targets (lines 1360-1394) and the broker row naming none (lines 1679-1683) | Change with the code pull request (item 9). |
| `tests/rotation_worker/test_lane_token_helper_targets.py`, lines 108-118 | Changes with the code pull request (item 9): it keeps refusing broker lanes. |

## Amendment 35 — A rotation adapter for GitHub App private keys: a person mints and deletes, the executor proves (2026-11-26)

**Status: Proposed (2026-11-26).** Accepted on merge, as amendment 31 is, except the items marked
"Open, for Devon", which stay open until he answers, and the item marked "Open, for HQ", which HQ
settles under build-mode rule 2. **Relates to:** amendment 32 item 1 ("The missing adapters"),
amendment 33 (the Coolify API-token adapter, whose shape this follows), amendment 34 (the root's
reach through the broker's keeper), amendment 15 (a type is only its provider), amendment 29 (the
executor on the VPS host), amendment 30 (the lane secrets broker), and ADR-0059 Q4, Q9 and the
D-11 addendum (the two keys' hand-rotation).

Amendment 32 item 1 puts the design's six missing adapters in Phase C, each "its own amendment,
which states the provider's API and the adapter's proof" (lines 4651-4664; every line number in
this amendment is at `d8d303b9` unless it names another file or commit). Amendment 33 decided the
`coolify-api-token` class. This amendment covers the `github-app-private-key` class's two rows in
this repository. It changes nothing that runs: no registry row, adapter, type, compose, launcher,
grant, key or package. The code pull request that builds the adapter cites it.

### Context

#### The two rows

Each is read from `.cred-consumers.toml` at `d8d303b9`. Neither has a `rotated_by_sds` field, so
each falls to the manual checklist (`src/rotation_worker/registry.py`, line 57: only `true`
counts), and `tests/architecture/test_rotation_agreement.py` (line 33) lists the class in
`UNTYPED`. The rotation proposer reports each as `legacy` and proposes no package
(`src/rotation_proposer/cli.py`, lines 502-504).

| Row (lines) | App | Keeper (BWS project) | Readers | `created` |
|---|---|---|---|---|
| `lanes-read-app-private-key` (1537-1634) | `alobar-sds-lanes-read`, App id 5684758, installation 180257215 (`provider_identity`, line 1541) | `314a966c-…`, base64 of the PEM, in `SDS / Lane Apps` (lines 1547-1550; `.bws-secrets.toml`, lines 376-379) | 12 `runtime-fetch` launchers, each with a `healthchecks_check` (lines 1552-1634) | 2026-11-09 |
| `lanes-push-app-private-key` (1636-1663) | `alobar-sds-lanes`, App id 5684778, installation 180257673 (line 1640), installed on intent-packages with `contents: write` (`.bws-secrets.toml`, lines 381-382) | `b8f547b9-…`, base64 of the PEM, in `SDS / Lane Apps` (lines 1646-1649; `.bws-secrets.toml`, lines 390-392) | 2 `runtime-fetch` launchers, bump-proposer and rotation-proposer (lines 1651-1663) | 2026-11-09 |

Both rows have `disposition = "reissue"` and the same three `rotation_preconditions` (lines 1545
and 1644): rotate it with the other row in one sitting; after every reader reads the new key,
delete the App's other key pairs on its settings page, because a new key pair doesn't revoke the
old one; and store base64 of the PEM. Neither has an `[[credential.exposure]]` entry or a recorded
fingerprint.

The rows are hand-rotated by decision. ADR-0059 puts both keys in this ADR's hand-rotated set "as
the `alobar-sds-rotation` App's key did (Q4 and Q9, decided)", with "no executor" handling either,
and Devon "rotates both in the same sitting (Devon's D-11 addendum, 2026-11-06)" (ADR-0059, lines
663-668 and 1404-1406). The block comment above the rows says the same (`.cred-consumers.toml`,
lines 1516-1520). An adapter changes that decision, so whether to make it is Devon's (item 13).

#### Every reader, found by grep

A grep of this repository at `d8d303b9` for both keeper UUIDs and both App ids finds these readers
and nothing else that reads a value:

- **Lane launchers.** Twelve read the read App's key: `scripts/run-landing-ledger.sh`,
  `run-deploy-watcher.sh`, `run-revision-watcher.sh`, `run-unit-verifier.sh`, `run-pin-watcher.sh`,
  `run-membership-check.sh`, `run-change-proposer.sh`, `run-work-carrier.sh`, `run-bump-proposer.sh`,
  `run-inert-landing.sh`, `run-estate-landing.sh` and `run-bump-driver.sh` (each's
  `READ_APP_KEY_UUID`). Two read the push App's key: `run-bump-proposer.sh` (line 151) and
  `run-rotation-proposer.sh` (line 97). These are exactly the rows' `runtime-fetch` consumers, and
  `.bws-secrets.toml`'s `lanes` lists agree (lines 379 and 393).
- **The secrets broker.** Its allowlist names the read App's key for five lanes: inert landing,
  estate landing, the bump driver, the landing ledger and the pin watcher
  (`lanes/secrets-broker/allowlist.toml`, lines 22, 31, 46, 75 and 83). The broker's account reads
  `SDS / Lane Apps` by a project grant (row `secrets-broker-bws-token`, `reach_projects`,
  `.cred-consumers.toml` line 1495; comment line 1471). No allowlist entry names the push App's key.
- **Composes.** No `lanes/` compose maps either key. Each launcher pipes the BWS read straight into
  `sds-github-mint`'s stdin, and the key is never in a variable, an argument or a file
  (`src/estate_clients/github_app_mint.py`, lines 23-27; for example
  `scripts/run-rotation-proposer.sh`, lines 255-262).
- **The Mac.** No file in `~/.claude/bin` or `~/Library/LaunchAgents` names either UUID. The other
  repositories under `~/Projects` name them only in sds-hq's `state/` records and codebase-review's
  findings, which read no value.
- **Tests** name the UUIDs as constants (for example `tests/architecture/test_bridge_edges.py`,
  lines 63-65). They read no value.

Every read is fatal to its pass, or fails its ping. A failed read or mint exits 1 with `FATAL` in
every launcher but one (for example `run-deploy-watcher.sh`, lines 134-136, and
`run-rotation-proposer.sh`, lines 260-262). The unit verifier runs on without a token and reports
exit 3, which pings `/fail` (`run-unit-verifier.sh`, lines 107-112 and 126-130). The executor
counts a reader switched only on a check whose status is `up` or `grace`
(`src/rotation_worker/readers.py`, lines 83 and 167). So a reader's successful ping after the
keeper's revision date proves that the reader minted a token with the new key, for every pass that
reaches its read. A pass can also end before its read: the inert lander exits 2 on an empty
repository list, having minted nothing (`run-inert-landing.sh`, lines 169-172). Whether such an
exit pings success differs by launcher (the root `CLAUDE.md`: exit codes 2 and 3 aren't shared),
and this amendment hasn't read each launcher's map. The code pull request's tests state, per
reader, which exits reach the read and which ping (item 15).

Every reader runs on a schedule (each `lanes/<lane>/crontab`). Six run daily: the landing ledger,
the pin watcher, the membership check, the work carrier, the bump proposer and the rotation
proposer. The others run hourly or more often. Each scheduled bump-driver pass is acting, so it
pings (`lanes/bump-driver/crontab`; the row's note, line 1634).

#### The executor's reach to the keepers

- **By its own grants: none.** The root has write on `Rotation / Work`, `Rotation / Keepers` and
  `Rotation / Inbox`, read on four other rotation projects, one direct read in `Ops / Platform`,
  "nothing else" (`docs/operations/rotation-executor.md`, line 677; decision 4, lines 214-217). It
  can't read or write `SDS / Lane Apps`, so a `store` step can't write either keeper where it is.
- **Through the broker's keeper: read.** The root reads every keeper in `Rotation / Keepers`, the
  broker's token among them, and that token reads `SDS / Lane Apps` by its project grant (amendment
  34, "What the root reaches through this keeper", lines 5459-5481). So whoever holds the root can
  read both App keys today. Amendment 34's Open item 10 puts that reach to Devon; this amendment adds
  nothing to it.

#### The provider's API

Everything in this subsection is a reading of GitHub's documentation on 2026-11-26, not a
measurement. No GitHub API was called.

- **A key pair is created and deleted only on the App's settings page.** "Managing private keys
  for GitHub Apps"
  (`https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps`)
  gives the steps: the App's **Edit**, then **Credentials**, **Key pairs**, **New key**, which
  downloads the private key as PEM. GitHub "only stores the public portion of the key". A key is
  deleted on the same page, and "you must regenerate a new key before you can delete the existing
  key". An App holds up to 25 keys, and the page recommends several keys "to rotate keys without
  downtime". Keys "do not expire".
- **No REST endpoint creates, lists or deletes an App's key.** "REST API endpoints for GitHub Apps"
  (`https://docs.github.com/en/rest/apps/apps`) lists `GET /app`, the installation endpoints and
  `POST /app/installations/{installation_id}/access_tokens`, and no key endpoint. The manifest
  conversion returns a key once, when an App is created, which isn't key management. Only the first
  part of that page was read word for word; the rest was read as a summary. GraphQL wasn't checked.
  So the claim is scoped: the documentation read names no key-management endpoint.
- **A new key doesn't revoke the old one.** No page says so in words. It follows from keys not
  expiring, from up to 25 being held at once, and from the rows' own precondition. It is an
  inference, and confirm-dead (item 7) is where it is tested.
- **Each key pair has a SHA-256 fingerprint**, shown in the App's Credentials section, which
  `openssl rsa -in <PEM> -pubout -outform DER | openssl sha256 -binary | openssl base64` reproduces
  from the private key (same page, "Verifying private keys"). It is a hash of the public half, not
  a secret.
- **An installation token is minted with a JWT** signed RS256 by the key, `iss` the App's id or
  client id, `exp` "no more than 10 minutes into the future", sent as `Authorization: Bearer` to
  `POST /app/installations/{installation_id}/access_tokens` ("Generating a JSON Web Token (JWT) for
  a GitHub App" and "Generating an installation access token for a GitHub App", under
  `https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/`). The
  existing mint does exactly this (`github_app_mint.py`, lines 168-190 and 92-103).
- **`GET /installation/repositories`** lists the repositories an installation token reaches, with
  `total_count`, and **`DELETE /installation/token`** revokes the token used to call it, answering
  204 ("REST API endpoints for GitHub App installations",
  `https://docs.github.com/en/rest/apps/installations`).
- **How GitHub answers a JWT signed by a deleted key, or by a key it never issued, isn't
  documented** on any page read. Item 2 treats both as unmeasured.

So no API can mint or delete, and the shape is amendment 33's: a person mints into the inbox, the
executor proves and stores, and a person deletes after the executor's proof
(`src/rotation_worker/adapters/bitbucket.py`; its type is
`Capabilities(machine_mint=False, machine_revoke=False)`, `src/rotation_worker/types.py` line 39).

### Decision

1. **The type.** `github-app-private-key` becomes a credential type named `github-app-key` with
   `Capabilities(machine_mint=False, machine_revoke=False)`: not hosted, with an overlap (GitHub
   holds both keys at once), no issue plan, no `env_at_distribute`. Its keepers are BWS secrets
   (`store_kind` `bws-secret`), so the VPS host executor takes its units. From each row's
   destinations (a keeper and `runtime-fetch` readers, so no `distribute`), `plans.derive` gives
   Bitbucket's plan, as amendment 33 item 1 does:
   1. **mint** (person): on the App's settings page, Devon generates a new key pair. He encodes the
      downloaded PEM as one line (`base64 -i <file> | pbcopy` on macOS, the stored form
      `decode_stored_key` reads, `github_app_mint.py` lines 249-266), pastes it into the inbox
      (`Rotation / Inbox`), and deletes the downloaded file and clears the clipboard. The download
      is a copy of the key at rest, so its deletion is part of the mint.
   2. **store** (machine): `verify_new` (items 2 to 4), then `store`, which writes the keeper and
      reads it back.
   3. **verify** (machine): `verify_consumers`. The unit waits unclaimed until every reader has a
      successful run since the keeper's revision date (`worker._gates_retire`,
      `src/rotation_worker/worker.py` line 954), up to a day for the daily readers. Its completion
      sends the revoke alert.
   4. **revoke** (person): Devon deletes the App's other key pairs (item 7).
   5. **confirm** (machine): `confirm_dead`.

   The type applies by class (`CLASS_TYPES`, `types.py` lines 67-77), so it also types localops'
   `fork-sync-app-private-key` row (localops `.cred-consumers.toml`, line 31, at `3431928`). That row
   has no `rotated_by_sds`, so the proposer still reports it `legacy`, and its consumers are
   Actions secrets holding the decoded PEM, a form other than its keeper's. `check_row` (item 11)
   refuses any `gh-actions-secret` consumer, so the type can't rotate it. It stays Devon's by hand.
2. **The probe and its known-bad control.** `probe` signs a JWT with the value (`iss` the row's App
   id, `iat` a minute back, `exp` nine minutes on, as the mint's `_jwt` does) and sends `GET /app`.
   Live is exactly 200 with an `id` equal to the row's declared App id. The control is a JWT with
   the same claims, signed by an RSA key the probe generates in memory and discards, which GitHub
   never issued. Both go through the same probe, so a refused control proves the 200 came from the
   key (amendment 5's rule). GitHub documents no status for either refusal (Context), so the
   adapter declares `dead_statuses = {401}` and the code pull request's exit check 1 records the
   control's measured status. A control that answers anything other than a 4xx fails the step, with
   the status named. `probe_before_revoke` is the same request.
3. **The identity check.** `same_identity` holds when both values answer `GET /app` with the row's
   App id. Unlike amendment 33's team check, this tells the two rows apart: each App has its own id,
   so a key pasted for the wrong App never binds. `verify_new` also refuses a new value whose public
   half equals the old value's. The inbox's byte comparison (`Value.same_as`,
   `src/rotation_worker/values.py` lines 60-61) can't see the same key wrapped differently, and that
   would pass every probe while rotating nothing.
4. **The working proof, in `verify_new`.** Before the keeper is written, the executor proves the new
   key does what every reader does with it, minting a narrow installation token:
   1. `estate_clients.github_app_mint.mint` with the new key, the row's App id, the row's declared
      `proof_repository` alone, and `{metadata: read}` alone, the narrowest token GitHub issues.
      The mint refuses unless GitHub's answer echoes that repository and permission
      (`github_app_mint.py`, lines 106-111).
   2. `GET /repos/<proof_repository>/installation` answers the row's declared installation id.
   3. `GET /installation/repositories` with the token answers `total_count` 1, naming the proof
      repository.
   4. `DELETE /installation/token` answers 204, so the token is dead before the step returns.

   The token is held only in memory, for those calls, and is never logged, stored or fingerprinted.
   **Who and where:** the executor's own process, as the root's rotation unit, on `prod-vps`
   (amendment 29). It reads the new value from staging. No other identity sees it.
5. **The readers' proof, and rule R10.** Every reader fetches the keeper fresh each pass and keeps
   no value between passes: the launchers pipe it into the mint (Context), and the broker caches
   nothing (amendment 30 item 4, lines 4386-4389). So no reader can stay on the old key past a pass
   that starts after the store. The proof is the existing route for a scheduled `runtime-fetch`
   reader ("How each consumer kind is verified", line 342): the keeper readback in `store`, then
   each row's `healthchecks_check` reporting a successful run that began after the keeper's revision
   date. Because every read and mint is fatal to its pass or fails its ping (Context), a green ping
   from a pass that reached its read proves that reader minted with the new key. A green ping from
   a pass that ended before its read proves less, but no revoke risk follows: no reader keeps a
   value, so none can be left on the old key. **Carried check:** which exits reach the read, per
   launcher, is verified by the code pull request's tests (item 15). No revoke alert is sent before
   every reader has passed, which meets R10 and Infra Brain 1032 by machine evidence. No helper target and no `loaded` read is needed:
   no container keeps the key.
6. **Open, for HQ: two mints in one sitting, and the inbox.** The inbox binds a pasted value only
   while no other rotation waits on a person's mint (`other_mints`,
   `src/rotation_worker/inbox.py` lines 12 and 120-136), and `bind` checks that before any type's
   check (lines 151-155). Both rows were created on 2026-11-09 with one interval, so both come due
   together. If both packages are approved together, both mint units wait, and neither binds. This
   is a check's form, not an authority, so HQ settles it under build-mode rule 2 (construction-mode
   ruling, `~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`). Options:
   - **A.** No code change. Devon approves the second package only after the first's value binds.
     The sitting becomes two approvals and two pastes, in order.
   - **B.** A provider that names its own credential binds while others wait. A new protocol beside
     `BindsByItself` lets `bind` skip the `other_mints` refusal when the value's `GET /app` id
     equals exactly one waiting rotation's declared App id and that rotation is this one. The
     other checks still apply.
   - **C.** Devon stages each value by hand (`rotation-worker stage`), which skips the inbox. That
     needs his write on `Rotation / Work`, which no one uses for this today.

   **Recommendation: B.** The App id is proven by the probe, so the paste names its own rotation,
   and Devon can paste both keys one after the other, each once the inbox empties (one executor
   pass, five minutes). **Blocks:** both rows' `rotated_by_sds`, under A only by the order rule.
   Under any option, **the deletions are a second sitting.** Each row's `verify` unit waits for its
   daily readers, so the revoke alerts come up to a day after the mints. The rows' "one sitting"
   precondition then reads: mint both in one sitting, and delete each App's other key pairs after
   its revoke alert. That rewording is part of item 13.
7. **The revoke and confirm-dead.** After the `verify` unit's alert, Devon deletes every key pair on
   the App's settings page except the new one. The alert names the new pair by the first 8
   characters of GitHub's own fingerprint, which the executor computes from the new key's public
   half as GitHub's documented `openssl` command does. That's a hash of a public key, not a secret,
   and 8 characters is below the evidence scan's 32-character refusal
   (`src/orchestrator/services/verifier/rotation_evidence.py`, line 36). It is not written into
   evidence, which holds only the executor's fingerprints. `confirm_dead` probes the old value from
   the quarantine and passes when it answers as the control answers, beside a 200 from the new
   value. The executor can't list key pairs (no API), so a stray third pair, one Devon made by hand
   and never pasted, is outside its proof. **Carried gap**, stated in the revoke alert. After an
   exposure, Devon may delete first (decision 6; Infra Brain 1032; D-26, amendment 19 item 10), so
   the adapter sets `revoke_may_come_first`.
8. **Open, for Devon: the keepers' project.** The `store` step writes the keeper, and the root
   can't write `SDS / Lane Apps` (Context). Options:
   - **A.** Move both keepers into `Rotation / Keepers` (Devon's act, which needs write on both
     projects, amendment 1 question 5), after each reading account gets a single-secret read grant
     there and proves it with only `PATH` and `HOME` set (decision 4). Recommended after Phase C's
     revoke sitting, when the broker is the only reading account: amendment 32 moves all 12 lanes
     onto it (D-81 (4) and (5) A, lines 4920-4921 and 4929), and the push App's two readers move in batches 3
     and 4 (lines 4976-4977). Then the broker needs two single-secret grants in
     `Rotation / Keepers`. The same change updates `.bws-secrets.toml`'s `project` (lines 378 and
     392), the broker row's `reach_secrets`, and, if `SDS / Lane Apps` then holds nothing it reads,
     its `reach_projects`. Amendment 34's bind compares the broker token's live reach with that
     declaration (amendment 34 item 5), so the declaration changes with the grants.
   - **B.** A direct read and write grant for `rotation-executor` on each secret where it is. That's
     the root's first write outside the rotation projects.
   - **C.** Leave both rows by hand.

   **Recommendation: A**, after the revoke sitting, since decision 4 says every rotated keeper moves
   to `Rotation / Keepers`, and A adds no new kind of reach. **Blocks:** both rows'
   `rotated_by_sds`.
9. **Open, for Devon: the root holding the push App's key with write.** Under item 8 A or B, the root
   reads and writes the push App's keeper by its own grant. That key mints `contents: write` on
   intent-packages, whose `main` the executor trusts for every rotation package's revision. The App
   pushes to `main` directly (ADR-0059, lines 774, 786 and 1411-1415). The executor still acts only
   on a revision a named person or the standing policy approved in the orchestrator, with its hash
   recomputed (`src/rotation_worker/CLAUDE.md`), so a package commit the root pushed alone would not
   be approved. Options:
   - **A.** Accept it. The approval record, not the commit, is the gate, and the root already reads
     the key through the broker's keeper (Context).
   - **B.** Rotate only the read App's key by machine. The push App's key stays by hand.
   - **C.** Accept it, and narrow the push App's reach first so the root's key can't write `main`
     directly (for example, a ruleset that admits the App only on a branch prefix). That changes
     the two proposers' push route.

   **Recommendation: A for the development phase**, reopened in the security phase as amendment 34's
   Open item 10 is. **Blocks:** the push row's `rotated_by_sds`.
10. **No new network path.** The adapter calls `https://api.github.com` from the executor's process
    on the VPS host, which already calls it for the rotation App (`src/rotation_worker/github_actions.py`,
    line 44; `rotation-executor.md`, line 675), and sends the keys only there, as JWT signatures.
    Its confined client (`estate_clients.confined.ConfinedClient`) permits exactly `GET /app`,
    `GET /repos/<proof_repository>/installation`,
    `POST /app/installations/<declared id>/access_tokens`, `GET /installation/repositories` and
    `DELETE /installation/token`.
11. **The row fields that change.** Each row gains `app_id`, `installation_id` and
    `proof_repository`. They are read today only from prose (`provider_identity`). `check_row` refuses
    a row without them, one whose consumers aren't a keeper and `runtime-fetch` readers with a
    `healthchecks_check`, or one whose `proof_repository` isn't one owner and name. The
    preconditions are reworded (item 6), the block comment's "Hand-rotated" text (lines 1516-1520)
    states the route, and `disposition`, the consumers and `created` keep their form. No
    `[[credential.exposure]]` is added. Each row becomes `rotated_by_sds = true`, with a
    `rotate_requested` date, only when the route is built, its exit checks pass, and its Open items
    are answered.
12. **Logging and failure.** The adapter logs nothing of a value through the executor's structured
    logging: evidence carries only fingerprints (the sha256 prefix and length, `values.py`) through
    `contract.py`. The JWT, the installation token and the PEM appear in no log line, exception
    message or evidence field. As the mint does, a failure names its cause and the status only, and
    never keeps a response body (`github_app_mint.py`, lines 19-21 and 224-236). A transport failure
    returns no answer, so the step is indeterminate and fails the unit, which a requeue retries
    (amendment 5). An unexpected status, a mismatched id, or a token that isn't revoked fails the
    step with that named. Nothing is swallowed.
13. **Open, for Devon: take both keys out of the hand-rotated set.** ADR-0059 Q4 and Q9 and the
    D-11 addendum put them in the hand-rotated set with no executor (Context). Amendment 32 lists
    them among the adapters Phase C builds or decides (item 1; "Phase C is done when", line 4960).
    Options:
    - **A.** Yes, both. The executor runs items 1 to 7 for both rows, and the precondition becomes
      "mint both in one sitting" (item 6).
    - **B.** The read App's key only (item 9 B).
    - **C.** Neither. Both stay by hand, and this amendment is the decision that Phase C requires.

    **Recommendation: A.** Devon's acts become two key generations and two deletions per year, and
    the readers' proof becomes the machine's. **Blocks:** both rows' `rotated_by_sds`.
14. **Open, for Devon: the measurement.** Items 2 and 7 rest on unmeasured statuses: GitHub's answer
    to a JWT from a key it never issued, and from a deleted key. The sds-hq standing answer of
    2026-11-26 (D-82: "A, A, C, yes, A") covers amendment 33's proofs only: a canary Coolify
    application with one non-secret variable (its item 13 C), and three throwaway tokens on
    `dev-coolify` (its item 14). It covers no GitHub App and no GitHub key. Options:
    - **A.** The first real rotation is the measurement. The control's status is recorded in
      `verify_new` before anything is stored, and the old key's status at `confirm`. If either isn't
      401, the step fails with the status named and nothing is revoked by machine.
    - **B.** A throwaway GitHub App, which Devon creates with no permissions and no installation. A
      session generates and deletes one of its keys and records both statuses. That's a new App
      and a new proof setup.

    **Recommendation: A.** Item 2's control runs on every probe, so the first rotation's
    `verify_new` measures the never-issued case before any write, and `confirm_dead` fails closed.
    **Blocks:** nothing under A. Under B, the code pull request's merge.
15. **What the code pull request changes.** Named here, none changed by this amendment:
    - `src/rotation_worker/adapters/github_app_key.py`, the provider: `NAME` (`github-app-key`) and
      `PROVIDER` (`github`), a `PLAN` from `derive(...)`, the probe, control and identity check
      (items 2 and 3), the working proof (item 4), the fingerprint for the revoke alert (item 7),
      `check_row` (item 11), and `mint`, `host`, `consumer_loaded` and `revoke` raising
      `ContractError`, as `bitbucket.py` does.
    - `src/estate_clients/github_app_mint.py`: the JWT signing made public for the adapter, with
      no change to `mint`.
    - `src/rotation_worker/adapters/__init__.py`: the import and an `ADAPTERS` entry with
      `capabilities=TYPES[...]`, `check_row` and `revoke_may_come_first=True` (as lines 202-206 and
      240 do).
    - `src/rotation_worker/types.py`: a `TYPES` entry and
      `CLASS_TYPES["github-app-private-key"]`.
    - `src/rotation_worker/inbox.py`, only under item 6 B: the protocol and its tests, including a
      refusal when two waiting rotations declare the same App id.
    - `tests/architecture/test_rotation_agreement.py`: `github-app-private-key` leaves `UNTYPED`.
    - `tests/architecture/test_rotation_edges.py`: a `PROVIDER_EDGES` entry for the adapter's reach
      (beside `"bitbucket"`, line 416).
    - `tests/architecture/test_wsp21_invariant_scan.py`: the module in `OUTBOUND_ALLOWLIST`, with its
      reason, beside `bitbucket.py` (line 835).
    - `layers.toml`, if no pattern there already names the module.
    - `src/orchestrator/factory-policy.toml`: `github-app-key` in the `rotation_adapters` lists that
      name `bitbucket` (lines 464 and 575).
    - `.cred-consumers.toml`: item 11's fields. Another worker edits that file, so the pull request
      rebases on it.
    - intent-packages: one `rotation-<row id>` package per row the rotation proposer generates.
      Neither is a first issue, so no standing approval covers it, and every revision is Devon's to
      approve by name.
    - `docs/operations/rotation-executor.md`: a section for the type, with item 1's mint note and
      item 7's deletion note.
    - A test per reader that names which of its launcher's exits reach the key's read and which
      ping success (item 5 and the Context's inert-landing case), so a green ping's meaning is
      pinned per row.

    **Exit checks**, each before a row's `rotated_by_sds = true`:
    1. **The control's status** (item 14 A or B) is recorded in the pull request's report, as a
       status only.
    2. **The working proof runs on the current keys without a mint**: a one-off run of item 4 by
       the executor against each keeper's current value, which proves the App ids, installation ids
       and proof repositories as declared, and that `DELETE /installation/token` answers 204.
    3. **One real rotation of each row** is the proof: the bind, `verify_new`, every reader's green
       ping, Devon's deletion, and `confirm_dead`. A canary can't stand in: no other App's key has
       these readers.

### What it grants, and nothing more

Nothing. No BWS identity, project, grant or scope, no GitHub App, key, permission or installation,
no network path and no proof setup is added or widened. Every grant, move and setup in items 6, 8,
9, 13 and 14 is named and not given: items 8, 9, 13 and 14 are Devon's, and item 6 is HQ's and
grants nothing. The executor's working proof (item 4) mints a token narrower than any reader's and
revokes it before the step returns. No endpoint is added, so the HTTPS rule for public FQDNs (Infra
Brain 212) doesn't apply. Nothing here declares a credential exposed, and no rotation age changes.

### Consequences

- **Both App keys can rotate by machine once the adapter merges** and Open items 8 and 13 are
  answered, the push App's only once item 9 is too.
- **Each rotation still needs Devon's browser acts**: a key generation per App, then a deletion per
  App after its revoke alert, because GitHub has no key API. The keeper write, the proofs, the
  deletion's timing and the identity checks become the executor's.
- **The deletion follows machine evidence** from every reader, by the existing `runtime-fetch`
  route, and a green ping here proves a successful mint with the new key.
- **The root writes both App keys** under item 8 A, and holds the push App's `contents: write` on
  intent-packages through it (item 9).
- **Stale until the code pull request:** the rows' comment and preconditions in
  `.cred-consumers.toml`, `UNTYPED`, and ADR-0059's statement that no executor handles either key.

### Passages it governs

This amendment edits none of them.

| Passage | Disposition |
|---|---|
| Amendment 32 item 1, "The missing adapters" (lines 4651-4664) | The `github-app-private-key` class is decided here. The `anthropic-api-key` class stays for its own amendment. |
| ADR-0059 Q4 and Q9 and the D-11 addendum, both keys hand-rotated, one sitting (ADR-0059, lines 663-668 and 1404-1406) | Stand until Open item 13 is answered. Under A, the keys leave the hand-rotated set and the sitting covers the mints (item 6). |
| Decision 4, the keepers move to `Rotation / Keepers` (lines 205-217) | Applied to two more keepers, under Open item 8 A. |
| Decision 4, the hand-rotated set (lines 221-222) | Loses the two lane App keys under Open item 13 A. The rotation App's key, `alobar-sds-rotation`, stays in it as an admin credential. |
| "How each consumer kind is verified", the scheduled `runtime-fetch` row (line 342) | Stands, and is the readers' proof (item 5). |
| Amendment 34's Context and Open item 10, the root's reach through the broker's keeper (lines 5459-5481 and 5696-5718) | Stand. They already cover the two keys' read, which this amendment doesn't change. |
| Amendment 34 item 5, the broker token's bind on declared reach (line 5579) | Its `reach_secrets` and `reach_projects` change with the keepers' move under Open item 8 A. |
| `src/rotation_worker/inbox.py`, `other_mints` (lines 12 and 120-136) | Stands, or gains item 6's exemption under HQ's answer B. |
| `tests/architecture/test_rotation_agreement.py`, `UNTYPED` (line 33) | Changes with the code pull request (item 15). |

## Amendment 36 — A rotation adapter for Anthropic API keys: a person mints and deletes, the executor proves (2026-11-26)

**Status: Proposed (2026-11-26).** Accepted on merge, as amendment 31 is, except the items marked
"Open, for Devon", which stay open until he answers. **Relates to:** amendment 32 item 1 ("The
missing adapters"), amendments 33 and 35 (the Coolify token and GitHub App key adapters, whose
shape this follows), amendment 15 (a type is only its provider), amendment 29 (the executor on the
VPS host), amendment 30 (the lane secrets broker), and ADR-0059 amendment 14 (the change-window
lane's own Anthropic key).

Amendment 32 item 1 puts the design's six missing adapters in Phase C, each "its own amendment,
which states the provider's API and the adapter's proof" (lines 4651-4664; every line number in
this amendment is at `9caad335` unless it names another file or commit). Amendments 33 and 35
decided the `coolify-api-token` and `github-app-private-key` classes. This amendment covers the
sixth adapter, the `anthropic-api-key` class's one row in this repository. It changes nothing that
runs: no registry row, adapter, type, compose, launcher, grant, key or package. The code pull
request that builds the adapter cites it.

### Context

#### The row

`anthropic-change-window-key` (`.cred-consumers.toml`, lines 1833-1852; its block comment, lines
1828-1832) is an API key in the Anthropic workspace `sds-change-window` (`provider_identity`, line
1837), created by Devon at HQ gate G-88 on 2026-11-22 (`created`, line 1839). ADR-0059 gave the
lane "a key of its own, separately revocable", in place of the shared key the Mac job read
(ADR-0059, lines 4203-4205). The row has `disposition = "reissue"` (line 1840), no `rotated_by_sds`
field, no `[[credential.exposure]]` entry and no recorded fingerprint ("no session has read the
value", line 1831). So it falls to the manual checklist (`src/rotation_worker/registry.py`, line
57: only `true` counts), `tests/architecture/test_rotation_agreement.py` lists the class in
`UNTYPED` (lines 26 and 29), and the rotation proposer reports it as `legacy` and proposes no
package (`src/rotation_proposer/cli.py`, lines 502-504).

Its two consumers:

- **The keeper**, `0e9ebf2d-…` (`change-window-anthropic-key`), in `Rotation / Keepers` (lines
  1842-1845; `.bws-secrets.toml`, lines 658-662).
- **One `runtime-fetch` reader**, `scripts/run-change-window.sh`, with `healthchecks_check =
  "Change Manager OPs Check"` (lines 1847-1852). It reads the key on `--submit` passes only, as
  `ANTHROPIC_API_KEY`, through the lane secrets broker (launcher lines 72-73 and 128-134). The
  broker serves it from the lane's allowlist (`lanes/secrets-broker/allowlist.toml`, line 60), and
  the broker's account reads it by a single-secret grant (the broker row's comment,
  `.cred-consumers.toml` lines 1474-1477; the row's comment, line 1830).

#### Every reader, found by grep

A grep for the keeper's UUID in this worktree, in every repository under `~/Projects` (main trees),
in `~/.claude/bin` and in `~/Library/LaunchAgents` finds these files and nothing else:

- **This repository:** `.cred-consumers.toml`, `.bws-secrets.toml`, the launcher, the broker's
  allowlist, two tests (`tests/scripts/test_run_change_window.py` and
  `tests/rotation_worker/test_secrets_broker_row.py`) and a build report. Only the launcher reads
  a value.
- **sds-hq:** handoffs, a gate and progress records in `state/`, which read no value.
- **Nothing on the Mac** names it. The Mac's `com.devon.change-window` job read the shared key
  `b74bf8b3-…`, not this one (ADR-0059, lines 822 and 4203-4205), and it was taken out at G-89
  (sds-hq `state/history/progress-2026-11.md`, line 6175).

So the row's consumer set is complete, and the lane is the key's only reader.

#### What the reader proves when it passes

- **The read is fatal.** An empty or unreadable key exits 1 with `FATAL`
  (`run-change-window.sh`, lines 132-134), and a `--submit` pass pings `/fail` on anything but 0
  (lines 34-38). So a green ping proves that the pass read a non-empty key from the keeper.
- **A green ping doesn't prove Anthropic accepted the key.** The launcher exits 0 when "the window
  ran ... whatever each item's outcome" (line 26). The lane runs infraops' bundle at the pinned
  commit `8dc4d43e` (`lanes/Dockerfile`, line 26). At that commit, each approved item gets its own
  agent call (`src/cli/change-mgr-cli.ts`, lines 153-155), and a failed agent call becomes that
  item's `failed` outcome (`src/change-manager/run-window.ts`, lines 70-80). A window with no
  approved item makes no Anthropic call.
- **The schedule is on.** The lane runs daily at 04:00 New York (`lanes/change-window/crontab`,
  line 10) only while `LANE_SCHEDULE_ENABLED` is `true` (crontab, line 9). It was turned on at the
  G-89 cut-over on 2026-11-22 (sds-hq `state/history/progress-2026-11.md`, line 6174). If it is
  turned off, the `verify` unit waits (item 4).

#### The executor's reach to the keeper

The keeper is already where a rotated keeper belongs. The root has write on `Rotation / Keepers`
(decision 4, lines 215-217; `docs/operations/rotation-executor.md`, line 677, the freshest list of
the root's projects), and the broker reads the keeper by a single-secret grant there, as decision
4 requires of a consumer account (lines 205-210). So no keeper moves and no grant changes.

#### The provider's API

Everything in this subsection is a reading of Anthropic's documentation on 2026-11-26, not a
measurement. No Anthropic API was called. Each `docs.anthropic.com` URL redirected to the same page
on `platform.claude.com`, which is cited. The pages:

- (A) the Admin API overview, `https://platform.claude.com/docs/en/api/administration-api`;
- (B), (C) and (D) the List, Retrieve and Update API Key references,
  `https://platform.claude.com/docs/en/api/admin-api/apikeys/list-api-keys`, `.../get-api-key` and
  `.../update-api-key`;
- (F) Authentication, `https://platform.claude.com/docs/en/manage-claude/authentication`;
- (G) Create an Admin API key, `https://platform.claude.com/docs/en/manage-claude/admin-api-keys`;
- (H) List Models, `https://platform.claude.com/docs/en/api/models-list`;
- (I) Errors, `https://platform.claude.com/docs/en/api/errors`;
- (J) the API overview, `https://platform.claude.com/docs/en/api/overview`;
- (K) Workspaces, `https://platform.claude.com/docs/en/manage-claude/workspaces`;
- the rate-limits and pricing pages, `https://platform.claude.com/docs/en/api/rate-limits` and
  `https://platform.claude.com/docs/en/about-claude/pricing`.

What they say:

- **No API creates or deletes an API key.** A's FAQ: "No. You create API keys in the Claude
  Console. The Admin API can only read, rename, and change the status of existing keys." The
  Admin API has three key routes: `GET /v1/organizations/api_keys` (list, filterable by
  `workspace_id` and `status`), `GET /v1/organizations/api_keys/{api_key_id}` and
  `POST /v1/organizations/api_keys/{api_key_id}`, whose body takes only `name` and `status`
  (`active`, `inactive` or `archived`) (B, C, D). "A single-workspace key always runs in the
  workspace chosen when it was created" (K), and no route moves a key between workspaces.
- **Disable and Delete.** In the Console, "**Disable** is reversible (the Admin API reports the
  key's `status` as `"inactive"`, and **Re-enable** returns it to `"active"`)", and "**Delete** is
  permanent: the key is archived and still appears in List API Keys with `status: "archived"`"
  (F).
- **The Admin API needs an admin credential.** A names "An **Admin API key** ... Only organization
  members with the admin role can provision one", an OAuth token with `org:admin`, or a personal or
  service-account key "that isn't scoped to a specific workspace". Keys "scoped to a single
  workspace ... don't work there" (K). Besides API keys, A names organization members and roles,
  invites, workspaces and their members, usage and cost reports, rate limits (including setting
  workspace limits) and organization webhooks among what the Admin API reaches.
- **The probe route.** `GET /v1/models` answers 200 with the model list (H), on the host
  `https://api.anthropic.com` ("The Claude API is a RESTful API at `https://api.anthropic.com`", J).
  H's example authenticates with `Authorization: Bearer`. J marks `anthropic-version` required and
  H marks it optional, so the probe sends it. No page says whether the route is billed or counts
  against a rate limit. The rate-limits page scopes its token limits to the Messages API, and the
  pricing page charges per token. The route sends no prompt, so it spends no tokens, but that is an
  inference.
- **Errors.** "401 - `authentication_error`: There's an issue with your API key (for example, it's
  malformed, revoked, or expired...)"; 403 is `permission_error`; 429, 500 and 529 are a rate limit,
  an internal error and an overload (I).
- **A regular key's workspace is named in API responses.** J's response-header
  table: `anthropic-workspace-id` is "The `wrkspc_`-prefixed ID of the workspace that the API key
  or access token resolved to ... Absent when the credential doesn't resolve to a workspace (for
  example, on Admin API requests) or the request fails before authentication completes." K: "Claude
  API responses include an `anthropic-workspace-id` header alongside the `request-id` and
  `anthropic-organization-id` response headers", and "The same accessors read the header from other
  Claude API endpoints too". No page lists endpoints one by one, so none confirms that
  `GET /v1/models` sends it.
- **Scoped and unscoped keys.** "A single-workspace key always runs in the workspace chosen when it
  was created. A key that isn't scoped to a workspace runs in the workspace named by each request's
  `anthropic-workspace-id` header" (K), and such a request without the header gets 400
  `invalid_request_error` (K). Only an unscoped key can call the Admin API (A, K).
- **Unmeasured:** what status a disabled or deleted key gets, and how soon it stops
  authenticating. Only expiry is stated ("requests made with it return a `401
  authentication_error`", F), and I's 401 entry says "revoked" without defining it.
- **The key's shape.** A regular key is a static secret that starts with the documented
  regular-key prefix (F), followed by a long random string. An Admin API key starts with a
  different, admin prefix (G). G also notes that one admin-scoped key form shares the regular
  prefix, so a prefix alone can't tell an admin key from a regular one.

So no API mints or deletes a key, and the shape is amendment 33's unless Devon gives the executor
an admin credential (item 10): a person mints into the inbox, the executor proves and stores, and a
person deletes the old key after the executor's proof (`src/rotation_worker/adapters/bitbucket.py`;
its type is `Capabilities(machine_mint=False, machine_revoke=False)`,
`src/rotation_worker/types.py` line 39).

### Decision

1. **The type.** `anthropic-api-key` becomes a credential type named `anthropic-key` with
   `Capabilities(machine_mint=False, machine_revoke=False)`: not hosted, with an overlap (Anthropic
   holds both keys at once until one is deleted), no issue plan, no `env_at_distribute`. Its
   keeper is a BWS secret (`store_kind` `bws-secret`), so the VPS host executor takes its units.
   From the row's destinations (a keeper and one `runtime-fetch` reader, so no `distribute`),
   `plans.derive` gives Bitbucket's plan, as amendment 33 item 1 does:
   1. **mint** (person): in the Console, in workspace `sds-change-window`, Devon creates a key
      scoped to that one workspace and pastes it into the inbox (`Rotation / Inbox`;
      `inbox.pasted`). A key not scoped to a workspace can call the Admin API (Context), so the
      mint note says single-workspace scope, and item 3 refuses an unscoped key.
   2. **store** (machine): `verify_new` (items 2 and 3), then `store`, which writes the keeper and
      reads it back.
   3. **verify** (machine): `verify_consumers`. The unit waits unclaimed until the reader has a
      successful run since the keeper's revision date (`worker._gates_retire`,
      `src/rotation_worker/worker.py` line 954), up to a day. Its completion sends the revoke
      alert.
   4. **revoke** (person): Devon deletes the old key in the Console (item 5).
   5. **confirm** (machine): `confirm_dead`.

   The type applies by class (`CLASS_TYPES`, `types.py` line 67), so it also types factory-runner's
   shared `anthropic-api-key` row (factory-runner `.cred-consumers.toml`, lines 112-118 at
   `280b641`). That row's keeper is in `Ops / Platform`, its consumers are Actions secrets, it is
   "INCOMPLETE BY DESIGN" (line 103), and it has no `rotated_by_sds`, so the proposer still reports
   it `legacy`. `check_row` (item 8) refuses it, as amendment 35 item 1 does for localops' row. It
   stays Devon's by hand.
2. **The probe and its known-bad control.** `probe` sends `GET /v1/models` to
   `https://api.anthropic.com` with the value as `Authorization: Bearer` and an
   `anthropic-version` header. Live is exactly 200. The control is a random value of the key's
   shape: the documented regular-key prefix followed by random characters of the subject's length
   and alphabet, generated in memory and discarded. Anthropic documents 401
   `authentication_error` for a key that is "malformed, revoked, or expired" (I). The control is
   none of those in words, only a key Anthropic never issued, so `dead_statuses` is `{401}` and
   exit check 1 records the control's measured status. The control and the subject go through
   the same probe, so a refused control proves the 200 came from the key (amendment 5's
   rule, lines 852-854). A 429, 5xx or transport failure is indeterminate and fails the step (item
   9). `probe_before_revoke` is the same request.
3. **The identity check.** `same_identity` holds when both values answer 200 and each response's
   `anthropic-workspace-id` header equals the row's declared `workspace_id` (item 8). A missing
   header refuses: the value isn't bound, and nothing is stored. Unlike amendment 33's team check,
   this tells the declared workspace from every other one in the organization, so a key pasted
   from another workspace never binds. The probe sends no `anthropic-workspace-id` request header,
   so an unscoped key, which needs one, gets 400 and never answers live (Context). That 400 is
   documented for requests in general, not measured for `GET /v1/models`, and no exit check can
   measure it without minting an unscoped key. **Carried gap**, which the mint note's
   single-workspace scope covers. The declared `workspace_id` is read from the current key (exit
   check 1), so by machine this proves "the same workspace as the key minted at G-88"; that the
   workspace is `sds-change-window` rests on Devon confirming the id at exit check 1, which holds
   ADR-0059's "key of its own" from then on. The inbox's
   rules still apply (`inbox.other_mints`, `src/rotation_worker/inbox.py` lines 12 and 120; the
   other-keeper check, lines 20 and 172), but with one row of the type the other-keeper check
   compares against nothing, so it isn't what tells keys apart here. The header is documented for
   API responses (J, K) but not confirmed for `GET /v1/models`. Exit check 1 measures it on the
   current key before the adapter merges.
4. **The consumers' proof, and rule R10.** The reader fetches the keeper fresh on every `--submit`
   pass, and nothing keeps the value between passes: the broker caches and writes nothing
   (amendment 30 item 4, lines 4386-4389; `scripts/secrets_broker.py`, lines 16-19), and each pass's
   container process ends with the pass. So the reader can't stay on the old key past a pass that
   started after the store. The proof is the existing route for a scheduled `runtime-fetch` reader
   ("How each consumer kind is verified", line 342): the keeper readback in `store`, then
   `Change Manager OPs Check` reporting a successful run that began after the keeper's revision
   date. Because the read is fatal (Context), that run read the new key. No revoke alert is sent
   before it, which meets R10 and Infra Brain 1032 by machine evidence. Amendment 29's `loaded`
   fingerprint doesn't apply: the lane keeps no value.

   **Carried gap:** the green run doesn't prove Anthropic accepted the key in use (Context). That
   isn't a revoke risk, because no consumer can be left on the old key, and `verify_new` proved
   the value itself live in the right workspace. A key that Anthropic later refuses shows as the
   window's items failing in its digest, not as a failed ping. The code pull request's test for the
   reader states what a green ping proves.
5. **The revoke and confirm-dead.** After the `verify` unit's alert, Devon deletes the old key in
   the Console. Delete, not Disable: a disabled key can be re-enabled (F), so it isn't revoked. The
   alert says to delete every key in the workspace except the one created for this rotation,
   and names that rotation's mint date. The executor can't read a key's Console name without an
   admin credential. `confirm_dead` probes the old value from the
   quarantine and passes on 401 beside the control's 401 and a 200 from the new value. The status a
   deleted key gets and how soon it applies aren't documented (Context), so item 11 decides how
   they are measured, and `confirm_dead` fails closed on any other answer. The executor can't list
   the workspace's keys without an admin credential, so a stray key Devon made by hand and never
   pasted is outside its proof. **Carried gap**, stated in the revoke alert. After an exposure,
   Devon may delete first (decision 6; Infra Brain 1032; D-26, amendment 19 item 10), so the
   adapter sets `revoke_may_come_first`, and the probe before the revoke takes either answer.
6. **The keeper's project: no change.** The keeper is in `Rotation / Keepers`, which the root
   writes, and its one reading account has its single-secret grant there (Context). The `store`
   step writes it where it is. `.bws-secrets.toml` and the broker row's `reach_secrets` keep their
   form.
7. **Open, for Devon: the executor calling Anthropic.** Neither the executor's code nor its
   architecture tests name an Anthropic host today: a grep of `src/rotation_worker` and
   `tests/architecture/test_rotation_edges.py` for "anthropic" finds nothing. The executor's
   process on the VPS host already calls public HTTPS providers (`https://openrouter.ai/api/v1`,
   `adapters/openrouter.py` line 62; `https://api.github.com`, `github_actions.py` line 43), and
   `docs/operations/rotation-executor.md` documents no egress filter on the host. The lane's
   container reached `https://api.anthropic.com/` at the G-89 cut-over (an unauthenticated
   request answered 404, reach only; sds-hq `state/history/progress-2026-11.md`, line 6172, the
   probe ADR-0059 lines 4366-4370 planned). Its resource runs on production Coolify, whose one
   server is its own host (amendment 29 item 2, line 4115). That is the container's reach, not
   the executor's process. So the network route
   is there, and the new thing is the executor's process sending a key to a host it doesn't call
   today. Options:
   - **A.** Accept it. The adapter's confined client (`estate_clients.confined.ConfinedClient`)
     permits exactly `GET https://api.anthropic.com/v1/models`, and sends the key nowhere else.
   - **B.** Leave the row by hand.

   **Recommendation: A.** It's the OpenRouter adapter's existing pattern for an LLM provider's key,
   and the request reads nothing but the model list. **Blocks:** the row's `rotated_by_sds`.
8. **The row fields that change.** The row gains `workspace_id`, the `wrkspc_`-prefixed id of
   `sds-change-window`, which isn't a secret. Exit check 1 reads it from the current key. Today the
   workspace is named only in prose (`provider_identity`). `check_row` refuses a row without it,
   or one whose consumers aren't a keeper and `runtime-fetch` readers each with a
   `healthchecks_check`. `check_row` sees only the row (`check_row(credential)`, for example
   `adapters/postgres_role.py` line 185), so it can't read a keeper's project; the consumer-kind
   refusal is what excludes factory-runner's row. The block comment's "No adapter rotates this
   class" (lines 1831-1832) states the route instead. `disposition`, the
   consumers and `created` keep their form, and no `[[credential.exposure]]` is added. The row
   becomes `rotated_by_sds = true`, with a `rotate_requested` date, only when the route is built,
   its exit checks pass, and items 7 and 11 are answered.
9. **Logging and failure.** The adapter logs through the executor's existing structured logging
   and logs nothing of a value: evidence carries only fingerprints (the sha256 prefix and length,
   `rotation_worker/values.py`, line 17) through `contract.py`, as every adapter's does. The key,
   the control and the `Authorization` header appear in no log line, exception message or evidence
   field, and a failure names its status only, never a response body. The workspace id isn't a
   secret and may appear in a refusal. A transport failure returns no answer, so the step is
   indeterminate and fails the unit, which a requeue retries (amendment 5). An unexpected status,
   a missing or mismatched workspace header, or a control that isn't refused fails the step with
   that named. Nothing is swallowed.
10. **Open, for Devon: an Admin API key for the executor.** An Admin API key could do two things
    the plan leaves to a person: revoke by machine (`POST /v1/organizations/api_keys/{id}` with
    `status: "archived"`), and list the workspace's keys, so a stray key is seen. It is an admin
    credential: it reaches every key in every workspace of the organization, and members, invites,
    workspaces and rate limits (Context, A). Decision 4 puts every admin credential in
    `Rotation / Admin` and in the hand-rotated set (lines 211-214 and 221-222), and the D-84
    standing answer leaves "an admin credential" to Devon (sds-hq `standing-answers.md`, lines
    300-323, at `b2460b34`). Options:
    - **A.** No. A person mints and deletes, as item 1 plans.
    - **B.** Devon mints an Admin API key into `Rotation / Admin`, read by UUID only while an
      `anthropic-key` unit revokes, as the OpenRouter management key is
      (`docs/operations/rotation-executor.md`, line 674). The type becomes
      `Capabilities(machine_mint=False, machine_revoke=True)`, GitHub PAT's shape (`types.py`, line
      38). The revoke picks the old key by an id the executor derives and confirms, never one taken
      from evidence (T8), which needs its own design: Anthropic exposes only a `partial_key_hint`
      for a key (B, C), so matching the quarantined value to an id is unmeasured.

    **Recommendation: A.** The gain is one Console deletion per rotation, and B adds an
    organization-wide admin credential and an id-matching design for it. It's re-askable.
    **Blocks:** nothing in item 1.
11. **Open, for Devon: the measurement.** Items 2, 3 and 5 rest on unmeasured answers: the
    control's status, whether `GET /v1/models` sends the workspace header, and a deleted key's
    status and lag. The D-82 standing answer covers a canary Coolify application and throwaway
    tokens on `dev-coolify` only (sds-hq `standing-answers.md`, lines 257-276), so it covers no
    Anthropic key. Options:
    - **A.** No new key. Exit check 1 measures the control and the header against the current key,
      with no mint. The first real rotation measures the deleted key: `confirm_dead` records the old
      key's status and, if it isn't 401, fails with the status named, and the person's deletion is
      already done.
    - **B.** A throwaway key Devon mints in `sds-change-window` and deletes, which a session
      probes before and after the deletion, recording both statuses and the lag. That's a new
      probe credential.

    **Recommendation: A**, as Devon chose for the GitHub App key (D-84 (14) A, sds-hq
    `state/decisions.md`, line 16). A failed `confirm_dead` revokes nothing by machine and leaves
    the unit to a person. **Blocks:** nothing under A. Under B, the code pull request's merge.
12. **What the 2026-11-26 standing answers cover.** Each covers only what it names (sds-hq
    `standing-answers.md`, lines 257-323, at `b2460b34`):
    - **D-82** (one canary application, three `dev-coolify` probe tokens): nothing here. It is
      Coolify's proof pattern.
    - **D-83** rule 2 (fix forward when a route proves before it switches): applies. `verify_new`
      proves the key before the keeper changes, and the old key keeps working until Devon deletes
      it, so a failed rotation needs no restore: Devon mints again. Rule 1 (reach through another
      identity's token) doesn't apply: the root reads this keeper by its own grant.
    - **D-84** (the root holding a rotated key by its own grant, in development): applies. Its
      condition 1 already holds (item 6). The key writes nothing the executor trusts, so its
      condition 2 doesn't arise. Its condition 3, the reach in T1, is recorded by the code pull
      request. Its exceptions, an admin credential and a measurement choice, are items 10 and 11.

    Item 7's network path is in none of them.
13. **What the code pull request changes.** Named here, none changed by this amendment:
    - `src/rotation_worker/adapters/anthropic_key.py`, the provider: `NAME` (`anthropic-key`) and
      `PROVIDER` (`anthropic`, the row's `provider`), a `PLAN` from `derive(...)`, the probe,
      control and identity check (items 2 and 3), `check_row` (item 8), and `mint`, `host`,
      `consumer_loaded` and `revoke` raising `ContractError`, as `bitbucket.py` does.
    - `src/rotation_worker/adapters/__init__.py`: the import and an `ADAPTERS` entry with
      `capabilities=TYPES[...]`, `check_row` and `revoke_may_come_first=True`.
    - `src/rotation_worker/types.py`: a `TYPES` entry and `CLASS_TYPES["anthropic-api-key"]`.
    - `tests/architecture/test_rotation_agreement.py`: `anthropic-api-key` leaves `UNTYPED`.
    - `tests/architecture/test_rotation_edges.py`: a `PROVIDER_EDGES` entry for
      `https://api.anthropic.com`, beside `"openrouter"` (line 366).
    - `tests/architecture/test_wsp21_invariant_scan.py`: the module in `OUTBOUND_ALLOWLIST`, with
      its reason, beside `openrouter.py` (line 782).
    - `layers.toml`, if no pattern there already names the module.
    - `src/orchestrator/factory-policy.toml`: `anthropic-key` in the `rotation_adapters` lists
      (lines 464 and 575).
    - `.cred-consumers.toml`: item 8's fields. Another worker edits that file, so the pull request
      rebases on it.
    - intent-packages: one `rotation-anthropic-change-window-key` package, which the rotation
      proposer generates. It isn't a first issue, so no standing approval covers it, and every
      revision is Devon's to approve by name.
    - `docs/operations/rotation-executor.md`: a section for the type, with item 1's mint note and
      item 5's deletion note.
    - The threat model's T1 note (D-84 condition 3).
    - A test for the reader that states what its green ping proves (item 4).

    **Exit checks**, each before the row's `rotated_by_sds = true`:
    1. **The probe on the current key, without a mint**: a one-off run of items 2 and 3 by the
       executor against the keeper's current value. It records, as statuses and the header's
       value only, the 200, the control's 401 and the `anthropic-workspace-id` value that becomes
       the row's `workspace_id`. Devon confirms that id is `sds-change-window`'s in the Console
       (a one-off, before the row declares it). If the header is absent, the adapter doesn't
       merge, and the identity check comes back as its own amendment.
    2. **One real rotation** is the proof: the bind, `verify_new`, the reader's green ping,
       Devon's deletion and `confirm_dead`, with the deleted key's status recorded (item 11 A).

### What it grants, and nothing more

Nothing. No BWS identity, project, grant or scope, no Anthropic key, Admin API key, workspace or
permission, no network path and no proof setup is added or widened. The keeper doesn't move. Items
7, 10 and 11 are Devon's, named and not given. No endpoint is added, so the HTTPS rule for public
FQDNs (Infra Brain 212) doesn't apply. Nothing here declares a credential exposed, and no rotation
age changes.

### Consequences

- **The change window's Anthropic key can rotate by machine once the adapter merges** and items 7
  and 11 are answered. No keeper moves first.
- **Each rotation still needs two Console acts from Devon**, a key creation and a deletion, because
  Anthropic has no key-creation API and item 10 recommends no admin credential. The keeper write,
  the proofs, the workspace check and the deletion's timing become the executor's.
- **The deletion follows machine evidence** from the lane's green `--submit` pass, by the existing
  `runtime-fetch` route. A green pass proves the read, not Anthropic's acceptance in use (item 4).
- **Factory-runner's shared key stays by hand**: the type applies to its row, and `check_row`
  refuses it.
- **Stale until the code pull request:** the row's comment, `UNTYPED`, and amendment 15's
  "not yet measured" for Anthropic keys (lines 1779-1784), which this amendment answers from
  Anthropic's documentation: mint is a person's, and revoke is a person's unless item 10 is
  answered B.

### Passages it governs

This amendment edits none of them.

| Passage | Disposition |
|---|---|
| Amendment 32 item 1, "The missing adapters" (lines 4651-4664) | The `anthropic-api-key` class is decided here, the last of the design's six. |
| Amendment 15, the mint and revoke table's "Anthropic and OpenAI keys" row (line 1784) | Anthropic's half is answered from documentation (Context): a person mints; a person revokes unless Open item 10 is answered B. OpenAI's half stays unmeasured. |
| ADR-0059 amendment 14, "One Anthropic API key held by this lane only" (ADR-0059, lines 4203-4205) | Stands, and item 3's workspace check holds it on each rotation. |
| Decision 4, the keepers in `Rotation / Keepers` and admin credentials in `Rotation / Admin` and the hand-rotated set (lines 205-225) | Stand. The keeper is already there (item 6). An Admin API key under Open item 10 B would join `Rotation / Admin` and the hand-rotated set. |
| "How each consumer kind is verified", the scheduled `runtime-fetch` row (line 342) | Stands, and is the reader's proof (item 4). |
| Amendment 29 item 3, a lane token's revoke waits on the loaded fingerprint | Stands for lane tokens. It doesn't apply to this row, whose reader keeps no value (item 4). |
| `tests/architecture/test_rotation_agreement.py`, `UNTYPED` (lines 26 and 29) | Changes with the code pull request (item 13). |
