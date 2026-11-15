Outcome: done

# Report: cred-consumers-nightly-comment

## What I built

I rewrote the comment block before `bws-bump-proposer-token` in `.cred-consumers.toml` so that it's true:

- Removed "after these rows land; no `created` or fingerprint until then, so neither ages before it exists". Step 0 has run, so "until then" had lapsed.
- Added "No `created` or fingerprint: a fingerprint needs a credentialed read, which stays with an interactive session." This matches the wording in security-standards' own registry.
- Replaced "security-standards keeps no .cred-consumers.toml (FE-5 plan follow-up 1)" with "its row lives in security-standards' .cred-consumers.toml (security-standards PR #96)".
- Kept the rest of the block: minted by Devon at FE-5 act step 0, console-minted and hand-rotated, D-54 A on 2026-11-15, the BWS keeper copies in Rotation / Keepers, the removed sds-operator grants, and the nightly token's row id and UUID.

The block matched the handoff's description. No other comment in the file says security-standards keeps no registry: I searched the file and the repo's `.toml` and `.md` files for "keeps no".

- PR: https://github.com/AlobarQuest/orchestrator/pull/720 (open, not merged)
- Branch: `hq/cred-consumers-nightly-comment`
- Fix commit: `b606c50e451f93f4bf9c0493aa4b72de3d5e62f8`. This report goes in a later commit on the same branch.

## Tests and checks run

- `make check` (test DB `orchestrator_test_hq_w_179`, `PGTZ=UTC`): exit 0. Lint passed, pyright reported 0 errors, and pytest collected 15000 items (10 workers): 14999 passed, 1 skipped.
- `tomllib.load(open(".cred-consumers.toml","rb"))` through the venv's Python: the file parses. The system `python3` has no `tomllib`, so I used `.venv/bin/python`.

## Files changed

- `.cred-consumers.toml`: comment lines only (7 lines replaced by 7 lines). No key or value changed.
- `docs/superpowers/reports/cred-consumers-nightly-comment-report.md`: this report.

## Self-review

The diff touches only `#` lines in one block. I read security-standards `origin/main:.cred-consumers.toml` to confirm that the row `bws-factory-events-nightly-token` with UUID `4c1bcf0e-46f6-4ac3-9d3f-b50300d50015` exists there. That file's header says its sibling rows live here, so the two registries now point at each other. I didn't run `/code-review`, because the change is a comment-only edit to a data file.

## Concerns

None.

## Standards followed

- Rule 5 (BLOCK, security, never log secrets): no token value or fingerprint appears in the file, commit, PR body, or this report. Tokens are named by row id, Keychain item, and UUID only.
- Rule 3 (BLOCK, structured JSON logs) and rule 4 (BLOCK, never swallow an exception) don't apply, because the change is a comment only and adds no code.
- BWS standard (secrets by UUID, never a token in any file): the comment names the keeper by UUID only and contains no token.
