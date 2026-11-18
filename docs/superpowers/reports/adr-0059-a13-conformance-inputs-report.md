Outcome: done

# ADR-0059 Amendment 13: the bump driver's conformance inputs (D-62 C)

- **PR:** #741 (ready for review, not merged), branch `hq/adr-0059-a13-conformance-inputs`, base
  `ad721fc0509de725f808059f6ce2878b9f613e26`.
- **Content commits:** `6ab6e9919473f4b46c10aff7db9333f63a509bad` (the amendment and `deploy.md`)
  and `2009916be5e50fd220359a20f79831d2f51d071e` (review fixes). This report's commit is the head.
- **Amendment's line range:** lines 3674-4001 of
  `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`. The table starts
  at line 3967.

## What I built

I appended Amendment 13 after Amendment 12. It follows Amendment 11's form: Status, Decided by
(D-62's question and recorded answer quoted in full; I checked both verbatim against sds-hq
`902da11b`), Relates to, a line-number note, Context, Decision items 1 to 7, Verify items, and the
table. No earlier passage is edited.

The amendment makes the following decisions:

1. **`code`:** code-standards becomes a fourth bump-driver clone, at
   `$HOME/Developer/code-standards`, the only path the claim resolves under the generator's
   allowlist. It runs through a shim on the image's interpreter, and `tomlkit` reaches that
   interpreter through an orchestrator change. The new-`HOME` guard widens to cover the clone.
2. **`infra` and `checks`:** the nightly's event, built as Amendment 11's was.
3. **Event content:** ids and statuses only, with any member keyed by full lowercased
   `owner/repo`. `timestamp` is the report's `generated_at`, and the 36-hour bound keys on it. An
   unreadable report is recorded, not skipped, to keep parity with the Mac.
4. **Reader and hold:** the driver reads the newest event by `timestamp`, only on a verified chain
   pass. It holds before the generator runs, so no decomposition is approved that a person must
   supersede. A hold covers only members whose claim reads the event.
5. **Signal:** a keyed alert through the driver's existing alert-hub sync. The nightly's exit and
   ping are unchanged.
6. **Where each change lands:** security-standards, orchestrator, and project-standards only if
   needed. No change to code-standards or intent-packages.
7. **Authority:** exactly D-62 C's five items. HQ's proposed names: actor and `source.system`
   `mac-conformance-facts`, action `mac_conformance.reported`, step `3g-3-inputs`.

Table rows, each with its lines at `ad721fc0`. The first eight rows cover the ADR's body and
Q6/Q9:

| Passage | Disposition |
|---|---|
| Decision 1, the clone list (306-311) | Extended |
| Decision 1, the `uv sync` venv (311-316) | Not extended to code-standards |
| Decision 2, the App's installations (614-617) | Extended |
| Decision 2, the bump driver's read scope (651-654) | Stands |
| Decision 5, the `bump-driver` row (773) | Stands |
| Increment 5 (1035) | Extended |
| The week bar's done-when (1299-1301) | Stands |
| The week bar, "the one that feeds a moved lane" (1309-1310) | Extended |
| Q6 (1512-1513) | Stands, extended to code-standards |
| Q9 (1553-1556) | Extended |

The next twelve rows cover Amendments 3 to 11:

| Passage | Disposition |
|---|---|
| Amendment 3, decision item 4 (2017) | Stands |
| Amendment 7, decision item 1 (2786-2795) | Extended |
| Amendment 7, decision item 2 (2796-2801) | Extended |
| Amendment 7, decision item 6 (2867-2872) | Extended |
| Amendment 7, the step 3f-2 paragraph (2883-2886) | Stands |
| Amendment 8, decision item 1 (2924-2927) | Superseded in part |
| Amendment 8, decision item 2 (2934-2935) | Extended |
| Amendment 8, decision item 4 (2945-2948) | Extended |
| Amendment 8, decision item 6 (2952-2953) | Stands |
| Amendment 9, decision item 1 (3004-3009) | Stands |
| Amendment 11, decision item 3 (3322-3325) | Stands |
| Amendment 11, decision item 5 (3353-3357) | Extended as a consequence |
| Amendment 11's row on Amendment 8 (3424) | Stands |

ADR-0055 is governed in six rows and not edited:

| Passage | Disposition |
|---|---|
| Amendment 24, item 9 (3424-3430) | Governed |
| Amendment 24, items 11 and 16 (3457, 3481-3486) | Governed |
| Amendment 25, item 1 (3616-3624) | Governed |
| Amendment 25, item 3 (3628-3642) | Governed |
| Amendment 25, item 6 (3665-3668) | Governed |
| Amendment 26, item 1 (3768-3773) | Governed |

**`deploy.md`:** I rewrote step 1 (lines 1012-1024 at the base) so it isn't a blocker. It points to
Amendment 13 and lists the acts and code steps by repository. It corrects two facts: the `checks`
cell reads `violation` (`checks.not-wired`), and it adds the `hook:` input. It also says infra-drift
stays on the Mac (D-41), with "until increment 9 moves it" dropped. Finally, it notes that steps 5
and 8 change.

## Tests and checks run

- `make check` on its own database, `orchestrator_test_hq_w_216`, at `2009916b` with a clean tree:
  exit 0, **15607 passed, 1 skipped**. An earlier run before the review fixes gave the same count.
- Security scan from the repo root: 0 BLOCK, 2 WARN (`.gitignore`, `.bws-secrets.toml`), and 1
  INFO. All three are on files this PR doesn't change.
- I re-verified W-214's facts at the same commits: project-standards `4673d726` (`checkers.py`,
  `wiring.py`, `config.py`), code-standards `37de9024` (`STANDARD_VERSION` 1.1, `tomlkit` its one
  dependency), security-standards `23854235` (the nightly at 03:30, the schema's `source.system`
  enum, the `portfolio_scan` adapter), and infraops-mcp-server `c3359bf1` (infra-drift at 03:00).
- I checked each member's `PROJECT.md` at its `main` on 2026-11-18. All eight declare
  `code: '1.1'`. brain and change-manager declare `infra`. The five Mac entries are as W-214 lists
  them.
- `/code-review` wasn't run: the change is docs only. In its place I had an independent advisor
  review the draft, and I applied its fixes in `2009916b`.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (appended)
- `docs/operations/deploy.md` (step 1)
- `docs/superpowers/reports/adr-0059-a13-conformance-inputs-report.md` (this report)

## Self-review

- Every number in the table was re-read with `sed` at the base. The D-62 quote matches the source
  verbatim after whitespace normalization.
- The amendment decides no code. Shapes it leaves open are verify items with a named step: the
  wiring set's source, the unreadable record's time, the mint's shape, `tomlkit`, the alert key,
  and the silent case.
- In the table, the Amendment 3 row sits among the decision rows rather than in file order. It's
  cosmetic.

## Concerns

- **"The ADR's outcome 1."** ADR-0059 has no passage by that name. HQ's A11 handoff glosses the
  same phrase as "every signal has a home", so I cited ADR-0021 amendment 1, as Amendment 11 did.
- **Forged-event reach.** Every append holder can write an event that releases a hold. D-62's
  question doesn't mention it, so the amendment states it as a consequence. Amendment 11's D-56
  explicitly accepted the same reach; D-62 didn't.
- **The hold is wider than the Mac's.** One event with one bound means a stale infra-drift report
  also holds security-standards and project-standards bumps, whose `checks` cells read only wiring
  facts. That follows D-62's words. If Devon wants it narrower, the event needs two times.
- **Naming.** `mac-conformance-facts`, `mac_conformance.reported` and `3g-3-inputs` are my
  proposals. W-214 had proposed `infra-drift` for the actor. I didn't use it because the event also
  carries Mac wiring facts, and the name could be mistaken for infra-drift's own identity.
- **Interpretation of "holds".** Today a non-green claim becomes an approved decomposition that
  only a person can supersede. So I made the hold a pre-generator check: no decomposition is
  proposed, and the bump retries next pass. The code step must keep the driver's bound no looser
  than project-standards' 36 hours.
- **Standards coverage.** No standard in the handoff covers free text in the append-only chain. I
  followed Amendment 11's statuses-only precedent.
- **Follow-ups outside the ADR** (also in the PR body):
  - ADR-0055 amendments 24, 25 and 26's reader passages.
  - `package-intake.md` line 146 and `factory-events-chain.md` line 83.
  - `.bws-secrets.toml` lines 516-525.
  - The header comment in `lanes/bump-driver/compose.yaml`, and `scripts/run-bump-driver.sh` lines
    52-55.
  - ADR-0059 decision 1, lines 311-316 (the stale `uv sync` text).
  - `deploy.md` steps 5 and 8's counts.
- **Teardown.** The test database `orchestrator_test_hq_w_216` and the worktree's `.venv` remain
  for HQ's teardown after the merge.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply. Docs only.
- **Code Brain rule 4 (never silently swallow an exception):** doesn't apply. Docs only. At design
  level, a missing, unusable or stale event fails closed and raises a keyed alert (decision items 4
  and 5). The nightly's skip of a bad input appends an unreadable record rather than vanishing.
- **Code Brain rule 5 (never log secrets, credentials, tokens):** the amendment names no secret
  value. It cites the read password only by truncated UUID (`2f708d08-…`), and the event carries ids
  and statuses only.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN` value):** the diff contains no token and no inline value, and the scan reports
  0 BLOCK.
- **Infra Brain 1032 (`cred.exposure-rotate`):** unchanged by this amendment. I saw no secret value
  anywhere: I printed the infra report's keys, counts and one timestamp only.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. Docs only, and the chain database
  isn't public; the lane reaches it over the `coolify` network.
