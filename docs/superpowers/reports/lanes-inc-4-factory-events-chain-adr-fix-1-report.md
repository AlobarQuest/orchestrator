Outcome: done

# Report: lanes-inc-4-factory-events-chain-adr, fix round 1 (W-131)

- **PR:** #690, branch `hq/lanes-inc-4-factory-events-chain-adr`, fast-forwarded from `c1bb688e`
  (`git log c1bb688e..origin/hq/lanes-inc-4-factory-events-chain-adr` was empty at start).
- **Decisions used:** D-40 B, D-42 A, D-43 B and D-44 A only. No grant, identity or project chosen.

## What I built

Docs only. I changed ADR-0055 amendment 24 and ADR-0059 amendment 7, and updated the original
report (`lanes-inc-4-factory-events-chain-adr-report.md`) with a "Fix round 1" section, corrected
Check 3 and Check 4, the verify items (plus a named-decisions table) and "Standards followed".
Every ADR hunk starts after line 3277 in ADR-0055 and line 2744 in ADR-0059.

- **B1:** a24 item 13 states the bare-id stdout (security-standards `cli.py` 33, stderr 34) and
  the emitter's bare-token fallback (`emitter.py` 63-67; JSON branch 52-58 doesn't match). A new
  FE-1 verify item, "`emit`'s stdout", keeps the id parseable once JSON logs share stdout and
  requires a test of the parse. No JSON line is claimed or required.
- **B2:** a7 Context says neither proposer names `verify-approval`, `factory_events`,
  `events.jsonl` or `FACTORY_EVENTS`; the bump proposer's `approve --by-policy` (`standing.py`
  397) in a container appends to that container's own file from genesis (`store.py` 20-25,
  60-80), which the carrier never reads, or aborts with `EmitError` (`emitter.py` 70-79); the
  rotation proposer emits no approval (D-43 B).
- **B3:** each DSN's BWS project and every machine account granted a read (Mac and lane
  containers) are Devon's named decision before a7 act 3, which stops until he names them. The
  history import's actor and role are his named decision before act 5's import, which stops until
  then; the conflict with items 3 and 4 is left to his decision. The append DSN goes to no shared
  or broad identity (for example `BWS_ACCESS_TOKEN_BROAD`, Keychain `BWS_ACCESS_TOKEN_VPS_BACKUP`,
  nightly lines 104-105) without his decision, because that widens D-43 B. Changed: a24 items 2,
  10, 11, 16, the Increments paragraph and two "Open before building" items; a7 FE-1, acts 3 and
  5, item 6 and "Open before building".

## Tests and checks

- `make check` at `3ece871d`: exit 0; 14,808 passed, 1 skipped (database
  `orchestrator_test_hq_w_131`, migrated, then dropped).
- Security scan from the repo root: exit 0, 0 BLOCK, 2 pre-existing WARNs (untouched files).
- `/code-review` low on `c1bb688e..HEAD`: no findings.
- Each new citation re-read at its source: security-standards `origin/main` `1baec77`
  (`cli.py` 33-34, `store.py` 17-25 and 60-80, `factory-events-nightly.sh` 104-105),
  intent-packages `78bb302` (`emitter.py` 40-79), orchestrator `2cf43757`
  (`bump_proposer/standing.py` 397; the `git grep` over both proposers returned nothing).

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (amendment 24 only)
- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (amendment 7 only)
- `docs/superpowers/reports/lanes-inc-4-factory-events-chain-adr-report.md`
- This report.

## Self-review

- I reflowed two paragraphs (a24 item 10, a7 act 5) to keep lines at 100 characters; their words
  changed only by the inserted clause.
- B2's container claim is conditional on the emitter locating security-standards, because the
  container's image isn't built yet; both outcomes are cited.

## Concerns

- None beyond the review's non-blocking follow-ups, which HQ records in the PR body.

## Standards followed

- **Code Brain 3:** a24 item 13 keeps JSON logs to stdout; the new FE-1 item keeps them from
  breaking the emitter's id parse.
- **Code Brain 4:** a24 items 7 and 8 are unchanged: append failure raises and writes nothing;
  verify failure refuses.
- **Code Brain 5:** no DSN, password or token in the text; item 13 unchanged.
- **Infra Brain 591, 592, 593:** no BWS token anywhere; identities appear only as variable and
  Keychain item names; secrets stay "to be created", by UUID, with project and readers Devon's
  decision.
- **Infra Brain 1032:** a24 item 15 unchanged; nothing declared exposed.
- **Infra Brain 212:** a24 item 12 and a7 item 5 unchanged; no public FQDN or port.
- **Infra Brain 6:** a24 item 14 and a7 act 2 unchanged; schema runs as an announced act.
- **Infra Brain 8:** a24 item 1 unchanged; Postgres is the primary store.
