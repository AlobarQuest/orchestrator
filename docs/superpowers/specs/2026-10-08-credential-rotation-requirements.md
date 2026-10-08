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

- **W1. (Rejected by Devon, 2026-10-08; see Q5.) Reversible automatic containment for exposures.** When an exposure finding arrives, the
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

**Devon, 2026-10-08:** a rotator on the personal Mac is fragile and won't carry over to a SaaS
offering. That rules out A and B as the long-term home.

| Option | Pros | Cons | Security impact |
|---|---|---|---|
| **F. A dedicated rotation executor that pulls work from the orchestrator** | Connected to the SDS the same way factory-runner is: the orchestrator holds approvals, units and evidence; the executor claims approved rotation units with its own WORKER identity, does the provider calls, and files fingerprint evidence. Always on. The same program can later run inside a customer's environment, so a SaaS customer keeps their own provider credentials. | A new host to run and patch. The executor's bootstrap BWS token sits on that host and is the root (R8). | Needs no inbound ports (it only calls out). No LLM, no Claude session, no infraops SSH key or `vps_exec` path reaches it. Its admin credentials are separated from the apps they protect, unlike C. |

Where F runs today, in order of preference:

- **A small separate server, in its own Hetzner project.** Hetzner API tokens are scoped to a
  project (**unverified**; to measure), so the infraops Hetzner token couldn't snapshot, rebuild or
  read the executor's server. Devon's own SSH key is the only way in.
- **A container on the existing VPS.** Cheaper, but it inherits C's problem: Coolify and anything
  that can reach that host can reach the executor.

For a SaaS offering, F is the shape that carries over: the provider runs the orchestrator, and
each customer runs the executor next to their own credentials, the way self-hosted CI runners work.

I recommend F on a separate server in its own Hetzner project.

**Devon asked, 2026-10-08: could F run in an OrbStack VM on the Mac, moving to a Hetzner server
when there are clients?** Measured the same day against the existing `ubuntu` machine, with
OrbStack's default settings:

- `orb -m ubuntu sudo -n true` succeeds: Devon's macOS user gets root in the VM with no password.
- `/Users/devon/Projects` is visible inside the VM.
- `orb -m ubuntu mac uname -s` returns `Darwin`: the VM can run commands on the Mac.

So the VM separates processes but not authority. Anything running as Devon's user, including every
Claude session in bypass mode, can become root in the VM and read the executor's credentials. That
is a weaker boundary than a separate macOS user (option B).

OrbStack's isolated machines (`orb create --isolated`, OrbStack 2.2.3) close only the other
direction. Per https://docs.orbstack.dev/machines/isolated, an isolated machine has no Mac file
system, can't reach the Mac over the network or run `mac` commands, and gets no SSH agent; with
`--isolate-network` it can't reach other machines either. The same page says the Mac keeps "SSH and
`orb` access", and `orb --help` documents `orb -m <machine> -u root` as the way to log in as root.
`--set-password` sets a password for the default user and root, which governs `sudo` inside the
machine, not `orb -u root` from the Mac. The page also states that every machine shares one Linux
kernel, so isolation "isn't a full security boundary". So an isolated machine protects the Mac from
the VM, not the VM from the Mac. Whether `orb -u root` works without a password on an isolated
machine is documented by implication only, not measured.

**Devon, 2026-10-08: no budget for a second server; asked whether a Claude Code deny rule on
`orb -m <machine> -u root` could close the gap.** A deny rule stops the obvious command, but not the
access: `ssh` to the machine, a script that runs `orb`, a subprocess from another language, and the
VM's disk image all reach the same data. Measured the same day: every machine's disk lives in
`~/Library/Group Containers/HUAQ24HBR6.dev.orbstack/data/data.img.raw`, mode 644, readable by
Devon's user. A deny rule also binds only Claude Code, not Codex or any other agent. It's a useful
guardrail against an accident, not a boundary against an injected session.

A separate macOS user (option B) costs nothing and gives an OS boundary: its files and keychain
are unreadable to Devon's user without `sudo`, and `sudo` needs the account password and is in the
Claude Code deny list. Devon's user is an admin (measured), so the boundary is the password prompt.

The executor's design doesn't depend on where it runs, so the OrbStack VM is a good place to build
and test it with throwaway credentials. I recommend it holds no live admin credential, and that the
first live rotation runs on the separate Hetzner server.

Devon decided (2026-10-08): **a dedicated OrbStack VM** (isolated, with network isolation) runs
the executor, built so it moves unchanged to a VPS if the SDS becomes a SaaS offering. No separate
macOS user. **Accepted residual risk:** an agent running as Devon's user can reach the VM. Devon's
reasoning: the Mac already holds the keys to every other host, so a determined agent on it isn't
stopped by moving the rotator elsewhere either.

Risks of this placement other than a hostile agent, and the requirement each one adds:

- **The Mac is asleep or off.** Rotations wait. A rotation interrupted by sleep resumes (R11). For
  an orchestrator bearer, an interruption between restarting with the new hash and writing BWS
  becomes an outage when the previous hash's `until` passes; resuming finishes it.
- **The VM is deleted or reset by accident** (an `orb delete`, an OrbStack reset). State that only
  lives on the VM is lost, including a new value not yet stored. So rotation state lives in the
  orchestrator (evidence, fingerprints) and the new value is stored in BWS before anything depends
  on it; the VM holds nothing that can't be rebuilt. The VM is excluded from Time Machine
  (`data_allow_backup: false`, measured), so no secret lands in a backup, and nothing on it can be
  restored from one either.
- **Someone runs the executor by hand.** The executor acts only on a unit it claimed after a human
  approval, so a hand run can't skip the approval. Its authority comes from the orchestrator, not
  from whoever starts it.
- **A compromised dependency.** The executor's Python dependencies run beside the admin
  credentials. Dependencies are pinned with hashes, and the executor has as few as possible.
- **Ports exposed to the LAN.** OrbStack exposes machine ports to the LAN by default
  (`machines.expose_ports_to_lan: true`, measured). The executor listens on nothing.
- **A shared kernel** with the `ubuntu` dev machine and the Docker engine. A container escape on the
  same Mac reaches the VM. Low likelihood, and accepted with the residual risk above.

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

Devon decided (2026-10-08): agreed. One BWS project per provider, fetched per unit; the Coolify
token stays out of the worker until a narrower scope is measured.

### Q3. One contract or one procedure per class?

| Option | Pros | Cons | Security impact |
|---|---|---|---|
| **A. Port the legacy executor's procedure per class** | Least new design; the executor's guards are proven. | Each new class adds a procedure; this is the drift Devon called out on 2026-10-07. | Neutral; guards stay per class and can diverge. |
| **B. One contract with adapters** (W3): mint, verify-new, store, deploy, verify-consumers, revoke, confirm-dead, record. Each adapter declares which steps it can do; a step it can't do becomes a human step in the package. | One set of guards and one evidence shape. A provider without an API is the same contract with human steps. | Up-front design of the contract. | Guards are written once and tested once. The ordering (R10) is enforced by the contract, not by each adapter. |

I recommend B.

Devon decided (2026-10-08): one contract with adapters.

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

Devon decided (2026-10-08): **no automatic disable.** An exposure finding is one input among
several; whether and how fast to contain depends on factors the finding doesn't carry. Exposure
response goes through a human decision. Option A, with the factors to be named in the design.

### Q6. Which human gates graduate?

Amendment 1 names four graduations. The question is which, for which classes, and on what evidence.

| Gate | Graduate? | Security impact |
|---|---|---|
| Policy grant for rotation package revisions | Candidate after N clean rotations | A revision only restates the standing package with a new occurrence; low risk if the grant refuses any change to authority or reach. |
| Policy-recognised decompositions | Candidate | Low if the decomposition is fixed per contract (Q3-B). |
| Known-good pattern for `operational_action` | Per class only | This is where mint and revoke authority is granted. Keep human for any class whose jewel is above the OpenRouter management key in the ranking. |
| Deterministic evaluator for probe evidence | Candidate | Safe only with R14: the evaluator must see the known-bad control in the evidence. |

The change record approval stays human (ADR-0054). Devon picks N.

Devon decides: open. Amendment 1 already defers graduations until one rotation has run.

### Q7. Self-issued bearers

**Verified 2026-10-08:** `src/orchestrator/identity/auth.py` holds one `token_hash` per key id, and
`_validate_m2m_credentials` refuses two key ids for one `agent_id` ("machine credentials must map
one-to-one"). So an orchestrator bearer can't rotate make-before-break today without a code change.
Clients pick the key id with the `X-Credential-Key-Id` header.

| Option | Pros | Cons | Security impact |
|---|---|---|---|
| **A. Rotate with a brief outage** | No code change. | Every client of that bearer fails until it reloads. Self-lockout for the worker's own bearer. | Neutral on security; costs availability. |
| **B. Accept two hashes per key id during a rotation** (W8) | Make-before-break. | A code change to an authentication path, reviewed by mutation. | A second valid value exists for the overlap; bounded by the rotation's lease. |

Devon decided (2026-10-08): **option B, the code change**, with the time-boxed previous hash
described in "The code-change alternative". An exposure-driven rotation may still skip the overlap.

### What the bearer outage is

This section explains Q7's option A. It describes an orchestrator bearer; change-manager bearers
differ (noted at the end).

**What a bearer is.** Each machine program (the verifier, the observer, factory-runner's workflows)
proves who it is to the orchestrator with a bearer token. The value lives in BWS. The orchestrator
holds only its sha256, in the Coolify env variable `ORCHESTRATOR_M2M_CREDENTIALS`, and reads that
variable only at boot.

**Why there's an outage.** The orchestrator accepts exactly one hash per identity (verified
2026-10-08, `identity/auth.py::_validate_m2m_credentials`). A rotation has two halves that can't
happen at the same instant:

1. The orchestrator restarts with the new hash. From then on the old value gets 401.
2. Every client switches to the new value. Clients that fetch from BWS on each run switch on their
   next run. Copies held elsewhere (factory-runner's bearer has seven Actions-secret copies) switch
   only when each copy is re-set.

Between those two moments, any client still holding the old value gets 401. That gap is the outage.
It affects only the programs using the rotated bearer, not the orchestrator as a whole.

**What fails during the gap.** A scheduled lane using that bearer fails its pass and pings its
Healthchecks check as failed. A factory-runner workflow using a stale Actions secret fails its run.
Nothing is lost: lanes re-run on schedule and failed passes write nothing.

**The order that keeps the gap short:**

1. Generate the new value and keep it in the rotating process.
2. Confirm no dispatched run is live (the orchestrator must never restart during one).
3. Write the new hash into `ORCHESTRATOR_M2M_CREDENTIALS` and restart the orchestrator.
4. Write the new value to BWS, then re-set every Actions-secret copy.
5. Probe: the new value gets 200, the old value gets 401.

The gap is from step 3 to the end of step 4, typically a few minutes. Inside the 02:00-06:00
window, most lanes don't run in it at all.

**Recovering from a failed rotation:**

- **The orchestrator doesn't boot after step 3.** A malformed credentials variable fails boot
  closed, and then every program is down, not just one. Recovery is to write the previous hash back
  and restart. So the rotating process records the previous hash (a hash is safe to keep) before it
  writes, and the variable is validated before the restart. This is the riskiest step.
- **The process stops between steps 3 and 4.** The orchestrator accepts only the new value, and BWS
  still holds the old one. Recovery is to finish step 4 from the value the process persisted, or to
  restore the previous hash. This is why R11 requires persisted state, and why the new value is
  persisted (0600, outside the transcript) before step 3.
- **An Actions-secret copy is missed.** That workflow fails with 401 on its next run. Recovery is to
  re-set the copy; the registry lists every copy.

**change-manager bearers differ.** change-manager stores the plaintext value in its env and
redeploys with a rolling update, so the old and new containers overlap briefly. The gap and the
recovery are otherwise the same.

### The code-change alternative (Q7 option B)

**The change.** Each identity in `ORCHESTRATOR_M2M_CREDENTIALS` may carry one optional previous hash
with an expiry time, for example `"previous": {"token_hash": "<sha256>", "until": "<UTC time>"}`.
`authenticate_m2m` accepts the current hash always, and the previous hash only before `until`.
Boot refuses a previous hash whose `until` is more than one change window away.

**The process becomes:**

1. Generate the new token and save it securely.
2. Confirm no dispatched run is in progress.
3. Write the new hash as current, the old hash as previous with `until` at the end of the window,
   and restart. Both tokens now work.
4. Store the new token in BWS and update every Actions-secret copy.
5. Probe that the new token works at the orchestrator and through each client path.
6. At `until`, the old token stops working with no second restart. Probe that it's refused.

No program sees a 401 at any point. A missed Actions-secret copy fails only after `until`, and the
step 5 probes are there to catch it first.

**Drawbacks:**

- **It changes the authentication path.** It's a small change, but it's in the most
  security-sensitive code in the orchestrator, so it needs review by mutation and a test that a
  previous hash past `until` is refused.
- **Two tokens are valid during the overlap.** For a routine rotation that's harmless. For an
  exposure, the leaked token stays valid until `until`. The rotator can skip the previous hash for
  an exposure and take the short outage instead; you decide which when you approve.
- **Restart risk is unchanged.** A malformed setting still stops the orchestrator from starting.
  The setting has one more field to get wrong, and the validation before restart covers it.
- **change-manager needs its own version** of the change for its bearers, in its own repository.

Without `until`, someone has to remember to remove the old hash later. That is the usual way an
overlap scheme leaves an old token valid forever, and is why the expiry is part of the change.

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

## Decisions from Devon (2026-10-08)

1. Where the rotator runs (Q1): **a dedicated, isolated OrbStack VM**, portable to a VPS.
2. How jewels are split (Q2): **agreed** as recommended.
3. Contract or procedures (Q3): **one contract with adapters.**
4. Exposure response (Q5): **no automatic disable**; containment is a human decision.
5. Self-issued bearer overlap (Q7): **the code change**, a time-boxed previous hash.
6. The hand-rotated root (R8): **agreed**: the BWS machine token that can read the rotator's
   projects.
7. Gate graduation (Q6): **open.**
8. The parked OpenRouter rotation: **likely retire**, and redo it under the new design.
