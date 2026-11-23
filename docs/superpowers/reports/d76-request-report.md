Outcome: done

# D-76 request: the rotation decomposer's lane-token reissue

## What I built

- `.cred-consumers.toml`, row `id = "rotation-decomposer-bws-token"`: `rotate_requested` changed
  from `"2026-11-13"` to `"2026-11-23"`. Nothing else on the row changed. The row was where the
  handoff said, carried `2026-11-13`, and has no `exposure` entry.
- The block comment above the row gained one sentence: the token's row is dated 2026-11-23 for its
  first reissue (D-76 B, revision 2, approved by Devon by name), and the ping URL's row keeps its
  date because a ping URL can't be reissued, only replaced.
- `rotation-decomposer-ping-url` is unchanged (`rotate_requested = "2026-11-13"`).
- `tests/rotation_worker/test_rotation_decomposer_rows.py` line 129 now pins `"2026-11-23"` for the
  token; line 130 keeps `"2026-11-13"` for the ping URL. `git grep -n 'rotation-decomposer' tests`
  found no other test or fixture pinning the token row's date. The one other hit on the token id,
  `tests/rotation_worker/test_work_carrier_rows.py:41`, lists it by id only.

### Which last-rotation date the new date must beat

The repository doesn't make the "later than the last rotation" comparison.
`src/rotation_proposer/findings.py` (module docstring, lines 3-10) says infraops' `credFindings`
(`src/security-drift/cred-rotation.ts`) owns the rule: a `rotate_requested` date later than the
last rotation is due. The proposer only reads the result. When infraops reports the
`cred.rotation-requested` check, `_due` (around line 130) uses `rotate_requested` as both the
basis and the date. infraops checks the requested date against its rotation state. That state is
the per-credential `last_rotated` map served in the same `cred-findings` answer and parsed by
`_last_rotated` (line 141), and it moves only through `security-drift-cli record-rotation`.

I can't read infraops' live state from this session, and the handoff forbids infraops calls. I
didn't confirm the live `last_rotated` value for `rotation-decomposer-bws-token`. The token's
first issue was requested on 2026-11-13, so a recorded issue is dated no earlier than that. It
can't be later than today, 2026-11-23. If infraops recorded the first issue on
2026-11-23 itself, the request isn't strictly later, and no finding comes. HQ can rule that out
when it sees `cred.rotation-requested` raised after the merge.

## Tests and checks

- `make check` against my own database `orchestrator_test_hq_w_283` (`TEST_DATABASE_URL` and
  `ORCHESTRATOR_DATABASE_URL` at it, `SECURITY_STANDARDS_DIR` at the fixture, `PGTZ=UTC`, migrated
  to head), with the worktree's `.venv`: exit 0. Lint and format reported "All checks passed!",
  pyright reported 0 errors, and pytest reported **14768 passed, 1 skipped** over 10 xdist workers.
- `tests/rotation_worker/test_rotation_decomposer_rows.py` on its own: 19 passed.
- `code-standards risk --repo .`: `normal`, so `vendor-review` didn't apply.
- `/code-review` (low) on `origin/main...HEAD`: no findings.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_rotation_decomposer_rows.py`
- `docs/superpowers/reports/d76-request-report.md` (this report)

## Self-review

The diff changes one value and adds one comment sentence in the registry, and changes one test
assertion. The test `test_both_rows_are_due` keeps its name and docstring. Both rows are still
dated, and the docstring explains the first issue's ordering, which still holds.

## Concerns

- The strict-later check runs in infraops, and I didn't read it. See "Which last-rotation date the
  new date must beat".
- I created the test database `orchestrator_test_hq_w_283` (and its `_gwN` worker databases).
  HQ's teardown drops them with the worktree.

## Standards followed

- Code Brain 3 (structured logs to stdout): no logging changes.
- Code Brain 4 (never silently swallow an exception): no exception handling changes.
- Code Brain 5 (never log secrets): no logging, and I read and printed no secret value.
- Infra Brain 591 `bws.no-token-in-tracked-files`: the diff adds a date and a comment. The UUIDs on
  the row were already there and are ids, not tokens.
- Infra Brain 592 `bws.no-token-in-git-history`: the commit adds no token.
- Infra Brain 593 `bws.bootstrap-token-not-inline`: no token is inlined anywhere.
- Infra Brain 1032 `cred.exposure-rotate`: this PR declares no exposure (D-48 B chose not to
  record one). The reissue runs by `rotate_requested`.
- Infra Brain 212 (HTTPS): doesn't apply, because no endpoint changes.
