# Platform layer Part 3: review pages

Spec: `docs/superpowers/specs/2026-11-04-platform-layer-design.md`, "Part 3: review pages", Devon's
decision 2, "Live proof" and "After the pass"; builder check items 20 to 22. Branch
`platform-part-3` in `.worktrees/platform`, from `origin/main` (Parts 1 and 2 merged; the worktree's
`platform-part-2-registry` branch stays on its own pull request and is not part of this one), its
own `.venv` (Python 3.14.3) and database `orchestrator_test_platform`. Tests need
`TEST_DATABASE_URL`, `ORCHESTRATOR_DATABASE_URL`, `SECURITY_STANDARDS_DIR`
(`$PWD/tests/fixtures/security-standards`) and `PGTZ=UTC`.

Tasks run one at a time in this worktree, so two pytest suites never share the database. Each task
is test-first where it adds a test, shows the new test failing on a deliberately broken input
before it passes (a control), ends with `make check` green, and is committed on its own. Each gets
an implementer and a reviewer (Sonnet); the final whole-branch review runs on Opus. Part 3 deploys
(Task 6).

New files go in `layers.toml`'s `platform` layer (`test_layer_manifest.py`); a file under
`src/orchestrator/templates/` or `tests/_support/` is already covered by its `**` pattern. Under
`src/orchestrator/` no new identifier or code string may contain `publisher` or a ws32, ws33 or
ws34 forbidden term (`dispatch`, `deploy`, `coolify`, `factory-runner`, `workflow_dispatch`,
`merge_to_main` and the rest in the tests).

**What a person can confirm does not change** (decision 2, ADR-0006). No form, token, button,
route, status code or service call changes. What changes is the body a refusal is shown in: today
every `/review` refusal but one is FastAPI's JSON, and after this part it is an HTML page. A
reviewer who finds any other behaviour change in the diff stops the task.

## Facts this plan rests on (read from the code at `origin/main`)

| Fact | Where |
|---|---|
| `_require_form` calls `hmac.compare_digest(expected, signature)` OUTSIDE its `try`, so a token whose payload half parses and whose signature half is non-ASCII raises `TypeError` (a 500) | `src/orchestrator/web.py:192-201` |
| `_session_id` catches only `ValueError` around its `compare_digest`, so a non-ASCII cookie signature raises `TypeError` on every `/review` page | `web.py:129-135` |
| 26 `/review` routes (9 GET, 17 POST); all 26 handlers call `_human(actor)`; all 17 POSTs call `_require_form(` | `grep -c` over `web.py` |
| `_require_form` is pinned today only by a monkeypatch in one test, never by a structural test; `test_scope_guards.py` and `test_platform_edges.py` don't name it | `tests/web/test_human_actions.py:759` |
| The one in-page catch: `approve_decomposition_route` catches `target_repository_not_granted`, rolls back, re-renders the proposal page with status 409 | `web.py:1438-1467`, template `decomposition_proposal.html:150`, test `tests/web/test_decomposition_review.py:392-407` |
| `get_session` is `with session_factory() as session: yield session`, and `session_factory` is a plain `sessionmaker(..., class_=Session)` | `src/orchestrator/api/dependencies.py:63-65`, `src/orchestrator/db.py` |
| FastAPI 0.159.1 runs the route handler inside the request's `AsyncExitStack`s (`request_response`, `fastapi/routing.py:149-153`), and `get_request_handler`'s callable reads the body, solves dependencies (raising `RequestValidationError` at `:478` and from the solver), then calls the endpoint. A wrapper around that callable sees dependency errors (`get_actor`'s `APIAuthenticationError` and observer `role_forbidden`), validation errors and handler errors, and returns before `get_session`'s `with` block exits | `fastapi/routing.py:398-530`, `:1280` |
| Starlette's `Jinja2Templates` autoescapes `.html` (`select_autoescape()`) | `starlette/templating.py:95` |
| `status_for` and `error_body` exist; the 401 body's message is chosen inline in `main.py`'s handler | `src/orchestrator/api/error_shape.py`, `src/orchestrator/main.py:73-86` |
| The handler set beyond FastAPI's defaults is pinned to `{APIAuthenticationError, DomainError, Exception}` | `tests/architecture/test_platform_edges.py:1083,1158`, `tests/api/test_server_errors.py:282`, CLAUDE.md line 136 |
| `_render` sets the session cookie through `_session_id`, which calls `_csrf_secret`, which raises `csrf_unavailable` when no secret is configured | `web.py:144-158`; test `tests/web/test_csrf.py:141-154` |
| `KIND_ORDER` has ten kinds; `web.py:220-221` says "five of the eight kinds are not work-unit states" | `src/orchestrator/services/reporting/pending_decisions.py:56-67` |
| `_parse_intake_payload`'s docstring says only `DomainError` and `APIAuthenticationError` have handlers, so other exceptions are bare 500s (false since Part 2) | `web.py:637-640` |
| `approve_decomposition_route`'s comment says "Every other refusal stays JSON" | `web.py:1460-1461` |

## Names fixed by this plan

| Thing | Name |
|---|---|
| Route class | `RefusalPageRoute(fastapi.routing.APIRoute)` in `src/orchestrator/web.py`; `router = APIRouter(prefix="/review", include_in_schema=False, route_class=RefusalPageRoute)` |
| Renderer | `_refusal_page(request: Request, error: Exception) -> HTMLResponse` in `web.py` |
| Template | `src/orchestrator/templates/refusal.html` (extends `base.html`) |
| Validation label | `request_invalid`: shown on the page as the code of a `RequestValidationError`; it is a page label, never a wire code |
| 401 message | `authentication_message(code: str) -> str` in `src/orchestrator/api/error_shape.py`, used by `main.py`'s handler and the page |
| Page element | one `<section id="refusal" data-refusal-code=… data-refusal-message=…>` carrying a `data-refusal-<field>` attribute per field `error_body` would carry (`recovery`, `current-state`, `current-version` when set) beside the visible text |
| Test helper | `tests/_support/refusal_page.py`: `refusal(response) -> dict[str, str]` (asserts `text/html`, returns the `data-refusal-*` attributes, unescaped) and `refusal_code(response) -> str` |
| New tests | `tests/web/test_refusal_page.py`, `tests/architecture/test_review_handlers.py` |

The route class and renderer live in `web.py`, not a new module, so `test_platform_edges.py`'s
population row (`PLACED`, `:1236`) and filesystem row (`Jinja2Templates` in `web.py`, `:950`) are
unchanged: the page reuses the module's one `templates` object.

## Task 1: CSRF and session parsing refuse a non-ASCII value

Files: `src/orchestrator/web.py`, `tests/web/test_csrf.py`.

1. Tests first, in `tests/web/test_csrf.py`:
   - **Token.** Render the unit page, lift the cancel form's real token, replace its signature half
     with `"é" * 64` (the payload half still decodes), post it confirmed: 403, refusal code
     `csrf_rejected`, the unit unchanged. Assert the status and that the body names
     `csrf_rejected` as text (`"csrf_rejected" in response.text`), so the test is valid both before
     and after Task 4 changes the body. The form value arrives as a non-ASCII `str`, so `data=`
     is enough.
   - **Cookie.** A `/review/units/{id}` GET with the cookie `orchestrator_review_session=x.é`:
     200, and the response sets a fresh cookie different from the one sent. httpx encodes `str`
     header values as ASCII, so send the header as bytes
     (`headers=[(b"cookie", b"orchestrator_review_session=x.\xe9"), *HUMAN as bytes]`; Starlette
     decodes cookies as latin-1, so `\xe9` arrives as `é`). If the client still refuses bytes, call
     `orchestrator.web._session_id` on a hand-built `Request` scope instead, and say so in the
     commit. Then a confirmed POST carrying that bad cookie and a token minted for the fresh session
     is refused `csrf_rejected` (the token's session is not the cookie).
   Controls: both fail on today's code. `db_client` is built with `raise_server_exceptions=False`
   (`tests/api/conftest.py:128-130`), so the `TypeError` reaches Part 2's catch-all and the test
   sees a 500 with code `internal_error` and an `alert_not_configured` log record, not a raised
   exception. Record that 500 in the task report as the control.
2. Fix. `_require_form`: compute `signature_matches = hmac.compare_digest(expected, signature)`
   inside the existing `try` (whose `except` already names `TypeError`), and set it `False` in the
   `except`. `_session_id`: `except (ValueError, TypeError):`. Nothing else moves.

Reading of the spec's "as a refusal" (ruling R2): the token case is the `csrf_rejected` refusal;
the cookie case is today's malformed-cookie path (ignored, a fresh session issued), because
`_session_id` never refuses and a POST still fails the session binding.

## Task 2: every `/review` handler is gated by `_human` and every POST by `_require_form`

Files: `tests/architecture/test_review_handlers.py` (new), `layers.toml`,
`docs/operations/architecture-guards.md`.

The spec asks only for the `_require_form` pin (it holds today, 17 of 17); this task pins `_human`
too (ruling R3), since ADR-0006's gate is pinned today only at the function
(`tests/api/test_observer_role_confinement.py:267`).

1. One AST scan of `web.py`: the functions decorated `@router.get(...)` or `@router.post(...)`.
   - Every such function's body contains a call `_human(actor)` (a direct statement, not nested
     in a branch). Count 26.
   - Every `@router.post` function's body calls `_require_form(` (anywhere in the body, including
     `approve_decomposition_route`'s `try` and `approve_authority`, which refuses two conditions
     before the call). Count 17.
   - The decorated set equals `{route.endpoint.__name__ for route in web_router.routes}`, so a
     route registered another way is not missed.
   Controls (each fed as source text to the same scanner): a POST with the `_require_form` call
   removed, a GET with `_human` removed, and a POST whose only `_require_form` call is in a nested
   `def` that never runs. Each is reported.
   If the scan finds a handler without `_human` or a POST without `_require_form` (all 26 and 17
   were read to have them), stop and report it to the controller: adding a gate is the behaviour
   change this part forbids, not a line to add quietly.
2. `architecture-guards.md`: a short section naming the test and its two rules.
3. `layers.toml` `platform`: `tests/architecture/test_review_handlers.py`.

## Task 3: one copy of the 401 message

Files: `src/orchestrator/api/error_shape.py`, `src/orchestrator/main.py`,
`tests/api/test_api_errors.py`.

1. Test first: `authentication_message("authentication_required")` and
   `authentication_message("authentication_failed")` equal the two strings `main.py` uses today;
   `test_api_errors.py`'s existing 401 body pins keep passing. Fails before step 2 (no function).
2. Move the conditional out of `authentication_error_handler` into `authentication_message`;
   the handler calls it. No body changes.

## Task 4: the refusal page

Files: `src/orchestrator/web.py`, `src/orchestrator/templates/refusal.html` (new),
`tests/_support/refusal_page.py` (new), `tests/web/test_refusal_page.py` (new), `layers.toml`,
and the assertion migration listed in step 4. The migration is in this task's commit, because the
route class reddens those assertions and every task ends green.

0. **Probe the hook first.** `APIRoute.__init__` builds `self.app =
   request_response(self.get_route_handler())` at construction (`fastapi/routing.py:1278`), and
   `get_route_handler` reads an effective-route context var (`:1282-1284`) for included routes, so
   the override should be reached under the app, but that is read from the source, not run. A
   five-line probe: a router with `route_class=RefusalPageRoute` and one route raising
   `DomainError`, included in `create_app()`, hit through `TestClient`: the override runs. If it
   doesn't under inclusion, the fallback is to override `handle` (or wrap `self.app` after
   `super().__init__`) with the same catch; report which was used. The probe becomes the escaping
   test below.
1. **Tests first**, in `tests/web/test_refusal_page.py`, through `create_app()` and the `db_client`
   fixture (never the bare router: under FastAPI 0.159.1 an included router is an
   `_IncludedRouter`, and the page must be proven through the app). The throwaway-router tests
   build their app the way `db_client` does (`tests/api/conftest.py:119-131`: `create_app(
   auth_config)`, `get_session` overridden with a `Session(migrated_engine)`, `base_url=
   "https://testserver"`), then include the throwaway router:
   - a POST refused by the service (a stale `expected_version` on the review form): 409,
     `content-type` `text/html`, `refusal(...)` holds `code` `version_conflict`, its message and
     `current-version`; no `set-cookie` header;
   - a POST refused by CSRF: 403 page, `csrf_rejected`;
   - a POST by an observer (refused in `get_actor`, a dependency): 403 page, `role_forbidden`;
   - a request with no identity headers: 401 page, `authentication_required`, the
     `authentication_message` text, and `WWW-Authenticate: Bearer`;
   - a POST with an empty `reason` (`Form(min_length=1)`): 422 page, code `request_invalid`, the
     message names the field (`reason`) and pydantic's `msg`, and holds no submitted value (post a
     long distinctive string in another field and assert it is absent);
   - GETs: an unknown unit id (`/review/units/<uuid4>`) is a 404 page `work_unit_not_found`; a
     malformed id (`/review/units/not-a-uuid`) a 422 page; a missing CSRF secret on
     `/review/units/{id}` a 503 page `csrf_unavailable` with no `set-cookie` (the page never goes
     through `_render`);
   - the recovery text: `intake_payload_invalid` on a staged confirm (as
     `test_staged_intake.py:405`) shows "withdraw this staged intake and re-stage";
   - escaping: a throwaway route on a router built with `route_class=RefusalPageRoute` and prefix
     `/review`, included in a fresh `create_app()`, raises `DomainError("x_refused",
     "<script>alert(1)</script>", "<b>r</b>")`: the body holds `&lt;script&gt;` and never
     `<script>alert`;
   - the link back: a `Referer` of `https://sds.alobar.net/review/units/<id>` gives
     `href="/review/units/<id>"`; `https://evil.example/elsewhere`, a missing header, and a
     non-`/review` path each give `href="/review"`; the href never contains a scheme or host;
   - a 500 stays JSON: the throwaway router's route raising `RuntimeError`, with
     `TestClient(app, raise_server_exceptions=False)`: 500, `application/json`, code
     `internal_error` (Part 2's body);
   - a write the handler made before refusing is not kept: the throwaway route adds a row through
     `SessionDep` and `flush()`es, then raises `DomainError`; the page renders and a fresh session
     finds no row;
   - the in-page grant refusal is unchanged: `test_decomposition_review.py:392-407` passes as is
     (the proposal page, with the repository name, status 409);
   - every route on `web_router.routes` is a `RefusalPageRoute` (control: a route appended to a
     copy with a plain `APIRoute` is reported).
   Controls: all but the 500, write, grant and route-type cases fail on today's code (JSON
   bodies); the route-type test fails before step 2 (the class doesn't exist).
2. **The route class.** `RefusalPageRoute.get_route_handler` wraps `super().get_route_handler()`:
   `await` it, and on `DomainError`, `APIAuthenticationError` or `RequestValidationError` return
   `_refusal_page(request, error)`. It catches nothing else, so any other exception reaches the
   app's `Exception` handler and stays Part 2's JSON 500. It is a `route_class`, not an
   `app.exception_handler`: the handler set stays `{APIAuthenticationError, DomainError,
   Exception}`, and `test_the_handlers_beyond_fastapis_defaults_are_the_listed_ones` and
   `..._are_exactly_three` stay green unchanged. Say both in its docstring.
3. **The renderer.** `_refusal_page`:
   - `DomainError`: status `status_for(error.code)`; fields `error_body(error.code,
     error.message, recovery=..., current_state=..., current_version=...)["error"]`, so the page
     shows exactly what the JSON did.
   - `APIAuthenticationError`: 401, `authentication_message(error.code)`, header
     `WWW-Authenticate: Bearer`.
   - `RequestValidationError`: 422, code `request_invalid`, message the first five errors as
     `<loc without "body"/"path"/"query">: <msg>` joined by `; ` (as `_registration` formats
     pydantic errors), never `input` or `ctx`.
   - Back link: the `Referer` header's `urlsplit(...).path` when it is `/review` or starts
     `/review/`, else `/review`; only the path is used.
   - Rendered with `templates.TemplateResponse(request=request, name="refusal.html", context=...,
     status_code=..., headers=...)`, never `_render`, so no cookie is set and no CSRF secret is
     read. It reads nothing from the database.
   - The rollback: the session is rolled back by `get_session`'s `with` block, which exits normally
     after the page is sent and closes the session (a plain `sessionmaker` session; close rolls
     back any open transaction). The page itself performs no rollback and no read (ruling R1).
     The docstring says so and names the test.
   - `refusal.html` extends `base.html`: an `<h1>` "Refused", the code in `<code>`, the message,
     the recovery and current state and version when set (a `<dl>`), and `<a href="{{ back }}">`
     "Back". Every value through autoescape; no `|safe` anywhere in the template.
4. **Migrate the assertions that read a JSON refusal from `/review`** to `refusal_code(response)`
   or `refusal(response)[...]`, keeping every status assertion:
   - `tests/web/test_csrf.py:138` (`csrf_rejected`), `:154` (`csrf_unavailable`, a GET).
   - `tests/web/test_human_act_form.py:158` (parametrized code), `:224`, `:268`, `:287`.
   - `tests/web/test_staged_intake.py:80`: the `_code` helper becomes `refusal_code`, and its eight
     callers (`:199`, `:209`, `:218` (a GET page), `:227`, `:312`, `:322`, `:334`, `:348`) follow;
     plus `:187`, `:300`, and `:419-421` (`refusal(response)`: code, and "withdraw this staged
     intake and re-stage" in its `recovery`).
   - `tests/web/test_intake_form.py:115`.
   - `tests/web/test_human_actions.py:231`, `:312-313` (`current-version` from the page), `:722`;
     and `:746` (`test_retry_form_refuses_attempts_above_the_integer_column`) gains
     `refusal_code(response) == "request_invalid"` beside its 422.
   - `tests/web/test_member_proposals.py:159`; and `:218`'s loop gains the page assertion for the
     `/review` branch (`refusal_code(response) in refusals`), which it doesn't check today.
   - `tests/web/test_reconciliation_resolution.py:106`, `:108`, `:155`.
   - `tests/api/test_observer_role_confinement.py:124-125`
     (`test_an_observer_is_refused_at_every_post_route_but_observations`, parametrized over
     `CONFINED_ROUTES`, which holds the 17 `/review` POSTs): read `refusal_code(response)` for a
     `/review` path and the JSON body otherwise. Cross-layer: this file is in another layer
     (`layers.toml:486`).
   No change, verified: `tests/_support/review_forms.py:50` (reads
   `/api/v1/decomposition-proposals`), `tests/api/test_membership_api.py:58` (compares a string
   field of an `/api` body), `test_member_proposals.py:36` and `:250`, `test_staged_intake.py:59`,
   `test_release_evidence_pack.py:22`, `test_evidence_pack.py:44,75` (`"detail"` is a payload key),
   and the `/api` reads in `tests/_support/protocol.py:75,275-308`,
   `tests/protocol/test_ws33_smoke.py:279-290,315,437,649,690` (its one `/review` step goes through
   `decide_decomposition`, which asserts a 303) and `tests/cli/test_cli_http_parity.py:270-350`
   (its `/review`-only decisions are not posted there; `:328` is the `/api` revision route). Before committing, run the step-1 scan again
   (`grep` for `.json()` in any test function that names `/review`) and list any hit this list
   missed in the task report.
5. `layers.toml` `platform`: `tests/web/test_refusal_page.py` (the template and helper are covered
   by `src/orchestrator/templates/**` and `tests/_support/**`).

## Task 5: records

Files: `src/orchestrator/web.py` (comments), `docs/decisions/0006-human-gates-browser-only.md`,
`CLAUDE.md`, `docs/history/claude-md-invariants-archive.md`,
`docs/operations/architecture-guards.md`.

1. `web.py:220-221`: drop the rotting count. Count the `KIND_ORDER` kinds that name no
   work-unit state against `WorkUnitState` and write the sentence without a number, naming them
   (proposals, staged intakes, package breakdowns, reconciliation conditions are not work-unit
   states at all), and say the kinds are `KIND_ORDER`.
2. `web.py:637-640` (`_parse_intake_payload`): a bare exception raised from here would be the
   server's `internal_error` 500, not a refusal the person can act on; a `DomainError` renders the
   refusal page.
3. `web.py:1460-1461`: the grant refusal is the one shown in place, on the proposal page with the
   repository named; every other refusal renders the refusal page.
4. ADR-0006 amendment 3 (2026-11-05, Devon's decision 2 of the platform layer design): a refused
   `/review` request renders `refusal.html` with the refusal's code, message, recovery and current
   state and version, and a link back, at the status the JSON had; what a person can confirm is
   unchanged; a 500 stays the JSON `internal_error`; the decomposition grant refusal stays in place;
   FastAPI's own `HTTPException`s (an unparseable body) and an unmatched `/review/...` path stay
   Starlette's JSON, since no `/review` route handles them.
5. CLAUDE.md, a new bullet beside the exception-handler rule, below `<!-- code-standards:end -->`:
   `/review` refusals (`DomainError`, `APIAuthenticationError`, `RequestValidationError`, raised
   in a handler or a dependency) render `refusal.html` through `web.py`'s `RefusalPageRoute` at
   `status_for`'s status (401 and 422 for the other two), with no cookie and no database read; a
   500 stays the JSON `internal_error`; the decomposition grant refusal stays in place; the page
   escapes every field and links back only to a `/review` path; every `/review` handler calls
   `_human(actor)` and every POST `_require_form` (`test_refusal_page.py`,
   `test_review_handlers.py`). Tests read a page refusal through `tests/_support/refusal_page.py`.
   Any rewritten text's old version goes to the archive under `### Platform Part 3, <merge date>`.
6. `architecture-guards.md`: confirm Task 2's section is there and the edge-table list is
   unchanged (no edge row moves).

## Closing (before merge)

1. Final whole-branch review on Opus against the spec's Part 3 and this plan, given the code, not
   this plan's conclusions; one fix wave; scoped re-review.
2. Mutation pass, clearing `__pycache__` around each mutate and restore, recording which tests
   fail for each. Kill or fix every survivor. Mutants:
   - `route_class=RefusalPageRoute` dropped from the router;
   - `RequestValidationError` removed from the catch; `APIAuthenticationError` removed;
   - `Exception` added to the catch (a 500 becomes a page);
   - the page's status hardcoded to 409; `WWW-Authenticate` dropped from the 401 page;
   - `|safe` on the message; `recovery` or `current_version` dropped from the template;
   - the Referer echoed unconditionally; its full URL used instead of the path;
   - the page rendered through `_render` (sets a cookie; `csrf_unavailable` recurses);
   - a `session.commit()` added before rendering (the write test kills it);
   - `input` included in the validation message;
   - `compare_digest` moved back out of `_require_form`'s `try`; `TypeError` removed from
     `_session_id`;
   - one POST's `_require_form` call deleted; one GET's `_human` deleted;
   - the grant catch in `approve_decomposition_route` deleted (the page lacks the repository name);
   - `authentication_message`'s two strings swapped.
   Expected survivor, said in the report rather than killed: none. An explicit rollback is not
   built (R1), so there is no "rollback removed" mutant.
3. Push, open the pull request, watch CI with `gh pr checks --watch --interval 30`, merge when
   green, confirm `MERGED` (`gh pr view --json state`), and read `main`.

## Task 6: deploy and live proof

1. Before `prepare`: no rotation unit `ready` or `claimed` (read the status ledger with the SYSTEM
   bearer, `include_inactive=true`, filtered to rotation units), no dispatched run unclaimed (the
   script checks), and not within ten minutes of :50 (the bump driver).
2. Deploy with `scripts/deploy_orchestrator.py` from a worktree at the merge commit, by
   `docs/operations/deploy.md` (`prepare`, the printed infraops calls, `recheck` immediately
   before the swap, `finish`). No migration is expected (Part 3 changes no schema); `prepare`
   decides.
3. Proof, read after `finish`:
   - `/health/live` serves the merge commit;
   - `https://sds.alobar.net/openapi.json`: paths and path-method pairs unchanged from a read taken
     before the deploy (the baseline is that read, not the spec's 70 and 75), since `/review` is
     outside the schema;
   - **the page in production, with no identity** (read-only, writes nothing): one `vps_exec` that
     finds the orchestrator container (`docker ps --filter name=eqj5l7k705fhi12x9i74fqf0 --format
     '{{.Names}}'`) and runs, inside it, a Python `urllib` GET of
     `http://127.0.0.1:8000/review/units/00000000-0000-4000-8000-000000000000` with no headers,
     printing only the status, the `content-type` and whether `data-refusal-code="authentication_required"`
     is in the body. Expected: `401`, `text/html`, true. `get_actor` refuses it before any handler
     runs, so it proves the route class and template are live, not the forward-auth path;
   - **through forward-auth** (read-only): in Devon's Chrome session via claude-in-chrome, open
     `https://sds.alobar.net/review/units/00000000-0000-4000-8000-000000000000`: expected the 404
     page with `work_unit_not_found` and a link to `/review`. A GET is no gate and records nothing,
     so it carries no attribution cost. If Chrome isn't connected, the in-container read stands
     alone, the report says so, and the forward-auth half of the proof is a person's next refusal
     (the spec's default).
   No staged POST refusal (spec, "Live proof").

## After the part

The controller's, not tasks: the SDS Layer Map's Core platform card re-measured; the year plan
sentence; the memory entry for the platform layer's progress; the spec's end-of-pass CLAUDE.md
sweep for the layer's rules (old text to the archive), whatever Task 5 didn't already rewrite.
Backlogged, not built: the `sds-bridge` sender token has no `.cred-consumers.toml` row (spec,
"After the pass").

## Rulings this plan makes that the spec didn't settle

- **R1. The rollback is `get_session`'s close, not a new call in the route class.** The spec says
  the page renders "after rolling the session back". The route class can't see the session (it is
  a dependency value), and reaching it would need a web-only dependency that puts the session on
  `request.state`. `get_session` closes a plain `sessionmaker` session when its `with` block exits,
  which rolls back any open transaction, and the page reads nothing, so ordering doesn't matter;
  the write test proves nothing flushed before a refusal is kept. Cost if wrong: if a later change
  makes the page read the database after a failed statement, Postgres refuses the read inside the
  aborted transaction (a 500). The CLAUDE.md rule forbids the read; adding the `request.state`
  hook later is about eight lines.
- **R2. A non-ASCII cookie is ignored, not refused** (a fresh session, as for any bad signature);
  a non-ASCII token is `csrf_rejected`. Cost if wrong: none found; a POST with that cookie still
  fails the session binding.
- **R3. Task 2 pins `_human` in all 26 handlers as well as `_require_form` in the 17 POSTs.** Cost
  if wrong: one more assertion in a new test.
- **R4. The page shows every field the JSON body carried** (code, message, recovery, current state
  and version), not only code and message, so moving to HTML loses nothing a person or a test read
  (`test_staged_intake.py:419` reads the recovery, `test_human_actions.py:313` the version). Cost if
  wrong: a slightly fuller page.
- **R5. The link back is the `Referer`'s path when it is under `/review`, else `/review`.** Only the
  path is used, so the link can't leave the host. Cost if wrong: a browser that sends no Referer
  (strict policy) gets `/review` rather than the form page.
- **R6. A validation refusal shows code `request_invalid`** (a page label) and each error's field
  and message, never the submitted value. Cost if wrong: a label nobody else uses; it is not on the
  wire.
- **R7. The 401 page keeps `WWW-Authenticate: Bearer`**, as the JSON did, and the 401 message moves
  to `error_shape.authentication_message` so there is one copy. Cost if wrong: none for a browser,
  which shows no prompt for a Bearer challenge.
- **R8. The route class and renderer live in `web.py`**, so the platform edge rows are unchanged.
  Cost if wrong: `web.py` grows by about 60 lines.
- **R9. Starlette's 404 for an unmatched `/review/...` path and FastAPI's 400 for an unparseable body
  stay JSON**: no `/review` route handles them, and catching them would mean an app-level handler,
  which the handler-set rule forbids. Recorded in the ADR amendment. Cost if wrong: a person who
  mistypes a URL sees `{"detail":"Not Found"}`.
- **R10. The live proof is two read-only GETs** (in-container with no identity, and Chrome under
  Devon's session), not the spec's "next person's refusal" alone. Cost if wrong: none; both write
  nothing.

## Unbuildable

Nothing. The one dependency outside the session is claude-in-chrome being connected for the
forward-auth GET in Task 6; without it the proof is the in-container read plus the next person's
refusal, as the spec's default.
