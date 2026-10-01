"""Симулятор станции («полевой» уровень): исполняет план, порождает «сырые» события датчиков с шумами и принимает нештатные ситуации.

Это источник данных — аналог внешнего WebSocket-симулятора. Состояние станции остальной системе известно только из событий.
Поезд двигается по фазам: scheduled → approach → waiting → entering → on_track ⇄ processing → ready → departing → departed.
Исполнение следует плану (путь, время приёма, локомотив, момент отправления), но подчиняется физике: путь/горловина/ресурс
должны быть реально свободны — иначе поезд ждёт, и появляются отклонения от графика."""
from __future__ import annotations

import math
import random
import time
from typing import Dict, List, Optional

from .infra import Infra
from .models import LOCO_OPS, Plan, TrainSched
from .timetable import Timetable

START_MIN = 6 * 60


class SimTrain:
    def __init__(self, s: TrainSched, delay: float):
        self.s = s
        self.delay = delay
        self.true_arr = s.arr + delay
        self.phase = "scheduled"
        self.track: Optional[int] = None
        self.t0 = 0.0
        self.prog = 0.0
        self.pos_m = 0.0
        self.speed = 0.0
        self.announced = False
        self.eta_revealed = False
        self.eta_sent = s.arr
        self.arr_actual: Optional[float] = None
        self.ops_idx = 0
        self.op_end: Optional[float] = None
        self.op_loco: Optional[str] = None
        self.ready_at: Optional[float] = None
        self.sort_track: Optional[int] = None
        self.dep_actual: Optional[float] = None
        self.stop_pos = 0.0


class Simulator:
    def __init__(self, infra: Infra, cfg, seed: int = 42):
        self.infra = infra
        self.cfg = cfg
        self.rng = random.Random(seed)
        self.tt = Timetable(infra.station_id, infra, seed)
        self.now = float(START_MIN)
        self.trains: Dict[str, SimTrain] = {}
        self.plan: Optional[Plan] = None
        self.locos = {k: {"state": "idle", "track": None, "down_until": 0.0, "down_after": False, "until": 0.0} for k in infra.locos}
        self.crews_total = infra.crews
        self.absent: List[List[float]] = []                 # [from, to, n]
        self.closures: List[dict] = []                      # {track, from, to, reason}
        self.route_extra = 0
        self.route_extra_until = 0.0
        self.L_busy = 0.0
        self.R_busy = 0.0
        self.sort_busy: Dict[int, str] = {}
        self.seq: Dict[str, int] = {}
        self.tt_to = self.now
        self.dirty_res = True
        self.log: List[dict] = []
        self.P_route_in, self.P_route_out, self.P_shunt, self.P_ready, self.P_head = 3, 3, 4, 5, 2
        self._extend()

    # ------------------------------------------------------------------------------------------------------------
    def set_params(self, pc):
        self.P_route_in, self.P_route_out, self.P_shunt, self.P_ready, self.P_head = pc.route_in_min, pc.route_out_min, pc.shunt_move_min, pc.readiness_min, pc.headway_min

    def set_plan(self, plan: Plan):
        self.plan = plan

    def _extend(self):
        want = self.now + self.cfg.horizon_min
        if want <= self.tt_to:
            return
        for s in self.tt.extend(self.tt_to if self.tt_to > self.now else self.now, want):
            self._add(s)
        self.tt_to = want

    def _add(self, s: TrainSched):
        r = self.rng
        delay = 0.0
        if s.type != "transit":
            if r.random() < 0.28:
                delay = min(48.0, 3 + r.expovariate(1 / 9.0))
            else:
                delay = r.uniform(-1.0, 2.0) if r.random() < 0.4 else 0.0
        self.trains[s.id] = SimTrain(s, round(delay, 1))

    def _ev(self, kind, entity, p, ts=None):
        self.seq[entity] = self.seq.get(entity, 0) + 1
        return {"kind": kind, "entity": entity, "seq": self.seq[entity], "ts": round(self.now if ts is None else ts, 3), "p": p, "src": "sim", "wt": time.time()}

    # -- вспомогательные --
    def _rin(self):
        return self.P_route_in + (self.route_extra if self.now < self.route_extra_until else 0)

    def _rout(self):
        return self.P_route_out + (self.route_extra if self.now < self.route_extra_until else 0)

    def _mv(self):
        return self.P_shunt + (self.route_extra if self.now < self.route_extra_until else 0)

    def _closed(self, track, t=None):
        t = self.now if t is None else t
        if any(c["track"] == track and c["from"] <= t < c["to"] for c in self.closures):
            return True
        return any(tr == track and s <= t < e for tr, s, e in self.infra.closures(t, t + 1))

    def _track_busy(self, track, me):
        return any(t.track == track and t is not me and t.phase in ("entering", "on_track", "processing", "ready", "departing") for t in self.trains.values())

    def _crews_free(self):
        absent = sum(n for a, b, n in self.absent if a <= self.now < b)
        busy = sum(1 for t in self.trains.values() if t.op_loco and t.phase == "processing")
        return self.crews_total - absent - busy

    def _item(self, tid):
        return self.plan.items.get(tid) if self.plan else None

    # ------------------------------------------------------------------------------------------------------------
    def step(self, dt: float) -> List[dict]:
        """Продвинуть модельное время на dt минут и вернуть «сырые» события."""
        self.now += dt
        self._extend()
        ev: List[dict] = [self._ev("sim_clock", "clock", {"now": round(self.now, 3)})]
        # окончание нештатных ситуаций
        for c in list(self.closures):
            if c["to"] <= self.now:
                self.closures.remove(c)
                ev.append(self._ev("track", f"track-{c['track']}", {"track": c["track"], "state": "free"}))
        self.absent = [a for a in self.absent if a[1] > self.now]
        if self.route_extra and self.now >= self.route_extra_until:
            self.route_extra = 0
            ev.append(self._ev("env", "env", {"route_extra": 0, "until": 0}))
        for k, l in self.locos.items():
            if l["state"] == "down" and self.now >= l["down_until"]:
                l.update(state="idle", down_after=False)
                self.dirty_res = True

        for t in sorted(self.trains.values(), key=lambda x: x.s.arr):
            self._advance(t, ev)

        ev += self._resource_events()
        # удаляем ушедшие
        for tid in [k for k, t in self.trains.items() if t.phase == "departed"]:
            del self.trains[tid]
        return ev

    # ------------------------------------------------------------------------------------------------------------
    def _announce(self, t: SimTrain, ev):
        s = t.s
        if not t.announced and self.now >= s.arr - 240:
            t.announced = True
            ev.append(self._ev("train_sched", s.id, s.to_dict()))
        if t.announced and not t.eta_revealed and self.now >= s.arr - 40:
            t.eta_revealed = True
            if abs(t.delay) >= 0.5:
                t.eta_sent = t.true_arr
                ev.append(self._ev("train_eta", s.id, {"id": s.id, "eta": round(t.true_arr, 1)}))

    def _advance(self, t: SimTrain, ev):
        s = t.s
        self._announce(t, ev)
        ph = t.phase
        if ph == "scheduled":
            if t.announced and self.now >= t.true_arr - 10:
                t.phase = "approach"
                t.t0 = self.now
        if t.phase == "approach":
            t.prog = max(0.0, min(1.0, 1 - (t.true_arr - self.now) / 10.0))
            t.speed, t.pos_m = 55 * (1 - 0.6 * t.prog), 0.0
            if self.now >= t.true_arr:
                t.phase, t.prog, t.t0 = "waiting", 1.0, self.now
        if t.phase == "waiting":
            t.speed = 0.0
            if self._can_enter(t):
                t.phase, t.t0 = "entering", self.now
                self.L_busy = self.now + self._rin()
                if s.type == "transit":
                    t.track = None
                else:
                    t.track = self._item(s.id).track if self._item(s.id) and self._item(s.id).track else self._first_free(t)
        elif t.phase == "entering":
            dur = self._rin()
            t.prog = min(1.0, (self.now - t.t0) / dur)
            t.speed = 35 * (1 - 0.5 * t.prog)
            if s.type == "transit":
                t.pos_m = t.prog * 600
                if t.prog >= 1:
                    t.phase = "departing"
                    t.t0 = self.now
                    self.R_busy = self.now + self._rout()
            else:
                tk = self.infra.tracks[t.track]
                t.stop_pos = min(tk.length_m - 12, s.wagons * 15 + 40)
                t.pos_m = t.prog * t.stop_pos
                if t.prog >= 1:
                    t.phase, t.arr_actual, t.speed = "on_track", self.now, 0.0
                    t.ready_at = None
        if t.phase in ("on_track", "processing"):
            self._work(t, ev)
        if t.phase == "ready":
            self._try_depart(t)
        if t.phase == "departing":
            dur = self._rout()
            t.prog = min(1.0, (self.now - t.t0) / dur)
            t.speed = 5 + 40 * t.prog
            if s.type == "transit":
                t.pos_m = 600 + t.prog * 300
            else:
                t.pos_m = t.stop_pos + t.prog * 250
            if t.prog >= 1:
                t.phase = "departed"
                ev.append(self._ev("train_pos", s.id, {"phase": "departed", "track": t.track, "prog": 1.0, "pos_m": t.pos_m, "speed": 0}))
                return
        if t.phase != "scheduled":
            tr = t.track if t.phase not in ("approach", "waiting") else None
            ev.append(self._ev("train_pos", s.id, {"phase": t.phase, "track": tr, "prog": round(t.prog, 3), "pos_m": round(t.pos_m, 1), "speed": round(t.speed, 1)}))

    def _first_free(self, t: SimTrain):
        for i in self.infra.reception:
            if not self._track_busy(i, t) and not self._closed(i):
                return i
        return self.infra.reception[0]

    def _can_enter(self, t: SimTrain) -> bool:
        if self.now < self.L_busy:
            return False
        if t.s.type == "transit":
            it = self._item(t.s.id)
            return self.plan is None or it is None or self.now >= it.arr - 0.01
        it = self._item(t.s.id)
        if it is None or it.track is None:
            return False                                        # плана ещё нет — ждём на сигнале
        if self.now < it.arr - 0.01:
            return False
        if self._closed(it.track) or self._track_busy(it.track, t):
            return False
        return True

    # --- работа на пути ---
    def _work(self, t: SimTrain, ev):
        s = t.s
        if t.phase == "on_track" and t.ops_idx >= len(s.ops):
            if t.ready_at is None:
                t.ready_at = (t.arr_actual or self.now) + (s.dwell_min if s.dwell_min else 0) if not s.ops else self.now + self.P_ready
            if self.now >= t.ready_at:
                t.phase = "ready"
            return
        if t.phase == "processing":
            if self.now >= (t.op_end or 0):
                kind = s.ops[t.ops_idx].kind
                ev.append(self._ev("train_op", s.id, {"state": "end", "op": kind, "idx": t.ops_idx, "loco": t.op_loco}))
                if t.op_loco:
                    l = self.locos[t.op_loco]
                    l.update(state="down" if l["down_after"] else "idle", track=None)
                    self.dirty_res = True
                t.op_loco = None
                t.ops_idx += 1
                last_loco = not any(o.kind in LOCO_OPS for o in s.ops[t.ops_idx:])
                if last_loco and t.sort_track is not None:
                    self.sort_busy.pop(t.sort_track, None)
                    t.sort_track = None
                t.phase = "on_track"
                if t.ops_idx >= len(s.ops):
                    t.ready_at = self.now + self.P_ready
            return
        # on_track: ждём следующую операцию
        op = s.ops[t.ops_idx]
        it = self._item(s.id)
        pop = it.ops[t.ops_idx] if it and t.ops_idx < len(it.ops) else None
        if pop is not None and self.now < pop.start - 0.01:
            return
        dur = op.dur * self.rng.uniform(0.92, 1.12)
        if op.kind in LOCO_OPS:
            if self._crews_free() <= 0 or self.now < self.L_busy:
                return
            loco = None
            if pop is not None and pop.loco in self.locos and self.locos[pop.loco]["state"] == "idle":
                loco = pop.loco
            else:
                loco = next((k for k, l in self.locos.items() if l["state"] == "idle"), None)
            if loco is None:
                return
            if t.sort_track is None and self.infra.sorting:
                want = pop.sort_track if pop and pop.sort_track in self.infra.sorting and pop.sort_track not in self.sort_busy else None
                if want is None:
                    want = next((u for u in self.infra.sorting if u not in self.sort_busy and not self._closed(u)), None)
                if want is None:
                    return
                t.sort_track = want
                self.sort_busy[want] = s.id
            t.op_loco = loco
            self.locos[loco].update(state="busy", track=t.track)
            self.L_busy = self.now + self._mv()
            self.dirty_res = True
        t.phase = "processing"
        t.op_end = self.now + dur
        ev.append(self._ev("train_op", s.id, {"state": "start", "op": op.kind, "idx": t.ops_idx, "loco": t.op_loco, "end": round(t.op_end, 2), "sort_track": t.sort_track}))

    def _try_depart(self, t: SimTrain):
        it = self._item(t.s.id)
        earliest = max(t.s.dep, (t.ready_at or self.now), it.dep if it else 0)
        if self.now >= earliest and self.now >= self.R_busy:
            t.phase, t.t0, t.dep_actual = "departing", self.now, self.now
            self.R_busy = self.now + self._rout()

    # ------------------------------------------------------------------------------------------------------------
    def _resource_events(self) -> List[dict]:
        out = []
        if not self.dirty_res and int(self.now * 2) % 6 != 0:
            return out
        self.dirty_res = False
        for k, l in self.locos.items():
            out.append(self._ev("resource", k, {"rtype": "loco", "state": l["state"], "track": l["track"], "until": l["down_until"] if l["state"] == "down" else 0}))
        out.append(self._ev("resource", "crews", {"rtype": "crew", "total": self.crews_total, "absent": [{"from": a, "to": b, "n": int(n)} for a, b, n in self.absent]}))
        return out

    # ------------------------------------------------------------------------------------------------------------
    def inject(self, kind: str, params: dict) -> dict:
        """Нештатная ситуация. Возвращает описание и сразу готовые события (чтобы интерфейс увидел её мгновенно)."""
        now = self.now
        ev: List[dict] = []
        info = {"kind": kind, "at": now}
        if kind == "train_delay":
            minutes = float(params.get("min", 25))
            tid = params.get("train")
            cand = [t for t in self.trains.values() if t.phase in ("scheduled", "approach") and t.s.type != "transit"]
            cand.sort(key=lambda x: x.s.arr)
            t = self.trains.get(tid) if tid in self.trains else (cand[1] if len(cand) > 1 else (cand[0] if cand else None))
            if t is None:
                return {**info, "error": "no_train"}
            t.delay += minutes
            t.true_arr = t.s.arr + t.delay
            t.eta_revealed = True
            t.eta_sent = t.true_arr
            if t.phase == "approach":
                t.phase = "scheduled"
            if not t.announced:
                t.announced = True
                ev.append(self._ev("train_sched", t.s.id, t.s.to_dict()))
            ev.append(self._ev("train_eta", t.s.id, {"id": t.s.id, "eta": round(t.true_arr, 1)}))
            info.update(train=t.s.id, minutes=minutes)
        elif kind in ("track_close", "switch_fail"):
            track = int(params.get("track", self.infra.reception[min(1, len(self.infra.reception) - 1)]))
            if track not in self.infra.tracks:
                return {**info, "error": "bad_track"}
            minutes = float(params.get("minutes", 60))
            self.closures.append({"track": track, "from": now, "to": now + minutes, "reason": "switch" if kind == "switch_fail" else "closed"})
            ev.append(self._ev("track", f"track-{track}", {"track": track, "state": "closed", "from": now, "until": now + minutes, "reason": "switch" if kind == "switch_fail" else "closed"}))
            info.update(track=track, minutes=minutes)
        elif kind == "loco_down":
            ids = [k for k, l in self.locos.items() if l["state"] != "down"]
            k = params.get("loco") if params.get("loco") in self.locos else (ids[0] if ids else None)
            if k is None:
                return {**info, "error": "no_loco"}
            minutes = float(params.get("minutes", 90))
            l = self.locos[k]
            l["down_until"] = now + minutes
            if l["state"] == "busy":
                l["down_after"] = True
            else:
                l["state"] = "down"
            self.dirty_res = True
            ev += self._resource_events()
            info.update(loco=k, minutes=minutes)
        elif kind == "crew_absent":
            n = int(params.get("n", 1))
            minutes = float(params.get("minutes", 120))
            self.absent.append([now, now + minutes, n])
            self.dirty_res = True
            ev += self._resource_events()
            info.update(n=n, minutes=minutes)
        elif kind == "power_fail":
            self.route_extra = int(params.get("extra", 3))
            minutes = float(params.get("minutes", 60))
            self.route_extra_until = now + minutes
            ev.append(self._ev("env", "env", {"route_extra": self.route_extra, "until": self.route_extra_until}))
            info.update(minutes=minutes, extra=self.route_extra)
        elif kind == "burst":
            n = int(params.get("n", 4))
            ids = []
            for s in self.tt.extra_burst(now, n):
                self._add(s)
                tr = self.trains[s.id]
                tr.delay = 0.0
                tr.true_arr = s.arr
                tr.announced = tr.eta_revealed = True
                ev.append(self._ev("train_sched", s.id, s.to_dict()))
                ids.append(s.id)
            info.update(n=n, trains=ids)
        else:
            return {**info, "error": "unknown_kind"}
        self.log.append(info)
        info["events"] = ev
        return info

    def add_loco(self, loco_id: str, start_at: float) -> List[dict]:
        """Резервный локомотив: появляется в депо в момент start_at."""
        self.locos[loco_id] = {"state": "down" if start_at > self.now else "idle", "track": None, "down_until": start_at, "down_after": False, "until": 0.0}
        self.dirty_res = True
        return self._resource_events()

    def end_absence_by(self, t: float) -> List[dict]:
        """Резервная бригада: отсутствие сокращается до момента t."""
        for a in self.absent:
            a[1] = min(a[1], max(t, self.now + 0.5))
        self.dirty_res = True
        return self._resource_events()

    @staticmethod
    def kinds():
        return ["train_delay", "track_close", "switch_fail", "loco_down", "crew_absent", "power_fail", "burst"]


def add_noise(events: List[dict], noise, rng: random.Random, carry: List[dict]) -> List[dict]:
    """Имитация «грязного» канала: дубликаты, потери, переупорядочивание, шум координат, некорректные значения."""
    out: List[dict] = list(carry)
    carry.clear()
    for e in events:
        if e["kind"] in ("sim_clock",):
            out.append(e)
            continue
        if rng.random() < noise.drop_rate and e["kind"] == "train_pos":
            continue
        e = {**e, "p": dict(e["p"])}
        if e["kind"] == "train_pos" and noise.jitter_m:
            e["p"]["pos_m"] = max(0.0, e["p"]["pos_m"] + rng.gauss(0, noise.jitter_m))
            e["p"]["speed"] = max(0.0, e["p"]["speed"] + rng.gauss(0, 1.0))
        if rng.random() < noise.invalid_rate and e["kind"] == "train_pos":
            bad = rng.choice(["neg", "track", "nan", "phase"])
            if bad == "neg":
                e["p"]["pos_m"] = -50
            elif bad == "track":
                e["p"]["track"] = 99
            elif bad == "nan":
                e["p"]["speed"] = float("nan")
            else:
                e["p"]["phase"] = "teleport"
        if rng.random() < noise.reorder_rate and e["kind"] == "train_pos":
            carry.append(e)                                    # придёт на следующем такте — уже устаревшим
            continue
        out.append(e)
        if rng.random() < noise.dup_rate:
            out.append(dict(e))
    return out
