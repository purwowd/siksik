from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class Metrics:
    """Counter + latency sederhana untuk production observability."""

    requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    timeouts: int = 0
    errors: int = 0
    by_mode: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_action: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    latency_ms_sum: float = 0.0
    latency_ms_count: int = 0

    @contextmanager
    def track(self, mode: str) -> Iterator[None]:
        self.requests += 1
        self.by_mode[mode] += 1
        start = time.perf_counter()
        try:
            yield
        except TimeoutError:
            self.timeouts += 1
            raise
        except Exception:
            self.errors += 1
            raise
        finally:
            elapsed = (time.perf_counter() - start) * 1000.0
            self.latency_ms_sum += elapsed
            self.latency_ms_count += 1

    def record_action(self, action: str) -> None:
        self.by_action[action] += 1

    def record_cache_hit(self) -> None:
        self.cache_hits += 1

    def record_cache_miss(self) -> None:
        self.cache_misses += 1

    def snapshot(self) -> dict:
        avg_ms = (
            self.latency_ms_sum / self.latency_ms_count
            if self.latency_ms_count
            else 0.0
        )
        return {
            "requests": self.requests,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": (
                self.cache_hits / (self.cache_hits + self.cache_misses)
                if (self.cache_hits + self.cache_misses)
                else 0.0
            ),
            "timeouts": self.timeouts,
            "errors": self.errors,
            "avg_latency_ms": round(avg_ms, 2),
            "by_mode": dict(self.by_mode),
            "by_action": dict(self.by_action),
        }
