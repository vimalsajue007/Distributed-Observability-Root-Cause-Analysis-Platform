"""Aggregation layer: composes trace, log, metrics and diagnostic data for the console.

Primary data (the thing asked for) failing => the error names the failing downstream service.
Secondary enrichment failing => a *partial* result with a machine-readable warning, never a silent gap.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from obs_common.api import ROLE_RANK
from obs_common.http import DownstreamError, SyncDownstreamClient

log = logging.getLogger("observability-query")

Warning = dict[str, str]


class Sources:
    def __init__(self, traces: SyncDownstreamClient, logs: SyncDownstreamClient, metrics: SyncDownstreamClient, rca: SyncDownstreamClient):
        self.traces, self.logs, self.metrics, self.rca = traces, logs, metrics, rca
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="query")

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        for c in (self.traces, self.logs, self.metrics, self.rca):
            c.close()

    # -------------------------------------------------------------- primitives

    @staticmethod
    def call(client: SyncDownstreamClient, path: str, params: dict[str, Any] | None, role: str, user: str) -> dict[str, Any]:
        headers = {"X-User-Role": role, "X-User-Name": user}
        return client.request_json("GET", path, params=params, headers=headers)

    def primary(self, client, path, params, role, user) -> dict[str, Any]:
        return self.call(client, path, params, role, user)  # DownstreamError propagates -> mapped by the route

    def optional(self, client, path, params, role, user, label: str) -> tuple[Any, Warning | None]:
        try:
            return self.call(client, path, params, role, user).get("data"), None
        except DownstreamError as exc:
            if exc.status_code == 404:
                return None, None  # nothing there yet is not a degraded dependency
            log.warning("enrichment unavailable", extra={"downstream": exc.service, "kind": exc.kind})
            return None, {"code": f"{label}_unavailable", "message": f"{exc.service} is {_kind_text(exc.kind)}; {label.replace('_', ' ')} data is missing from this response."}

    def parallel(self, jobs: dict[str, tuple]) -> dict[str, tuple[Any, Warning | None]]:
        futures = {name: self._pool.submit(self.optional, *args, name) for name, args in jobs.items()}
        return {name: f.result() for name, f in futures.items()}


def _kind_text(kind: str) -> str:
    return {"timeout": "timing out", "unavailable": "unreachable"}.get(kind, "returning errors")


def can_read_logs(role: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK["operator"]
