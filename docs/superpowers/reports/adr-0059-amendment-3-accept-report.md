Outcome: done

# Report: adr-0059-amendment-3-accept

Worker W-69. Records Devon's acceptance of ADR-0059 Amendment 3 (gate G-22) on draft PR #648.

## What I built

- Content commit `730f4c7c`, on top of the PR head `01ca2782` (fast-forward; the worktree branch
  was moved to `01ca2782` with `git merge --ff-only`, because `git reset --hard` was denied).
- The report commit follows it on the PR's branch, `hq/adr-0059-amendment-3-forks-stay`. The new
  head is the report commit, pushed fast-forward; the PR stays a draft.

In `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`, Amendment 3 only:

- Status line (lines 1877-1881 at `730f4c7c`): "Accepted, 2026-11-10", quoting Devon's reply
  "G-22: accept 1. yes 2. No 3. No", the Slack send and the interactive restatement at about
  15:40 UTC as the record, and the clean Opus whole-branch review of #648 at `01ca2782` (HQ D-12,
  limit 3). The D-35 quotation that followed is kept.
- "Open before building", each question text kept, an answer appended:
  - Item 1 (lines 2084-2086): yes; the forks leave the "SDS lanes" App's installation
    (`180257673`), and removing them is a later act this amendment doesn't do.
  - Item 2 (line 2091): no; the read-only App keeps its Issues read permission.
  - Item 3 (lines 2095-2096): no; neither fork lane gets a narrower Mac credential than Devon's
    `gh` login.
  - Item 4 is not one of G-22's questions and is unchanged.

The ADR body and the other amendments are untouched.

PR body: a line under its opening summary that Amendment 3 is Accepted (G-22), with items 1 to 3
answered. The "Review follow-ups" section is kept.

## Tests and checks

- `make check` with `TEST_DATABASE_URL` at `orchestrator_test_hq_w_69`: exit 0, 14207 passed,
  1 skipped.
- Security scan from the repo root (`security_scan.cli . --category security`): 0 BLOCK, 2 WARN,
  1 INFO. The WARNs (gitignore pattern coverage, manifest drift) predate this change.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (+11, -1)
- `docs/superpowers/reports/adr-0059-amendment-3-accept-report.md` (this file)

## Self-review

- The diff is four hunks, all inside Amendment 3; `git diff 01ca2782 730f4c7c` shows no other
  file or section.
- The status bullet still carries D-35's sentence "The amendment takes effect when Devon accepts
  this text; HQ doesn't merge it." I left it, because the handoff asks for a status edit, not a
  rewrite of the decision record; with acceptance recorded it reads as history.

## Concerns

None.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout): doesn't apply; docs only.
- Code Brain rule 4 (never swallow an exception silently): doesn't apply; docs only.
- Code Brain rule 5 (never log secrets): the diff names no secret value, only the installation id
  `180257673`, which is not a secret.
- Infra Brain 591, 592, 593: no BWS token in the diff or history; no inline `BWS_ACCESS_TOKEN`
  value. The security scan reports 0 BLOCK.
- Infra Brain 1032 (exposure means rotate): I saw no secret value anywhere.
- Infra Brain 212 (HTTPS for public FQDNs): doesn't apply; docs only.
- Infra Brain 2890 (never commit a ping URL): the diff contains no ping URL.
