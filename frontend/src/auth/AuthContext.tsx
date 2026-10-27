import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/endpoints';
import { AUTH_EXPIRED_EVENT, tokenStore } from '../api/client';
import type { Me, Role } from '../api/types';

const RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 };

interface Ctx {
  user: Me | null;
  loading: boolean;
  expired: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  can: (minimum: Role) => boolean;
}

const AuthContext = createContext<Ctx | null>(null);

export function useAuth(): Ctx {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState<boolean>(() => tokenStore.get() !== null);
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    if (!tokenStore.get()) return;
    api.me().then((r) => setUser(r.data)).catch(() => tokenStore.clear()).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onExpired = () => { setUser(null); setExpired(true); };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const r = await api.login(username, password);
    tokenStore.set(r.data.access_token);
    setExpired(false);
    setUser(r.data.user);
  }, []);

  const logout = useCallback(() => { tokenStore.clear(); setUser(null); setExpired(false); }, []);
  const can = useCallback((minimum: Role) => (user ? RANK[user.role] >= RANK[minimum] : false), [user]);

  const value = useMemo(() => ({ user, loading, expired, login, logout, can }), [user, loading, expired, login, logout, can]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
