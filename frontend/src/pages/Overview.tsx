import BugReportIcon from '@mui/icons-material/BugReport';
import NotificationsActiveIcon from '@mui/icons-material/NotificationsActive';
import SpeedIcon from '@mui/icons-material/Speed';
import SwapVertIcon from '@mui/icons-material/SwapVert';
import { Box, Chip, Link as MuiLink, Stack, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import { Link } from 'react-router-dom';
import { api } from '../api/endpoints';
import { ClassificationChip, EmptyState, ErrorState, HealthDot, LoadingBlock, Mono, PageHeader, Section, SeverityChip, StatCard, StatGrid, StatusChip, WarningsBanner } from '../components/common';
import { useRefresh } from '../components/RefreshContext';
import { useApi } from '../hooks/useApi';
import { statusColors } from '../theme/theme';
import { formatMs, formatNumber, formatPct, formatTime, relativeTime, shortId } from '../utils/format';

function Unavailable({ what }: { what: string }) {
  return <EmptyState title={`${what} unavailable`} hint="The service that provides this data did not answer. The rest of the page is still accurate." />;
}

export function Overview() {
  const { windowMinutes } = useRefresh();
  const overview = useApi((s) => api.overview(windowMinutes, s), [windowMinutes]);
  const health = useApi((s) => api.systemHealth(s), []);
  const o = overview.data;
  const m = o?.metrics ?? null;

  return (
    <>
      <PageHeader title="Overview" subtitle="Live health of the platform and the sample application: traffic, errors, latency, recent failures and what the root-cause engine found." />
      {overview.error && <ErrorState error={overview.error} onRetry={overview.reload} />}
      <WarningsBanner warnings={overview.warnings} />
      {overview.loading && !o ? <LoadingBlock rows={3} height={90} /> : o && (
        <>
          <StatGrid>
            <StatCard label="Requests" value={m ? formatNumber(m.request_count) : '—'} hint={m ? `${formatNumber(m.services_observed)} services observed` : 'metrics unavailable'} icon={<SwapVertIcon />} />
            <StatCard label="Error rate" value={m ? formatPct(m.error_rate) : '—'} hint={m ? `${formatNumber(m.error_count)} failed requests` : undefined} accent={m && (m.error_rate ?? 0) > 0.05 ? statusColors.error : statusColors.ok} icon={<BugReportIcon />} />
            <StatCard label="p95 latency" value={m ? formatMs(m.p95_latency_ms) : '—'} hint={m ? `avg ${formatMs(m.avg_latency_ms)} · p99 ${formatMs(m.p99_latency_ms)}` : undefined} accent="#22d3ee" icon={<SpeedIcon />} />
            <StatCard label="Active alerts" value={m ? m.active_alerts : (o.alerts?.length ?? '—')} hint={m ? `${m.critical_alerts} critical` : undefined} accent={m && m.critical_alerts > 0 ? statusColors.error : statusColors.warn} icon={<NotificationsActiveIcon />} />
          </StatGrid>

          <Box sx={{ display: 'grid', gap: 2.5, gridTemplateColumns: { xs: '1fr', lg: '3fr 2fr' }, mb: 2.5 }}>
            <Section title="Services">
              {o.services === null ? <Unavailable what="Service metrics" /> : o.services.length === 0 ? (
                <EmptyState title="No traffic in this window" hint="Run a scenario in the Scenario Lab or widen the time window." action={<MuiLink component={Link} to="/scenarios">Open Scenario Lab</MuiLink>} />
              ) : (
                <Box sx={{ overflowX: 'auto' }}>
                  <Table size="small">
                    <TableHead><TableRow><TableCell>Service</TableCell><TableCell align="right">Requests</TableCell><TableCell align="right">Errors</TableCell><TableCell align="right">Avg</TableCell><TableCell align="right">p95</TableCell><TableCell>Status</TableCell></TableRow></TableHead>
                    <TableBody>
                      {o.services.map((s) => (
                        <TableRow key={s.service_name} hover>
                          <TableCell><MuiLink component={Link} to={`/metrics?service=${s.service_name}`} underline="hover" fontWeight={600}>{s.service_name}</MuiLink></TableCell>
                          <TableCell align="right">{formatNumber(s.request_count)}</TableCell>
                          <TableCell align="right" sx={{ color: (s.error_rate ?? 0) > 0 ? 'error.main' : undefined }}>{formatPct(s.error_rate)}</TableCell>
                          <TableCell align="right">{formatMs(s.avg_latency_ms)}</TableCell>
                          <TableCell align="right">{formatMs(s.p95_latency_ms)}</TableCell>
                          <TableCell><StatusChip status={s.status ?? 'healthy'} /></TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
              )}
            </Section>

            <Section title="Platform components" action={health.data && <StatusChip status={health.data.status} />}>
              {health.error ? <ErrorState error={health.error} onRetry={health.reload} compact /> : !health.data ? <LoadingBlock rows={5} height={26} /> : (
                <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 1 }}>
                  {health.data.services.map((s) => (
                    <Tooltip key={s.service_name} title={s.detail ?? (s.latency_ms !== null ? `${s.latency_ms} ms` : '')}>
                      <Stack direction="row" spacing={1} alignItems="center" sx={{ p: 1, borderRadius: 2, border: 1, borderColor: 'divider' }}>
                        <HealthDot health={s.status} />
                        <Typography variant="body2" noWrap>{s.service_name}</Typography>
                      </Stack>
                    </Tooltip>
                  ))}
                </Box>
              )}
            </Section>
          </Box>

          <Box sx={{ display: 'grid', gap: 2.5, gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' }, mb: 2.5 }}>
            <Section title="Latest root-cause findings" action={<MuiLink component={Link} to="/root-cause">All reports</MuiLink>}>
              {o.diagnostics === null ? <Unavailable what="Root-cause reports" /> : o.diagnostics.length === 0 ? <EmptyState title="No failures analysed yet" hint="Failed or slow traces are analysed automatically once they complete." /> : (
                <Stack spacing={1.25}>
                  {o.diagnostics.map((d) => (
                    <Box key={d.id} component={Link} to={`/root-cause/${d.id}`} sx={{ p: 1.5, borderRadius: 2, border: 1, borderColor: 'divider', color: 'inherit', textDecoration: 'none', '&:hover': { borderColor: 'primary.main' } }}>
                      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 0.5 }}>
                        <ClassificationChip value={d.classification} />
                        {d.affected_service && <Chip size="small" variant="outlined" label={d.affected_service} />}
                        <Typography variant="caption" color="text.secondary">{relativeTime(d.created_at)}</Typography>
                      </Stack>
                      <Typography variant="body2" color="text.secondary" sx={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{d.summary}</Typography>
                    </Box>
                  ))}
                </Stack>
              )}
            </Section>

            <Section title="Active alerts" action={<MuiLink component={Link} to="/alerts">Alert Center</MuiLink>}>
              {o.alerts === null ? <Unavailable what="Alerts" /> : o.alerts.length === 0 ? <EmptyState title="No active alerts" hint="Thresholds are healthy for the observed services." /> : (
                <Stack spacing={1}>
                  {o.alerts.map((a) => (
                    <Stack key={a.id} direction="row" spacing={1.25} alignItems="center" sx={{ p: 1.25, borderRadius: 2, border: 1, borderColor: 'divider' }}>
                      <SeverityChip severity={a.severity} />
                      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                        <Typography variant="body2" fontWeight={600} noWrap>{a.service_name}</Typography>
                        <Typography variant="caption" color="text.secondary" noWrap component="div">{a.message}</Typography>
                      </Box>
                      <Typography variant="caption" color="text.secondary">{relativeTime(a.last_seen_at)}</Typography>
                    </Stack>
                  ))}
                </Stack>
              )}
            </Section>
          </Box>

          <Box sx={{ display: 'grid', gap: 2.5, gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' } }}>
            <TraceList title="Recent failed traces" traces={o.failed_traces} empty="No failed traces in this window." />
            <TraceList title="Slow traces (≥ 1 s)" traces={o.slow_traces} empty="No slow traces in this window." />
          </Box>
        </>
      )}
    </>
  );
}

function TraceList({ title, traces, empty }: { title: string; traces: import('../api/types').TraceSummary[] | null; empty: string }) {
  return (
    <Section title={title} action={<MuiLink component={Link} to="/traces">Trace Explorer</MuiLink>}>
      {traces === null ? <Unavailable what="Traces" /> : traces.length === 0 ? <EmptyState title={empty} /> : (
        <Box sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead><TableRow><TableCell>Trace</TableCell><TableCell>Root</TableCell><TableCell align="right">Duration</TableCell><TableCell>Status</TableCell><TableCell>Started</TableCell></TableRow></TableHead>
            <TableBody>
              {traces.map((t) => (
                <TableRow key={t.trace_id} hover>
                  <TableCell><MuiLink component={Link} to={`/traces/${t.trace_id}`}><Mono>{shortId(t.trace_id)}</Mono></MuiLink></TableCell>
                  <TableCell>{t.root_service ?? '—'}</TableCell>
                  <TableCell align="right">{formatMs(t.duration_ms)}</TableCell>
                  <TableCell><StatusChip status={t.status} /></TableCell>
                  <TableCell>{formatTime(t.start_time)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
    </Section>
  );
}
