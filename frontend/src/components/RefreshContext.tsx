import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { WINDOWS } from '../utils/format';

interface Ctx {
  windowMinutes: number;
  setWindowMinutes: (m: number) => void;
  autoRefresh: boolean;
  setAutoRefresh: (v: boolean) => void;
  /** increments on every auto-refresh interval (and on manual refresh) */
  tick: number;
  refreshNow: () => void;
}

const RefreshContext = createContext<Ctx>({ windowMinutes: 60, setWindowMinutes: () => undefined, autoRefresh: false, setAutoRefresh: () => undefined, tick: 0, refreshNow: () => undefined });
export const useRefresh = () => useContext(RefreshContext);
export const REFRESH_INTERVAL_MS = 15_000;

export function RefreshProvider({ children }: { children: ReactNode }) {
  const [windowMinutes, setWindowMinutes] = useState(WINDOWS[1].minutes);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = window.setInterval(() => {
      if (!document.hidden) setTick((t) => t + 1);
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [autoRefresh]);

  const value = useMemo(
    () => ({ windowMinutes, setWindowMinutes, autoRefresh, setAutoRefresh, tick, refreshNow: () => setTick((t) => t + 1) }),
    [windowMinutes, autoRefresh, tick],
  );
  return <RefreshContext.Provider value={value}>{children}</RefreshContext.Provider>;
}
