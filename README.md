# Distributed Observability & Root-Cause Analysis Platform

Collects traces, logs and metrics from a sample microservice application (Order → Payment → Notification), reconstructs
traces, derives a service dependency graph, raises alerts and explains failures with a **deterministic, evidence-based
root-cause engine**. A violet React console presents it all.

## Quick start

```bash
cp .env.example .env          # replace every "change-me" value (the gateway refuses them when ENVIRONMENT=production)
docker compose up -d --build
open http://localhost:3000    # console (nginx -> api-gateway)
open http://localhost:8000/docs   # gateway Swagger (ENABLE_DOCS=true)
python scripts/generate_traffic.py --gateway http://localhost:8000 --user operator --password "$SEED_OPERATOR_PASSWORD" --rounds 5
```

Seed users (passwords come from `.env`: `SEED_ADMIN_PASSWORD`, `SEED_OPERATOR_PASSWORD`, `SEED_VIEWER_PASSWORD`):

| user | role | can |
|---|---|---|
| `viewer` | viewer | overview, traces, metrics, dependencies, alerts, reports (logs are restricted) |
| `operator` | operator | + logs, acknowledge alerts, run analysis, create sample orders / scenarios |
| `admin` | admin | + alert rules, gateway request audit |

### Try it without Docker (single process, SQLite + in-memory broker)

```bash
pip install -r requirements/dev.txt && pip install -e libs/obs_common
python scripts/dev_stack.py                       # gateway on :8000, dev passwords Admin#dev-1 / Operator#dev-1 / Viewer#dev-1
python scripts/generate_traffic.py --user operator --password 'Operator#dev-1'
cd frontend && npm ci && npm run dev              # console on :5173, proxies /api to :8000
```

## Architecture

```mermaid
flowchart LR
  UI[React console] -->|JWT| GW[API Gateway]
  GW --> Q[Observability Query]
  GW --> M[Metrics Aggregation]
  GW --> R[Root-Cause Analysis]
  GW --> O[Order Service]
  O -->|HTTP| P[Payment Service]
  O -->|HTTP| N[Notification Service]
  GW & O & P & N -->|OTel spans, logs, metrics<br/>X-API-Key| I[Telemetry Ingestion]
  I -->|telemetry.traces / logs / metrics| MQ{{RabbitMQ}}
  MQ --> T[Trace Processing]
  MQ --> L[Log Processing]
  MQ --> M
  T -->|events.trace.completed| MQ
  MQ --> R
  Q -.reads.-> T & L & M & R
  T --- DB1[(trace_db)]
  L --- DB2[(log_db)]
  M --- DB3[(metrics_db)]
  R --- DB4[(rca_db)]
  I --- DB5[(ingestion_db)]
  GW --- DB6[(gateway_db)]
  O --- DB7[(order_db)]
  P --- DB8[(payment_db)]
  N --- DB9[(notification_db)]
```

| Service | Responsibility | Owns DB |
|---|---|---|
| api-gateway | login/JWT, RBAC, routing, rate/lockout, request audit, system health | gateway_db |
| ingestion-service | validates telemetry (API key), buffers in DB, publishes to RabbitMQ, retries publication | ingestion_db |
| trace-service | groups spans, rebuilds parent/child tree, detects missing spans, completion policy, dependency graph | trace_db |
| log-service | stores/search logs, redaction, error-pattern grouping | log_db |
| metrics-service | percentile aggregation, alert rules/alerts | metrics_db |
| rca-service | rules engine, reports + evidence | rca_db |
| query-service | stateless fan-out; partial results with warnings | – |
| order / payment / notification | instrumented sample app with failure scenarios | own DBs |

Every service exposes `/health` (liveness), `/ready` (DB + broker), Swagger at `/docs`, structured JSON logs carrying
`correlation_id`, `trace_id`, `span_id`. Responses use one envelope: `{data, meta, warnings, error}`; `error.source` is
`gateway | service | downstream` and names the failing `service`.

## Flows

1. **Request**: console → gateway (JWT, role check) → order-service → payment-service → notification-service. Trace context
   (W3C `traceparent`) and `X-Correlation-ID` propagate through every hop.
2. **Telemetry**: services ship spans/logs/metrics to ingestion (`X-API-Key`) → events are stored then published
   (`telemetry.*`). Consumers ack after commit; failures retry through TTL queues (2 s, 10 s, 30 s) then land in `<queue>.dlq`.
   Idempotency keys (`event_id`, `(trace_id, span_id)`) make redelivery harmless.
3. **Trace completion**: the trace service marks a trace *complete* once its root span arrived and no new span appeared for
   `TRACE_COMPLETION_WAIT_SECONDS`; otherwise *incomplete* (missing parents are reported). It then publishes
   `events.trace.completed`.
4. **Diagnosis**: rca-service consumes that event, gathers the trace, logs, service metrics and alerts (a missing source lowers
   confidence and is stated), applies the rules and stores one report per trace (re-analysis is idempotent).

### Event contracts

| routing key | queue | payload (essentials) |
|---|---|---|
| `telemetry.traces` | `trace-processing.traces` | `event_id, trace_id, span_id, parent_span_id, service_name, operation_name, kind, start_time, end_time, status, error_message, attributes` |
| `telemetry.logs` | `log-processing.logs` | `event_id, service_name, severity, message, timestamp, trace_id?, span_id?, correlation_id?` |
| `telemetry.metrics` | `metrics-aggregation.metrics` | `event_id, service_name, metric_name, value, timestamp, labels, trace_id?` |
| `events.trace.completed` | `rca.trace-completed` | `trace_id, completion_state, status, completed_at` |

### Database schema (one MySQL 8 database + user per service; Alembic per service in `services/<svc>/migrations`)

`users, gateway_requests` · `ingestion_events` · `traces, trace_spans` · `processed_logs, error_patterns` ·
`metric_samples, metric_aggregates, alert_rules, alerts` · `diagnostic_reports, diagnostic_evidence` ·
`orders, order_items` · `payments` · `notifications`. Migrations run at container start (`docker/entrypoint.sh`) and are
generated from the models with `python scripts/generate_migrations.py`.

## Method notes

* **Trace reconstruction**: spans are keyed by `(trace_id, span_id)`; children are ordered by start time; a span whose parent
  never arrived is flagged `missing_parent` and the trace gets a warning. Self time = duration − merged child intervals.
  A span is *slow* when above `SLOW_SPAN_MS` or well above its service/operation baseline.
* **Dependency graph**: derived only from observed cross-service parent→child spans, plus CLIENT spans whose callee never
  reported (shown dashed — timeouts/unreachable services).
* **Percentiles**: nearest-rank over the raw samples in the window (p50/p95/p99), never an average of averages.
* **Root-cause rules** (`rca_service/rules.py`):

| situation | classification | confidence |
|---|---|---|
| a leaf/own span in service S ended in ERROR | `confirmed_failure` (S) | high |
| only a caller saw timeout/5xx, callee reported nothing | `potential_cause` (peer service) | medium |
| one service holds most of a slow trace's self time | `latency_bottleneck` | medium/high |
| nothing wrong | `healthy` | – |
| spans missing or no evidence source | `insufficient_evidence` / confidence lowered | – |

  Every report carries its evidence rows (spans, logs, metrics, alerts) and never claims more than the evidence supports.

### Scenario → expected result

| `X-Scenario` | HTTP result | Expected diagnosis |
|---|---|---|
| `normal` | 201 | healthy |
| `slow_payment` | 201 (slow) | latency_bottleneck → payment-service |
| `payment_failure` | 502, `error.source=downstream`, service `payment-service` | confirmed_failure → payment-service |
| `payment_timeout` (extra) | 504 | potential_cause → payment-service |
| `notification_timeout` | 201, notification non-fatal | potential_cause → notification-service |
| `invalid_downstream_response` | 502 | confirmed_failure → order-service (response validation span) |
| `intermittent_unavailability` | 201 after retry or 502 | mixed; retries use idempotency key `order-{id}-payment` |

Set via the `X-Scenario` request header (validated) or `DEFAULT_SCENARIO`; the console has a *Scenario Lab* page.

## API

Gateway (all under `/api/v1`, Bearer JWT; Swagger at `/docs`; Postman: `postman/observability-platform.postman_collection.json`):

`POST auth/login` · `GET auth/me` · `GET overview` · `GET traces`, `traces/{id}` · `GET logs`, `logs/patterns` (operator) ·
`GET dependencies`, `dependencies/{service}` · `GET metrics/summary|services|timeseries|latency` ·
`GET alerts`, `alerts/rules`, `alerts/{id}` · `PATCH alerts/{id}` (operator) · `PUT alerts/rules` (admin) ·
`GET diagnostics`, `diagnostics/{id}` · `POST diagnostics/analyze/{trace_id}` (operator) ·
`GET|POST sample/orders`, `GET sample/orders/{id}`, `GET sample/scenarios` (operator) ·
`GET system/health`, `GET system/requests` (admin).

Ingestion (direct, `X-API-Key`): `POST /api/v1/telemetry/{logs|traces|metrics|batch}` (single or batch, 202).
Common filters: `start`, `end`, `window_minutes`, `service_name`, `severity`, `trace_id`, `correlation_id`, `page`, `page_size`.

## Configuration

All variables are listed and documented in `.env.example` (DB passwords per service, RabbitMQ credentials,
`INTERNAL_API_TOKEN`, `INGEST_API_KEYS`, `JWT_SECRET`, seed passwords, `ENVIRONMENT`, `ENABLE_DOCS`, `DEFAULT_SCENARIO`,
`TRACE_COMPLETION_WAIT_SECONDS`, `SLOW_SPAN_MS`, ports). Compose refuses to start when a required secret is missing.

## Testing

```bash
scripts/run_tests.sh            # backend + frontend (typecheck, vitest, build)
pytest -m "not compose and not rabbitmq"          # 186 backend tests
BASE_URL=http://localhost:8000 E2E_PASSWORD=... pytest -m compose   # against running compose
RABBITMQ_URL=amqp://obs:...@localhost:5672/%2F pytest -m rabbitmq   # real broker
```

Suites: unit (rules, reconstruction, percentiles, validation), service integration (each service over its API), gateway API
(auth/RBAC/lockout/routing), failure tests (broker down, retries, DLQ, duplicate events, downstream timeouts, partial
query results), in-process end-to-end across all services, migrations for all 9 databases, deployment-config consistency.

## Security

JWT with PBKDF2-hashed passwords and login lockout; role read from the DB per request; services reachable only with
`X-Internal-Token` (not published to the network except localhost debugging ports); telemetry needs an API key; strict input
validation (patterns, length and page limits); secrets and tokens redacted from logs and ingested text; security headers +
CSP in nginx; containers run as non-root, read-only filesystem, all capabilities dropped; docs can be disabled in production.

## Delivery guarantees & operations

At-least-once delivery with idempotent consumers; bounded retries then DLQ. Inspect/replay a dead-letter queue:
`python scripts/replay_dlq.py --queue trace-processing.traces --dry-run` (RabbitMQ UI: http://localhost:15672).
`POST /api/v1/telemetry/republish` (admin) re-publishes events buffered while the broker was down.

## Known limitations

* The test suite and the in-process stack were run on Python 3.13; container images target Python 3.12 (`docker/Dockerfile.service`).
* Docker image builds, compose startup, real MySQL and real RabbitMQ were **not** exercised in the authoring sandbox (no Docker
  daemon). The `compose` and `rabbitmq` test markers are provided for that; tests otherwise use SQLite and an in-memory broker
  that shares the retry/DLQ decision logic with the RabbitMQ consumer.
* Login lockout and rate state are in-memory per gateway instance.
* Metrics use raw samples with periodic aggregation; very high volumes would need pre-aggregation / retention jobs.
