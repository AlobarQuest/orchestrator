# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder

ARG SECURITY_STANDARDS_REVISION
ARG REGISTRY_ARTIFACT_SHA256
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.5.31 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY scripts/build_registry_bundle.py ./scripts/build_registry_bundle.py
COPY --from=registry / /registry
RUN uv sync --frozen --no-dev \
    && .venv/bin/python scripts/build_registry_bundle.py \
      --artifact-dir /registry \
      --source-revision ${SECURITY_STANDARDS_REVISION} \
      --artifact-sha256 ${REGISTRY_ARTIFACT_SHA256} \
      --output /app/registry-bundle.json

FROM python:3.12-slim AS runtime

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    ORCHESTRATOR_REGISTRY_BUNDLE=/app/registry-bundle.json \
    SECURITY_STANDARDS_DIR=/app/security-standards
WORKDIR /app

RUN groupadd --system orchestrator \
    && useradd --system --gid orchestrator --home-dir /app orchestrator
COPY --from=builder --chown=orchestrator:orchestrator /app/.venv /app/.venv
COPY --from=builder --chown=orchestrator:orchestrator /app/src /app/src
COPY --from=builder --chown=orchestrator:orchestrator /app/registry-bundle.json /app/registry-bundle.json
COPY --from=registry --chown=orchestrator:orchestrator /agents /app/security-standards/registry/agents
COPY --from=registry --chown=orchestrator:orchestrator /src /app/security-standards/src
COPY --from=registry --chown=orchestrator:orchestrator /schema /app/security-standards/schema
COPY --chown=orchestrator:orchestrator alembic.ini /app/alembic.ini
COPY --chown=orchestrator:orchestrator migrations /app/migrations

USER orchestrator
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"]
CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
