Outcome: done

# Build report: the inert lander's ping-URL row is due

Handoff: `lanes-inc-5-inert-landing-ping-url-due` (worker W-244). Branch
`hq/lanes-inc-5-inert-landing-ping-url-due`, base `origin/main` at `b4175ea6`. Every fact the
handoff gives at `b4175ea6` matched at the base. PR: #756, ready and not merged. Its code commit is `4f05d0b9`; the head is the commit that records this PR number.

## What I built

The `inert-landing-ping-url` row takes `rotate_requested = "2026-11-20"`, so infraops reports it
due and the rotation proposer generates its first issue. The lane's block comment states the rule
as it stands, and the row test asserts that both rows carry their dates. The change mirrors #747
(`2f809005`).

## Items 1 to 6

1. `.cred-consumers.toml:1392` adds `rotate_requested = "2026-11-20"` right after
   `rotated_by_sds = true`. That is the order the token row uses (`:1363`, `:1364`). The row has
   no `created` and no `last_rotated`. I removed the row's "Not yet due" comment line, as #747
   did for the proposers' rows.
2. The block comment (`.cred-consumers.toml:1351-1357`) reads: "Both are due for their first issue
   by `rotate_requested`. The ping URL's row is dated only after the token's `store` unit
   completed, so the two issue units never waited on a mint at once." The rest of the comment is
   unchanged, reflowed to 100 columns. It has no correction history.
3. `tests/rotation_worker/test_inert_landing_rows.py:125` replaces `test_only_the_token_row_is_due`
   with `test_both_rows_are_due`. The test asserts the token row's `2026-11-19` and the ping-URL
   row's `2026-11-20`. I read the token's date from the file at the base (`:1364`). The module
   docstring (`:4-6`) states that both rows are due. A grep of `tests/` and `src/` for
   `inert-landing-ping-url` and for `rotate_requested` beside `inert` found no other assertion that
   the row isn't due.
4. Here's how the proposer reaches the issue plan:
   - The row is due through infraops' `cred-findings` (item 5).
   - `_generate` (`src/rotation_proposer/cli.py:444`) renders revision 1 with
     `first_issue=_first_issue(last_rotated, due.credential_id)` (`:462`).
   - `_first_issue` (`:436-441`) returns `True` while infraops serves the row's `last_rotated` as
     null. Both scratch runs in item 5 served `"inert-landing-ping-url": null`. infraops builds the
     field from the row's age anchor and the rotation state
     (`src/cli/security-drift-cli.ts:190-194`); the row has no `created` and no recorded rotation.
     The scratch state was empty, so this is observed for the row's own fields, not for the live
     state.
   - `plan_for` (`src/rotation_proposer/generate.py:131-162`) returns the issue plan for
     `first_issue is True` (`:149-150`).

   `test_a_first_issue_derives_the_issue_plan`
   (`tests/rotation_worker/test_inert_landing_rows.py:152-155`) is parametrized over both rows
   (`BOTH`, `:46`) and asserts `plan.issue` for each.
5. Due-ness:
   - Setup: infraops' `cred-findings` (`dist/cli/security-drift-cli.js`), run from a scratch
     `rsync` copy (no `.git`) of `~/Projects/infraops-mcp-server` at `68c4af5`, in
     `/tmp/w244-infraops.IYiC`. For each run, `INFRADRIFT_CONFIG_DIR` and
     `SECURITY_DRIFT_STATE_DIR` named a scratch directory holding a one-file list and an empty
     (`{}`, mode `0600`) `cred-rotation-state.json`. Both runs used
     `--now 2026-11-20T14:00:00Z`. I didn't edit or build the checkout, and I didn't touch the
     real list.
   - Treated (this branch's file): exit 0 and 27 findings, all `cred.rotation-requested`,
     including
     `{'check': 'cred.rotation-requested', 'id': 'inert-landing-ping-url', 'class': 'healthchecks-ping-url', 'rotate_requested': '2026-11-20', 'rotated_by_sds': True}`.
   - Control (`origin/main`'s file): exit 0 and 26 findings, none for the ping-URL row.
   - As `(check, id)` pairs, treated minus control is exactly
     `('cred.rotation-requested', 'inert-landing-ping-url')`, and control minus treated is empty.
   - Neither run raised a parse error or `cred.invalid-rotate-requested`, so the file parses under
     infraops' TOML subset. The other rows show as due in both runs only because the scratch state
     is empty.
   - The source rule is `src/security-drift/cred-rotation.ts:301-312` in infraops.
6. Mutation check: I copied the file and removed the new line by exact pattern. The row tests gave
   1 failed and 12 passed (`test_both_rows_are_due`, `KeyError: 'rotate_requested'` at `:129`). I
   restored from the copy, and `cmp` showed the file equal to the copy. The rerun gave 13 passed.

## Compose at the base (out of scope)

`lanes/inert-landing/compose.yaml:26` holds `SDS_DEADMAN_PING_URL_UUID: ""`, and its comment
(`:13-16`) says a later change writes the keeper's id. I didn't change the compose,
`.bws-secrets.toml`, or `test_deploy_lanes.py`'s `placeholders` (deploy.md item 6).

## Tests and checks

- `make check` (test database `orchestrator_test_hq_w_244`): exit 0. Lint, format, and pyright are clean, and pytest gave 14708 passed, 1 skipped, and 11 warnings in 339 s.
- Row tests: 13 passed.
- Security scan from the repo root: 0 BLOCK, 2 WARN, and 1 INFO. The two WARNs are
  `bws.secret-files-gitignored` (`*-migration/`) and `bws.manifest-matches-usage` (UUID
  `aaff45ca-…`). Neither comes from this diff, which adds no UUID and no file. The INFO is the
  standing `bws.least-privilege-scope` judgment item.
- `/code-review` (low) on `origin/main...HEAD`: no findings.
- `code-standards risk`: `normal`, so no vendor review is required.

## Files changed

- `.cred-consumers.toml`
- `tests/rotation_worker/test_inert_landing_rows.py`
- `docs/superpowers/reports/lanes-inc-5-inert-landing-ping-url-due-report.md`

## Self-review

The diff touches one row and its block comment, and it changes no other lane's row. The test pins
both dates as literals, which is what makes the mutation fail. The comment's rule matches the
wording #747 gave the proposers' blocks.

## Concerns

None. Deploy.md item 6 (the compose's `SDS_DEADMAN_PING_URL_UUID` and the manifest) remains for
a later change, as the handoff scopes it.

## Follow-ups

- Deploy.md, the inert lander's cut-over item 6: write the keeper's id into the compose, add it to
  `.bws-secrets.toml`'s lane list, and update `test_deploy_lanes.py`'s `placeholders`.

## Standards followed

- Code Brain rule 3 (structured JSON logs to stdout): the diff changes no logging.
- Code Brain rule 4 (never swallow an exception): the replaced test has no `try`. Its assertions
  fail loudly, as the mutation check showed (`KeyError`).
- Code Brain rule 5 (never log secrets): nothing added prints a token, a ping URL, or the UUID
  inside a ping URL. The row names its BWS secret id (`bws_uuid`), which is a keeper id, not the
  ping URL's UUID.
- Infra Brain 591, 592, and 593: the diff adds no BWS token, and the bootstrap token isn't inline.
  The one added value is a date. The scan reports 0 BLOCK.
- Infra Brain 2890: the diff commits no ping URL. The row names its check by name
  (`sds-inert-landing`) and its keeper by BWS secret id, and no file in the diff holds a ping URL
  with a UUID.
- Infra Brain 1032 (cred.exposure-rotate): neither inert-landing row declares a
  `[[credential.exposure]]`.
- Infra Brain 212 (HTTPS for public FQDNs): not applicable, because the diff changes no domain.
- Infra Brain lesson 450 (rotation landmines): no secret value enters the registry, a plan, a test,
  or a log. The rows name ids only, and the test reads only ids and dates.
