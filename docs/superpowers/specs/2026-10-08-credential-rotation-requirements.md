# Credential rotation: requirements, options and security impact (2026-10-08)

This document is the input to the rotation design ADR, not the design itself. It sets out what any
design must do, what a design is wanted to do, the options for each open question with their pros,
cons and security impact, and the decisions that are Devon's. The ADR and the increment plan come
after Devon fills in the "Devon decides" slots.

The factual base is `2026-10-08-credential-rotation-research.md` beside this file. Claims carry the
research's markings: **verified** (with a source) or **unverified**. Two facts were verified for
this document and are marked with today's date.

## The goal

> "As automatic of a system, as possible, to rotate exposed creds, without creating a new, larger,
> security hole." (Devon, 2026-10-08)

The sentence has two halves, and both are risks. The baseline isn't zero risk. Today an exposed
credential waits on about eleven human acts, and the exposure path has never run end to end in the
SDS. Every security impact in this document compares the hole an option opens against the hole
that slowness leaves open.

## Fixed inputs

The following decisions are already made and this document doesn't reopen them (handoff,
"Decisions already made"):

- The SDS owns credential rotation (ADR-0054).
- Rotation records use change-manager's `work` source. The worker lives in this repository as
  `src/rotation_worker/`. Expiry is age-only for now.
- One standing package per credential on the `non-software-operational` profile. Devon clicks every
  gate on the first rotations. Rotation can be requested on demand.
- A rotation package declares its true reach; `live_estate` only for a hosted consumer.
- The `rotated_by_sds` interlock.
- For OpenRouter, the SDS mints and revokes through a management key (Option 2). Its containment
  is open.

## Requirements

A design that misses any of these is rejected. Each one names where it comes from.

### Secret handling

- **R1. No value leaves the process that uses it.** No secret value appears in a transcript, tool
  argument, log, evidence row, observation, commit, or process argument list. Values are generated
  and consumed in one process; records carry sha256 prefixes and lengths only. Source: ADR-0054,
  handoff constraints, method lesson #72.
  - This tightens the legacy executor, which accepts values in argv for `bws secret create/edit`
    and `security add-generic-password -w` (research §4, "Accepted weaknesses").
- **R2. No LLM touches a value.** Agents decide and orchestrate; deterministic code moves values.
  Source: ADR-0054, handoff question 3.
- **R3. No secret travels through an infraops MCP tool.** The worker calls provider and Coolify
  HTTP APIs in-process. Source: `~/Projects/CLAUDE.md`, two confirmed leaks.
- **R4. Every parsed `bws` call passes `--color no`, and a process that needs two BWS identities
  reads each into its own variable.** Source: `~/Projects/CLAUDE.md`, orchestrator CLAUDE.md.

### Triggers and authority

- **R5. Only structured, trusted sources trigger a rotation or a containment action.** The allowed
  triggers are the registry (`rotate_requested`, age) and the scanner's structured findings
  (`security-drift-cli cred-findings`). Content fetched from mail, web pages, issues, commits or
  tasks never triggers anything, directly or through an agent's inference. Source: global
  CLAUDE.md, "Secure Way of Working" rule 2; handoff question 6.
  - Fast containment (question 6) is safe only under this requirement. A forged trigger that
    disables keys is a denial-of-service lever.
- **R6. A machine may only act on a credential the registry hands to the SDS** (`rotated_by_sds =
  true`) and only through that credential's approved standing package. Source: amendment 2.
- **R7. Human gates shrink only by explicit graduation, never by simulation.** Source: handoff
  constraints; memory "human gates shrink, never simulate".
- **R8. Some credential is the root, and Devon rotates it by hand.** The design names the root and
  every credential the root can reach. Source: handoff question 1.
- **R9. Each automated path holds the narrowest authority its provider allows**, and the design
  states, per credential the rotator holds: where it's stored, which identities can read it, its
  scope, its expiry, and who rotates it.

### Order and recovery

- **R10. Make-before-break.** The old value is revoked last, and only after the new value verifies
  at every consumer. A partial deploy never revokes. Source: ADR-0054.
- **R11. Every step is resumable and idempotent from persisted state.** State holds fingerprints,
  never values. A re-run after a crash at any step either completes or refuses with a named reason;
  it never guesses (the legacy executor's "never guess" rule, research §4).
- **R12. A worker never rotates the credential it authenticates with during that run.** Rotating its
  own orchestrator bearer cuts it off its evidence path. That credential gets a different path.
  Source: research §3 row 7.
- **R13. Every step fits inside its lease**, including a Coolify redeploy and the BWS one-hour
  revocation lag. A lapsed lease can't file evidence. Source: research §4, "Leases".

### Proof

- **R14. A probe counts only after it's shown to refuse a known-bad value** under the same
  conditions it runs in. Live is exactly 200, dead is exactly 401 (or the provider's documented
  equivalent); anything else is indeterminate and the worker refuses. Probes don't follow
  redirects. Source: ADR-0054, credentials.md #71, method lessons.
- **R15. "Done" means the new value works at each consumer and the old value is dead**, each proved
  by a probe. A green write (`gh secret set`, a Coolify PATCH) isn't proof. Source: credentials.md
  #153.

### Scope and environment

- **R16. A scheduled job's credential path is proven by running it with only `PATH` and `HOME`.**
  Source: handoff constraints.
- **R17. A rotation that touches a hosted consumer runs only inside the `live_estate` change
  window**, enforced where the worker acts, not only at dispatch. Today nothing windows operational
  units (research §4, "Reach and change windows"). Source: ADR-0054, handoff question 10.
- **R18. Every mint, revoke and disable is attributable** to a named machine identity and recorded
  as SDS evidence with fingerprints, so the chain from trigger to revoke is traceable like any other
  change. Source: ADR-0054 decision.

## Wanted, not required

These make the system better. A design can defer any of them with a stated reason.

- **W1. Reversible automatic containment for exposures.** When an exposure finding arrives, the
  machine disables the exposed key at once (OpenRouter `PATCH {disabled: true}`), then replaces it
  on approval. Disable and delete are different authorities: disable is reversible, delete isn't.
  Only providers with a reversible disable qualify.
- **W2. Fewer long-lived credentials.** Remove a credential instead of rotating it where a provider
  allows: GitHub App installation tokens for `FACTORY_PR_TOKEN`, short expiries on minted keys.
- **W3. One contract with per-provider adapters**, rather than one procedure per class (question 4).
- **W4. One human act per routine rotation**, reached by the four graduations amendment 1 names.
- **W5. Rotator credentials split by provider.** Each provider's mint authority lives in its own
  BWS project, readable by its own machine account, so a defect in one adapter can't reach
  another provider's jewel.
- **W6. Not tied to the operator machine being awake.** Devon's concern of 2026-10-07: Keychain
  staging "will limit us too much in the future".
- **W7. A provider's expiry date as a due trigger**, alongside age (deferred by ADR-0054; listed so
  the contract leaves room for it).
- **W8. Self-issued bearers with overlap.** Orchestrator and change-manager accept two values per
  identity during a rotation, so their bearers can rotate make-before-break.
- **W9. Spend and blast limits on minted keys**: a credit `limit` and an `expires_at` on every
  OpenRouter key the machine mints.

## Non-goals

- Rotating credentials that aren't in a registry. Registering them is a separate step.
- Machine minting for providers with no API: BWS machine tokens, GitHub PATs, Atlassian tokens,
  Anthropic keys. These stay human-minted (research §2).
- Detecting exposures. The scanner and the registry own detection; this design consumes their
  findings.

## Options by question

Each section leads with the option that adds the least machinery, then the alternatives. The
"Devon decides" line is blank until he answers.

### Q1. Where does the rotator run?

| Option | Pros | Cons | Security impact |
|---|---|---|---|
| **A. Operator machine, as Devon's user** (today's model) | The tokens, Keychain and launchd lanes already exist. No new host. | Needs the Mac awake (fails W6). Keychain ties it to one machine. | **(inference, high confidence)** The scheduled lanes read Keychain items with no prompt as Devon's user, so any process running as that user can too, including every Claude session in bypass mode. R2 ("no LLM touches a value") is a convention on this host, not a boundary. |
| **B. Operator machine, separate macOS user** with its own login keychain, run by a LaunchDaemon | Same host, real boundary: Devon's user, and every agent session, can't read the rotator's keychain. Moderate build cost. | Still needs the Mac awake (fails W6). A second user to administer. Root on the Mac still reads everything. | Turns R2 into an enforced boundary. The jewel sits behind an OS account boundary instead of a convention. |
| **C. Container on the VPS** | Always on (meets W6). Near the Coolify API. | The VPS hosts every app. Its secrets would live in Coolify env, readable by any `read:sensitive` Coolify token and by the infraops MCP leak path. A new runtime to harden. | Co-locates the rotator's jewels with the estate they protect. A compromise of Coolify or the VPS takes both. |
| **D. GitHub Actions runner** | Ephemeral, logged, always on. | Jewels become Actions secrets. Anyone who can push a workflow to that repo can read them, and the factory's own token can push workflows (`FACTORY_PR_TOKEN`). Environment protection on a personal account is limited (unverified). | Puts the jewels one workflow edit away from the factory. Worst option against the kill-chain. |
| **E. Inside the orchestrator** | One process. | The orchestrator is an internet-facing API. Its word guards forbid `coolify` and outbound clients by design. | Puts mint authority behind the public API. Rejected by existing invariants. |

I recommend B now, with the adapters written so the same code can move to C later if W6 becomes
pressing. B is the only option that makes R2 enforceable without a new host.

Devon decides:

### Q2. Who holds mint and revoke power, and how is it contained?

The research ranks the jewels an automated rotator would hold (§3). In order: the Coolify API token,
a GitHub identity that writes Actions secrets in eight repositories, a GitHub App private key, the
cred-rotation BWS token, the OpenRouter management key, and an OpenAI admin key. Whichever identity
can read them holds their union.

| Option | Pros | Cons | Security impact |
|---|---|---|---|
| **A. One rotation BWS token reads every jewel** (today's shape, extended) | One credential to manage. | The union problem in full: one token reaches every provider. | A single compromise reaches every jewel. Largest new hole. |
| **B. One BWS project and machine account per provider jewel** (W5) | A defect in one adapter can't reach another provider. Each jewel rotates on its own. | More machine accounts, each human-minted (BWS has no token API). On a single host, the host still holds all of them. | Limits a code defect or a misrouted call to one provider. Doesn't limit a host compromise; Q1-B is what limits that. |
| **C. B, plus the worker fetches only the jewel for the unit it's working** | Least standing exposure in memory. | Slightly more plumbing per run. | The process working an OpenRouter unit never holds the Coolify token. |

Per-jewel containment, independent of the option above:

- **Coolify token.** The biggest jewel: team-wide, it can rewrite or delete any app. Options: keep
  hosted-consumer deploys human for now, or give the worker a token with `write` and `deploy` but
  not `read:sensitive`, and prove the deploy by probing the consumer rather than reading the env
  back. Whether `write` without `read:sensitive` can PATCH envs is **unverified**.
- **OpenRouter management key.** Account-wide and unscoped. Containment the docs support: store it
  only in its own BWS project; set `limit` and `expires_at` on every minted key; delete or disable
  only the hash the worker pinned at creation; never list-and-match. Whether a management key can
  carry an expiry is **unverified**.
- **GitHub secret writer.** A fine-grained PAT limited to Secrets: write on the repositories that
  hold a copy, nothing else.

I recommend C, with the Coolify token kept out of the worker until a narrower scope is measured.

Devon decides:

### Q3. One contract or one procedure per class?

| Option | Pros | Cons | Security impact |
|---|---|---|---|
| **A. Port the legacy executor's procedure per class** | Least new design; the executor's guards are proven. | Each new class adds a procedure; this is the drift Devon called out on 2026-10-07. | Neutral; guards stay per class and can diverge. |
| **B. One contract with adapters** (W3): mint, verify-new, store, deploy, verify-consumers, revoke, confirm-dead, record. Each adapter declares which steps it can do; a step it can't do becomes a human step in the package. | One set of guards and one evidence shape. A provider without an API is the same contract with human steps. | Up-front design of the contract. | Guards are written once and tested once. The ordering (R10) is enforced by the contract, not by each adapter. |

I recommend B.

Devon decides:

### Q4. Remove credentials instead of rotating them?

| Option | Pros | Cons | Security impact |
|---|---|---|---|
| **A. Keep the PAT for `FACTORY_PR_TOKEN`** | No change. | Long-lived; eight copies to re-set on every rotation; no API to mint. | A 180-day secret in eight repositories. |
| **B. GitHub App installation tokens** | 1-hour tokens; nothing in eight repositories. | The App private key becomes the long-lived jewel, UI-minted. The App needs `workflows: write`, which widens an App whose reach is already wider than its work. Whether an installation token can set Actions secrets is **unverified**. | Trades eight long-lived copies for one long-lived key. A net gain only if the key is stored as tightly as Q2-C. |
| **C. Short expiries on every machine-minted key** (W9) | Cheap where the provider allows it. | More frequent rotations. | Shrinks the useful life of any leaked value. |

I recommend C everywhere it applies now, and B as its own increment after the unverified fact is
measured.

Devon decides:

### Q5. What does an exposure trigger, and how fast?

| Option | Pros | Cons | Security impact |
|---|---|---|---|
| **A. Same flow as a due rotation** | No new authority. | Containment waits on every human gate. | The exposure window stays hours to days. |
| **B. Automatic reversible disable, replacement on approval** (W1) | Containment in minutes for providers with a disable. | A consumer that depends on the key goes down until replacement. Needs R5 strictly. | Opens a denial-of-service lever if a trigger can be forged; R5 closes it. Never deletes automatically. |
| **C. B, plus automatic replacement for credentials with no hosted consumer** | Fastest recovery. | Graduates more gates at once. | Adds mint authority to the automatic path. |

I recommend B for providers with a reversible disable; A for the rest.

Devon decides:

### Q6. Which human gates graduate?

Amendment 1 names four graduations. The question is which, for which classes, and on what evidence.

| Gate | Graduate? | Security impact |
|---|---|---|
| Policy grant for rotation package revisions | Candidate after N clean rotations | A revision only restates the standing package with a new occurrence; low risk if the grant refuses any change to authority or reach. |
| Policy-recognised decompositions | Candidate | Low if the decomposition is fixed per contract (Q3-B). |
| Known-good pattern for `operational_action` | Per class only | This is where mint and revoke authority is granted. Keep human for any class whose jewel is above the OpenRouter management key in the ranking. |
| Deterministic evaluator for probe evidence | Candidate | Safe only with R14: the evaluator must see the known-bad control in the evidence. |

The change record approval stays human (ADR-0054). Devon picks N.

Devon decides:

### Q7. Self-issued bearers

**Verified 2026-10-08:** `src/orchestrator/identity/auth.py` holds one `token_hash` per key id, and
`_validate_m2m_credentials` refuses two key ids for one `agent_id` ("machine credentials must map
one-to-one"). So an orchestrator bearer can't rotate make-before-break today without a code change.
Clients pick the key id with the `X-Credential-Key-Id` header.

| Option | Pros | Cons | Security impact |
|---|---|---|---|
| **A. Rotate with a brief outage** | No code change. | Every client of that bearer fails until it reloads. Self-lockout for the worker's own bearer. | Neutral on security; costs availability. |
| **B. Accept two hashes per key id during a rotation** (W8) | Make-before-break. | A code change to an authentication path, reviewed by mutation. | A second valid value exists for the overlap; bounded by the rotation's lease. |

Devon decides:

## Per-class summary

The following table shows, per live class, what the API allows and the recommended disposition
under the recommendations above. "Human" means a console step in the package.

| Class | Live credentials | Mint | Revoke | Recommended |
|---|---|---|---|---|
| OpenRouter key | 1 | API | API | Fully automated; auto-disable on exposure |
| GitHub PAT (fine-grained) | 2 | Human | API (unauthenticated) | Human mint; machine deploy, verify, revoke |
| Atlassian API token | 1 | Human | Human (unverified) | Human mint and revoke; machine deploy and verify |
| BWS machine token | 4 | Human | Human | Human mint and revoke; machine stores and verifies. One of these is the root (R8). |
| change-manager bearer | 4 | Generated | Generated | Machine, once the Coolify question in Q2 is settled |
| orchestrator bearer | 6 | Generated | Generated | Machine after Q7-B; never the worker's own bearer in its own run |

## Facts to measure before the design commits

Each of these can be probed without a live secret, or with a throwaway key.

1. Whether a Coolify token with `write` but not `read:sensitive` can PATCH application envs.
2. Whether an OpenRouter management key can carry an expiry, and whether `/api/v1/auth/key` still
   answers like `/api/v1/key` (throwaway key).
3. Whether a disabled OpenRouter key probes 401 (throwaway key).
4. Whether a GitHub App installation token can write Actions secrets.
5. The `bws project list` exit code and message for a revoked token.
6. How `external_system` and `operator_machine` windows compose in code (research §4, unverified).
7. Whether the rotator's Keychain items are readable without a prompt by Devon's user. Inferred
   from the scheduled lanes; confirm by item ACL, not by reading a value.

## Decisions needed from Devon

1. Where the rotator runs (Q1). I recommend a separate macOS user on the operator machine.
2. How jewels are split (Q2). I recommend one BWS project per provider, fetched per unit, and the
   Coolify token kept out of the worker for now.
3. Contract or procedures (Q3). I recommend one contract with adapters.
4. Exposure response (Q5). I recommend automatic reversible disable where a provider supports it.
5. Self-issued bearer overlap (Q7). Outage or a code change.
6. Which credential is the hand-rotated root (R8). I'd expect the BWS machine token that can read
   the rotator's projects.
7. Gate graduation threshold N (Q6).
8. The parked OpenRouter rotation: continue under a revised package once the design lands, or
   retire it now.
