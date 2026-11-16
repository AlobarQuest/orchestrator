Outcome: done

# Membership check D-59 A: narrow the registry-file rule

PR: https://github.com/AlobarQuest/orchestrator/pull/728 (branch `hq/membership-check-d59-narrow`,
commit `55867b44` on `44180fe1`). Not merged.

## What I built

The new rule, in one sentence: a `.cred-consumers.toml` in a repository outside
`rotation_worker.REGISTRY_REPOSITORIES` is a disagreement only when one of its `[[credential]]` rows
has `rotated_by_sds = true`. A file whose rows hand nothing over passes.

- `src/membership_check/declaration.py`
  - `hands_over(text)` is a pure parser that returns a `Declaration`. It applies the same test as
    `RegistryEntry.rotated_by_sds`: `is True`.
    - `True`: any row has `rotated_by_sds = true`. This holds even when another row is malformed,
      because the `true` row alone is the finding.
    - `False`: every row has `false` or no flag, or the file has no rows. A missing `credential`
      key counts as no rows, which matches `rotation_worker.registry._entries`.
    - `None` (a gap): the file isn't TOML, `credential` isn't an array of tables, or a flag isn't a
      bool. A non-bool includes the string `"true"` and the integer `1`. The code says why: the
      author meant something by the value, and reading it as `False` would turn it into a pass.
  - `GitHubDeclarationSource.registry_hands_over(slug)` reads the file through the same
    `_contents` call that `holds_registry` uses. It decodes `response.content` strictly as UTF-8,
    as the rotation mirror does; a lenient decode could make a broken file parse. A new private
    `_registry` helper holds the non-2xx branch that both methods share.
- `src/membership_check/cli.py`: `_registry_files` reads a member that `REGISTRY_REPOSITORIES`
  names for presence only (`holds_registry`), as before, and keeps its gap. Every other non-retired
  member is read with `registry_hands_over`. The function returns only the outside repositories
  whose file hands over a credential.
- `src/membership_check/consistency.py`: the `_rotation_scope` message is
  `<repo>: its .cred-consumers.toml hands the SDS a credential (rotated_by_sds = true), outside rotation_worker REGISTRY_REPOSITORIES`.
  The repository comes first, and the `outside rotation_worker REGISTRY_REPOSITORIES` suffix stays
  greppable. The docstring and the module docstring state the rule as it is now.
- `docs/operations/membership.md`: the `credential_rotation` paragraph restates the rule and its gap
  cases, and cites D-59 A and Infra Brain rule 1032.

### Network surface

The host (`api.github.com`), the route (`/repos/<o>/<r>/contents/.cred-consumers.toml`, already in
`_REGISTRY_ALLOWED` and pinned in `test_membership_edges.py` as `CRED_CONSUMERS`), and the verb
(`GET`) are all unchanged. The only difference is that the lane now reads the response body for
outside repositories. `tests/architecture/test_membership_edges.py` needed no change and passes.

### Statements of the old rule

I searched for the three phrases from the handoff and for `cred-consumers` in `CLAUDE.md`,
`docs/operations/`, `docs/decisions/` and `tests/`. I rewrote each statement once:

- The `_rotation_scope` docstring, the message, and the `consistency.py` module docstring.
- The comment on `_REGISTRY_ALLOWED` and the `holds_registry` docstring in `declaration.py`.
  The latter said "The file's content is never read", which is no longer true of the module.
- `docs/operations/membership.md`, line 162.
- The tests: `test_rotation_scope.py` and the public-surface pin in `test_declaration.py`.

I left the following unchanged:

- No CLAUDE.md invariant states the rule.
- "REGISTRY_REPOSITORIES stays the ceiling" appears only in #572's commit message, which is
  history.
- ADR-0055, line 2170, says `REGISTRY_REPOSITORIES` "stays in the executor as a ceiling the
  orchestrator can't widen". That is about the executor and still holds, so it needs no amendment.
- ADR-0059 names `_registry_files` at lines 2543 and 1994. The function still exists and still
  reads each member's file, so it needs no amendment.

## Tests and checks

I wrote the tests first and watched them fail: 18 failed and 190 passed before the implementation.
The 500, 403 and 301 cases and the absent case already passed, because those gaps exist today.

These are the new tests in `tests/membership_check/test_rotation_scope.py`. They drive the Typer
entry point with the real `GitHubDeclarationSource` over an `httpx.MockTransport`.

- `test_an_outside_file_with_only_false_rows_hands_nothing_over`: the fixture has the shape of
  security-standards' file (one row, `bws-factory-events-nightly-token`, `rotated_by_sds = false`).
  The test expects exit 0 and no output about the file.
- `test_an_outside_file_with_rows_that_omit_the_flag_hands_nothing_over`: rows with no flag, an
  empty file, and a file with no `credential` table. Each is a pass.
- `test_an_outside_file_with_a_true_row_disagrees`: exit 2 with the new wording.
- `test_a_true_row_beside_a_malformed_one_still_disagrees`: exit 2.
- `test_an_outside_file_that_cant_be_judged_is_a_gap`, parametrized over ten cases: malformed TOML,
  `credential` as a string, an array of strings or a single table, non-UTF-8 bytes, `"true"` as a
  string, `1`, and the statuses 500, 403 and 301. Each case expects exit 3, the
  `.cred-consumers.toml unread: A/one` gap, and no disagreement. A `SECRET-MARKER` string in the
  body must not appear in the output.
- `test_an_absent_outside_file_is_nothing`: a 404 that the repository probe confirms gives exit 0.
- `test_a_file_inside_the_tuple_is_read_for_presence_only`: an unparseable body in a tuple
  repository produces no gap here, because its rows come through the mirror.

I also changed these existing tests:

- `test_declaration.py`: the public-surface pin now includes `registry_hands_over`. The name-refusal
  test covers both methods, and a new test checks the reader's yes, no, and could-not-tell answers.
- `test_rotation_scope.py`: the tests that use the double moved to the `hands_over` answer. One
  assertion that would have passed vacuously after the rewording (`"holds .cred-consumers.toml" not
  in output`) now checks the new suffix.
- `test_consistency.py`: the `Declarations` double gained `registry_hands_over` and `_wire` gained a
  `hands_over` keyword.

The other existing rotation-scope tests, including `test_todays_estate_agrees`, are unchanged in
meaning and pass.

### Mutation results

Each mutation ran against `tests/membership_check`. I restored the file after each run and cleared
`__pycache__` before and after.

| Mutation | Result |
|---|---|
| M1: revert the narrowing (`read = source.holds_registry` for every member) | 11 failed, including `test_an_outside_file_with_only_false_rows_hands_nothing_over` |
| M2: drop the `true`-row branch in `hands_over` | 3 failed, including `test_an_outside_file_with_a_true_row_disagrees` |
| M3: let a non-bool flag pass | 2 failed (`string-true`, `int`) |
| M4: decode with `errors="replace"` | 1 failed (`utf-8`) |
| M5: read inside repositories with `registry_hands_over` | 1 failed (`test_a_file_inside_the_tuple_is_read_for_presence_only`) |

No mutation survived.

### The real file

I fed security-standards' file at `0226b199` to `hands_over` and printed only the `Declaration`. It
returned `Declaration(target=False, detail='.cred-consumers.toml has no rotated_by_sds = true row')`.
The lane therefore passes this row, rather than reporting it as a gap with exit 3.

### Gates

- `make check` in the worktree against `orchestrator_test_hq_w_189` exited 0:
  `15244 passed, 1 skipped`. Pyright reported 0 errors, and ruff and format checks were clean.
  That run came before a docstring reflow and the review's test fix. After those, I reran
  `tests/membership_check` (208 passed), `tests/architecture` (2288 passed with the membership
  tests), ruff, and pyright on the changed files. The final commit's full gate is CI's run.
- `/code-review` (medium) returned three findings:
  - **The inside read can only produce a gap.** Not acted on: the handoff requires repositories
    inside the tuple to keep today's handling, which includes the presence read and its gap.
    Dropping the read, and `holds_registry` with it, is a separate decision for HQ.
  - **The inside test was weak.** Fixed: the test now spies on the reader and asserts that
    `registry_hands_over` is never called for an inside repository. M5 shows the spy catches
    the regression.
  - **The decoding may differ from the mirror's.** Not a defect: `_entries` also decodes with a
    strict `.decode()` followed by `tomllib.loads`, so a BOM is a parse error in both readers.
- CI on PR #728: see the CI state section.

## Files changed

- `src/membership_check/declaration.py`
- `src/membership_check/cli.py`
- `src/membership_check/consistency.py`
- `docs/operations/membership.md`
- `tests/membership_check/test_rotation_scope.py`
- `tests/membership_check/test_declaration.py`
- `tests/membership_check/test_consistency.py`

## Self-review

- Repositories inside the tuple keep exactly today's handling: a presence read, a gap when it can't
  be read, and nothing reported.
- `_rotation_scope` still filters out the names in `registry_repositories`. The CLI no longer
  passes tuple repositories, but `check` stays pure and the filter keeps `test_todays_estate_agrees`
  meaningful.
- The parameter keeps the name `registry_files` to limit churn. The `_rotation_scope` docstring now
  says what it holds: repositories whose file has a `rotated_by_sds = true` row.
- `hands_over` restates the `is True` test rather than importing `RegistryEntry`. This keeps the
  pinned import set from `rotation_worker.registry` in `test_membership_edges.py` unchanged.

## Concerns

- A row whose flag is `true` but whose type the SDS doesn't rotate, such as the canary's
  `rotation-canary` class, still counts as a hand-over here. The handoff defines the rule as
  "has a `rotated_by_sds = true` row", and the `_flagged_repositories` row applies the type filter
  only to rows that come through the mirror. I followed the handoff. No outside repository has such
  a row today.
- The lane goes green only after HQ releases and re-tags it. I made no deploy, release, or
  lane change.

## CI state

All three checks on PR #728 pass: `Quality`, `Runner consumer compatibility`, and
`Rollout transcription currency`. CI ran on these commits:

- `55867b44`, run `43961008961`: `15222 passed, 23 skipped`.
- `3d0fa3a8`, run `43964013900`: `15222 passed, 23 skipped`. This commit adds the review's
  test fix and this report.

CI skips 22 more tests than my local run (`15244 passed, 1 skipped`). I didn't check which tests
CI skips.

The edit that adds this section comes after `3d0fa3a8` and changes only this report.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout in production):** the lane's output shape is
  unchanged. Disagreements still go to stdout and gaps to stderr through the existing
  `typer.echo` calls, and the change adds no file logging.
- **Code Brain rule 4 (never silently swallow an exception):** each failure returns a `Declaration`
  with a reason, which the CLI reports as a gap:
  - The TOML catch covers `TOMLDecodeError`, `ValueError` and `RecursionError`, the same tuple
    `parse()` uses.
  - The decode catch covers `UnicodeDecodeError`.
  - The 2xx check sits in `_registry`.

  The change adds no bare `except` and no `pass`. The docstrings of `hands_over` and
  `registry_hands_over` justify each choice.
- **Code Brain rule 5 (never log secrets, credentials, or tokens):** gap details name the file and
  a reason only, with at most the exception's type name. They never include the parser message or a
  value from the file. The `SECRET-MARKER` assertion across all ten gap cases tests this. The token
  stays in the client header and appears in no message.
- **Infra Brain 591, 592 and 593 (no BWS token in tracked files or history, and no inline
  `BWS_ACCESS_TOKEN` value):** the diff contains no token and no env-file value. The test fixtures
  use placeholder ids only.
- **Infra Brain 1032 (`cred.exposure-rotate`):** the change makes the check consistent with this
  rule. A credential's row can live in its owning repository's `.cred-consumers.toml` without
  failing the lane, unless the row hands the credential to SDS from outside the executor's list. I
  saw no secret value: the lane reads the file's flags and never prints them.
- **Infra Brain 212 (HTTPS for public FQDNs):** doesn't apply. The change makes no network or domain
  change; the lane reads the same HTTPS GitHub API route.

## Follow-ups

- HQ releases and re-tags the `membership-check` lane after merge, then confirms that a daily pass
  exits 0 against security-standards.
- Teardown after merge: remove the worktree, drop `orchestrator_test_hq_w_189`, and delete the
  merged branch.
