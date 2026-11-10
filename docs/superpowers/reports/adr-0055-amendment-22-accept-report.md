Outcome: done

# Report: ADR-0055 amendment 22 accepted (G-23)

Worker W-70. Branch `hq/adr-0055-amendment-22-lane-credential-standing-approval` (draft PR #647),
started at `d21a43cc`.

## What I built

Docs only. The content commit `a31e514d` records Devon's acceptance of amendment 22 in
`docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md`:

- Line 33 (the header's "Amended" list): amendment 22's entry reads "accepted" instead of
  "proposed", matching how amendment 19's entry records acceptance. This is the one edit outside
  amendment 22's own section; leaving it "proposed" would contradict the amendment's status line.
- Lines 2658-2660: amendment 22's Status is "Accepted (2026-11-10)", quoting Devon's Slack reply
  ("G-23: accept", recorded 2026-11-10 15:51 UTC) and naming the bar: a clean Opus whole-branch
  review of PR #647 at `d21a43cc` (HQ D-12 standing answer, limit 3). The D-34 quote follows
  unchanged.
- Lines 2956-2957: under "Open before building", "A revision 1 that is in effect a reissue" gains
  one line: Devon accepted without widening it (G-23), so the default stands and the standing
  answer covers only the issue plan. The item's question text and the other open items are
  unchanged.

The PR body gains a line at the top of its summary: amendment 22 is Accepted (G-23), the reissue
item is at its default, and the summary predates fix round 1, so the amendment text is the
reference. The follow-up sections are kept. The PR stays a draft.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_70`: exit 0; ruff "All checks passed!", pyright
  0 errors, pytest 14207 passed, 1 skipped.
- Security scan from the repo root (`--category security`): 0 BLOCK, 2 WARN, 1 INFO (the
  repository's standing judgment and coverage findings; none touches this diff).

## Files changed

- `docs/decisions/0055-credential-rotation-runs-in-a-pull-based-executor.md` (+6, -3).
- `docs/superpowers/reports/adr-0055-amendment-22-accept-report.md` (this report).

## Self-review

The diff changes no decision content: no condition, increment, or other amendment moves. The
handoff's `git reset --hard d21a43cc` was denied by the permission layer, so I used
`git switch -C hq/adr-0055-amendment-22-accept d21a43cc` on the clean tree, which has the same
effect; `git log -1` showed `d21a43cc` before editing.

## Concerns

- The header list edit (line 33) is outside amendment 22's section; the handoff said "amendment 22
  only". I made it because the entry is amendment 22's own status pointer. HQ can drop it if it
  reads that differently.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout): doesn't apply; the change is docs only.
- Code Brain rule 4 (never swallow an exception silently): doesn't apply; the change is docs only.
- Code Brain rule 5 (never log secrets): the diff and this report name no secret value.
- Infra Brain 591, 592, 593: no BWS token in any tracked file or commit, and no inline
  `BWS_ACCESS_TOKEN` value; the security scan reports 0 BLOCK.
- Infra Brain 1032 (exposure means rotate): I saw no secret value while working.
- Infra Brain 212 (HTTPS for public FQDNs): doesn't apply; the change is docs only.
- Infra Brain 2890 (never commit a ping URL): the diff and this report contain no ping URL.
