"""Наблюдаемое состояние станции — строится ТОЛЬКО из нормализованных событий (ingest). Здесь же — живые KPI для индекса."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .infra import Infra
from .models import OpSpec

ACTIVE = ("approach", "waiting", "entering", "on_track", "processing", "ready", "departing")


@dataclass
class TrainObs:
    id: str
    type: str
    pr: int
    wagons: int
    sched_arr: float
    sched_dep: float
    ops: List[Tuple[str, int]]
    dwell_min: int
    cargo: str = ""
    eta: float = 0.0
    phase: str = "scheduled"
    track: Optional[int] = None
    prog: float = 0.0
    pos_m: float = 0.0
    speed: float = 0.0
    arrived_at: Optional[float] = None
    departed_at: Optional[float] = None
    ops_done: int = 0
    op_running: Optional[dict] = None
    last_wall: float = 0.0
    stale: bool = False
    sort_track: Optional[int] = None

    @property
    def transit(self):
        return self.type == "transit"

    def to_frame(self):
        return {"id": self.id, "type": self.type, "pr": self.pr, "w": self.wagons, "ph": self.phase, "tr": self.track,
                "pg": round(self.prog, 3), "pos": round(self.pos_m), "v": round(self.speed, 1), "eta": round(self.eta, 1),
                "sa": self.sched_arr, "sd": self.sched_dep, "od": self.ops_done, "nops": len(self.ops), "stale": self.stale,
                "op": (self.op_running or {}).get("kind"), "loco": (self.op_running or {}).get("loco"), "st": self.sort_track}


class StationState:
    def __init__(self, infra: Infra):
        self.infra = infra
        self.now = 0.0
        self.trains: Dict[str, TrainObs] = {}
        self.departed: Deque[dict] = deque(maxlen=80)
        self.locos: Dict[str, dict] = {k: {"state": "idle", "track": None, "until": 0.0, "wall": 0.0} for k in infra.locos}
        self.crews_total = infra.crews
        self.crew_absent: List[Tuple[float, float, int]] = []
        self.closures: List[dict] = []
        self.route_extra = 0
        self.route_extra_until = 0.0
        self.last_event_wall = 0.0
        self.util_loco = 0.4
        self.util_crew = 0.4

    # --------------------------------------------------------------------------------------------------------------
    def apply(self, ev: dict, wall: float):
        k, p = ev["kind"], ev["p"]
        self.last_event_wall = wall
        if k == "sim_clock":
            self.now = max(self.now, p["now"])
        elif k == "train_sched":
            t = self.trains.get(p["id"])
            ops = [(o["kind"], int(o["dur"])) for o in p.get("ops", [])]
            if t is None:
                self.trains[p["id"]] = TrainObs(p["id"], p["type"], int(p["pr"]), int(p["wagons"]), float(p["arr"]), float(p["dep"]), ops,
                                                int(p.get("dwell_min", 0)), p.get("cargo", ""), eta=float(p["arr"]), last_wall=wall)
            else:
                t.sched_arr, t.sched_dep, t.ops = float(p["arr"]), float(p["dep"]), ops
        elif k == "train_eta":
            t = self.trains.get(p["id"])
            if t and t.phase in ("scheduled", "approach", "waiting"):
                t.eta = float(p["eta"])
                t.last_wall = wall
        elif k == "train_pos":
            t = self.trains.get(ev["entity"])
            if t is None:
                return
            ph = p["phase"]
            if ph == "departed":
                self._depart(t, ev["ts"])
                return
            if ph in ("on_track", "processing", "ready") and t.arrived_at is None:
                t.arrived_at = ev["ts"]
            t.phase, t.track = ph, p.get("track")
            t.prog, t.pos_m, t.speed = p["prog"], p["pos_m"], p["speed"]
            t.last_wall, t.stale = wall, False
        elif k == "train_op":
            t = self.trains.get(ev["entity"])
            if t is None:
                return
            if p["state"] == "start":
                t.op_running = {"kind": p["op"], "loco": p.get("loco"), "start": ev["ts"], "end": p.get("end", ev["ts"] + 10)}
                t.sort_track = p.get("sort_track", t.sort_track)
                if p.get("loco") in self.locos:
                    self.locos[p["loco"]].update(state="busy", track=t.track)
            else:
                t.ops_done = max(t.ops_done, int(p["idx"]) + 1)
                if t.op_running and t.op_running.get("loco") in self.locos and self.locos[t.op_running["loco"]]["state"] == "busy":
                    self.locos[t.op_running["loco"]].update(state="idle", track=None)
                t.op_running = None
        elif k == "resource":
            if p["rtype"] == "loco":
                st = self.locos.setdefault(ev["entity"], {"state": "idle", "track": None, "until": 0.0, "wall": wall})
                st.update(state=p["state"], track=p.get("track"), until=float(p.get("until", 0.0)), wall=wall)
            else:
                self.crews_total = int(p["total"])
                self.crew_absent = [(float(a["from"]), float(a["to"]), int(a["n"])) for a in p.get("absent", [])]
        elif k == "track":
            tr = p["track"]
            self.closures = [c for c in self.closures if c["track"] != tr or c.get("src") == "maint"]
            if p["state"] == "closed":
                self.closures.append({"track": tr, "from": float(p.get("from", ev["ts"])), "to": float(p["until"]), "reason": p.get("reason", "closed"), "src": "event"})
        elif k == "env":
            self.route_extra = int(p.get("route_extra", 0))
            self.route_extra_until = float(p.get("until", 0.0))

    def _depart(self, t: TrainObs, ts: float):
        t.phase, t.departed_at = "departed", ts
        self.departed.append({"id": t.id, "type": t.type, "pr": t.pr, "sched_arr": t.sched_arr, "sched_dep": t.sched_dep,
                              "arr": t.arrived_at if t.arrived_at is not None else t.eta, "dep": ts})
        self.trains.pop(t.id, None)

    # --------------------------------------------------------------------------------------------------------------
    def prune_closures(self):
        self.closures = [c for c in self.closures if c["to"] > self.now - 1]

    def all_closures(self, horizon: float) -> List[Tuple[int, int, int]]:
        out = [(c["track"], int(c["from"]), int(c["to"] + 0.999)) for c in self.closures if c["to"] > self.now]
        out += self.infra.closures(self.now, self.now + horizon)
        return out

    def closed_now(self) -> set:
        return {tr for tr, s, e in self.all_closures(1) if s <= self.now < e}

    def mark_stale(self, wall: float, after_s: float) -> int:
        n = 0
        for t in self.trains.values():
            if t.phase not in ("scheduled",) and wall - t.last_wall > after_s:
                if not t.stale:
                    t.stale = True
                n += 1
        return n

    def occupancy(self) -> Dict[int, str]:
        occ = {}
        for t in self.trains.values():
            if t.track is not None and t.phase in ("entering", "on_track", "processing", "ready", "departing"):
                occ[t.track] = t.id
        return occ

    # --------------------------------------------------------------------------------------------------------------
    def kpis(self, pw: Dict[int, float], conflicts_w: float, window: float = 60.0) -> Dict[str, float]:
        now = self.now
        w0 = now - window
        # пропускная способность: обработано / запланировано за окно
        served = sum(1 for d in self.departed if d["type"] != "transit" and w0 <= d["dep"] <= now)
        due_dep = [t for t in self.trains.values() if t.type != "transit" and w0 <= t.sched_dep <= now]
        planned = served + len(due_dep) + sum(1 for d in self.departed if d["type"] != "transit" and w0 <= d["sched_dep"] <= now and not (w0 <= d["dep"] <= now))
        thr = served / planned if planned else 1.0
        # отклонения: опоздание отправления + ожидание приёма
        devs, wts = [], []
        for d in self.departed:
            if d["type"] == "transit" or d["dep"] < w0:
                continue
            devs.append((max(0.0, d["dep"] - d["sched_dep"]) + 0.5 * max(0.0, d["arr"] - d["sched_arr"]), pw.get(d["pr"], 2)))
        for t in self.trains.values():
            if t.type == "transit" or t.phase == "scheduled":
                continue
            late_dep = max(0.0, now - t.sched_dep) if t.phase in ("ready", "processing", "on_track") else 0.0
            wait_in = max(0.0, (now - t.sched_arr)) if t.phase in ("approach", "waiting") else max(0.0, (t.arrived_at or now) - t.sched_arr)
            devs.append((late_dep + 0.5 * wait_in, pw.get(t.pr, 2)))
        sw = sum(w for _, w in devs)
        avg_dev = sum(d * w for d, w in devs) / sw if sw else 0.0
        max_dev = max((d for d, _ in devs), default=0.0)
        # загрузка путей
        closed = self.closed_now()
        usable = [t for t in self.infra.tracks if t not in closed]
        occ = set(self.occupancy())
        for t in self.trains.values():
            if t.sort_track is not None and t.op_running:
                occ.add(t.sort_track)
        track_load = len([t for t in usable if t in occ]) / max(1, len(usable))
        queue = sum(1 for t in self.trains.values() if t.phase in ("waiting",) or (t.phase == "approach" and now >= t.eta))
        locos_ok = [k for k, v in self.locos.items() if v["state"] != "down"]
        busy_l = sum(1 for k in locos_ok if self.locos[k]["state"] == "busy")
        ul = busy_l / len(locos_ok) if locos_ok else 1.0
        absent = sum(n for a, b, n in self.crew_absent if a <= now < b)
        cap = max(0, self.crews_total - absent)
        busy_c = sum(1 for t in self.trains.values() if t.op_running and t.op_running.get("kind") in ("ROSPUSK", "PODFORM"))
        uc = busy_c / cap if cap else 1.0
        a = 0.08                                          # сглаживание загрузки ресурсов (EMA)
        self.util_loco += a * (ul - self.util_loco)
        self.util_crew += a * (uc - self.util_crew)
        return {"throughput": thr, "avg_dev": avg_dev, "max_dev": max_dev, "track_load": track_load, "conflicts": conflicts_w,
                "queue_outside": float(queue), "loco_idle": self.util_loco, "crew_idle": self.util_crew}
