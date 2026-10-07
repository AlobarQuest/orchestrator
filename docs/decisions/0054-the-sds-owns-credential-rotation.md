# ADR-0054 — The SDS owns credential rotation

- **Status:** Accepted
- **Date:** 2026-10-06
- **Decided by:** Devon, accepted 2026-10-06 ("I intend on having SDS handle credential
  rotations"). Increments 2 to 5 are deferred: tracked, not scheduled.
- **Relates to:** ADR-0011 (known-good patterns), ADR-0019 increment 5a and ADR-0026 decision 5 (a
  producer may propose but never move a change record's status), ADR-0027 (machine intakes name the
  change record that caused them), ADR-0028 (a standing package per target, revised per change),
  ADR-0029 (the work watcher retires a record when its units finish), ADR-0030 (activation models
  and the machine-local producer), ADR-0039 (an out-of-process producer observes and never proposes,
  with named exceptions), WS-0.7 (the infraops credential-rotation lane), WS-P2.13 (the first
  rotation worked through the orchestrator)

## Context

Credential rotation runs in two places today, and neither is the SDS.

- **The infraops lane (WS-0.7).** At 03:00, `infraops-mcp-server`'s security-drift scan
  (`src/security-drift/cred-rotation.ts`) reads the credential registries listed in
  `~/.config/infra-drift/cred-consumers.list`. It files due or exposed credentials to change-manager
  as `security` records, using change-manager's full bearer. At 04:00, if Devon has approved a
  record, a deterministic executor with no LLM (`src/security-drift/rotation-executor.ts`) verifies
  the new value, stores it, deploys it to consumers and confirms the old value is dead. The
  executor refuses three classes: BWS machine tokens, Coolify Postgres passwords and brain MCP keys.
  It never mints or revokes at a provider. SDS 1.1 item L1b increment 1 widened its registries from
  6 credentials to 21 on 2026-10-06.
- **By hand, through the orchestrator (WS-P2.13).** The one rotation of a refused class this year,
  the shared BWS machine token on 2026-07-30, went through `factory create`, a pasted intake, a
  hand-written decomposition and three units (`rotation-inventory`, `rotation-execute`,
  `rotation-verify`). HQ did the inventory and the verification. Devon did the console mint and
  revoke and the Keychain writes. The units used the `operational_action` capability, which no
  runner may hold, so nothing was dispatched.

So the credentials that matter most are rotated by hand. The lane that runs on a schedule covers
only the classes that matter least, and the SDS records neither in its own chain.

## Decision

The SDS becomes the one place credential rotation happens. A rotation is ordinary SDS work: it has a
change record, an intake, units, evidence, adjudication and completion, and it is traceable like any
other change. The infraops executor stops being a separate lane and becomes the SDS's execution
engine. Its 04:00 window retires once the SDS path covers its classes.

### The flow

1. **Detect.** The security-drift scan keeps detecting at 03:00 from the same registries. For every
   credential that is due or exposed, it proposes a `work` change record through a narrow
   `propose`-scope credential. It no longer posts `security` records with the full bearer. Under
   ADR-0026, a proposer cannot move the record's status.
2. **Decide.** Devon approves or declines the record in change-manager. This is the only gate a
   routine rotation needs.
3. **Carry.** The work carrier carries the approved record into an intake, as ADR-0027 requires. The
   package is a standing rotation package per credential class (ADR-0028 shape), revised per
   rotation with the record id and the credential id. The carrier gains an explicit operational
   branch for packages with no target repository. Today it holds them forever
   (`src/work_carrier/workability.py`).
4. **Execute.** A new machine-local program, the **rotation worker**, claims the rotation units and
   runs the executor's steps: verify the new value, store it in BWS, deploy it to each consumer,
   confirm the old value is dead, and file evidence for each step. It runs on the operator machine,
   because only that machine holds the rotation token, the Keychain and the infraops tools.
5. **Verify and complete.** The verifier adjudicates the evidence, as it does any unit's. The work
   watcher retires the change record when the units complete (ADR-0029).

### What stays human

- **Approving the change record.** One click per rotation.
- **Minting and revoking at a provider console**, for every class whose provider has no API that
  the worker's credentials can use. A BWS machine-account token is the standing example. For these
  classes the rotation package has a human step: Devon mints the new value and stages it where the
  worker reads it. The worker does everything after that, and the revoke is Devon's again.

### The rotation worker's authority and boundaries

- **Credential.** It holds the BWS rotation token (`BWS_ACCESS_TOKEN_CRED_ROTATION`), which can write
  to the Ops / Platform project. This is the first SDS program that can change a live credential,
  and that is the standing-authority change this ADR records. It also needs its own WORKER M2M
  identity in the orchestrator. An identity's `agent_id` must resolve in the registry bundle baked
  into the image, so adding it takes a security-standards commit and an image rebuild.
- **Classification.** It is an out-of-process program, so it gets a row in
  `tests/architecture/test_external_content_observes_only.py`. It writes claims, evidence and
  lifecycle transitions for rotation units only, and only units whose package is a standing
  rotation package. It reads nothing but the registries, the orchestrator and the consumer
  surfaces it deploys to. No external content can mint work through it.
- **Change window.** A rotation package declares the reach its consumers actually have, like any
  other package. It includes `live_estate` only when a consumer is a hosted service; then the
  worker's claim checks the `live_estate` change window (02:00–06:00) before it acts, so
  machine-run rotations of hosted credentials happen at night. A credential held only in BWS and
  the Keychain declares `external_system` and `operator_machine` and has no window. Devon's console
  steps are not bound to the window.
- **Secret handling.** No value appears in a log, an evidence payload, a tool argument or an
  observation. Evidence records fingerprints (sha256 prefixes) and probe outcomes only. Every probe
  must be proven to tell a good value from a bad one before its answer counts (the WS-P2.13
  lesson).

## Increments

1. **Registry coverage** (done 2026-10-06: infraops-mcp-server #107, orchestrator #364,
   change-manager #109, factory-runner #91).
2. **Detect into the SDS.** Security-drift proposes `work` records through a `propose`-scope
   credential. Write the standing rotation packages for the first classes. Give the carrier its
   operational branch. Units are minted and worked by HQ, as WS-P2.13 was, so the flow is proven
   before any machine acts.
3. **The rotation worker, for the classes the executor already supports.** These are GitHub PATs,
   the Bitbucket token, and the OpenRouter and OpenAI keys. The worker reuses the executor's steps.
   The infraops 04:00 window stays as a fallback for these classes until the worker has completed
   one rotation of each.
4. **The refused classes.** Rotate BWS machine tokens, Coolify Postgres passwords and brain MCP keys
   through the worker, using the human console step where a class needs one. Two infraops changes
   come first: a value-from-file option for `coolify_update_app_env` (today the value is a literal
   tool argument, which puts it in a transcript), and a `postgres_password` parameter for
   `coolify_update_database`.
5. **Retire the infraops window.** Remove `run-security-window` from `scripts/change-window.sh`.
   Delete change-manager's unused `rotation` source.

## Consequences

- Every rotation is an SDS change with a traceable chain, and the credentials rotated by hand today
  join the scheduled path.
- An SDS program can now change live credentials. The boundaries above are the only things holding
  that authority in, so each one ships with a guard test, and the worker's classification row is
  reviewed like the image binder's under ADR-0039.
- During increments 3 and 4, two executors exist. The worker and the infraops window must never
  both act on one credential. Increment 3's design must name the interlock. The simplest is for
  infraops to skip any credential with an open SDS rotation unit.
- A rotation that fails partway leaves both values live. That is safe: the old value is revoked
  last, and only after the new one verifies everywhere. The worker reports and stops. It never
  revokes on a partial deploy.

## Settled questions

Devon agreed to all three on 2026-10-06:

1. **Record source: `work`.** Rotation records use change-manager's generic `work` source, which
   the carrier and the work watcher already read. The unused `rotation` source is deleted in
   increment 5.
2. **Where the worker lives: the orchestrator repo**, as `src/rotation_worker/`, like the
   activation sweep. The executor's steps are ported or wrapped there, beside the worker's guard
   rows, M2M identity and contract tests.
3. **Expiry: age only for now.** Provider expiry dates (GitHub fine-grained PATs carry one) come
   later as a per-class field.

## Amendment 1 — increment 2's shape (2026-10-07)

Measuring increment 2 against today's code changed three parts of the plan. Devon decided all four
questions below on 2026-10-07.

**What measurement found.** A rotation that ran the way dependency updates do would take about
eleven human acts, not the one this ADR aims for:

- the change record;
- the package revision (no approval policy grants this profile);
- the decomposition (`factory decompose` only knows dependency updates);
- three authority approvals (no known-good pattern covers `operational_action`);
- five criterion adjudications (every evidence type this profile allows has a human floor);
- the console mint and revoke.

Reaching one gate means graduating four of those gates, each deliberately: a policy grant for
rotation revisions, policy-recognised decompositions, a known-good pattern for `operational_action`,
and a deterministic evaluator for rotation probe evidence. Increment 2 proves the flow with every
gate in place. The graduations are decided after one rotation has run.

**Decisions:**

1. **Who proposes: a new `src/rotation_proposer/` in this repository.** It reuses
   `bump_proposer`'s audited revise, publish and propose path, its confined client and its
   observation. The infraops scan only detects. This replaces "the security-drift scan proposes" in
   step 1 of the flow.
2. **One standing package per credential**, not per class, on the existing
   `non-software-operational` profile with optional `standing` and `credential_id` fields. With one
   package per class, two due credentials of the same class would supersede each other's records.
3. **Devon clicks every gate on the first rotations**: revision, decomposition, authority and
   adjudication. The construction-mode pre-authorization does not cover acts that reach a live
   credential.
4. **Rotation can be requested on demand.** An optional `rotate_requested` date on a registry entry
   raises a finding, so the first rotation does not wait for the first natural due date
   (2026-12-29). The first credential is the generic OpenRouter key: its consumers are a BWS secret
   and a Keychain item, so rotating it redeploys nothing.

**A correction to the flow.** The change record's id cannot go into the package revision: the
revision is created first, and the record names it. The carrier puts the record id on the intake
(ADR-0027).

**A correction to the change window (2026-10-07).** The original text had every rotation unit
declare `live_estate`. Reach is never inferred and members only narrow, so a package claiming a
reach its consumers lack would put a needless window on rotations that touch no hosted service.
The change-window bullet now has each package declare its true reach. The first package,
`rotation-openrouter-generic`, declares `external_system` and `operator_machine`.
