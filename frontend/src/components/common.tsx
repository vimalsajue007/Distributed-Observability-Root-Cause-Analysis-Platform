import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import InboxOutlinedIcon from '@mui/icons-material/InboxOutlined';
import RefreshIcon from '@mui/icons-material/Refresh';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { Alert, AlertTitle, Box, Button, Chip, CircularProgress, Paper, Skeleton, Stack, Typography, alpha, useTheme } from '@mui/material';
import type { ReactNode } from 'react';
import type { ApiError } from '../api/client';
import type { ApiWarning, Classification, Confidence, Health } from '../api/types';
import { statusColors } from '../theme/theme';
import { humanize } from '../utils/format';

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" alignItems={{ xs: 'flex-start', sm: 'center' }} spacing={2} sx={{ mb: 3 }}>
      <Box>
        <Typography variant="h4" component="h1">{title}</Typography>
        {subtitle && <Typography color="text.secondary" sx={{ mt: 0.5, maxWidth: 820 }}>{subtitle}</Typography>}
      </Box>
      {actions && <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">{actions}</Stack>}
    </Stack>
  );
}

export function Section({ title, action, children, sx }: { title?: string; action?: ReactNode; children: ReactNode; sx?: object }) {
  return (
    <Paper sx={{ p: 2.5, ...sx }}>
      {(title || action) && (
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
          {title && <Typography variant="h6">{title}</Typography>}
          {action}
        </Stack>
      )}
      {children}
    </Paper>
  );
}

export function StatCard({ label, value, hint, accent, icon }: { label: string; value: ReactNode; hint?: ReactNode; accent?: string; icon?: ReactNode }) {
  const theme = useTheme();
  const color = accent ?? theme.palette.primary.main;
  return (
    <Paper sx={{ p: 2.25, position: 'relative', overflow: 'hidden', '&::before': { content: '""', position: 'absolute', inset: '0 auto 0 0', width: 4, background: color } }}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
        <Box>
          <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: '0.08em' }}>{label}</Typography>
          <Typography variant="h4" sx={{ lineHeight: 1.15, mt: 0.25 }}>{value}</Typography>
          {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
        </Box>
        {icon && <Box sx={{ color, opacity: 0.85, display: 'flex' }}>{icon}</Box>}
      </Stack>
    </Paper>
  );
}

export function StatGrid({ children }: { children: ReactNode }) {
  return <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, mb: 3 }}>{children}</Box>;
}

type ChipColor = 'default' | 'success' | 'warning' | 'error' | 'info' | 'primary' | 'secondary';

export function StatusChip({ status }: { status: string }) {
  const map: Record<string, ChipColor> = {
    OK: 'success', ERROR: 'error', up: 'success', down: 'error', degraded: 'warning', healthy: 'success', critical: 'error', unknown: 'default',
    open: 'error', acknowledged: 'warning', resolved: 'success', complete: 'success', incomplete: 'warning', in_progress: 'info',
  };
  return <Chip size="small" label={humanize(status)} color={map[status] ?? 'default'} variant="outlined" />;
}

export function SeverityChip({ severity }: { severity: string }) {
  const map: Record<string, ChipColor> = { INFO: 'info', WARNING: 'warning', ERROR: 'error', CRITICAL: 'error', warning: 'warning', critical: 'error', info: 'info', low: 'info', medium: 'warning', high: 'error' };
  const filled = severity === 'CRITICAL' || severity === 'critical';
  return <Chip size="small" label={severity.toUpperCase()} color={map[severity] ?? 'default'} variant={filled ? 'filled' : 'outlined'} />;
}

export function HealthDot({ health }: { health: Health | string }) {
  const color = health === 'healthy' || health === 'up' ? statusColors.ok : health === 'degraded' ? statusColors.warn : health === 'critical' || health === 'down' ? statusColors.error : statusColors.muted;
  return <Box component="span" aria-label={String(health)} sx={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', bgcolor: color, boxShadow: `0 0 0 3px ${alpha(color, 0.25)}` }} />;
}

const CLASS_LABEL: Record<Classification, { label: string; color: ChipColor }> = {
  confirmed_failure: { label: 'Confirmed failure', color: 'error' },
  potential_cause: { label: 'Potential cause (unconfirmed)', color: 'warning' },
  latency_bottleneck: { label: 'Latency bottleneck', color: 'info' },
  insufficient_evidence: { label: 'Insufficient evidence', color: 'default' },
  healthy: { label: 'Healthy', color: 'success' },
};
export function ClassificationChip({ value }: { value: Classification }) {
  const c = CLASS_LABEL[value] ?? { label: humanize(value), color: 'default' as ChipColor };
  return <Chip size="small" label={c.label} color={c.color} />;
}
export function ConfidenceChip({ value }: { value: Confidence }) {
  const color: ChipColor = value === 'high' ? 'success' : value === 'medium' ? 'info' : value === 'low' ? 'warning' : 'default';
  return <Chip size="small" variant="outlined" color={color} label={`${humanize(value)} confidence`} />;
}

export function LoadingBlock({ rows = 4, height = 36 }: { rows?: number; height?: number }) {
  return (
    <Stack spacing={1} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} variant="rounded" height={height} />)}
    </Stack>
  );
}

export function Spinner({ label }: { label?: string }) {
  return <Stack direction="row" spacing={1.5} alignItems="center" sx={{ p: 2 }}><CircularProgress size={20} /><Typography color="text.secondary">{label ?? 'Loading…'}</Typography></Stack>;
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <Stack alignItems="center" spacing={1} sx={{ py: 6, color: 'text.secondary', textAlign: 'center' }}>
      <InboxOutlinedIcon sx={{ fontSize: 44, opacity: 0.6 }} />
      <Typography variant="subtitle1" color="text.primary">{title}</Typography>
      {hint && <Typography variant="body2" sx={{ maxWidth: 460 }}>{hint}</Typography>}
      {action}
    </Stack>
  );
}

/** Shows the failing component (gateway vs a named downstream service) and the correlation id support can search for. */
export function ErrorState({ error, onRetry, compact }: { error: ApiError; onRetry?: () => void; compact?: boolean }) {
  const where =
    error.source === 'downstream' ? `Downstream service${error.service ? `: ${error.service}` : ''}`
      : error.source === 'gateway' ? 'API gateway'
        : error.source === 'network' ? 'Network'
          : error.service ? `Service: ${error.service}` : 'Service';
  const forbidden = error.status === 403;
  return (
    <Alert
      severity={forbidden ? 'info' : 'error'}
      icon={<ErrorOutlineIcon />}
      action={onRetry && !forbidden ? <Button color="inherit" size="small" startIcon={<RefreshIcon />} onClick={onRetry}>Retry</Button> : undefined}
      sx={{ alignItems: 'flex-start' }}
    >
      <AlertTitle>{forbidden ? 'Not permitted for your role' : compact ? error.message : 'Something went wrong'}</AlertTitle>
      {!compact && <Typography variant="body2">{error.message}</Typography>}
      <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
        <Chip size="small" label={where} />
        <Chip size="small" variant="outlined" label={`${error.code}${error.status ? ` · HTTP ${error.status}` : ''}`} />
        {error.correlationId && <Chip size="small" variant="outlined" label={`correlation ${error.correlationId.slice(0, 12)}`} title={error.correlationId} />}
      </Stack>
    </Alert>
  );
}

/** Partial results: some data sources were unavailable but the rest is still shown. */
export function WarningsBanner({ warnings }: { warnings: ApiWarning[] | undefined | null }) {
  if (!warnings || warnings.length === 0) return null;
  return (
    <Alert severity="warning" icon={<WarningAmberIcon />} sx={{ mb: 2 }}>
      <AlertTitle>Partial result</AlertTitle>
      <Stack component="ul" sx={{ m: 0, pl: 2 }} spacing={0.25}>
        {warnings.map((w) => <li key={w.code}><Typography variant="body2">{w.message}</Typography></li>)}
      </Stack>
    </Alert>
  );
}

export function JsonView({ value, maxHeight = 260 }: { value: unknown; maxHeight?: number }) {
  return (
    <Box component="pre" sx={{ m: 0, p: 1.5, borderRadius: 2, overflow: 'auto', maxHeight, fontSize: 12.5, bgcolor: (t) => alpha(t.palette.primary.main, 0.08), fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }}>
      {JSON.stringify(value, null, 2)}
    </Box>
  );
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return <Typography component="code" title={title} sx={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', fontSize: 12.5 }}>{children}</Typography>;
}
