# Credential rotation increment 5: OpenRouter (session handoff)

ADR-0055 designs credential rotation as a pull-based executor in an isolated OrbStack VM.
Increment 4 built the executor and ran it end to end on production with a fake adapter (report:
`docs/superpowers/plans/2026-10-12-rotation-increment-4-report.md`). Increment 5 rotates the
first real credential, `openrouter-generic`. It drops the key's Keychain copy, moves its keeper to
its own BWS project, builds the OpenRouter adapter with its management key, and runs the rotation
on production with every gate clicked.

This is the first increment in which the executor holds a provider admin credential and moves a
live value. Its scope ends at OpenRouter: no hosted consumer, no Coolify token and no bearer.

## What the session must produce

Each item below is merged, and `openrouter-generic` has been rotated on production by the
executor: the old key revoked and probed dead, the new key in its keeper and working for every
consumer. ADR-0055's "Increments" section and amendments 1 and 2 are the list of record. The
increment 4 report's proposed amendment 4 isn't accepted yet: read it, and where it bears on
this work, put it to Devon. Where this handoff and the ADR disagree, the ADR wins and the session
says so in its report.

1. **A consumer inventory, before any write.** The registry's last sweep is 2026-07-02, with two
   consumers: the BWS secret and the Keychain item that `~/.zshrc` exports as
   `OPENROUTER_API_KEY`. Search again: every repository's `.bws-secrets.toml` and source for the
   BWS UUID and the variable name, the shell, LaunchAgents and the Coolify environments. One lead
   is known: FacelessTT's design document says FacelessTT resolves `OPENROUTER_API_KEY` from its
   environment first and falls back to this BWS UUID
   (`FacelessTT/docs/superpowers/specs/2026-05-31-openrouter-script-comparison-design.md`), and
   FacelessTT runs on dev Coolify. Settle whether it is a consumer, and through which BWS account.
   The brains' `BRAIN_OPENROUTER_API_KEY` is a separate key and stays out of scope. Also establish
   what actually needs `OPENROUTER_API_KEY` in every login shell: an export at shell init puts the
   value into every agent session's environment, so if nothing needs it there, a fetch at the
   point of use replaces the export. Record what you searched and each exclusion. A hosted consumer changes the plan (the `live_estate` window,
   human writes until increment 6), so stop and put it to Devon.
2. **The Keychain copy dropped** (ADR-0055, per-class section, Devon 2026-10-08): whatever needs
   `OPENROUTER_API_KEY` fetches it from BWS, so the keeper is the only stored copy. Replace the
   Keychain read at `~/.zshrc` with a BWS read through the identity Devon chooses (Decisions for
   Devon), at the place item 1 shows it is needed. Remove the `keychain` consumer from the
   registry and register its replacement. Two kinds fit, and their fields differ
   (`destinations.py`, and the kind notes at the top of `.cred-consumers.toml`): `runtime-fetch`
   is `{repo, ref}`, for code in a repository, and `shell-export` is `{file, var}`. `~/.zshrc`
   belongs to no repository. Either kind is an attested one, so the plan needs a human act for it
   (item 4). Delete the Keychain item `openrouter-api` once the rotation has retired the value it
   holds.
3. **The keeper moved to its own project** (decision 4 and amendment 1's canary procedure):
   `Rotation / Keeper / openrouter-generic`. Give the root write on it, and every consumer account
   read. Prove each consumer reads a canary secret there with only `PATH` and `HOME` set, then
   delete the canary and move secret `9661da8f-ac66-4e97-a31f-b42500bb849c`. The UUID survives the
   move (amendment 1, question 5). The root has no write on `Ops / Platform`, so the move itself is
   Devon's act. Update the `project` field in every `.bws-secrets.toml` that names the secret, in
   the same change.
4. **The OpenRouter adapter**, `src/rotation_worker/adapters/openrouter.py`, registered in
   `ADAPTERS`. It supplies a `Provider` and a `Plan`, as the fake adapter does, and its steps run
   through `contract.py`. The contract was built against the fake provider and probably needs
   extending, never bypassing. Today `Provider` has one `probe(value)` with one target, `revoke()`
   returns nothing and can file no evidence, and `Adapter.build` receives only the state
   directory, so nothing gives the provider the management key's UUID (`config.toml` has no field
   for it). Extend the contract where OpenRouter needs it, keep decision 3's invariants in the
   contract, and mutate each change. Its plan holds a human act for the shell's consumer: `contract.py`
   refuses retire-old until a person's recorded fingerprint attests each attested-kind consumer.
   Every unit's envelope sets `touches_no_hosted_service: true` unless item 1 finds a hosted
   consumer, or each claim waits for the `live_estate` window (`driving-a-unit.md`, "Claim a
   rotation unit"). Amendment 1 fixes the provider facts:
   - **Mint** with the management key: `POST /api/v1/keys`, with an `expires_at` written in the
     `…Z` form (the `+00:00` form returns 400) and a credit `limit` (decision 4: minted keys are
     as weak as the provider allows).
   - **Probe:** live is exactly 200, and the known-bad control is a malformed key, which answers
     401 `User not found.`. Key-info lags a revoke by up to about a minute; inference doesn't.
     So confirm-dead uses a zero-cost completion on a `:free` model chosen at probe time, beside
     the same completion with the new value (200) and with the malformed key (401). Key-info
     polled until 401 is the fallback, with a deadline of a few minutes.
   - **Revoke id:** derived at revoke time, never taken from evidence. Take the sha256 of the
     quarantined value, and require `GET /api/v1/keys/{that hash}` with the management key to
     return 200 with the same `label` that key-info returns for the value. Only then delete.
     Matching by label alone stays forbidden (T8).
   - **The live-before half:** malformed and revoked keys both answer 401, so the revoke step's
     key-info 200 on the quarantined value is the proof the key was live before. Amendment 1 says
     increment 5 must keep it, so file it as evidence.
   - **Retire-old for today's key** is the first decision for Devon (Decisions for Devon). The
     per-class table says human, but the ADR gives no reason, and the requirements' reason
     (revoke only the hash pinned at creation) is what amendment 1 replaced. A human retire-old
     can't produce the live-before half. Decision 3 fixes the decomposition per adapter, so the
     adapter carries one path, not both.

   Tests reach no live endpoint: serve OpenRouter's answers from a local fake.
5. **The management key**, in `Rotation / OpenRouter`, which the root reads and nothing else
   does. The executor fetches it by UUID only while an OpenRouter unit runs (decision 4). It is
   account-wide and unscoped (a provider limit), and it joins the hand-rotated set: add it to
   `docs/operations/rotation-executor.md`'s credentials table with how to create and rotate it.
6. **Revision 2 of `rotation-openrouter-generic`**, written for the executor. Revision 1 describes
   increment 2's by-hand flow (Keychain staging, HQ's scripts, `record-rotation`), and none of that
   applies. Rewrite these parts:
   - `profile_fields.destinations`, copied from `rotation-worker compare-destinations
     openrouter-generic` after item 2's registry change lands;
   - one acceptance criterion per machine step, written from the adapter plan's step list;
   - `reach`, re-derived from item 1's inventory;
   - an `occurrence` from item 7's request.

   The increment 4 canary's AC-002 put a store-step fact in the stage step and had to be waived;
   write each criterion from what its step does. Devon approves the revision by name after
   reading its diff against the last approved revision and the comparison command's output
   (`docs/operations/rotation-proposer.md`, step 3).
7. **The production run** (increment 5's "request `openrouter-generic` again and run it with
   every gate clicked"). Set `rotate_requested` in infraops' registry, and bring the request
   through the rotation proposer and the work carrier as `docs/operations/rotation-proposer.md`
   describes. Then decompose it by the adapter's plan, with the envelope keys in
   `docs/operations/rotation-executor.md`. Reinstall the VM at the merged commit first. Finish by
   checking, by fingerprint only:
   - the old key is dead;
   - the keeper and every consumer hold the new key;
   - the staging and quarantine secrets are gone;
   - a fresh login shell gets the new key.

   Then remove `rotate_requested`, and make the next security-drift run report no rotation
   finding for `openrouter-generic`. Find out what records the rotation for security-drift now
   that `record-rotation` isn't the executor's.

## Read first

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`, in full: decisions
  1 to 10, the verification table, the threat model, the per-class disposition, and amendments 1
  and 2.
- `docs/superpowers/plans/2026-10-12-rotation-increment-4-report.md`: the run's sequence, proposed
  amendment 4, and the operational notes.
- `docs/operations/rotation-executor.md`: how the executor decides, the decomposition keys,
  installing the VM, and its credentials.
- `docs/operations/rotation-proposer.md`: the request path, and Devon's two reads before approval.
- `docs/operations/driving-a-unit.md`, "Claim a rotation unit" and "Resolve dependencies".
- infraops-mcp-server `.cred-consumers.toml`, credential `openrouter-generic`, and
  `src/security-drift/cred-rotation.ts` for the `openrouter-key` class policy.
- intent-packages `packages/rotation-openrouter-generic/`, revision 1 and its lineage.

## Where the code is

Starting points, not designs. Read each before using it; the shapes are the code's, not this
handoff's (`memory: handoffs-state-pointers-not-shapes`).

| Concern | Where to start |
|---|---|
| The contract, its invariants, the `Provider` protocol | `src/rotation_worker/contract.py` |
| An adapter's plan and provider, end to end | `src/rotation_worker/adapters/fake.py`, `adapters/__init__.py` |
| Revision, registry and destination checks | `src/rotation_worker/checks.py`, `registry.py`, `destinations.py` |
| BWS access in-process | `src/rotation_worker/secret_store.py` |
| Values and fingerprints | `src/rotation_worker/values.py` |
| Outbound HTTP allowlist | `tests/architecture/test_wsp21_invariant_scan.py` (`OUTBOUND_ALLOWLIST`) |
| The production flow, through the real routes | `tests/api/test_rotation_worker_flow.py` |
| Provisioning the VM | `scripts/rotation-worker-vm/provision.sh` |

## Decisions already made

Don't reopen these; they are Devon's (ADR-0055 and its amendments).

- The Keychain copy is dropped; the keeper is the only stored copy (per-class section).
- One keeper project per credential, readable by exactly its consumers' accounts; consumers are
  proven with a canary before the move (decision 4, amendment 1).
- Every minted OpenRouter key carries `expires_at` and a credit `limit` (decision 4).
- The revoke id is derived from the quarantined value and confirmed by the provider, never taken
  from evidence or matched by label (amendment 1).
- Devon clicks every gate on each class's first rotation (decision 10). Which gates graduate is
  decided after this run.
- No automatic disable or revoke on an exposure (decision 6).

A new design fork found during the build goes to Devon, least machinery first
(`memory: devon-prefers-less-new-machinery`). Everything else is the session's to decide
(`memory: construction-mode-not-bear-wrestling`).

## Decisions for Devon

Put these to Devon at the start. Each changes what the session builds.

1. **Whether the executor retires today's key by machine.** Amendment 1's derive-then-confirm
   revoke works for any key on the account, not only keys the executor minted, and only a machine
   revoke produces the live-before evidence amendment 1 requires. Machine retirement amends the
   per-class table; human retirement means amendment 1's evidence is missing for this run. Ask
   before building the plan.
2. **Which BWS identity the shell uses to read `OPENROUTER_API_KEY`** once the Keychain copy is
   gone. The shell needs a BWS token on the Mac to fetch the keeper, so the choice is which
   account holds read on `Rotation / Keeper / openrouter-generic`. Least machinery first: an
   existing Mac identity that isn't one of the session's own (see Constraints), at the cost of
   widening what it reads; or a new account that reads only this keeper. List the existing
   identities and what each reads before asking.
3. **The minted key's credit `limit` and `expires_at`.** The key does Devon's real work, so a
   limit too low breaks it. The expiry must outlast the class's rotation age
   (`cred-rotation.ts`), or the key expires before it is due.
4. **The exposure-response factors** that decision 6 says Devon names before increment 5.
5. **Who clicks `/review`.** Decision 10 has Devon clicking every gate on this first OpenRouter
   rotation. In increment 4 the session drove the clicks in his signed-in browser with his
   consent for that run (`memory: browser-gate-automation-is-per-run`). Ask again; the ADR's
   default is that he clicks.
6. **Amendment 4 (increment 4 report)**, where it bears on this run. Item 3 (attestation matched
   by fingerprint, not destination) matters if the inventory finds more than one attested
   consumer. Item 5 (200-character destinations) bears on the new consumer's fields. Item 7
   (`factory submit` fails from a worktree) bears on the intake: run it from the main trees.

## Devon's acts

The session prepares each so it takes Devon one sitting, and says exactly what to click. No value
passes through a transcript, a tool argument or the Mac's clipboard where a path around it exists
(`memory: secrets-never-in-tool-arguments`). In increment 4, a token pasted at a hidden prompt
lost its first character, the third such paste on this machine, so shape-check every pasted
value in-process
(`memory: check-saved-token-shape`).

1. Creating `Rotation / Keeper / openrouter-generic` and `Rotation / OpenRouter`, and giving the
   root write on the first and read on the second. In increment 4 the grants didn't save on the
   first try; confirm them by listing the root's projects from the VM.
2. Granting each consumer account read on the keeper project, for the canary proof.
3. Moving the secret `9661da8f-…` from `Ops / Platform` to the keeper project.
4. Creating the OpenRouter management key and storing it in `Rotation / OpenRouter`.
5. Approving revision 2 by name, after the two reads.
6. Approving the change record, and clicking the `/review` gates (decision 5 in the preceding
   section).
7. Attesting the shell's consumer at `/review` with the new key's fingerprint, after running a
   fresh shell that reads it.
8. Revoking today's key at the console, if decision 1 keeps retire-old human.

## Constraints

- **Edit only in worktrees**, one per repository, each with its own venv and test database. The
  scheduled lanes run the main trees.
- **The management key and the live values exist only in the VM.** No test, fixture, transcript
  or Mac file holds them. Evidence carries sha256 prefixes, lengths and status codes only.
- **The executor's root is the only BWS identity that writes the keeper.** The session's own BWS
  identities (the ones it reads from Keychain to drive the SDS) read nothing in the `Rotation /`
  projects, and none is given a grant there.
- **`OUTBOUND_ALLOWLIST`**: a file that imports an HTTP client needs an entry with a reason.
- **Dependencies are few and pinned by hash** (T7). Name each new one and why in the report.
- **Run `make check` from a clean tree**, and `/code-review` on each diff. Mutate each new guard
  by hand and report the count and every survivor (`memory: mutate-the-code-run-the-controls`).
  Before any `git reset --soft origin/main` squash, rebase on `origin/main` and read the staged
  file list.
- **Build the flow test's intake the way production does.** Increment 4's first production pass
  failed because the tests used an intake shape the emitter never produces. Where a test stands
  in for a production input, take its shape from the producer, not from the consumer.
- **Recovering a failed rotation unit** with attempts left is `POST /work-units/{id}/requeue` as
  SYSTEM; `/review` has no requeue form. A waiver at `/review` must follow a `failed`
  adjudication on the same evidence.

## Out of scope

- Hosted consumers, the Coolify token, writes and restarts, and the bearers (increment 6).
- The rotation GitHub App and Actions-secret writes (increment 7).
- The brains' `BRAIN_OPENROUTER_API_KEY`.
- The canary package's AC-002 wording, which its next revision fixes.

## Closing steps

1. Merge each pull request when its checks pass; the session merges its own work.
2. Deploy the orchestrator only if orchestrator code changed, with
   `scripts/deploy_orchestrator.py`, and verify it as `docs/operations/deploy.md` describes.
   Reinstall the VM at each merged executor change, and set `source_revision` in its config.
3. Resolve this increment's backlog item, naming the pull requests, the deployed revision, the
   VM's installed commit, and the run's unit ids.
4. Report, at `docs/superpowers/plans/<date>-rotation-increment-5-report.md`:
   - what shipped;
   - the mutation counts and survivors;
   - the inventory and its exclusions;
   - the run's unit ids and fingerprints;
   - the dependencies added;
   - anything the ADR got wrong, as proposed amendment text, not an edit;
   - increment 6's prerequisites.
5. Check each main tree's `git status` for stray files, then remove each worktree, test database
   (including xdist's `_gw` copies) and merged branch.
6. Write the increment 6 handoff if Devon asks for it.
