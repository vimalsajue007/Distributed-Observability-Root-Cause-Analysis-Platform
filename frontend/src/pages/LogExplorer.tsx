import SearchIcon from '@mui/icons-material/Search';
import { Box, Button, Chip, Collapse, FormControl, IconButton, InputLabel, Link as MuiLink, MenuItem, Pagination, Paper, Select, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, TextField, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import { Fragment, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/endpoints';
import { EmptyState, ErrorState, JsonView, LoadingBlock, Mono, PageHeader, SeverityChip, WarningsBanner } from '../components/common';
import { useRefresh } from '../components/RefreshContext';
import { useApi } from '../hooks/useApi';
import { formatNumber, formatTime, relativeTime } from '../utils/format';

const SEVERITIES = ['INFO', 'WARNING', 'ERROR', 'CRITICAL'];
const PAGE_SIZE = 30;

export function LogExplorer() {
  const { windowMinutes } = useRefresh();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [q, setQ] = useState(params.get('q') ?? '');
  const [traceDraft, setTraceDraft] = useState(params.get('trace_id') ?? '');

  const severity = params.getAll('severity');
  const service = params.get('service_name') ?? '';
  const order = params.get('order') ?? 'desc';
  const page = Number(params.get('page') ?? '1') || 1;
  const traceId = params.get('trace_id') ?? '';
  const query = params.get('q') ?? '';

  const set = (changes: Record<string, string | string[]>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      next.delete(k);
      (Array.isArray(v) ? v : v ? [v] : []).forEach((x) => next.append(k, x));
    }
    if (!('page' in changes)) next.delete('page');
    setParams(next, { replace: true });
  };

  const start = useMemo(() => new Date(Date.now() - windowMinutes * 60_000).toISOString(), [windowMinutes, params]); // eslint-disable-line react-hooks/exhaustive-deps
  const services = useApi((s) => api.metricsServices({ window_minutes: 10_080 }, s), [], { poll: false });
  const logs = useApi(
    (s) => api.logs({ severity, service_name: service, trace_id: traceId, q: query, order, page, page_size: PAGE_SIZE, start: traceId ? undefined : start }, s),
    [severity.join(','), service, traceId, query, order, page, windowMinutes], { enabled: tab === 0 },
  );
  const patterns = useApi((s) => api.logPatterns({ service_name: service, page_size: 50 }, s), [service], { enabled: tab === 1 });
  const active = tab === 0 ? logs : patterns;
  const total = logs.meta?.total ?? 0;

  return (
    <>
      <PageHeader title="Log Explorer" subtitle="Structured logs from every service, correlated by trace id. Secrets are redacted before storage. Requires the operator role." />
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}><Tab label="Logs" /><Tab label="Repeated error patterns" /></Tabs>

      <Paper sx={{ p: 2, mb: 2.5 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} alignItems={{ md: 'center' }} flexWrap="wrap" useFlexGap component="form" onSubmit={(e: React.FormEvent) => { e.preventDefault(); set({ q: q.trim(), trace_id: traceDraft.trim().toLowerCase() }); }}>
          {tab === 0 && (
            <ToggleButtonGroup size="small" value={severity} onChange={(_, v: string[]) => set({ severity: v })} aria-label="Severity">
              {SEVERITIES.map((s) => <ToggleButton key={s} value={s} sx={{ px: 1.5 }}>{s}</ToggleButton>)}
            </ToggleButtonGroup>
          )}
          <FormControl size="small" sx={{ minWidth: 170 }}><InputLabel id="ls">Service</InputLabel>
            <Select labelId="ls" label="Service" value={service} onChange={(e) => set({ service_name: e.target.value })}>
              <MenuItem value="">All services</MenuItem>
              {(services.data ?? []).map((s) => <MenuItem key={s.service_name} value={s.service_name}>{s.service_name}</MenuItem>)}
            </Select>
          </FormControl>
          {tab === 0 && (
            <>
              <TextField size="small" label="Search message" value={q} onChange={(e) => setQ(e.target.value)} inputProps={{ maxLength: 200 }} sx={{ minWidth: 220 }} />
              <TextField size="small" label="Trace id" value={traceDraft} onChange={(e) => setTraceDraft(e.target.value)} inputProps={{ maxLength: 32 }} sx={{ minWidth: 250 }} />
              <FormControl size="small" sx={{ minWidth: 130 }}><InputLabel id="lo">Order</InputLabel>
                <Select labelId="lo" label="Order" value={order} onChange={(e) => set({ order: e.target.value })}><MenuItem value="desc">Newest first</MenuItem><MenuItem value="asc">Oldest first</MenuItem></Select>
              </FormControl>
              <Button type="submit" variant="contained" startIcon={<SearchIcon />}>Search</Button>
              <Button onClick={() => { setQ(''); setTraceDraft(''); setParams({}, { replace: true }); }}>Clear</Button>
            </>
          )}
        </Stack>
      </Paper>

      {active.error && <ErrorState error={active.error} onRetry={active.reload} />}
      <WarningsBanner warnings={active.warnings} />
      {!active.error && (
        <Paper>
          {active.loading && !active.data ? <Box sx={{ p: 2 }}><LoadingBlock rows={10} height={30} /></Box> : tab === 0 ? (
            logs.data && logs.data.length === 0 ? <EmptyState title="No logs match these filters" hint="Widen the time window or remove a filter." /> : logs.data && (
              <>
                <Box sx={{ overflowX: 'auto' }}>
                  <Table size="small" aria-label="Logs">
                    <TableHead><TableRow><TableCell width={40} /><TableCell>Time</TableCell><TableCell>Severity</TableCell><TableCell>Service</TableCell><TableCell>Message</TableCell><TableCell>Trace</TableCell></TableRow></TableHead>
                    <TableBody>
                      {logs.data.map((l) => (
                        <Fragment key={l.id}>
                          <TableRow hover onClick={() => setExpanded(expanded === l.id ? null : l.id)} sx={{ cursor: 'pointer', '& > td': { borderBottom: expanded === l.id ? 'none' : undefined } }}>
                            <TableCell><IconButton size="small" aria-label={expanded === l.id ? 'Collapse' : 'Expand'}>{expanded === l.id ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}</IconButton></TableCell>
                            <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatTime(l.timestamp)}</TableCell>
                            <TableCell><SeverityChip severity={l.severity} /></TableCell>
                            <TableCell>{l.service_name}</TableCell>
                            <TableCell sx={{ maxWidth: 640, wordBreak: 'break-word' }}>{l.message}</TableCell>
                            <TableCell onClick={(e) => e.stopPropagation()}>{l.trace_id ? <MuiLink component={Link} to={`/traces/${l.trace_id}`}><Mono>{l.trace_id.slice(0, 8)}</Mono></MuiLink> : '—'}</TableCell>
                          </TableRow>
                          <TableRow>
                            <TableCell colSpan={6} sx={{ py: 0, borderBottom: expanded === l.id ? undefined : 'none' }}>
                              <Collapse in={expanded === l.id} unmountOnExit>
                                <Box sx={{ py: 1.5 }}>
                                  <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
                                    {l.span_id && <Chip size="small" variant="outlined" label={`span ${l.span_id.slice(0, 8)}`} />}
                                    {l.correlation_id && <Chip size="small" variant="outlined" label={`correlation ${l.correlation_id.slice(0, 12)}`} />}
                                    {l.logger && <Chip size="small" variant="outlined" label={l.logger} />}
                                  </Stack>
                                  <JsonView value={l.attributes} maxHeight={180} />
                                </Box>
                              </Collapse>
                            </TableCell>
                          </TableRow>
                        </Fragment>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
                {total > PAGE_SIZE && (
                  <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ p: 1.5 }}>
                    <Typography variant="body2" color="text.secondary">{formatNumber(total)} log records</Typography>
                    <Pagination count={Math.ceil(total / PAGE_SIZE)} page={page} onChange={(_, p) => set({ page: String(p) })} color="primary" shape="rounded" />
                  </Stack>
                )}
              </>
            )
          ) : patterns.data && patterns.data.length === 0 ? <EmptyState title="No repeated error patterns yet" hint="Patterns appear when the same normalised error message occurs repeatedly." /> : patterns.data && (
            <Box sx={{ overflowX: 'auto' }}>
              <Table size="small" aria-label="Error patterns">
                <TableHead><TableRow><TableCell align="right">Count</TableCell><TableCell>Service</TableCell><TableCell>Pattern</TableCell><TableCell>First seen</TableCell><TableCell>Last seen</TableCell><TableCell>Latest trace</TableCell></TableRow></TableHead>
                <TableBody>
                  {patterns.data.map((p) => (
                    <TableRow key={p.id} hover>
                      <TableCell align="right"><Chip size="small" color={p.occurrence_count > 5 ? 'error' : 'warning'} label={formatNumber(p.occurrence_count)} /></TableCell>
                      <TableCell>{p.service_name}</TableCell>
                      <TableCell sx={{ maxWidth: 520, wordBreak: 'break-word' }}>{p.pattern}</TableCell>
                      <TableCell>{formatTime(p.first_seen)}</TableCell>
                      <TableCell>{relativeTime(p.last_seen)}</TableCell>
                      <TableCell>{p.last_trace_id ? <MuiLink component={Link} to={`/traces/${p.last_trace_id}`}><Mono>{p.last_trace_id.slice(0, 8)}</Mono></MuiLink> : '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </Paper>
      )}
    </>
  );
}
