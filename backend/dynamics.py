"""Динамика станции: учёт простоя поездов (Train Dwell Time), согласование 4 ресурсов и поиск «узкого места».

Клиент (3D-двойник) периодически шлёт телеметрию: счётчики ресурсов, активные/запрошенные маршруты и список составов
с причиной ожидания. Бэкенд интегрирует простой по причинам, проверяет конфликты маршрутов и формирует диагноз."""
from collections import defaultdict, deque

SCALE = 2.0              # минут модельного времени в секунде времени сцены
RESOURCES = ("TRACK", "LOCO", "CREW", "ROUTE")


def route_conflicts(layout, route_id, active_ids, failed_switches=(), blocked_tracks=()):
    """Конфликтная проверка маршрута: враждебные маршруты, отказ стрелки, закрытый путь."""
    r = layout["routes"].get(route_id)
    if not r:
        return [{"code": "UNKNOWN_ROUTE", "route": route_id}]
    reasons = []
    if r["track"] in blocked_tracks:
        reasons.append({"code": "TRACK_BLOCKED", "track": r["track"]})
    for sw, pos in r["switches"].items():
        if int(sw) in failed_switches and pos == "R":
            reasons.append({"code": "SWITCH_FAILED", "switch": int(sw)})
    for rid in active_ids:
        if rid == route_id:
            continue
        r2 = layout["routes"].get(rid)
        if not r2:
            continue
        shared = sorted(set(r["sections"]) & set(r2["sections"]))
        if shared:
            reasons.append({"code": "HOSTILE_ROUTE", "route": rid, "sections": shared})
    return reasons


class StationDynamics:
    def __init__(self, station_id, layout):
        self.station_id = station_id
        self.layout = layout
        self.reset()

    def reset(self):
        self.trains = {}                      # id -> {arr_t, wait: {cause: min}, n}
        self.done = deque(maxlen=40)          # завершённые: dwell, wait
        self.cause_total = defaultdict(float)
        self.wagon_wait_min = 0.0
        self.last_t = None
        self.history = deque(maxlen=60)

    def _route_state(self, active, failed, blocked):
        routes = [rid for rid, r in self.layout["routes"].items() if r["type"] in ("arrival", "departure")]
        free = [rid for rid in routes if not route_conflicts(self.layout, rid, active, failed, blocked)]
        return routes, free

    def ingest(self, tel):
        t = float(tel.get("t", 0.0))
        dt = 0.0 if self.last_t is None else max(0.0, min(t - self.last_t, 10.0)) * SCALE
        self.last_t = t
        res = tel.get("resources", {})
        trains = tel.get("trains", [])
        seen = set()
        for tr in trains:
            tid = tr["id"]
            seen.add(tid)
            rec = self.trains.setdefault(tid, {"arr_t": tr.get("arr_t", t), "wait": defaultdict(float), "n": tr.get("n", 8)})
            cause = tr.get("reason")
            if cause in RESOURCES and dt:
                rec["wait"][cause] += dt
                self.cause_total[cause] += dt
                self.wagon_wait_min += dt * rec["n"]
        for d in tel.get("departed", []):
            rec = self.trains.pop(d["id"], None)
            wait = sum(rec["wait"].values()) if rec else 0.0
            self.done.append({"dwell": (d["dep_t"] - d["arr_t"]) * SCALE, "wait": wait})
        for tid in list(self.trains):
            if tid not in seen and tid not in {d["id"] for d in tel.get("departed", [])}:
                self.trains.pop(tid, None)

        # --- ресурсы ---
        blocked = list(res.get("blocked_tracks", []))
        failed = [int(x) for x in res.get("failed_switches", [])]
        active = list(res.get("active_routes", []))
        requested = list(res.get("requested_routes", []))
        rt_all, rt_free = self._route_state(active, failed, blocked)
        waiters = [x for x in trains if x.get("reason") in RESOURCES]
        by_reason = defaultdict(list)
        for x in waiters:
            by_reason[x["reason"]].append(max(0.0, (t - x.get("wait_t0", t)) * SCALE))

        tr_total = max(1, res.get("recv_total", 0) + res.get("sort_total", 0) - len(blocked))
        tr_busy = res.get("recv_busy", 0) + res.get("sort_busy", 0)
        items = {
            "TRACK": {"used": tr_busy, "total": tr_total, "free": max(0, tr_total - tr_busy),
                      "detail": {"recv_free": res.get("recv_total", 0) - res.get("recv_busy", 0),
                                 "sort_free": res.get("sort_total", 0) - res.get("sort_busy", 0), "blocked": blocked}},
            "LOCO": {"used": res.get("locos_total", 0) - res.get("locos_free", 0), "total": res.get("locos_total", 0), "free": res.get("locos_free", 0), "detail": {}},
            "CREW": {"used": res.get("crews_total", 0) - res.get("crews_free", 0), "total": res.get("crews_total", 0), "free": res.get("crews_free", 0),
                     "detail": {"absent": res.get("crews_absent", 0)}},
            "ROUTE": {"used": len(rt_all) - len(rt_free), "total": len(rt_all), "free": len(rt_free),
                      "detail": {"active": active, "requested": requested, "failed_switches": failed}},
        }
        for k, it in items.items():
            tot = max(1, it["total"])
            it["util_pct"] = round(it["used"] / tot * 100)
            waiting = len(by_reason.get(k, []))
            if k == "ROUTE":                 # доступность маршрутов — обратная шкала
                it["util_pct"] = round((1 - it["free"] / tot) * 100)
            it["waiting"] = waiting
            it["status"] = "deficit" if waiting else ("tight" if it["free"] == 0 or it["util_pct"] >= 80 else "ok")
            if k == "ROUTE" and not waiting:
                it["status"] = "tight" if it["util_pct"] >= 60 else "ok"

        # --- конфликты запрошенных маршрутов ---
        conflicts = []
        for rid in requested:
            rs = route_conflicts(self.layout, rid, active, failed, blocked)
            if rs:
                conflicts.append({"route": rid, "reasons": rs})

        # --- узкое место ---
        bottleneck = None
        if by_reason:
            primary = max(by_reason, key=lambda k: (sum(by_reason[k]), len(by_reason[k])))
            avail = [k for k in RESOURCES if k != primary and items[k]["free"] > 0 and items[k]["status"] != "deficit"]
            avg_wait = sum(by_reason[primary]) / len(by_reason[primary])
            cause = "MISSING_WITH_AVAILABLE" if avail else "ALL_SCARCE"
            if primary == "ROUTE" and failed:
                cause = "SWITCH_FAILED"
            elif primary == "TRACK" and blocked:
                cause = "TRACK_BLOCKED"
            elif primary == "CREW" and res.get("crews_absent", 0):
                cause = "CREW_ABSENT"
            elif primary == "ROUTE" and conflicts:
                cause = "ROUTE_CONFLICT"
            bottleneck = {"resource": primary, "cause": cause, "available": avail, "waiting": len(by_reason[primary]),
                          "avg_wait_min": round(avg_wait, 1), "max_wait_min": round(max(by_reason[primary]), 1),
                          "severity": "crit" if avg_wait >= 40 else "warn" if avg_wait >= 12 else "info",
                          "params": {"failed": failed, "blocked": blocked, "conflict": conflicts[0]["route"] if conflicts else None}}

        # --- простой ---
        cur = [max(0.0, (t - v["arr_t"]) * SCALE) for v in self.trains.values()]
        done_avg = sum(d["dwell"] for d in self.done) / len(self.done) if self.done else None
        total_wait = sum(self.cause_total.values())
        dwell = {
            "trains_now": len(cur), "avg_now_min": round(sum(cur) / len(cur), 1) if cur else 0.0,
            "max_now_min": round(max(cur), 1) if cur else 0.0,
            "avg_done_min": round(done_avg, 1) if done_avg is not None else None, "done_count": len(self.done),
            "wagon_wait_h": round(self.wagon_wait_min / 60.0, 1),
            "wait_share": {k: round(self.cause_total[k] / total_wait * 100) if total_wait else 0 for k in RESOURCES},
            "wait_min": {k: round(self.cause_total[k]) for k in RESOURCES},
        }
        self.history.append({"t": round(t, 1), "avg": dwell["avg_now_min"], "queue": len(waiters)})
        return {"resources": items, "routes": {"total": len(rt_all), "free": len(rt_free), "free_ids": rt_free},
                "conflicts": conflicts, "bottleneck": bottleneck, "dwell": dwell, "history": list(self.history)}
