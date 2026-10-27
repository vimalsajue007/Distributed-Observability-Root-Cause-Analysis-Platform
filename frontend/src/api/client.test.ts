import { AxiosError, type AxiosResponse } from 'axios';
import { ApiError, cleanParams, toApiError, tokenStore } from './client';

function axiosError(status: number | null, body?: unknown, headers: Record<string, string> = {}): AxiosError {
  const err = new AxiosError('failed');
  if (status !== null) err.response = { status, data: body, headers, statusText: '', config: {} as never } as AxiosResponse;
  return err;
}

describe('error normalisation', () => {
  it('names the failing downstream service', () => {
    const e = toApiError(axiosError(504, { error: { code: 'downstream_timeout', message: 'trace-service did not respond in time', source: 'downstream', service: 'trace-service' } }, { 'x-correlation-id': 'abc123' }));
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(504);
    expect(e.source).toBe('downstream');
    expect(e.service).toBe('trace-service');
    expect(e.correlationId).toBe('abc123');
  });

  it('defaults to a gateway error when the body has no source', () => {
    expect(toApiError(axiosError(401, { error: { code: 'unauthorized', message: 'Authentication required' } })).source).toBe('gateway');
  });

  it('reports network failures separately', () => {
    const e = toApiError(axiosError(null));
    expect(e.source).toBe('network');
    expect(e.code).toBe('network_error');
  });
});

describe('params and token storage', () => {
  it('drops empty parameters', () => {
    expect(cleanParams({ a: 1, b: '', c: null, d: undefined, e: [], f: ['x'], g: 0 })).toEqual({ a: 1, f: ['x'], g: 0 });
  });

  it('keeps the token in session storage and can clear it', () => {
    tokenStore.set('t0k3n');
    expect(tokenStore.get()).toBe('t0k3n');
    tokenStore.clear();
    expect(tokenStore.get()).toBeNull();
  });
});
