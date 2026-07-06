# WS-3.4 Evidence Index

Intent package:

- `ws-3.4-evidence-events`
- Approved revision: 2
- Approved hash: `8530173a7cd1ec70a40e4a177c7dae3db68170f11d3a9ea88563edf5188a9239`

Design artifacts:

- `docs/superpowers/specs/2026-07-06-ws34-evidence-events-design.md`
- `docs/superpowers/specs/2026-07-06-ws34-adversarial-architecture-review.md`
- `docs/superpowers/plans/2026-07-06-ws34-evidence-events.md`

Implementation evidence:

- `security-standards` focused schema test was first red for `source.system == "orchestrator"`, then green after adding `orchestrator` to the `factory-event/v1` source enum.
- `security-standards` focused tests passed:
  - `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_factory_envelope.py::test_make_event_accepts_orchestrator_source tests/test_factory_envelope.py tests/test_factory_cli.py tests/test_agent_registry.py`
  - Result: `28 passed`
- `security-standards make check` passed after the schema change:
  - Result: `175 passed, 3 skipped`
  - Existing pyright missing-source warnings remained.
- Orchestrator migration test was first red for missing `event_publications`, then green after migration/model implementation.
- Orchestrator focused mapper/outbox/API/CLI/web/scope/migration verification:
  - `PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test pytest tests/services/test_event_publications.py tests/api/test_event_publications_api.py tests/cli/test_event_publications_cli.py tests/web/test_evidence_pack.py tests/architecture/test_ws34_scope_guards.py tests/architecture/test_scope_guards.py tests/architecture/test_ws33_scope_guards.py tests/persistence/test_migrations.py -q`
  - Result: `46 passed`
  - Existing Starlette/httpx warning remained.

Behavior covered:

- deterministic `factory-event/v1` IDs by source kind, source ID, and `ws34.v1` mapping version;
- schema validation through `security-standards` envelope helpers;
- registered actor validation through the `security-standards` registry;
- fallback to `unknown` only for protocol fixture or explicitly historical rows;
- direct mapping for local event, evidence, adjudication, and context snapshot source facts;
- durable `event_publications` rows with idempotent queue behavior;
- deterministic full snapshot export to caller-provided paths, not live factory-events store mutation;
- failed export leaves lifecycle state and publication rows unchanged;
- retry recomputes publication state without lifecycle mutation;
- API and CLI `event-publications` list, queue, export, and retry surfaces;
- Evidence Pack read-only publication status display;
- scope guards for no factory dispatch, workflow dispatch, production deployment, Coolify mutation, automatic merge, or live store import.

Final verification:

- `orchestrator` full local gate:
  - `PATH="$PWD/.venv/bin:$PATH" TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@192.168.97.2:5432/orchestrator_test make check`
  - Result: Ruff passed, format passed, pyright passed, `668 passed`.
  - Existing Starlette/httpx warning remained.
- `security-standards` final gate:
  - `make check`
  - Result: Ruff passed, format passed, pyright reported 0 errors and 5 existing missing-source warnings, `175 passed, 3 skipped`.
  - `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_factory_envelope.py tests/test_factory_cli.py tests/test_agent_registry.py`
  - Result: `28 passed`.
- `intent-packages` final gate:
  - `PYTHONPATH=src .venv/bin/python -m intent_packages verify-approval packages/ws-3.4-evidence-events`
  - `PYTHONPATH=src .venv/bin/python -m intent_packages validate --all`
  - `make check`
  - Result: approval verified, all packages OK, existing `ws-2.3-intent-authoring-skill` warning remained, pyright reported 0 errors and 3 existing missing-source warnings, `157 passed`.
