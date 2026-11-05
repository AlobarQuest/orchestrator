# Known Non-obvious Invariants, continued from the root `CLAUDE.md`: The orchestrator server package (`src/orchestrator/`): word guards, the `/review` web surface, route inventories, factory policy, reach, rotation executor identities and the transaction clock

- A refused `/review` request (`DomainError`, `APIAuthenticationError` or `RequestValidationError`,
  raised in a handler or a dependency) renders `refusal.html` through `web.py`'s `RefusalPageRoute`
  at `status_for`'s status (401 and 422 for the other two), with no cookie and no database read.
  Only the router's own routes are covered: an unrouted `/review` path (404) and a wrong method
  (405) are Starlette's JSON `{"detail": ...}`, FastAPI's unparseable-body 400 stays JSON, and a
  500 stays the JSON `internal_error`. The decomposition grant refusal stays in place on its
  proposal page. The page escapes every field and links back only to a `/review` path (an
  unparseable `Referer` links to `/review`). The page performs no rollback: `get_session` must
  never commit on normal exit, because the route class swallows the refusal and the generator
  exits normally, so the closing session is what discards a write flushed before the refusal
  (`test_the_production_session_generator_does_not_commit_on_normal_exit`,
  `test_the_production_session_does_not_keep_a_write_made_before_a_refusal`). Tests read a page
  refusal through `tests/_support/refusal_page.py`; the 404, 405 and 400 pins are in
  `test_an_unrouted_path_a_wrong_method_and_an_unparseable_body_stay_json`
  (`test_refusal_page.py`). Detail: ADR-0006 amendment 3.
- Every `/review` handler calls `_human(actor)`, and every POST calls `_require_form(...)`, each as
  a direct top-level statement of its body: a call inside a branch, an `except` clause or a
  nested def is not the gate (`tests/architecture/test_review_handlers.py`).
- Under `src/orchestrator/` the word guards (ADR-0051) refuse forbidden terms in CODE only:
  identifiers, imports and non-docstring strings with no whitespace. ws32 forbids `dispatch`,
  `deploy`, `coolify`, `workflow_dispatch`, `factory-runner`, `auto_merge`, `merge_pull_request` and
  similar; ws34 forbids `workflow_dispatch`, `factory_runner`, `github.actions`, `coolify` and
  `merge_to_main`. Read the lists in the tests; reword rather than allowlist. ws32 excuses
  `config.py` for `dispatch` and `factory-runner`, and `models.py` for `dispatch` and `deploy`, by
  label: a term carrying any other forbidden label is reported, and a label that stops occurring
  fails the rot check (`LABEL_EXEMPT_PATHS`,
  `test_ws32_label_exemptions_name_only_labels_their_files_still_trip`).
  ws33 still scans `config.py`'s whole text, comments and docstrings included, because they name
  the pilot workflow.
- New routes must be added to the exact POST/GET inventories in `test_scope_guards.py`. The POST
  inventory includes `/review`, and the GET inventory holds the `/api/v1` GETs, the nine `/review`
  pages and `/health/live` and `/health/ready`. Both read the schema and the routers' own
  routes, so a route with `include_in_schema=False` on a domain or web router is held to them. The
  app serves GET, HEAD and POST only: a PUT, PATCH or DELETE route on those routers or in the
  schema fails `test_only_get_head_and_post_are_served`; one added straight to the app with
  `include_in_schema=False` is not seen. A new ingress POST route
  also needs a row in `tests/idempotency/test_matrix.py` or a reasoned `NON_INGRESS_POST_ROUTES`
  entry; a `workflow_dispatch` workflow needs both workflow-guard allowlists edited. Detail:
  `docs/operations/architecture-guards.md`.
- `reach` (`reach_vocabulary.py`) is a package-declared SET over `source_repository`, `live_estate`,
  `external_system`, `operator_machine`; never inferred, absence is `unknown`, members only narrow,
  no `orchestrator_self` member. Read it only via `reach_from_snapshot()`; execution locus is a
  separate, unmodelled dimension. History: `docs/history/claude-md-invariants-archive.md` #78.
- `factory-policy.toml` can only REFUSE: no schema value permits, `factory_policy.py` imports no
  config, an empty refusal list means no objection, and a listed value (`known_good`,
  `[admission]`) only withholds one objection, so an EMPTY `[admission]` list refuses every value.
  Exactly one row per `REACH_VOCABULARY` member or it fails to load; `SUPPORTED_SCHEMA_VERSIONS` is
  exact; a new field ships with its reader. History: `docs/history/claude-md-invariants-archive.md`
  #81.
- `test_factory_policy.py` requires exactly one module in `src/orchestrator/` to name the policy
  artifact's filename, docstrings included, and forbids its row rationales in source. Reword; never
  allowlist. Detail: `docs/operations/architecture-guards.md`.
- `REACH_VOCABULARY` must stay a dict of string literals or `test_cross_boundary_vocabulary` loses
  the whole vocabulary; fix with a pinned duplicate, never an allowlist entry. Detail:
  `docs/operations/architecture-guards.md`.
- The conformance gate's anti-tautology rule is docstring only. Do not delete the `status ==
  "green"` short-circuit: the pinned envelope fixture would then be refused and all dispatch stops.
  History: `docs/history/claude-md-invariants-archive.md` #111.
- `change_class` is a free string matched against `factory-policy.toml`'s `[admission]`
  `change_classes`, and `required_capability` against its `capabilities` (bounded to
  `RUNNER_CAPABILITIES` at load); a missing `change_class` falls back to `required_capability`,
  which no listed class matches. Both lists only withhold an objection: empty refuses every value.
  Widening one is a standing authority change and a release. History: ADR-0053;
  `docs/history/claude-md-invariants-archive.md` #147.
- `TransactionClock.now()` is `transaction_timestamp()` and does not advance: every admission term,
  including change windows, is judged at the instant the transaction opened
  (`test_the_clock_does_not_advance_inside_a_transaction`).
  History: `docs/history/claude-md-invariants-archive.md` #163.
- `change_window` is optional per reach row in `factory-policy.toml`; `live_estate` must keep one,
  enforced by `test_the_live_estate_row_declares_a_change_window`. Never require a window in
  `reach_admission.change_window_refusal`. History: `docs/history/claude-md-invariants-archive.md`
  #164.
- The rotation executor runs as two identities, each confined both ways
  (`rotation_claims.RotationExecutors`): `ORCHESTRATOR_ROTATION_KEYCHAIN_EXECUTOR_AGENT_ID` alone
  claims a rotation unit whose envelope's `rotation_adapter` is in `KEYCHAIN_ADAPTERS`, and
  `ORCHESTRATOR_ROTATION_EXECUTOR_AGENT_ID` alone claims every other rotation unit
  (`rotation_claims.is_rotation_unit`); neither claims anything else. An unset setting refuses
  every claim on its units, and boot refuses a credential holding `rotation-executor-v1` or
  `rotation-executor-keychain-v1` that its profile's setting doesn't name. A lapsed rotation
  claim is released (`release_expired_claim`), never reclaimed. A rotation claim asks the
  `live_estate` window unless its envelope sets `touches_no_hosted_service: true`, except a
  continuation after a lapse with the holder's evidence. Detail:
  `docs/operations/driving-a-unit.md`.
