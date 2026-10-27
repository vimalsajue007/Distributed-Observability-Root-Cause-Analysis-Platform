import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { Alert, Box, Button, Card, CardActionArea, CardContent, Chip, Link as MuiLink, Paper, Slider, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { toApiError } from '../api/client';
import { api } from '../api/endpoints';
import { useAuth } from '../auth/AuthContext';
import { EmptyState, ErrorState, LoadingBlock, Mono, PageHeader, Section } from '../components/common';
import { useApi } from '../hooks/useApi';
import { formatMs, formatTime, humanize } from '../utils/format';

interface RunResult {
  n: number;
  scenario: string;
  at: string;
  ok: boolean;
  httpStatus: number;
  ms: number;
  orderStatus?: string;
  orderId?: number;
  notification?: string;
  failure?: string;
  failingService?: string | null;
  source?: string;
  correlationId: string | null;
}

export function ScenarioLab() {
  const { can } = useAuth();
  const scenarios = useApi((s) => api.scenarios(s), [], { poll: false, enabled: can('operator') });
  const [selected, setSelected] = useState('normal');
  const [count, setCount] = useState(1);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<RunResult[]>([]);
  const allowed = can('operator');

  const run = async () => {
    setRunning(true);
    const out: RunResult[] = [];
    for (let i = 1; i <= count; i++) {
      const t0 = performance.now();
      try {
        const r = await api.createSampleOrder(selected);
        out.push({ n: i, scenario: selected, at: new Date().toISOString(), ok: true, httpStatus: 201, ms: performance.now() - t0, orderStatus: r.data.status, orderId: r.data.order_id, notification: r.data.notification_status,
          failure: r.warnings[0]?.message, correlationId: (r.headers['x-correlation-id'] as string | undefined) ?? r.data.correlation_id });
      } catch (e) {
        const err = toApiError(e);
        out.push({ n: i, scenario: selected, at: new Date().toISOString(), ok: false, httpStatus: err.status, ms: performance.now() - t0, failure: `${err.code}: ${err.message}`, failingService: err.service, source: err.source, correlationId: err.correlationId });
      }
      setResults([...out].reverse().concat(results.filter((r) => !out.includes(r))));
    }
    setResults((prev) => [...out].reverse().concat(prev.filter((p) => !out.some((o) => o.at === p.at && o.n === p.n))));
    setRunning(false);
  };

  const sel = scenarios.data?.find((s) => s.name === selected);

  return (
    <>
      <PageHeader title="Scenario Lab" subtitle="Drive the sample Order → Payment → Notification application through its failure scenarios and watch the platform detect, trace and explain each one." />
      {!allowed && <Alert severity="info" sx={{ mb: 2 }}>Running scenarios requires the operator role. You can still read the catalogue below.</Alert>}
      {scenarios.error && <ErrorState error={scenarios.error} onRetry={scenarios.reload} />}
      {scenarios.loading && !scenarios.data && allowed ? <LoadingBlock rows={2} height={110} /> : (
        <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', xl: 'repeat(3, 1fr)' }, mb: 2.5 }}>
          {(scenarios.data ?? []).map((s) => (
            <Card key={s.name} variant="outlined" sx={{ borderColor: selected === s.name ? 'primary.main' : 'divider', borderWidth: selected === s.name ? 2 : 1 }}>
              <CardActionArea onClick={() => setSelected(s.name)} aria-pressed={selected === s.name} sx={{ height: '100%' }}>
                <CardContent>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.75 }}><Typography fontWeight={700}>{humanize(s.name)}</Typography>{selected === s.name && <Chip size="small" color="primary" label="selected" />}</Stack>
                  <Typography variant="body2" color="text.secondary">{s.description}</Typography>
                  <Typography variant="caption" color="primary.light" component="div" sx={{ mt: 1 }}>Expected diagnosis: {s.expected_diagnosis}</Typography>
                </CardContent>
              </CardActionArea>
            </Card>
          ))}
        </Box>
      )}

      <Paper sx={{ p: 2.5, mb: 2.5 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={3} alignItems={{ md: 'center' }}>
          <Box sx={{ minWidth: 220 }}>
            <Typography variant="body2" color="text.secondary">Scenario</Typography>
            <Typography fontWeight={700}>{humanize(selected)}</Typography>
            {sel && <Typography variant="caption" color="text.secondary">{sel.description}</Typography>}
          </Box>
          <Box sx={{ width: { xs: '100%', md: 280 }, px: 1 }}>
            <Typography variant="body2" color="text.secondary" id="count-label">Orders to submit: {count}</Typography>
            <Slider aria-labelledby="count-label" value={count} min={1} max={10} step={1} marks onChange={(_, v) => setCount(v as number)} />
          </Box>
          <Button variant="contained" size="large" startIcon={<PlayArrowIcon />} onClick={run} disabled={!allowed || running}>{running ? 'Running…' : 'Run scenario'}</Button>
        </Stack>
        <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1.5 }}>
          Failed and slow traces are finalised after a short quiet period (default 10 s) and then analysed automatically. Open the Root-Cause screen a few seconds after running.
        </Typography>
      </Paper>

      <Section title="Results">
        {results.length === 0 ? <EmptyState title="Nothing run yet" hint="Pick a scenario and press “Run scenario”." /> : (
          <Box sx={{ overflowX: 'auto' }}>
            <Table size="small" aria-label="Scenario results">
              <TableHead><TableRow><TableCell>Time</TableCell><TableCell>Scenario</TableCell><TableCell>Outcome</TableCell><TableCell align="right">Latency</TableCell><TableCell>Order</TableCell><TableCell>Detail</TableCell><TableCell>Trace</TableCell></TableRow></TableHead>
              <TableBody>
                {results.map((r, i) => (
                  <TableRow key={`${r.at}-${r.n}-${i}`} hover>
                    <TableCell>{formatTime(r.at, false)}</TableCell>
                    <TableCell>{humanize(r.scenario)}</TableCell>
                    <TableCell><Chip size="small" color={r.ok ? (r.failure ? 'warning' : 'success') : 'error'} label={r.ok ? (r.failure ? 'completed with warning' : 'completed') : `failed · HTTP ${r.httpStatus}`} /></TableCell>
                    <TableCell align="right">{formatMs(r.ms)}</TableCell>
                    <TableCell>{r.orderId ? `#${r.orderId}` : '—'}</TableCell>
                    <TableCell sx={{ maxWidth: 420, wordBreak: 'break-word' }}>
                      {r.failure ?? (r.notification ? `notification ${r.notification}` : '—')}
                      {r.failingService && <Chip size="small" variant="outlined" sx={{ ml: 1 }} label={`${r.source}: ${r.failingService}`} />}
                    </TableCell>
                    <TableCell>{r.correlationId ? <MuiLink component={Link} to={`/traces?correlation_id=${r.correlationId}`}><Mono>{r.correlationId.slice(0, 8)}</Mono></MuiLink> : '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </Section>
    </>
  );
}
