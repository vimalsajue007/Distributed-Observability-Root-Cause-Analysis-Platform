import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import {
  Alert, Box, Button, Chip, FormControl, InputLabel, Link as MuiLink, MenuItem, Pagination, Paper, Select, Stack, Step, StepLabel, Stepper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts';
import { toApiError } from '../api/client';
import { api } from '../api/endpoints';
import type { Evidence } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { ClassificationChip, ConfidenceChip, EmptyState, ErrorState, JsonView, LoadingBlock, Mono, PageHeader, Section, SeverityChip, WarningsBanner } from '../components/common';
import { useApi } from '../hooks/useApi';
import { chartColors } from '../theme/theme';
import { formatMs, formatPct, formatTime, humanize, relativeTime, shortId } from '../utils/format';

const PAGE_SIZE = 15;

export function RootCauseList() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const [traceInput, setTraceInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const classification = params.get('classification') ?? '';
  const severity = params.get('severity') ?? '';
  const service = params.get('affected_service') ?? '';
  const page = Number(params.get('page') ?? '1') || 1;

  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    if (!('page' in changes)) next.delete('page');
    setParams(next, { replace: true });
  };
  const list = useApi((s) => api.diagnostics({ classification, severity, affected_service: service, page, page_size: PAGE_SIZE }, s), [classification, severity, service, page]);
  const total = list.meta?.total ?? 0;
  const validTrace = /^[0-9a-fA-F]{32}$/.test(traceInput.trim());

  const analyze = async () => {
    setBusy(true); setActionError(null);
    try { const r = await api.analyze(traceInput.trim().toLowerCase()); navigate(`/root-cause/${r.data.id}`); } catch (e) { setActionError(toApiError(e).message); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader title="Root-Cause Analysis" subtitle="Deterministic, rule-based diagnoses. A confirmed failure needs an error recorded inside the service itself; when only a caller observed the problem, the service is reported as a potential cause. Every conclusion lists its evidence." />
      {can('operator') && (
        <Paper sx={{ p: 2, mb: 2.5 }}>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} alignItems={{ md: 'flex-start' }}>
            <TextField size="small" label="Analyse a trace (32-character id)" value={traceInput} onChange={(e) => setTraceInput(e.target.value)} sx={{ minWidth: { md: 380 } }} inputProps={{ maxLength: 32 }} error={Boolean(traceInput) && !validTrace} helperText={traceInput && !validTrace ? 'Expected 32 hexadecimal characters' : 'Failed and slow traces are analysed automatically.'} />
            <Button variant="contained" startIcon={<PlayArrowIcon />} disabled={!validTrace || busy} onClick={analyze}>{busy ? 'Analysing…' : 'Analyse'}</Button>
          </Stack>
          {actionError && <Alert severity="error" sx={{ mt: 1.5 }}>{actionError}</Alert>}
        </Paper>
      )}
      <Paper sx={{ p: 2, mb: 2.5 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5}>
          <FormControl size="small" sx={{ minWidth: 210 }}><InputLabel id="c">Classification</InputLabel>
            <Select labelId="c" label="Classification" value={classification} onChange={(e) => set({ classification: e.target.value })}>
              <MenuItem value="">All</MenuItem>
              {['confirmed_failure', 'potential_cause', 'latency_bottleneck', 'insufficient_evidence', 'healthy'].map((c) => <MenuItem key={c} value={c}>{humanize(c)}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 150 }}><InputLabel id="sv">Severity</InputLabel>
            <Select labelId="sv" label="Severity" value={severity} onChange={(e) => set({ severity: e.target.value })}>
              <MenuItem value="">All</MenuItem>{['critical', 'high', 'medium', 'low', 'info'].map((c) => <MenuItem key={c} value={c}>{humanize(c)}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField size="small" label="Affected service" value={service} onChange={(e) => set({ affected_service: e.target.value.trim().toLowerCase() })} sx={{ minWidth: 200 }} inputProps={{ maxLength: 100 }} />
        </Stack>
      </Paper>
      {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
      <WarningsBanner warnings={list.warnings} />
      <Paper>
        {list.loading && !list.data ? <Box sx={{ p: 2 }}><LoadingBlock rows={6} /></Box> : list.data && list.data.length === 0 ? (
          <EmptyState title="No diagnostic reports match" hint="Run a failing scenario in the Scenario Lab; once its trace completes the report appears here automatically." action={<MuiLink component={Link} to="/scenarios">Open Scenario Lab</MuiLink>} />
        ) : list.data && (
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small" aria-label="Diagnostic reports">
              <TableHead><TableRow><TableCell>When</TableCell><TableCell>Classification</TableCell><TableCell>Affected service</TableCell><TableCell>Severity</TableCell><TableCell>Confidence</TableCell><TableCell>Summary</TableCell><TableCell>Trace</TableCell></TableRow></TableHead>
              <TableBody>
                {list.data.map((d) => (
                  <TableRow key={d.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/root-cause/${d.id}`)}>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>{relativeTime(d.created_at)}</TableCell>
                    <TableCell><ClassificationChip value={d.classification} /></TableCell>
                    <TableCell>{d.affected_service ?? '—'}</TableCell>
                    <TableCell><SeverityChip severity={d.severity} /></TableCell>
                    <TableCell><ConfidenceChip value={d.confidence_level} /></TableCell>
                    <TableCell sx={{ maxWidth: 420 }}><Typography variant="body2" noWrap title={d.summary}>{d.summary}</Typography></TableCell>
                    <TableCell onClick={(e) => e.stopPropagation()}><MuiLink component={Link} to={`/traces/${d.trace_id}`}><Mono>{shortId(d.trace_id)}</Mono></MuiLink></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
        {total > PAGE_SIZE && <Stack alignItems="flex-end" sx={{ p: 1.5 }}><Pagination count={Math.ceil(total / PAGE_SIZE)} page={page} onChange={(_, p) => set({ page: String(p) })} color="primary" shape="rounded" /></Stack>}
      </Paper>
    </>
  );
}

const EVIDENCE_ORDER = ['trace', 'span', 'log', 'metric', 'alert', 'related_trace', 'gap', 'rule'];

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  const groups = EVIDENCE_ORDER.map((type) => ({ type, items: evidence.filter((e) => e.evidence_type === type) })).filter((g) => g.items.length > 0);
  return (
    <Stack spacing={2}>
      {groups.map((g) => (
        <Box key={g.type}>
          <Typography variant="overline" color="text.secondary">{humanize(g.type)} evidence ({g.items.length})</Typography>
          <Stack spacing={1}>
            {g.items.map((e) => (
              <Box key={e.id} sx={{ p: 1.25, borderRadius: 2, border: 1, borderColor: 'divider' }}>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 0.25 }}>
                  {e.service_name && <Chip size="small" label={e.service_name} />}
                  {e.observed_at && <Typography variant="caption" color="text.secondary">{formatTime(e.observed_at)}</Typography>}
                  {e.reference_id && <Mono>{e.reference_id.length > 20 ? e.reference_id.slice(0, 20) : e.reference_id}</Mono>}
                </Stack>
                <Typography variant="body2">{e.description}</Typography>
              </Box>
            ))}
          </Stack>
        </Box>
      ))}
    </Stack>
  );
}

export function RootCauseDetail() {
  const { id = '' } = useParams();
  const report = useApi((s) => api.diagnostic(id, s), [id]);
  const d = report.data;
  return (
    <>
      <PageHeader title="Root-cause report" subtitle={d ? `Trace ${d.trace_id}` : undefined} actions={<Button startIcon={<ArrowBackIcon />} component={Link} to="/root-cause">All reports</Button>} />
      {report.error && <ErrorState error={report.error} onRetry={report.reload} />}
      <WarningsBanner warnings={[...report.warnings, ...(d?.warnings ?? [])].filter((w, i, a) => a.findIndex((x) => x.code === w.code) === i)} />
      {report.loading && !d ? <LoadingBlock rows={6} height={60} /> : d && (
        <>
          <Alert severity={d.classification === 'healthy' ? 'success' : d.classification === 'confirmed_failure' ? 'error' : d.classification === 'insufficient_evidence' ? 'info' : 'warning'} sx={{ mb: 2.5, alignItems: 'flex-start' }}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
              <ClassificationChip value={d.classification} />
              <SeverityChip severity={d.severity} />
              <ConfidenceChip value={d.confidence_level} />
              {d.affected_service && <Chip size="small" variant="outlined" label={`Affected: ${d.affected_service}`} />}
              <Chip size="small" variant="outlined" label={d.trigger === 'event' ? 'automatic' : 'manual'} />
            </Stack>
            <Typography>{d.diagnosis}</Typography>
            <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>
              Analysed {formatTime(d.updated_at)} · {d.analysis_count} run{d.analysis_count === 1 ? '' : 's'} · rules v{d.rules_version} ·{' '}
              <MuiLink component={Link} to={`/traces/${d.trace_id}`}>Open trace</MuiLink>
            </Typography>
          </Alert>

          {d.request_path && d.request_path.length > 0 && (
            <Section title="Request path to the failing span" sx={{ mb: 2.5 }}>
              <Box sx={{ overflowX: 'auto', pb: 1 }}>
                <Stepper alternativeLabel sx={{ minWidth: Math.max(480, d.request_path.length * 190) }}>
                  {d.request_path.map((s) => (
                    <Step key={s.span_id} active completed={false}>
                      <StepLabel error={s.status === 'ERROR'} optional={<Typography variant="caption">{formatMs(s.duration_ms)}</Typography>}>
                        <Typography variant="body2" fontWeight={650}>{s.service_name}</Typography>
                        <Typography variant="caption" color="text.secondary">{s.operation_name}</Typography>
                      </StepLabel>
                    </Step>
                  ))}
                </Stepper>
              </Box>
            </Section>
          )}

          <Box sx={{ display: 'grid', gap: 2.5, gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' }, mb: 2.5 }}>
            <Section title="Ranked suspects">
              {!d.ranking || d.ranking.length === 0 ? <EmptyState title="No suspects" hint="No rule produced a score for any service." /> : (
                <Stack spacing={1.25}>
                  {d.ranking.map((r, i) => (
                    <Box key={r.service_name} sx={{ p: 1.5, borderRadius: 2, border: 1, borderColor: i === 0 ? 'primary.main' : 'divider' }}>
                      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                        <Typography fontWeight={700}>#{i + 1} {r.service_name}</Typography>
                        <Chip size="small" color={r.classification === 'confirmed' ? 'error' : 'warning'} label={r.classification} />
                        <Chip size="small" variant="outlined" label={`score ${r.score}`} />
                        {r.evidence_types.map((t) => <Chip key={t} size="small" variant="outlined" label={t} />)}
                      </Stack>
                      <Box component="ul" sx={{ m: 0, mt: 0.75, pl: 2.25 }}>
                        {r.reasons.map((x, j) => <li key={j}><Typography variant="caption" color="text.secondary"><b>+{x.points}</b> {x.detail}</Typography></li>)}
                      </Box>
                    </Box>
                  ))}
                </Stack>
              )}
            </Section>

            <Section title="Latency contribution">
              {!d.latency_contributions || d.latency_contributions.length === 0 ? <EmptyState title="No timing data" /> : (
                <Box sx={{ height: 260 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={d.latency_contributions.map((c) => ({ ...c, share: Number((c.latency_share * 100).toFixed(1)) }))} layout="vertical" margin={{ left: 24 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                      <XAxis type="number" unit="%" domain={[0, 100]} />
                      <YAxis type="category" dataKey="service_name" width={150} />
                      <RTooltip formatter={(v: number, _n, p) => [`${v}% (${formatMs((p.payload as { self_time_ms: number }).self_time_ms)})`, 'share of self time']} />
                      <Bar dataKey="share" radius={[0, 6, 6, 0]}>{d.latency_contributions.map((_, i) => <Cell key={i} fill={chartColors[i % chartColors.length]} />)}</Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </Box>
              )}
              {d.latency_contributions && <Typography variant="caption" color="text.secondary">Total self time shares: {d.latency_contributions.map((c) => `${c.service_name} ${formatPct(c.latency_share, 0)}`).join(' · ')}</Typography>}
            </Section>
          </Box>

          <Box sx={{ display: 'grid', gap: 2.5, gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' } }}>
            <Section title="Failure timeline">
              {!d.failure_timeline || d.failure_timeline.length === 0 ? <EmptyState title="No events" /> : (
                <Stack spacing={0}>
                  {d.failure_timeline.map((e, i) => (
                    <Stack key={i} direction="row" spacing={1.5} sx={{ py: 0.9, borderBottom: i === d.failure_timeline!.length - 1 ? 'none' : 1, borderColor: 'divider' }}>
                      <Typography variant="caption" color="text.secondary" sx={{ width: 78, flexShrink: 0, pt: 0.2 }}>{formatTime(e.timestamp, false)}</Typography>
                      <Chip size="small" label={humanize(e.type)} color={e.type.includes('error') ? 'error' : e.type === 'slow_span' ? 'warning' : e.type === 'missing_span' ? 'warning' : 'default'} variant="outlined" sx={{ flexShrink: 0 }} />
                      <Typography variant="body2" sx={{ minWidth: 0, wordBreak: 'break-word' }}>{e.service_name && <b>{e.service_name}: </b>}{e.message}</Typography>
                    </Stack>
                  ))}
                </Stack>
              )}
            </Section>
            <Section title="Evidence">
              {!d.evidence || d.evidence.length === 0 ? <EmptyState title="No evidence recorded" /> : <EvidenceList evidence={d.evidence} />}
            </Section>
          </Box>
          {d.origin_span && <Box sx={{ mt: 2.5 }}><Section title="Origin span (raw)"><JsonView value={d.origin_span} /></Section></Box>}
        </>
      )}
    </>
  );
}
