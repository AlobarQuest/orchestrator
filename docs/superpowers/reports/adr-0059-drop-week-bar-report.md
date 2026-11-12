Outcome: done

# Report: adr-0059-drop-week-bar (worker W-108)

- **PR:** #676, https://github.com/AlobarQuest/orchestrator/pull/676 (ready for review; not merged)
- **Branch:** `hq/adr-0059-drop-week-bar`, based on `origin/main` at `519bb23f`
- **Content commits:** `e7d9ed1c` (the amendment) and `4a991328` (two table rows corrected after
  review). The final content is `4a991328`. The head is the report commit that follows it, which
  can't name its own SHA; earlier report commits are `92acf28e` and `294ed54e`.
- **Amendment's line range:** lines 2646-2725 of
  `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` at `4a991328`.

## What I built

ADR-0059 Amendment 6, "the week bar is dropped", appended after Amendment 5 in the form of
Amendments 3 and 4: Status (Amendment 1's wording, with Devon's words quoted verbatim), Decided by,
Relates to, the line-number paragraph, Context, Decision, and a table of superseded passages. No
earlier text is edited.

The decision items:

1. A moved lane's bar is its cut-over checks: the paired pass matches the Mac run and the Mac job is
   booted out. No time with the Mac off or asleep, no baseline, no week.
2. Increment 6 starts once increments 2 to 5 have cut over.
3. Increments 7, 8 and 9 are each done at their own cut-over checks, which increment 7's ADR-0055
   amendment and increments 8 and 9's plans state. The amendment doesn't define them.
4. Lanes that cut over before the amendment are held to the same bar; a captured baseline is
   evidence of nothing. (I found no report confirming that any baseline was captured. Two increment
   3 reports list it as a future step.)
5. What stays: the dead-man and outcome alarms on the same checks, the paired pass, the boot-out and
   last-ping check, rollback until increment 6, and the executor's heartbeat schedule as an alarm.
6. No new authority.

## Table rows (lines at `519bb23f`)

| Passage | Disposition |
|---|---|
| Context, line 24 | Superseded; the rest of the paragraph stands. |
| Decision 6 heading, line 828 | "and the week bar" names no bar. |
| Increment 6, lines 1048-1049 | Starts once increments 2 to 5 have cut over. |
| Cut-over step 1, baseline capture, lines 1119-1122 | Withdrawn; the rest of step 1 stands. |
| Cut-over step 2, line 1148 | Withdrawn; the ping list, no-running-pass check, boot-out and state comparison stand. |
| Cut-over step 3, lines 1161-1162 | Withdrawn; "the check goes down" stands. |
| Rollback, lines 1245-1246 | Rollback until increment 6 stands. |
| Week bar, bar and baseline, lines 1248-1279 | Superseded; the flip-retention and ping-log-limit verifies (1262-1263, 1269-1270) are withdrawn; the `/fail` meaning stands via decision 3 (741-743). |
| Week bar, what goes quiet, lines 1281-1284 | Moot: nothing is excluded from a week; where each agent runs is unchanged. |
| Week bar, executor's second week and increments 8 and 9's weeks, lines 1286-1293 | Superseded by decision item 3. |
| Week bar, done-when paragraph, lines 1295-1310 | Stands except "and each week has passed" (1299). |
| Week bar, evidence, lines 1312-1318 | Withdrawn, the read-only key's verify included; "no lane holds it" and "no decision grants HQ the broad account" stand (decision 2, 487-490; decision 3, 677-687 and 700-702). |
| Amendment 3, Relates to, line 1900 | Moot. |
| Amendment 3, daily-lanes row, line 1983 | Moot. |
| Amendment 3, "The week bar" bullet, lines 2045-2049 | Moot; the two lanes still stay on the Mac. |
| Amendment 3, passages list, lines 2129-2130 | Moot. |

Amendments 1, 2, 4 and 5 name no week-bar passage. The handoff pointed at "Amendment 3's and 4's
line-number references (about 2129)"; line 2129 is Amendment 3's, and Amendment 4 has none
(`grep -n -i -E "week|baseline"` and a search for its line citations into 1248-1318).

## Tests and checks

- `make check` at `4a991328` (and earlier at `e7d9ed1c`, same result), with this worktree's venv and
  `orchestrator_test_hq_w_108` (`TEST_DATABASE_URL`, `ORCHESTRATOR_DATABASE_URL`,
  `SECURITY_STANDARDS_DIR` at the fixture, `PGTZ=UTC`, `alembic upgrade head` first): exit 0,
  **14677 passed, 1 skipped**, pyright 0 errors.
- Security scan from the repo root (`security_scan.cli . --category security`): 0 BLOCK, 2 WARN
  (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`) and 1 INFO
  (`bws.least-privilege-scope`). The diff touches one Markdown file and names no secret, so none of
  the three comes from it.
- Greps run: `week|baseline`, `ping-log|retention|\bbar\b|survive|asleep|[0-9] days` over the ADR;
  `week.bar|week-off` over other ADRs (no hits); `off for a week|week bar` across the repo.
- Layer manifest: `docs/superpowers/**` (layers.toml line 750) covers this report, and
  `docs/decisions/0059-*.md` (line 143) covers the ADR.
- I didn't run `/code-review`: the change is ADR text with no code, and HQ's Opus whole-branch
  review is the gate for merge.

## Week-bar mentions outside the ADR (follow-ups, unchanged)

- `docs/superpowers/specs/2026-11-05-sds-hq-always-on-design.md:327`: the pilot's success criterion
  quotes Phase 1's gate with "a lane survives the Mac being off for a week".
- `docs/superpowers/reports/adr-0059-amendment-3-forks-stay-report.md`: lines 44, 68 and 150.
- `docs/superpowers/reports/lanes-inc-3-deploy-watcher-report.md:265` and
  `docs/superpowers/reports/lanes-inc-3-landing-ledger-report.md:193`: list the week-bar baseline
  as a cut-over step still to run.

No other ADR, script or operations page names the week bar.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (+81 lines, appended)
- `docs/superpowers/reports/adr-0059-drop-week-bar-report.md` (this report)

## Self-review

- Every line number in the table was re-read against `git show 519bb23f:<file>`. I corrected four
  citations (the reporting-run list starts at 1134, decision 2's identity bullet at 487, and
  the `/fail` meaning is decision 3's 741-743, not a claim about ADR-0021). After an advisor review,
  `4a991328` reworded the 1281-1284 row, which wrongly said the executor and the outside agents stay
  on the Mac, and cited decision 3's line 702 for "no decision grants HQ the broad account".
- The week-bar section isn't superseded as one block: its done-when paragraph and the evidence
  paragraph's authority statements carry content that isn't about the week, and the table keeps it
  (handoff item 5).
- The amendment adds no replacement condition for increment 6 beyond "increments 2 to 5 have cut
  over", and doesn't define the cut-over checks of increments 7 to 9.

## Concerns

- **Year plan quote.** The handoff quotes the done-when with a trailing "(Devon dropped the week-off
  bar, 2026-11-12.)". sds-hq `plan/year-plan.md` line 56 at `ec8e83f` (PR #69's merge) doesn't have
  it. The amendment quotes the file. The substance is the same.
- **Rollback window.** With no week, increment 6 can start as soon as increments 2 to 5 cut over, so
  the time a moved lane can still roll back may be short. That follows from Devon's decision and
  the handoff; I note it so HQ can schedule increment 6 deliberately.
- **Items not in the handoff's list.** Decision item 4 (lanes already cut over, and captured
  baselines) and the heartbeat line in item 5 are mine. They follow from the decision; HQ's reviewer
  can drop either.
- No standard in the handoff was missing for this docs-only change.

## Fix round 1 (worker W-109)

HQ's Opus whole-branch review at `8ab0dccc` found one blocking finding, B1: a `launchctl bootout`
lasts only until the next login, so the cut-over's Mac step must be a bootout plus a disable.

- **Content commit:** `afe47acf`, fast-forward on `8ab0dccc` (nothing later existed on the
  branch). The head is the report commit that follows it.
- **Amendment's line range:** lines 2646-2742 at `afe47acf`. No line before 2646 changed
  (`git diff 519bb23f` shows no removed line).
- **Decision items:** a new item 2 states the bootout plus disable, with the 2026-11-12 restart as
  its reason, the step 2 and step 4 commands, and `launchctl enable` before the rollback's
  bootstrap. Items 2 to 6 became 3 to 7, and the table's cross-references moved with them (the
  increment 6 row now cites item 3, the executor row items 4 and 6). Items 1 and 6 say "booted out
  and disabled". Item 5 adds that an already-cut-over lane counts as moved only once its Mac job is
  disabled and checked as step 4 checks, and states that the execution watch and the landing ledger
  were disabled on 2026-11-12, in an interactive session with Devon's approval. The year plan quote
  in the Context is unchanged.
- **Rows added (lines at `519bb23f`):** 1151-1152 (step 2: bootout, then disable, plist kept), 1164
  (step 4: the check adds the disabled state in `launchctl print-disabled`), and 1190-1191
  (rollback: `launchctl enable` before the bootstrap). Amendment 1's lines 1664-1665 got no row, as
  the handoff directs.
- **Decided by:** adds Devon's approval of the disable (interactive session, 2026-11-12).
- **Non-blocking notes:** both applied. Item 5 says a captured baseline "is no longer needed for
  any bar"; the 1248-1279 row says "consistent with decision 3's outcome-alarm bullet (lines
  741-743)". None skipped.
- **`make check`** at `afe47acf` with `orchestrator_test_hq_w_109` (same environment as before):
  exit 0, **14677 passed, 1 skipped**, pyright 0 errors.
- **Security scan** from the repo root: 0 BLOCK; the same 2 WARN and 1 INFO as before.
- The earlier sections of this report describe the round-0 numbering (items 1 to 6).

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply; docs only, no code.
- **Code Brain rule 4 (never swallow an exception silently):** doesn't apply; docs only, no code.
- **Code Brain rule 5 (never log secrets):** the amendment and report name no secret value. The one
  key reference is decision 3's line range, not the key.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history, no inline
  `BWS_ACCESS_TOKEN` value):** none written; the security scan from the repo root has 0 BLOCK.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value anywhere in this task.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply; docs only, no endpoint.
- **Infra Brain 2890 (never commit a ping URL):** no ping URL appears in the diff.
