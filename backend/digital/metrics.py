"""Метрики сервиса в формате Prometheus (без внешних зависимостей) + структурированное логирование."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from typing import Dict, Tuple

LOG = logging.getLogger("railtwin")


def setup_logging(level="INFO"):
    class J(logging.Formatter):
        def format(self, r):
            d = {"ts": round(time.time(), 3), "lvl": r.levelname, "logger": r.name, "msg": r.getMessage()}
            for k in ("station", "event", "ms", "reason"):
                if hasattr(r, k):
                    d[k] = getattr(r, k)
            return json.dumps(d, ensure_ascii=False)
    h = logging.StreamHandler()
    h.setFormatter(J())
    root = logging.getLogger("railtwin")
    root.handlers[:] = [h]
    root.setLevel(level)
    root.propagate = False


class Metrics:
    BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

    def __init__(self):
        self.lock = threading.Lock()
        self.counters: Dict[Tuple[str, Tuple], float] = defaultdict(float)
        self.gauges: Dict[Tuple[str, Tuple], float] = {}
        self.hist: Dict[Tuple[str, Tuple], dict] = {}
        self.started = time.time()

    @staticmethod
    def _k(name, labels):
        return name, tuple(sorted((labels or {}).items()))

    def inc(self, name, v=1.0, **labels):
        with self.lock:
            self.counters[self._k(name, labels)] += v

    def set(self, name, v, **labels):
        with self.lock:
            self.gauges[self._k(name, labels)] = v

    def observe(self, name, v, **labels):
        k = self._k(name, labels)
        with self.lock:
            h = self.hist.setdefault(k, {"b": [0] * len(self.BUCKETS), "sum": 0.0, "n": 0})
            for i, b in enumerate(self.BUCKETS):
                if v <= b:
                    h["b"][i] += 1
            h["sum"] += v
            h["n"] += 1

    def quantile(self, name, q, **labels):
        h = self.hist.get(self._k(name, labels))
        if not h or not h["n"]:
            return 0.0
        target = q * h["n"]
        for i, b in enumerate(self.BUCKETS):
            if h["b"][i] >= target:
                return b
        return self.BUCKETS[-1]

    @staticmethod
    def _fmt(labels):
        return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}" if labels else ""

    def render(self) -> str:
        out = [f"# TYPE railtwin_uptime_seconds gauge\nrailtwin_uptime_seconds {time.time() - self.started:.1f}"]
        with self.lock:
            seen = set()
            for (n, l), v in sorted(self.counters.items()):
                if n not in seen:
                    out.append(f"# TYPE {n} counter")
                    seen.add(n)
                out.append(f"{n}{self._fmt(l)} {v}")
            for (n, l), v in sorted(self.gauges.items()):
                if n not in seen:
                    out.append(f"# TYPE {n} gauge")
                    seen.add(n)
                out.append(f"{n}{self._fmt(l)} {v}")
            for (n, l), h in sorted(self.hist.items()):
                out.append(f"# TYPE {n} histogram")
                for i, b in enumerate(self.BUCKETS):
                    out.append(f'{n}_bucket{self._fmt(l + (("le", b),))} {h["b"][i]}')
                out.append(f'{n}_bucket{self._fmt(l + (("le", "+Inf"),))} {h["n"]}')
                out.append(f"{n}_sum{self._fmt(l)} {h['sum']:.4f}")
                out.append(f"{n}_count{self._fmt(l)} {h['n']}")
        return "\n".join(out) + "\n"


METRICS = Metrics()
