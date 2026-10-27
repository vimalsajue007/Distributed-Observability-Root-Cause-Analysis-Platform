// Shapes returned by the platform API (see README "API reference"). All responses use the {data, meta, warnings, error} envelope.

export type Role = 'viewer' | 'operator' | 'admin';

export interface ApiWarning { code: string; message: string }
export interface PageMeta { page: number; page_size: number; total: number; total_pages: number }
export interface Envelope<T> {
  data: T;
  meta: (Partial<PageMeta> & Record<string, unknown>) | null;
  warnings: ApiWarning[] | null;
  error: ApiErrorBody | null;
}
export interface ApiErrorBody {
  code: string;
  message: string;
  details?: unknown;
  source?: 'gateway' | 'service' | 'downstream';
  service?: string | null;
}

export interface LoginResult { access_token: string; token_type: string; expires_in: number; user: { username: string; role: Role } }
export interface Me { username: string; role: Role }

export type TraceStatus = 'OK' | 'ERROR';
export type CompletionState = 'in_progress' | 'complete' | 'incomplete';

export interface TraceSummary {
  trace_id: string;
  root_service: string | null;
  correlation_id: string | null;
  start_time: string;
  end_time: string;
  duration_ms: number;
  status: TraceStatus;
  completion_state: CompletionState;
  completed_at: string | null;
  span_count: number;
  error_span_count: number;
  missing_parent_count: number;
}

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  service_name: string;
  operation_name: string;
  kind: string;
  start_time: string;
  end_time: string;
  duration_ms: number;
  status: TraceStatus;
  error_message: string | null;
  peer_service: string | null;
  attributes: Record<string, unknown>;
  depth: number;
  order: number;
  offset_ms: number;
  self_time_ms: number;
  children_count: number;
  is_error: boolean;
  is_slow: boolean;
  missing_parent: boolean;
  invalid_parent: boolean;
}

export interface TraceServiceBreakdown {
  service_name: string;
  span_count: number;
  error_count: number;
  self_time_ms: number;
  total_time_ms: number;
  latency_share: number;
}

export interface LogRecord {
  id: number;
  event_id: string;
  trace_id: string | null;
  span_id: string | null;
  correlation_id: string | null;
  service_name: string;
  severity: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
  message: string;
  timestamp: string;
  created_at: string;
  logger: string | null;
  attributes: Record<string, unknown>;
}

export interface ErrorPattern {
  id: number;
  service_name: string;
  pattern: string;
  sample_message: string;
  severity: string;
  occurrence_count: number;
  first_seen: string;
  last_seen: string;
  last_trace_id: string | null;
}

export interface Evidence {
  id: number;
  evidence_type: string;
  reference_id: string | null;
  description: string;
  service_name: string | null;
  observed_at: string | null;
  data: Record<string, unknown>;
}

export type Classification = 'confirmed_failure' | 'potential_cause' | 'latency_bottleneck' | 'insufficient_evidence' | 'healthy';
export type Confidence = 'high' | 'medium' | 'low' | 'insufficient';

export interface RankingEntry {
  service_name: string;
  score: number;
  classification: 'confirmed' | 'potential';
  evidence_types: string[];
  reasons: { rule: string; points: number; detail: string }[];
}

export interface TimelineEntry { timestamp: string | null; type: string; service_name: string | null; span_id?: string | null; message: string }

export interface Diagnostic {
  id: number;
  trace_id: string;
  affected_service: string | null;
  classification: Classification;
  severity: 'info' | 'low' | 'medium' | 'high' | 'critical';
  confidence_level: Confidence;
  summary: string;
  diagnosis: string;
  status: string;
  created_at: string;
  updated_at: string;
  analysis_count: number;
  trigger: 'event' | 'manual';
  rules_version: string;
  // present on the detail endpoint
  ranking?: RankingEntry[];
  failure_timeline?: TimelineEntry[];
  latency_contributions?: { service_name: string; self_time_ms: number; total_time_ms: number; latency_share: number; error_count: number }[];
  request_path?: { span_id: string; service_name: string; operation_name: string; status: string; duration_ms: number }[];
  service_path?: string[];
  first_failing_span?: Span | Record<string, unknown> | null;
  origin_span?: Record<string, unknown> | null;
  related_trace_ids?: string[];
  warnings?: ApiWarning[];
  evidence?: Evidence[];
}

export interface TraceDetail extends TraceSummary {
  has_root_span: boolean;
  slow_span_count: number;
  services: TraceServiceBreakdown[];
  missing_spans: { span_id: string; referenced_by: string[] }[];
  spans: Span[];
  logs: LogRecord[];
  logs_available: boolean;
  diagnostic: Diagnostic | null;
}

export interface ServiceMetrics {
  service_name: string;
  request_count: number;
  error_count: number;
  error_rate: number | null;
  avg_latency_ms: number | null;
  min_latency_ms: number | null;
  max_latency_ms: number | null;
  p50_latency_ms: number | null;
  p90_latency_ms: number | null;
  p95_latency_ms: number | null;
  p99_latency_ms: number | null;
  throughput_rps: number | null;
  last_seen_at?: string;
  status?: 'healthy' | 'degraded';
}

export interface MetricsSummary extends Omit<ServiceMetrics, 'service_name'> {
  window: { start: string; end: string };
  services_observed: number;
  services_with_errors: { service_name: string; error_count: number; error_rate: number; request_count: number }[];
  active_alerts: number;
  critical_alerts: number;
  percentile_method: string;
}

export interface SeriesPoint {
  interval_start: string;
  interval_end: string;
  sample_count: number;
  error_count: number;
  error_rate: number | null;
  average: number | null;
  min: number | null;
  max: number | null;
  p50: number | null;
  p95: number | null;
  p99: number | null;
  throughput_rps: number | null;
  missing: boolean;
}
export interface Timeseries { service_name: string | null; metric_name: string; bucket_seconds: number; source: string; percentile_method: string; points: SeriesPoint[] }

export interface LatencyReport {
  window: { start: string; end: string };
  percentile_method: string;
  services: { service_name: string; count: number; avg_ms: number; min_ms: number; max_ms: number; p50_ms: number; p90_ms: number; p95_ms: number; p99_ms: number }[];
  histogram: { le_ms: number | null; count: number; cumulative: number }[];
}

export type AlertStatus = 'open' | 'acknowledged' | 'resolved';
export interface Alert {
  id: number;
  service_name: string;
  alert_type: 'error_rate' | 'avg_latency' | 'p95_latency' | 'request_volume';
  severity: 'warning' | 'critical';
  status: AlertStatus;
  measured_value: number;
  threshold_value: number;
  operator: 'gt' | 'lt';
  message: string;
  first_seen_at: string;
  last_seen_at: string;
  occurrence_count: number;
  evidence: Record<string, unknown>;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
  resolution: string | null;
}
export interface AlertRule {
  id: number;
  service_name: string;
  alert_type: Alert['alert_type'];
  operator: 'gt' | 'lt';
  threshold: number;
  window_seconds: number;
  min_samples: number;
  severity: 'warning' | 'critical';
  enabled: boolean;
  updated_at: string;
}

export type Health = 'healthy' | 'degraded' | 'critical' | 'unknown';
export interface DependencyNode {
  service_name: string;
  request_count: number;
  error_count: number;
  error_rate: number | null;
  avg_latency_ms: number | null;
  observed_as_peer_only: boolean;
  metrics?: ServiceMetrics | null;
  active_alerts?: number;
  health?: Health;
}
export interface DependencyEdge {
  source: string;
  target: string;
  request_count: number;
  error_count: number;
  error_rate: number | null;
  avg_latency_ms: number | null;
  max_latency_ms: number;
  unanswered_calls: number;
  failure_observed: boolean;
}
export interface DependencyGraph { window: { start: string; end: string }; nodes: DependencyNode[]; edges: DependencyEdge[] }

export interface Overview {
  window: { start: string; end: string };
  metrics: MetricsSummary | null;
  services: ServiceMetrics[] | null;
  alerts: Alert[] | null;
  failed_traces: TraceSummary[] | null;
  slow_traces: TraceSummary[] | null;
  diagnostics: Diagnostic[] | null;
  degraded_sources: string[];
}

export interface SystemHealth {
  status: 'up' | 'degraded' | 'down';
  checked_at: string;
  services: { service_name: string; status: 'up' | 'degraded' | 'down'; latency_ms: number | null; http_status: number | null; checks: Record<string, unknown>; detail: string | null }[];
}

export interface Scenario { name: string; description: string; expected_diagnosis: string }
export interface SampleOrder {
  order_id: number;
  customer_id: string;
  total_amount: number;
  currency: string;
  status: string;
  payment_id: number | null;
  notification_status: string;
  scenario: string;
  failure_reason: string | null;
  correlation_id: string | null;
}
