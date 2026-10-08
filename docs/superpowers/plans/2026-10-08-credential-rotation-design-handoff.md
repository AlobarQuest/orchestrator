# Credential rotation design: session handoff

**For:** the session that designs the SDS's credential rotation. **Written:** 2026-10-08, by the
session that built ADR-0054 increment 2 and parked the first rotation.

## The goal, in Devon's words

> "I really need us to think through the best design for as automatic of a system, as possible, to
> rotate exposed creds, without creating a new, larger, security hole."

And from 2026-10-07, when the first rotation was parked: "It seems we will end up with a custom
process for each type of cred rotation … I think we should step back and look at the cred rotation
feature of the system harder, and put together an overall plan."

The session's job is that overall plan. Building comes after Devon accepts it.

## What the session must produce

1. **A threat model** for automated rotation: what the rotator can do, which credentials it must
   hold to do it, who can read those, and what an attacker gains by compromising each.
2. **A design**, recorded as an ADR: either ADR-0054 amendment 3 or a new ADR that supersedes the
   parts of ADR-0054 it changes. It has to say, per credential class, what is automated, what stays
   human, and why.
3. **An increment plan**, and what happens to the parked first rotation (see "Parked state").
4. **The facts the design rests on, measured.** Several are marked unverified in the research. Probe
   them safely, for example with a throwaway key, before the design commits to them.

Do not build rotation machinery in this session beyond safe measurement probes.

## Read first

| Document | Why |
|---|---|
| `docs/decisions/0054-the-sds-owns-credential-rotation.md` | The accepted ADR, with amendment 1 (increment 2's shape) and amendment 2 (what shipped, the interlock, the park). |
| `docs/superpowers/specs/2026-10-08-credential-rotation-research.md` | **The factual base.** All 21 credentials, provider capabilities by class, the authority each automated path needs ranked by blast radius, reusable SDS machinery with file:line, and gaps. |
| `docs/superpowers/specs/2026-10-08-credential-rotation-provider-docs.md` | Provider API findings with every source URL. |
| `docs/operations/rotation-proposer.md` | How the shipped proposer works. |
| infraops-mcp-server `CLAUDE.md`, section "Credential rotation" | The legacy executor, the registry, `cred-findings`, the `rotated_by_sds` handover. |
| `~/Projects/CLAUDE.md` invariants on secrets in MCP tools and on `bws --color no` | Why secrets never pass through infraops MCP tools or tool arguments. |

## Where things stand

### Shipped (ADR-0054 amendment 2)

- **Registry coverage:** 21 credentials across four `.cred-consumers.toml` files, scanned nightly.
- **Detection:** infraops' 03:00 scan raises `cred.exposure-rotate`, `cred.rotation-age` and
  `cred.rotation-requested`. `security-drift-cli cred-findings` exposes them as JSON, and is the
  only definition of "due".
- **Proposal:** `src/rotation_proposer/` revises a credential's standing package and, after a named
  human approves it, proposes a `work` record. It never approves.
- **Carrying:** the work carrier registers an approved rotation record as an intake with no target
  repository, worked by HQ.
- **The interlock:** `rotated_by_sds = true` on a registry entry hands that credential to the SDS.
  The legacy scan stops posting its triggers, the legacy 04:00 window refuses its plans (and any
  unregistered credential's), and the proposer acts only on flagged credentials.
- **The legacy executor** (`rotation-executor.ts`) still runs make-before-break reissues for
  unflagged, executor-supported classes, with Devon minting and revoking at provider consoles.

### Parked state

Backlog item `37bb0f906c7b` holds the detail. In short:

- `openrouter-generic` is flagged `rotated_by_sds` with `rotate_requested = "2026-10-07"`.
- Change record 122 is approved and carried as intake `496f9c81`.
- Decomposition `ff08aeed` is approved. It created units `rotate-and-deploy` (`f509d6e4`) and
  `revoke-confirm-and-record` (`63bfe30b`), both in draft awaiting authority approval.
- **Nothing was claimed, no key was minted or staged.** The decomposition can still be superseded
  (ADR-0052).
- The package, `rotation-openrouter-generic` rev 1, still describes the console mint and Keychain
  staging that Option 2 replaces, and prohibits `infra_mutation`.

The design decides whether this rotation continues as is, continues under a revised package, or is
retired. Retiring means superseding the decomposition and removing `rotate_requested`.

### Facts the research established

- **No credential has an open exposure.** All six recorded exposures are resolved. The only live
  trigger is the on-demand request above. The next natural due dates are 2026-12-29
  (`github-finegrained-mirror`) and 2027-01-30 (`factory-pr-token`). The exposure path has never run
  end to end in the SDS.
- **Few providers let a machine mint a credential.** OpenRouter (management key), OpenAI (service
  accounts), Cloudflare and Resend can. GitHub PATs, Bitbucket and Atlassian tokens, Anthropic keys
  and BWS machine tokens cannot; some can be revoked by API. GitHub App installation tokens are an
  alternative that removes a long-lived PAT but adds a long-lived private key.
- **Automation concentrates authority.** The research ranks the credentials an automated rotator
  would hold. In order of blast radius: the Coolify API token, a GitHub identity that can write
  Actions secrets in eight repositories, a GitHub App private key, the credential-rotation BWS
  token, the OpenRouter management key, and an OpenAI admin key. Whichever BWS identity can read
  them holds their union; **where they live and who can read them is the real containment
  boundary.**
- **Self-issued credentials** (orchestrator and change-manager bearers, brain MCP keys) need no
  provider, but rotating the bearer the worker itself uses can lock it out. Orchestrator bearers
  likely cannot overlap old and new today (an inference from the research, not checked against
  `identity/auth.py`).
- **Gaps in today's machinery:**
  - Nothing enforces a change window on a claim. Windows apply only at dispatch, and operational
    units are never dispatched.
  - Two classes ADR-0054 calls "refused" (`brain-mcp-key`, `coolify-pg-password`) have no
    registered credentials.
  - The infraops MCP redactor is pattern-based and misses several token shapes, so a worker must
    call provider and Coolify APIs directly, never through infraops MCP tools.

## The questions the design has to answer

These are framed so the session decides them rather than inherits them.

1. **Who holds the power to mint and revoke?** For each class with a provider API: which
   credential, stored where, readable by which identity, scoped how narrowly, expiring when, and
   rotated by whom. That last one matters most: some credential is always the root, and it should
   be one Devon rotates by hand.
2. **Where does the rotator run?** The operator machine (Keychain-bound, where today's flow
   lives), a CI runner, a container on the VPS, or the orchestrator. Each puts the root credentials
   somewhere different. Devon's concern on 2026-10-07 was that Keychain staging "will limit us too
   much in the future".
3. **What is the LLM allowed to touch?** ADR-0054 already says the executor has no LLM. Hold that
   as a line: agents decide and orchestrate, deterministic code moves values, and no value ever
   enters a transcript, tool argument, log, evidence row or observation.
4. **One contract or one procedure per class?** For example, one make-before-break contract (mint,
   verify, store, deploy, verify consumers, revoke, confirm dead, record) with per-provider
   adapters that declare what they can do, and a human step wherever a provider has no API.
5. **Can long-lived credentials be removed instead of rotated?** For example, GitHub App
   installation tokens in place of the factory PAT, or short expiries everywhere a provider allows
   them. Fewer long-lived secrets means less to rotate and less to steal.
6. **What does an exposure trigger, and how fast?** An exposed key wants containment in minutes,
   while the current flow has eleven human acts. Is there a safe "contain now, replace on approval"
   path, such as automatically disabling the exposed key? What may trigger a rotation at all? A
   trigger derived from fetched content (an email, a web page) is the prompt-injection path into a
   credential-changing machine.
7. **Which human gates graduate, and to what?** ADR-0054 aims at one approval per rotation.
   Amendment 1 counted eleven human acts and named the four graduations needed: a policy grant
   for rotation revisions, policy-recognised decompositions, a known-good pattern for
   `operational_action`, and a deterministic evaluator for rotation probe evidence. Which of these
   are safe, for which classes, and on what evidence?
8. **How is a half-done rotation recovered?** Make-before-break leaves both values live, which is
   safe; the design still needs a defined resume and rollback for every step.
9. **How is "it worked" proven?** Every probe must be shown to refuse a known-bad value before its
   answer counts. Evidence carries fingerprints and status codes only.
10. **Where does a change window apply?** Rotations that touch a hosted consumer declare
    `live_estate`, but nothing enforces a window on a claim today.

## Decisions already made (do not reopen without Devon)

- The SDS owns credential rotation (ADR-0054).
- Rotation records use change-manager's `work` source; the worker lives in this repository as
  `src/rotation_worker/`; expiry is age-only for now (ADR-0054, settled questions).
- One standing package per credential on the `non-software-operational` profile; Devon clicks
  every gate on the first rotations; rotation can be requested on demand (amendment 1).
- A rotation package declares its true reach; `live_estate` only for a hosted consumer.
- The `rotated_by_sds` interlock (amendment 2).
- For OpenRouter, the SDS mints and revokes through a management key ("Option 2", accepted
  2026-10-07). Its containment is open.

## Constraints any design must keep

- **No secret value in a transcript, tool argument, log, evidence row, observation or commit.**
  Generate and consume in one process; record sha256 prefixes and lengths only.
- **infraops MCP tools are not a secret path.** Two of them have leaked secrets because their
  redaction is best-effort (`~/Projects/CLAUDE.md`). Call the narrow API directly.
- **Every parsed `bws` call passes `--color no`;** a process that needs two BWS identities reads
  each into its own variable, never one ambient `BWS_ACCESS_TOKEN`.
- **A scheduled job's credential path is unproven until it runs with only `PATH` and `HOME`.**
- **Human gates shrink by explicit graduation, never by simulation.**
- **Reach is declared, never inferred;** policy can only refuse.
- **Old values are revoked last,** only after the new one verifies at every consumer.

## Suggested method

1. Read the documents in "Read first", then walk Devon through the research's authority ranking
   and the ten questions. His answers decide the design space.
2. Write the threat model before any option. For each candidate design, name the new crown jewel,
   its blast radius and its containment.
3. Measure the unverified facts the leading option depends on, with probes that cannot cause harm.
4. Draft the ADR with options and a recommendation, and send it through an independent adversarial
   review before Devon accepts it. Earlier reviews in this program found real defects in every
   guard-shaped change.
5. End with the increment plan and a decision on the parked rotation.

## Closing steps

- Devon accepts the ADR. The session that wrote it merges it.
- If the parked rotation is retired: supersede decomposition `ff08aeed`, remove
  `rotate_requested` from `openrouter-generic`, and resolve backlog item `37bb0f906c7b`.
- If it continues: revise `rotation-openrouter-generic` to the accepted design before any unit is
  claimed.
