Outcome: done

# Report: record D-81's answers in ADR-0055 Amendment 32 (docs only)

Worker W-309, 2026-11-24. Branch `hq/broker-phase-c-d81-answers`, based on `origin/main` at
`3675d24f`. `origin/main` hadn't changed ADR-0055 since `3675d24f` (`git diff --stat 3675d24f
origin/main` over the file was empty; both resolved to `3675d24f`).

## What I built

One file changed: `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`,
inside Amendment 32 only. The amendment stays `Proposed (2026-11-24)`; no other amendment changes.

### By requirement

1. **Answers subsection.** A new `### Answers (2026-11-24)` sits after decision item 15 (the end of
   the Open items list, line 4895 at `3675d24f`) and before `### Phase C is done when` (line 4897).
   It holds Devon's quote, the date and time (23:32 UTC), the seven-part answer string, the pointer
   to `docs/superpowers/reports/broker-phase-c-adr-question-answered.md`, the handoff's table
   verbatim, the D18 context for (4), cited as "codebase-review D18, 2026-11-24", and HQ's three
   items (13 A, 11, 10 open), marked as HQ's under build-mode rule 2 with the construction-mode
   ruling cited by path. Items 6 to 9 and 11 to 14 each gained one line,
   `**Answered:** see Answers (2026-11-24).`; item 10 gained
   `**HQ's to settle:** see Answers (2026-11-24); not settled yet.` No Open item's text changed.
2. **Resolved counts.** Each conditional count stays, with the resolved count appended:
   - "Phase C is done when", the lanes (lines 4902-4905): "; D-81 (4) A and (5) A: all 12".
   - "Phase C is done when", the revoke (lines 4906-4907): "; D-81 (4) A: 13".
   - "Proposed batches and order", last paragraph (lines 4935-4936): "; D-81 (4) A: 13".
   - "Consequences", first bullet (lines 4948-4950): "D-81 (4) A and (5) A: no lane keeps its
     token."
   - "Consequences", reach (lines 4951-4952): "; D-81 (4) A: 18".
   - "Consequences", the revoke (lines 4953-4954): "; D-81 (4) A: 13".

   The Answers subsection also states the consequence once: all 12 lanes move, the sitting retires
   13 tokens, and the append password's grant joins the other 17 as a gate step for Devon, named
   and not given. I left the conditional counts in decision items 1, 5 and 9 (lines 4645-4647,
   4818-4822, 4869-4871) untouched, since requirement 1 forbids rewriting the Open items and items
   1 and 5 are covered by the Answers subsection.
3. **Unlock tier and broker token bullets** (lines 4908-4909): one line each. "D-81 (1) A: the
   tier is named, with no mechanism, and each use stays Devon's browser act." and "D-81 (2) B: the
   row becomes `rotated_by_sds = true` when the `bws-lane-token` adapter route is built."
4. **Factory-events passwords bullet** (line 4914): "D-81 (7) B removes them from Phase C's
   close."
5. **Status.** Unchanged: line 4594's `Proposed (2026-11-24)` and the heading's date stand.
6. **Line numbers.** Every line number in this report is at `3675d24f`, and I read each against
   the file at that commit: Status 4594-4595; Open items 6 (4852), 7 (4855), 8 (4860), 9 (4863),
   10 (4872), 11 (4875), 12 (4877), 13 (4882), 14 (4885), item 15 ending at 4895; "Phase C is done
   when" 4897 with bullets 4902-4914; batches paragraph 4929-4936; "What it grants" 4938-4944;
   "Consequences" 4946-4955. The text added to the ADR cites no line number.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_309`, migrated with `alembic upgrade head`: exit 0,
  14776 passed, 1 skipped (docs only, run as the handoff asked).
- Token-shape grep of the diff (`0.` + UUID + `.` pattern, and an inline `BWS_ACCESS_TOKEN=`
  assignment): 0 matches each.

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`
- `docs/superpowers/reports/broker-phase-c-d81-answers-report.md` (this report)

## PR

Recorded in the PR body and HQ's PR read; the URL and head are in the section "PR, filled after
opening" at the end of this report.

## Self-review

- The answer table is the handoff's table, unchanged. The Answers prose adds no decision beyond
  the handoff's: the "18 gate steps" line follows from (4) A and the amendment's item 3.
- The attribution stays split: items 6 to 9, 12 and 14 are Devon's (D-81); items 10, 11 and 13 are
  HQ's.
- I didn't name a PR number for `hq/broker-registration-allowlist`.

## Concerns

- **"Open, for Devon" text versus HQ's settled items.** Items 10, 11 and 13 are headed "Open, for
  Devon", the status line (4594-4595) says those items "stay open until Devon answers them", and
  "What it grants" (line 4942) says "Each Open item stays Devon's decision". The handoff has HQ
  settle 11 and 13 itself under build-mode rule 2, which I recorded as HQ's. I didn't treat this as
  a stop point: it isn't an answer contradicting the text, it's HQ's own instruction, and
  requirement 1 forbids rewriting the Open items. HQ may want a later edit to those three
  sentences.
- **"What it grants" (lines 4941-4942)** still says the append password "isn't a gate step until
  Devon answers Open item 9". The Answers subsection records that it now is; I didn't edit that
  sentence, since the requirements name only the done-when and Consequences counts.

## Standards followed

- **Code Brain 3 (structured JSON logs), 4 (never swallow an exception):** no code changes, so
  neither applies.
- **Code Brain 5 (never log secrets):** the change writes no secret value; it names secrets by
  registry name only (`factory-events-append-password`, `bws-bump-proposer-token`).
- **Infra Brain 591, 592, 593 (no BWS token, token-shaped string, or inline bootstrap token in a
  tracked file or history):** the diff's token-shape grep found 0 matches, and no inline
  `BWS_ACCESS_TOKEN=` assignment.
- **Infra Brain 1032 `cred.exposure-rotate`:** no exposure is declared or changed.
- **Infra Brain 212 (HTTPS for public FQDNs):** no endpoint; not applicable.
- **Construction-mode ruling:** cited by path
  (`~/docs/software-delivery-system/2026-07-30-construction-mode-ruling.md`), not restated.
