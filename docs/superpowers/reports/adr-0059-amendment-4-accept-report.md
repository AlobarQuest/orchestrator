Outcome: done

# Report: adr-0059-amendment-4-accept

- **PR:** #662 (https://github.com/AlobarQuest/orchestrator/pull/662), ready for review, not merged.
- **Branch:** `hq/adr-0059-amendment-4-accept`, based on `origin/main` at `29f97472`.
- **Content commit:** `ca337cd8`.
- **Head:** the commit that adds this report, on top of `ca337cd8`.

## What I built

A docs-only change to Amendment 4 of
`docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`:

1. Amendment 4's Status line reads "Accepted, 2026-11-11", in Amendment 3's form: Devon accepted
   the text on gate G-34, replying "accept" from Slack on 2026-11-11; acceptance followed a clean
   Opus whole-branch review of PR #659 at `30b0e2e2` (HQ D-12, limit 3). The D-33 question,
   Devon's answer "C", the 2026-11-09 standing-answer quote, and "the ADR's own status is
   unchanged" are kept. The two sentences saying the amendment becomes Accepted later and is
   merged as Proposed are removed.
2. "this amendment governs once it's accepted" now reads "this amendment governs".

## Diff line numbers

Against `29f97472` (old → new):

- Old line 2135 → new lines 2135-2137: the Status line's opening.
- Old lines 2147-2148 → new line 2149: the Proposed-merge sentences removed.
- Old line 2157 → new line 2158: "governs once it's accepted" → "governs".

## Tests and checks

- `make check` with `TEST_DATABASE_URL` and `ORCHESTRATOR_DATABASE_URL` at
  `orchestrator_test_hq_w_89` (migrated to head first), `SECURITY_STANDARDS_DIR` at the fixture,
  and `PGTZ=UTC`: exit 0, **14416 passed, 1 skipped**, "All checks passed!".
- Security scan (`security_scan.cli . --category security`) from the repo root: 0 BLOCK, 2 WARN
  (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`), 1 INFO
  (`bws.least-privilege-scope`). These are repo-level findings that a one-file ADR edit can't
  affect.
- I checked that no test, manifest, or operations file pins this ADR's text or hash. A grep of
  `src`, `tests`, `scripts`, and `docs/operations` for the ADR's file name returns nothing.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (Amendment 4 only).
- `docs/superpowers/reports/adr-0059-amendment-4-accept-report.md` (this report).

## Self-review

- The phrase "amendment governs once it's accepted" also appears in Amendment 3 (line 1906). I
  scoped the replacement to the text after Amendment 4's heading, so Amendment 3 is unchanged. The
  diff shows three hunks, all inside Amendment 4.
- "Open before building" items 1 to 4 are unchanged, and so are the ADR body, its top Status
  line, and the other amendments.
- The Status line states the rule as it stands, with no correction history.
- The decision's content is unchanged.

## Concerns

None.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply. The change is docs only
  and adds no logging.
- **Code Brain rule 4 (never swallow an exception silently):** doesn't apply. The change is docs
  only and adds no code.
- **Code Brain rule 5 (never log secrets):** the diff and this report name no secret value.
- **Infra Brain 591, 592, 593 (no BWS token in tracked files or history; no inline
  `BWS_ACCESS_TOKEN` value):** the diff adds no token and no `BWS_ACCESS_TOKEN` assignment. The
  security scan reports 0 BLOCK findings.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value during this task, so there
  is nothing to report.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The change is docs only and adds no
  public FQDN.
- **Infra Brain 2890 (never commit a ping URL):** the diff adds no ping URL.
