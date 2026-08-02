# SPEC (future wave) — deterministic deploy, and the inference inventory it belongs to

**Written:** 2026-08-02 · **Status:** deferred deliberately · **Not scheduled.**

**Devon's ruling, 2026-08-02, recorded because it is the reason this is not being built now:**

> *"My reason for pausing is we might still make changes to the deploy process, since we are still
> building it out. It fits right into the work we will be doing to make all of the rest of the system
> use more deterministic tools."*

**So: do not build this against today's process.** The steps below are a *measurement of what is
currently inference*, not a design. When this is picked up, re-derive the procedure from whatever the
process is then, and hold it to the **properties** in §3 rather than to the step list in §2.

---

## 1. The finding

Devon, on being walked through the current deploy: *"It seems to me that it is effectively
undeployable, other than by AI. It seems as if there are a good many things that could be
deterministically done, but are being done by inference."*

That is correct, and it is a specific kind of gap. **WS-P2.18 Increment 7 made the ARTIFACT
deterministic** — the image asserts `org.opencontainers.image.revision`, `sha-<full-sha>` is a
function of the commit, a pushed image is pulled back and its label verified, and (2026-08-02) a tag
cannot be silently re-pointed. **The PROCEDURE that consumes those guarantees never followed.** It
remains a sequence of remembered steps whose correctness lives in CLAUDE.md prose.

**Verified 2026-08-02: no deploy tooling exists.** `scripts/` holds build-side helpers the workflow
calls (`compute_image_tags.py`, `shape_registry_context.py`), drill scripts, and a credential
fetcher. The orchestrator CLI has no deploy verb. Nothing drives a deploy end to end.

### It was also mis-scoped, and that is why it sat

"Automate the deploy" was filed under **policy 4 / self-update**, which is blocked on an authority
question (`what attests an orchestrator self-build that has no work unit?` — P3 `a4939a3839c3`).
**Those are different problems and the blocked one shadowed the unblocked one.** Policy 4 is *the
system deciding to deploy itself*. This is *an operator running a tool*: no attestation question, no
work unit, no control-plane egress, none of WS-P2.18 Increment 6's blockers. **This does not depend
on policy 4 and must not be sequenced behind it.**

## 2. What is inference today — the measurement, not the design

Six points where correctness depends on remembered knowledge. Each was observed on 2026-08-02 while
deploying twice by hand.

| # | Inference | Why it bites |
|---|---|---|
| 1 | Choosing the image `label` | Free text with no rule. Two builds of one commit can be named differently. |
| 2 | Checking `migrations/versions/` **first** | If skipped and migrations exist, the still-running container reports `/health/ready` 503 `migration_drift`. Survivable only because no health check consults it. |
| 3 | `--ref` vs `-f ref=` | Different things. `--ref` selects where the workflow FILE is read from and 422s on a raw SHA; `-f ref=` is the revision and must be the full 40 characters. |
| 4 | Recording the outgoing tag | **Nothing stores it.** On 2026-08-02 the rollback target existed only in a chat message. |
| 5 | Finding the pushed digest | Requires a GHCR API query or reading the run summary. |
| 6 | The digest verification recipe | `container → .Image → RepoDigest`. Inspecting the *container* for `RepoDigests` returns nothing — the recipe in CLAUDE.md was wrong until 2026-08-02. |

## 3. The properties the tool must have — hold it to THESE, not to §2

- **One invocation.** A target revision in, a verified deployment or a refusal out.
- **Fails closed at every step**, and says which step and why. A partial deploy that reports success
  is worse than no tool.
- **The rollback target is recorded as an artifact**, not printed and lost. What was running, its
  digest, and the exact command to return to it.
- **Verification is an assertion, not a display.** The running container's `RepoDigest` must equal
  the pushed digest and its revision label must equal the target SHA, and a mismatch must be a
  non-zero exit — not a line of output a human is trusted to read.
- **It states the migration shape before acting**, and refuses or handles it explicitly. Never
  silently.
- **Nothing it needs is remembered.** If a step requires knowing a trap, the tool encodes the trap.
- **It must be runnable by a person who has read none of CLAUDE.md.** That is the acceptance test
  for the whole thing, and it is the point Devon was making.

## 4. The one design fork

**Does the tool perform the Coolify swap, or stop short and print it?**

Performing it needs a Coolify API credential the tool can reach — a new secret, and it steps around
the standing convention that infra mutation goes through `infraops` rather than ad-hoc HTTP. Stopping
short introduces nothing but leaves one manual step, which leaves the rollback-recording problem
(#4) half-solved.

HQ's lean is **perform it**: a purpose-built reviewed tool is not what that convention was written
against, and a half-automated deploy preserves the exact failure it exists to remove. **But it is a
genuine fork and belongs to whoever builds it.**

## 5. What must be proven

- [ ] A deploy of an unchanged revision is refused or is a no-op — not a silent re-point.
- [ ] A **digest mismatch after the swap exits non-zero.** Construct it; do not assume.
- [ ] A revision carrying migrations is **detected and stated** before anything is built.
- [ ] The rollback artifact exists after a successful deploy and names the previous digest.
- [ ] Someone who has not read CLAUDE.md can run it from the `--help` alone.

## 6. The wave this belongs to

Devon named the theme: *"the work we will be doing to make all of the rest of the system use more
deterministic tools."* Deploy is one instance. Others observed in the same period, offered as
candidates rather than commitments:

- **Package authoring.** A package is ~150 lines of YAML. `factory create` scaffolds placeholders;
  the values are prose. Devon on 2026-08-02: *"I have no amount of useful judgement on that
  package."* A human-usable interface is already intended once the system mechanically works.
- **`factory decompose`'s invocation.** Needs two different BWS identities (no single one can read
  both the SDS and `brains` projects), a PATH entry, a PYTHONPATH, and an env var whose name CLAUDE.md
  **documented wrongly** (`ORCHESTRATOR_API_TOKEN`; it is `ORCHESTRATOR_SYSTEM_TOKEN`). Without the
  correct name it falls through to BWS and is unrunnable.
- **Intake.** Paste JSON into a form; the idempotency key comes from the form field, not the payload.
- **Reach declaration.** An author cannot honestly declare reach without knowing how the target repo
  deploys. Devon's fix direction is App Brain as the source of truth (P1 `c99a4e598506`).
- **Named-check evidence.** Operator-typed on both sides — being fixed by WS-P2.20, and the template
  for what "observed rather than asserted" looks like everywhere else.

**The through-line:** each is a place where the system is correct only because someone remembered
something. The deploy tool is the smallest and most self-contained, which is why it is a good first
instance — but the acceptance test in §3 (*runnable by someone who has read none of CLAUDE.md*)
generalises to all of them.
