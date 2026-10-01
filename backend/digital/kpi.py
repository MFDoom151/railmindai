"""KPI плана → значения факторов индекса эффективности (для прогноза индекса по варианту плана) и сводка для интерфейса."""
from __future__ import annotations

from typing import Dict, List

from .models import LOCO_OPS, Plan
from .planner import Params, PlanInput

WINDOW = 240       # окно прогноза, мин


def plan_kpis(inp: PlanInput, plan: Plan, P: Params, conflicts_w: float) -> Dict[str, float]:
    now = inp.now
    w0, w1 = now, now + WINDOW
    items = [it for it in plan.items.values() if not it.transit]
    pw = P.pw
    jobs = {j.id: j for j in inp.jobs}
    sched = [it for it in items if it.sched_dep <= w1]
    done = [it for it in sched if it.dep <= w1]
    thr = len(done) / len(sched) if sched else 1.0
    devs = []
    for it in items:
        if it.dep < w0 or it.sched_dep > w1 + 60:
            continue
        pr = jobs[it.train_id].pr if it.train_id in jobs else 2
        devs.append((it.late + 0.5 * it.wait_out, pw.get(pr, 2)))
    sw = sum(w for _, w in devs)
    avg_dev = sum(d * w for d, w in devs) / sw if sw else 0.0
    max_dev = max((d for d, _ in devs), default=0.0)

    def clip(a, b):
        return max(0, min(b, w1) - max(a, w0))

    occ = 0.0
    for it in items:
        if it.track is not None:
            occ += clip(max(it.arr, now), it.dep + P.headway)
    closed = sum(clip(max(s, w0), e) for tr, s, e in inp.closures if tr in inp.infra.tracks)
    n_tr = len(inp.infra.tracks)
    track_load = occ / max(1.0, n_tr * WINDOW - closed)
    # очередь на подходе (одновременно ожидающие приёма)
    pts = []
    for it in items:
        if it.arr > it.eta:
            pts += [(it.eta, 1), (it.arr, -1)]
    pts.sort()
    cur = peak = 0
    for t, d in pts:
        if w0 - 1 <= t <= w1:
            cur += d
            peak = max(peak, cur)
    busy = sum(clip(o.start, o.end) for it in items for o in it.ops if o.kind in LOCO_OPS)
    avail_l = sum(WINDOW - max(0, min(av, w1) - w0) for av in inp.locos.values()) or 1
    absent = sum(clip(s, e) * n for s, e, n in inp.crew_absent)
    avail_c = max(1.0, inp.crews_total * WINDOW - absent)
    return {"throughput": thr, "avg_dev": avg_dev, "max_dev": max_dev, "track_load": track_load, "conflicts": conflicts_w,
            "queue_outside": float(peak), "loco_idle": min(1.0, busy / avail_l), "crew_idle": min(1.0, busy / avail_c)}


def plan_summary(plan: Plan, P: Params) -> dict:
    items = [it for it in plan.items.values() if not it.transit]
    late = sum(it.late for it in items)
    wait = sum(it.wait_out for it in items)
    return {"trains": len(items), "late_sum": int(late), "wait_sum": int(wait), "late_max": int(max((it.late for it in items), default=0)),
            "delayed": sum(1 for it in items if it.late >= 5), "changed_tracks": 0}


def diff_changes(new: Plan, old: Plan | None) -> int:
    if not old:
        return 0
    n = 0
    for k, it in new.items.items():
        o = old.items.get(k)
        if o and not it.transit and o.track != it.track:
            n += 1
    return n
