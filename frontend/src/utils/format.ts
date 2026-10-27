export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—';
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)} min`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  if (ms >= 10) return `${ms.toFixed(0)} ms`;
  return `${ms.toFixed(1)} ms`;
}

export function formatPct(rate: number | null | undefined, digits = 1): string {
  if (rate === null || rate === undefined || Number.isNaN(rate)) return '—';
  return `${(rate * 100).toFixed(digits)}%`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return new Intl.NumberFormat().format(n);
}

/** Backend timestamps are UTC ISO-8601; some arrive without the "Z". */
export function parseTime(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const hasZone = /([zZ]|[+-]\d\d:?\d\d)$/.test(iso);
  const d = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatTime(iso: string | null | undefined, withDate = true): string {
  const d = parseTime(iso);
  if (!d) return '—';
  const time = d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  if (!withDate) return time;
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`;
}

export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  const d = parseTime(iso);
  if (!d) return '—';
  const s = Math.max(0, Math.round((now - d.getTime()) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86_400)}d ago`;
}

export const shortId = (id: string | null | undefined, n = 8): string => (id ? id.slice(0, n) : '—');

export function humanize(key: string): string {
  const s = key.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export const WINDOWS: { label: string; minutes: number }[] = [
  { label: 'Last 15 minutes', minutes: 15 },
  { label: 'Last hour', minutes: 60 },
  { label: 'Last 6 hours', minutes: 360 },
  { label: 'Last 24 hours', minutes: 1440 },
  { label: 'Last 7 days', minutes: 10_080 },
];

/** Pick a chart bucket so a window yields roughly 30-90 points (and never more than the API's 1000). */
export function bucketFor(windowMinutes: number): number {
  const seconds = windowMinutes * 60;
  const candidates = [10, 30, 60, 300, 600, 1800, 3600, 7200, 21600];
  return candidates.find((c) => seconds / c <= 90) ?? 21600;
}
