"""Приём и нормализация потока данных: валидация, дедупликация, переупорядочивание (буфер), сглаживание шумов.

Событие (JSON):  {"kind": "train_pos", "entity": "Г-2041", "seq": 17, "ts": 523.5, "p": {...}, "src": "sim", "wt": 1699999999.1}
  ts — модельное время (мин суток), seq — монотонный номер по сущности, wt — wall-time создания (для замера задержки).
Виды: sim_clock, train_sched, train_eta, train_pos, train_op, resource, track, env.
"""
from __future__ import annotations

import json
import math
import time
from collections import OrderedDict, defaultdict
from typing import Dict, List, Tuple

from .infra import Infra
from .models import OP_KINDS, PHASES, TRAIN_TYPES
from .state import StationState

KINDS = ("sim_clock", "train_sched", "train_eta", "train_pos", "train_op", "resource", "track", "env")


def _num(x, lo=-1e9, hi=1e9) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and lo <= x <= hi


class Validator:
    def __init__(self, infra: Infra):
        self.infra = infra

    def check(self, ev) -> str | None:
        """Возвращает причину отказа или None, если событие корректно."""
        if not isinstance(ev, dict):
            return "not_object"
        k = ev.get("kind")
        if k not in KINDS:
            return "unknown_kind"
        if not isinstance(ev.get("entity"), str) or not ev["entity"]:
            return "no_entity"
        if not _num(ev.get("ts"), -1e6, 1e7):
            return "bad_ts"
        p = ev.get("p")
        if not isinstance(p, dict):
            return "no_payload"
        tr = self.infra.tracks
        if k == "sim_clock":
            return None if _num(p.get("now"), 0, 1e7) else "bad_now"
        if k == "train_sched":
            if p.get("type") not in TRAIN_TYPES:
                return "bad_type"
            if not (isinstance(p.get("id"), str) and _num(p.get("pr"), 1, 5) and _num(p.get("wagons"), 1, 120)):
                return "bad_train"
            if not (_num(p.get("arr")) and _num(p.get("dep")) and p["dep"] >= p["arr"]):
                return "bad_times"
            for o in p.get("ops", []):
                if o.get("kind") not in OP_KINDS or not _num(o.get("dur"), 1, 600):
                    return "bad_op"
            return None
        if k == "train_eta":
            return None if (isinstance(p.get("id"), str) and _num(p.get("eta"), 0, 1e7)) else "bad_eta"
        if k == "train_pos":
            if p.get("phase") not in PHASES:
                return "bad_phase"
            t = p.get("track")
            if t is not None and t not in tr:
                return "unknown_track"
            if not _num(p.get("prog"), 0, 1.0001):
                return "bad_prog"
            if not _num(p.get("pos_m"), 0, 5000):
                return "bad_pos"
            if not _num(p.get("speed"), 0, 120):
                return "bad_speed"
            return None
        if k == "train_op":
            if p.get("state") not in ("start", "end") or p.get("op") not in OP_KINDS or not _num(p.get("idx"), 0, 20):
                return "bad_op"
            return None
        if k == "resource":
            if p.get("rtype") == "loco":
                return None if p.get("state") in ("idle", "busy", "down") else "bad_loco_state"
            if p.get("rtype") == "crew":
                return None if _num(p.get("total"), 0, 50) else "bad_crew"
            return "bad_rtype"
        if k == "track":
            if p.get("track") not in tr or p.get("state") not in ("free", "closed"):
                return "bad_track"
            if p["state"] == "closed" and not _num(p.get("until"), 0, 1e7):
                return "bad_until"
            return None
        if k == "env":
            return None if _num(p.get("route_extra", 0), 0, 30) else "bad_env"
        return "unknown"


class Ingest:
    def __init__(self, infra: Infra, state: StationState, icfg):
        self.infra, self.state, self.cfg = infra, state, icfg
        self.v = Validator(infra)
        self.seen: "OrderedDict[Tuple, None]" = OrderedDict()
        self.buffer: List[Tuple[float, dict]] = []                # (wall прихода, событие)
        self.latest_ts: Dict[Tuple[str, str], float] = {}
        self.ema: Dict[str, dict] = {}
        self.counters: Dict[str, int] = defaultdict(int)
        self.invalid_reasons: Dict[str, int] = defaultdict(int)
        self.lat_ms: List[float] = []

    def set_config(self, icfg):
        self.cfg = icfg

    # ------------------------------------------------------------------------------------------------------------
    def push(self, events: List[dict], wall: float | None = None):
        wall = time.time() if wall is None else wall
        for ev in events:
            self.counters["received"] += 1
            why = self.v.check(ev)
            if why:
                self.counters["invalid"] += 1
                self.invalid_reasons[why] += 1
                continue
            key = (ev["kind"], ev["entity"], ev.get("seq")) if ev.get("seq") is not None else \
                (ev["kind"], ev["entity"], round(ev["ts"], 3), json.dumps(ev["p"], sort_keys=True, default=str))
            if key in self.seen:
                self.counters["duplicates"] += 1
                continue
            self.seen[key] = None
            if len(self.seen) > self.cfg.dedup_window:
                self.seen.popitem(last=False)
            self.buffer.append((wall, ev))

    def flush(self, wall: float | None = None, force: bool = False) -> int:
        """Применить к состоянию события, вышедшие из буфера переупорядочивания (по ts, затем seq)."""
        wall = time.time() if wall is None else wall
        win = 0 if force else self.cfg.reorder_window_ms / 1000.0
        ready = [e for t, e in self.buffer if wall - t >= win]
        if not ready:
            return 0
        self.buffer = [(t, e) for t, e in self.buffer if wall - t < win]
        ready.sort(key=lambda e: (e["ts"], e.get("seq") or 0))
        n = 0
        for ev in ready:
            ek = (ev["kind"], ev["entity"])
            if ev["kind"] in ("train_pos", "resource", "track") and ev["ts"] < self.latest_ts.get(ek, -1e9) - 1e-6:
                self.counters["stale"] += 1                       # опоздавшее событие по той же сущности
                continue
            self.latest_ts[ek] = ev["ts"]
            if ev["kind"] == "train_pos":
                self._smooth(ev)
            self.state.apply(ev, wall)
            if ev.get("wt"):
                self.lat_ms.append((wall - ev["wt"]) * 1000)
                if len(self.lat_ms) > 400:
                    self.lat_ms = self.lat_ms[-200:]
            self.counters["applied"] += 1
            n += 1
        return n

    def _smooth(self, ev):
        """EMA по координате и скорости — гасит шум датчиков; смена фазы и пути сбрасывает фильтр."""
        p, a = ev["p"], self.cfg.ema_alpha
        e = self.ema.get(ev["entity"])
        if e is None or e["phase"] != p["phase"] or e["track"] != p.get("track"):
            self.ema[ev["entity"]] = {"phase": p["phase"], "track": p.get("track"), "pos": p["pos_m"], "v": p["speed"]}
            return
        e["pos"] += a * (p["pos_m"] - e["pos"])
        e["v"] += a * (p["speed"] - e["v"])
        p["pos_m"], p["speed"] = e["pos"], e["v"]

    def stats(self) -> dict:
        lat = sorted(self.lat_ms)
        p95 = lat[int(len(lat) * 0.95)] if lat else 0.0
        return {**self.counters, "invalid_reasons": dict(self.invalid_reasons), "buffer": len(self.buffer),
                "latency_ms_avg": round(sum(lat) / len(lat), 1) if lat else 0.0, "latency_ms_p95": round(p95, 1)}
