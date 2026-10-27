import { Box, FormControl, InputLabel, MenuItem, Select, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/endpoints';
import { EmptyState, ErrorState, LoadingBlock, PageHeader, Section, StatCard, StatGrid, StatusChip, WarningsBanner } from '../components/common';
import { useRefresh } from '../components/RefreshContext';
import { useApi } from '../hooks/useApi';
import { chartColors, statusColors } from '../theme/theme';
import { bucketFor, formatMs, formatNumber, formatPct, formatTime } from '../utils/format';

export function Metrics() {
  const { windowMinutes } = useRefresh();
  const [params, setParams] = useSearchParams();
  const service = params.get('service') ?? '';
  const bucket = bucketFor(windowMinutes);

  const services = useApi((s) => api.metricsServices({ window_minutes: windowMinutes }, s), [windowMinutes]);
  const summary = useApi((s) => api.metricsSummary({ window_minutes: windowMinutes, service_name: service }, s), [windowMinutes, service]);
  const series = useApi((s) => api.metricsTimeseries({ window_minutes: windowMinutes, service_name: service, bucket_seconds: bucket }, s), [windowMinutes, service, bucket]);
  const latency = useApi((s) => api.metricsLatency({ window_minutes: windowMinutes, service_name: service }, s), [windowMinutes, service]);

  const points = (series.data?.points ?? []).map((p) => ({
    t: formatTime(p.interval_start, windowMinutes > 1440), avg: p.average, p95: p.p95, p99: p.p99, rps: p.throughput_rps,
    errPct: p.error_rate === null ? null : Number((p.error_rate * 100).toFixed(2)), missing: p.missing, count: p.sample_count,
  }));
  const hasData = points.some((p) => !p.missing);
  const gaps = points.filter((p) => p.missing).length;
  const s = summary.data;

  return (
    <>
      <PageHeader
        title="Metrics"
        subtitle="Request volume, error rate and latency percentiles per service. Percentiles use the nearest-rank method on raw samples; intervals without data are shown as gaps, never as zero."
        actions={
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel id="ms">Service</InputLabel>
            <Select labelId="ms" label="Service" value={service} onChange={(e) => { const n = new URLSearchParams(params); if (e.target.value) n.set('service', e.target.value); else n.delete('service'); setParams(n, { replace: true }); }}>
              <MenuItem value="">All services</MenuItem>
              {(services.data ?? []).map((x) => <MenuItem key={x.service_name} value={x.service_name}>{x.service_name}</MenuItem>)}
            </Select>
          </FormControl>
        }
      />
      {[summary, series, latency, services].map((r, i) => r.error && <Box key={i} sx={{ mb: 2 }}><ErrorState error={r.error} onRetry={r.reload} /></Box>)}
      <WarningsBanner warnings={[...summary.warnings, ...series.warnings]} />

      <StatGrid>
        <StatCard label="Requests" value={s ? formatNumber(s.request_count) : '—'} hint={s?.throughput_rps != null ? `${s.throughput_rps} req/s` : undefined} />
        <StatCard label="Error rate" value={s ? formatPct(s.error_rate) : '—'} hint={s ? `${formatNumber(s.error_count)} errors` : undefined} accent={s && (s.error_rate ?? 0) > 0.05 ? statusColors.error : statusColors.ok} />
        <StatCard label="Avg latency" value={s ? formatMs(s.avg_latency_ms) : '—'} hint={s ? `min ${formatMs(s.min_latency_ms)} · max ${formatMs(s.max_latency_ms)}` : undefined} accent="#22d3ee" />
        <StatCard label="p95 / p99" value={s ? `${formatMs(s.p95_latency_ms)} / ${formatMs(s.p99_latency_ms)}` : '—'} hint="nearest-rank" accent="#f472b6" />
      </StatGrid>

      <Box sx={{ display: 'grid', gap: 2.5, gridTemplateColumns: { xs: '1fr', xl: '1fr 1fr' }, mb: 2.5 }}>
        <Section title="Latency over time">
          {series.loading && !series.data ? <LoadingBlock rows={1} height={300} /> : !hasData ? <EmptyState title="No latency data in this window" hint="Intervals with no samples are left empty on purpose." /> : (
            <Box sx={{ height: 320 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                  <XAxis dataKey="t" minTickGap={28} tick={{ fontSize: 12 }} />
                  <YAxis unit=" ms" tick={{ fontSize: 12 }} width={70} />
                  <RTooltip formatter={(v) => (typeof v === 'number' ? formatMs(v) : 'no data')} />
                  <Legend />
                  <Line type="monotone" dataKey="avg" name="avg" stroke={chartColors[0]} strokeWidth={2} dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="p95" name="p95" stroke={chartColors[1]} strokeWidth={2} dot={false} connectNulls={false} />
                  <Line type="monotone" dataKey="p99" name="p99" stroke={chartColors[2]} strokeWidth={2} dot={false} connectNulls={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </Box>
          )}
          {gaps > 0 && hasData && <Typography variant="caption" color="text.secondary">{gaps} interval(s) had no samples and are shown as gaps.</Typography>}
        </Section>

        <Section title="Throughput and error rate">
          {series.loading && !series.data ? <LoadingBlock rows={1} height={300} /> : !hasData ? <EmptyState title="No traffic in this window" /> : (
            <Box sx={{ height: 320 }}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                  <XAxis dataKey="t" minTickGap={28} tick={{ fontSize: 12 }} />
                  <YAxis yAxisId="l" tick={{ fontSize: 12 }} width={50} />
                  <YAxis yAxisId="r" orientation="right" unit="%" tick={{ fontSize: 12 }} width={50} />
                  <RTooltip />
                  <Legend />
                  <Bar yAxisId="l" dataKey="count" name="requests" fill={chartColors[0]} radius={[4, 4, 0, 0]} />
                  <Line yAxisId="r" type="monotone" dataKey="errPct" name="error %" stroke={statusColors.error} strokeWidth={2} dot={false} connectNulls={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </Box>
          )}
        </Section>
      </Box>

      <Box sx={{ display: 'grid', gap: 2.5, gridTemplateColumns: { xs: '1fr', xl: '1fr 1fr' } }}>
        <Section title="Latency distribution">
          {latency.loading && !latency.data ? <LoadingBlock rows={1} height={260} /> : !latency.data || latency.data.histogram.every((h) => h.count === 0) ? <EmptyState title="No samples" /> : (
            <Box sx={{ height: 280 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={latency.data.histogram.map((h) => ({ bucket: h.le_ms === null ? '+Inf' : `≤ ${h.le_ms}`, count: h.count }))}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                  <XAxis dataKey="bucket" tick={{ fontSize: 11 }} interval={0} angle={-30} textAnchor="end" height={55} />
                  <YAxis allowDecimals={false} width={44} />
                  <RTooltip />
                  <Bar dataKey="count" name="requests" fill={chartColors[3]} radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Box>
          )}
        </Section>

        <Section title="Per-service latency percentiles">
          {!latency.data || latency.data.services.length === 0 ? <EmptyState title="No services reported latency" /> : (
            <Box sx={{ overflowX: 'auto' }}>
              <Table size="small">
                <TableHead><TableRow><TableCell>Service</TableCell><TableCell align="right">Count</TableCell><TableCell align="right">Avg</TableCell><TableCell align="right">p50</TableCell><TableCell align="right">p90</TableCell><TableCell align="right">p95</TableCell><TableCell align="right">p99</TableCell><TableCell align="right">Max</TableCell></TableRow></TableHead>
                <TableBody>
                  {latency.data.services.map((x) => (
                    <TableRow key={x.service_name} hover>
                      <TableCell>{x.service_name}</TableCell><TableCell align="right">{formatNumber(x.count)}</TableCell><TableCell align="right">{formatMs(x.avg_ms)}</TableCell>
                      <TableCell align="right">{formatMs(x.p50_ms)}</TableCell><TableCell align="right">{formatMs(x.p90_ms)}</TableCell><TableCell align="right">{formatMs(x.p95_ms)}</TableCell>
                      <TableCell align="right">{formatMs(x.p99_ms)}</TableCell><TableCell align="right">{formatMs(x.max_ms)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
          {services.data && services.data.length > 0 && (
            <Box sx={{ mt: 2, overflowX: 'auto' }}>
              <Table size="small">
                <TableHead><TableRow><TableCell>Service</TableCell><TableCell align="right">Requests</TableCell><TableCell align="right">Error rate</TableCell><TableCell align="right">Throughput</TableCell><TableCell>Status</TableCell></TableRow></TableHead>
                <TableBody>
                  {services.data.map((x) => (
                    <TableRow key={x.service_name}><TableCell>{x.service_name}</TableCell><TableCell align="right">{formatNumber(x.request_count)}</TableCell><TableCell align="right">{formatPct(x.error_rate)}</TableCell><TableCell align="right">{x.throughput_rps ?? '—'} rps</TableCell><TableCell><StatusChip status={x.status ?? 'healthy'} /></TableCell></TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </Section>
      </Box>
    </>
  );
}
