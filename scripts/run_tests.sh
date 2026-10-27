#!/usr/bin/env bash
# Run every test suite. Usage: scripts/run_tests.sh [backend|frontend|all]
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
what="${1:-all}"

if [[ "$what" == "backend" || "$what" == "all" ]]; then
  echo "== backend (unit, integration, API, failure, in-process end-to-end, migrations)"
  "$PY" -m pytest -q -m "not compose and not rabbitmq"
fi
if [[ "$what" == "frontend" || "$what" == "all" ]]; then
  echo "== frontend (typecheck, unit, build)"
  (cd frontend && npm ci --no-audit --no-fund && npx tsc --noEmit && npx vitest run && npm run build)
fi
if [[ -n "${BASE_URL:-}" ]]; then
  echo "== compose end-to-end against $BASE_URL"
  "$PY" -m pytest -q -m compose
fi
if [[ -n "${RABBITMQ_URL:-}" ]]; then
  echo "== RabbitMQ integration"
  "$PY" -m pytest -q -m rabbitmq
fi
