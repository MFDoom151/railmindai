"""Обнаружение конфликтов плана и генерация вариантов их разрешения.

Проверяются: пересечение занятости путей, закрытые пути/окна ТО, конфликты маршрутов в горловинах (приём/манёвры — входная L,
отправление — выходная R), двойное назначение и недоступность локомотивов, нехватка бригад, занятость сортировочных путей,
соответствие пути типу и длине состава, опоздания и ожидание на подходе."""
from __future__ import annotations

from typing import Dict, List

from .models import LOCO_OPS, Plan
from .planner import Params, PlanInput, TrainJob

CRIT, WARN, INFO = "crit", "warn", "info"


def _ov(a0, a1, b0, b1):
    return max(0, min(a1, b1) - max(a0, b0))


def _c(ctype, sev, t0, t1, trains, track=None, **params):
    key = f"{ctype}:{','.join(sorted(map(str, trains)))}:{track}:{int(t0) // 5}"
    return {"id": key, "type": ctype, "severity": sev, "t_from": int(t0), "t_to": int(t1), "trains": sorted(map(str, trains)), "track": track, "params": params}


def validate_plan(inp: PlanInput, plan: Plan, P: Params, cc) -> List[dict]:
    out: List[dict] = []
    infra = inp.infra
    rin, rout, mv = P.route_in + inp.route_extra, P.route_out + inp.route_extra, P.shunt + inp.route_extra
    jobs = {j.id: j for j in inp.jobs}
    items = [it for it in plan.items.values() if it.train_id in jobs]          # ушедшие поезда в плане игнорируем
    for j in inp.jobs:
        if j.id not in plan.items:
            out.append(_c("UNPLANNED", INFO, inp.now, j.eta, [j.id], None))
        elif not j.occupying and not j.transit and plan.items[j.id].arr < j.eta - 1:
            out.append(_c("ETA_SHIFT", INFO, plan.items[j.id].arr, j.eta, [j.id], None, minutes=int(j.eta - plan.items[j.id].arr)))

    # --- пути ---
    by_track: Dict[int, List] = {}
    for it in items:
        if it.track is None:
            continue
        a = inp.now if (jobs.get(it.train_id) and jobs[it.train_id].occupying) else it.arr
        by_track.setdefault(it.track, []).append((a, it.dep + P.headway, it))
    for t, lst in by_track.items():
        lst.sort(key=lambda x: x[0])
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                o = _ov(lst[i][0], lst[i][1], lst[j][0], lst[j][1])
                if o > 0:
                    out.append(_c("TRACK_OVERLAP", CRIT, max(lst[i][0], lst[j][0]), min(lst[i][1], lst[j][1]), [lst[i][2].train_id, lst[j][2].train_id], t, minutes=o))
        for a, b, it in lst:
            job0 = jobs.get(it.train_id)
            for tr, s, e in inp.closures:
                if job0 and job0.occupying and s <= inp.now:
                    continue                                     # состав уже на пути: закрытие вступит в силу после отправления
                if tr == t and _ov(a, b, s, e) > 0:
                    out.append(_c("TRACK_CLOSED", CRIT, max(a, s), min(b, e), [it.train_id], t, minutes=_ov(a, b, s, e)))
            job = jobs.get(it.train_id)
            tk = infra.tracks.get(t)
            if job and tk:
                if job.type == "passenger" and not tk.platform:
                    out.append(_c("PLATFORM_MISMATCH", WARN, a, b, [it.train_id], t))
                if job.type == "freight" and tk.platform and not P.freight_on_platform:
                    out.append(_c("PLATFORM_MISMATCH", WARN, a, b, [it.train_id], t))
                if job.wagons > tk.cap_wagons:
                    out.append(_c("TRACK_TOO_SHORT", WARN, a, b, [it.train_id], t, wagons=job.wagons, cap=tk.cap_wagons))

    # --- горловины ---
    L, R = [], []
    for it in items:
        job = jobs.get(it.train_id)
        if not (job and job.occupying):
            L.append((it.arr, it.arr + rin, it.train_id, "ARR"))
        R.append((it.dep if not it.transit else it.dep, (it.dep + rout), it.train_id, "DEP"))
        for o in it.ops:
            if o.kind in LOCO_OPS and not (job and job.op_running and o.start <= inp.now):
                L.append((o.start, o.start + mv, it.train_id, "SHUNT"))
    for name, lst in (("L", L), ("R", R)):
        lst.sort()
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j][0] >= lst[i][1]:
                    break
                if lst[i][2] != lst[j][2] or lst[i][3] != lst[j][3]:
                    out.append(_c("ROUTE_CONFLICT", CRIT, max(lst[i][0], lst[j][0]), min(lst[i][1], lst[j][1]), [lst[i][2], lst[j][2]], None, throat=name, kinds=f"{lst[i][3]}/{lst[j][3]}"))

    # --- локомотивы, бригады, сортировочные пути ---
    per_loco: Dict[str, List] = {}
    ops_all = []
    for it in items:
        for o in it.ops:
            if o.kind in LOCO_OPS:
                ops_all.append((o.start, o.end, it.train_id, o))
                if o.loco:
                    per_loco.setdefault(o.loco, []).append((o.start, o.end, it.train_id))
    for k, lst in per_loco.items():
        lst.sort()
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j][0] < lst[i][1]:
                    out.append(_c("LOCO_CONFLICT", CRIT, lst[j][0], min(lst[i][1], lst[j][1]), [lst[i][2], lst[j][2]], None, loco=k))
        av = inp.locos.get(k, 0)
        for s, e, tid in lst:
            if inp.now <= s < av:
                out.append(_c("LOCO_UNAVAILABLE", CRIT, s, min(e, av), [tid], None, loco=k))
    pts = sorted({s for s, e, _, _ in ops_all} | {a for a, b, n in [(x[0], x[1], x[2]) for x in inp.crew_absent]})
    for t in pts:
        load = sum(1 for s, e, _, _ in ops_all if s <= t < e) + sum(n for s, e, n in inp.crew_absent if s <= t < e)
        if load > inp.crews_total:
            trs = [tid for s, e, tid, _ in ops_all if s <= t < e]
            out.append(_c("CREW_SHORTAGE", CRIT, t, t + 1, trs, None, need=load, have=inp.crews_total))
            break
    spans: Dict[int, List] = {}
    for it in items:
        lo = [o for o in it.ops if o.kind in LOCO_OPS]
        if lo and lo[0].sort_track is not None:
            spans.setdefault(lo[0].sort_track, []).append((lo[0].start, lo[-1].end, it.train_id))
    for u, lst in spans.items():
        lst.sort()
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j][0] < lst[i][1]:
                    out.append(_c("TRACK_OVERLAP", CRIT, lst[j][0], min(lst[i][1], lst[j][1]), [lst[i][2], lst[j][2]], u, minutes=_ov(*lst[i][:2], *lst[j][:2]), sorting=True))

    # --- опоздания и ожидание ---
    for it in items:
        if it.transit:
            continue
        if it.late >= cc.late_crit_min:
            out.append(_c("LATE_DEPARTURE", CRIT, it.sched_dep, it.dep, [it.train_id], it.track, minutes=it.late))
        elif it.late >= cc.late_warn_min:
            out.append(_c("LATE_DEPARTURE", WARN, it.sched_dep, it.dep, [it.train_id], it.track, minutes=it.late))
        if it.wait_out >= 10:
            out.append(_c("ARRIVAL_WAIT", WARN, it.eta, it.arr, [it.train_id], it.track, minutes=it.wait_out))
    seen, uniq = set(), []
    for c in out:
        if c["id"] not in seen:
            seen.add(c["id"])
            uniq.append(c)
    uniq.sort(key=lambda c: ({"crit": 0, "warn": 1, "info": 2}[c["severity"]], c["t_from"]))
    return uniq


def conflict_weight(conflicts: List[dict]) -> float:
    """Взвешенное число конфликтов для индекса. Опоздания и ожидание учитываются отдельными факторами — здесь не дублируются."""
    w = 0.0
    for c in conflicts:
        if c["type"] in ("LATE_DEPARTURE", "ARRIVAL_WAIT", "UNPLANNED", "ETA_SHIFT"):
            continue
        w += 1.0 if c["severity"] == CRIT else 0.4 if c["severity"] == WARN else 0.1
    return w


# ----------------------------------------------------------------------------------------------------------------------
def suggest_resolutions(c: dict, inp: PlanInput, plan: Plan, P: Params) -> List[dict]:
    """До трёх вариантов разрешения конфликта с оценкой влияния (мин задержки) — быстрые «что-если» без полного пересчёта."""
    opts: List[dict] = []
    jobs = {j.id: j for j in inp.jobs}
    items = plan.items
    t = c["type"]
    victim = c["trains"][-1] if c["trains"] else None
    it = items.get(victim) if victim else None
    if t in ("TRACK_OVERLAP", "TRACK_CLOSED", "PLATFORM_MISMATCH", "TRACK_TOO_SHORT") and it and it.track is not None and not c["params"].get("sorting"):
        from .planner import allowed_tracks
        job = jobs.get(victim)
        a = it.arr if not (job and job.occupying) else inp.now
        for tr in allowed_tracks(job, inp.infra, P) if job else []:
            if tr == it.track:
                continue
            clash = [o for o in items.values() if o.track == tr and o.train_id != victim and _ov(a, it.dep + P.headway, o.arr, o.dep + P.headway) > 0]
            closed = any(x == tr and _ov(a, it.dep + P.headway, s, e) > 0 for x, s, e in inp.closures)
            if not clash and not closed and not (job and job.occupying):
                opts.append({"code": "MOVE_TRACK", "params": {"train": victim, "track": tr}, "delta_min": 0})
                break
        k = max(1, int(c["params"].get("minutes", 5)))
        opts.append({"code": "DELAY_ARRIVAL", "params": {"train": victim, "min": k}, "delta_min": k})
    if t == "ROUTE_CONFLICT":
        opts.append({"code": "SEQUENCE_ROUTES", "params": {"trains": ",".join(c["trains"]), "min": P.route_in}, "delta_min": P.route_in})
    if t in ("LOCO_CONFLICT", "LOCO_UNAVAILABLE", "CREW_SHORTAGE"):
        opts.append({"code": "RESERVE_RESOURCE", "params": {"res": "loco" if t != "CREW_SHORTAGE" else "crew", "min": 35}, "delta_min": 35})
        opts.append({"code": "SHIFT_OPERATION", "params": {"trains": ",".join(c["trains"]), "min": 10}, "delta_min": 10})
    if t in ("LATE_DEPARTURE", "ARRIVAL_WAIT"):
        opts.append({"code": "PRIORITIZE_TRAIN", "params": {"train": victim}, "delta_min": -int(c["params"].get("minutes", 5) * 0.5)})
    opts.append({"code": "REPLAN", "params": {}, "delta_min": None})
    return opts[:3] if len(opts) > 3 else opts
