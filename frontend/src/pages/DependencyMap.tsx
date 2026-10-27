import { Box, Button, Chip, Drawer, IconButton, Stack, Typography, alpha } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/endpoints';
import type { DependencyEdge, DependencyGraph, DependencyNode, Health } from '../api/types';
import { EmptyState, ErrorState, HealthDot, LoadingBlock, PageHeader, Section, WarningsBanner } from '../components/common';
import { useRefresh } from '../components/RefreshContext';
import { useApi } from '../hooks/useApi';
import { useColorMode } from '../theme/ColorMode';
import { statusColors } from '../theme/theme';
import { formatMs, formatNumber, formatPct } from '../utils/format';

const HEALTH_COLOR: Record<Health, string> = { healthy: statusColors.ok, degraded: statusColors.warn, critical: statusColors.error, unknown: statusColors.muted };

interface ServiceNodeData extends Record<string, unknown> { node: DependencyNode }
type ServiceNode = Node<ServiceNodeData, 'service'>;

/**
 * Pure layout helper (unit tested): layered left-to-right layout from the observed edges.
 * Back-edges (calls that close a cycle, e.g. a callback) are ignored for layering so they cannot push a service
 * to the wrong side; the remaining graph is acyclic and layered by longest path from the entry services.
 */
export function layoutLayers(names: string[], edges: { source: string; target: string }[]): Record<string, { x: number; y: number }> {
  const known = new Set(names);
  const adj = new Map<string, string[]>(names.map((n) => [n, []]));
  const hasIncoming = new Set<string>();
  for (const e of edges) {
    if (e.source === e.target || !known.has(e.source) || !known.has(e.target)) continue;
    if (!adj.get(e.source)!.includes(e.target)) adj.get(e.source)!.push(e.target);
    hasIncoming.add(e.target);
  }
  // 1) drop back-edges with a DFS that starts at the entry services
  const state = new Map<string, 0 | 1 | 2>(names.map((n) => [n, 0]));
  const forward = new Map<string, string[]>(names.map((n) => [n, []]));
  const visit = (n: string): void => {
    state.set(n, 1);
    for (const next of [...(adj.get(n) ?? [])].sort()) {
      if (state.get(next) === 1) continue; // back-edge
      forward.get(n)!.push(next);
      if (state.get(next) === 0) visit(next);
    }
    state.set(n, 2);
  };
  [...names.filter((n) => !hasIncoming.has(n)).sort(), ...[...names].sort()].forEach((n) => { if (state.get(n) === 0) visit(n); });
  // 2) longest-path layering on the acyclic remainder (Kahn)
  const indegree = new Map<string, number>(names.map((n) => [n, 0]));
  forward.forEach((targets) => targets.forEach((t) => indegree.set(t, (indegree.get(t) ?? 0) + 1)));
  const depth = new Map<string, number>(names.map((n) => [n, 0]));
  const queue = names.filter((n) => indegree.get(n) === 0);
  for (let i = 0; i < queue.length; i++) {
    const n = queue[i];
    for (const next of forward.get(n) ?? []) {
      depth.set(next, Math.max(depth.get(next) ?? 0, (depth.get(n) ?? 0) + 1));
      indegree.set(next, (indegree.get(next) ?? 1) - 1);
      if (indegree.get(next) === 0) queue.push(next);
    }
  }
  const columns = new Map<number, string[]>();
  names.forEach((n) => { const d = depth.get(n) ?? 0; columns.set(d, [...(columns.get(d) ?? []), n]); });
  const pos: Record<string, { x: number; y: number }> = {};
  columns.forEach((col, d) => col.sort().forEach((n, i) => { pos[n] = { x: d * 320, y: i * 170 - ((col.length - 1) * 170) / 2 }; }));
  return pos;
}

function ServiceCard({ data }: NodeProps<ServiceNode>) {
  const n = data.node;
  const health = n.health ?? 'unknown';
  const color = HEALTH_COLOR[health];
  return (
    <Box sx={{ width: 220, p: 1.5, borderRadius: 3, bgcolor: 'background.paper', border: 2, borderColor: color, boxShadow: `0 8px 26px ${alpha(color, 0.25)}`, cursor: 'pointer' }}>
      <Handle type="target" position={Position.Left} style={{ background: color }} />
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
        <HealthDot health={health} />
        <Typography variant="subtitle2" noWrap sx={{ fontWeight: 700 }}>{n.service_name}</Typography>
      </Stack>
      {n.observed_as_peer_only ? (
        <Typography variant="caption" color="warning.main">Called, but sent no telemetry</Typography>
      ) : (
        <Typography variant="caption" color="text.secondary" component="div">
          {formatNumber(n.request_count)} req · {formatPct(n.error_rate)} err · {formatMs(n.avg_latency_ms)}
        </Typography>
      )}
      {(n.active_alerts ?? 0) > 0 && <Chip size="small" color="error" label={`${n.active_alerts} alert${n.active_alerts === 1 ? '' : 's'}`} sx={{ mt: 0.75 }} />}
      <Handle type="source" position={Position.Right} style={{ background: color }} />
    </Box>
  );
}

const nodeTypes = { service: ServiceCard };

function toFlow(graph: DependencyGraph): { nodes: ServiceNode[]; edges: Edge[] } {
  const pos = layoutLayers(graph.nodes.map((n) => n.service_name), graph.edges);
  const nodes: ServiceNode[] = graph.nodes.map((n) => ({ id: n.service_name, type: 'service', position: pos[n.service_name] ?? { x: 0, y: 0 }, data: { node: n } }));
  const edges: Edge[] = graph.edges.map((e) => {
    const color = e.failure_observed ? statusColors.error : '#a78bfa';
    return {
      id: `${e.source}->${e.target}`, source: e.source, target: e.target, animated: e.failure_observed,
      label: `${formatNumber(e.request_count)} req · ${formatPct(e.error_rate, 0)} err · ${formatMs(e.avg_latency_ms)}${e.unanswered_calls ? ` · ${e.unanswered_calls} unanswered` : ''}`,
      style: { stroke: color, strokeWidth: 2 + Math.min(4, Math.log10(1 + e.request_count)), strokeDasharray: e.unanswered_calls ? '6 4' : undefined },
      labelStyle: { fontSize: 11, fill: color }, labelBgStyle: { fill: '#120a22', fillOpacity: 0.85 }, labelBgPadding: [6, 3] as [number, number],
      markerEnd: { type: MarkerType.ArrowClosed, color },
    };
  });
  return { nodes, edges };
}

export function DependencyMap() {
  const { windowMinutes } = useRefresh();
  const { mode } = useColorMode();
  const graph = useApi((s) => api.dependencies({ window_minutes: windowMinutes }, s), [windowMinutes]);
  const [selected, setSelected] = useState<string | null>(null);
  const flow = useMemo(() => (graph.data ? toFlow(graph.data) : null), [graph.data]);
  const node = graph.data?.nodes.find((n) => n.service_name === selected) ?? null;
  const incoming: DependencyEdge[] = graph.data?.edges.filter((e) => e.target === selected) ?? [];
  const outgoing: DependencyEdge[] = graph.data?.edges.filter((e) => e.source === selected) ?? [];

  return (
    <>
      <PageHeader title="Dependency Map" subtitle="Derived entirely from observed spans: who calls whom, how often, how slowly and how often it fails. Dashed edges are calls whose callee never reported a span (timeouts, unreachable services)." />
      {graph.error && <ErrorState error={graph.error} onRetry={graph.reload} />}
      <WarningsBanner warnings={graph.warnings} />
      {graph.loading && !graph.data ? <LoadingBlock rows={1} height={520} /> : graph.data && flow && (
        graph.data.nodes.length === 0 ? <Section><EmptyState title="No service interactions observed in this window" hint="Generate traffic from the Scenario Lab, or widen the time window." /></Section> : (
          <Section sx={{ p: 0, overflow: 'hidden' }}>
            <Box sx={{ height: { xs: 520, md: 620 } }} aria-label="Service dependency graph">
              <ReactFlow
                nodes={flow.nodes} edges={flow.edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.25 }} colorMode={mode} minZoom={0.3}
                nodesConnectable={false} onNodeClick={(_, n) => setSelected(n.id)} proOptions={{ hideAttribution: true }}
              >
                <Background gap={22} color={mode === 'dark' ? '#3b2a68' : '#d8ccff'} />
                <Controls showInteractive={false} />
                <MiniMap pannable zoomable nodeColor={(n) => HEALTH_COLOR[(n.data as ServiceNodeData).node.health ?? 'unknown']} maskColor={mode === 'dark' ? 'rgba(18,10,34,.7)' : 'rgba(246,243,255,.7)'} />
              </ReactFlow>
            </Box>
            <Stack direction="row" spacing={2} sx={{ p: 1.5, borderTop: 1, borderColor: 'divider' }} flexWrap="wrap" useFlexGap>
              {(['healthy', 'degraded', 'critical', 'unknown'] as Health[]).map((h) => <Stack key={h} direction="row" spacing={0.75} alignItems="center"><HealthDot health={h} /><Typography variant="caption" color="text.secondary">{h}</Typography></Stack>)}
              <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>Click a service for details</Typography>
            </Stack>
          </Section>
        )
      )}
      <Drawer anchor="right" open={Boolean(node)} onClose={() => setSelected(null)} PaperProps={{ sx: { width: { xs: '100%', sm: 380 }, p: 2.5 } }}>
        {node && (
          <Stack spacing={2}>
            <Stack direction="row" justifyContent="space-between" alignItems="center">
              <Stack direction="row" spacing={1} alignItems="center"><HealthDot health={node.health ?? 'unknown'} /><Typography variant="h6">{node.service_name}</Typography></Stack>
              <IconButton aria-label="Close" onClick={() => setSelected(null)}><CloseIcon /></IconButton>
            </Stack>
            {node.observed_as_peer_only && <Chip color="warning" label="This service was called but sent no telemetry" />}
            <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5 }}>
              <Info label="Requests" value={formatNumber(node.request_count)} />
              <Info label="Error rate" value={formatPct(node.error_rate)} />
              <Info label="Avg latency" value={formatMs(node.avg_latency_ms)} />
              <Info label="p95 latency" value={formatMs(node.metrics?.p95_latency_ms)} />
              <Info label="Throughput" value={node.metrics?.throughput_rps != null ? `${node.metrics.throughput_rps} rps` : '—'} />
              <Info label="Active alerts" value={String(node.active_alerts ?? 0)} />
            </Box>
            <EdgeList title="Called by" edges={incoming} other="source" />
            <EdgeList title="Calls" edges={outgoing} other="target" />
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <Button size="small" variant="outlined" component={Link} to={`/traces?service_name=${node.service_name}&status=ERROR`}>Failed traces</Button>
              <Button size="small" variant="outlined" component={Link} to={`/logs?service_name=${node.service_name}&severity=ERROR`}>Error logs</Button>
              <Button size="small" variant="outlined" component={Link} to={`/metrics?service=${node.service_name}`}>Metrics</Button>
            </Stack>
          </Stack>
        )}
      </Drawer>
    </>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return <Box><Typography variant="caption" color="text.secondary">{label}</Typography><Typography fontWeight={650}>{value}</Typography></Box>;
}

function EdgeList({ title, edges, other }: { title: string; edges: DependencyEdge[]; other: 'source' | 'target' }) {
  if (edges.length === 0) return null;
  return (
    <Box>
      <Typography variant="overline" color="text.secondary">{title}</Typography>
      <Stack spacing={0.75}>
        {edges.map((e) => (
          <Box key={`${e.source}-${e.target}`} sx={{ p: 1, borderRadius: 2, border: 1, borderColor: e.failure_observed ? 'error.main' : 'divider' }}>
            <Typography variant="body2" fontWeight={600}>{e[other]}</Typography>
            <Typography variant="caption" color="text.secondary">{formatNumber(e.request_count)} calls · {formatPct(e.error_rate)} failed · avg {formatMs(e.avg_latency_ms)} · max {formatMs(e.max_latency_ms)}{e.unanswered_calls ? ` · ${e.unanswered_calls} unanswered` : ''}</Typography>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}
