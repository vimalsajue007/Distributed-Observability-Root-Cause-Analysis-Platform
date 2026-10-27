import { call, get } from './client';
import type {
  Alert, AlertRule, AlertStatus, DependencyGraph, Diagnostic, ErrorPattern, LatencyReport, LogRecord, LoginResult, Me, MetricsSummary, Overview,
  SampleOrder, Scenario, ServiceMetrics, SystemHealth, Timeseries, TraceDetail, TraceSummary,
} from './types';

type P = Record<string, unknown>;

export const api = {
  login: (username: string, password: string) => call<LoginResult>({ method: 'POST', url: '/auth/login', data: { username, password } }),
  me: () => get<Me>('/auth/me'),

  overview: (windowMinutes: number, signal?: AbortSignal) => get<Overview>('/overview', { window_minutes: windowMinutes }, signal),
  systemHealth: (signal?: AbortSignal) => get<SystemHealth>('/system/health', undefined, signal),

  traces: (p: P, signal?: AbortSignal) => get<TraceSummary[]>('/traces', p, signal),
  trace: (id: string, signal?: AbortSignal) => get<TraceDetail>(`/traces/${id}`, undefined, signal),

  logs: (p: P, signal?: AbortSignal) => get<LogRecord[]>('/logs', p, signal),
  logPatterns: (p: P, signal?: AbortSignal) => get<ErrorPattern[]>('/logs/patterns', p, signal),

  dependencies: (p: P, signal?: AbortSignal) => get<DependencyGraph>('/dependencies', p, signal),

  metricsSummary: (p: P, signal?: AbortSignal) => get<MetricsSummary>('/metrics/summary', p, signal),
  metricsServices: (p: P, signal?: AbortSignal) => get<ServiceMetrics[]>('/metrics/services', p, signal),
  metricsTimeseries: (p: P, signal?: AbortSignal) => get<Timeseries>('/metrics/timeseries', p, signal),
  metricsLatency: (p: P, signal?: AbortSignal) => get<LatencyReport>('/metrics/latency', p, signal),

  alerts: (p: P, signal?: AbortSignal) => get<Alert[]>('/alerts', p, signal),
  alertRules: (signal?: AbortSignal) => get<AlertRule[]>('/alerts/rules', undefined, signal),
  setAlertStatus: (id: number, status: AlertStatus) => call<Alert>({ method: 'PATCH', url: `/alerts/${id}`, data: { status } }),
  upsertRule: (rule: Omit<AlertRule, 'id' | 'updated_at'>) => call<AlertRule>({ method: 'PUT', url: '/alerts/rules', data: rule }),

  diagnostics: (p: P, signal?: AbortSignal) => get<Diagnostic[]>('/diagnostics', p, signal),
  diagnostic: (id: string | number, signal?: AbortSignal) => get<Diagnostic>(`/diagnostics/${id}`, undefined, signal),
  analyze: (traceId: string) => call<Diagnostic>({ method: 'POST', url: `/diagnostics/analyze/${traceId}` }),

  scenarios: (signal?: AbortSignal) => get<Scenario[]>('/sample/scenarios', undefined, signal),
  createSampleOrder: (scenario: string) =>
    call<SampleOrder>({
      method: 'POST',
      url: '/sample/orders',
      headers: { 'X-Scenario': scenario },
      data: { customer_id: `console-${Math.floor(Math.random() * 900 + 100)}`, currency: 'USD', items: [{ sku: 'SKU-DEMO', quantity: 1 + Math.floor(Math.random() * 3), unit_price: 19.99 }] },
    }),
};
