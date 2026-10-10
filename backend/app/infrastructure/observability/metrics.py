"""Dependency-free in-process metrics registry for the chat pipeline.

The plan's observability item (metrics and monitoring) is served here with
zero new dependencies: counters and timers live in one module-level registry
so the API layer (`app.api.v1.chat`) and the ReAct service
(`app.services.chat`) can both record without passing state around, and a
plain-dict snapshot is what the `GET /api/v1/chat/metrics` endpoint
serializes. There is intentionally NO prometheus client — the instrumented
surface is a couple of dict updates per event, which cannot measurably
affect streaming latency.

Design notes
------------
* Labels are folded INTO the metric name (e.g. ``chat.error.provider_unreachable``)
  so the registry stays a flat dict of totals — no label arity to maintain,
  and the snapshot API never grows.
* Timers record ``(sum, count)``; the snapshot derives ``mean`` from them.
  Walls are measured with ``time.monotonic`` by the callers, never here.
* Names are prefixed ``chat.`` and grouped:
    - counters_: turn distribution, retrieval, intent gate, privacy, errors.
    - timers_: ``chat.retrieval.seconds``, ``chat.drafting.seconds``.
* ``reset()`` is the test seam: the registry is process-global, so tests
  that assert on metric values clear it first.
"""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()


class Metrics:
    """Thread-safe counters (name → total) and timers (name → sum/count)."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._timers: dict[str, dict[str, float]] = {}

    def incr(self, name: str, by: int = 1) -> None:
        """Increment a total counter (names may be label-folded)."""
        with _LOCK:
            self._counters[name] = self._counters.get(name, 0) + by

    def timing(self, name: str, seconds: float) -> None:
        """Record one duration sample; snapshot reports the running mean."""
        with _LOCK:
            entry = self._timers.setdefault(name, {"sum": 0.0, "count": 0})
            entry["sum"] += seconds
            entry["count"] += 1

    def snapshot(self) -> dict[str, Any]:
        """Copy of every counter and timer (with derived ``mean``)."""
        with _LOCK:
            counters = dict(self._counters)
            timers = {
                name: {
                    "sum": entry["sum"],
                    "count": entry["count"],
                    "mean": entry["sum"] / entry["count"] if entry["count"] else 0.0,
                }
                for name, entry in self._timers.items()
            }
        return {"counters": counters, "timers": timers}

    def reset(self) -> None:
        """Test seam: drop every recorded sample."""
        with _LOCK:
            self._counters.clear()
            self._timers.clear()


metrics = Metrics()
