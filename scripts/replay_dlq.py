#!/usr/bin/env python3
"""Move messages from a dead-letter queue back to its main queue (RabbitMQ).

Usage:
    python scripts/replay_dlq.py --queue trace-processing.traces            # replay up to 100 messages
    python scripts/replay_dlq.py --queue log-processing.logs --limit 10 --dry-run

The DLQ of queue ``X`` is ``X.dlq``. Replayed messages get ``x-attempt`` reset to 1, so the
consumer's idempotency checks (event_id / message_id) still prevent double processing.
Requires: pika  (pip install pika)   and   BROKER_URL / RABBITMQ_URL (amqp://user:pass@host:5672/%2F)
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", required=True, help="main queue name; its DLQ is '<queue>.dlq'")
    ap.add_argument("--url", default=os.getenv("BROKER_URL") or os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F"))
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true", help="only report what would be replayed")
    args = ap.parse_args()

    import pika

    conn = pika.BlockingConnection(pika.URLParameters(args.url))
    ch = conn.channel()
    dlq = f"{args.queue}.dlq"
    try:
        depth = ch.queue_declare(queue=dlq, passive=True).method.message_count
    except pika.exceptions.ChannelClosedByBroker as exc:
        print(f"queue {dlq!r} not found: {exc}", file=sys.stderr)
        return 2
    print(f"{dlq}: {depth} message(s)")
    moved = 0
    while moved < args.limit:
        method, props, body = ch.basic_get(dlq, auto_ack=False)
        if method is None:
            break
        if args.dry_run:
            ch.basic_nack(method.delivery_tag, requeue=True)
            moved += 1
            continue
        headers = dict(props.headers or {})
        headers["x-attempt"] = 1
        headers.pop("x-last-error", None)
        ch.basic_publish(
            "", args.queue, body,
            pika.BasicProperties(delivery_mode=2, content_type="application/json", message_id=props.message_id, headers=headers),
            mandatory=True,
        )
        ch.basic_ack(method.delivery_tag)
        moved += 1
    print(f"{'would replay' if args.dry_run else 'replayed'} {moved} message(s) to {args.queue!r}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
