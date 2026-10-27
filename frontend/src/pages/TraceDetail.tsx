import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import InsightsIcon from '@mui/icons-material/Insights';
import { Alert, Box, Button, Chip, Link as MuiLink, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Typography } from '@mui/material';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts';
import { toApiError } from '../api/client';
import { api } from '../api/endpoints';
import { useAuth } from '../auth/AuthContext';
import { ClassificationChip, ConfidenceChip, EmptyState, ErrorState, LoadingBlock, Mono, PageHeader, Section, SeverityChip, StatCard, StatusChip, WarningsBanner } from '../components/common';
import { Waterfall } from '../components/Waterfall';
import { useApi } from '../hooks/useApi';
import { chartColors } from '../theme/theme';
import { formatMs, formatPct, formatTime } from '../utils/format';

export function TraceDetail() {
  const { traceId = '' } = useParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const [tab, setTab] = useState(0);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const trace = useApi((s) => api.trace(traceId, s), [traceId]);
  const t = trace.data;

  const analyze = async () => {
    setBusy(true);
    setActionError(null);
    try {
      const r = await api.analyze(traceId);
      navigate(`/root-cause/${r.data.id}`);
    } catch (e) {
      setActionError(toApiError(e).message);
    } finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader
        title="Trace"
        subtitle={traceId}
        actions={
          <>
            <Button startIcon={<ArrowBackIcon />} component={Link} to="/traces">All traces</Button>
            {t?.diagnostic ? (
              <Button variant="contained" startIcon={<InsightsIcon />} component={Link} to={`/root-cause/${t.diagnostic.id}`}>Root-cause report</Button>
            ) : can('operator') && t && (
              <Button variant="contained" startIcon={<InsightsIcon />} onClick={analyze} disabled={busy}>{busy ? 'Analysing…' : 'Run root-cause analysis'}</Button>
            )}
          </>
        }
      />
      {actionError && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setActionError(null)}>{actionError}</Alert>}
      {trace.error && <ErrorState error={trace.error} onRetry={trace.reload} />}
      <WarningsBanner warnings={trace.warnings} />
      {trace.loading && !t ? <LoadingBlock rows={6} height={48} /> : t && (
        <>
          <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, mb: 2.5 }}>
            <StatCard label="Status" value={<StatusChip status={t.status} />} hint={`${t.completion_state.replace('_', ' ')}${t.completed_at ? ` · ${formatTime(t.completed_at)}` : ''}`} />
            <StatCard label="Duration" value={formatMs(t.duration_ms)} hint={`started ${formatTime(t.start_time)}`} accent="#22d3ee" />
            <StatCard label="Spans" value={t.span_count} hint={`${t.error_span_count} failed · ${t.slow_span_count} slow`} />
            <StatCard label="Root service" value={t.root_service ?? '—'} hint={t.correlation_id ? `correlation ${t.correlation_id.slice(0, 12)}` : undefined} accent="#f472b6" />
          </Box>

          {t.diagnostic && (
            <Alert severity={t.diagnostic.classification === 'healthy' ? 'success' : t.diagnostic.classification === 'confirmed_failure' ? 'error' : 'warning'} sx={{ mb: 2.5, alignItems: 'flex-start' }}
              action={<Button color="inherit" size="small" component={Link} to={`/root-cause/${t.diagnostic.id}`}>Open</Button>}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 0.5 }}>
                <ClassificationChip value={t.diagnostic.classification} />
                <ConfidenceChip value={t.diagnostic.confidence_level} />
                {t.diagnostic.affected_service && <Chip size="small" variant="outlined" label={t.diagnostic.affected_service} />}
              </Stack>
              <Typography variant="body2">{t.diagnostic.diagnosis}</Typography>
            </Alert>
          )}

          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }} variant="scrollable">
            <Tab label={`Waterfall (${t.spans.length})`} />
            <Tab label={`Logs (${t.logs.length})`} />
            <Tab label="Services" />
            <Tab label="Missing spans" disabled={t.missing_spans.length === 0} />
          </Tabs>

          {tab === 0 && (
            <Section>
              {t.spans.length === 0 ? <EmptyState title="No spans" hint="This trace has no stored spans yet." /> : <Waterfall spans={t.spans} durationMs={t.duration_ms} highlightSpanId={t.diagnostic?.first_failing_span ? (t.diagnostic.first_failing_span as { span_id?: string }).span_id ?? null : null} />}
            </Section>
          )}

          {tab === 1 && (
            <Section>
              {!t.logs_available ? <EmptyState title="Logs are not available" hint="Either your role cannot read logs, or the log service did not respond. See the warning above." /> : t.logs.length === 0 ? <EmptyState title="No logs for this trace" /> : (
                <Box sx={{ overflowX: 'auto' }}>
                  <Table size="small">
                    <TableHead><TableRow><TableCell>Time</TableCell><TableCell>Severity</TableCell><TableCell>Service</TableCell><TableCell>Message</TableCell><TableCell>Span</TableCell></TableRow></TableHead>
                    <TableBody>
                      {t.logs.map((l) => (
                        <TableRow key={l.id} hover>
                          <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatTime(l.timestamp, false)}</TableCell>
                          <TableCell><SeverityChip severity={l.severity} /></TableCell>
                          <TableCell>{l.service_name}</TableCell>
                          <TableCell sx={{ maxWidth: 560, wordBreak: 'break-word' }}>{l.message}</TableCell>
                          <TableCell><Mono>{l.span_id?.slice(0, 8) ?? '—'}</Mono></TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
              )}
            </Section>
          )}

          {tab === 2 && (
            <Box sx={{ display: 'grid', gap: 2.5, gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' } }}>
              <Section title="Where the time went (self time)">
                <Box sx={{ height: 280 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={t.services} layout="vertical" margin={{ left: 24 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                      <XAxis type="number" unit=" ms" />
                      <YAxis type="category" dataKey="service_name" width={150} />
                      <RTooltip formatter={(v: number) => formatMs(v)} />
                      <Bar dataKey="self_time_ms" name="Self time" radius={[0, 6, 6, 0]}>
                        {t.services.map((_, i) => <Cell key={i} fill={chartColors[i % chartColors.length]} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </Box>
              </Section>
              <Section title="Per-service breakdown">
                <Table size="small">
                  <TableHead><TableRow><TableCell>Service</TableCell><TableCell align="right">Spans</TableCell><TableCell align="right">Errors</TableCell><TableCell align="right">Self time</TableCell><TableCell align="right">Share</TableCell></TableRow></TableHead>
                  <TableBody>
                    {t.services.map((s) => (
                      <TableRow key={s.service_name}>
                        <TableCell>{s.service_name}</TableCell><TableCell align="right">{s.span_count}</TableCell>
                        <TableCell align="right" sx={{ color: s.error_count ? 'error.main' : undefined }}>{s.error_count}</TableCell>
                        <TableCell align="right">{formatMs(s.self_time_ms)}</TableCell><TableCell align="right">{formatPct(s.latency_share, 0)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Section>
            </Box>
          )}

          {tab === 3 && (
            <Section title="Spans referenced as parents but never received">
              <Typography color="text.secondary" sx={{ mb: 1.5 }}>The trace is shown as complete as the data allows; conclusions drawn from it are marked with lower confidence.</Typography>
              {t.missing_spans.map((m) => (
                <Typography key={m.span_id}><Mono>{m.span_id}</Mono> is the parent of {m.referenced_by.length} span(s): {m.referenced_by.map((id) => <Mono key={id}> {id.slice(0, 8)}</Mono>)}</Typography>
              ))}
            </Section>
          )}
          {t.diagnostic && <Box sx={{ mt: 2 }}><SeverityChip severity={t.diagnostic.severity} /> <MuiLink component={Link} to={`/root-cause/${t.diagnostic.id}`}>View evidence</MuiLink></Box>}
        </>
      )}
    </>
  );
}
