import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Span } from '../api/types';
import { barGeometry, Waterfall } from './Waterfall';
import { layoutLayers } from '../pages/DependencyMap';

const span = (over: Partial<Span>): Span => ({
  span_id: 'a'.repeat(16), parent_span_id: null, service_name: 'order-service', operation_name: 'POST /orders', kind: 'SERVER', start_time: '2026-10-16T10:00:00Z', end_time: '2026-10-16T10:00:01Z',
  duration_ms: 1000, status: 'OK', error_message: null, peer_service: null, attributes: {}, depth: 0, order: 0, offset_ms: 0, self_time_ms: 400, children_count: 1, is_error: false, is_slow: false,
  missing_parent: false, invalid_parent: false, ...over,
});

describe('waterfall geometry', () => {
  it('positions bars proportionally and never overflows', () => {
    expect(barGeometry({ offset_ms: 250, duration_ms: 500 }, 1000)).toEqual({ left: 25, width: 50 });
    const tail = barGeometry({ offset_ms: 990, duration_ms: 500 }, 1000);
    expect(tail.left + tail.width).toBeLessThanOrEqual(100);
    expect(barGeometry({ offset_ms: 0, duration_ms: 0 }, 1000).width).toBeGreaterThan(0);
    expect(barGeometry({ offset_ms: 0, duration_ms: 5 }, 0).left).toBe(0);
  });
});

describe('Waterfall', () => {
  it('renders rows and shows error details when a span is selected', async () => {
    const spans = [
      span({ order: 0 }),
      span({ span_id: 'b'.repeat(16), parent_span_id: 'a'.repeat(16), service_name: 'payment-service', operation_name: 'payment.charge', depth: 1, order: 1, offset_ms: 100, duration_ms: 300, is_error: true, status: 'ERROR', error_message: 'card processor failed' }),
    ];
    render(<Waterfall spans={spans} durationMs={1000} />);
    expect(screen.getAllByRole('row')).toHaveLength(3); // header + 2 spans
    await userEvent.click(screen.getByText('payment.charge'));
    expect(screen.getByText('card processor failed')).toBeInTheDocument();
  });
});

describe('dependency layout', () => {
  it('places callers left of callees and survives cycles', () => {
    const pos = layoutLayers(['gateway', 'order', 'payment'], [{ source: 'gateway', target: 'order' }, { source: 'order', target: 'payment' }, { source: 'payment', target: 'order' }]);
    expect(pos.gateway.x).toBeLessThan(pos.order.x);
    expect(pos.order.x).toBeLessThan(pos.payment.x);
    expect(Object.keys(layoutLayers(['a', 'b'], [{ source: 'a', target: 'b' }, { source: 'b', target: 'a' }]))).toHaveLength(2);
  });
});
