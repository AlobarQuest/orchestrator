Outcome: done

# FE-5: D-53 C (no retry after the import) recorded in ADR-0055 amendment 28 and the cut-over plan

Handoff: `sds-hq/state/handoffs/fe-5-record-d53.md`. Worker W-169. Branch `hq/fe-5-record-d53`,
base `origin/main` `fd468cad`. PR #708, open and not merged. The PR's content head is `27f1fefb`;
this report is committed on top of it. Docs only.

## What I built

ADR-0055 amendment 28 records Devon's D-53 C (2026-11-14, sds-hq `state/decisions.md` D-53 line,
commit `9d97574`), quoting his choice and his words. It withdraws amendment 27 item 1's
`fe-5-chain-reset` owner act for the post-import case, the only case the act covered. D-51 B's path
replaces it: FE-5 ends, the database keeps the imported copy, and the next try needs a new
amendment. The cut-over plan now agrees in every place that mentioned the act or a retry.

## Files and sections changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`:
  - **New amendment 28:** D-53 C, what it replaces in amendment 27, what's unchanged
    (a rollback before step 6 succeeds; amendment 27 items 2 and 3), consequences, and grants
    (none).
  - **Amendment 27, "Open before the act":** question 9 is answered by amendment 28. The
    question's earlier text stays as "the question as it stood".
  - **Amendment 27, item 1, and "What it grants":** each gets one pointer clause saying amendment
    28 withdraws the act. Item 1 also names the plan's replacement section. D-51 A's record is
    unchanged.
  - **Amendment 24, item 3 (line 3378) and its import open item (line 3542):** PR #706 added both
    pointer sentences. Each now ends "amendment 28 withdraws it (D-53 C)". These are pointer
    clauses, not term changes; see concern 1.
- `docs/superpowers/plans/2026-11-14-fe-5-cutover.md`:
  - **Intro:** adds that Devon answered question 9 (D-53 C) and that amendment 28 records it.
  - **Merge-order checks, the security-standards #94 bullet:** "The `fe-5-chain-reset` act rests on
    it" now says no FE-5 act rests on it, and it's the evidence a later amendment can use. I kept
    the check itself; see concern 2.
  - **RP row:** the retry-through-`fe-5-chain-reset` sentence is gone. FE-5 ends until a new
    amendment (D-53 C, D-51 B). Step 4's head and step 6's `<n>` are still recorded "when step 6
    succeeds", because they identify the copy the database holds, which a later amendment's plan
    must check before it touches the chain.
  - **"What rolls back after the rollback point":** the step 7, 8, 9 or RP bullet now says FE-5
    ends until a new ADR-0055 amendment plans the next try.
  - **"Reset the chain before a retry: `fe-5-chain-reset`":** this section is replaced by
    "No retry after the import". The new section covers the decision, why a reset isn't available,
    and the security-standards PR #94 (`237e422`) evidence.
  - **Open questions:** the intro now says no question is open. Question 2 says D-53 C supersedes
    D-51 A's retry for the post-import case. Question 9 is marked answered (D-53 C, 2026-11-14).
- `docs/superpowers/reports/fe-5-plan-decisions-report.md` (W-162's report): one
  "Superseded in part" note under the header. Line 1 and the body are unchanged.

## Every `fe-5-chain-reset` hit

I grepped `docs/`, `CLAUDE.md` and `scripts/`, and the following files had hits:

- **ADR-0055:** amendment 27 items 1 and "Open before the act", plus amendment 28. Each agrees.
- **The plan:** questions 2 and 9 name the act as withdrawn, and nothing else names it.
- **`docs/superpowers/reports/fe-5-plan-decisions-report.md`:** this is W-162's historical
  report. It's docs, so I added the superseded note and left the narrative as the record of what
  that PR did.
- **`CLAUDE.md` and `scripts/`:** no hits.

I also grepped for "retry", "reset", "re-import", "question 9" and "D-51" in both files. ADR-0059
holds no D-51 or `fe-5-chain-reset` text; its FE-5 edit from PR #706 is D-52 A only.

## Tests and checks

- **PR #708 CI at `27f1fefb`:** all pass, including Quality (10m28s), Rollout transcription
  currency and Runner consumer compatibility. The Quality job log shows `[15006 items]` collected
  and `14983 passed, 23 skipped, 5 warnings in 557.69s`.
- **Security scanner** (`security_scan.cli . --category security`, repo root): no BLOCK findings
  (2 WARN, 1 INFO, unrelated to this change).
- I didn't run `make check` locally or `/code-review`. The change is docs only, so I relied on CI's
  Quality job for the full suite and on an advisor review of the diff in their place.

## Self-review

- No FE-5 act step changes except the rollback text in RP and in "What rolls back".
- Amendment 27's record of D-51 A is intact; each change is a pointer to amendment 28.
- Devon's words are quoted verbatim from the D-53 line. The date is 2026-11-14 (the session's
  local date), and the record's timestamp is 2026-11-15T02:50 UTC.

## Concerns

1. **Pointer clauses in amendment 24.** The stop point bars changing ADR terms outside amendments
   27 and 28. Amendment 24's two lines were pointer sentences PR #706 added, saying amendment 27
   names an act. I appended "amendment 28 withdraws it" to each so they don't point at a live act
   that doesn't exist. No amendment 24 term changed. If HQ reads this as out of scope, revert those
   two clauses; amendment 28 item 2 still says how to read them.
2. **The #94 merge-order check stays.** That check no longer gates anything in FE-5. Removing it
   would change the act's preconditions, so I kept it (it's already met) and reworded its reason.
   HQ can drop it in a later edit.
3. **The ADR's "Amended" header list** stops at amendment 24, and 25 to 27 aren't listed. I didn't
   add 28, to keep to amendments 27 and 28 only.

## Standards followed

- **Code Brain rule 3 (structured logs to stdout):** doesn't apply, because there's no code.
- **Code Brain rule 4 (never silently swallow an exception):** doesn't apply, because there's no
  code.
- **Code Brain rule 5 (never log secrets):** neither file has a secret value, password or token
  shape. The owner password is named only by where it lives (Coolify). The scanner found no BLOCK.
- **Infra Brain security BLOCK rules (212, 591, 592, 593, 1032):** the no-secret-in-text rule holds
  as in rule 5. Amendment 28 adds no credential, role, account, grant or reader, and it declares
  nothing exposed.
- **Repo rules (root `CLAUDE.md`, ADR conventions):** I added the change as a new amendment and
  didn't rewrite amendment 27's record. Supersession is stated where the earlier text stands, and
  prose wraps at 100 columns (table rows excepted, as elsewhere in the plan).
