import { bucketFor, formatMs, formatPct, parseTime, relativeTime, shortId } from './format';

describe('format helpers', () => {
  it('formats durations with sensible units', () => {
    expect(formatMs(null)).toBe('—');
    expect(formatMs(4.26)).toBe('4.3 ms');
    expect(formatMs(250)).toBe('250 ms');
    expect(formatMs(1500)).toBe('1.50 s');
    expect(formatMs(90_000)).toBe('1.5 min');
  });

  it('formats percentages and ids', () => {
    expect(formatPct(0.1234)).toBe('12.3%');
    expect(formatPct(undefined)).toBe('—');
    expect(shortId('abcdef0123456789')).toBe('abcdef01');
    expect(shortId(null)).toBe('—');
  });

  it('treats timestamps without a zone as UTC', () => {
    expect(parseTime('2026-10-16T10:00:00')?.toISOString()).toBe('2026-10-16T10:00:00.000Z');
    expect(parseTime('2026-10-16T10:00:00Z')?.toISOString()).toBe('2026-10-16T10:00:00.000Z');
    expect(parseTime('2026-10-16T12:00:00+02:00')?.toISOString()).toBe('2026-10-16T10:00:00.000Z');
    expect(parseTime('nonsense')).toBeNull();
  });

  it('describes relative time', () => {
    const now = Date.parse('2026-10-16T10:10:00Z');
    expect(relativeTime('2026-10-16T10:09:58Z', now)).toBe('just now');
    expect(relativeTime('2026-10-16T10:05:00Z', now)).toBe('5m ago');
    expect(relativeTime('2026-10-16T07:10:00Z', now)).toBe('3h ago');
  });

  it('keeps chart point counts bounded', () => {
    for (const minutes of [15, 60, 360, 1440, 10_080]) {
      const points = (minutes * 60) / bucketFor(minutes);
      expect(points).toBeLessThanOrEqual(1000);
      expect(points).toBeGreaterThan(5);
    }
  });
});
