import SearchIcon from '@mui/icons-material/Search';
import { Box, Button, FormControl, InputLabel, Link as MuiLink, MenuItem, Pagination, Paper, Select, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/endpoints';
import { EmptyState, ErrorState, LoadingBlock, Mono, PageHeader, StatusChip, WarningsBanner } from '../components/common';
import { useRefresh } from '../components/RefreshContext';
import { useApi } from '../hooks/useApi';
import { formatMs, formatTime, shortId } from '../utils/format';

const PAGE_SIZE = 20;

export function TraceExplorer() {
  const { windowMinutes } = useRefresh();
  const [params, setParams] = useSearchParams();
  const [draftTrace, setDraftTrace] = useState(params.get('trace_id') ?? '');
  const [draftMin, setDraftMin] = useState(params.get('min_duration_ms') ?? '');

  const status = params.get('status') ?? '';
  const service = params.get('service_name') ?? '';
  const completion = params.get('completion_state') ?? '';
  const correlation = params.get('correlation_id') ?? '';
  const page = Number(params.get('page') ?? '1') || 1;

  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    if (!('page' in changes)) next.delete('page');
    setParams(next, { replace: true });
  };

  const start = useMemo(() => new Date(Date.now() - windowMinutes * 60_000).toISOString(), [windowMinutes, params]); // eslint-disable-line react-hooks/exhaustive-deps
  const services = useApi((s) => api.metricsServices({ window_minutes: 10_080 }, s), [], { poll: false });
  const traces = useApi(
    (s) => api.traces({ status, service_name: service, completion_state: completion, correlation_id: correlation, trace_id: params.get('trace_id') ?? '', min_duration_ms: params.get('min_duration_ms') ?? '', start: correlation ? undefined : start, page, page_size: PAGE_SIZE }, s),
    [status, service, completion, correlation, params.get('trace_id'), params.get('min_duration_ms'), windowMinutes, page],
  );
  const total = traces.meta?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const submit = (e: React.FormEvent) => { e.preventDefault(); set({ trace_id: draftTrace.trim().toLowerCase(), min_duration_ms: draftMin.trim() }); };

  return (
    <>
      <PageHeader title="Trace Explorer" subtitle="Find a request by status, service, duration or trace id, then open it to see the span waterfall, correlated logs and the root-cause diagnosis." />
      <Paper component="form" onSubmit={submit} sx={{ p: 2, mb: 2.5 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} alignItems={{ md: 'center' }} flexWrap="wrap" useFlexGap>
          <TextField size="small" label="Trace id (or prefix)" value={draftTrace} onChange={(e) => setDraftTrace(e.target.value)} sx={{ minWidth: 250 }} inputProps={{ maxLength: 32, pattern: '[0-9a-fA-F]{8,32}|' }} helperText={draftTrace && !/^[0-9a-fA-F]{8,32}$/.test(draftTrace) ? 'Hex, 8–32 characters' : ' '} error={Boolean(draftTrace) && !/^[0-9a-fA-F]{8,32}$/.test(draftTrace)} />
          <FormControl size="small" sx={{ minWidth: 150 }}><InputLabel id="svc">Service</InputLabel>
            <Select labelId="svc" label="Service" value={service} onChange={(e) => set({ service_name: e.target.value })}>
              <MenuItem value="">All services</MenuItem>
              {(services.data ?? []).map((s) => <MenuItem key={s.service_name} value={s.service_name}>{s.service_name}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 130 }}><InputLabel id="st">Status</InputLabel>
            <Select labelId="st" label="Status" value={status} onChange={(e) => set({ status: e.target.value })}>
              <MenuItem value="">Any</MenuItem><MenuItem value="OK">OK</MenuItem><MenuItem value="ERROR">ERROR</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 150 }}><InputLabel id="cs">Completion</InputLabel>
            <Select labelId="cs" label="Completion" value={completion} onChange={(e) => set({ completion_state: e.target.value })}>
              <MenuItem value="">Any</MenuItem><MenuItem value="complete">Complete</MenuItem><MenuItem value="in_progress">In progress</MenuItem><MenuItem value="incomplete">Incomplete</MenuItem>
            </Select>
          </FormControl>
          <TextField size="small" label="Min duration (ms)" type="number" value={draftMin} onChange={(e) => setDraftMin(e.target.value)} sx={{ width: 160 }} inputProps={{ min: 0 }} />
          <Button type="submit" variant="contained" startIcon={<SearchIcon />} sx={{ mb: { md: 2.6 } }}>Search</Button>
          {(status || service || completion || correlation || params.get('trace_id') || params.get('min_duration_ms')) && (
            <Button sx={{ mb: { md: 2.6 } }} onClick={() => { setDraftTrace(''); setDraftMin(''); setParams({}, { replace: true }); }}>Clear</Button>
          )}
        </Stack>
        {correlation && <Typography variant="caption" color="text.secondary">Filtered by correlation id <Mono>{correlation}</Mono> (time window ignored)</Typography>}
      </Paper>

      {traces.error && <ErrorState error={traces.error} onRetry={traces.reload} />}
      <WarningsBanner warnings={traces.warnings} />
      <Paper>
        {traces.loading && !traces.data ? <Box sx={{ p: 2 }}><LoadingBlock rows={8} /></Box> : traces.data && traces.data.length === 0 ? (
          <EmptyState title="No traces match these filters" hint="Try a wider time window, or generate traffic from the Scenario Lab." action={<MuiLink component={Link} to="/scenarios">Open Scenario Lab</MuiLink>} />
        ) : traces.data && (
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small" aria-label="Traces">
              <TableHead>
                <TableRow><TableCell>Trace id</TableCell><TableCell>Root service</TableCell><TableCell>Started</TableCell><TableCell align="right">Duration</TableCell><TableCell align="right">Spans</TableCell><TableCell align="right">Errors</TableCell><TableCell>Status</TableCell><TableCell>State</TableCell></TableRow>
              </TableHead>
              <TableBody>
                {traces.data.map((t) => (
                  <TableRow key={t.trace_id} hover>
                    <TableCell><MuiLink component={Link} to={`/traces/${t.trace_id}`} underline="hover"><Mono title={t.trace_id}>{shortId(t.trace_id, 12)}</Mono></MuiLink></TableCell>
                    <TableCell>{t.root_service ?? '—'}</TableCell>
                    <TableCell>{formatTime(t.start_time)}</TableCell>
                    <TableCell align="right">{formatMs(t.duration_ms)}</TableCell>
                    <TableCell align="right">{t.span_count}{t.missing_parent_count > 0 && <Typography component="span" color="warning.main" title="Some parent spans are missing"> ⚠</Typography>}</TableCell>
                    <TableCell align="right" sx={{ color: t.error_span_count > 0 ? 'error.main' : undefined }}>{t.error_span_count}</TableCell>
                    <TableCell><StatusChip status={t.status} /></TableCell>
                    <TableCell><StatusChip status={t.completion_state} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
        {total > PAGE_SIZE && (
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ p: 1.5 }}>
            <Typography variant="body2" color="text.secondary">{total} traces</Typography>
            <Pagination count={pages} page={Math.min(page, pages)} onChange={(_, p) => set({ page: String(p) })} color="primary" shape="rounded" />
          </Stack>
        )}
      </Paper>
    </>
  );
}
