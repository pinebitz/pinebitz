from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock


@dataclass
class RouteMetric:
    count: int = 0
    total_ms: int = 0
    min_ms: int = 0
    max_ms: int = 0
    status_2xx: int = 0
    status_4xx: int = 0
    status_5xx: int = 0


class InMemoryMetrics:
    """Small in-process metrics collector for API timing."""

    def __init__(self, latency_window_size: int = 200) -> None:
        self._window_size = latency_window_size
        self._routes: dict[str, RouteMetric] = defaultdict(RouteMetric)
        self._latencies: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=self._window_size))
        self._lock = Lock()

    def record(self, *, route_key: str, duration_ms: int, status_code: int) -> None:
        with self._lock:
            m = self._routes[route_key]
            m.count += 1
            m.total_ms += duration_ms
            if m.min_ms == 0 or duration_ms < m.min_ms:
                m.min_ms = duration_ms
            if duration_ms > m.max_ms:
                m.max_ms = duration_ms

            if 200 <= status_code <= 299:
                m.status_2xx += 1
            elif 400 <= status_code <= 499:
                m.status_4xx += 1
            elif 500 <= status_code <= 599:
                m.status_5xx += 1

            self._latencies[route_key].append(duration_ms)

    def snapshot(self) -> dict:
        with self._lock:
            routes = {}
            total_requests = 0
            for key, m in self._routes.items():
                total_requests += m.count
                avg_ms = round(m.total_ms / m.count, 2) if m.count else 0.0
                p95_ms = self._p95(self._latencies[key])
                routes[key] = {
                    "count": m.count,
                    "avg_ms": avg_ms,
                    "p95_ms": p95_ms,
                    "min_ms": m.min_ms,
                    "max_ms": m.max_ms,
                    "status_2xx": m.status_2xx,
                    "status_4xx": m.status_4xx,
                    "status_5xx": m.status_5xx,
                }
            return {
                "total_requests": total_requests,
                "routes": routes,
            }

    def snapshot_prometheus(self) -> str:
        snap = self.snapshot()
        lines: list[str] = []
        lines.append("# HELP pinebitz_http_requests_total Total HTTP requests observed")
        lines.append("# TYPE pinebitz_http_requests_total counter")
        lines.append("# HELP pinebitz_http_request_duration_ms_avg Average request duration in milliseconds")
        lines.append("# TYPE pinebitz_http_request_duration_ms_avg gauge")
        lines.append("# HELP pinebitz_http_request_duration_ms_p95 p95 request duration in milliseconds")
        lines.append("# TYPE pinebitz_http_request_duration_ms_p95 gauge")
        lines.append("# HELP pinebitz_http_request_duration_ms_min Min request duration in milliseconds")
        lines.append("# TYPE pinebitz_http_request_duration_ms_min gauge")
        lines.append("# HELP pinebitz_http_request_duration_ms_max Max request duration in milliseconds")
        lines.append("# TYPE pinebitz_http_request_duration_ms_max gauge")
        lines.append("# HELP pinebitz_http_requests_status_total Requests by status class")
        lines.append("# TYPE pinebitz_http_requests_status_total counter")

        lines.append(f'pinebitz_http_requests_total{{route="__all__"}} {snap["total_requests"]}')
        for route, m in snap["routes"].items():
            route_label = route.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'pinebitz_http_requests_total{{route="{route_label}"}} {m["count"]}')
            lines.append(f'pinebitz_http_request_duration_ms_avg{{route="{route_label}"}} {m["avg_ms"]}')
            lines.append(f'pinebitz_http_request_duration_ms_p95{{route="{route_label}"}} {m["p95_ms"]}')
            lines.append(f'pinebitz_http_request_duration_ms_min{{route="{route_label}"}} {m["min_ms"]}')
            lines.append(f'pinebitz_http_request_duration_ms_max{{route="{route_label}"}} {m["max_ms"]}')
            lines.append(
                f'pinebitz_http_requests_status_total{{route="{route_label}",status_class="2xx"}} {m["status_2xx"]}'
            )
            lines.append(
                f'pinebitz_http_requests_status_total{{route="{route_label}",status_class="4xx"}} {m["status_4xx"]}'
            )
            lines.append(
                f'pinebitz_http_requests_status_total{{route="{route_label}",status_class="5xx"}} {m["status_5xx"]}'
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _p95(values: deque[int]) -> int:
        if not values:
            return 0
        arr = sorted(values)
        idx = max(0, int((len(arr) - 1) * 0.95))
        return arr[idx]
