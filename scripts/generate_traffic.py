#!/usr/bin/env python3
"""Generate sample traffic through the API gateway (stdlib only).

    python scripts/generate_traffic.py                                  # a mix of every scenario, once
    python scripts/generate_traffic.py --rounds 5 --scenarios normal payment_failure
    GATEWAY_URL=http://localhost:8000 OPERATOR_PASSWORD=... python scripts/generate_traffic.py

Prints one line per order: scenario, HTTP status, latency, the failing service (if any) and the correlation id to search for.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

SCENARIOS = ["normal", "slow_payment", "payment_failure", "payment_timeout", "notification_timeout", "invalid_downstream_response", "intermittent_unavailability"]


def request(method: str, url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 30.0) -> tuple[int, dict, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}"), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as err:
        try:
            payload = json.loads(err.read() or b"{}")
        except ValueError:
            payload = {}
        return err.code, payload, {k.lower(): v for k, v in err.headers.items()}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gateway", default=os.getenv("GATEWAY_URL", "http://localhost:8000"))
    p.add_argument("--user", default=os.getenv("OPERATOR_USER", "operator"))
    p.add_argument("--password", default=os.getenv("OPERATOR_PASSWORD") or os.getenv("SEED_OPERATOR_PASSWORD"))
    p.add_argument("--rounds", type=int, default=1, help="how many times to run the scenario list")
    p.add_argument("--scenarios", nargs="*", default=SCENARIOS, choices=SCENARIOS)
    p.add_argument("--pause", type=float, default=0.2, help="seconds between orders")
    args = p.parse_args()
    if not args.password:
        print("Set OPERATOR_PASSWORD (or pass --password).", file=sys.stderr)
        return 2

    status, body, _ = request("POST", f"{args.gateway}/api/v1/auth/login", {"username": args.user, "password": args.password})
    if status != 200:
        print(f"login failed: HTTP {status} {body.get('error', {}).get('message', '')}", file=sys.stderr)
        return 1
    auth = {"Authorization": f"Bearer {body['data']['access_token']}"}

    sent = failed = 0
    for round_no in range(1, args.rounds + 1):
        for scenario in random.sample(args.scenarios, len(args.scenarios)):
            order = {"customer_id": f"cust-{random.randint(1, 50)}", "currency": "USD", "items": [{"sku": f"SKU-{random.randint(1, 9)}", "quantity": random.randint(1, 3), "unit_price": round(random.uniform(5, 80), 2)}]}
            t0 = time.monotonic()
            code, payload, headers = request("POST", f"{args.gateway}/api/v1/sample/orders", order, {**auth, "X-Scenario": scenario})
            ms = (time.monotonic() - t0) * 1000
            err = payload.get("error") or {}
            sent += 1
            failed += code >= 400
            where = f"  <- {err.get('source')}: {err.get('service')} ({err.get('code')})" if err else ""
            print(f"[{round_no}] {scenario:<28} HTTP {code}  {ms:7.0f} ms  correlation={headers.get('x-correlation-id', '-')}{where}")
            time.sleep(args.pause)
    print(f"\n{sent} orders sent, {failed} returned an error status (expected for failure scenarios).")
    print("Failed/slow traces are analysed automatically once their quiet period elapses (default 10 s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
