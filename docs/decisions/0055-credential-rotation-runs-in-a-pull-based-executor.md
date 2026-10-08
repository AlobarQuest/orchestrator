# ADR-0055 — Credential rotation runs in a pull-based executor through one contract

- **Status:** Proposed
- **Date:** 2026-10-08
- **Decided by:** Devon, decisions recorded 2026-10-08 in
  `docs/superpowers/specs/2026-10-08-credential-rotation-requirements.md`. Acceptance pending.
- **Supersedes:** the parts of ADR-0054 named in "What this changes in ADR-0054".
- **Relates to:** ADR-0054 (the SDS owns credential rotation), ADR-0052 (superseding an unworked
  decomposition), ADR-0039 (out-of-process producers), ADR-0011 (known-good patterns)

## Context

ADR-0054 made credential rotation SDS work. Working the first rotation (`openrouter-generic`,
parked 2026-10-07) showed each credential class heading toward its own procedure, and showed the
operator machine's Keychain as a dead end for a system that might one day serve customers. Devon's
goal for this design:

> "As automatic of a system, as possible, to rotate exposed creds, without creating a new, larger,
> security hole."

The facts this ADR rests on are in `docs/superpowers/specs/2026-10-08-credential-rotation-research.md`.
The requirements (R1–R18), the options considered, and Devon's decisions are in
`docs/superpowers/specs/2026-10-08-credential-rotation-requirements.md`. This ADR doesn't repeat
them; it records the design they produce.

### Facts measured for this ADR (2026-10-08)

- **One hash per machine identity.** `identity/auth.py::_validate_m2m_credentials` refuses two key
  ids for one `agent_id`, and each key id holds one `token_hash`. An orchestrator bearer can't
  overlap two values without a code change.
- **Change windows compose by intersection.** `factory_policy.py::window_refusal` returns
  `outside_change_window` if any declared reach member's window is closed. A package declaring
  `external_system` and `operator_machine` is bound by `operator_machine`'s 02:00–06:00 window.
- **Windows are checked only at dispatch admission**, and rotation units are never dispatched, so
  nothing windows them today (`reach_admission.py::change_window_refusal`, "Asked once, at
  admission, and nowhere else").
- **Keychain items are readable by any process running as Devon's user.** `scripts/sds-token.sh`
  creates its item with `-T /usr/bin/security`, which trusts that binary for every caller.
- **OrbStack (2.2.3) machines are reachable from the Mac.** With default settings, Devon's user
  gets root in a machine with no password, the machine sees `/Users/devon`, and it can run commands
  on the Mac. An isolated machine (`orb create --isolated`) blocks the machine-to-Mac direction but
  keeps "SSH and `orb` access" from the Mac (https://docs.orbstack.dev/machines/isolated). Every
  machine's disk is one file, `data.img.raw`, mode 644. OrbStack excludes it from Time Machine
  (`data_allow_backup: false`) and exposes machine ports to the LAN by default.

## Decision

### 1. A rotation executor that pulls approved work

A deterministic program, the **rotation executor**, does every step that moves a credential value.
It has no LLM. It lives in this repository as `src/rotation_worker/` (ADR-0054's settled location).

- **It pulls; nothing pushes to it.** It claims approved rotation units from the orchestrator with
  its own WORKER M2M identity, the way factory-runner takes dispatched work. It listens on no port.
- **The orchestrator is the control plane; the executor is the only place values exist.** The
  orchestrator holds the change record, the package, the approvals, the units and the evidence. The
  executor holds the admin credentials and the values in flight. No value ever flows back.
- **Its authority comes from the claim, not from who starts it.** Started by hand, it still acts
  only on a unit it claimed after a human approval.
- **Everything it acts on comes from the registry, never from the unit.** A unit names a credential
  id and an occurrence. The provider, the endpoints, the consumers and their destinations are read
  from the credential registries at a git revision the executor pins. Unit data can't name a
  destination, so a compromised orchestrator can trigger a rotation but can't redirect a value.

### 2. Where it runs

**A dedicated, isolated OrbStack VM on the operator machine** (`orb create --isolated` with
`--isolate-network`), built so it moves unchanged to a VPS if the SDS becomes a SaaS offering. In
that case each customer runs the executor beside their own credentials, as self-hosted CI runners
work, and the provider never holds customer admin credentials.

The VM holds nothing that can't be rebuilt: no rotation state lives only on its disk (see
decision 9).

**Accepted residual risk.** Any process running as Devon's user, including an agent session, can
reach the VM and read what it holds. Devon accepted this on 2026-10-08: the Mac already holds the
keys to every other host, so moving the executor elsewhere wouldn't stop a determined agent on the
Mac either. What the placement does prevent is an agent reaching the values *by accident*: no value
is in the Mac's Keychain, environment, shell or files, and none passes through a tool.

### 3. One contract with per-provider adapters

Every rotation runs the same ordered steps:

1. **mint** — create the new value
2. **verify-new** — probe the new value is live
3. **store** — write it to its BWS keeper, quarantining the old value first
4. **deploy** — write it to each consumer
5. **verify-consumers** — probe each consumer path with the new value
6. **revoke** — invalidate the old value
7. **confirm-dead** — probe the old value is refused
8. **record** — file fingerprint evidence and close the unit

An adapter declares, per step, one of `machine` or `human`. A `human` step becomes a step in the
standing package: the executor stops, files what it has, and resumes when the human's act is
attested. The contract, not the adapter, enforces the order: revoke is unreachable until
verify-consumers has passed for every registered consumer (R10).

Every probe is shown to refuse a known-bad value in the same run before its answer counts, and the
evidence carries both results (R14). Live is exactly 200; dead is exactly 401 or the provider's
documented equivalent; anything else is indeterminate and the executor stops.

### 4. Where the admin credentials live

- **Each provider's admin credential lives in its own BWS project** (for example
  `Rotation / OpenRouter`). The executor fetches only the one secret the claimed unit's adapter
  needs, by UUID, at the moment it needs it.
- **One BWS machine account, the executor's, reads those projects.** Its access token is the
  **root**: it lives only in the VM, Devon mints and rotates it by hand at the Bitwarden web app,
  and it is never rotated by the executor.

  This reconciles two recommendations Devon agreed to: separate projects per provider (Q2) and a
  single hand-rotated root (R8). Separate machine accounts per project would add tokens without
  adding containment, because all of them would sit on the same VM. The per-unit fetch is what
  limits a code defect to one provider.
- **The Coolify API token is not given to the executor** until a scope narrower than `write` +
  `deploy` + `read:sensitive` is measured. Until then, every step that writes a Coolify env or
  restarts a hosted app is a `human` step.
- **Minted keys are as weak as the provider allows:** an `expires_at` and a credit `limit` on every
  OpenRouter key; the executor revokes only a key whose provider id it pinned when it minted it.

### 5. What may trigger a rotation

Only the registry (`rotate_requested`, age) and infraops' structured `security-drift-cli
cred-findings`, through the existing rotation proposer (R5). Content fetched from mail, web pages,
issues, commits or tasks never triggers anything, directly or through an agent's inference.

### 6. Exposure response is a human decision

**No automatic disable or revoke** (Devon, 2026-10-08). An exposure finding is one input; whether
and how fast to contain depends on factors the finding doesn't carry. An exposure rotation follows
the same flow as any other. When Devon approves it, the record shows, at minimum: where the value
was exposed (the scanner's exposure record), every consumer and whether it is hosted, and whether
the provider can disable reversibly. Devon names any further factors before increment 3.

An exposure rotation of a self-issued bearer may skip the overlap in decision 7, taking the short
outage so the exposed value stops working at once. Devon chooses at approval.

### 7. Self-issued bearers overlap for a bounded time

The orchestrator accepts, per identity, an optional previous hash with an expiry:
`"previous": {"token_hash": "<sha256>", "until": "<UTC instant>"}`. The current hash is always
accepted; the previous hash only before `until`. Boot refuses a previous hash whose `until` is
further than one change window away. change-manager gets the equivalent change for its bearers.

The executor never rotates the bearer it is using in that run (R12). Its own WORKER bearer is
rotated through the same contract with every step `human`.

### 8. Change windows apply at the orchestrator's claim

**Devon decided (2026-10-08):** the window check lives in the orchestrator's claim path, so it holds
whoever claims, and resuming a paused rotation counts as continuing, not starting.

- **Where.** A claim refuses `outside_change_window` when the unit's package declares a reach whose
  window is closed, using `window_refusal`. It applies to every claimant, including HQ working an
  operational unit by hand, which is what ADR-0054 already said was meant to happen.
- **Starting versus continuing.** A first claim of a unit is a start and is windowed. A claim of a
  unit that already has evidence from an earlier attempt is a continuation and isn't windowed, so a
  rotation paused for a human step resumes when the human acts, at any hour. Renewing a claim
  continues and is never windowed.
- **The first hosted step is still windowed.** Continuing must not let a rotation that has touched
  nothing hosted begin touching it at midday. So the executor refuses to take the first step that
  writes to a hosted consumer (a Coolify env, an orchestrator or change-manager restart) outside the
  window, and waits for the next one. Steps after it continue at any hour, because finishing a
  started change is safer than leaving it half done.
- A rotation that starts inside the window runs to completion (R17).

### 9. State and recovery

- **Rotation state lives in the orchestrator**, as evidence on the unit: per step, the fingerprints
  (sha256 prefix and length) of the values involved and the probe results. Never a value.
- **A new value goes into BWS before anything depends on it.** The old value is quarantined in a
  named BWS secret before the keeper changes, as the legacy executor does.
- **Every step is idempotent and resumes from evidence.** On restart the executor reads the unit's
  evidence, re-probes the current state, and either continues or stops with a named reason. It
  never guesses (R11). If the VM is lost mid-rotation, both values are still live (the old one is
  revoked last), and the rotation resumes from a rebuilt VM.
- **Orchestrator bearer rotation records the previous hash before writing**, and validates the
  credentials setting before the restart, because a malformed setting stops the orchestrator from
  starting at all.

### 10. Human gates

Devon clicks every gate on the first rotation of each class (ADR-0054 amendment 1). Which gates
graduate, and on what evidence, is decided after that rotation has run (Devon, 2026-10-08).

## Threat model

### Assets

| Asset | Where it lives | Who can read it | Scope | Expiry | Rotated by |
|---|---|---|---|---|---|
| Executor BWS token (**root**) | The VM only | The executor; anything with root in the VM | Read on the rotation projects; write on the keeper projects for registered credentials | Set at mint | Devon, by hand |
| OpenRouter management key | `Rotation / OpenRouter` BWS project | The root | Account-wide, unscoped (provider limit) | Unverified whether it can carry one | Devon, by hand |
| GitHub secret-writer PAT (increment 5) | Its own BWS project | The root | Secrets: write on the repositories that hold a registered copy | 180 days or less | Devon, by hand |
| Executor WORKER bearer | `SDS Operator` keeper, the VM | The root | Claim and file evidence on rotation units | Registry age | Executor contract, all steps human |
| Values in flight | The executor's memory; BWS keeper and quarantine | The root | The rotated credential's scope | Until revoked | — |

### Threats

| # | Threat | What it gains | Containment |
|---|---|---|---|
| T1 | An agent session on the Mac reads the VM | Every asset above | **Accepted residual** (decision 2). No value is in the Mac's Keychain, files or environment, so it takes a deliberate act. |
| T2 | A forged trigger from fetched content | Unwanted rotations; churn or outage | Triggers are the registry and `cred-findings` only (decision 5); every rotation needs a human-approved record. |
| T3 | The orchestrator is compromised | It can mark units approved and make the executor rotate | Rotations are make-before-break, so the result is churn, not exposure. Destinations come from the pinned registry, never the unit, so no value can be redirected (decision 1). |
| T4 | A registry change adds a hostile consumer | The new value is written somewhere an attacker reads | Registries change only through reviewed merges. The executor accepts only known consumer kinds and destinations inside the estate (AlobarQuest repositories, the Coolify team, the rotation and keeper BWS projects), and pins the registry revision it read. |
| T5 | The root writes a keeper with an attacker's value | Consumers run on an attacker's account (for example, an OpenRouter key whose usage the attacker reads) | Only through T1, which is accepted. The fingerprint evidence for every write makes a substitution visible after the fact. |
| T6 | A compromised executor dependency | The same as T1, from inside | Few dependencies, pinned by hash. |
| T7 | The management key revokes the wrong key | An outage of another service (for example the brains' separate OpenRouter key) | The executor revokes only a provider id it pinned at mint. A key it didn't mint, including today's `openrouter-generic`, is revoked by a human step. |
| T8 | A value leaks into a log, evidence row or observation | The value | R1; the observation secret detector; evidence carries fingerprints only; the executor's output passes through a scrubber of every value it touched. |
| T9 | An executor defect causes an outage | Availability | Make-before-break; probes with known-bad controls; stop on indeterminate; change window on claim; bounded bearer overlap. |
| T10 | The Mac is stolen | Every asset | FileVault; the root and every admin credential are rotated by hand. |

### What the baseline risk was

Before this design, an exposed credential waited on about eleven human acts, and the exposure path
had never run end to end. This design keeps the human decision (decision 6) and removes the human
mechanics where a provider has an API.

## Per-class disposition

| Class | Live | Mint | Deploy | Revoke | Notes |
|---|---|---|---|---|---|
| OpenRouter key | 1 | machine | machine (BWS); see Keychain below | machine for keys the executor minted; human for the existing key | |
| GitHub PAT (fine-grained) | 2 | human | machine (Actions secrets, BWS); human for Coolify | machine (`POST /credentials/revoke`) | |
| Atlassian API token | 1 | human | machine (BWS); human for Coolify | human | Revoke API unverified |
| BWS machine token | 4 | human | human (Keychain consumers are on the Mac) | human | Executor records evidence only |
| change-manager bearer | 4 | machine | human until the Coolify token decision | machine (overlap expires) | Needs decision 7 in change-manager |
| orchestrator bearer | 6 | machine | human until the Coolify token decision | machine (overlap expires) | Never the executor's own bearer in its own run |

**Keychain consumers.** An isolated VM can't write the Mac's Keychain. `openrouter-generic` has one
Keychain consumer: the shell exports `OPENROUTER_API_KEY` from Keychain item `openrouter-api` at
init (infraops registry, lines 182–212). Until that consumer reads BWS itself, refreshing it is a
`human` step: Devon runs a sync script on the Mac that reads the keeper from BWS into the Keychain
item, and the executor's verify-consumers step waits for its attestation. **Devon decided (2026-10-08): drop the Keychain copy.** Whatever needs the key fetches it from
BWS, so the credential's only stored copy is its BWS keeper. This is done in increment 3, before
`openrouter-generic` is requested again, and its registry entry loses the `keychain` consumer.

## What this changes in ADR-0054

- **"Execute" in the flow, and "The rotation worker's authority and boundaries":** the worker runs
  in an isolated OrbStack VM, not as a Keychain-bound program on the operator machine, and holds
  the executor root token, not `BWS_ACCESS_TOKEN_CRED_ROTATION`.
- **"What stays human":** minting is machine work wherever the provider has an API the executor's
  credentials can use; containment of an exposure is human.
- **Increments 3 and 4** are replaced by the increment plan below.
- **"Change window":** enforced at the executor's claim (decision 8), not only stated.

Everything else in ADR-0054 and its amendments stands.

## The parked rotation

**Retire it** (Devon, 2026-10-08, "likely retire it, and redo it under whatever we end up
building"). On acceptance of this ADR: supersede decomposition `ff08aeed` (ADR-0052; no unit was
claimed), remove `rotate_requested` from `openrouter-generic` in infraops' registry, and resolve
backlog item `37bb0f906c7b`. `openrouter-generic` stays flagged `rotated_by_sds`, so the legacy
window keeps refusing it. The rotation is requested again in increment 3.

## Increments

1. **Accept and retire.** Accept this ADR; retire the parked rotation as above.
2. **The executor, with no live credentials.** The contract and its state machine in
   `src/rotation_worker/`, a fake adapter, the claim-time window check, the registry pin and
   destination allowlist, the executor's WORKER identity (security-standards commit and image
   rebuild) and its classification row. Built and tested in an isolated OrbStack VM. Measure here:
   - whether a Coolify token with `write` but not `read:sensitive` can PATCH application envs;
   - whether an OpenRouter management key can carry an expiry;
   - whether a disabled or deleted OpenRouter key probes 401 on `/api/v1/key` (throwaway key);
   - whether `orb -u root` from the Mac works on an isolated machine without a password.
3. **OpenRouter.** The adapter; the management key in its own BWS project; the executor's root
   token minted by Devon; the Keychain consumer resolved. Then request `openrouter-generic` again
   and run it with every gate clicked.
4. **Self-issued bearers.** The bounded overlap in the orchestrator and in change-manager, reviewed
   by mutation; the bearer adapters with Coolify steps `human`.
5. **GitHub PATs and the Atlassian token.** Human mint; the GitHub secret-writer PAT; machine
   deploy, verify and (for GitHub) revoke. Measure first: whether a GitHub App installation token
   can write Actions secrets, which decides whether `FACTORY_PR_TOKEN` is replaced rather than
   rotated.
6. **BWS machine tokens.** All steps human; the executor records the evidence.
7. **Retire the infraops window** (ADR-0054 increment 5).

Gate graduations (decision 10) are decided per class after that class's first rotation.

## Consequences

- One contract replaces per-class procedures; a provider without an API is the same contract with
  human steps.
- Admin credentials no longer touch the Mac's Keychain, environment or tools. A deliberate act
  inside the VM can still reach them; that risk is accepted and stated.
- Until the Coolify token question is answered, every hosted consumer deploy is a human step, which
  is ten of the eighteen live credentials. Measuring the Coolify scope early decides how automatic
  most rotations become.
- The orchestrator's authentication path changes (decision 7). That change ships with mutation
  review and a test that an expired previous hash is refused.
- Moving to a VPS, or to customer-run executors, changes where the VM runs, not the design.
