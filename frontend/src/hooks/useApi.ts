import { useCallback, useEffect, useRef, useState } from 'react';
import type { ApiResult } from '../api/client';
import { ApiError, toApiError } from '../api/client';
import type { ApiWarning } from '../api/types';
import { useRefresh } from '../components/RefreshContext';

export interface ApiState<T> {
  data: T | null;
  meta: ApiResult<T>['meta'];
  warnings: ApiWarning[];
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Load data when `deps` change; re-load on the global auto-refresh tick. A newer request supersedes an older one
 * (stale responses never overwrite fresh state) and unmounting aborts the request.
 */
export function useApi<T>(fn: (signal: AbortSignal) => Promise<ApiResult<T>>, deps: unknown[], opts: { enabled?: boolean; poll?: boolean } = {}): ApiState<T> {
  const { enabled = true, poll = true } = opts;
  const { tick } = useRefresh();
  const [state, setState] = useState<Omit<ApiState<T>, 'reload'>>({ data: null, meta: null, warnings: [], error: null, loading: enabled });
  const seq = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const [manual, setManual] = useState(0);
  const reload = useCallback(() => setManual((n) => n + 1), []);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const key = JSON.stringify(deps);
  useEffect(() => {
    if (!enabled) {
      setState((s) => ({ ...s, loading: false }));
      return;
    }
    const controller = new AbortController();
    const mine = ++seq.current;
    setState((s) => ({ ...s, loading: s.data === null })); // keep showing old data while refreshing
    fnRef.current(controller.signal)
      .then((r) => {
        if (mine === seq.current) setState({ data: r.data, meta: r.meta, warnings: r.warnings, error: null, loading: false });
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted || mine !== seq.current) return;
        const err = toApiError(e);
        if (err.code === 'ERR_CANCELED') return;
        setState((s) => ({ ...s, error: err, loading: false }));
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled, manual, poll ? tick : 0]);

  return { ...state, reload };
}
