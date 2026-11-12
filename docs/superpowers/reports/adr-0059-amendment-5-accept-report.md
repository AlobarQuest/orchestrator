Outcome: done

# Report: adr-0059-amendment-5-accept

- **PR:** #674 (ready for review, not merged), branch `hq/adr-0059-amendment-5-accept`, based on
  `origin/main` at `e94ffa2a`.
- **Content commit:** `be13634d`. The head is the commit that adds this report, on top of
  `be13634d`.

## What I built

These edits are all in Amendment 5 of
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`:

1. Status line (old line 2426, new lines 2426-2428): it reads "Accepted, 2026-11-12. Devon accepted
   this text on gate G-38, in an interactive HQ session on 2026-11-12. Acceptance followed a clean
   Opus whole-branch review of PR #670 at `24c43faf` (HQ D-12, limit 3). The ADR's own status is
   unchanged." It follows Amendment 4's form and has no correction history.
2. Old line 2591, new line 2593: "They change in plan step 3e-6 (`lanes-inc-3-membership-check`),
   the lane's increment 3 move:" now reads "They change in the membership check's increment 3
   move:". I dropped the branch-style name because the sentence reads clearly without it.
3. Old lines 2637-2638, new lines 2639-2640: "Resolved by plan step 3e-6, which also adds…" now reads
   "Resolved by the membership check's increment 3 move, which also adds…".
4. I confirmed that Amendment 5 has no "once it's accepted" wording. The only such phrase in the ADR
   is at line 1906, in Amendment 3, which is out of scope, so I left it.
5. "Open before building" items 1 to 3 stay open and unchanged. I didn't edit the ADR body, its top
   Status line, or any other amendment. The PR body lists the Opus review's other notes (the
   Context's "exit 3", "Relates to", the smart-HTTP sentence, and the launcher's truncating `>`) as
   follow-ups for the lane-files change.

## Tests and checks

- `make check`, run against `orchestrator_test_hq_w_106` with `PGTZ=UTC` and the fixture
  `SECURITY_STANDARDS_DIR`: rc 0. 14644 passed, 1 skipped, ruff clean, and pyright reported 0 errors.
- Security scan from the repo root (`--category security`): 0 BLOCK. The 2 WARN findings
  (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`) and 1 INFO finding
  (`bws.least-privilege-scope`) are repo-level and this docs diff doesn't touch them.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (+5 −4).
- `docs/superpowers/reports/adr-0059-amendment-5-accept-report.md` (this report).

## Self-review

- The diff touches only Amendment 5's Status line and the two plan-step references.
- The rewording keeps each sentence's meaning: the same files change in the same move, and the
  same move resolves open item 2.
- No other file in the repo references Amendment 5's status.

## Concerns

None.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply. The diff is docs only.
- **Code Brain rule 4 (never swallow an exception silently):** doesn't apply. The diff is docs only.
- **Code Brain rule 5 (never log secrets):** the diff and this report name no secret value.
- **Infra Brain 591, 592, 593:** the diff adds no BWS token and no inline `BWS_ACCESS_TOKEN` value.
  The scan reports 0 BLOCK findings, covering tracked files and history.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value during the work.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The diff is docs only.
- **Infra Brain 2890 (never commit a ping URL):** the diff adds no ping URL.
