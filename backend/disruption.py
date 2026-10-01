"""Disruption Management: пересчёт маневрового графика при нештатных ситуациях.
Для каждой ситуации строится эталонный (до сбоя) план, затем — «ничего не делать» и 2–3 альтернативы,
каждая решается тем же планировщиком. Время расчёта измеряется и возвращается клиенту."""
import copy
import time
import zlib

from optimizer import anneal, clean, fifo, gen_tasks, simulate

KINDS = ("switch_fail", "switch_icing", "false_occupancy", "train_delay", "crew_absent", "loco_fail", "track_full", "border_hold", "arrival_burst", "power_fail")
ITERS = 900
DWELL_W = 30.0


def blocked_tracks(layout, sw_id):
    """Пути, к которым нельзя проложить маршрут при отказе стрелки (остаётся в «+», не переводится в «−»)."""
    n = layout["n_tracks"]
    head = (sw_id - 1) // 2 if sw_id % 2 == 1 else (sw_id - 2) // 2
    if head == 0:
        return list(range(1, n + 1))       # входная стрелка: закрыта вся горловина с этой стороны
    return [head] if head <= n else []


def _window(tasks, now, n=10):
    fut = sorted([t for t in tasks if t["ready"] >= now], key=lambda t: t["ready"])
    if len(fut) < 6:
        fut = sorted(tasks, key=lambda t: t["ready"])
        now = max(0.0, fut[0]["ready"] - 1)
    win = copy.deepcopy(fut[:n])
    r0 = win[0]["ready"]
    for t in win:                       # «пиковое окно»: операции идут плотнее суточного среднего
        slack = t["deadline"] - t["ready"]
        t["ready"] = round(now + (t["ready"] - r0) * 0.3, 1)
        t["deadline"] = round(t["ready"] + slack, 1)
    return win, now


def _metrics(res, ref):
    return {
        "late_min": round(res["late_min"], 1), "dwell_wagon_h": round(res["dwell_wagon_h"], 1),
        "empty_km": round(res["empty_km"], 2), "makespan": round(res["makespan"], 1),
        "d_late_min": round(res["late_min"] - ref["late_min"], 1),
        "d_dwell_pct": round((res["dwell_wagon_h"] - ref["dwell_wagon_h"]) / ref["dwell_wagon_h"] * 100, 1) if ref["dwell_wagon_h"] else 0.0,
    }


def _solve(layout, tasks, n_locos, loco_start=None, seed=1):
    seqs, m = anneal(layout, tasks, n_locos, fifo(tasks, n_locos), 0, None, loco_start, ITERS, seed, DWELL_W)
    return seqs, m


def replan(station_id, layout, kind, params=None, n_locos=2, n_crews=2, now_min=None):
    t0 = time.perf_counter()
    params = params or {}
    n_locos = max(1, min(4, n_locos))
    n_crews = max(1, min(4, n_crews))
    seed = zlib.crc32((station_id + kind).encode()) % 991
    if now_min is None:
        lt = time.localtime()
        now_min = lt.tm_hour * 60 + lt.tm_min
    all_tasks = gen_tasks(station_id, layout)
    tasks, now = _window(all_tasks, now_min)

    # эталон — оптимальный план до сбоя
    ref_seqs, ref = _solve(layout, tasks, n_locos, None, seed)
    info, alts, noact = {}, [], None

    def add(code, title_params, tks, seqs_res, flags=None, extra_cost=0.0):
        seqs, m = seqs_res
        alts.append({"code": code, "params": title_params or {}, "metrics": _metrics(m, noact_m),
                     "cost": m["cost"] + extra_cost, "flags": flags or [], "schedule": m["schedule"]})

    if kind in ("switch_fail", "switch_icing", "false_occupancy"):
        if kind == "false_occupancy":
            sw = None
            blocked = [int(params.get("track", 2))]
            repair = float(params.get("repair_min", 30))
        elif kind == "switch_icing":
            sws = [int(x) for x in params.get("switches", [3, 5])]
            sw = sws[0]
            blocked = sorted({b for x in sws for b in blocked_tracks(layout, x)})
            repair = float(params.get("repair_min", 60))
        else:
            sw = int(params.get("switch", 3))
            blocked = blocked_tracks(layout, sw)
            repair = float(params.get("repair_min", 45))
        kinds = {t["idx"]: t["kind"] for t in layout["tracks"]}
        hit = [t for t in tasks if t["from"] in blocked or t["to"] in blocked]
        info = {"switch": sw, "switches": params.get("switches", [sw] if sw else []), "track": blocked[0] if kind == "false_occupancy" else None,
                "blocked": blocked, "repair_min": repair, "tasks_affected": len(hit)}
        # без действий: исходный порядок, задачи по закрытым путям ждут ремонта
        base_t = copy.deepcopy(tasks)
        for t in base_t:
            if t["from"] in blocked or t["to"] in blocked:
                t["ready"] = max(t["ready"], now + repair)
        noact = simulate(layout, base_t, ref_seqs, 0, None, False, None, DWELL_W)
        noact_m = noact

        # A. переназначение на ближайшие свободные пути
        ta = copy.deepcopy(tasks)
        free = {k: [i for i, kk in kinds.items() if kk == k and i not in blocked and i > 0] for k in ("reception", "sorting")}
        for t in ta:
            for key in ("from", "to"):
                if t[key] in blocked and free.get(kinds[t[key]]):
                    t[key] = min(free[kinds[t[key]]], key=lambda i: abs(i - t[key]))
        add("REROUTE", {"tracks": ",".join(map(str, blocked))}, ta, _solve(layout, ta, n_locos, None, seed + 1))
        # B. отложить задачи по закрытым путям, остальные — вперёд
        tb = copy.deepcopy(tasks)
        for t in tb:
            if t["from"] in blocked or t["to"] in blocked:
                t["ready"] = max(t["ready"], now + repair)
        add("HOLD_DEFER", {"min": int(repair)}, tb, _solve(layout, tb, n_locos, None, seed + 2))
        # C. ручной перевод стрелки / пропуск по приказу после проверки (ТРА): без простоя, но +время и контроль
        tc = copy.deepcopy(tasks)
        extra_m = 10.0 if kind == "false_occupancy" else 14.0
        for t in tc:
            if t["from"] in blocked or t["to"] in blocked:
                t["extra"] = t.get("extra", 0.0) + extra_m
        if kind == "false_occupancy":
            add("VISUAL_PASS", {"track": blocked[0]}, tc, _solve(layout, tc, n_locos, None, seed + 3), ["NEEDS_ESC", "VERIFY_CCTV"], extra_cost=150.0)
        else:
            add("MANUAL_THROW", {"sw": sw}, tc, _solve(layout, tc, n_locos, None, seed + 3), ["NEEDS_ESC", "SAFETY_BRIEFING"], extra_cost=180.0)
        if kind == "switch_icing":                  # D. включить обогрев стрелок: ремонт сокращается до 20 мин
            td = copy.deepcopy(tasks)
            for t in td:
                if t["from"] in blocked or t["to"] in blocked:
                    t["ready"] = max(t["ready"], now + 20)
            add("HEAT_ON", {"min": 20}, td, _solve(layout, td, n_locos, None, seed + 4), ["CALL_ESC"], extra_cost=40.0)

    elif kind == "train_delay":
        delay = float(params.get("delay_min", 75))
        cand = [t for t in tasks if t["op"] == "ROSPUSK"] or tasks
        victim = cand[0]["id"]
        info = {"train_task": victim, "delay_min": delay}

        def delayed():
            tt = copy.deepcopy(tasks)
            for t in tt:
                if t["id"] == victim:
                    t["ready"] += delay
                    t["deadline"] += delay * 0.6
            return tt
        noact = simulate(layout, delayed(), ref_seqs, 0, None, False, None, DWELL_W)
        noact_m = noact
        td = delayed()
        add("SWAP_SLOT", {"delay": int(delay)}, td, _solve(layout, td, n_locos, None, seed + 1))
        te = delayed()
        add("ADD_LOCO", {}, te, _solve(layout, te, n_locos + 1, None, seed + 2), ["RESERVE_LOCO"], extra_cost=60.0)
        tf = delayed()
        for t in tf:
            if t["id"] == victim:
                t["priority"] = 1
            elif t["ready"] < now + 180:
                t["priority"] = min(3, t["priority"] + 1)
        add("PRIORITY_SHIFT", {}, tf, _solve(layout, tf, n_locos, None, seed + 3))

    elif kind == "crew_absent":
        eff = max(1, min(n_locos, n_crews) - 1)
        info = {"crews_before": n_crews, "crews_after": max(1, n_crews - 1), "standby_min": 40}
        ref_loc = max(1, min(n_locos, n_crews))
        seqs0 = [list(s) for s in ref_seqs]
        lost = seqs0[eff:] if len(seqs0) > eff else []
        seqs0 = seqs0[:eff]
        for s in lost:
            seqs0[0].extend(s)
        by = {t["id"]: t["ready"] for t in tasks}
        seqs0[0].sort(key=lambda i: by[i])
        noact = simulate(layout, tasks, seqs0, 0, None, False, None, DWELL_W)
        noact_m = noact
        # A. резервная бригада прибывает через 40 мин
        add("STANDBY", {"min": 40}, tasks, anneal(layout, tasks, ref_loc, fifo(tasks, ref_loc), 0, None,
                                                   [now + 40.0] + [0.0] * (ref_loc - 1), ITERS, seed + 1, DWELL_W), ["CALL_STANDBY"], extra_cost=40.0)
        # B. перепланирование на меньшее число бригад
        add("REDUCE", {"crews": eff}, tasks, _solve(layout, tasks, eff, None, seed + 2))
        # C. отложить низкоприоритетные операции
        tc = copy.deepcopy(tasks)
        for t in tc:
            if t["priority"] == 1:
                t["ready"] += 60
                t["deadline"] += 60
        add("DEFER_LOW", {}, tc, _solve(layout, tc, eff, None, seed + 3))

    elif kind == "loco_fail":
        eff = max(1, n_locos - 1)
        info = {"locos_before": n_locos, "locos_after": eff, "borrow_min": 50}
        seqs0 = [list(x) for x in ref_seqs]
        lost = seqs0[eff:] if len(seqs0) > eff else []
        seqs0 = seqs0[:eff]
        for x in lost:
            seqs0[0].extend(x)
        by = {t["id"]: t["ready"] for t in tasks}
        seqs0[0].sort(key=lambda i: by[i])
        noact = simulate(layout, tasks, seqs0, 0, None, False, None, DWELL_W)
        noact_m = noact
        add("BORROW_LOCO", {"min": 50}, tasks, anneal(layout, tasks, n_locos, fifo(tasks, n_locos), 0, None,
                                                       [now + 50.0] + [0.0] * (n_locos - 1), ITERS, seed + 1, DWELL_W), ["REQUEST_NEIGHBOR"], extra_cost=50.0)
        add("REDUCE_LOCO", {"locos": eff}, tasks, _solve(layout, tasks, eff, None, seed + 2))
        merged, used = [], set()
        srt = sorted(tasks, key=lambda x: x["ready"])
        for a in srt:
            if a["id"] in used:
                continue
            for b in srt:
                if b["id"] != a["id"] and b["id"] not in used and b["from"] == a["from"] and abs(b["ready"] - a["ready"]) < 60 and a["wagons"] + b["wagons"] <= 34:
                    a = dict(a, wagons=a["wagons"] + b["wagons"], priority=max(a["priority"], b["priority"]), deadline=min(a["deadline"], b["deadline"]))
                    used.add(b["id"])
                    break
            merged.append(a)
        add("COMBINE_CUTS", {"n": len(tasks) - len(merged)}, merged, _solve(layout, merged, eff, None, seed + 3))

    elif kind == "track_full":
        so = [t["idx"] for t in layout["tracks"] if t["kind"] == "sorting"]
        blocked = so[: max(1, (len(so) + 1) // 2)] if so else []
        hold = float(params.get("hold_min", 120))
        hit = [t for t in tasks if t["from"] in blocked or t["to"] in blocked]
        info = {"blocked": blocked, "hold_min": hold, "tasks_affected": len(hit)}
        base_t = copy.deepcopy(tasks)
        for t in base_t:
            if t["from"] in blocked or t["to"] in blocked:
                t["ready"] = max(t["ready"], now + hold)
        noact = simulate(layout, base_t, ref_seqs, 0, None, False, None, DWELL_W)
        noact_m = noact
        free_so = [i for i in so if i not in blocked]
        ta = copy.deepcopy(tasks)
        for t in ta:
            for key in ("from", "to"):
                if t[key] in blocked and free_so:
                    t[key] = min(free_so, key=lambda i, v=t[key]: abs(i - v))
        add("EVACUATE", {"tracks": ",".join(map(str, blocked)), "min": 45}, ta, _solve(layout, ta, n_locos, None, seed + 1), ["REQUEST_NEIGHBOR"], extra_cost=60.0)
        tb = copy.deepcopy(tasks)
        rec = [t["idx"] for t in layout["tracks"] if t["kind"] == "reception"]
        for t in tb:
            for key in ("from", "to"):
                if t[key] in blocked and rec:
                    t[key] = rec[(t["id"] + (1 if key == "to" else 0)) % len(rec)]
        add("RECV_DIRECT", {}, tb, _solve(layout, tb, n_locos, None, seed + 2))
        tc = copy.deepcopy(tasks)
        for t in tc:
            if t["op"] == "ROSPUSK" and (t["id"] % 2 == 0):
                t["ready"] += 60
                t["deadline"] += 60
        add("HOLD_ARRIVALS", {"min": 60}, tc, _solve(layout, tc, n_locos, None, seed + 3), ["APPROACH_HOLD"], extra_cost=30.0)

    elif kind == "border_hold":
        win = float(params.get("hold_min", 90))
        info = {"hold_min": win, "factor": 1.8, "tasks_affected": sum(1 for t in tasks if t["op"] != "RASSTANOVKA")}

        def inspected(extra_each):
            tt = copy.deepcopy(tasks)
            for t in tt:
                if t["op"] != "RASSTANOVKA":
                    t["extra"] = t.get("extra", 0.0) + extra_each
            return tt
        noact = simulate(layout, inspected(18.0), ref_seqs, 0, None, False, None, DWELL_W)
        noact_m = noact
        t1 = inspected(8.0)
        add("EXTRA_TEAM", {}, t1, _solve(layout, t1, n_locos, None, seed + 1), ["REQUEST_BORDER"], extra_cost=70.0)
        t2 = inspected(18.0)
        for t in t2:
            if t["priority"] >= 2 and t["op"] != "RASSTANOVKA":
                t["extra"] -= 10.0
        add("PRECLEARED_FIRST", {}, t2, _solve(layout, t2, n_locos, None, seed + 2))
        t3 = inspected(18.0)
        add("ADD_LOCO_PARALLEL", {}, t3, _solve(layout, t3, n_locos + 1, None, seed + 3), ["RESERVE_LOCO"], extra_cost=60.0)

    elif kind == "arrival_burst":
        n_b = int(params.get("n", 4))
        rec = [t["idx"] for t in layout["tracks"] if t["kind"] == "reception"]
        so = [t["idx"] for t in layout["tracks"] if t["kind"] == "sorting"]
        extra = [{"id": 900 + k, "op": "ROSPUSK", "from": rec[k % len(rec)], "to": so[k % len(so)] if so else rec[0], "wagons": 20, "priority": 2,
                  "ready": round(now + k * 6.0, 1), "deadline": round(now + k * 6.0 + 70, 1), "extra": 0.0} for k in range(n_b)]
        info = {"burst": n_b, "window_min": (n_b - 1) * 6, "tasks_affected": n_b}
        base_t = copy.deepcopy(tasks) + copy.deepcopy(extra)
        noact = simulate(layout, base_t, fifo(base_t, n_locos), 0, None, False, None, DWELL_W)
        noact_m = noact
        ta = copy.deepcopy(tasks) + [dict(e, ready=round(e["ready"] + k * 12.0, 1), deadline=round(e["deadline"] + k * 12.0, 1)) for k, e in enumerate(copy.deepcopy(extra))]
        add("SPREAD", {"min": 12}, ta, _solve(layout, ta, n_locos, None, seed + 1), ["APPROACH_HOLD"])
        tb = copy.deepcopy(tasks) + copy.deepcopy(extra)
        add("ADD_LOCO", {}, tb, _solve(layout, tb, n_locos + 1, None, seed + 2), ["RESERVE_LOCO"], extra_cost=60.0)
        late = lambda k: 60 if k >= n_b // 2 else 0
        tc = copy.deepcopy(tasks) + [dict(e, ready=round(e["ready"] + late(k), 1), deadline=round(e["deadline"] + late(k), 1)) for k, e in enumerate(copy.deepcopy(extra))]
        add("HOLD_HALF", {"n": n_b // 2, "min": 60}, tc, _solve(layout, tc, n_locos, None, seed + 3), ["APPROACH_HOLD"], extra_cost=20.0)

    elif kind == "power_fail":
        info = {"extra_min": 6, "window_min": 60, "tasks_affected": len(tasks)}

        def slow(extra_each):
            tt = copy.deepcopy(tasks)
            for t in tt:
                t["extra"] = t.get("extra", 0.0) + extra_each
            return tt
        noact = simulate(layout, slow(6.0), ref_seqs, 0, None, False, None, DWELL_W)
        noact_m = noact
        t1 = slow(2.0)
        add("BACKUP_POWER", {"min": 20}, t1, _solve(layout, t1, n_locos, None, seed + 1), ["CALL_ESC"], extra_cost=40.0)
        t2 = slow(6.0)
        add("MANUAL_ROUTES", {}, t2, _solve(layout, t2, n_locos, None, seed + 2), ["NEEDS_ESC", "SAFETY_BRIEFING"], extra_cost=120.0)
        t3 = slow(6.0)
        for t in t3:
            if t["priority"] == 1:
                t["ready"] += 45
                t["deadline"] += 45
        add("DEFER_LOW", {}, t3, _solve(layout, t3, n_locos, None, seed + 3))
    else:
        raise ValueError("unknown disruption")

    base_cost = noact["cost"]
    alts.sort(key=lambda a: a["cost"])
    for i, a in enumerate(alts):
        a["id"] = f"ALT-{i + 1}"
        a["recommended"] = i == 0
        a["saves_cost"] = round(base_cost - a["cost"], 1)
        del a["cost"]
    return {
        "station_id": station_id, "kind": kind, "now_min": now, "info": info,
        "reference": {k: v for k, v in clean(ref).items() if k != "schedule"},
        "no_action": _metrics(noact, ref),   # дельты — относительно эталонного плана до сбоя
        "alternatives": alts,
        "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
