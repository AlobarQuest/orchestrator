# ADR-0054 — The SDS owns credential rotation

- **Status:** Proposed
- **Date:** 2026-10-06
- **Decided by:** Devon (pending). Direction set 2026-10-06: "I intend on having SDS handle
  credential rotations."
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
- **Change window.** Rotation units declare `live_estate` reach. The worker's claim checks the
  `live_estate` change window (02:00–06:00) before it acts, so machine-run rotations happen at
  night. Devon's console steps are not bound to the window.
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

## Open questions

1. **Record source.** Use the generic `work` source, or revive change-manager's `rotation` source
   for these records? Recommendation: `work`. It is the source the carrier and work watcher already
   read, and a second source would need its own retirement path.
2. **Where the worker lives.** A new package in the orchestrator repo (`src/rotation_worker/`, like
   the activation sweep), or inside infraops-mcp-server next to the executor it reuses?
   Recommendation: the orchestrator repo. The executor's TypeScript steps are ported or wrapped,
   because the worker's guard rows, M2M identity and contract tests all live here.
3. **Expiry.** Track credential age only, as today, or read provider expiry dates too?
   Recommendation: age only for now, and add expiry as a per-class field later (GitHub fine-grained
   PATs carry one).
