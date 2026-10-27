#!/bin/bash
# Runs once, when the MySQL data directory is first initialised.
# One database + one least-privilege user per service (no service can read another service's data).
set -euo pipefail

create() {
  local db="$1" user="$2" pass="$3"
  if [ -z "$pass" ]; then echo "missing password for $user" >&2; exit 1; fi
  mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE DATABASE IF NOT EXISTS \`${db}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${user}'@'%' IDENTIFIED BY '${pass}';
ALTER USER '${user}'@'%' IDENTIFIED BY '${pass}';
GRANT ALL PRIVILEGES ON \`${db}\`.* TO '${user}'@'%';
SQL
}

create ingestion_db    ingestion    "${INGESTION_DB_PASSWORD:-}"
create trace_db        trace        "${TRACE_DB_PASSWORD:-}"
create log_db          logs         "${LOG_DB_PASSWORD:-}"
create metrics_db      metrics      "${METRICS_DB_PASSWORD:-}"
create rca_db          rca          "${RCA_DB_PASSWORD:-}"
create gateway_db      gateway      "${GATEWAY_DB_PASSWORD:-}"
create order_db        orders       "${ORDER_DB_PASSWORD:-}"
create payment_db      payments     "${PAYMENT_DB_PASSWORD:-}"
create notification_db notifications "${NOTIFICATION_DB_PASSWORD:-}"
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "FLUSH PRIVILEGES;"
echo "created 9 service databases"
