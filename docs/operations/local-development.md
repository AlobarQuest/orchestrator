# Local development

WS-3.1 supports PostgreSQL 16 only. The local database is disposable; no tracked
environment file or credential is required.

```bash
docker compose up -d orchestrator-postgres
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn orchestrator.main:app --reload --port 8000
uv run orchestrator --help
```

The web process never applies migrations. Run Alembic explicitly before starting it.
Use `/health/live` for process liveness and `/health/ready` for database and migration
readiness.

The checked-in Compose service intentionally clears `ORCHESTRATOR_REGISTRY_BUNDLE`; it is
health-only and all authenticated API/review routes remain fail-closed. To exercise those
routes, supply the complete runtime configuration documented in `authentication.md`. No
fixture bearer token or CSRF secret is baked into Compose.

To build the fixture-registry image locally:

```bash
docker build \
  --build-context registry=tests/fixtures/security-standards \
  --build-arg SECURITY_STANDARDS_REVISION=0123456789abcdef0123456789abcdef01234567 \
  --build-arg REGISTRY_ARTIFACT_SHA256=00e17cb8e9841ccc8d892f0ca5b7ee240a23721c62d2d0f2236b5777ddab7d69 \
  -t orchestrator:ws31 .
docker run --rm --entrypoint orchestrator orchestrator:ws31 --help
```

The fixture identity bundle is test-only and contains no credentials. Both its recorded
source revision and deterministic content digest are pinned; changing any accepted artifact
byte requires an explicit digest update.
