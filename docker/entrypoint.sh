#!/bin/sh
# 1) apply Alembic migrations (services that own a database), waiting for MySQL if needed
# 2) start the API with a single worker (consumers / background loops run inside the process)
set -eu

if [ -f /app/alembic.ini ] && [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  attempt=1
  until alembic -c /app/alembic.ini upgrade head; do
    if [ "$attempt" -ge 30 ]; then
      echo "migrations failed after $attempt attempts" >&2
      exit 1
    fi
    echo "database not ready or migration failed (attempt $attempt), retrying in 3s..." >&2
    attempt=$((attempt + 1))
    sleep 3
  done
fi

exec uvicorn "${PACKAGE}.main:create_app" --factory --host 0.0.0.0 --port 8000 --no-server-header --proxy-headers
