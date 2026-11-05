# Core platform layer design

**Status:** Approved by Devon 2026-11-04, after the builder check. Layer id: `platform`
(`make layer L=platform`).

## Intent

This layer is the orchestrator's own skeleton: the kernel (state machine and value types),
persistence (tables and Alembic migrations), the platform's API plumbing (`api/dependencies.py`,
`api/health.py`, `api/routes/common.py`, `api/schemas/common.py`), `main.py`, the review pages
(`web.py` and templates), the CLI (`cli.py`), identity, config, and `services/github_app.py`.
Route modules and services belong to the layers they serve.

This pass uses the same four outcomes as the earlier layers:

1. Every signal has a home: a finding or failure reaches alert-hub as one keyed alert, never only
   a log or a bare 500 nobody hears.
2. Scope comes from the member record: act by grant, observe by membership.
3. Records stay true: no record, doc, docstring, ADR or state says something that stopped being so.
4. Edges are clear: one test states the layer's outbound surface, and every rule has a test that
   fails when the rule breaks.

## Measurements, 2026-11-04

Read from the code at `f817a6e`, production's served `openapi.json`, and the production
container's log (counts only). Nothing was written.

| Measure | Value |
|---|---|
| Platform source | 7,178 lines of Python in `src/orchestrator` (kernel 1,402, persistence 1,955, platform api 422, `web.py` 1,546, `cli.py` 836, identity, main and config 748), plus 4,104 lines of migrations and 674 of templates |
| Platform test files | 66 `test_*.py` |
| Baseline gate | `make check` green in the worktree: 13,541 passed, 1 skipped, no tool skipped |
| Alembic | One head, `0047_retire_tracker_bindings`; 47 revisions; longest id 30 characters |
| Served schema | 70 paths, 75 path-and-method pairs, identical to `main`; production serves `a6e5329` |
| Route inventories | `test_scope_guards.py`'s POST and GET inventories match the code; the 9 `/review` GETs, `/health/*` and FastAPI's four documentation routes are outside them; no route uses PUT, PATCH or DELETE |
| Exception handlers | FastAPI's three defaults plus `APIAuthenticationError` and `DomainError`; no logging configuration, nothing forwards a server error |
| Production 5xx | None in the log since the 17:49 UTC restart (about five hours) |
| Reachable bare 500s | Non-ASCII CSRF token or session cookie (`web.py` `_require_form`, `_session_id`: `hmac.compare_digest` raises `TypeError`); `cli.py` `check-consistency` outside `_run`; a malformed `ORCHESTRATOR_API_URL` in the CLI; `new_max_attempts` above the `Integer` column's limit, on both the form and `/api` |
| Review-page refusals | Every refused submit but a grant refusal shows the person a raw JSON body |
| `IN (...)` CHECKs in the model | 39: 20 built with `!r`, 19 joined. One, `ck_work_package_revisions_verification_mode`, has a one-element vocabulary and renders the trailing comma; migration 0003 wrote it correctly, so the model has drifted from the database |
| Run-id columns | `landing_reverts.failed_run_id` is `BigInteger`; `dispatch_records.github_run_id` and `release_artifact_bindings.workflow_run_id` are `String` |
| Rules governing the layer | 29, plus 6 whose code is here but whose tests belong to other layers: 13 pinned, 10 partial, 2 missing, 4 procedures no test can hold |
| Repository acts without a grant | None. Two GitHub reads ask no grant first: the shared admission functions behind `/api/v1/estate-pr-merge-admission` and `/api/v1/inert-pr-merge-admission`, and the verifier's named-check read |

## Devon's decisions, 2026-11-04

1. **An unhandled exception raises one keyed alert-hub alert.** A catch-all handler returns the
   nested error shape (`internal_error`) and fires one alert keyed on route template and exception
   type. A person closes it after fixing the cause.
2. **Review-page refusals render as an HTML page**, showing the refusal's code and message and a
   link back. What a person can confirm doesn't change.
3. **Three parts**, each with its own plan, reviews and merge.

## Claude's calls, under Devon's rule that a deviation needs a documented reason

1. **The server's alert sender is a new alert-hub sender, `sds-orchestrator`**, so a 500 names the
   orchestrator, not the bridge lanes. alert-hub shows a sender's token once on its dashboard, so
   registering it is Devon's one task, generated at Part 2's step, as for `sds-bridge`. The sender
   has no `silent:` schedule, because it sends only when something breaks. The token is held by
   the orchestrator to call alert-hub, so it is a `coolify-env-held` destination: a row in this
   repository's `.cred-consumers.toml` without `rotated_by_sds` (no alert-hub rotation adapter
   exists, so its rotation stays a person's), a `.bws-secrets.toml` entry, and a `HELD_SETTINGS`
   line deployed before the row lands. Cost if wrong: one more credential on the manual
   checklist.
2. **The server's send is fire-only and can't change the response.** A new stateless
   `alert_hub_client.fire_once` (one `ConfinedClient` permitting only `POST /v1/alerts`, a timeout
   parameter, no 429 wait, never raises) runs as a `BackgroundTask` on the 500 response. With no
   token configured it logs `alert_not_configured` and sends nothing, which is what every test
   gets. The key is `alert_key("server-error", <route template or "unmatched">, <exception type>)`;
   a fresh request id goes in the log line, the alert message and the body's `error` object, never
   in the key. Nothing carries the exception's text or a traceback, which can hold a database URL
   or a token; uvicorn already logs the traceback. This is a cross-layer edit into the runtime
   layer. Cost if wrong: an alert-hub outage loses the alert, which the log still holds.
3. **The four deliberate 503 `DomainError`s keep their home.** Each is a configuration refusal a
   calling lane already treats as an error, and the lane's outcome alarm or dead-man check hears
   it. Cost if wrong: a misconfiguration is heard through a lane, not named by the server.
4. **The shared admission functions skip every GitHub read when the repository isn't granted
   `dependency_updates`**, a service every inert and estate branch requires. They answer with the
   grant refusal and the terms that need no GitHub read (estate, policy, record, window, pace and
   prior), with `head_sha` and the other remote fields as for an unreadable pull request. The
   change applies to the acting paths too, since they share the functions. Both landers already
   treat `landing_repository_not_granted` as an exception and never close such a pull request.
   What changes: an ungranted repository's answer is that refusal alone, which reads as an
   exception in both landers (silent), never held or settled, and is never closed; before, an
   answer that also carried a GitHub-read refusal such as `landing_checks_not_clean` could read as
   held (alerting). An ungranted repository is not an SDS target, so that is intended. That
   closes "every App call site stays bounded by a grant" for reads, the way the revert path
   already asks first. Cost if wrong: a person reading an ungranted repository's answer sees one
   refusal, not all of them.
5. **The verifier's named-check read keeps asking no grant**, recorded as a rule: it reads only
   the unit's own repository, for work already dispatched, and every landing re-asks the grant.
   Cost if wrong: a withdrawn grant still lets a dispatched unit be verified, never landed.
6. **App tokens stay installation-wide.** `credentials.md` documents that the dispatch App's reach
   is deliberately wider than its work and bounded at each call site, so this pass pins the call
   sites instead of narrowing the token. Cost if wrong: none beyond today.
7. **Transaction ownership gets a static check only if its predicate discriminates.** Services own
   transactions in 44 places, and some routes (`web.py` `retry`) never commit, so "routes commit,
   services don't" fails on day one. The predicate would have to model service-level owners
   (`transition_unit` against `transition_in_transaction`). Part 1 gives it one task; if no
   predicate fails on a seeded nested commit and passes today's code, the rule stays
   document-only and the CLAUDE.md rule says why. Cost if wrong: a nested commit is still found
   only by review.
8. **`/api` stays M2M-only at Traefik alone; the app doesn't duplicate it.** Production has
   recorded forward-auth `/api` routers (`driving-a-unit.md` #10 and #40), and the app already
   refuses forward-auth headers from any peer outside `trusted_proxy_ips`. An in-app copy would
   hold a second list of human routes that only the live Traefik file can confirm, and about 60
   test calls, including the protocol harness's intake, depend on human headers to `/api`. The
   rule stays a procedure, and its text says why. Cost if wrong: a Traefik router that stops
   stripping headers lets a signed-in person reach an `/api` route, as that person.
9. **Run-id columns refuse a 32-bit integer.** `String` can't overflow, and changing the two
   `String` columns is a migration with no benefit, so the guard refuses `Integer` and
   `SmallInteger` and accepts `BigInteger` or `String`. Cost if wrong: none.

## Part 1: kernel and persistence

No deploy: the database schema doesn't change, so Part 1 ships with Part 2's image, and
`/health/live`'s revision trailing `main` until then is deliberate.

- **The `IN (...)` CHECK shape.** Rewrite the 20 `!r` constraints in `persistence/models.py` to
  join quoted members explicitly, keeping the vocabulary name inside the `CheckConstraint(...)`
  call (`test_cross_boundary_vocabulary` treats a name there as database-pinned). A committed
  fixture holds every `IN` CHECK's `str(constraint.sqltext)`: 38 are unchanged, and
  `ck_work_package_revisions_verification_mode` changes to migration 0003's text. A test compares
  each model CHECK with the text its migration wrote. A guard test refuses an f-string with a
  `!r` conversion, `repr(` or `%r` whose literal text contains ` IN ` in `models.py` and in any
  revision numbered above 0047.
- **Run-id columns**, under call 9: a test finds every column whose name ends `run_id`.
- **Nullable UNIQUE constraints.** A test reads `Base.metadata`, resolves the columns of every
  unique constraint and unique index, expression indexes included, and requires
  `postgresql_nulls_not_distinct=True` or a named reason for any nullable column. It passes today:
  the one hit, `uq_release_artifact_source_tuple`, already sets it.
- **Revision ids.** A named test holds every revision id to 32 characters or fewer.
- **The `updated_at` trigger.** A test back-dates a row through SQL and asserts the trigger
  overwrites it.
- **`TransactionClock`.** A named test asserts two reads in one transaction are equal and a new
  transaction reads later.
- **Transaction ownership**, under call 7.
- **Records:** the docstrings in `kernel/states.py` (OBSERVER is named in three places),
  `kernel/leases.py` (the hung-worker hole is closed), `kernel/secret_metadata.py` (three
  ingresses) and `config.py` (four JSON settings); `local-development.md`'s test-database entry
  (shared schema, `-n auto`, 47 migrations), Python floor and lane count; the CLAUDE.md rules for
  `IN` CHECKs, run ids, nullable UNIQUE, revision ids, the trigger, `TransactionClock`,
  transaction ownership and the shared test database, rewritten as they stand.

## Part 2: API, identity, CLI and config

Deploys.

- **The 500 home**, under decision 1 and calls 1 and 2. `main.py`'s inline error body and status
  map become `error_body` and `status_for`, which the catch-all and Part 3's page reuse. Logging
  is configured with an uppercased level (Coolify injects `LOG_LEVEL=info`). A test asserts the
  handlers beyond FastAPI's three defaults are exactly `APIAuthenticationError`, `DomainError` and
  `Exception`. Tests use `raise_server_exceptions=False` and an injected transport, and prove the
  500 body is the same whether alert-hub accepts, refuses, times out or has no token.
- **Provisioning the sender**, in order: Devon registers `sds-orchestrator` and the generated task
  stores its token in BWS from the clipboard, printing only the secret's id; `HELD_SETTINGS` gains
  the setting (and `test_held_credentials_api.py` its key) in the deploy; the Coolify env write is
  its own step, under `credentials.md`'s `/envs` rules (both `is_preview` rows); then the
  `.cred-consumers.toml` and `.bws-secrets.toml` rows land.
- **Grant-first admission reads**, under call 4. `test_a_retired_members_hold_still_refuses_and_its_name_is_case_folded`
  is rewritten to grant the member under test, and the ungranted-repository tests gain a "no
  gateway read" assertion.
- **The CLI** wraps `check-consistency` in `_run` and catches `httpx.InvalidURL`, so every command
  exits 1 with `code=… message=…`.
- **`services/github_app.py`** catches `(httpx.HTTPError, httpx.InvalidURL, ValueError)` as the
  rule says.
- **Retry attempts** are bounded at `2**31 - 1`, the column's limit, on `/api`'s
  `RetryAuthorizationRequest` and the form (Part 3's form edit lands here with it). The served
  schema gains a `maximum`.
- **`tests/architecture/test_platform_edges.py`** states the layer's surface in the shared
  `edge_table.py` pattern:
  - The mint: one POST to `/app/installations/{id}/access_tokens` on `https://api.github.com`, no
    body.
  - The exact callers of `token_provider_for` and `github_app_credentials_for`, each bound to a
    grant or allowlist check or listed with its reason.
  - The CLI's one HTTP client, every path under `/api/v1/`.
  - The `Settings` fields, the `SecretStr` subset, and the raw environment readers
    (`ORCHESTRATOR_REVISION`, the CLI's three, `LOG_LEVEL`).
  - The filesystem reads (registry bundle, templates, Alembic files).
  - The unauthenticated routes: `/health/live`, `/health/ready`, `/openapi.json`, `/docs`,
    `/docs/oauth2-redirect`, `/redoc`.
  - The handler table and the alert-hub send.
  - A `test_control_*` per row, and a population check tied to `layers.toml`.
- **Inventories** gain the `/review` GETs (from `web_router.routes`, since the router is out of
  the schema) and the `/health` GETs, and a method guard walking the OpenAPI paths, the web router
  and the documentation routes refuses any PUT, PATCH or DELETE route. It never walks `app.routes`,
  which holds nested routers under the pinned FastAPI.
- **Word-guard exemptions:** ws32's whole-file entries for `config.py` and `persistence/models.py`
  become a (path, label) map: `config.py` to `dispatch` and `factory-runner`, `models.py` to
  `dispatch` and `deploy`, each with a check that the label still occurs. ws33's whole-text scan
  of `config.py` stays as it is, and the rule says why.
- **The protocol public-surface scan** covers all of `tests/protocol/`, with
  `tests._support.review_forms` (which writes nothing) allowed.
- **`OUTBOUND_ALLOWLIST`** gains the server's first alert egress, with its reason; the runtime
  layer's `test_runtime_edges.py` and `tests/alert_hub_client/` gain `fire_once`.
- **Records:** ADR-0006's `_bump_policy_approval` reference; `architecture-guards.md`'s layer and
  unit counts and edge-test list; `authentication.md`'s two rotation agent-id settings;
  `test_layer_manifest.py`'s docstring; `main.py`'s comment that `include_router` copies routes;
  the CLAUDE.md test name ending `_and_not_the_command_term`; the CLAUDE.md rules for exception
  handlers, `/api` (call 8), App call sites and the verifier's read (call 5), all rewritten as they
  stand; `layers.toml` for the new files.

## Part 3: review pages

Deploys.

- **CSRF and session parsing** catch the `TypeError` a non-ASCII value raises, as a refusal.
- **Every web POST calls `_require_form`**: it holds today (17 of 17), so an AST test pins it.
- **The HTML refusal page**, under decision 2. A custom `route_class` on `web_router` renders
  `DomainError`, `APIAuthenticationError` and `RequestValidationError` raised in a `/review`
  handler or its dependencies, after rolling the session back, with the status `status_for`
  gives. `/review` GETs render it too. A 500 on `/review` stays Part 2's JSON `internal_error`.
  The one existing catch, the grant refusal on the decomposition page, keeps its in-page message.
  About 25 assertions that read a JSON refusal body, in 8 `tests/web` files, `review_forms.py`,
  and two `tests/api` files, move to the page; most of those files belong to other layers.
- **Records:** `web.py`'s "five of the eight kinds" comment (ten kinds); ADR-0006's amendment for
  the page; the final docs pass.

## Method

Each part runs subagent-driven: a per-task implementer and review (Sonnet), a final whole-branch
review on Opus, one fix wave, a scoped re-review, then merge. Each part ends with a mutation pass
over its rules, every survivor killed or fixed. Parts 2 and 3 deploy with
`scripts/deploy_orchestrator.py`, after checking that no rotation unit is ready or claimed and no
dispatched run is live, away from the bump driver's :50 run, and read the served `openapi.json`
afterwards.

## Live proof

- Part 1: none beyond the deployed image; the schema doesn't change.
- Part 2: the served `openapi.json` (the retry `maximum`) and `/health/live` revision; the
  `HELD_SETTINGS` report through `GET /api/v1/held-credentials` showing the sender's fingerprint.
  The 500 alert has tests only unless Devon approves a staged proof; a route that raises on demand
  would itself be a new surface, so the default is none. Grant-first admission is proven by the
  next lander pass reading a granted repository with an unchanged answer.
- Part 3: a person's next refused submit on `/review`, with no staged proof by default.

## After the pass

CLAUDE.md's rules for this layer rewritten as they stand, old text to
`docs/history/claude-md-invariants-archive.md`; `architecture-guards.md`; ADR amendments for the
server's alert edge and the refusal page (ADR-0006); the SDS Layer Map's Core platform card
re-measured; one sentence in the year plan doc; a memory entry. Backlogged, not built: the
`sds-bridge` sender token is registered in no `.cred-consumers.toml`.
