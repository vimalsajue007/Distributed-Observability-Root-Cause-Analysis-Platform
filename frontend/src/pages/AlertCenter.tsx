import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import DoneAllIcon from '@mui/icons-material/DoneAll';
import ReplayIcon from '@mui/icons-material/Replay';
import SettingsIcon from '@mui/icons-material/Settings';
import {
  Alert as MuiAlert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, FormControlLabel, IconButton, InputLabel, Link as MuiLink, MenuItem, Pagination, Paper, Select, Stack, Switch, Tab, Table, TableBody, TableCell, TableHead,
  TableRow, Tabs, TextField, Tooltip, Typography,
} from '@mui/material';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { toApiError } from '../api/client';
import { api } from '../api/endpoints';
import type { Alert, AlertRule, AlertStatus } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { EmptyState, ErrorState, JsonView, LoadingBlock, PageHeader, SeverityChip, StatusChip, WarningsBanner } from '../components/common';
import { useApi } from '../hooks/useApi';
import { formatPct, formatTime, humanize, relativeTime } from '../utils/format';

const PAGE_SIZE = 15;

function valueText(a: Pick<Alert, 'alert_type' | 'measured_value' | 'threshold_value'>, field: 'measured_value' | 'threshold_value'): string {
  const v = a[field];
  if (a.alert_type === 'error_rate') return formatPct(v);
  if (a.alert_type === 'request_volume') return `${v} req`;
  return `${Math.round(v)} ms`;
}

export function AlertCenter() {
  const { can } = useAuth();
  const [tab, setTab] = useState(0);
  const [service, setService] = useState('');
  const [severity, setSeverity] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Alert | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Partial<AlertRule> | null>(null);

  const status = tab === 0 ? 'active' : tab === 1 ? 'resolved' : '';
  const alerts = useApi((s) => api.alerts({ status, service_name: service, severity, page, page_size: PAGE_SIZE }, s), [status, service, severity, page], { enabled: tab < 2 });
  const rules = useApi((s) => api.alertRules(s), [], { enabled: tab === 2 });
  const total = alerts.meta?.total ?? 0;

  const change = async (a: Alert, next: AlertStatus) => {
    setBusyId(a.id); setActionError(null);
    try { await api.setAlertStatus(a.id, next); alerts.reload(); } catch (e) { setActionError(toApiError(e).message); } finally { setBusyId(null); }
  };

  return (
    <>
      <PageHeader title="Alert Center" subtitle="Threshold alerts on error rate, latency and traffic. There is at most one active alert per service and type; repeated breaches raise its occurrence count instead of creating duplicates." />
      <Tabs value={tab} onChange={(_, v) => { setTab(v); setPage(1); }} sx={{ mb: 2 }}><Tab label="Active" /><Tab label="Resolved" /><Tab label="Rules" /></Tabs>
      {actionError && <MuiAlert severity="error" sx={{ mb: 2 }} onClose={() => setActionError(null)}>{actionError}</MuiAlert>}

      {tab < 2 && (
        <>
          <Paper sx={{ p: 2, mb: 2.5 }}>
            <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5}>
              <TextField size="small" label="Service" value={service} onChange={(e) => { setService(e.target.value.trim().toLowerCase()); setPage(1); }} inputProps={{ maxLength: 100 }} sx={{ minWidth: 200 }} />
              <FormControl size="small" sx={{ minWidth: 150 }}><InputLabel id="asv">Severity</InputLabel>
                <Select labelId="asv" label="Severity" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }}><MenuItem value="">All</MenuItem><MenuItem value="warning">Warning</MenuItem><MenuItem value="critical">Critical</MenuItem></Select>
              </FormControl>
            </Stack>
          </Paper>
          {alerts.error && <ErrorState error={alerts.error} onRetry={alerts.reload} />}
          <WarningsBanner warnings={alerts.warnings} />
          <Paper>
            {alerts.loading && !alerts.data ? <Box sx={{ p: 2 }}><LoadingBlock rows={6} /></Box> : alerts.data && alerts.data.length === 0 ? (
              <EmptyState title={tab === 0 ? 'No active alerts' : 'No resolved alerts'} hint={tab === 0 ? 'All monitored services are within their thresholds.' : undefined} action={tab === 0 ? <MuiLink component={Link} to="/scenarios">Trigger a failure in the Scenario Lab</MuiLink> : undefined} />
            ) : alerts.data && (
              <Box sx={{ overflowX: 'auto' }}>
                <Table size="small" aria-label="Alerts">
                  <TableHead><TableRow><TableCell>Severity</TableCell><TableCell>Service</TableCell><TableCell>Condition</TableCell><TableCell align="right">Measured</TableCell><TableCell align="right">Threshold</TableCell><TableCell align="right">Seen</TableCell><TableCell>Last seen</TableCell><TableCell>Status</TableCell><TableCell align="right">Actions</TableCell></TableRow></TableHead>
                  <TableBody>
                    {alerts.data.map((a) => (
                      <TableRow key={a.id} hover sx={{ cursor: 'pointer' }} onClick={() => setSelected(a)}>
                        <TableCell><SeverityChip severity={a.severity} /></TableCell>
                        <TableCell>{a.service_name}</TableCell>
                        <TableCell>{humanize(a.alert_type)} {a.operator === 'gt' ? '>' : '<'}</TableCell>
                        <TableCell align="right" sx={{ fontWeight: 650 }}>{valueText(a, 'measured_value')}</TableCell>
                        <TableCell align="right">{valueText(a, 'threshold_value')}</TableCell>
                        <TableCell align="right">×{a.occurrence_count}</TableCell>
                        <TableCell title={formatTime(a.last_seen_at)}>{relativeTime(a.last_seen_at)}</TableCell>
                        <TableCell><StatusChip status={a.status} />{a.resolution && <Typography variant="caption" color="text.secondary" component="div">{a.resolution}</Typography>}</TableCell>
                        <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                          {can('operator') && (
                            <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                              {a.status === 'open' && <Tooltip title="Acknowledge"><span><IconButton size="small" aria-label={`Acknowledge alert ${a.id}`} disabled={busyId === a.id} onClick={() => change(a, 'acknowledged')}><DoneAllIcon fontSize="small" /></IconButton></span></Tooltip>}
                              {a.status !== 'resolved' && <Tooltip title="Resolve"><span><IconButton size="small" aria-label={`Resolve alert ${a.id}`} disabled={busyId === a.id} onClick={() => change(a, 'resolved')}><CheckCircleOutlineIcon fontSize="small" /></IconButton></span></Tooltip>}
                              {a.status === 'acknowledged' && <Tooltip title="Re-open"><span><IconButton size="small" aria-label={`Re-open alert ${a.id}`} disabled={busyId === a.id} onClick={() => change(a, 'open')}><ReplayIcon fontSize="small" /></IconButton></span></Tooltip>}
                            </Stack>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
            )}
            {total > PAGE_SIZE && <Stack alignItems="flex-end" sx={{ p: 1.5 }}><Pagination count={Math.ceil(total / PAGE_SIZE)} page={page} onChange={(_, p) => setPage(p)} color="primary" shape="rounded" /></Stack>}
          </Paper>
        </>
      )}

      {tab === 2 && (
        <>
          {rules.error && <ErrorState error={rules.error} onRetry={rules.reload} />}
          <Paper>
            {rules.loading && !rules.data ? <Box sx={{ p: 2 }}><LoadingBlock rows={5} /></Box> : rules.data && (
              <Box sx={{ overflowX: 'auto' }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ p: 2 }}>
                  <Typography color="text.secondary">Service “*” is the default for every service without its own rule.</Typography>
                  {can('admin') && <Button variant="contained" startIcon={<SettingsIcon />} onClick={() => setEditing({ service_name: '*', alert_type: 'error_rate', operator: 'gt', threshold: 0.1, window_seconds: 300, min_samples: 5, severity: 'warning', enabled: true })}>New rule</Button>}
                </Stack>
                <Table size="small" aria-label="Alert rules">
                  <TableHead><TableRow><TableCell>Service</TableCell><TableCell>Type</TableCell><TableCell>Condition</TableCell><TableCell align="right">Window</TableCell><TableCell align="right">Min samples</TableCell><TableCell>Severity</TableCell><TableCell>Enabled</TableCell>{can('admin') && <TableCell />}</TableRow></TableHead>
                  <TableBody>
                    {rules.data.map((r) => (
                      <TableRow key={r.id} hover>
                        <TableCell>{r.service_name}</TableCell><TableCell>{humanize(r.alert_type)}</TableCell>
                        <TableCell>{r.operator === 'gt' ? '>' : '<'} {valueText({ alert_type: r.alert_type, measured_value: r.threshold, threshold_value: r.threshold }, 'threshold_value')}</TableCell>
                        <TableCell align="right">{r.window_seconds}s</TableCell><TableCell align="right">{r.min_samples}</TableCell>
                        <TableCell><SeverityChip severity={r.severity} /></TableCell><TableCell>{r.enabled ? 'Yes' : 'No'}</TableCell>
                        {can('admin') && <TableCell align="right"><Button size="small" onClick={() => setEditing(r)}>Edit</Button></TableCell>}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
            )}
          </Paper>
        </>
      )}

      <Dialog open={Boolean(selected)} onClose={() => setSelected(null)} maxWidth="sm" fullWidth>
        {selected && (
          <>
            <DialogTitle>{selected.service_name} · {humanize(selected.alert_type)}</DialogTitle>
            <DialogContent>
              <Stack spacing={1.5}>
                <Stack direction="row" spacing={1}><SeverityChip severity={selected.severity} /><StatusChip status={selected.status} /></Stack>
                <Typography>{selected.message}</Typography>
                <Typography variant="body2" color="text.secondary">First seen {formatTime(selected.first_seen_at)} · last seen {formatTime(selected.last_seen_at)} · {selected.occurrence_count} breach{selected.occurrence_count === 1 ? '' : 'es'}</Typography>
                {selected.acknowledged_by && <Typography variant="body2">Acknowledged by <b>{selected.acknowledged_by}</b> at {formatTime(selected.acknowledged_at)}</Typography>}
                <Typography variant="overline" color="text.secondary">Evidence</Typography>
                <JsonView value={selected.evidence} />
                <Stack direction="row" spacing={1}>
                  <Button size="small" component={Link} to={`/traces?service_name=${selected.service_name}&status=ERROR`}>Failed traces</Button>
                  <Button size="small" component={Link} to={`/metrics?service=${selected.service_name}`}>Metrics</Button>
                </Stack>
              </Stack>
            </DialogContent>
            <DialogActions><Button onClick={() => setSelected(null)}>Close</Button></DialogActions>
          </>
        )}
      </Dialog>

      <RuleDialog rule={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); rules.reload(); }} />
    </>
  );
}

function RuleDialog({ rule, onClose, onSaved }: { rule: Partial<AlertRule> | null; onClose: () => void; onSaved: () => void }) {
  const [draft, setDraft] = useState<Partial<AlertRule>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState<unknown>(null);
  if (rule !== key) { setKey(rule); setDraft(rule ?? {}); setError(null); }
  const percent = draft.alert_type === 'error_rate';
  const display = draft.threshold === undefined ? '' : String(percent ? Number((draft.threshold * 100).toFixed(4)) : draft.threshold);

  const save = async () => {
    setBusy(true); setError(null);
    try {
      await api.upsertRule({
        service_name: (draft.service_name ?? '*').trim() || '*', alert_type: draft.alert_type!, operator: draft.operator ?? 'gt', threshold: Number(draft.threshold ?? 0),
        window_seconds: Number(draft.window_seconds ?? 300), min_samples: Number(draft.min_samples ?? 5), severity: draft.severity ?? 'warning', enabled: draft.enabled ?? true,
      });
      onSaved();
    } catch (e) { setError(toApiError(e).message); } finally { setBusy(false); }
  };

  return (
    <Dialog open={Boolean(rule)} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{rule?.id ? 'Edit rule' : 'New rule'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {error && <MuiAlert severity="error">{error}</MuiAlert>}
          <TextField size="small" label="Service (or *)" value={draft.service_name ?? ''} onChange={(e) => setDraft({ ...draft, service_name: e.target.value })} disabled={Boolean(rule?.id)} inputProps={{ maxLength: 100 }} />
          <FormControl size="small"><InputLabel id="rt">Type</InputLabel>
            <Select labelId="rt" label="Type" value={draft.alert_type ?? 'error_rate'} disabled={Boolean(rule?.id)} onChange={(e) => setDraft({ ...draft, alert_type: e.target.value as AlertRule['alert_type'] })}>
              {['error_rate', 'avg_latency', 'p95_latency', 'request_volume'].map((t) => <MenuItem key={t} value={t}>{humanize(t)}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField size="small" type="number" label={percent ? 'Threshold (%)' : draft.alert_type === 'request_volume' ? 'Threshold (requests)' : 'Threshold (ms)'} value={display}
            onChange={(e) => { const n = Number(e.target.value); setDraft({ ...draft, threshold: percent ? n / 100 : n }); }} inputProps={{ min: 0, step: 'any' }} />
          <TextField size="small" type="number" label="Window (seconds)" value={draft.window_seconds ?? 300} onChange={(e) => setDraft({ ...draft, window_seconds: Number(e.target.value) })} inputProps={{ min: 30, max: 86400 }} />
          <TextField size="small" type="number" label="Minimum samples" value={draft.min_samples ?? 5} onChange={(e) => setDraft({ ...draft, min_samples: Number(e.target.value) })} inputProps={{ min: 1 }} />
          <FormControl size="small"><InputLabel id="rs">Severity</InputLabel>
            <Select labelId="rs" label="Severity" value={draft.severity ?? 'warning'} onChange={(e) => setDraft({ ...draft, severity: e.target.value as AlertRule['severity'] })}><MenuItem value="warning">Warning</MenuItem><MenuItem value="critical">Critical</MenuItem></Select>
          </FormControl>
          <FormControlLabel control={<Switch checked={draft.enabled ?? true} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />} label="Enabled" />
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Cancel</Button><Button variant="contained" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save rule'}</Button></DialogActions>
    </Dialog>
  );
}
