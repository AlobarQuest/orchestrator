Outcome: done

# The narrowed GitHub App mint helper and the lane Apps' key rows: report

Worker W-61, branch `hq/lanes-inc-3-mint-helper`, base `origin/main` at `fb485d2c`. PR
[#642](https://github.com/AlobarQuest/orchestrator/pull/642), opened ready for review and not
merged. Code head before this report: `0e55922e`. This report's commit is the PR's head.

No production act: no token mint, no BWS read or write, no GitHub App call, no Coolify, VM or
Healthchecks call, and no launcher or `lanes/` change. GitHub was read only with `gh api` GETs
(factory-runner's registry). infraops-mcp-server was read with `git show origin/main:...` at
`c3359bf1486cfe63018cfb404b03246aaabd4f32`; it wasn't built or run.

## What I built

### 1. `src/estate_clients/github_app_mint.py`

`mint(app_id, private_key_pem, repositories, permissions, *, transport, clock, user_agent)`
(`:69`) returns the token as a `str`, or raises `MintRefused` or `MintUnavailable`.

- **The request is checked before the key is read or anything is sent** (`_check_request`,
  `_check_repositories`, `:127-165`). It needs a numeric App id and 1 to 500 `owner/name`
  repositories (`.` and `..` refused as names; one repository named twice in any case refused).
  The repositories must have one owner (`:163`). It needs at least one permission, because GitHub
  grants every App permission when none is named. `metadata` must be one of them (`:141`); see
  "Self-review".
- **The installation comes from the repositories.** Each requested repository is looked up with
  `GET /repos/{owner}/{name}/installation`. More than one distinct installation id is refused
  before the mint (`:90`). No owner name and no installation id are written in the module.
- **The echo check** (`:106-111`): the response's `permissions` must equal the request, and the
  casefolded `full_name` list must have the request's length and set. A malformed `repositories`
  value is a refusal (`_echoed_repositories`, `:118`), not a crash.
- **Transport:** one `ConfinedClient` per mint. It permits only the requested repositories'
  installation lookups and `POST /app/installations/\d+/access_tokens`. `TransportFailure` (which
  wraps `TRANSPORT_ERRORS` at construction and request) and any 5xx become `MintUnavailable`. Any
  other unexpected status becomes `MintRefused`, which keeps the status alone, never the body.
- **The module accepts whatever levels its caller passes.** Only the console script limits them.

### 2. `RotationApp.token` calls it

`src/rotation_worker/github_actions.py:96-113` calls `mint` with `[repository]` and translates
`MintUnavailable` to `GitHubUnavailable` and `MintRefused` to `ContractError`, carrying the
message. It still sends `rotation-worker/1 (+AlobarQuest/orchestrator)` as its User-Agent, through
the new `user_agent` argument (pinned by `test_the_mint_names_the_rotation_worker`). The module's
own `_client`, `_jwt`, `_send` wrapper, `_check_repository`, `REPOSITORY` and `JWT_LIFETIME` were
removed; they live once in the mint. The existing 21 tests in
`tests/rotation_worker/test_github_actions.py` pass unchanged.

Behavior differences, both narrowing: a key that doesn't load was a raw `ValueError` and is now a
`ContractError` naming the exception type, and a repository named `.` or `..` is refused.

### 3. Console script `sds-github-mint`

`pyproject.toml:107`: `sds-github-mint = "estate_clients.github_app_mint:main"`.

- `--app-id`, repeated `--repository`, repeated `--permission name=level`, and `--allow-write`.
- The key is read from **stdin** only. Without `--allow-write` every level must be `read`
  (`:314`). With it, `read` and `write` are accepted. `admin` is refused either way.
- stdout carries only the token, with no newline. Once the arguments parse, every failure writes
  `FATAL: <cause>` on stderr, writes nothing on stdout, and exits 1. A usage error is argparse's
  and exits 2 (stated in the module docstring and in `deploy.md`).

### 4. Key form

**The stored form is base64 of the PEM; the in-process form is the PEM text.** `decode_stored_key`
(`:249`) strips all whitespace, so it accepts one line (macOS `base64`) or wrapped (`openssl
base64`, GNU `base64`). It decodes with `validate=True` and requires the decoded text to start
with `-----BEGIN`. Raw PEM on stdin is refused with "the key arrived as PEM; the stored form is
base64 of the PEM" (`:255`).

Sources checked:

- **Dispatch App:** `src/orchestrator/services/github_app.py` holds `private_key_b64` (`:51`) and
  decodes it in `_assertion` (`:153-161`) by stripping whitespace and calling
  `b64decode(validate=True)`. The new module's decode follows it.
- **Fork-sync App:** factory-runner's `.cred-consumers.toml` row `fork-sync-app-private-key`
  (read at `main` `e1fb580aac7b4e72d6458fd10c7f639969cafa51`; the file last changed at
  `4b1580f7246b7a12f08b33181dc691ee50487133`) says "the BWS keeper holds base64 of the PEM", and
  its keeper's note says "base64 of the PEM".
- **The rotation App (`alobar-sds-rotation`, App id 5383095) differs:** its key is read as raw PEM
  (`BwsStore.read_key`, `src/rotation_worker/secret_store.py:107-109`, loaded directly as PEM).
  That's why `mint` takes PEM text and the base64 decode sits at the stdin boundary. See
  "Concerns".

The form is stated in the module docstring and in `docs/operations/deploy.md`, in the new
section "A lane's GitHub token (ADR-0059 increment 3)" (`:315`) inside "The lane phases". I put it
there because a lane's move PR is driven from that page, and the section names the launcher
pipeline and the two Apps' ids.

### 5. Registry rows

`.cred-consumers.toml:728-768`: `lanes-read-app-private-key` and `lanes-push-app-private-key`,
class `github-app-private-key`, `provider = "github"`, `provider_identity` naming the App, its App
id and its installation. Each row also has `bws_uuid` (`314a966c-…` and `b8f547b9-…`),
`created = "2026-11-09"` and `disposition = "reissue"`. Neither row is `rotated_by_sds`. The rows
are modelled on factory-runner's `fork-sync-app-private-key` at `4b1580f7`. Their
`rotation_preconditions` are: rotate both in one sitting, delete the old key pair (a new pair
doesn't revoke the old), and store base64 of the PEM.

- **Consumers:** each row lists only its `bws-secret` keeper, noted "no reader holds a grant yet".
  The orchestrator registry's own convention for a credential nothing reads is the keeper alone
  with a note saying so: `alert-hub-sds-orchestrator-sender` (keeper note "nothing scheduled
  reads it", besides one held copy). I didn't use `rotation-canary`'s `runtime-fetch` row with
  `ref = "none; a person attests"`, because that row is SDS-rotated and these aren't. Each lane's
  `runtime-fetch` row lands in that lane's move PR (stated in the block comment).
- **`created` is deliberate**, unlike the execution-watch rows. Those omit it so the proposer's
  first-issue path runs. These keys exist, and `created` is the age anchor that makes the 365-day
  finding fire (item 6).
- **Where `alobar-sds-rotation`'s key is registered:** in no `.cred-consumers.toml`. I grepped
  this repository's tree and `origin/main` of infraops-mcp-server, factory-runner,
  security-standards and intent-packages. It appears only in docs:
  `docs/operations/rotation-executor.md:379` (`github_app_key_id = "80c752aa-…"` in the VM's
  `config.toml`) and `:431` (BWS secret `rotation github app private key` in `Rotation / Admin`).
  intent-packages names the App in `packages/rotation-factory-pr-token/package.yaml` as the
  writer, not as a credential. See "Concerns".

### 6. The keys' due date without a rotation package

The existing marking already does this, so I used it and invented nothing. A row without
`rotated_by_sds` is a person's to rotate:

- **The 03:00 scan reports the due date to Devon.** `rotationAnchor` takes `created` when nothing
  has recorded a rotation (`src/security-drift/cred-rotation.ts:209-211`). `credFindings` raises
  `cred.rotation-age` (WARN) when the age passes the class's `maxAgeDays` (`:266-278`). The class
  is 365 days (`:54-59`), with the key-pair landmine. `scanFindings` drops rotation triggers only
  for `rotated_by_sds` rows (`:307-315`), so these findings are posted.
  `buildCredClassifications` gives each one the title "Rotation due: <id>" and the manual
  checklist from `rotationClassification` (`:317-374`): the landmine, each
  `rotation_preconditions` line, then create, store and deploy, revoke, and `record-rotation`.
  infraops' own registry header states the rule: "the 03:00 scan offers a manual checklist for
  every row not handed over" (`.cred-consumers.toml:11`).
- **The rotation proposer proposes no package.** Each finding's `handed_over` is infraops'
  `rotated_by_sds` (`src/rotation_proposer/findings.py:91-93`, read at `:134-137`). For a finding
  not handed over, `_pass` appends `_not_handed_over` and `continue`s (`cli.py:697-700`), and
  `_not_handed_over` reports the outcome `legacy`, "a person rotates it", with no write
  (`cli.py:464-475`). It reports `not-handed-over` only if a package somehow exists.

So both keys come due on 2027-11-09 as change-manager security items, and no rotation package is
ever proposed for them.

### 7. Guards

- **`layers.toml`:** no edit needed. `src/estate_clients/**` (`:109`) and `tests/estate_clients/**`
  (`:115`) in layer `runtime` cover both new files, and `tests/architecture/test_layer_manifest.py`
  passes.
- **`OUTBOUND_ALLOWLIST`:** added `src/estate_clients/github_app_mint.py` with its reason
  (`tests/architecture/test_wsp21_invariant_scan.py:543-547`).
- **`test_unreachable_guards.py`:** its `SCOPE` is `orchestrator/kernel/` and
  `orchestrator/services/` only (`:65`), so the module is outside it. I didn't widen `SCOPE`. The
  module is reached from the `sds-github-mint` entry point and from
  `rotation_worker.github_actions`.
- **Two guards needed updating, and `make check` found both:**
  - `tests/architecture/test_rotation_edges.py`: the rotation worker's GitHub predicate is now
    captured from `github_app_mint._client` (`:248-252`). The worker's client count moves one
    client from `github_actions.py` (4 to 3) to `github_app_mint.py` (1) (`:507-509`). Its
    borrowed names gain `estate_clients.github_app_mint` (`:642`).
  - `tests/architecture/test_out_of_process_isolation.py`: the `estate_clients` row gains
    `argparse`, `base64`, `binascii`, `cryptography`, `datetime`, `jwt` and `sys`, with reasons.
    The `rotation_worker` row loses `cryptography` and `jwt`, which no worker module imports any
    more.
- `pyjwt[crypto]` was already a dependency; no dependency changed.

### W-60's report

`docs/superpowers/reports/lanes-inc-3-verify-report.md` is W-60's file from
`hq/lanes-inc-3-verify` at `6f060f2c`, unchanged except for a five-line correction note after its
`Outcome:` line. The note says the 14th member is `AlobarQuest/sds-canary`, which is private, so
"Every repository in the table is public" holds for the 13 seed members only.

## Tests and checks run

- `make check` (ruff, pyright, full pytest with `-n auto` against `orchestrator_test_hq_w_61`):
  **14,178 passed, 1 skipped**, ruff "All checks passed", pyright "0 errors". The first two runs
  failed on the two architecture guards named in item 7; I fixed both and reran.
- `tests/estate_clients/test_github_app_mint.py`: 38 tests. They cover the success path through
  the console script, a wrapped key, a permission echo that differs, and a repository echo that
  differs (extra, missing, one name twice, malformed). They also cover GitHub's own spelling,
  mixed owners (no request sent), two installations (no mint sent), a repository not installed,
  a transport error, a 5xx, and no token. Bad key forms (raw PEM, not base64, not a PEM, empty,
  broken PEM, a non-RSA key) send no request. So do a write level without `--allow-write`, `admin`
  with it, malformed requests, a missing `metadata`, and stdin that isn't text. One test runs
  every failure path and asserts stdout is empty and that stderr and captured output carry no
  key, JWT or token; another checks that no raised refusal does. One test checks the
  `[project.scripts]` entry and calls `main` itself.
- `tests/rotation_worker/test_github_actions.py`: the 21 existing tests unchanged, plus
  `test_the_mint_names_the_rotation_worker`.
- **Mutation pass** on the mint (cleared `__pycache__` around each): disabling the permission echo,
  the repository echo, the owner check, the installation check, the `--allow-write` limit, the
  raw-PEM refusal, and the `metadata` requirement. **7 of 7 mutants caught.**
- Security scanner (`security_scan.cli . --category security`): no BLOCK. Two WARNs, neither from
  this diff: `.gitignore` glob `*-migration/`, and an undeclared UUID `aaff45ca-…` in existing code.
- `/code-review` (medium) on the diff. Fixed: a missing `metadata` permission, a malformed
  `repositories` echo, non-text stdin, and the rotation worker's User-Agent. Not changed (see
  "Self-review"): the duplicated `_send`/`_json`, 403 and 429 classed as refusals, and one lookup
  per repository.

## Files changed

- `src/estate_clients/github_app_mint.py` (new)
- `tests/estate_clients/test_github_app_mint.py` (new)
- `src/rotation_worker/github_actions.py`
- `tests/rotation_worker/test_github_actions.py`
- `pyproject.toml`
- `.cred-consumers.toml`
- `docs/operations/deploy.md`
- `tests/architecture/test_wsp21_invariant_scan.py`
- `tests/architecture/test_rotation_edges.py`
- `tests/architecture/test_out_of_process_isolation.py`
- `docs/superpowers/reports/lanes-inc-3-verify-report.md` (W-60's, carried)
- `docs/superpowers/reports/lanes-inc-3-mint-helper-report.md` (this report)

## Self-review

- **`metadata` is required, not added.** GitHub's echo includes `metadata: read` whether or not
  it was asked for (the rotation App's `READ` and `WRITE` always name it). If the helper added it
  silently, the request would differ from what the caller wrote. Requiring it gives a clear cause.
  I haven't confirmed GitHub's behavior with a live mint (none allowed); requiring it costs nothing
  if GitHub doesn't add it.
- **One lookup per repository**, as the handoff states it ("a lookup that resolves another
  installation for any of them"). For the read lanes' 14 members that's 14 GETs with one JWT per
  pass. Because the repositories must share one owner, one lookup plus the echo check would also
  hold, so this trades a few requests for the literal guarantee and a per-repository "isn't
  installed" message.
- **`_send` and `_json`** repeat about ten lines of `github_actions.send` and `_json` with
  different exception types. `estate_clients` can't import a program's exceptions, and the
  worker's `send` takes a tuple of statuses for its other clients. I left both.
- **403 and 429 are refusals**, as they were in `RotationApp.token` before this change. A
  secondary rate limit on a lane's mint fails that pass with exit 1; the next pass mints again.
  See "Follow-ups".

## Concerns

- **`alobar-sds-rotation`'s private key has no registry row anywhere**, so nothing ages it:
  infraops never reports it due. Its keeper is BWS `Rotation / Admin` id `80c752aa-…`
  (`rotation-executor.md:379,431`). Its stored form is raw PEM, unlike the dispatch, fork-sync and
  both lane Apps (base64). I didn't add a row: the handoff scopes this PR's rows to the two lane
  keys, and the rotation App's row has its own consumers (the VM's `config.toml`) to name.
- **The rotation instances need a reinstall after merge.** `RotationApp.token` now runs through
  `src/estate_clients`, and `deploy.md`'s "Rotation instances" says a change there reaches the
  executor only by reinstalling both instances at the merged commit.
- **Standards coverage:** the handoff's standards cover this change. Two concerns fell outside
  them: the stdout-as-token-channel exception to Code Brain rule 3 (handled under rule 3), and
  `fork_sync/estate.py:103-135`. That module signs its own App JWT to check an installation. It
  doesn't mint, so it's outside item 2, but it's a third copy of the JWT claims.
- **Credential exposure (Infra Brain 1032):** none found in any file or history I read.

## Follow-ups

- HQ: register the `alobar-sds-rotation` key (class `github-app-private-key`, hand-rotated), and
  decide whether its keeper moves to the base64 form.
- HQ: reinstall the VM and Keychain rotation instances at the merge commit.
- Each lane's move PR: add its `runtime-fetch` consumer row to the right key row, grant its
  machine account the key, and pass `metadata=read` in its launcher's `sds-github-mint` call.
- Optional: have `fork_sync/estate.py`'s `app_installed` sign through the mint module's JWT
  helper, and treat 403 and 429 with `Retry-After` as `MintUnavailable`.

## Standards followed

- **Code Brain rule 5 (never log secrets, credentials, auth headers, tokens, or full request
  bodies).** The key and token appear in no message: refusals name causes, transport failures name
  the exception type, and error bodies are never read (`_send`). Every `raise` from a caught
  exception uses `from None`. The token leaves only as stdout. Two tests check every failure path
  and every raised refusal for the PEM, the base64 key, the token, `Bearer` and a JWT prefix.
  Test keys are generated at test time.
- **Code Brain rule 4 (never silently swallow an exception).** Every caught exception is re-raised
  as `MintRefused` or `MintUnavailable` with its cause, and the console script turns those into
  `FATAL: <cause>` on stderr and exit 1. There's no bare `except`. The review's three escapes (a
  malformed echo, non-text stdin, and argparse's own exit 2) are fixed or documented.
- **Code Brain rule 3 (structured JSON logs to stdout in production).** stdout is the token
  channel, so the helper writes nothing else there. Its diagnostics go to stderr as one
  `FATAL: <cause>` line, as the existing lane CLIs do (`src/execution_watch/cli.py:220-260`). The
  helper runs inside a lane's launcher, and the lane's own structured output is unchanged.
- **Infra Brain 591, 592, 593 (no BWS token, private key or minted token in a tracked file,
  fixture or history).** The registry rows and docs carry only BWS secret UUIDs and App and
  installation ids. Tests generate an RSA key per run and use a placeholder token string. The
  scanner reports no BLOCK, and the branch history holds the two commits plus this report.
- **Infra Brain 1032 (cred.exposure-rotate).** No exposed credential was found in anything I
  read, so there's no finding to report.
- **Infra Brain 212.** Not applicable: this change configures no domain.
