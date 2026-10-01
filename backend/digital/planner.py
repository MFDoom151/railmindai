"""ИИ-планирование работы станции.

Постановка (constraint-based scheduling): для каждого поезда выбираем путь, время приёма/отправления, локомотив для каждой
манёвровой операции и сортировочный путь так, чтобы
  • пути, горловины (входная L — приём и манёвры, выходная R — отправление), локомотивы и сортировочные пути не пересекались во времени;
  • суммарная занятость бригад не превышала число бригад (кумулятивное ограничение), окна ТО/закрытия путей и недоступность
    локомотивов/бригад соблюдались; пассажирские — только на платформенных путях; длина состава ≤ длины пути;
  • минимизировать взвешенное (по приоритету) опоздание отправления и ожидание приёма на подходе + штраф за смену решений
    (устойчивость плана).
Движок: OR-Tools CP-SAT (оптимальный/близкий к оптимальному план за ≤ time_limit_s) и быстрая эвристика списочного
расписания (десятки миллисекунд) — используется как мгновенный первый ответ и как запасной вариант.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from .infra import Infra
from .models import LOCO_OPS, Plan, PlanItem, PlanOp

try:                                              # OR-Tools необязателен: без него работает эвристика
    from ortools.sat.python import cp_model
    HAVE_CPSAT = True
except Exception:                                 # pragma: no cover
    HAVE_CPSAT = False


@dataclass
class TrainJob:
    id: str
    type: str
    pr: int
    wagons: int
    eta: int                                      # прогнозное прибытие (абс. минуты)
    sched_arr: int
    sched_dep: int
    ops: List[Tuple[str, int]] = field(default_factory=list)      # оставшиеся операции (вид, длительность)
    dwell_min: int = 0
    phase: str = "scheduled"
    track: Optional[int] = None                   # занятый путь (если уже на станции)
    op_running: Optional[Tuple[str, int, Optional[str]]] = None   # (вид, конец абс., локомотив)
    transit: bool = False
    pin_track: Optional[int] = None                # закреплено диспетчером

    @property
    def occupying(self):
        return self.phase in ("entering", "on_track", "processing", "ready") and self.track is not None


@dataclass
class PlanInput:
    now: int
    infra: Infra
    jobs: List[TrainJob]
    locos: Dict[str, int]                         # id -> доступен с (абс. минуты)
    crews_total: int
    crew_absent: List[Tuple[int, int, int]] = field(default_factory=list)   # (с, по, сколько)
    closures: List[Tuple[int, int, int]] = field(default_factory=list)      # (путь, с, по)
    route_extra: int = 0
    horizon: int = 480


@dataclass
class Params:
    horizon: int = 480
    headway: int = 2
    route_in: int = 3
    route_out: int = 3
    shunt: int = 4
    readiness: int = 5
    w_late: float = 1.0
    w_wait: float = 0.6
    w_change: float = 3.0
    pw: Dict[int, float] = field(default_factory=lambda: {1: 1, 2: 2, 3: 4, 4: 7, 5: 12})
    freight_on_platform: bool = False
    time_limit: float = 1.5
    workers: int = 4
    seed: int = 1

    @staticmethod
    def from_config(pc, **over) -> "Params":
        p = Params(pc.horizon_min, pc.headway_min, pc.route_in_min, pc.route_out_min, pc.shunt_move_min, pc.readiness_min,
                   pc.w_late, pc.w_wait, pc.w_change, dict(pc.priority_weights), pc.freight_on_platform, pc.time_limit_s, pc.workers)
        return replace(p, **over) if over else p


# ----------------------------------------------------------------------------------------------------------------------
def allowed_tracks(job: TrainJob, infra: Infra, P: Params) -> List[int]:
    if job.occupying:
        return [job.track]
    tr = infra.tracks
    if job.pin_track in tr:
        return [job.pin_track]
    if job.type == "passenger":
        ids = infra.platforms or infra.reception
    else:
        ids = [t for t in infra.reception if not tr[t].platform or P.freight_on_platform] or infra.reception
    fit = [t for t in ids if tr[t].cap_wagons >= job.wagons]
    return fit or ids


def _loco_ops(job: TrainJob):
    return [(i, k, d) for i, (k, d) in enumerate(job.ops) if k in LOCO_OPS]


# ======================================================================================================================
#  CP-SAT
# ======================================================================================================================
def solve_cpsat(inp: PlanInput, P: Params, prev: Optional[Plan] = None, label: str = "") -> Plan:
    if not HAVE_CPSAT:
        return solve_heuristic(inp, P, prev, label)
    t0 = time.perf_counter()
    m = cp_model.CpModel()
    now, H, infra = inp.now, max(P.horizon, 120), inp.infra
    rin, rout, mv = P.route_in + inp.route_extra, P.route_out + inp.route_extra, P.shunt + inp.route_extra
    track_iv = {t: [] for t in infra.tracks}
    loco_iv = {k: [] for k in inp.locos}
    sort_iv = {u: [] for u in infra.sorting}
    L_iv, R_iv, crew_iv, crew_dem = [], [], [], []
    obj, hints = [], []

    def fixed(a, b):
        a, b = max(0, int(a)), min(H + 200, int(b))
        return m.NewIntervalVar(a, max(1, b - a), a + max(1, b - a), "fx") if b > a else None

    occ_tracks = {j.track for j in inp.jobs if j.occupying}
    deferred = []                                                 # закрытия, начавшиеся под стоящим составом
    for tr, s, e in inp.closures:
        if tr in track_iv and e > now:
            if tr in occ_tracks and s <= now + 1:
                deferred.append((tr, s, e))
                continue
            iv = fixed(s - now, e - now)
            if iv is not None:
                track_iv[tr].append(iv)
    occ_dep = {}
    for k, av in inp.locos.items():
        if av > now:
            iv = fixed(0, av - now)
            if iv is not None:
                loco_iv[k].append(iv)
    for (s, e, n) in inp.crew_absent:
        if e > now and n > 0:
            iv = fixed(s - now, e - now)
            if iv is not None:
                crew_iv.append(iv)
                crew_dem.append(int(n))

    var = {}
    for job in inp.jobs:
        eta = max(0, job.eta - now)
        sdep = max(0, job.sched_dep - now)
        wgt = int(round(P.pw.get(job.pr, 2)))
        if job.transit:                                        # пропуск по главному пути — фиксированные интервалы в горловинах
            a = max(0, job.sched_arr - now)
            for lst, d, st in ((L_iv, rin, a), (R_iv, rout, a + 3)):
                iv = fixed(st, st + d)
                if iv is not None:
                    lst.append(iv)
            var[job.id] = ("transit", a)
            continue

        occ = job.occupying
        if occ:
            arr = m.NewConstant(0)
        else:
            arr = m.NewIntVar(eta, H, f"arr_{job.id}")
            iv = m.NewIntervalVar(arr, rin, arr + rin, f"in_{job.id}")
            L_iv.append(iv)
        cursor = m.NewConstant(0) if occ else arr + rin
        # --- операции ---
        op_vars = []
        first_s = last_e = None
        for i, (kind, dur) in enumerate(job.ops):
            running = i == 0 and job.op_running is not None
            if running:
                s = m.NewConstant(0)
                dur = max(1, job.op_running[1] - now)
            else:
                s = m.NewIntVar(0, H, f"s_{job.id}_{i}")
                m.Add(s >= cursor)
            e = s + dur
            if kind in LOCO_OPS:
                if first_s is None:
                    first_s = s
                last_e = e
                ys = []
                for k in inp.locos:
                    if running and job.op_running[2] is not None and k != job.op_running[2]:
                        continue
                    y = m.NewBoolVar(f"y_{job.id}_{i}_{k}")
                    ys.append((k, y))
                    loco_iv[k].append(m.NewOptionalIntervalVar(s, dur, e, y, f"l_{job.id}_{i}_{k}"))
                if not ys:
                    ys = [(None, m.NewConstant(1))]
                else:
                    m.AddExactlyOne([y for _, y in ys])
                crew_iv.append(m.NewIntervalVar(s, dur, e, f"c_{job.id}_{i}"))
                crew_dem.append(1)
                if not running:
                    L_iv.append(m.NewIntervalVar(s, mv, s + mv, f"mv_{job.id}_{i}"))
                op_vars.append((kind, s, dur, ys))
            else:
                op_vars.append((kind, s, dur, []))
            cursor = e
        dep = m.NewIntVar(max(sdep, 1), H + 120, f"dep_{job.id}")
        if job.ops:
            m.Add(dep >= cursor + P.readiness)
        else:
            m.Add(dep >= (cursor if occ else arr + rin) + max(job.dwell_min, 1))
        R_iv.append(m.NewIntervalVar(dep, rout, dep + rout, f"out_{job.id}"))
        if occ:
            occ_dep[job.track] = dep

        # --- путь ---
        xs = {}
        for t in allowed_tracks(job, infra, P):
            x = m.NewBoolVar(f"x_{job.id}_{t}")
            xs[t] = x
            end = m.NewIntVar(0, H + 130, f"end_{job.id}_{t}")
            m.Add(end == dep + P.headway)
            size = m.NewIntVar(1, H + 130, f"sz_{job.id}_{t}")
            m.Add(size == end - arr)
            track_iv[t].append(m.NewOptionalIntervalVar(arr, size, end, x, f"o_{job.id}_{t}"))
        m.AddExactlyOne(list(xs.values()))
        # --- сортировочный путь на время манёвров ---
        zs = {}
        if first_s is not None and infra.sorting:
            span = m.NewIntVar(1, H + 130, f"span_{job.id}")
            m.Add(span == last_e - first_s)
            for u in infra.sorting:
                z = m.NewBoolVar(f"z_{job.id}_{u}")
                zs[u] = z
                sort_iv[u].append(m.NewOptionalIntervalVar(first_s, span, last_e, z, f"u_{job.id}_{u}"))
            m.AddExactlyOne(list(zs.values()))

        # --- цель ---
        late = m.NewIntVar(0, H + 120, f"late_{job.id}")
        m.Add(late == dep - sdep)
        wait = m.NewIntVar(0, H, f"wait_{job.id}")
        m.Add(wait == arr - eta)
        obj.append(int(100 * P.w_late * wgt) * late)
        obj.append(int(100 * P.w_wait * wgt) * wait)
        obj.append(1 * dep)                                       # компактность
        pit = prev.items.get(job.id) if prev else None
        if pit is not None and not occ and pit.track in xs and P.w_change > 0:
            ch = m.NewBoolVar(f"ch_{job.id}")
            m.Add(ch + xs[pit.track] == 1)
            obj.append(int(100 * P.w_change) * ch)
        if pit is not None and not pit.transit:
            hints += [(arr, max(eta, pit.arr - now)), (dep, max(sdep, pit.dep - now))]
            for t, x in xs.items():
                hints.append((x, 1 if t == pit.track else 0))
        var[job.id] = ("train", arr, dep, xs, op_vars, zs, job)

    for tr, s, e in deferred:                                     # закрытие вступает в силу после ухода стоящего состава
        end_rel = max(1, e - now)
        x = m.NewIntVar(0, end_rel, f"cl_{tr}")
        m.AddMinEquality(x, [occ_dep[tr] + P.headway, end_rel]) if tr in occ_dep else m.Add(x == 0)
        sz = m.NewIntVar(0, end_rel, f"clsz_{tr}")
        m.Add(sz == end_rel - x)
        track_iv[tr].append(m.NewIntervalVar(x, sz, end_rel, f"cld_{tr}"))
    for t, ivs in track_iv.items():
        if len(ivs) > 1:
            m.AddNoOverlap(ivs)
    for ivs in loco_iv.values():
        if len(ivs) > 1:
            m.AddNoOverlap(ivs)
    for ivs in sort_iv.values():
        if len(ivs) > 1:
            m.AddNoOverlap(ivs)
    if len(L_iv) > 1:
        m.AddNoOverlap(L_iv)
    if len(R_iv) > 1:
        m.AddNoOverlap(R_iv)
    if crew_iv:
        m.AddCumulative(crew_iv, crew_dem, max(inp.crews_total, 0) if inp.crews_total > 0 else 0)
    m.Minimize(sum(obj))
    for v, val in hints:
        try:
            m.AddHint(v, val)
        except Exception:
            pass

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = P.time_limit
    solver.parameters.num_workers = P.workers
    solver.parameters.random_seed = P.seed
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        plan = solve_heuristic(inp, P, prev, label)
        plan.solver.update({"fallback": True, "cpsat_status": solver.StatusName(status)})
        return plan

    items: Dict[str, PlanItem] = {}
    for jid, v in var.items():
        if v[0] == "transit":
            a = v[1] + now
            items[jid] = PlanItem(jid, None, a, a + 3, a, a + 3, [], True)
            continue
        _, arr, dep, xs, op_vars, zs, job = v
        a, d = solver.Value(arr) + now, solver.Value(dep) + now
        track = next((t for t, x in xs.items() if solver.Value(x)), None)
        sort_t = next((u for u, z in zs.items() if solver.Value(z)), None)
        ops = []
        for kind, s, dur, ys in op_vars:
            loco = next((k for k, y in ys if k is not None and solver.Value(y)), None)
            st = solver.Value(s) + now
            ops.append(PlanOp(kind, st, st + dur, loco, sort_t if kind in LOCO_OPS else None))
        items[jid] = PlanItem(jid, track, a, d, job.eta, job.sched_dep, ops)
    ms = (time.perf_counter() - t0) * 1000
    plan = Plan(0, now, items, {"engine": "cpsat", "status": solver.StatusName(status), "ms": round(ms, 1), "objective": solver.ObjectiveValue() / 100,
                                "bound": solver.BestObjectiveBound() / 100, "workers": P.workers, "wall_s": round(solver.WallTime(), 3)}, label)
    return plan


# ======================================================================================================================
#  Эвристика списочного расписания
# ======================================================================================================================
class _Book:
    def __init__(self, inp: PlanInput, P: Params):
        self.inp, self.P = inp, P
        self.track = {t: [] for t in inp.infra.tracks}
        self.sort = {u: [] for u in inp.infra.sorting}
        self.loco = {k: [] for k in inp.locos}
        self.L, self.R, self.crew = [], [], []
        self.deferred = []
        occ = {j.track for j in inp.jobs if j.occupying}
        for tr, s, e in inp.closures:
            if tr in self.track:
                if tr in occ and s <= inp.now:
                    self.deferred.append((tr, s, e))
                else:
                    self.track[tr].append((s, e))
        for k, av in inp.locos.items():
            if av > inp.now:
                self.loco[k].append((inp.now, av))
        for s, e, n in inp.crew_absent:
            self.crew.append((s, e, n))

    @staticmethod
    def free(lst, a, b):
        return all(b <= s or a >= e for s, e in lst)

    def crew_ok(self, a, b):
        cap = self.inp.crews_total
        pts = sorted({a} | {s for s, e, n in self.crew if a < s < b})
        for t in pts:
            load = sum(n for s, e, n in self.crew if s <= t < e)
            if load + 1 > cap:
                return False
        return True


def solve_heuristic(inp: PlanInput, P: Params, prev: Optional[Plan] = None, label: str = "", fifo: bool = False, force: bool = False) -> Plan:
    t0 = time.perf_counter()
    now, infra = inp.now, inp.infra
    rin, rout, mv = P.route_in + inp.route_extra, P.route_out + inp.route_extra, P.shunt + inp.route_extra
    B = _Book(inp, P)
    items: Dict[str, PlanItem] = {}
    pw = P.pw
    # 1. фиксированные: транзит и поезда на станции
    for job in inp.jobs:
        if job.transit:
            a = max(now, job.sched_arr)
            B.L.append((a, a + rin))
            B.R.append((a + 3, a + 3 + rout))
            items[job.id] = PlanItem(job.id, None, a, a + 3, a, a + 3, [], True)
    order = [j for j in inp.jobs if not j.transit]
    occ_first = sorted([j for j in order if j.occupying], key=lambda j: (j.sched_dep, -j.pr))
    rest = sorted([j for j in order if not j.occupying], key=(lambda j: (j.eta, j.sched_dep)) if fifo else (lambda j: (j.eta + (-6 * j.pr), j.sched_dep)))
    for job in occ_first:
        items[job.id] = _place(job, inp, P, B, prev, rin, rout, mv, force)
    for tr, s, e in B.deferred:
        after = max([it.dep + P.headway for it in items.values() if it.track == tr] + [s])
        if after < e:
            B.track[tr].append((after, e))
    for job in rest:
        items[job.id] = _place(job, inp, P, B, prev, rin, rout, mv, force)
    plan = Plan(0, now, items, {"engine": "heuristic", "status": "FEASIBLE", "ms": round((time.perf_counter() - t0) * 1000, 1)}, label)
    return plan


def _ends(lists, lo):
    """Отсортированные моменты ≥ lo: сам lo и концы интервалов — только в них может освободиться ресурс."""
    pts = {lo}
    for lst in lists:
        for iv in lst:
            if iv[1] > lo:
                pts.add(iv[1])
    return sorted(pts)


def _place(job, inp, P, B: _Book, prev, rin, rout, mv, force=False) -> PlanItem:
    now = inp.now
    tracks = allowed_tracks(job, inp.infra, P)
    pit = prev.items.get(job.id) if prev else None
    if pit is not None and pit.track in tracks:
        tracks = [pit.track] if force else [pit.track] + [t for t in tracks if t != pit.track]
    occ = job.occupying
    a0 = now if occ else max(now, job.eta)
    min_occ = rin + sum(d for _, d in job.ops) + P.readiness + 1
    # кандидаты времени приёма: только моменты, когда освобождается горловина или путь
    cands = _ends([B.L] + [B.track[t] for t in tracks], a0) if not occ else [a0]
    for a in cands[:600]:
        if a > a0 + P.horizon + 300:
            break
        if not occ and not B.free(B.L, a, a + rin):
            continue
        if not occ and not any(B.free(B.track[t], a, a + min_occ) for t in tracks):
            continue
        ops = _ops(job, inp, P, B, a, rin, mv, occ)
        if ops is None:
            continue
        for t in tracks:
            res = _finish(job, inp, P, B, a, t, rout, occ, ops)
            if res is not None:
                return res
    a = a0 + P.horizon
    return PlanItem(job.id, tracks[0], a, a + 30, job.eta, job.sched_dep, [])


def _ops(job, inp, P, B: _Book, a, rin, mv, occ):
    """Расписание операций от момента приёма a (не зависит от выбора пути): локомотив, бригада, горловина L."""
    now = inp.now
    cursor = now if occ else a + rin
    ops_out, commits = [], []
    first_s = last_e = None
    for i, (kind, dur) in enumerate(job.ops):
        running = i == 0 and job.op_running is not None
        if running:
            s, dur = now, max(1, job.op_running[1] - now)
            loco = job.op_running[2]
            ops_out.append(PlanOp(kind, s, s + dur, loco))
            if kind in LOCO_OPS:
                first_s = s if first_s is None else first_s
                last_e = s + dur
                if loco in B.loco:
                    commits.append(("loco", loco, (s, s + dur)))
                commits.append(("crew", None, (s, s + dur, 1)))
            cursor = s + dur
            continue
        if kind in LOCO_OPS:
            lists = [B.L, [(0, e) for _, e, _ in B.crew]] + list(B.loco.values()) + [[iv for k, key, iv in commits if k in ("L", "loco")]]
            found = None
            for s in _ends(lists, cursor)[:400]:
                if s > cursor + P.horizon:
                    break
                if not B.free(B.L, s, s + mv) or not _pending_free(commits, "L", s, s + mv):
                    continue
                if not B.crew_ok(s, s + dur) or not _pending_crew(commits, B, s, s + dur):
                    continue
                loco = next((k for k in inp.locos if B.free(B.loco[k], s, s + dur) and _pending_free(commits, ("loco", k), s, s + dur)), None)
                if loco is None:
                    continue
                found = (s, loco)
                break
            if found is None:
                return None
            s, loco = found
            commits += [("L", None, (s, s + mv)), ("loco", loco, (s, s + dur)), ("crew", None, (s, s + dur, 1))]
            first_s = s if first_s is None else first_s
            last_e = s + dur
            ops_out.append(PlanOp(kind, s, s + dur, loco))
        else:
            s = cursor
            ops_out.append(PlanOp(kind, s, s + dur, None))
        cursor = s + dur
    return ops_out, commits, first_s, last_e, cursor


def _finish(job, inp, P, B: _Book, a, t, rout, occ, ops):
    now = inp.now
    ops_out, commits, first_s, last_e, cursor = ops
    commits = list(commits)
    rin = P.route_in + inp.route_extra
    sort_t = None
    if first_s is not None and inp.infra.sorting:
        sort_t = next((u for u in inp.infra.sorting if B.free(B.sort[u], first_s, last_e)), None)
        if sort_t is None:
            return None
        ops_out = [PlanOp(o.kind, o.start, o.end, o.loco, sort_t if o.kind in LOCO_OPS else None) for o in ops_out]
        commits.append(("sort", sort_t, (first_s, last_e)))
    if job.ops:
        d0 = max(job.sched_dep, cursor + P.readiness)
    else:
        d0 = max(job.sched_dep, (now if occ else a + rin) + max(job.dwell_min, 1))
    d = next((x for x in _ends([B.R], d0)[:300] if B.free(B.R, x, x + rout)), None)
    if d is None:
        return None
    occ_iv = (a if not occ else now, d + P.headway)
    if not B.free(B.track[t], *occ_iv):
        return None
    if not occ:
        B.L.append((a, a + rin))
    B.R.append((d, d + rout))
    B.track[t].append(occ_iv)
    for kind, key, iv in commits:
        if kind == "L":
            B.L.append(iv)
        elif kind == "loco" and key in B.loco:
            B.loco[key].append(iv)
        elif kind == "crew":
            B.crew.append((iv[0], iv[1], 1))
        elif kind == "sort":
            B.sort[key].append(iv)
    return PlanItem(job.id, t, a, d, job.eta, job.sched_dep, ops_out)


def _pending_free(commits, key, a, b):
    for kind, k, iv in commits:
        if (kind == "L" and key == "L") or (kind == "loco" and key == ("loco", k)):
            if not (b <= iv[0] or a >= iv[1]):
                return False
    return True


def _pending_crew(commits, B, a, b):
    extra = [(iv[0], iv[1], 1) for kind, _, iv in commits if kind == "crew"]
    cap = B.inp.crews_total
    pts = sorted({a} | {s for s, e, n in B.crew + extra if a < s < b})
    for t in pts:
        load = sum(n for s, e, n in B.crew + extra if s <= t < e)
        if load + 1 > cap:
            return False
    return True


# ======================================================================================================================
def solve(inp: PlanInput, P: Params, prev: Optional[Plan] = None, label: str = "", engine: Optional[str] = None) -> Plan:
    eng = engine or "cpsat"
    plan = solve_cpsat(inp, P, prev, label) if (eng == "cpsat" and HAVE_CPSAT) else solve_heuristic(inp, P, prev, label)
    return plan


def solve_parallel(variants: List[Tuple[str, PlanInput, Params, Optional[Plan]]], engine: Optional[str] = None) -> List[Plan]:
    """Решает несколько вариантов плана одновременно (CP-SAT освобождает GIL)."""
    import os
    share = max(1, (os.cpu_count() or 4) // max(1, len(variants)))            # делим ядра между вариантами
    with ThreadPoolExecutor(max_workers=max(1, len(variants))) as ex:
        futs = [ex.submit(solve, inp, replace(P, workers=max(1, min(P.workers, share))), prev, label, engine) for label, inp, P, prev in variants]
        return [f.result() for f in futs]
