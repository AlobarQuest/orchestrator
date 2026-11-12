Outcome: done

# Report: adr-0059-membership-packages, fix round 1 (PR #670)

- Fix commits on `hq/adr-0059-membership-packages`, fast-forward on `b230eda9`:
  `c40cc5a3d44d01496dd99097da4bf1539407cdb0` and `1afb0bceac0164774d1bfefab0a84e09c577e36b`.
- New head with the fixes: `1afb0bceac0164774d1bfefab0a84e09c577e36b`. This report follows in a
  separate commit that touches only `docs/superpowers/reports/`.
- Before the first commit and again before the push, `origin/hq/adr-0059-membership-packages` was
  `b230eda9`, with no later commit.

## What I changed

All ADR edits are in amendment 5 (it starts at line 2424). No line before it changed.

1. **Finding 1, the credential rule's inversion.**
   - In the Context paragraph (former line 2464), the sentence now says that decision 2 requires
     a credential on decision 1's per-pass clones whether or not the repository is private, and
     names one exception, infraops-mcp-server (lines 307-310 and 541-543). It then says that this
     amendment adds a second exception, the membership check's clone of intent-packages, for the
     membership check only. The bump driver's clone of intent-packages still needs a credential.
   - The "Decided by" line (former line 2427) now says HQ adds a second exception to decision 2's
     rule at lines 541-543. It no longer says that decision 1's clone bullet already allows the
     clone.
   - In decision item 1's "What it isn't" bullet (former line 2508), "a public clone needs no
     credential, as for infraops-mcp-server" restated the same inverted rule. It now says that
     this clone is decision 2's second exception (lines 541-543), beside infraops-mcp-server. The
     handoff didn't name this line. It's the same finding, inside amendment 5, so I fixed it in a
     second commit, `1afb0bce`.
   - The table row for lines 541-543 ("Extended by a second exception") is unchanged.
2. **Finding 2, the mint and install rows.**
   - The row for lines 603-613 now says that intent-packages is a member
     (`docs/operations/membership-seed.toml`, lines 125-127). The membership check reads it like
     every observed member: through the minted token in the container, and through Devon's login
     on the Mac (`cli.py` lines 166, 177 and 192 at `a6442254`: `_declarations`,
     `_registry_files` and `_auto_merge`). Only the standing-package read goes through the
     anonymous clone, and only that read is outside the mint.
   - The row for lines 620-622 now says that the lane's member reads of intent-packages depend on
     the read-only App's installation there (Q9, HQ D-17). Only the standing-package read doesn't.
   - In the base report, the mint row on line 34 now says the same thing.

## Checks

- `make check` against `orchestrator_test_hq_w_102` (migrated to head, `PGTZ=UTC`,
  `SECURITY_STANDARDS_DIR` set to the fixture), run at `1afb0bce` before the push: exit 0. Lint
  and type checks were clean, with 14622 tests collected, 14621 passed and 1 skipped. A run at
  `c40cc5a3` had the same result.
- `git diff -U0 b230eda9 -- docs/decisions` shows hunks only at lines 2427, 2466, 2508 and 2541,
  all inside amendment 5.

Diff stat against `b230eda9` at `1afb0bce`:

```
 ...led-lanes-run-as-pinned-containers-off-the-mac.md | 20 +++++++++++++-------
 .../reports/adr-0059-membership-packages-report.md   |  2 +-
 2 files changed, 14 insertions(+), 8 deletions(-)
```

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (amendment 5 only)
- `docs/superpowers/reports/adr-0059-membership-packages-report.md` (line 34)
- `docs/superpowers/reports/adr-0059-membership-packages-fix-1-report.md` (this report)

## Self-review

- I searched all of amendment 5 and the base report for `mint`, `install`, `clone bullet`,
  `no credential`, `Devon's login` and `only through`. The only other sentence that restated
  either claim was the one at former line 2508, which I fixed. The other hits are accurate. Line
  2526 rejects the minted token on the clone, and the Consequences sentence (line 2619) says the
  amendment adds nothing beyond what decision 2 grants. Both remain true with the second exception
  stated as this amendment's addition.
- The base report's line 35 (install set) says "the clone adds nothing to it", which is true. I
  left it unchanged.

## Concerns

- The Consequences bullet at line 2626 says "(the App is already installed there)" about
  intent-packages. The base report's lines 121-122 say that no source the first round read
  confirms the installation. My finding 2 rows rely on Q9 (HQ D-17), the decision, not on an
  observed installation. I didn't change line 2626, because the handoff says it holds. HQ needs to
  decide whether to verify the installation on intent-packages before increment 3.
- I didn't touch the review's listed follow-ups.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** doesn't apply. The change is ADR prose
  only.
- **Code Brain rule 4 (never swallow an exception silently):** the amendment's loud-failure text
  is unchanged. That includes the Consequences bullet (lines 2624-2627), decision item 5's
  gap-on-a-failed-clone text, and the launcher test rule "exits 3 with the gap, never 1".
- **Code Brain rule 5 (never log secrets):** I added no secret identifier or value. The existing
  table names `lanes-read-app-private-key` by its id prefix only.
- **Infra Brain 591, 592 and 593:** I wrote no BWS token, no inline `BWS_ACCESS_TOKEN` value and
  no placeholder that looks like a token. The repository's Stop-hook scanner runs at session end.
- **Infra Brain 1032 (exposure means rotate):** I saw no secret value during this round.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The change is documentation only.
