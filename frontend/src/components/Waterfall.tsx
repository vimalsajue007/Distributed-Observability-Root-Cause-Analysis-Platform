import { Box, Chip, Paper, Stack, Tooltip, Typography, alpha } from '@mui/material';
import { useMemo, useState } from 'react';
import type { Span } from '../api/types';
import { chartColors, statusColors } from '../theme/theme';
import { formatMs, formatTime } from '../utils/format';
import { JsonView, Mono } from './common';

/** Pure helper (unit tested): left offset and width of a span bar as percentages of the trace duration. */
export function barGeometry(span: Pick<Span, 'offset_ms' | 'duration_ms'>, traceDurationMs: number): { left: number; width: number } {
  const total = Math.max(traceDurationMs, 0.001);
  const left = Math.min(Math.max((span.offset_ms / total) * 100, 0), 99.5);
  const width = Math.min(Math.max((span.duration_ms / total) * 100, 0.6), 100 - left);
  return { left, width };
}

export function serviceColor(services: string[], name: string): string {
  const i = services.indexOf(name);
  return chartColors[(i < 0 ? 0 : i) % chartColors.length];
}

export function Waterfall({ spans, durationMs, highlightSpanId }: { spans: Span[]; durationMs: number; highlightSpanId?: string | null }) {
  const [selected, setSelected] = useState<string | null>(highlightSpanId ?? null);
  const ordered = useMemo(() => [...spans].sort((a, b) => a.order - b.order), [spans]);
  const services = useMemo(() => Array.from(new Set(ordered.map((s) => s.service_name))), [ordered]);
  const active = ordered.find((s) => s.span_id === selected) ?? null;

  return (
    <Box>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1.5 }}>
        {services.map((s) => <Chip key={s} size="small" label={s} sx={{ bgcolor: alpha(serviceColor(services, s), 0.2), color: 'text.primary', borderLeft: `4px solid ${serviceColor(services, s)}` }} />)}
        <Chip size="small" variant="outlined" label="error" sx={{ borderColor: statusColors.error, color: statusColors.error }} />
        <Chip size="small" variant="outlined" label="slow" sx={{ borderColor: statusColors.warn, color: statusColors.warn }} />
      </Stack>
      <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
        <Box role="table" aria-label="Span waterfall">
          <Stack role="row" direction="row" sx={{ px: 1.5, py: 0.75, bgcolor: (t) => alpha(t.palette.primary.main, 0.1), fontSize: 12, color: 'text.secondary' }}>
            <Box sx={{ width: { xs: '48%', md: '38%' }, flexShrink: 0 }}>Operation</Box>
            <Box sx={{ flexGrow: 1, display: 'flex', justifyContent: 'space-between' }}><span>0</span><span>{formatMs(durationMs / 2)}</span><span>{formatMs(durationMs)}</span></Box>
          </Stack>
          {ordered.map((s) => {
            const { left, width } = barGeometry(s, durationMs);
            const color = s.is_error ? statusColors.error : serviceColor(services, s.service_name);
            const isActive = s.span_id === selected;
            return (
              <Box
                key={s.span_id}
                role="row"
                tabIndex={0}
                aria-selected={isActive}
                onClick={() => setSelected(isActive ? null : s.span_id)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelected(isActive ? null : s.span_id); } }}
                sx={{
                  display: 'flex', alignItems: 'center', px: 1.5, py: 0.6, cursor: 'pointer', borderTop: 1, borderColor: 'divider',
                  bgcolor: isActive ? (t) => alpha(t.palette.primary.main, 0.18) : 'transparent', '&:hover, &:focus-visible': { bgcolor: (t) => alpha(t.palette.primary.main, 0.12), outline: 'none' },
                }}
              >
                <Box sx={{ width: { xs: '48%', md: '38%' }, flexShrink: 0, pr: 1, minWidth: 0, pl: `${Math.min(s.depth, 8) * 14}px` }}>
                  <Typography variant="body2" noWrap fontWeight={600}>
                    {s.missing_parent && <Tooltip title="Parent span was never received"><span>⚠ </span></Tooltip>}
                    {s.operation_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" noWrap component="div">{s.service_name} · {s.kind.toLowerCase()}</Typography>
                </Box>
                <Box sx={{ position: 'relative', flexGrow: 1, height: 22, bgcolor: (t) => alpha(t.palette.primary.main, 0.06), borderRadius: 1 }}>
                  <Tooltip title={`${formatMs(s.duration_ms)} (self ${formatMs(s.self_time_ms)})`}>
                    <Box
                      sx={{
                        position: 'absolute', top: 3, bottom: 3, left: `${left}%`, width: `${width}%`, borderRadius: 1, bgcolor: color,
                        outline: s.is_slow ? `2px solid ${statusColors.warn}` : 'none', outlineOffset: 1,
                      }}
                    />
                  </Tooltip>
                  <Typography variant="caption" sx={{ position: 'absolute', top: 2, left: `min(${left + width}%, calc(100% - 64px))`, ml: 0.75, color: 'text.secondary', whiteSpace: 'nowrap' }}>
                    {formatMs(s.duration_ms)}
                  </Typography>
                </Box>
              </Box>
            );
          })}
        </Box>
      </Paper>
      {active && (
        <Paper variant="outlined" sx={{ mt: 2, p: 2 }}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
            <Typography variant="subtitle1" fontWeight={700}>{active.operation_name}</Typography>
            <Chip size="small" label={active.service_name} />
            <Chip size="small" variant="outlined" label={active.kind} />
            {active.is_error && <Chip size="small" color="error" label="ERROR" />}
            {active.is_slow && <Chip size="small" color="warning" label="SLOW" />}
          </Stack>
          {active.error_message && <Typography color="error" sx={{ mb: 1 }}>{active.error_message}</Typography>}
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, gap: 1.5, mb: 1.5 }}>
            <Meta label="Duration" value={formatMs(active.duration_ms)} />
            <Meta label="Self time" value={formatMs(active.self_time_ms)} />
            <Meta label="Started" value={formatTime(active.start_time)} />
            <Meta label="Peer service" value={active.peer_service ?? '—'} />
            <Meta label="Span id" value={<Mono>{active.span_id}</Mono>} />
            <Meta label="Parent" value={<Mono>{active.parent_span_id ?? 'root'}</Mono>} />
          </Box>
          <JsonView value={active.attributes} maxHeight={200} />
        </Paper>
      )}
    </Box>
  );
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return <Box><Typography variant="caption" color="text.secondary">{label}</Typography><Typography variant="body2" component="div">{value}</Typography></Box>;
}
