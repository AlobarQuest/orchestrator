Outcome: done

# Report: ping-url-increment-3-docs (W-79)

- PR: #652 (draft), https://github.com/AlobarQuest/orchestrator/pull/652
- Branch: `hq/ping-url-increment-3-docs`, based on `2a7fb611c22237009ba0a94335ec852786311eae`
- Head SHA: `39253f402e0546cc632d0f85ca3244f7ab640639`

## What I built

Text-only changes recording ADR-0055 amendment 23 increment 3 as done (HQ gate G-26, 2026-11-10).
Every line the handoff named said what the handoff said at `2a7fb611` before I edited it.

## Diff stat against `2a7fb611`

```
 .bws-secrets.toml                                            |  4 +++-
 ...0055-credential-rotation-runs-in-a-pull-based-executor.md |  4 ++++
 docs/operations/rotation-executor.md                         | 12 ++++++------
 3 files changed, 13 insertions(+), 7 deletions(-)
```

## Lines changed per file

- `docs/operations/rotation-executor.md` (6 lines changed):
  - line 379: the comment is `# set at increment 3 (2026-11-10)`;
  - lines 406-409: the "It isn't configured until … increment 3 …" sentence is replaced by "ADR-0055
    amendment 23's increment 3 configured it on the VM on 2026-11-10, after a person granted the
    root its read." The preceding rule (no id configured, every ping-URL unit refused before the
    claim) is unchanged;
  - line 441: "(pending increment 3)" dropped;
  - line 445: "; pending increment 3" dropped.
  The file's other `increment 3` mark (line 182, amendment 15's increment 3c) is about another
  increment and is unchanged.
- `.bws-secrets.toml` (comment only, 1 line changed, 2 added): the comment above `51509d5e-…`
  ends with a sentence naming the VM rotation executor's root (`rotation-executor`) as a reader
  through a direct read grant on this one secret, only while a ping-URL unit is about to be claimed
  or runs (ADR-0055 amendment 23). No `[[secret]]` field changed.
- `docs/decisions/0055-…md` (4 lines added after "The key's registry rows"): increment 3 found
  (2026-11-10) that neither key (`51509d5e-…`, `f07b1368-…`) has a `.cred-consumers.toml` row in any
  file `~/.config/infra-drift/cred-consumers.list` names (infraops-mcp-server, change-manager,
  factory-runner, orchestrator), so it added the executor to `.bws-secrets.toml`'s reader comment
  only; whether the keys get registry rows is open. I confirmed the absence by grepping the four
  listed files for both UUIDs: no match.

## Other pages (change 4)

I grepped `docs/` (excluding `docs/superpowers/reports/`) for `51509d5e` and
`healthchecks_management_key_id`. ADR-0059 (lines 42, 678, 1314, 1503),
`docs/superpowers/plans/2026-10-27-bridge-p1-findings-and-edges.md` (line 63) and ADR-0055 lines
3063, 3197 and 3223 mention the key but state nothing as pending increment 3. ADR-0055 line 3232
("done before increment 3 reads the key") describes the order of the grant and is still true. No
other page changed.

## Tests and checks

`make check` against `orchestrator_test_hq_w_79` (migrated to head, `PGTZ=UTC`,
`SECURITY_STANDARDS_DIR` at the fixture): exit 0, lint "All checks passed!", tests
**14252 passed, 1 skipped** (14253 collected), 11 warnings, in 349.57 s. No test pins the changed
bytes.

## Self-review

The diff touches prose, a table's cells and TOML comments only. No secret value, ping URL or token
appears in it. I added no `.cred-consumers.toml` row or credential class (left open as 3e-pua-rows).

## Concerns

None. Cleanup left for HQ after merge: the worktree, branch and test database
`orchestrator_test_hq_w_79`.

## Standards followed

- Code Brain rule 3 (structured JSON logs): not touched; no code changed.
- Code Brain rule 4 (never swallow an exception): not touched; no code changed.
- Code Brain rule 5 (never log secrets): no secret value written anywhere, in the diff, commit, PR
  body or this report.
- Infra Brain 591, 592, 593 (no BWS token in a tracked file or history, no inline
  `BWS_ACCESS_TOKEN`): none added; secrets are named by UUID only, both already in
  `.bws-secrets.toml`.
- Infra Brain 1032 (a seen secret must be rotated): I saw no secret value in this session; nothing
  to rotate.
- Infra Brain 212 (HTTPS for public FQDNs): not touched.
- Infra Brain 2890 (never commit a ping URL): no ping URL in the diff.
