Outcome: done

# Report: lanes-inc-4-factory-events-chain-adr (W-128)

- **PR:** #PR_NUMBER, branch `hq/lanes-inc-4-factory-events-chain-adr`, head in the PR (this
  report is committed on it).
- **Base:** orchestrator `origin/main` at `2cf43757` (main later moved to `c194950b`, #689, which
  touches neither ADR). Read-only repositories: intent-packages `78bb302`, security-standards
  `1baec77`, and sds-hq `e5c4547` for the cycle's code and `state/decisions.md`.
- **Decisions used:** D-40 B, D-42 A, and Devon's answers to my question: D-43 B (only the
  approvers and the Mac nightly hold the append-only role) and D-44 A (the read-only role also
  serves Devon's hand paths on the Mac). The question and its answer are in
  `lanes-inc-4-factory-events-chain-adr-question-answered.md`.

## What I built

These are docs only. There's no code change and no production act.

- **ADR-0055 amendment 24**, "The factory-events chain of record is a VPS database". It closes
  amendment 22's open item "The chain the carrier reads". It states:
  - the table and hash formula (unchanged from `store._line_hash`, so imported records verify);
  - the three roles, with the owner held by no one at run time;
  - the four append holders and the function they use (D-43 B);
  - the rotation proposer's consequence: it writes no chain event, and its lineage records
    `event_id` None;
  - linear appends (an advisory transaction lock, plus unique `seq` and `prev_hash`);
  - append failure (raise, write nothing, no fallback);
  - the verify (read-only role, one repeatable-read snapshot, a full walk, and a fail-closed refusal
    when unreachable);
  - the readers (D-44 A);
  - the local file (imported, sealed, and kept in backup; the file path is removed at the
    cut-over);
  - the two credentials, as a table;
  - the network, logging, schema setup, no exposure, and what the amendment grants.

  I also added a header line for amendment 24 in the ADR's "Amended" list, as amendments 22 and 23
  did.
- **ADR-0059 amendment 7**, "the factory-events chain moves to a VPS database". It covers:
  - the Phase 1 scope, which is only D-40 B and D-42 A's parts;
  - why the nightly stays on the Mac;
  - the steps FE-1 to FE-6, run inside increment 4 after the change proposer and before the work
    carrier;
  - six named production acts;
  - a "Passages this amendment changes" table.

## Checks

### Check 1: every line and file the handoff names

| Handoff claim | Found | Result |
|---|---|---|
| ADR-0055 a22 open item, ~2964-2967 | Lines 2965-2968 | Holds |
| ADR-0055 a23 at ~3016 | Line 3016 | Holds |
| ADR-0059 a6 at ~2646, with its passages table | Line 2646; table at 2716 | Holds |
| ADR-0059 ~816-826, ~1298, ~1522-1526 | D-9 sentence at 816-818, the agents table at 820-826, SDS HQ at 1297-1298, and Q7 at 1522-1526 | Holds. Line 1298 names SDS HQ only, so amendment 7 marks it "Stands". |
| intent-packages `do_approve`, `operations.py` ~288-305 | `do_approve` at 216; the emit at 292-305 | Holds |
| `verify_approval` ~529 | `default_chain_checker` calls `_verify_chain_integrity` at 529; `verify_approval` is at **533** | Drift. I cite 533. |
| `work_carrier/package_sources.py` ~225-395 | **No such file.** The code is `src/orchestrator/package_sources.py`, lines 226-395 | Wrong path. The amendment cites the real one. |
| `deploy/factory-events-db/docker-compose.yml`, `postgres:16`, port 5545 | Lines 3 and 11 | Holds |
| `src/factory_events/ship.py`: bulk insert, DDL, `TRUNCATE`, one owner role | DDL at 60 and 72, `TRUNCATE` at 74, `FACTORY_DB_DSN` at 46; compose lines 7-9 | Holds |

### Check 2: `package.approved` emitters and readers

I grepped intent-packages, security-standards, the orchestrator and sds-hq for the action. The
only code that emits it is intent-packages `operations.py`, lines 292-305 (`do_approve`). That
function is reached only through `intent-packages approve` (`cli.py`, `_run_approve`, lines
137-180, with the call at 169).

Emitters (callers of `approve`):

1. **Devon's `approve`**: intent-packages `cli.py` 137-180, approver `devon`.
2. **The cycle's standing approval**: sds-hq `src/sds_hq/standing_approve.py`, `approve`, lines
   540-547. It sets `FACTORY_AGENT_ID=lane-credential-policy` (line 36) and runs `--by-policy`.
3. **The bump proposer**: orchestrator `src/bump_proposer/standing.py`, line 397
   (`approve --by-policy`).

The rotation proposer runs `revise` and `transition` only (`rotation_proposer/standing.py`, lines
281 and 290; docstring line 13). Under D-43 B it holds no credential.

Readers:

1. **intent-packages**:
   - `_events_file_has_matching_approval`, `operations.py` 476-508, with the match at 503;
   - `default_chain_checker`, 511-530;
   - `verify_approval`, 533, which fails closed at 586-592.
2. **orchestrator**:
   - `_verify_factory_chain`, `package_sources.py` 231-253;
   - `_iter_package_approval_events`, 276-296, with the match at 294;
   - `_approval_event_to_verified`, 299-330;
   - `_verified_approval_from_events`, 337-361;
   - `_verify_current_approval`, 364-395, which runs the sibling `verify-approval` at 168.
3. **Invokers of those readers**:
   - the work carrier's `emit-intake-payload` (`work_carrier/prepare.py` 117-126; refusal
     `package_not_intakeable` at 152);
   - `orchestrator intake-package` (`cli.py` 254);
   - the `factory` journey (intent-packages `factory/journey.py` 141-148);
   - hand `verify-approval` (intent-packages `cli.py` 211-217).
4. **Not a reader:** `work_carrier/standing.py` trusts the verified payload (docstring 16-22).
   security-standards holds no reader; the action appears there only in docs and the
   `lane-credential-policy` registry file.

### Check 3: nothing grants beyond D-40 B, D-42 A, D-43 B and D-44 A

- **Append-only role:** granted to exactly the three approvers and the Mac nightly (a24 item 4 and
  the credentials table).
- **Read-only role:** granted to exactly the work carrier and the three hand paths (a24 item 9).
- **Owner:** held by no one at run time (a24 item 3). No lane gets it (a7 item 6).
- **Rotation proposer:** gets nothing (a24 item 5).
- **Rotation classes:** none assigned; carried as a verify item.
- **BWS secrets:** both are "to be created", with no UUID or project chosen.
- **Explicit statements:** a24 item 16 and a7 item 6 each say what the amendment grants and
  nothing more.

### Check 4: no false claim about current code

Each claim in a24's Context and Decision cites the lines it rests on. These are the load-bearing
quotes:

- **`approve` aborts on a failed emit:** `except EmitError as exc: raise OperationError(f"approval
  emit failed, aborting: {exc}")` (`operations.py` 304-305).
- **`transition` and `revise` don't abort:** `except EmitError: event_id = None` (`operations.py`
  164-165 and 357-358).
- **The hash formula:** `hashlib.sha256(canonical_json({"seq": seq, "prev_hash": prev_hash,
  "event": event}))` (`store.py` 33-35).
- **The `flock` append:** `fcntl.flock(lock, fcntl.LOCK_EX)` (`store.py` 66).
- **The self-declared actor:** `actor = os.environ.get("FACTORY_AGENT_ID", _DEFAULT_ACTOR)`
  (`emitter.py` 92).
- **The emitter parses `event_id` from a JSON line:** `emitter.py` 52-58.
- **The registry refuses an unknown actor:** `envelope.py` 53-54, called at 87.
- **The high-power adapter reads a Mac file:** `DEFAULT_SOURCE = Path.home() / ".claude" / "audit" /
  "high-power-actions.jsonl"` (`high_power.py` 21).
- **The nightly reports the head:** `observe_fact chain_head` (`factory-events-nightly.sh` 67).
- **The `test_intent_edges.py` names:** `ENVIRONMENT` 38, `READERS` 42, `EVENTS_FILE_PARTS` 43.
- **The nearest rotation class:** `postgres-role` at `rotation_worker/types.py` 46-48, with the class
  map at 76.

## Other checks

- **`make check`:** exit 0: ruff, ruff format, pyright clean; pytest 14,808 passed, 1 skipped in 325 s. It ran at `d534c5b0`; the one later ADR edit corrects a line range in amendment 24, docs only (test database `orchestrator_test_hq_w_128`, migrated with
  `alembic upgrade head`; `SECURITY_STANDARDS_DIR` set to the fixtures).
- **Security scan from the repo root:** exit 0, with 0 BLOCK findings and 2 WARNs. Both WARNs
  predate this diff, which touches neither file:
  - `bws.secret-files-gitignored`: `.gitignore` doesn't cover `*-migration/`.
  - `bws.manifest-matches-usage`: an undeclared UUID `aaff45ca-…` in code.
- **`/code-review` low on `origin/main...HEAD`:** no findings.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`: the amendment 24
  header line and amendment 24 itself.
- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`: amendment 7,
  appended.
- `docs/superpowers/reports/lanes-inc-4-factory-events-chain-adr-question-answered.md`: HQ's
  answered question.
- This report.

## Verify items carried, with the step that resolves each

| Item | Step |
|---|---|
| Where the chain head is anchored outside the VPS database; the candidate is the nightly's `chain:factory-events` `chain_head` fact. FE-6 waits on it. | FE-1 |
| The adapters' deterministic event ids against the primary key, so a re-run skips rather than fails | FE-1 |
| The verify walk's cost. Each carry walks the chain three times; the Mac file held 3,751 events on 2026-11-13. | FE-1 |
| A rotation class for each secret. `postgres-role` assumes one Coolify service, the VPS helper and a restart, but these readers include Mac processes. A new type would be its own amendment. | FE-4 |
| That the database port is reachable only on the tailnet (a bind or a host firewall), or the act stops | FE-4 |
| Which BWS identity fetches each DSN on the Mac, each a direct grant named in the act | FE-4 |

Amendment 22's open item "The cycle's emitting identity" is settled by code
(`standing_approve.py` 36 and 542), and a24 says so.

## Production acts named (pre-named only; none run)

These are in ADR-0059 amendment 7, decision item 4:

1. Create the Postgres resource.
2. Create its schema and roles.
3. Create the two BWS secrets and their grants.
4. Add the vps-backup target, in FE-4 before any data arrives.
5. The cut-over, which is the nightly's target switch.
6. Retire the Mac database.

## Deviations

- **I added a header line** to ADR-0055's "Amended" list. That isn't a pure append, but amendments
  22 and 23 did the same.
- **I added two unnamed steps.** The handoff's six steps don't name a history import or the removal
  of the file path. I put both inside FE-1 (code) and FE-5 (the cut-over act). Without the import,
  existing approvals couldn't verify. Without the file path's removal, a Mac process with no DSN
  could start a second local chain.
- **I named the step labels FE-1 to FE-6,** inside increment 4.

## Self-review

- **The rotation proposer before it moves.** D-43 B speaks of the moved rotation proposer. Under a24
  item 5, it holds no credential on the Mac after the cut-over either, because the file path is
  removed then. So the "no chain event" consequence starts at FE-5, not at 3f-5. a24 states this
  explicitly. Nothing is granted to avoid it.
- **The owner and the trigger.** The owner can drop the trigger, which is why the anchor's home is
  a FE-1 verify item and FE-6 waits on it.

## Concerns

- **No standard covers the anchor.** None in the handoff's Standards covers tamper-evidence when the
  chain's store and its owner coincide. It's carried as FE-1's verify item.
- **Every approval depends on the VPS.** After FE-5, an approval made while the VPS database is down
  fails and must be run again. That includes Devon's interactive `approve`. a24's Consequences
  state it.

## Follow-ups

- HQ dispatches FE-1 (security-standards) next, then FE-2 and FE-3, before any of the three acts.

## Standards followed

- **Code Brain 3 (structured JSON logs to stdout):** a24 item 13 says the append function and both
  verifiers log structured JSON to stdout, never to a file.
- **Code Brain 4 (never swallow an exception):** a24 items 7 and 8 say an append failure raises and
  writes nothing, and that a verify failure, including an unreachable database, refuses. Neither has
  a fallback, and both are logged. The Consequences restate it.
- **Code Brain 5 (never log secrets):** a24 item 13 says no record holds the DSN, a password or a
  credential, and that driver error text isn't logged because it can repeat the DSN. The text holds
  no DSN or password.
- **Infra Brain 591, 592 and 593 (BWS handling):** the text holds no BWS token. Both secrets are
  named by role and "to be created", to be recorded by UUID in `.bws-secrets.toml` (a24 item 11; a7
  act 3).
- **Infra Brain 1032 (cred.exposure-rotate):** a24 item 15 says nothing is declared exposed and no
  secret exists yet to rotate.
- **Infra Brain 212 (HTTPS for public FQDNs):** the database gets no public FQDN and no public port.
  VPS containers reach it over the `coolify` network, and the Mac over the tailnet (a24 item 12; a7
  item 5). The tailnet-only port is a FE-4 verify item.
- **Infra Brain 6 (no migrations at build time):** a24 item 14 and a7 act 2 run the schema as an
  announced act, never in an image build. The append and verify paths run no DDL.
- **Infra Brain 8 (Redis isn't a primary store):** a24 item 1 makes Postgres the chain's primary
  store, with no cache or queue holding an event the database doesn't.
