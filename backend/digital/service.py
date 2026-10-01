"""Оркестратор «цифровой станции»: поток событий → нормализация → состояние → конфликты → индекс → план → рассылка (WS/SSE) и история.

Конвейер на каждом такте (по умолчанию 2 Гц):
    Simulator.step → шум канала → Ingest.push/flush (валидация, дедупликация, переупорядочивание, сглаживание)
    → StationState → конфликты текущего плана при фактической обстановке → индекс эффективности
    → (при необходимости) асинхронный пересчёт плана → кадр state всем подписчикам → снимок в хранилище.
Нештатная ситуация применяется немедленно (в обход такта) и сразу запускает расчёт альтернативных планов."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Dict, List, Optional

from . import config as cfgmod
from .conflicts import conflict_weight, suggest_resolutions, validate_plan
from .efficiency import compute_index, formula_doc
from .infra import build_infra
from .ingest import Ingest
from .kpi import diff_changes, plan_kpis, plan_summary
from .metrics import METRICS
from .models import Plan, hhmm
from .planner import Params, PlanInput, TrainJob, solve, solve_heuristic, solve_parallel
from .simulator import Simulator, add_noise
from .state import StationState
from .store import Store

LOG = logging.getLogger("railtwin")
POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="planner")
HARD = {"TRACK_OVERLAP", "TRACK_CLOSED", "ROUTE_CONFLICT", "LOCO_CONFLICT", "LOCO_UNAVAILABLE", "CREW_SHORTAGE", "ETA_SHIFT", "UNPLANNED"}
RESOURCE_KINDS = {"loco_down", "crew_absent"}


class ClientQueue:
    """Очередь подписчика: при отставании выбрасывает старые кадры, но не блокирует цикл реального времени."""

    def __init__(self, maxsize=120):
        self.q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, msg: str):
        if self.q.full():
            try:
                self.q.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
        self.q.put_nowait(msg)

    async def get(self) -> str:
        return await self.q.get()


class StationRuntime:
    def __init__(self, sid: str, store: Store):
        self.sid = sid
        self.store = store
        c = cfgmod.get()
        self.infra = build_infra(sid)
        self.state = StationState(self.infra)
        self.ingest = Ingest(self.infra, self.state, c.ingest)
        self.sim = Simulator(self.infra, c.sim, c.sim.seed)
        self.sim.set_params(c.planner)
        self.rng = random.Random(c.sim.seed + 11)
        self.carry: List[dict] = []
        self.plan: Optional[Plan] = None
        self.plan_version = 0
        self.alts: Optional[dict] = None
        self.alt_plans: Dict[str, Plan] = {}
        self.conflicts: List[dict] = []
        self.conf_sig = ""
        self.index: dict = {}
        self.frames = deque(maxlen=int(c.storage.replay_buffer_s * max(c.sim.tick_hz, 1)))
        self.clients: set = set()
        self.alerts: deque = deque(maxlen=300)
        self.seen_alert: set = set()
        self.seq = 0
        self.tick_no = 0
        self.replanning = False
        self.pending: Optional[dict] = None
        self.block_until = 0.0
        self.last_snapshot = 0.0
        self.task: Optional[asyncio.Task] = None
        self.paused = False
        self.scale = c.sim.time_scale
        self.last_client = time.time()
        self.tick_ms = 0.0
        self.auto_apply = c.planner.auto_apply
        self.last_input: Optional[PlanInput] = None
        self.last_plan_ms = {"quick": 0.0, "optimal": 0.0}
        self.disruptions: deque = deque(maxlen=100)
        self.events_out = 0
        self.pins: Dict[str, dict] = {}
        self.phase_ms: dict = {}

    # ------------------------------------------------------------------------------------------------------------
    @property
    def cfg(self):
        return cfgmod.get()

    def on_config(self, c):
        self.ingest.set_config(c.ingest)
        self.sim.set_params(c.planner)
        self.sim.cfg = c.sim
        self.auto_apply = c.planner.auto_apply
        self.request_replan("config")

    # ------------------------------------------------------------------------------------------------------------
    def build_input(self) -> PlanInput:
        st, c = self.state, self.cfg
        now = int(st.now + 0.999)
        jobs: List[TrainJob] = []
        for t in st.trains.values():
            if t.phase in ("departing", "departed"):
                continue
            occ = t.phase in ("entering", "on_track", "processing", "ready") and t.track is not None
            if t.transit:
                if t.phase in ("entering",):
                    continue
                jobs.append(TrainJob(t.id, t.type, t.pr, t.wagons, int(max(now, t.sched_arr)), int(max(now, t.sched_arr)), int(t.sched_dep), [], 0, t.phase, None, None, True))
                continue
            ops = t.ops[t.ops_done:]
            running = None
            if t.op_running and ops:
                running = (t.op_running["kind"], int(t.op_running["end"] + 0.999), t.op_running.get("loco"))
            eta = int(max(now, t.eta + 0.999)) if not occ else now
            pin = self.pins.get(t.id)
            if pin and not occ:
                eta = max(eta, int(pin.get("not_before", 0)))
            jobs.append(TrainJob(t.id, t.type, t.pr, t.wagons, eta, int(t.sched_arr), int(t.sched_dep + 0.999), list(ops),
                                 max(0, t.dwell_min - int(max(0, now - (t.arrived_at or now)))) if t.type == "passenger" else 0,
                                 t.phase, t.track if occ else None, running, False, (pin or {}).get("track") if not occ else None))
            if pin and pin.get("pr_boost"):
                jobs[-1].pr = min(5, jobs[-1].pr + int(pin["pr_boost"]))
        for k in [k for k in self.pins if k not in st.trains]:
            self.pins.pop(k, None)
        locos = {k: (int(v["until"] + 0.999) if v["state"] == "down" else now) for k, v in st.locos.items()}
        crew_abs = [(int(a), int(b + 0.999), n) for a, b, n in st.crew_absent if b > now]
        rex = st.route_extra if st.now < st.route_extra_until else 0
        return PlanInput(now, self.infra, jobs, locos, st.crews_total, crew_abs, st.all_closures(c.planner.horizon_min), rex, c.planner.horizon_min)

    def params(self, **over) -> Params:
        return Params.from_config(self.cfg.planner, **over)

    # ------------------------------------------------------------------------------------------------------------
    def bootstrap(self, minutes: int = 100):
        """«Тёплый старт»: прогоняем модель без шума, чтобы на старте на путях уже были поезда и накопилась история."""
        c = self.cfg
        for i in range(int(minutes)):
            evs = self.sim.step(1.0)
            self.ingest.push(evs)
            self.ingest.flush(force=True)
            if i % 4 == 0:
                inp = self.build_input()
                P = self.params()
                self._adopt(solve_heuristic(inp, P, self.plan, "bootstrap"), inp, silent=True)
        inp = self.build_input()
        P = self.params()
        self._adopt(solve(inp, P, self.plan, "initial"), inp, silent=True)
        self.ingest.counters.clear()
        self.ingest.invalid_reasons.clear()
        self.update_derived()

    # ------------------------------------------------------------------------------------------------------------
    def _adopt(self, plan: Plan, inp: PlanInput, silent=False, label: str | None = None):
        self.plan_version += 1
        plan.version = self.plan_version
        if label:
            plan.label = label
        P = self.params()
        cf = validate_plan(inp, plan, P, self.cfg.conflicts)
        plan.kpi = {**plan_summary(plan, P), "conflicts": len(cf), "changes": diff_changes(plan, self.plan)}
        self.plan = plan
        self.sim.set_plan(plan)
        self.last_input = inp
        if not silent:
            msg = {"type": "plan", "st": self.sid, "plan": plan.to_dict(), "sim": round(self.state.now, 1)}
            self.broadcast(msg)
            self.store.plan(self.sid, time.time(), self.state.now, plan.version, plan.label, {**plan.kpi, "solver": plan.solver})
            METRICS.inc("railtwin_plans_total", station=self.sid, engine=plan.solver.get("engine", "?"))
        return plan

    # ------------------------------------------------------------------------------------------------------------
    def update_derived(self):
        """Конфликты текущего плана при фактической обстановке и индекс эффективности."""
        st, c = self.state, self.cfg
        inp = self.build_input()
        P = self.params()
        if self.plan is None:
            self.conflicts = []
        else:
            self.conflicts = validate_plan(inp, self.plan, P, c.conflicts)
        kp = st.kpis(P.pw, conflict_weight(self.conflicts))
        self.index = compute_index(c.index, kp)
        self.index["kpi"] = {k: round(v, 3) for k, v in kp.items()}
        return inp, P

    def publish_conflicts(self, inp, P):
        sig = ",".join(sorted(c["id"] for c in self.conflicts))
        if sig == self.conf_sig:
            return
        self.conf_sig = sig
        enriched = []
        for c in self.conflicts[:30]:
            enriched.append({**c, "resolutions": suggest_resolutions(c, inp, self.plan, P) if self.plan else []})
        self.broadcast({"type": "conflicts", "st": self.sid, "list": enriched, "sim": round(self.state.now, 1)})
        for c in self.conflicts:
            if c["severity"] in ("crit",) and c["id"] not in self.seen_alert:
                self.seen_alert.add(c["id"])
                self.alert("crit", "CONFLICT", {"type": c["type"], "trains": c["trains"], "track": c["track"], **c["params"]})

    def alert(self, level, code, params):
        a = {"wall": time.time(), "sim": round(self.state.now, 1), "level": level, "code": code, "params": params}
        self.alerts.append(a)
        self.broadcast({"type": "alert", "st": self.sid, **a})
        self.store.event(self.sid, a["wall"], a["sim"], code, {"level": level, **params})

    # ------------------------------------------------------------------------------------------------------------
    def need_replan(self) -> Optional[str]:
        if self.plan is None:
            return "no_plan"
        if any(c["type"] in HARD for c in self.conflicts):
            return "conflict"
        return None

    def request_replan(self, reason: str, alts: bool = False, disruption: Optional[dict] = None):
        if self.replanning:
            p = self.pending or {"reason": reason, "alts": False, "disruptions": [], "t": time.perf_counter()}
            p["alts"] = p["alts"] or alts
            if disruption:
                p["disruptions"].append(disruption)
            self.pending = p
            return
        self.replanning = True
        d = [disruption] if disruption else []
        asyncio.get_event_loop().create_task(self._replan(reason, alts, d, time.perf_counter()))

    async def _replan(self, reason, alts, disruptions, t_req):
        loop = asyncio.get_event_loop()
        try:
            inp = self.build_input()
            P = self.params()
            prev = self.plan
            t0 = time.perf_counter()
            if alts:
                await self._alternatives(inp, P, prev, disruptions, t_req)
            else:
                prev_bad = prev is None or any(c["type"] in HARD for c in validate_plan(inp, prev, P, self.cfg.conflicts))
                quick = solve_heuristic(inp, P, prev, "quick")
                self.last_plan_ms["quick"] = round((time.perf_counter() - t0) * 1000, 1)
                if prev_bad:
                    self._adopt(quick, inp, label="quick")
                    METRICS.observe("railtwin_replan_seconds", time.perf_counter() - t_req, stage="quick")
                if self.cfg.planner.engine == "cpsat":
                    plan = await loop.run_in_executor(POOL, solve, inp, P, self.plan or prev, "optimal")
                else:
                    plan = quick
                self.last_plan_ms["optimal"] = round((time.perf_counter() - t0) * 1000, 1)
                cf = validate_plan(inp, plan, P, self.cfg.conflicts)
                if not any(c["type"] in HARD for c in cf):
                    self._adopt(plan, inp, label=f"replan:{reason}")
                METRICS.observe("railtwin_replan_seconds", time.perf_counter() - t_req, stage="optimal")
            self.block_until = time.time() + 2.0
            LOG.info("replan", extra={"station": self.sid, "event": "replan", "reason": reason, "ms": round((time.perf_counter() - t_req) * 1000)})
        except Exception:                                                              # pragma: no cover
            LOG.exception("replan failed")
        finally:
            self.replanning = False
            if self.pending:
                p, self.pending = self.pending, None
                self.request_replan(p["reason"], p["alts"], p["disruptions"][-1] if p["disruptions"] else None)

    # ------------------------------------------------------------------------------------------------------------
    async def _alternatives(self, inp: PlanInput, P: Params, prev: Optional[Plan], disruptions: list, t_req: float):
        loop = asyncio.get_event_loop()
        c = self.cfg
        kinds = {d["kind"] for d in disruptions}
        stale = validate_plan(inp, prev, P, c.conflicts) if prev else []
        stale_hard = [x for x in stale if x["type"] in HARD or (x["severity"] == "crit" and x["type"] not in ("LATE_DEPARTURE", "ARRIVAL_WAIT"))]
        # 1) «ничего не делать»: FIFO, прежние пути — как поведут себя операторы без перепланирования
        base = solve_heuristic(inp, P, prev, "baseline", fifo=True, force=True)
        base_kpi = plan_kpis(inp, base, P, conflict_weight(stale))
        base_idx = compute_index(c.index, base_kpi)
        base_info = {"label": "baseline", "index": _idx_short(base_idx), "kpi": _r(base_kpi), "summary": plan_summary(base, P),
                     "conflicts": len(stale_hard), "ms": 0}
        # 2) быстрый эвристический вариант — первый ответ интерфейсу
        q = solve_heuristic(inp, P, prev, "quick")
        quick_info = self._alt_info("quick", q, inp, P, prev, base_idx)
        t_quick = (time.perf_counter() - t_req) * 1000
        alt_set = f"A{int(time.time() * 1000) % 10_000_000}"
        self.alt_plans = {f"{alt_set}-quick": q}
        quick_info["id"] = f"{alt_set}-quick"
        self.alts = {"type": "alternatives", "st": self.sid, "set": alt_set, "stage": "quick", "disruptions": disruptions, "baseline": base_info,
                     "alts": [quick_info], "t_quick_ms": round(t_quick, 1), "sim": round(self.state.now, 1), "applied": None}
        self.broadcast(self.alts)
        METRICS.observe("railtwin_replan_seconds", time.perf_counter() - t_req, stage="alt_quick")
        # 3) варианты CP-SAT — параллельно
        variants = [("min_delay", inp, P, prev),
                    ("min_change", inp, replace(P, w_change=60.0, w_wait=0.3), prev),
                    ("priority_first", inp, replace(P, pw={k: v ** 1.6 for k, v in P.pw.items()}, w_wait=0.3, w_change=1.0), prev),
                    ("min_queue", inp, replace(P, w_wait=3.0, w_late=0.35, w_change=1.0), prev),
                    ("passenger_first", inp, replace(P, pw={1: 1, 2: 1, 3: 1, 4: 6, 5: 60}, w_wait=0.2, w_change=1.0), prev)]
        if kinds & RESOURCE_KINDS:
            inp2 = _with_reserve(inp, kinds)
            variants.append(("reserve_resource", inp2, P, prev))
        eng = "cpsat" if c.planner.engine == "cpsat" else "heuristic"
        plans = await loop.run_in_executor(POOL, solve_parallel, variants, eng)
        alts = []
        for (label, vinp, _, _), plan in zip(variants, plans):
            info = self._alt_info(label, plan, vinp, P, prev, base_idx)
            info["id"] = f"{alt_set}-{label}"
            self.alt_plans[info["id"]] = plan
            alts.append(info)
        # быстрый эвристический вариант тоже предлагаем (компромисс «скорость / качество»), если он отличается; дубли по сути убираем
        alts.insert(0, {**quick_info, "id": f"{alt_set}-quick"})
        uniq, seen = [], set()
        plans_by_id = {f"{alt_set}-quick": q, **{a_["id"]: self.alt_plans[a_["id"]] for a_ in alts[1:]}}
        for a_ in alts:
            pl = plans_by_id[a_["id"]]
            sig = tuple(sorted((k, it.track, it.arr, it.dep) for k, it in pl.items.items()))
            if sig in seen:
                continue
            seen.add(sig)
            uniq.append(a_)
        best = max(uniq, key=lambda a: (a["index"]["score"], -a["changed_tracks"], -a["ms"]))
        for a in uniq:
            a["recommended"] = a is best
        self.alts = {"type": "alternatives", "st": self.sid, "set": alt_set, "stage": "final", "disruptions": disruptions, "baseline": base_info, "alts": uniq,
                     "t_quick_ms": round(t_quick, 1), "t_total_ms": round((time.perf_counter() - t_req) * 1000, 1), "sim": round(self.state.now, 1), "applied": None}
        self.broadcast(self.alts)
        METRICS.observe("railtwin_replan_seconds", time.perf_counter() - t_req, stage="alt_final")
        self.store.event(self.sid, time.time(), self.state.now, "ALTERNATIVES", {"set": alt_set, "n": len(uniq), "ms": self.alts["t_total_ms"], "best": best["label"],
                                                                                  "best_index": best["index"]["score"], "base_index": base_idx["score"]})
        if self.auto_apply:
            self.apply_alternative(best["id"], auto=True)

    def _alt_info(self, label, plan, inp, P, prev, base_idx):
        cf = validate_plan(inp, plan, P, self.cfg.conflicts)
        hard = [x for x in cf if x["type"] in HARD or (x["severity"] == "crit" and x["type"] not in ("LATE_DEPARTURE", "ARRIVAL_WAIT"))]
        kp = plan_kpis(inp, plan, P, conflict_weight(cf))
        idx = compute_index(self.cfg.index, kp)
        return {"label": label, "index": _idx_short(idx), "delta_index": round(idx["score"] - base_idx["score"], 1), "kpi": _r(kp),
                "summary": plan_summary(plan, P), "conflicts": len(hard), "changed_tracks": diff_changes(plan, prev), "ms": plan.solver.get("ms", 0),
                "solver": plan.solver, "plan": plan.to_dict(), "recommended": False}

    def resolve(self, code: str, params: dict) -> dict:
        """Применить вариант разрешения конфликта, предложенный системой."""
        now = self.state.now
        tid = params.get("train")
        if code == "MOVE_TRACK" and tid:
            self.pins.setdefault(tid, {})["track"] = int(params["track"])
        elif code == "DELAY_ARRIVAL" and tid:
            self.pins.setdefault(tid, {})["not_before"] = now + float(params.get("min", 5)) + max(0.0, (self.state.trains[tid].eta - now) if tid in self.state.trains else 0.0)
        elif code == "PRIORITIZE_TRAIN" and tid:
            self.pins.setdefault(tid, {})["pr_boost"] = 1
        elif code == "RESERVE_RESOURCE":
            evs = self.sim.add_loco(f"РЕЗ-{len(self.state.locos) + 1}", now + float(params.get("min", 35))) if params.get("res") == "loco" else self.sim.end_absence_by(now + float(params.get("min", 35)))
            self.ingest.push(evs)
            self.ingest.flush(force=True)
        self.alert("info", "RESOLUTION", {"code": code, **{k: v for k, v in params.items() if k in ("train", "track", "min", "res")}})
        self.request_replan("resolution", alts=False)
        return {"ok": True, "code": code}

    def apply_alternative(self, alt_id: str, auto=False) -> bool:
        plan = self.alt_plans.get(alt_id)
        if plan is None:
            return False
        if alt_id.endswith("reserve_resource"):
            kinds = {d["kind"] for d in (self.alts or {}).get("disruptions", [])}
            if "loco_down" in kinds:
                self.ingest.push(self.sim.add_loco(f"РЕЗ-{len(self.state.locos) + 1}", self.state.now + 35))
            if "crew_absent" in kinds:
                self.ingest.push(self.sim.end_absence_by(self.state.now + 35))
            self.ingest.flush(force=True)
        inp = self.build_input()
        self._adopt(plan, inp, label=alt_id.split("-", 1)[-1])
        if self.alts:
            self.alts["applied"] = alt_id
            self.broadcast({"type": "alternatives_applied", "st": self.sid, "id": alt_id, "auto": auto})
        self.alert("info", "PLAN_APPLIED", {"alt": alt_id.split("-", 1)[-1], "auto": auto})
        self.update_derived()
        return True

    # ------------------------------------------------------------------------------------------------------------
    def inject(self, kind: str, params: dict, source="api") -> dict:
        t0 = time.perf_counter()
        info = self.sim.inject(kind, params or {})
        evs = info.pop("events", []) if "events" in info else []
        if "error" in info:
            return info
        self.ingest.push(evs)
        self.ingest.flush(force=True)
        self.disruptions.append({**info, "wall": time.time(), "source": source})
        self.alert("warn", "DISRUPTION", {k: v for k, v in info.items() if k != "events"})
        METRICS.inc("railtwin_disruptions_total", kind=kind, station=self.sid)
        inp, P = self.update_derived()
        self.publish_conflicts(inp, P)
        self.request_replan("disruption", alts=True, disruption={"kind": kind, **{k: v for k, v in info.items() if k not in ("kind", "events")}})
        info["accepted_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self.broadcast(self.build_frame())
        return info

    def stress(self, multiplier: int) -> List[dict]:
        """Одновременные нештатные ситуации (x5…x10): разные типы, одна волна перепланирования."""
        kinds = ["train_delay", "track_close", "loco_down", "crew_absent", "train_delay", "switch_fail", "burst", "power_fail", "train_delay", "track_close"]
        out = []
        rec = self.infra.reception
        for i in range(max(1, min(multiplier, 20))):
            k = kinds[i % len(kinds)]
            p: dict = {}
            if k in ("track_close", "switch_fail"):
                p = {"track": rec[(i * 2 + 1) % len(rec)], "minutes": 45 + 10 * (i % 4)}
            elif k == "train_delay":
                p = {"min": 12 + (i * 7) % 30}
            elif k == "burst":
                p = {"n": 3}
            elif k == "loco_down":
                p = {"minutes": 70}
            elif k == "crew_absent":
                p = {"n": 1, "minutes": 90}
            elif k == "power_fail":
                p = {"minutes": 40, "extra": 2}
            info = self.sim.inject(k, p)
            if "error" in info:
                continue
            evs = info.pop("events", [])
            self.ingest.push(evs)
            self.disruptions.append({**info, "wall": time.time(), "source": "stress"})
            self.alert("warn", "DISRUPTION", {k2: v for k2, v in info.items() if k2 != "events"})
            METRICS.inc("railtwin_disruptions_total", kind=k, station=self.sid)
            out.append({k2: v for k2, v in info.items() if k2 != "events"})
        self.ingest.flush(force=True)
        inp, P = self.update_derived()
        self.publish_conflicts(inp, P)
        self.request_replan("stress", alts=True, disruption={"kind": "stress", "n": len(out)})
        self.broadcast(self.build_frame())
        return out

    # ------------------------------------------------------------------------------------------------------------
    def build_frame(self) -> dict:
        st = self.state
        occ = st.occupancy()
        closed = st.closed_now()
        sort_use = {t.sort_track: t.id for t in st.trains.values() if t.sort_track is not None and t.op_running}
        tracks = []
        for idx, tk in self.infra.tracks.items():
            state = "closed" if idx in closed else "occupied" if idx in occ else "sort_busy" if idx in sort_use else "free"
            tracks.append({"idx": idx, "s": state, "train": occ.get(idx) or sort_use.get(idx)})
        trains = [t.to_frame() for t in st.trains.values() if t.phase != "scheduled" or t.eta < st.now + 120]
        absent = sum(n for a, b, n in st.crew_absent if a <= st.now < b)
        busy_c = sum(1 for t in st.trains.values() if t.op_running and t.op_running.get("kind") in ("ROSPUSK", "PODFORM"))
        self.seq += 1
        n_conf = {"crit": sum(1 for c in self.conflicts if c["severity"] == "crit"), "warn": sum(1 for c in self.conflicts if c["severity"] == "warn"),
                  "info": sum(1 for c in self.conflicts if c["severity"] == "info")}
        lat = self.ingest.stats()
        return {"type": "state", "st": self.sid, "seq": self.seq, "wall": round(time.time(), 3), "sim": round(st.now, 2), "scale": self.scale, "paused": self.paused,
                "trains": trains, "locos": [{"id": k, "s": v["state"], "tr": v["track"], "until": v["until"]} for k, v in st.locos.items()],
                "crews": {"total": st.crews_total, "absent": absent, "busy": busy_c},
                "tracks": tracks, "closures": [{"track": c["track"], "from": c["from"], "to": c["to"], "reason": c["reason"]} for c in st.closures if c["to"] > st.now],
                "env": {"route_extra": st.route_extra if st.now < st.route_extra_until else 0},
                "index": {"score": self.index["score"], "cat": self.index["category"], "letter": self.index["letter"], "top": self.index["top"], "factors": self.index["factors"]},
                "conflicts": n_conf, "plan_v": self.plan_version, "replanning": self.replanning,
                "ingest": {"recv": lat.get("received", 0), "applied": lat.get("applied", 0), "inv": lat.get("invalid", 0), "dup": lat.get("duplicates", 0),
                           "stale": lat.get("stale", 0), "lat_ms": lat.get("latency_ms_avg", 0), "p95": lat.get("latency_ms_p95", 0)},
                "tick_ms": round(self.tick_ms, 1)}

    def broadcast(self, msg: dict):
        s = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
        self.events_out += 1
        for c in list(self.clients):
            c.put(s)

    # ------------------------------------------------------------------------------------------------------------
    async def run(self):
        c = self.cfg
        LOG.info("station runtime started", extra={"station": self.sid})
        while True:
            hz = max(1.0, self.cfg.sim.tick_hz)
            interval = 1.0 / hz
            t0 = time.perf_counter()
            if not self.clients and time.time() - self.last_client > 90:
                LOG.info("station runtime idle -> stop", extra={"station": self.sid})
                return
            if not self.paused:
                self.ingest_phase(self.scale / hz)
                await asyncio.sleep(self.cfg.ingest.reorder_window_ms / 1000.0)      # окно переупорядочивания
                self.publish_phase()
            self.tick_ms = (time.perf_counter() - t0) * 1000
            METRICS.observe("railtwin_tick_seconds", self.tick_ms / 1000, station=self.sid)
            want = max(0.0, interval - (time.perf_counter() - t0))
            t1 = time.perf_counter()
            await asyncio.sleep(want)
            lag = (time.perf_counter() - t1) - want                    # насколько цикл событий опоздал с пробуждением
            METRICS.set("railtwin_loop_lag_seconds", round(lag, 4), station=self.sid)
            if lag > 0.25 or self.tick_ms > 250:
                LOG.warning("slow tick", extra={"station": self.sid, "event": "slow_tick", "ms": round(self.tick_ms), "reason": f"lag={lag:.2f}s phases={self.phase_ms}"})

    def tick(self, dt_sim: float):
        self.ingest_phase(dt_sim)
        self.publish_phase(force=True)

    def ingest_phase(self, dt_sim: float):
        self._tp = time.perf_counter()
        c = self.cfg
        evs = self.sim.step(dt_sim)
        evs = add_noise(evs, c.sim.noise, self.rng, self.carry)
        self.ingest.push(evs)

    def publish_phase(self, force=False):
        c = self.cfg
        tp = time.perf_counter()
        self.phase_ms = {"sim": round((tp - getattr(self, "_tp", tp)) * 1000)}
        self.ingest.flush(force=force)
        self.state.prune_closures()
        stale = self.state.mark_stale(time.time(), c.ingest.stale_after_s)
        self.phase_ms["ingest"] = round((time.perf_counter() - tp) * 1000)
        inp, P = self.update_derived()
        self.phase_ms["derive"] = round((time.perf_counter() - tp) * 1000)
        self.publish_conflicts(inp, P)
        self.phase_ms["conf"] = round((time.perf_counter() - tp) * 1000)
        if not self.replanning and time.time() >= self.block_until:
            why = self.need_replan()
            if why:
                self.request_replan(why)
        frame = self.build_frame()
        self.tick_no += 1
        s = json.dumps(frame, separators=(",", ":"), ensure_ascii=False)
        for cl in list(self.clients):
            cl.put(s)
        self.frames.append(frame)
        METRICS.set("railtwin_index", self.index["score"], station=self.sid)
        METRICS.set("railtwin_conflicts", len(self.conflicts), station=self.sid)
        METRICS.set("railtwin_stale_trains", stale, station=self.sid)
        now = time.time()
        if now - self.last_snapshot >= c.storage.history_interval_s:
            self.last_snapshot = now
            self.store.snapshot(self.sid, now, self.state.now, self.index["score"], {k: frame[k] for k in ("sim", "trains", "locos", "crews", "tracks", "index", "conflicts", "plan_v")})

    # ------------------------------------------------------------------------------------------------------------
    def hello(self, backfill: int = 450) -> List[str]:
        """Начальный набор сообщений новому подписчику: инфраструктура, конфигурация индекса, план, конфликты, альтернативы, история кадров."""
        c = self.cfg
        out = [{"type": "hello", "st": self.sid, "infra": self.infra.to_dict(), "index_formula": formula_doc(c.index),
                "planner": {"engine": c.planner.engine, "time_limit_s": c.planner.time_limit_s, "auto_apply": self.auto_apply},
                "sim": {"tick_hz": c.sim.tick_hz, "scale": self.scale}, "config_version": cfgmod.STORE.version},
               {"type": "plan", "st": self.sid, "plan": self.plan.to_dict() if self.plan else None, "sim": round(self.state.now, 1)},
               {"type": "conflicts", "st": self.sid, "list": [{**x, "resolutions": []} for x in self.conflicts[:30]], "sim": round(self.state.now, 1)}]
        if self.alts:
            out.append(self.alts)
        out.append({"type": "alerts", "st": self.sid, "list": list(self.alerts)[-40:]})
        frames = list(self.frames)
        step = max(1, len(frames) // max(1, backfill))
        out.append({"type": "backfill", "st": self.sid, "frames": frames[::step][-backfill:]})
        return [json.dumps(m, separators=(",", ":"), ensure_ascii=False) for m in out]

    def replay(self, seconds: int, step: int = 1) -> List[dict]:
        cut = time.time() - seconds
        fr = [f for f in self.frames if f["wall"] >= cut]
        return fr[::max(1, step)]


# ----------------------------------------------------------------------------------------------------------------------
def _r(d):
    return {k: round(v, 3) for k, v in d.items()}


def _idx_short(i):
    return {"score": i["score"], "cat": i["category"], "letter": i["letter"], "top": i["top"]}


def _with_reserve(inp: PlanInput, kinds) -> PlanInput:
    """Вариант «резервный ресурс»: добавляется резервный локомотив / бригада через 35 мин."""
    locos = dict(inp.locos)
    crew_abs = list(inp.crew_absent)
    if "loco_down" in kinds:
        locos[f"РЕЗ-{len(locos) + 1}"] = inp.now + 35
    if "crew_absent" in kinds:
        crew_abs = [(a, min(b, inp.now + 35), n) for a, b, n in crew_abs]
    return replace(inp, locos=locos, crew_absent=crew_abs)


class Hub:
    """Реестр рантаймов станций: запускает по первому подписчику, останавливает после простоя."""

    def __init__(self):
        self.store: Optional[Store] = None
        self.rt: Dict[str, StationRuntime] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()

    def start(self):
        c = cfgmod.get()
        import os
        self.store = Store(os.environ.get("RAILTWIN_DB_PATH", c.storage.db_path), c.storage.retention_h)
        cfgmod.STORE.on_change(self._on_config)

    def _on_config(self, c):
        for r in self.rt.values():
            r.on_config(c)

    async def get(self, sid: str) -> StationRuntime:
        async with self.lock:
            r = self.rt.get(sid)
            if r is None:
                loop = asyncio.get_event_loop()
                r = await loop.run_in_executor(None, self._create, sid)
                self.rt[sid] = r
            t = self.tasks.get(sid)
            if t is None or t.done():
                self.tasks[sid] = asyncio.get_event_loop().create_task(r.run())
            r.last_client = time.time()
            return r

    def _create(self, sid):
        r = StationRuntime(sid, self.store)
        r.bootstrap()
        return r

    async def stop(self):
        for t in self.tasks.values():
            t.cancel()
        if self.store:
            self.store.flush()
            self.store.close()


HUB = Hub()
