# Question: lanes-inc-3-membership-check-auto-merge (W-125)

Two stop points hold. No code is changed and nothing is pushed. Base: `origin/main` at `1d7c07c0`.

## Step 1: the premise at the source

`sds-github-mint` is `estate_clients.github_app_mint:main` (`pyproject.toml` line 107).

- `--permission administration=read` reaches GitHub's request body: `run` builds `permissions`
  from each flag, and `mint` POSTs `{"repositories": [...], "permissions": dict(permissions)}` to
  `/app/installations/{id}/access_tokens`. `test_the_script_prints_only_the_token_narrowed_to_the_request`
  pins the posted body.
- The mint checks the answer: `if minted.get("permissions") != dict(permissions): raise MintRefused(...)`.
  A permission GitHub granted less of would make the mint print `FATAL: the token for … was minted
  with other permissions` and exit 1 (`test_a_permission_echo_that_differs_is_refused`).
- G-48's container pass exited 3, not 1, so the mint succeeded. The token held exactly
  `metadata=read, contents=read, administration=read`, and `GET /repos/{owner}/{repo}` still
  didn't serve `allow_auto_merge`. Administration read isn't what gates the field.

## Step 2: what GitHub documents

REST, [Get a repository](https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#get-a-repository)
(`GET /repos/{owner}/{repo}`), read 2026-11-13:

- The route's note: "To view merge-related settings, you must have the contents:read and
  contents:write permissions."
- Its token section: "This endpoint works with the following fine-grained token types: GitHub App
  user access tokens, GitHub App installation access tokens, Fine-grained personal access tokens.
  The fine-grained token must have the following permission set: "Metadata" repository
  permissions (read)".
- The field: `allow_auto_merge` (boolean) in the response schema. Its only description is on
  Update a repository: "Either true to allow auto-merge on pull requests, or false to disallow
  auto-merge."

So the route serves installation tokens, but the documented condition for the merge-related
fields (`allow_auto_merge` among them) is `contents:write`. The read App holds `contents: read`
(D-18). This explains G-48: the Mac pass's admin login holds write, and the container's token
doesn't.

GraphQL, [Repository object](https://docs.github.com/en/graphql/reference/objects#repository):
"`autoMergeAllowed` (Boolean!): Whether or not Auto-merge can be enabled on pull requests in this
repository." The page names no permission for the field. But a GraphQL query is a `POST`
([Forming calls with GraphQL](https://docs.github.com/en/graphql/guides/forming-calls-with-graphql):
"you'll provide a JSON-encoded body whether you're performing a query or a mutation, so the HTTP
verb is `POST`"), so it's a non-GET route and needs a change to `auto_merge.py`'s route allowlist.
Its behaviour for a read-only installation token isn't documented, and a test under `gh` proves
nothing.

## Stop points that hold

1. The documented GET read needs a wider permission (`contents:write`), and the read App is
   read-only.
2. The only other read is GraphQL, a `POST` outside `_ALLOWED` / `is_allowed`, with no documented
   permission rule.

## The question

Which of these do you want? I recommend option C (smallest authority change, and step 4 and 5 are
worth doing regardless).

- **A. GraphQL read under the read App.** Add one `POST /graphql` route to `auto_merge.py`, sending
  only a fixed query for `repository(owner, name) { autoMergeAllowed }`, refused for any other
  body. Unproven until the paired retry: the documentation doesn't say a read-only installation
  token is served the field. Changes the route allowlist and its refusal probes.
- **B. Widen the read App to `contents: write`** (or mint it from a different App) for this read.
  Makes a read lane hold write, which ADR-0059's read-only App rules out; I don't recommend it.
- **C. Report the field as a documented gap in the container.** Keep `None` (never a pass), make
  the `detail` say the field needs `contents:write` per GitHub's documentation and name the route
  and status (step 4), correct the false "admin read" text in the three places (step 5), drop
  `administration=read` from the mint, and list "drop the read App's Administration permission"
  as a follow-up for Devon. The container pass then exits 3 for this reason on every pass, so the
  paired pass needs an agreed rule for it (for example, the Mac pass alone answers the setting).

If you choose A or C, say whether `administration=read` is used by anything else the check reads;
I found no other reader in `src/membership_check/` and would drop it.

## HQ's answer

HQ answered A, built as a probe: one GraphQL read on both hosts with no REST fallback, fail
closed on anything but a literal boolean, a diagnostic `detail`, an exact allowlist with refusal
probes and a mutation test, and `administration=read` kept in the mint for this change. B and C
are Devon's, decided only if the paired retry shows the token isn't served the field. The build is
in `lanes-inc-3-membership-check-auto-merge-report.md`.
