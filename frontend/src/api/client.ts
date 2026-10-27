import axios, { AxiosError, type AxiosRequestConfig } from 'axios';
import type { ApiErrorBody, ApiWarning, Envelope } from './types';

const TOKEN_KEY = 'obs.token';

export const tokenStore = {
  get(): string | null {
    try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
  },
  set(token: string): void {
    try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* storage unavailable: session lives in memory only */ }
  },
  clear(): void {
    try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
  },
};

/** Normalised error: tells the user WHICH component failed (gateway vs a named downstream service). */
export class ApiError extends Error {
  status: number;
  code: string;
  source: 'gateway' | 'service' | 'downstream' | 'network';
  service: string | null;
  correlationId: string | null;
  details: unknown;

  constructor(init: { status: number; code: string; message: string; source: ApiError['source']; service?: string | null; correlationId?: string | null; details?: unknown }) {
    super(init.message);
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.source = init.source;
    this.service = init.service ?? null;
    this.correlationId = init.correlationId ?? null;
    this.details = init.details;
  }
}

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  if (axios.isAxiosError(err)) {
    const ax = err as AxiosError<Partial<Envelope<unknown>>>;
    const correlationId = (ax.response?.headers?.['x-correlation-id'] as string | undefined) ?? null;
    if (!ax.response) {
      const timeout = ax.code === 'ECONNABORTED';
      return new ApiError({
        status: 0,
        code: timeout ? 'timeout' : 'network_error',
        message: timeout ? 'The request timed out before the server answered.' : 'Cannot reach the server. Check your connection.',
        source: 'network',
      });
    }
    const body: Partial<ApiErrorBody> = ax.response.data?.error ?? {};
    return new ApiError({
      status: ax.response.status,
      code: body.code ?? `http_${ax.response.status}`,
      message: body.message ?? ax.message,
      source: body.source ?? 'gateway',
      service: body.service ?? null,
      correlationId,
      details: body.details,
    });
  }
  return new ApiError({ status: 0, code: 'unexpected', message: err instanceof Error ? err.message : 'Unexpected error', source: 'network' });
}

export const http = axios.create({ baseURL: '/api/v1', timeout: 20_000, headers: { Accept: 'application/json' } });

http.interceptors.request.use((config) => {
  const token = tokenStore.get();
  if (token) config.headers.set('Authorization', `Bearer ${token}`);
  return config;
});

export const AUTH_EXPIRED_EVENT = 'obs:auth-expired';

http.interceptors.response.use(
  (r) => r,
  (error) => {
    const apiError = toApiError(error);
    const isLogin = (error?.config?.url ?? '').includes('/auth/login');
    if (apiError.status === 401 && !isLogin) {
      tokenStore.clear();
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
    }
    return Promise.reject(apiError);
  },
);

export interface ApiResult<T> {
  data: T;
  meta: Envelope<T>['meta'];
  warnings: ApiWarning[];
  headers: Record<string, string>;
}

/** Perform a request and unwrap the envelope. */
export async function call<T>(config: AxiosRequestConfig): Promise<ApiResult<T>> {
  const res = await http.request<Envelope<T>>(config);
  return { data: res.data.data, meta: res.data.meta ?? null, warnings: res.data.warnings ?? [], headers: res.headers as Record<string, string> };
}

export const get = <T>(url: string, params?: Record<string, unknown>, signal?: AbortSignal) =>
  call<T>({ method: 'GET', url, params: cleanParams(params), signal, paramsSerializer: { indexes: null } });

export function cleanParams(params?: Record<string, unknown>): Record<string, unknown> | undefined {
  if (!params) return undefined;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0)) continue;
    out[k] = v;
  }
  return out;
}
