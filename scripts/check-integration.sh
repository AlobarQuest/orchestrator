#!/usr/bin/env bash
# Run the factory-events database tests (`pytest -m integration`) against a disposable Postgres,
# as security-standards' `make check-integration` (FE-1) and intent-packages'
# `scripts/check-integration.sh` (FE-2) do.
#
# SECURITY_STANDARDS_DIR must name a security-standards checkout that holds FE-1's database chain
# (src/factory_events/db.py). The tests run FE-1's `factory_events` under this repository's
# interpreter, as package_sources does. With FACTORY_TEST_DSN set (a superuser on a throwaway
# cluster), use it. Without it, start a throwaway postgres:16-alpine container on a random
# 127.0.0.1 port with a random password, run the tests, and remove the container on every exit
# path. The DSN is never printed.
#
# Never point FACTORY_TEST_DSN at a database you'd keep: the tests create databases and
# cluster-wide roles.
set -euo pipefail

cd "$(dirname "$0")/.."
PYBIN="${PYBIN:-.venv/bin/python}"
SEC="${SECURITY_STANDARDS_DIR:?SECURITY_STANDARDS_DIR is required: a security-standards checkout with FE-1}"
[ -f "$SEC/src/factory_events/db.py" ] \
    || { echo "check-integration: $SEC has no factory_events/db.py (FE-1); refusing to run"; exit 1; }

export IT_SECURITY_STANDARDS_DIR="$SEC"
# Serial: the tests share nothing, and a database per test keeps them apart.
run_tests() { PYTEST_ADDOPTS="" "$PYBIN" -m pytest -q -p no:xdist -m integration "$@"; }

if [ -n "${FACTORY_TEST_DSN:-}" ]; then
    run_tests "$@"
    exit
fi

command -v docker >/dev/null 2>&1 || { echo "check-integration: docker not found"; exit 1; }
NAME="orchestrator-it-$$-$RANDOM"
POSTGRES_PASSWORD="$(openssl rand -hex 16)"
export POSTGRES_PASSWORD  # passed by name, so the value stays out of docker's argv
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

docker run -d --rm --name "$NAME" -e POSTGRES_PASSWORD -p 127.0.0.1::5432 \
    postgres:16-alpine >/dev/null
PORT="$(docker port "$NAME" 5432/tcp | head -n1 | sed 's/.*://')"

for _ in $(seq 1 60); do
    if docker exec "$NAME" pg_isready -q -U postgres -h 127.0.0.1; then break; fi
    sleep 1
done
docker exec "$NAME" pg_isready -q -U postgres -h 127.0.0.1 \
    || { echo "check-integration: the disposable Postgres didn't start"; exit 1; }

FACTORY_TEST_DSN="postgresql://postgres:${POSTGRES_PASSWORD}@127.0.0.1:${PORT}/postgres" \
    run_tests "$@"
