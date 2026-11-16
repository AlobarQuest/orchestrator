Outcome: done

# ADR-0059 amendment 11 (portfolio feed) build report

- **PR:** #724 (ready for review, not merged)
- **Branch:** `hq/adr-0059-a11-portfolio-feed`, based on `6b356b9f88ae65916ef4c604f5ea1ef55404c10a`
- **Content commits:** `ad3bc0e5a94184a644bdfdf1171d8f4bf1d505de` (the amendment) and
  `c1353c83e898dc63658bd6412a300d13d24eaf95` (fix: the reader's properties, `result` left to the
  code step)
- **Head:** content head `c1353c83`; this report is committed on top of it (an earlier report
  commit, `c14b0a45`, sits between them and is superseded by this one)
- **Amendment's line range:** lines 3210-3425 of
  `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (the heading at
  3210; one blank separator line at 3209)

## What I built

I appended `## Amendment 11 (2026-11-16): the work carrier reads the portfolio scan's results
from the factory-events chain` in Amendment 10's form: Status, Decided by (D-55 and D-56 quoted in
full, both question and recorded answer, byte-compared against sds-hq `4427c4e1`), Relates to, the
line-number note, Context, Decision (six items), Verify items, and the passages table. Nothing
before it was edited.

- **Event's `action`:** `portfolio.scanned` (checked against the schema pattern
  `^[a-z0-9_]+\.[a-z0-9_.\-]+$`)
- **Actor:** `portfolio-scan` (credential-less registry agent, `none-v1`, `capabilities: []`)
- **`source.system` value:** `portfolio-scan`; `source.ref` is the scan time
- **Scan time field:** the event's `timestamp` (as both existing adapters do); deterministic
  `event_id` from `source.system` and `source.ref`, so the same scan never appends twice; rows in
  `evidence`; `result`, and the marker for a scan that measured nothing, left to the code step
- **Reader:** newest event by `timestamp` (not `seq`), used only on a pass whose chain verify
  succeeded, no Mac file and nothing new in `lane-run`'s list; which process walks the chain for
  the event is a 3f-2-feed verify item; holds when the event is missing, more than 48 hours old, or
  has no row for the repository

### The table's rows (lines at `6b356b9f`)

| Passage | Disposition |
|---|---|
| `work-carrier` row (line 791) | Answered |
| Decision 5, work carrier's Mac input (lines 805-811) | Answered; "if it's a file" doesn't apply |
| Done-when, "nor any other moved lane's input may still live on the Mac" (lines 1299-1301) | Stands, with the facts |
| Done-when, `portfolio-scan` feeds the carrier (lines 1309-1310) | Stands |
| Amendment 7 item 1 (lines 2786-2795) | Extended by D-56 (event, actor, source value only) |
| Amendment 7 item 2 (lines 2796-2801) | Extended: the nightly also appends the event |
| Amendment 7 item 6 (lines 2867-2872) | Stands |
| Amendment 7 step 3f-2 paragraph, D-52 A (lines 2883-2886) | Stands; the event is read from that walk |
| Amendment 8 item 1 (lines 2924-2927) | Stands |
| Amendment 8 item 6 (lines 2952-2953) | Stands |

## Tests and checks

- `make check` against `orchestrator_test_hq_w_185` (created and migrated for the run), on a clean
  tree at `c1353c83` with the first report file present: exit 0, "All checks passed!",
  **15,074 items collected; 15,073 passed, 1 skipped**, 11 warnings. (An earlier run at
  `ad3bc0e5` gave the same counts.) The database was dropped afterwards.
- Append-only confirmed: `git diff -U0 6b356b9f -- docs/decisions` has no removed line.
- Security scan from the repo root (`security_scan.cli . --category security`): no BLOCK
  findings (2 WARN, 1 INFO, the repository's standing judgment items).
- Both D-quotes compared, whitespace-normalized, against `git show 4427c4e1:state/decisions.md`:
  all four quoted strings match.
- Every cited line range was read at `6b356b9f` (orchestrator) or the named commit
  (project-standards `90391362`, security-standards `05963b76`).

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (append only,
  +217 lines)
- `docs/superpowers/reports/adr-0059-a11-portfolio-feed-report.md` (this report)

## Self-review

- Append only; no general rule written. Both answers are scoped to the work carrier's portfolio
  input in the "Decided by" line.
- The feed's population is stated precisely: not "every member" but the repositories the sweep
  measures, which are the non-retired members whose `app_changes` grant is `granted` or
  `remediating` (`member_scope.py` lines 23-24, 69-88). A member with no Mac working copy has no
  row, as today.
- Decision item 3 first said the carrier reads the event "from the same verified walk" the
  launcher runs. The launcher runs `factory_chain_read` as its own process and passes only a
  summary, so I replaced that with the properties I can source and made the mechanism a verify
  item (fix commit `c1353c83`).
- The failure signal rests on the existing outcome alarm (held → `UNDECIDED` → exit 2 → `/fail`
  on `sds-work-carrier`), and names the one silent case as a verify item instead of inventing a
  mechanism.

## Concerns

1. **The phase bar (lines 1299-1301).** "Nor any other moved lane's input may still live on the
   Mac by then" sits beside an interim feed produced on the Mac until Phase 3. I recorded the row
   as "Stands" with the facts (the input is an event in the VPS chain, as Mac-written approvals
   are; the producer stays on the Mac until Phase 3 in D-55's words; a stopped scan holds after 48
   hours) and said that neither answer mentions the bar. If HQ reads D-55 as changing the bar, that
   needs Devon's word, not this amendment's.
2. **Handoff pointers the sources don't bear out (none blocks):**
   - "The ADR's outcome 1: every signal has a home" isn't in ADR-0059. I cited ADR-0021
     amendment 1's heading, "every signal has a machine home".
   - "Phase 3's onboarding scan" has no source in this repository; I attributed it to D-55's text.
   - The D-52 A paragraph is lines 2883-2886, not 2883-2887 (2887 is blank).
   - `generated_at` isn't written as `None`: `aggregate.py::to_json` writes no such key at
     `90391362`; `workability.py`'s docstring reads it as `None`. The amendment states it that
     way.
3. **The silent case.** A stale feed with no repository-targeted record queued raises nothing.
   The amendment makes "report on every pass or only when holding" a 3f-2-feed verify item.
4. **Follow-ups outside the ADR** (listed in the PR body): security-standards schema value,
   registry entry and nightly append; project-standards feed; orchestrator reader, its docstrings,
   `test_container_portfolio.py` and the two architecture guards; and
   `docs/operations/onboarding-a-target.md` line 203, which names `portfolio.json`.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply; the change is docs only.
- **Code Brain rule 4 (never silently swallow an exception):** doesn't apply; docs only.
- **Code Brain rule 5 (never log secrets, credentials, tokens):** the amendment names no secret
  value; it refers to the append password and read password by truncated UUID only
  (`2ce0dbc7-…`, `2f708d08-…`), as the ADR does.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN` value):** no token or token-shaped string was written; the security scan from
  the repo root reports no BLOCK finding.
- **Infra Brain 1032 (`cred.exposure-rotate`):** I saw no secret value in any file or output
  during this work, so nothing to report.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; docs only, and the amendment adds
  no network path.
