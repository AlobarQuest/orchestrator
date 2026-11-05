# Platform layer Part 2: API, identity, CLI and config

Spec: `docs/superpowers/specs/2026-11-04-platform-layer-design.md`, "Part 2", Devon's decision 1,
Claude's calls 1 to 6 and 8, and "Live proof". Branch `platform-part-2` in `.worktrees/platform`,
at `origin/main` `ee2131f` (Part 1 merged), its own `.venv` (Python 3.14.3) and database
`orchestrator_test_platform`. Tests need `TEST_DATABASE_URL`, `ORCHESTRATOR_DATABASE_URL`,
`SECURITY_STANDARDS_DIR` (`$PWD/tests/fixtures/security-standards`) and `PGTZ=UTC`.

Tasks run one at a time in this worktree, so two pytest suites never share the database. Each task
is test-first where it adds a test, shows the new test failing on a deliberately broken input
before it passes (a control), ends with `make check` green, and is committed on its own. Each gets
an implementer and a reviewer (Sonnet); the final whole-branch review runs on Opus. Part 2 deploys
(Task 11), and Part 1's image ships with it.

New files go in `layers.toml`'s `platform` layer unless a task says otherwise
(`test_layer_manifest.py`). Under `src/orchestrator/` no new identifier or code string may contain
`publisher` (`test_application_defines_no_event_publisher_or_dispatch_objects`) or a ws32, ws33 or
ws34 forbidden term (`dispatch`, `deploy`, `coolify`, `factory-runner`, `workflow_dispatch`,
`merge_to_main` and the rest in the tests).

**Secrets in this part.** The `sds-orchestrator` sender token never appears in a tool argument,
a command line, a log line, a commit, or any output. It moves only inside one process at a time:
clipboard to BWS (Task 10, step 2), BWS to Coolify (Task 10, step 4). Every script prints ids,
`key`/`is_preview` pairs, HTTP status codes, and `sha256` prefixes with lengths, nothing else.
infraops' env tools are not used for it, because their arguments and responses are transcript.

## Names fixed by this plan

| Thing | Name |
|---|---|
| `Settings` field | `alert_hub_token: SecretStr \| None = None` in `src/orchestrator/config.py` |
| Environment variable | `ORCHESTRATOR_ALERT_HUB_TOKEN` (passes ws32, ws33 and ws34; `SECRET_KEY_PARTS` governs observation metadata keys, not settings, so `token` in the name is fine, as `change_record_token` already is) |
| alert-hub sender | `sds-orchestrator`, no `silent:` schedule |
| User-Agent | `sds-orchestrator/1 (+AlobarQuest/orchestrator)` (alerts.alobar.net is behind Cloudflare, which refuses default Python agents) |
| Alert key | `alert_key("server-error", <route template or "unmatched">, <exception type name>)` |
| Severity | `warning` (alert-hub accepts `critical`, `warning`, `info`, `~/Projects/alert-hub/src/ingest/validate.ts`; every SDS sender uses `warning`) |
| Error helpers | `src/orchestrator/api/error_shape.py` (new): `error_body`, `status_for` |
| Server alert send | `src/orchestrator/api/server_errors.py` (new): the catch-all handler, its alert config and the send |
| Client function | `alert_hub_client.fire_once` |
| Column limit | `INTEGER_COLUMN_MAX: Final = 2**31 - 1` in `src/orchestrator/api/schemas/common.py` |
| BWS secret | `ALERT_HUB_SENDER_TOKEN_SDS_ORCHESTRATOR`, project `Ops / Platform` (ruling R2) |

`error_shape.py` and not `api/errors.py`, so it can't be confused with `orchestrator/errors.py`
(where `DomainError` lives, and which the kernel imports).

## Task 1: factor the error shape out of `main.py`

Files: `src/orchestrator/api/error_shape.py` (new), `src/orchestrator/main.py`,
`tests/api/test_api_errors.py`, `layers.toml`.

1. Test first, against today's code: a parametrized test in `tests/api/test_api_errors.py` pins
   the status every `DomainError` code gets through the app (a `*_not_found` code 404; the four
   503 codes in `main.py` today, `csrf_unavailable`, `factory_policy_invalid`,
   `factory_policy_version_unsupported`, `named_check_observation_unavailable`; the four 403 codes
   `role_forbidden`, `human_actor_required`, `csrf_rejected`, `intake_registrar_invalid`; any
   other 409), and the body: `{"error": {"code", "message"}}` plus `recovery`, `current_state` and
   `current_version` only when set. Raise each through a throwaway route added to a `create_app()`
   instance in the test. Also pin the 401 body and `WWW-Authenticate: Bearer` for both
   `APIAuthenticationError` messages. It passes before and after; the control is a copy of the
   map with one 503 code moved to 409, which the test's table must refuse (assert the helper's
   answer, not only the route's).
2. Move the status map into `status_for(code: str) -> int` and the body into
   `error_body(code: str, message: str, **detail: object) -> dict[str, object]` (drops `None`
   values), both in `error_shape.py`. Both handlers in `create_app` call them. Part 3's refusal
   page will import them; nothing else changes.
3. `main.py`'s comment at lines 35 to 38 says `include_router` copies routes. Under the pinned
   FastAPI 0.159.1 `create_app().router.routes` holds `_IncludedRouter` objects; rewrite the
   comment to say routers are included per domain so each domain's router keeps its own
   `.routes`, and that inventories read `openapi()["paths"]` and the domain routers, never
   `app.routes`.

## Task 2: `alert_hub_client.fire_once`

Files: `src/alert_hub_client/__init__.py`, `tests/alert_hub_client/test_client.py`,
`tests/architecture/test_runtime_edges.py`. A cross-layer edit into the runtime layer (spec
call 2).

1. Tests first, in `tests/alert_hub_client/test_client.py`, each with an `httpx.MockTransport`:
   - an accepted send (202) returns `True` and posts exactly one `POST /v1/alerts` with
     `kind: alert`, `status: firing`, the given severity, title, clipped message and key, and
     `Authorization: Bearer <token>`;
   - a 401, a 500, a 429 and a transport that raises `httpx.ConnectTimeout` each return `False`,
     raise nothing, and make exactly one request (no 429 wait: pass a `sleep` spy that fails the
     test if called, or assert no `sleep` parameter exists);
   - a key outside `KEY_GRAMMAR` returns `False` and sends nothing;
   - an unusable base URL (patch `BASE_URL` to a malformed host) returns `False`;
   - no log record carries the token (capture with `caplog`, assert the token string is absent).
   Each fails before step 2 (the function doesn't exist).
2. Add
   `fire_once(token: str, key: str, alert: Alert, *, user_agent: str, timeout: float,
   severity: str = "warning", transport: httpx.BaseTransport | None = None,
   logger: logging.Logger = LOG) -> bool`.
   It is stateless: no state file, no `refire`, no 429 retry. It builds its client through one
   module function shared with `KeyedAlerts.open_client`, so there is one permits predicate,
   `(method, path) == ("POST", ALERTS)`, and `timeout` is that function's parameter (today
   `open_client` passes `TIMEOUT_SECONDS`; it keeps doing so). It catches
   `(TransportFailure, ValueError)` at construction and `TransportFailure` at the request, logs
   `notify_failed` with the exception type or status as `KeyedAlerts._send` does, and returns
   `False`; it never raises. Reuse `_send`'s payload building rather than restating it (extract
   it as a module function taking the severity).
3. `test_runtime_edges.py`: capture `fire_once`'s predicate with `edge_table.captured_permits`
   (as the row captures `KeyedAlerts`') and assert `alert_routes(...) == {("POST", "/v1/alerts")}`,
   with a control that widens it. If the change adds an import to `alert_hub_client`, its row in
   `tests/architecture/test_out_of_process_isolation.py` gains the name with a reason; it should
   add none.
4. Update the module docstring's bullet list with one line for `fire_once`.

## Task 3: the 500 home

Files: `src/orchestrator/api/server_errors.py` (new), `src/orchestrator/main.py`,
`src/orchestrator/config.py`, `src/orchestrator/api/routes/rotation.py`,
`tests/api/test_server_errors.py` (new), `tests/api/test_held_credentials_api.py`,
`tests/architecture/test_wsp21_invariant_scan.py`, `layers.toml`.

Decision 1 and calls 1 and 2.

1. Tests first, in `tests/api/test_server_errors.py`, using `TestClient(app,
   raise_server_exceptions=False)` on a `create_app()` with a throwaway route that raises
   `RuntimeError("postgresql://user:hunter2@db/x")`:
   - the response is 500 and its body is `{"error": {"code": "internal_error", "message":
     <fixed text>, "request_id": <uuid4 hex>}}`;
   - the body, minus `request_id`, is identical when alert-hub accepts (202), refuses (401),
     times out (the transport raises `httpx.ReadTimeout`), and when no token is configured;
   - with a token and an accepting transport, exactly one `POST /v1/alerts` is made, its key is
     `alert_key("server-error", "/boom", "RuntimeError")` for the route template, and two
     requests to the same route send the same key (the request id is not in it);
   - a request with no matched route keys on `unmatched` (call the handler directly with a
     `Request` built from a scope that has no `route` entry, since no app route raises before
     matching);
   - the alert's title and message, the body and every log record hold the request id, the
     method, the route template and the exception type, and never the exception's text
     (`hunter2` appears nowhere) or a traceback;
   - with no token, nothing is sent (the transport raises `AssertionError` on any request) and
     one `alert_not_configured` record is logged;
   - `set(create_app().exception_handlers) - set(FastAPI().exception_handlers) ==
     {APIAuthenticationError, DomainError, Exception}`.
   Controls: each fails on today's code (bare 500 plain text, no handler).
2. Logging. In `server_errors.py`, a pure `log_level(value: str | None) -> int` that uppercases
   and maps through `logging.getLevelNamesMapping()`, falling back to `INFO` for an unknown or
   absent value (Coolify injects `LOG_LEVEL=info`, and `basicConfig(level="info")` raises at
   import, a crash loop). `main.py` calls `logging.basicConfig(level=log_level(os.environ.get(
   "LOG_LEVEL")))` once at module level, beside `app = create_app(...)`, not inside
   `create_app`. Without it the root logger drops INFO, so `alert_not_configured` and
   `notified` never reach the container log. Test `log_level` for `info`, `INFO`, `debug`,
   `bogus` and `None`.
3. The setting. `config.py` gains `alert_hub_token: SecretStr | None = None` with a comment:
   the `sds-orchestrator` sender's token, held to call alert-hub (a held copy, ADR-0055
   amendment 13), unset in every test, and unset means a 500 is logged and not sent.
4. The handler. `server_errors.py` holds:
   - a frozen `ServerAlertConfig(token: SecretStr | None, user_agent: str, timeout: float,
     transport: httpx.BaseTransport | None = None)` and `server_alert_config(settings) ->
     ServerAlertConfig`;
   - `async def server_error_handler(request, error) -> JSONResponse`: a fresh
     `uuid.uuid4().hex`, the route template from `request.scope.get("route")` (its `.path`) or
     `"unmatched"`, `type(error).__name__`; one `server_error` log line (request id, method,
     template, type; no traceback, which uvicorn already logs when the middleware re-raises);
     the body from `error_body("internal_error", ..., request_id=...)`; and, when the token is
     set, a `BackgroundTask` on the response that calls `fire_once` with a timeout of 5 seconds.
     With no token it logs `alert_not_configured` and attaches nothing. It never touches the
     database session.
   - `create_app(auth_config=None, alert_config: ServerAlertConfig | None = None)` stores the
     config on `application.state.alert_config` (default: a config with no token) and registers
     `server_error_handler` for `Exception`. Exception handlers get no `Depends`, so tests replace
     `app.state.alert_config`, never `dependency_overrides[get_settings]`. The module-level
     `app = create_app(load_auth_config(), server_alert_config(get_settings()))`; a
     `get_settings()` failure there is the boot failure it already is through `db.py`.
   - Two facts for the reviewer: Starlette's `ServerErrorMiddleware` awaits the handler's
     response, which runs its background task after the body is sent, then re-raises; and
     `BackgroundTask` is not in `test_wsp21_invariant_scan.py`'s `BACKGROUND_STARTERS`: it is
     one send after one response, not a concurrent actor, so ADR-0002's loop-free rule holds.
5. `HELD_SETTINGS` in `api/routes/rotation.py` gains
   `"ORCHESTRATOR_ALERT_HUB_TOKEN": "alert_hub_token"`, and its comment names both holders.
   `tests/api/test_held_credentials_api.py` gains a case setting both tokens and asserting both
   fingerprints, and one setting only the sender's. Control: the case fails before the line.
   This line must be in the deploy of Task 11 before the registry row of Task 12 lands (the
   executor refuses a held destination the report doesn't list).
6. `OUTBOUND_ALLOWLIST` gains `Path("src/orchestrator/api/server_errors.py")` with the reason:
   the server's first alert egress of its own, one `POST /v1/alerts` through `alert_hub_client`
   after a 500 response, keyed on route template and exception type; every other alert-hub
   entry's egress is a lane's.

## Task 4: grant-first admission reads (call 4)

Files: `src/orchestrator/services/landing/estate_landing_admission.py`,
`src/orchestrator/services/landing/inert_landing_admission.py`,
`tests/services/test_estate_landing_admission.py`,
`tests/services/test_inert_landing_admission.py`. Cross-layer (landing).

1. Tests first. The fake gateways record reads (`gateway.commit_reads` and the pull, compare,
   contents and run read lists); assert each is empty:
   - estate: `test_an_ungranted_repository_is_refused_not_granted` and
     `test_security_patches_alone_does_not_grant_this_lane` gain "no gateway read";
   - inert: `test_an_ungranted_repository_is_refused_not_granted`, the parametrized
     `test_a_fork_sync_needs_both_security_patches_and_dependency_updates` (with only
     `security_patches` there is no read; with only `dependency_updates` the pull request is read
     and the wholesale branch refuses as today), and the two tests asserting
     `LANDING_REPOSITORY_NOT_GRANTED in answer.refusals` near lines 1292 and 1304;
   - `test_a_retired_members_hold_still_refuses_and_its_name_is_case_folded`
     (estate, near line 1356) can't be rewritten to "grant the member under test": a retired
     member grants nothing, and `uq_sds_members_repository_lower` is a total unique index on
     `lower(repository)`, so no live member can sit beside it. Split it (ruling R5): the
     admission test asserts `(LANDING_REPOSITORY_NOT_GRANTED,)` and no commit read; a granted
     live member spelled `AlobarQuest/Change-Manager` and asked as `alobarquest/change-manager`
     keeps the case-folding kill (`LANDING_VERSION_CEILING_EXCEEDED` only); and a direct
     `members.holds_for` test in the membership layer's tests keeps "a retired member's hold is
     returned".
   They fail before step 2.
2. In both functions, compute `grant_term(MemberRecordGrants(session), repository,
   (DEPENDENCY_UPDATES_SERVICE,))` before any remote read. When it is not met, skip
   `_remote_terms` and `_revert_remote_terms` and use a `_RemoteTerms` whose term is
   `Term(False, ())` (no refusal of its own) with `head_sha=None`, `rollout_base_matches_pin=False`,
   `ceiling_breaches=()`, `update_bot_authored=False` (inert: `merge_method=SQUASH`,
   `wholesale=False`, as `_remote_terms` returns for an unreadable pull request, minus its
   refusal). The answer is
   then the grant refusal plus the terms that read no GitHub: estate, policy, record, class,
   window, pace and prior. When it is met, inert keeps today's second ask with
   `SECURITY_PATCHES_SERVICE` for a wholesale subject. Keep each function's refusal order. Say
   in a comment that the acting paths share these functions, that both landers already treat
   `landing_repository_not_granted` as an exception and never close such a pull request, and
   that this is the revert path's order (`revert_act._refuse_unless_permitted`).
3. `pr_merge_admission` reads no GitHub (its reads are in `pr_merge._act`, behind the grant), so
   it is out of scope; say so in the commit message.

## Task 5: the CLI and the App token mint catch what the rule says

Files: `src/orchestrator/cli.py`, `src/orchestrator/services/github_app.py`,
`tests/cli/test_cli_errors.py`, `tests/services/test_github_app.py`.

1. Tests first:
   - `check-consistency` through the Typer entrypoint (`CliRunner` on `cli.app`, never the bare
     function) against a transport answering 401, 503 with a plain-text body, and raising
     `httpx.ConnectError`: exit 1, stderr `code=… message=…`, no traceback. A divergent report
     still exits 1 after printing it, and a clean one exits 0.
   - `ORCHESTRATOR_API_URL` malformed (`http://exa mple`, a control character) and a unit id
     argument with a control character: exit 1 with `code=api_url_invalid`.
   - `GitHubAppTokenProvider` with a fake `httpx.post` raising `httpx.InvalidURL` and
     `UnicodeError`: `GitHubAppTokenError` with code `mint_request_error:<type>`.
2. `check-consistency`: run the request and the divergence check inside one `_run` operation
   (raise `typer.Exit(1)` after emitting a divergent report), or make `_run` return the
   operation's value; either way no `CliError` escapes it.
3. `cli.request` catches `(httpx.InvalidURL, ValueError)` around client construction and the
   request (both inside the existing `try`; the JSON parse stays outside it) as
   `CliError({"code": "api_url_invalid", "message": "API URL or path is not a valid URL"})`.
4. `github_app._mint` computes the assertion before the `try`, and catches
   `(httpx.HTTPError, httpx.InvalidURL, ValueError)` around `httpx.post`.

## Task 6: retry attempts bounded at the column's limit

Files: `src/orchestrator/api/schemas/common.py`, `src/orchestrator/api/schemas/lifecycle.py`,
`src/orchestrator/web.py`, `src/orchestrator/templates/unit.html`, `tests/api/test_lifecycle_api.py`,
`tests/web/test_human_actions.py`, and any pin found in step 0.

The spec's `RetryAuthorizationRequest` is `RetryCommand` (`api/schemas/lifecycle.py`), served at
`POST /api/v1/work-units/{unit_id}/retry-authorization`.

0. Grep `tests/` and `tests/fixtures/` for pins of `RetryCommand`'s schema or
   `exclusiveMinimum` (contract tests, `tests/cli/test_cli_contract.py`) and update them in this
   task.
1. Tests first: `new_max_attempts = 2**31` answers 422 on `/api` and on the `/review` form (the
   form answer is FastAPI's JSON 422 until Part 3), and `2**31 - 1` passes validation and reaches
   the service. A schema test asserts
   `create_app().openapi()["components"]["schemas"]["RetryCommand"]["properties"]["new_max_attempts"]["maximum"] == 2147483647`,
   and that `work_units.max_attempts` is an `Integer` column, so the bound and the column move
   together. Each fails before step 2 (today `2**31` reaches Postgres and is a bare 500).
2. `INTEGER_COLUMN_MAX: Final = 2**31 - 1` in `api/schemas/common.py`;
   `RetryCommand.new_max_attempts: int = Field(gt=0, le=INTEGER_COLUMN_MAX)`; the form's
   `Form(gt=0, le=INTEGER_COLUMN_MAX)` (`web.py`, `retry`); `unit.html`'s
   `new-max-attempts` input gains `max="2147483647"`. Not the envelope's
   `budgets.max_attempts`: a person's retry may exceed it.

## Task 7: inventories, the method guard, word-guard narrowing, the protocol scan

Files: `tests/architecture/test_scope_guards.py`, `tests/architecture/test_ws32_scope_guards.py`,
`tests/protocol/drills/test_drills_change_state_publicly.py`.

1. Inventories (`test_scope_guards.py`):
   - the GET inventory gains the nine `/review` GETs, read from `web_router.routes` (APIRoutes
     with `GET`), as the POST inventory reads its POSTs: `/review`, `/review/units/{unit_id}`,
     `/review/intakes/new`, `/review/intakes/{revision_id}`,
     `/review/staged-intakes/{staged_id}`, `/review/member-proposals/{proposal_id}`,
     `/review/decomposition-proposals/{proposal_id}`, `/review/units/{unit_id}/evidence-pack`,
     `/review/revisions/{revision_id}/evidence-pack`; and the two health GETs from the schema
     (`/health/live`, `/health/ready`). Confirm the exact paths from the router before writing.
   - a method guard: every method on every schema path, every `web_router` route, and the four
     documentation routes (found by `app.openapi_url`, `app.docs_url`,
     `app.swagger_ui_oauth2_redirect_url`, `app.redoc_url`, matched against the plain
     `starlette.routing.Route`s at the top of `create_app().router.routes`) is `GET`, `HEAD` or
     `POST`. It never descends into `app.routes`. Control: a throwaway `PUT` route on a copy of
     the web router, and one on the app, each fail it.
2. ws32 (`test_ws32_scope_guards.py`): remove `config.py` and `persistence/models.py` from
   `WS42_DISPATCH_PATHS` and add `LABEL_EXEMPT_PATHS: dict[Path, frozenset[str]] =
   {config.py: {"dispatch", "factory-runner"}, models.py: {"dispatch", "deploy"}}`. In
   `_find_matches`, a term in such a file is skipped only when every label it matched is in its
   set (the merge exemption's subset rule). Add: a rot check that each listed label still
   occurs in its file's terms, and controls (a `deploy` identifier in `config.py`'s text fed to
   the matcher is reported; a label no term matches fails the rot check). Before writing,
   confirm with the tokenizer which labels each file trips today. ws33's whole-text scan of
   `config.py` (`DISPATCH_EXEMPT_PATHS`) stays: it reads comments and docstrings, and
   `config.py`'s comments name `factory-runner-pilot.yml` on purpose. Task 9's CLAUDE.md text
   says so.
3. Protocol scan: `SCANNED` becomes every `.py` under `tests/protocol/` except the files in
   `SHARED_SETUP`; `SHARED_SETUP` and `ALLOWED_SUPPORT_MODULES` gain
   `tests/_support/review_forms.py` / `tests._support.review_forms` (it writes nothing, so
   `test_lease_expiry_is_the_only_write_the_shared_setup_makes` still reads
   `["expire_latest_claim"]`). Run it first: if `test_ws33_smoke.py` or
   `test_decomposition_supersession_route.py` trips `WRITES` on a non-session receiver (a
   `set.add`, a `dict.update`), report the hit to the controller rather than widening `WRITES`'
   exceptions. Update the module docstring ("the drills" becomes the protocol tests).

## Task 8: `tests/architecture/test_platform_edges.py`

Files: `tests/architecture/test_platform_edges.py` (new), `layers.toml`.

One test states what the Core platform layer reaches, in the `edge_table.py` pattern: rows as
data, each compared with the code, each with a `test_control_*` that widens the thing and is
reported, and a docstring with a "What this cannot see" section (URLs built at run time, calls
through an object, `import_module`). Rows:

1. **The mint.** `github_app.GITHUB_API_URL == "https://api.github.com"`; `github_app.py` makes
   exactly one HTTP call, `httpx.post`, to an f-string ending
   `/app/installations/{...}/access_tokens`, with no `json=`, `data=` or `content=` keyword
   (installation-wide reach, call 6). Control: a source with a body keyword or a second call.
2. **Token callers.** An AST scan of `src/orchestrator/` for calls to `token_provider_for` and
   `github_app_credentials_for`, keyed `(file, enclosing function)`, equals a table in which
   each entry names its bound (the grant or allowlist check it reaches) or its reason. Today's
   per-file counts, to confirm: `api/routes/execution.py` 1 and 1, `api/routes/landing.py` 10
   and 13, `api/routes/verifier.py` 1 and 1. The verifier's named-check read is listed with
   call 5's reason. Control: a planted call in a fake module is reported.
3. **The CLI.** `cli.py` builds one `httpx.Client` (`base_url` from `CliSettings`), and every
   literal or f-string path passed to `request(` starts `/api/v1/`. Control: a planted
   `request("GET", "https://api.github.com/x")`.
4. **Configuration.** `set(Settings.model_fields)` equals the listed names (today's 35 plus
   `alert_hub_token`), `env_prefix == "ORCHESTRATOR_"`, and the `SecretStr` subset is
   `{github_app_private_key_b64, app_brain_read_key, change_record_token, proxy_marker,
   csrf_secret, alert_hub_token}`. Raw environment readers across the layer's source (AST
   `os.environ`, `os.environ.get`, `os.getenv`) equal `{api/health.py: ORCHESTRATOR_REVISION,
   cli.py: ORCHESTRATOR_API_URL, ORCHESTRATOR_API_TOKEN, ORCHESTRATOR_API_CREDENTIAL_KEY_ID,
   main.py: LOG_LEVEL, migrations/env.py: ORCHESTRATOR_DATABASE_URL}`. Controls: a planted field,
   a planted `os.environ["X"]`.
5. **Filesystem reads.** Calls to `read_text`, `read_bytes`, `open(` and `Jinja2Templates(` in
   the layer's source equal: `identity/registry.py` (the registry bundle), `web.py` (the
   templates directory), `cli.py` (`--data @file`, `--context @file`), and the Alembic files
   read through `alembic.config.Config` in `api/health.py`. Confirm the set by the scan first.
   Control: a planted `Path("x").read_text()`.
6. **Unauthenticated routes.** Every schema path and `web_router` route whose dependant tree
   (walked recursively) doesn't include `api.dependencies.get_actor`, plus the four
   documentation routes, equals `{/health/live, /health/ready, /openapi.json, /docs,
   /docs/oauth2-redirect, /redoc}`. Control: a throwaway route with no actor dependency.
7. **Handlers and the alert send.** The handler set beyond FastAPI's defaults is
   `{APIAuthenticationError, DomainError, Exception}`; `status_for`'s non-default codes equal the
   table; `fire_once`'s captured predicate admits only `POST /v1/alerts` on
   `https://alerts.alobar.net`; `server_errors.py` is the one platform file importing
   `alert_hub_client`. Controls: an added handler; a widened predicate.
8. **Population.** Every file `scripts.layers.load()["platform"]` assigns under `src/` and
   `migrations/env.py` is named by a row or by a `REACHES_NOTHING` list (kernel, persistence,
   `errors.py`, `clock.py`, `db.py` and the rest), so a new platform file fails until placed.

## Task 9: records

Files: `docs/operations/architecture-guards.md`, `docs/operations/authentication.md`,
`docs/operations/rotation-executor.md`, `docs/decisions/0006-human-gates-browser-only.md`,
`docs/decisions/0021-two-signal-ledgers-split-by-whether-a-signal-needs-a-decision.md`,
`tests/architecture/test_layer_manifest.py` (docstring), `CLAUDE.md`,
`docs/history/claude-md-invariants-archive.md`, `layers.toml`.

1. `architecture-guards.md`: "The edge tables" lists all eleven edge tests
   (`test_admission_edges.py`, `bridge`, `execution`, `intent`, `landing`, `membership`,
   `platform`, `release`, `rotation`, `runtime`, `verification`), not three, and "Each lane
   layer" becomes "Each layer"; "The layer manifest" says fifteen layers, thirteen units of
   ownership plus `tooling` and `records` (count `layers.toml`'s sections and say what you
   counted). Add a short section on the method guard and the `/review` inventories.
2. `test_layer_manifest.py`'s docstring: thirteen units of ownership plus tooling and records.
3. `authentication.md`: add `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID` and
   `ORCHESTRATOR_ROTATION_KEYCHAIN_EXECUTOR_AGENT_ID` (what each confines, and that boot refuses a
   credential holding an executor profile its setting doesn't name), and
   `ORCHESTRATOR_ALERT_HUB_TOKEN` (optional; unset means 500s are logged, not sent).
4. `rotation-executor.md`, "Held copies": the orchestrator holds two copies,
   `ORCHESTRATOR_CHANGE_RECORD_TOKEN` and `ORCHESTRATOR_ALERT_HUB_TOKEN`; the second has no SDS
   rotation (no alert-hub adapter), so its row carries no `rotated_by_sds` and a person rotates
   it from the manual checklist.
5. ADR-0006 line 159: `decomposition._bump_policy_approval` becomes the bump branch of
   `decomposition._policy_approval` (`_bump_policy_outcome`), and the grant bullet says the grant
   is asked earlier, in `submit_decomposition_proposal`, as `_bump_policy_outcome`'s docstring
   says.
6. ADR-0021 amendment 4 (2026-11-05, Devon's decision 1 and calls 1 and 2 of this spec): an
   unhandled exception in the orchestrator is a signal whose home is alert-hub, sent by the
   server itself as `sds-orchestrator`, one keyed alert per route template and exception type,
   fire-only after the response, closed by a person after the fix; the four deliberate 503s stay
   heard through their lanes (call 3). ADR-0021, because amendment 1's table already says "a
   person goes untold → alert-hub" and this is the orchestrator's first send of its own. Not
   ADR-0002: one send after one response is not a loop or a background actor.
7. CLAUDE.md, rewritten as they stand, each naming its test, old text to the archive under
   `### Platform Part 2, <merge date>`:
   - the exception-handler rule (archive #44): the handlers beyond FastAPI's defaults are
     exactly `APIAuthenticationError`, `DomainError` and `Exception`; a 500 is the nested
     `internal_error` body and one keyed alert (`test_server_errors.py`); route parsing still
     raises `DomainError`, and a service still rejects every value a DB CHECK would;
   - the `/api` rule (call 8): M2M-only at Traefik alone, deliberately not duplicated in the app,
     with the reason (an in-app list of human routes only the live Traefik file can confirm,
     and the test harness's human `/api` calls);
   - the App call-site rule (call 5 and Task 4): every App call site is bounded by a grant or
     allowlist check before any read (`test_platform_edges.py`), except the verifier's
     named-check read of the unit's own repository for dispatched work;
   - the word-guard rule: ws32 exempts `config.py` and `models.py` by label, ws33 still scans
     `config.py`'s whole text, and why;
   - the held-copy rule: `HELD_SETTINGS` names two settings.
   The CLAUDE.md test name the spec lists (`…_and_not_the_command_term`) is already correct at
   line 315; record it as verified, change nothing. Everything stays below
   `<!-- code-standards:end -->`.
8. `layers.toml` `platform`: `src/orchestrator/api/error_shape.py`,
   `src/orchestrator/api/server_errors.py`, `tests/architecture/test_platform_edges.py`,
   `tests/api/test_server_errors.py` (each added in its own task; this step confirms none is
   missing).

## Closing (before merge)

1. Final whole-branch review on Opus against the spec's Part 2 and this plan; one fix wave;
   scoped re-review.
2. Mutation pass, clearing `__pycache__` around each mutate and restore, recording which tests
   fail for each. Kill or fix every survivor. Mutants:
   - the `Exception` handler unregistered;
   - `str(error)` put in the 500 body, the alert message and the log line;
   - the request id put in the alert key;
   - `fire_once` re-raising a `TransportFailure`; a 429 wait added to it; its predicate widened
     to `GET`;
   - the no-token branch removed (sends with an empty bearer);
   - `.upper()` and the mapping removed from `log_level`;
   - the grant short-circuit removed in the estate function; keyed on `security_patches` in the
     inert function;
   - `check-consistency` unwrapped from `_run`; the `InvalidURL` catch removed from `cli.py`;
     from `github_app.py`;
   - `le=` dropped from `RetryCommand`; from the form;
   - a `PUT` route added; a `/review` GET added; a health GET added;
   - a `deploy_x` identifier added to `config.py`; the last `factory-runner` occurrence removed
     from `config.py`;
   - a `session.commit()` added to `test_ws33_smoke.py`;
   - a `token_provider_for` call added in `web.py`; an `os.environ` read added in `web.py`; a
     `Settings` field added;
   - the `HELD_SETTINGS` line removed;
   - `json={}` added to `_mint`;
   - an unauthenticated route added.
3. Push, open the pull request, watch CI with `gh pr checks --watch --interval 30`, merge when
   green, confirm `MERGED` (`gh pr view --json state`), and read `main`.

## Task 10: provisioning the sender (after merge, before the deploy where possible)

Ruling R1: the env write comes before Task 11's swap, so the swap loads it and no extra restart
is needed. An image without the field ignores an unknown `ORCHESTRATOR_` variable
(pydantic-settings reads only declared fields), so an unplanned restart of the old image is
harmless. If Devon's step isn't done by deploy time, deploy without it (500s are logged, not
sent) and do steps 3 to 5 later, restarting under Task 11 step 1's checks.

1. **Devon's step, as a generated task with instructions.** On `https://alerts.alobar.net/`,
   create the source `sds-orchestrator` with no `silent:` schedule, copy the token it shows once,
   then run the command in step 2. The controller writes the task; Devon's chat "yes" is the
   confirmation.
2. **The store command (the session writes it to the scratchpad; Devon runs it).** One Python
   process, in the worktree venv (`bitwarden-sdk` is in the `rotation-worker` group, which `dev`
   includes): read the clipboard with `subprocess.run(["pbpaste"], capture_output=True)`;
   shape-check it in-process (non-empty, no whitespace, printable ASCII, a plausible length; a
   failure prints only which check failed); read the broad BWS identity from Keychain
   `BWS_ACCESS_TOKEN_VPS_BACKUP` with `/usr/bin/security find-generic-password -w` into a
   variable; create `ALERT_HUB_SENDER_TOKEN_SDS_ORCHESTRATOR` in `Ops / Platform` through the
   SDK; empty the clipboard (`pbcopy` from empty input); print only the new secret's id and
   `sha256[:8]`. Never `bws secret create`, which puts the value in its argument list.
3. **Confirm the source exists** with `mcp__alert-hub__list_sources` (its response holds no
   token): `sds-orchestrator` is listed and has no silent schedule.
4. **The Coolify env write (the session's step).** One Python process, printing nothing but
   statuses, `key`/`is_preview` pairs and the value's `sha256[:16]` and length:
   - read the sender token by its new UUID and a Coolify token with the broad identity, through
     the SDK;
   - the Coolify token (ruling R3): try `prod-coolify-api-token` (`bbd71f41-…`, `Ops / Platform`,
     the token infraops uses for its writes, though this repository's `.bws-secrets.toml` calls
     it read-only) with the real call; on 401 or 403 stop and report, do not try other tokens
     without the controller;
   - `POST /api/v1/applications/eqj5l7k705fhi12x9i74fqf0/envs` with `key`
     `ORCHESTRATOR_ALERT_HUB_TOKEN` over the tailnet (`http://100.92.138.19:8000`), which
     creates both rows; then `GET` the envs and print, for rows with that key only, `key` and
     `is_preview`, never `value` or `real_value`. Two rows, `is_preview` false and true, is the
     pass; one row means `PATCH` the other with `is_preview` set; a 500 falls back to delete and
     recreate (credentials.md #12);
   - if the token turns out to write, correct `.bws-secrets.toml`'s "READ-ONLY" description of it
     in Task 12, with the date measured.
5. Nothing reads the sender token at runtime from BWS. Its readers are this step and a person's
   rotation.

## Task 11: deploy and live proof

1. Before `prepare`: no rotation unit `ready` or `claimed` (read the status ledger with the
   SYSTEM bearer, `include_inactive=true`, filtered to rotation units), no dispatched run
   unclaimed (the script checks), and not within ten minutes of :50 (the bump driver) or :15 and
   :35 (the landers, whose next pass is the proof in step 4). `alert_hub_client` ships in the
   image as `estate_clients` already does (`COPY src ./src`, installed by `uv sync`).
2. Deploy with `scripts/deploy_orchestrator.py` from a worktree at the merge commit, by
   `docs/operations/deploy.md` (`prepare`, the printed infraops calls, `recheck` immediately
   before the swap, `finish`). No migration is expected (Part 1 and Part 2 change no schema);
   `prepare` decides.
3. Proof, read after `finish`:
   - `/health/live` serves the merge commit;
   - `https://sds.alobar.net/openapi.json`:
     `components.schemas.RetryCommand.properties.new_max_attempts.maximum == 2147483647`;
   - the held report (ruling R4): `GET /api/v1/held-credentials` answers only the configured
     executor, whose WORKER bearer is read only inside the VM. Run one read there:
     `orb -m rotation-executor -u root runuser -u rotation -- env -i PATH=/usr/bin:/bin
     HOME=/var/lib/rotation-worker /opt/rotation-worker/venv/bin/python -c '<read>'`, where the
     read builds the executor's own `OrchestratorClient` from `/etc/rotation-worker/config.toml`
     the way `rotation-worker run` does and prints, per key of `held()`, only `sha256_prefix`
     and `length`. It must list `ORCHESTRATOR_ALERT_HUB_TOKEN` with the prefix Task 10 step 4
     printed. If that read can't be built without new code, the fallback is a `vps_exec` that
     prints only whether the variable is set in the running container
     (`test -n "$ORCHESTRATOR_ALERT_HUB_TOKEN" && echo set`), and the report says the weaker
     proof was used;
   - grant-first admission: before the deploy, record `GET /api/v1/inert-pr-merge-admission`
     and `GET /api/v1/estate-pr-merge-admission` for one open pull request in a granted
     repository and one in an ungranted repository the App can see; after it, the granted
     answer is unchanged and the ungranted answer holds the grant refusal alone with `head_sha`
     null. Then the next lander passes (`~/Library/Logs/inert-landing.log` at :35,
     `estate-landing.log` at :15) show the same per-repository outcomes as the pass before.
   The 500 alert has tests only (spec, "Live proof"); no staged proof unless Devon approves one.

## Task 12: the registry rows (a second, small pull request)

After Task 11's deploy serves the `HELD_SETTINGS` line.

1. infraops first (ruling R6): `CLASS_POLICY` in
   `~/Projects/infraops-mcp-server/src/security-drift/cred-rotation.ts` has no class for an
   alert-hub sender token, so a row with a new class raises `cred.unknown-class` nightly. Add
   `alert-hub-sender-token` (365 days, no landmines) in an infraops pull request through its own
   process, and land it before step 2.
2. This repository:
   - `.cred-consumers.toml`: a `[[credential]]` `id = "alert-hub-sds-orchestrator-sender"`,
     `class = "alert-hub-sender-token"`, `provider = "alert-hub"`, `provider_identity` naming the
     sender, `bws_uuid`, `fingerprint_sha256_8` from Task 10 step 2, `created`, `disposition =
     "reissue"`, no `rotated_by_sds`; consumers: the `bws-secret` keeper, and a
     `coolify-env-held` destination `instance = "prod"`, `resource_type = "application"`,
     `uuid = "eqj5l7k705fhi12x9i74fqf0"`, `key = "ORCHESTRATOR_ALERT_HUB_TOKEN"`, with a note that
     no adapter rotates it;
   - `.bws-secrets.toml`: a `[[secret]]` with the UUID, name and project, and a comment naming
     its readers (Task 10 step 4 and a person's rotation; nothing scheduled);
   - the `bbd71f41` description correction, if Task 10 measured a write.
   Value-free by construction; the session-end scanner must stay clean.
3. Pull request, CI with `gh pr checks --watch --interval 30`, merge, confirm `MERGED`. No deploy
   (no code changes).

## After the part

The SDS Layer Map's Core platform card and the year plan sentence wait for Part 3. The memory
entry for Part 2's progress is the controller's. Backlogged, not built: the `sds-bridge` sender
token has no `.cred-consumers.toml` row (spec, "After the pass").

## Rulings this plan makes that the spec didn't settle

- **R1. Env write before the swap.** Cost if wrong: none found; an old image ignores the variable.
- **R2. The sender token lives in `Ops / Platform`, not `SDS Operator`** (where `sds-bridge`'s
  is), because no lane reads it and the Coolify token the write needs is there too, so one
  identity does both. Cost if wrong: it departs from the bridge precedent; a later lane that
  needs it would need a grant.
- **R3. `bbd71f41` is the Coolify token tried, and a refusal stops for the controller.** Cost if
  wrong: a stop and one question.
- **R4. The held-report proof runs inside the VM**, with a set/unset container check as the
  labelled fallback. Cost if wrong: a weaker proof, said as such.
- **R5. The retired-member admission test is split** (spec: "grant the member under test",
  which a retired member can't be). Cost if wrong: none; each property keeps a test.
- **R6. A new infraops credential class** before the row lands. Cost if wrong: one infraops pull
  request; the alternative is a standing WARN.
- **R7. Severity `warning`.** Cost if wrong: a 500 doesn't page if alert-hub's rules page only on
  `critical`.
- **R8. `log_level` falls back to `INFO`** for an unknown value rather than refusing to boot.
  Cost if wrong: a typo in `LOG_LEVEL` logs at INFO.
- **R9. `request_id` is a field of the nested `error` object.** Cost if wrong: a client that
  asserts the exact error keys; none does today.
