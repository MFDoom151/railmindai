"""AI-планировщик маневровой работы ДСП.

Формирует суточный график операций ЧМЭ3: расстановка, роспуск, подформирование. Критерий — минимум порожнего пробега
при ограничении на простой вагонов и опоздания. Решение: локальный поиск (relocate/swap + отжиг) над последовательностями
задач локомотивов. Базовый план — «ручной»: задачи по очереди готовности, локомотивы чередуются и уходят на стоянку."""
import math
import random
import zlib

SPEED_KMH = 15.0          # маневровая скорость
COUPLE_MIN = 4.0          # сцепка/расцепка
PER_WAGON_MIN = 0.7
LATE_PENALTY_M = 40.0     # метров-эквивалент за минуту опоздания
OP_FACTOR = {"ROSPUSK": 1.0, "PODFORM": 0.85, "RASSTANOVKA": 0.6}


def _dist(layout, a, b):
    """Расстояние по горловине между головками путей a и b (м); 0 — депо/главный."""
    return abs(a - b) * layout["lead_step_m"] + 40.0 if a != b else 0.0


def gen_tasks(station_id, layout, n_tasks=None, horizon=1440.0):
    rnd = random.Random(zlib.crc32(station_id.encode()))
    tr = [t["idx"] for t in layout["tracks"] if t["kind"] == "reception"]
    so = [t["idx"] for t in layout["tracks"] if t["kind"] == "sorting"]
    n_tasks = n_tasks or min(34, 12 + 3 * layout["n_tracks"])
    tasks, step = [], horizon / n_tasks
    for k in range(n_tasks):
        t = step * (k + rnd.uniform(0.1, 0.9))
        x = rnd.random()
        if x < 0.4:
            op, src, dst = "ROSPUSK", rnd.choice(tr), rnd.choice(so)
        elif x < 0.8:
            op, src, dst = "PODFORM", rnd.choice(so), rnd.choice(tr)
        else:
            op, src = "RASSTANOVKA", rnd.choice(so)
            others = [s for s in so if s != src] or tr
            dst = rnd.choice(others)
        prio = rnd.choice([1, 1, 2, 3])
        wagons = rnd.randint(6, 24)
        tasks.append({"id": k + 1, "op": op, "from": src, "to": dst, "wagons": wagons, "priority": prio,
                      "ready": round(t, 1), "deadline": round(t + rnd.uniform(40, 95) / prio ** 0.5, 1), "extra": 0.0})
    return tasks


def simulate(layout, tasks, seqs, depot_track=0, dwell_cap=None, return_home=False, loco_start=None, dwell_w=2.0):
    """seqs — последовательности id задач по локомотивам. Возвращает метрики и расписание."""
    by = {x["id"]: x for x in tasks}
    empty_m = loaded_m = late = dwell = 0.0
    sched = []
    for li, seq in enumerate(seqs):
        pos = depot_track
        clock = (loco_start[li] if loco_start and li < len(loco_start) else 0.0)
        for tid in seq:
            tk = by[tid]
            e = _dist(layout, pos, tk["from"])
            l = _dist(layout, tk["from"], tk["to"])
            travel = (e / 1000.0) / SPEED_KMH * 60.0
            start = max(clock + travel, tk["ready"])
            run = ((l / 1000.0) / SPEED_KMH * 60.0 + 2 * COUPLE_MIN + tk["wagons"] * PER_WAGON_MIN) \
                * OP_FACTOR.get(tk.get("op"), 1.0) + tk.get("extra", 0.0)
            end = start + run
            late += max(0.0, end - tk["deadline"]) * tk["priority"]
            dwell += (end - tk["ready"]) * tk["wagons"]
            empty_m += e
            loaded_m += l
            sched.append({"loco": li + 1, "task": tid, "op": tk.get("op"), "start": round(start, 1), "end": round(end, 1),
                          "from": tk["from"], "to": tk["to"], "empty_m": round(e), "wagons": tk["wagons"],
                          "priority": tk["priority"]})
            if return_home:
                back = _dist(layout, tk["to"], depot_track)
                empty_m += back
                pos, clock = depot_track, end + (back / 1000.0) / SPEED_KMH * 60.0
            else:
                pos, clock = tk["to"], end
    over = 0.0 if dwell_cap is None else max(0.0, dwell - dwell_cap) / 60.0 * 300.0
    return {"empty_km": empty_m / 1000, "loaded_km": loaded_m / 1000, "late_min": late, "dwell_wagon_h": dwell / 60.0,
            "schedule": sched, "cost": empty_m + late * LATE_PENALTY_M + over + dwell / 60.0 * dwell_w,
            "makespan": max((s["end"] for s in sched), default=0.0)}


def fifo(tasks, n_locos):
    seqs = [[] for _ in range(n_locos)]
    for k, tk in enumerate(sorted(tasks, key=lambda x: x["ready"])):
        seqs[k % n_locos].append(tk["id"])
    return seqs


def anneal(layout, tasks, n_locos, start_seqs, depot=0, dwell_cap=None, loco_start=None, iters=3000, seed=7, dwell_w=2.0):
    rnd = random.Random(seed)
    cur = [list(s) for s in start_seqs]
    cur_m = simulate(layout, tasks, cur, depot, dwell_cap, False, loco_start, dwell_w)
    best, best_m = [list(s) for s in cur], cur_m
    T = max(50.0, abs(cur_m["cost"]) * 0.05)
    for _ in range(iters):
        cand = [list(s) for s in cur]
        if rnd.random() < 0.6:
            src = [i for i, s in enumerate(cand) if s]
            if not src:
                break
            a = rnd.choice(src)
            tid = cand[a].pop(rnd.randrange(len(cand[a])))
            b = rnd.randrange(n_locos)
            cand[b].insert(rnd.randint(0, len(cand[b])), tid)
        else:
            a, b = rnd.randrange(n_locos), rnd.randrange(n_locos)
            if not cand[a] or not cand[b]:
                continue
            i, j = rnd.randrange(len(cand[a])), rnd.randrange(len(cand[b]))
            cand[a][i], cand[b][j] = cand[b][j], cand[a][i]
        m = simulate(layout, tasks, cand, depot, dwell_cap, False, loco_start, dwell_w)
        d = m["cost"] - cur_m["cost"]
        if d < 0 or rnd.random() < math.exp(-d / max(T, 1e-6)):
            cur, cur_m = cand, m
            if m["cost"] < best_m["cost"]:
                best, best_m = [list(s) for s in cand], m
        T *= 0.9985
    return best, best_m


def _pct(a, b):
    return round((a - b) / a * 100, 1) if a > 1e-9 else 0.0


def clean(m):
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items() if k != "cost"}


def optimize(station_id, layout, n_locos=2, n_tasks=None):
    n_locos = max(1, min(4, n_locos))
    tasks = gen_tasks(station_id, layout, n_tasks)
    base_seqs = fifo(tasks, n_locos)
    base = simulate(layout, tasks, base_seqs, 0, None, True)
    seqs, best = anneal(layout, tasks, n_locos, base_seqs, 0, base["dwell_wagon_h"] * 60.0,
                        iters=3000, seed=7 + zlib.crc32(station_id.encode()) % 997)
    empty_sav = _pct(base["empty_km"], best["empty_km"])
    dwell_sav = _pct(base["dwell_wagon_h"], best["dwell_wagon_h"])
    tb, to = base["empty_km"] + base["loaded_km"], best["empty_km"] + best["loaded_km"]
    gain = round(min(9.0, 0.07 * max(empty_sav, 0) + 0.25 * max(dwell_sav, 0)), 1)
    ops = {}
    for t in tasks:
        ops[t["op"]] = ops.get(t["op"], 0) + 1
    return {
        "station_id": station_id, "locos": n_locos, "horizon_min": 1440, "tasks": tasks, "ops": ops,
        "baseline": clean(base), "optimized": clean(best),
        "savings": {"empty_km_pct": empty_sav, "total_km_pct": _pct(tb, to), "dwell_pct": dwell_sav,
                    "late_min": round(base["late_min"] - best["late_min"], 1), "throughput_gain_pct": gain},
        "schedule": sorted(best["schedule"], key=lambda x: (x["loco"], x["start"])),
        "seqs": seqs,
        "method": "simulated-annealing(relocate+swap), 3000 iter",
    }
