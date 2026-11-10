Outcome: done

# Report: adr-0059-amendment-3-item-1-done

- PR: #650 (https://github.com/AlobarQuest/orchestrator/pull/650), not a draft, not merged.
- Content commit: `63b160a9a319c54deb30c0312c2a58454239a854`, on `origin/main` `6d32e881`.
- Head: the report commit that adds this file, on top of the content commit.

## What I built

Docs only, in `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md`,
Amendment 3. The line numbers are in the new file:

- Line 2066 (Consequences, the risk line at 2064-2065): one added line, "That item is done
  (G-24, 2026-11-10): the forks have left the installation."
- Lines 2088-2090 (Open before building, item 1, after "remove them."): the **Done (G-24,
  2026-11-10)** line as the handoff worded it. It names the installation's list as
  `AlobarQuest/intent-packages` alone and says the record is Devon's reply "G-24: done", because the
  `gh` login can't read the list (HTTP 403).

I checked the rest of Amendment 3 for text that says the forks are still installed. The table row
at line 1969 says only that "the forks' place in the install is the first 'Open before building'
item". That stays true, so I left it alone. I didn't edit the ADR body, including line 1376, or any
other amendment.

## Tests and checks

- `make check` against `orchestrator_test_hq_w_72` (migrated to head, `PGTZ=UTC`, fixture
  `SECURITY_STANDARDS_DIR`): rc=0, 14207 passed, 1 skipped.
- Security scan from the repo root (`--category security`): 0 BLOCK, 2 WARN, 1 INFO. The WARN and
  INFO findings are existing repo-level judgment items, not from this diff.

## Files changed

- `docs/decisions/0059-scheduled-lanes-run-as-pinned-containers-off-the-mac.md` (+4 lines)
- `docs/superpowers/reports/adr-0059-amendment-3-item-1-done-report.md` (this report)

## Self-review

The diff adds four lines and changes no existing text. The new lines shift every later line in the
file by one at 2066 and by four after 2090. No reference to a line number past 2064 exists inside
Amendment 3, and `make check` passed with the shifted file.

## Concerns

None. The test database `orchestrator_test_hq_w_72` is left for HQ's teardown with the worktree.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout): docs only, so it doesn't apply.
- Code Brain rule 4 (never swallow an exception silently): docs only, so it doesn't apply.
- Code Brain rule 5 (never log secrets): the diff names no secret value. It names only the public
  installation id `180257673` and a repository name.
- Infra Brain 591, 592, 593: no BWS token is in the diff or the commits, and no inline
  `BWS_ACCESS_TOKEN` value. The security scan reports 0 BLOCK.
- Infra Brain 1032 (exposure means rotate): I saw no secret value during the task.
- Infra Brain 212 (HTTPS for public FQDNs): docs only, so it doesn't apply.
- Infra Brain 2890 (never commit a ping URL): the diff has no ping URL.
