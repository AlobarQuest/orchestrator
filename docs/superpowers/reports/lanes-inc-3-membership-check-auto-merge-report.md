Outcome: done

# Report: lanes-inc-3-membership-check-auto-merge (W-125)

- PR: [AlobarQuest/orchestrator#687](https://github.com/AlobarQuest/orchestrator/pull/687), ready,
  not merged.
- Code head: `b6d00fdd`. This report and the answered question follow it in one more commit.
- Base: `origin/main` at `1d7c07c0`. The handoff's facts are confirmed there.

## What I built

The membership check reads the auto-merge setting over GraphQL on both hosts. The steps are
HQ's answer A (see `lanes-inc-3-membership-check-auto-merge-question-answered.md`).

### Step 1: the premise at the source

`sds-github-mint` is `estate_clients.github_app_mint:main` (`pyproject.toml` line 107).

- **The permission reaches GitHub.** `run` builds the permissions from each `--permission` flag.
  `mint` then POSTs `{"repositories": [...], "permissions": dict(permissions)}` to
  `/app/installations/{id}/access_tokens`. The test
  `test_the_script_prints_only_the_token_narrowed_to_the_request` pins the posted body.
- **The answer is checked.** `mint` raises `MintRefused` unless the answer's `permissions` equal the
  request: `the token for … was minted with other permissions`, then exit 1
  (`test_a_permission_echo_that_differs_is_refused`).
- **So the token held `administration=read`.** G-48's container pass exited 3, not 1, so its
  token held metadata, contents and administration read. Administration read isn't what gates
  REST's `allow_auto_merge`.

### Step 2: the read

GitHub's REST page for `GET /repos/{owner}/{repo}`,
[Get a repository](https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#get-a-repository),
read 2026-11-13, says three things:

- **The route's note:** "To view merge-related settings, you must have the contents:read and
  contents:write permissions."
- **Its token section:** "This endpoint works with the following fine-grained token types: GitHub
  App user access tokens, GitHub App installation access tokens, Fine-grained personal access
  tokens. The fine-grained token must have the following permission set: "Metadata" repository
  permissions (read)".
- **The field:** `allow_auto_merge` is a boolean in the response schema. Its only description is
  on Update a repository: "Either true to allow auto-merge on pull requests, or false to disallow
  auto-merge."

The GraphQL reference describes the Repository object's field this way: "`autoMergeAllowed`
(Boolean!): Whether or not Auto-merge can be enabled on pull requests in this repository." It names
no permission. A query is a POST. [Forming calls with GraphQL](https://docs.github.com/en/graphql/guides/forming-calls-with-graphql)
says: "you'll provide a JSON-encoded body whether you're performing a query or a mutation, so the
HTTP verb is `POST`".

Both of these hit a stop point, so I wrote the question file. HQ answered A, built as a probe.

### Step 3: the change

`src/membership_check/auto_merge.py`:

- **One read on both hosts.** `GitHubAutoMergeSource.state` still asks `GET /repos/{o}/{r}`
  first, so a 404 on the workflows directory still means the directory is absent. It no longer
  reads `allow_auto_merge` from that answer. The setting comes from `_auto_merge_allowed`, one
  `POST /graphql` with `AUTO_MERGE_QUERY`. There's no REST fallback.
- **Fail closed.** Only a literal boolean at `data.repository.autoMergeAllowed` in a 200 counts.
  Any other status (201, 204, 401, 403, 502), any `errors` key (even an empty list or `null`), a
  null repository or data, a non-boolean value, or a body that isn't a JSON object is `None`.
- **The guard.** `is_allowed_query(path, body)` admits only `/graphql` with a body of exactly
  `{"query", "variables"}`. The query must equal `AUTO_MERGE_QUERY`, and the variables must be
  exactly `owner` and `name`, each matching the existing segment pattern. It judges the built
  request: the path the transport will send, no query string, and the body as encoded. A refused
  query is never sent. `_ALLOWED` and `is_allowed` are unchanged.

### Step 4: a diagnostic `detail`

A failed read gives `<repo>: autoMergeAllowed was not served (POST /graphql answered <status>…)`,
in one of the following forms:

- `answered 403`, for a status other than 200.
- `answered 200 with errors: NOT_FOUND, FORBIDDEN`, naming each error's `type` that matches
  `[A-Z][A-Z_]{0,63}`, and otherwise `an error with no type`.
- `answered 200 with errors: a malformed errors list`.
- `answered 200 without data.repository.autoMergeAllowed`.
- `answered 200 with no JSON object`.
- `is not a query this reader may send`, when the guard refuses.

No message, path, body, token or header is kept. The tests put a `secret-echo` marker in error
messages and assert that it never appears in `detail`.

### Step 5: the false text, corrected

- **`auto_merge.py`:** the module docstring states the documented rule and the GraphQL read.
- **`scripts/run-membership-check.sh`:** the comment says no read is known to need
  `administration`, and why the setting is read over GraphQL.
- **`docs/operations/deploy.md`:** step 7 (around line 449) and the mint paragraph (around line
  609) are corrected the same way.
- **`tests/architecture/test_wsp21_invariant_scan.py`:** the `OUTBOUND_ALLOWLIST` reason names
  the GraphQL query.

`administration=read` stays in the mint, as HQ directed, so the retry changes one thing.

### Step 6: how it's proven

The proof is HQ's paired retry after release, in a gate like G-48. If the container's token is
served the field, each member reads `True` or `False`. If it isn't, each member's gap names the
status and error types, and B or C is Devon's call. This is `deploy.md` step 7's text as it now
reads:

> 7. The lane's `--add`, then the `lane-run --read` proof. The check has no `--dry-run`, so its first
>    container pass is cut-over step 2's ordinary pass, which settles ADR-0059 amendment 5 open
>    item 1: its report must show the clone's commit and no "packages checkout" gap. The same pass
>    shows whether the read App's installation token is served GraphQL's `autoMergeAllowed`
>    (`POST /graphql`, `src/membership_check/auto_merge.py`): served, or a gap per member whose
>    detail names the route, the status and each error's `type`. Both hosts read the setting there;
>    REST's `allow_auto_merge` isn't read, because GitHub serves merge-related settings only to a
>    token with `contents:write`. It also proves the read App's installation covers every
>    repository the member record observes, and that they share one owner: otherwise the mint
>    refuses, and every pass exits 1.

## Tests and checks

- **`make check`:** green on the committed tree `b6d00fdd`, with database
  `orchestrator_test_hq_w_125`. Ruff and pyright are clean, and pytest gave 14789 passed and 1
  skipped.
- **`tests/membership_check/test_auto_merge.py`:**
  - The field served as true and as false.
  - Unserved, one case per shape and status, each checked for its stated reason and for no leaked
    text.
  - REST's `allow_auto_merge` is never read.
  - The fixed-query refusal probes: other paths, query text, extra keys, missing or extra
    variables, a slash, empty or non-string names, and non-dict bodies.
  - A refused query is never sent.
- **`tests/architecture/test_membership_edges.py`:**
  - The edge table lists `("POST", "/graphql")`.
  - A control shows a widened query guard fails the table.
  - The source scan admits exactly one `build_request POST GRAPHQL`, and its control shows a POST
    to any other path is reported.
- **`tests/architecture/test_out_of_process_isolation.py`:** `json` is added to
  `membership_check`'s row, because the guard re-reads the body it built.
- **Mutation test:** 7 mutants on `auto_merge.py`, 0 survivors:
  1. Guard removed.
  2. Variables unchecked.
  3. `errors` ignored.
  4. A non-boolean accepted.
  5. Error text leaked.
  6. Any 2xx accepted.
  7. An empty `errors` list accepted.
- **Security scan from the repo root:** no BLOCK findings. Two WARNs
  (`bws.secret-files-gitignored`, `bws.manifest-matches-usage`) are about files this diff doesn't
  touch.
- **`/code-review` (low) on `origin/main...HEAD`:** no findings.

## Files changed

- `src/membership_check/auto_merge.py`
- `tests/membership_check/test_auto_merge.py`
- `tests/architecture/test_membership_edges.py`
- `tests/architecture/test_out_of_process_isolation.py`
- `tests/architecture/test_wsp21_invariant_scan.py` (comment only)
- `scripts/run-membership-check.sh` (comment only)
- `docs/operations/deploy.md`
- `docs/superpowers/reports/lanes-inc-3-membership-check-auto-merge-question-answered.md`
- `docs/superpowers/reports/lanes-inc-3-membership-check-auto-merge-report.md`

## Self-review

- **One code path on both hosts.** The Mac's admin login no longer gets the REST field, so the
  Mac pass also reads GraphQL. If the Mac login isn't served `autoMergeAllowed`, the Mac pass
  becomes a gap too. I don't expect that, because the field is non-null for an authorized reader.
- **The repository GET still runs first.** Its answer's settings are ignored. It's kept only to
  tell an absent workflows directory from an absent repository.

## Deviations

None from HQ's answer. The handoff's step 3 said to read over a documented GET route. No such
route serves the field to this token, so the question and HQ's answer replaced that step.

## Follow-ups

- Drop `administration=read` from the membership check's mint once every route is mapped to its
  documented permission. If that leaves the read App's Administration permission unused, Devon
  can drop it from the App.
- If the paired retry shows the field isn't served, Devon decides between B (widen) and C (report
  the field as a documented gap).

## Concerns

None beyond the unproven read, which is the point of the retry.

## Standards followed

- **Code Brain rule 3 (structured JSON logs to stdout):** no logging change. The module logs
  nothing, and failures travel as `detail` in the program's existing report.
- **Code Brain rule 4 (never swallow an exception silently):** every failure in the GraphQL read
  raises `_Unread` with a stated reason, which becomes `None` plus `detail`. Each `except` names
  its exception types. There's no bare `except`.
- **Code Brain rule 5 (never log secrets):** `detail` carries the route, the status and
  pattern-checked error types only. No token, Authorization header, error message or body appears
  in it. Tests assert that a `secret-echo` marker in error messages and the placeholder token never
  appear.
- **Infra Brain 591, 592 and 593 (BWS tokens):** no BWS token is in any tracked file or in
  history, and no bootstrap token is inline. The test token is `placeholder-not-a-token`, which
  doesn't match the token shape. The security scan reports no BLOCK.
- **Infra Brain 1032 (cred.exposure-rotate):** no credential was surfaced or changed, and the mint
  and the App are unchanged.
- **Infra Brain 212 (HTTPS for public FQDNs):** not applicable. There's no domain change, and the
  reader calls `https://api.github.com` as before.
