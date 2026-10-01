"""Модуль безопасности маневров: контроль тормозных башмаков (RFID) и враждебных маршрутов ДСП.
Все решения — коды причин; локализацию делает фронтенд."""
import time
from collections import deque

LOG = deque(maxlen=200)


def log_event(station_id, level, code, **params):
    ev = {"ts": time.time(), "station": station_id, "level": level, "code": code, "params": params}
    LOG.appendleft(ev)
    return ev


class Interlocking:
    """Упрощённая электрическая централизация: замыкание маршрутов, враждебность, контроль стрелок."""

    def __init__(self, layout):
        self.layout = layout
        self.routes = layout["routes"]
        self.active = {}          # route_id -> {"signal": "GREEN"}
        self.occupied = set()     # занятые секции (по данным рельсовых цепей)
        self.sw_pos = {s["id"]: "N" for s in layout["switches"]}
        self.sw_locked = {}       # switch id -> route id

    def _sections_of_switch(self, sw_id):
        side = "L" if sw_id % 2 == 1 else "R"
        head = (sw_id - 1) // 2 if side == "L" else (sw_id - 2) // 2
        n = self.layout["n_tracks"]
        secs = {"ML" if side == "L" else "MR"}
        if head >= 1:
            secs.add(f"L{side}{head}")
        if head < n:
            secs.add(f"L{side}{head + 1}")
        return secs

    def request_route(self, route_id):
        r = self.routes.get(route_id)
        if not r:
            return {"allowed": False, "reasons": [{"code": "UNKNOWN_ROUTE", "route": route_id}]}
        reasons = []
        for rid, st in self.active.items():
            r2 = self.routes[rid]
            shared = sorted(set(r["sections"]) & set(r2["sections"]))
            if shared:
                reasons.append({"code": "HOSTILE_ROUTE", "route": rid, "sections": shared})
        for sw, pos in r["switches"].items():
            holder = self.sw_locked.get(sw)
            if holder and holder != route_id and self.sw_pos[sw] != pos:
                reasons.append({"code": "SWITCH_LOCKED", "switch": sw, "route": holder, "need": pos, "have": self.sw_pos[sw]})
        busy = sorted(set(r["sections"]) & self.occupied)
        if busy:
            reasons.append({"code": "SECTION_OCCUPIED", "sections": busy})
        if reasons:
            return {"allowed": False, "route": route_id, "reasons": reasons}
        for sw, pos in r["switches"].items():
            self.sw_pos[sw] = pos
            self.sw_locked[sw] = route_id
        self.active[route_id] = {"signal": "GREEN"}
        return {"allowed": True, "route": route_id, "signal": r["signal"], "locked": {str(k): v for k, v in r["switches"].items()}}

    def request_switch(self, sw_id, pos):
        reasons = []
        holder = self.sw_locked.get(sw_id)
        if holder and self.sw_pos[sw_id] != pos:
            reasons.append({"code": "SWITCH_LOCKED", "switch": sw_id, "route": holder, "need": pos, "have": self.sw_pos[sw_id]})
            if self.active.get(holder, {}).get("signal") == "GREEN":
                reasons.append({"code": "PROHIBITED_SIGNAL_MOVE", "signal": self.routes[holder]["signal"], "route": holder})
        under = sorted(self._sections_of_switch(sw_id) & self.occupied)
        if under:
            reasons.append({"code": "SWITCH_UNDER_TRAIN", "switch": sw_id, "sections": under})
        if reasons:
            return {"allowed": False, "switch": sw_id, "reasons": reasons}
        self.sw_pos[sw_id] = pos
        return {"allowed": True, "switch": sw_id, "pos": pos}

    def release_route(self, route_id):
        self.active.pop(route_id, None)
        for sw, rid in list(self.sw_locked.items()):
            if rid == route_id:
                del self.sw_locked[sw]


class SafetyState:
    def __init__(self, station_id, layout, case):
        self.station_id = station_id
        self.layout = layout
        self.case = case
        self.shoes = {}
        self._blocked = set()
        self.unsecured = {}
        self.il = Interlocking(layout)
        self._seed()

    def _seed(self):
        # Реестр содержит только башмаки, зафиксированные RFID под подвижным составом (по умолчанию пусто).
        self.shoes = {}

    def snapshot(self):
        return {"station": self.station_id, "shoes": list(self.shoes.values()), "unsecured": sorted(self.unsecured),
                "active_routes": list(self.il.active.keys()),
                "sw_pos": {str(k): v for k, v in self.il.sw_pos.items()}}

    # --- тормозные башмаки ---
    def inject_shoe(self, shoe_id, track):
        self.shoes[shoe_id] = {"id": shoe_id, "track": track, "status": "ON_RAIL", "rfid": f"RFID-{shoe_id:04d}"}
        log_event(self.station_id, "warn", "SHOE_DETECTED", shoe=shoe_id, track=track)
        return self.shoes[shoe_id]

    def remove_shoe(self, shoe_id):
        s = self.shoes.get(shoe_id)
        if not s:
            return None
        s["status"] = "REMOVED"
        del self.shoes[shoe_id]
        log_event(self.station_id, "ok", "SHOE_REMOVED", shoe=shoe_id, track=s["track"])
        return s

    # --- незакреплённый состав (IoT-контроль закрепления, кейс уклона) ---
    def inject_unsecured(self, track):
        self.unsecured[track] = {"track": track}
        log_event(self.station_id, "crit", "UNSECURED_DETECTED", track=track)
        return {"track": track, "status": "UNSECURED"}

    def secure(self, track):
        if self.unsecured.pop(track, None) is not None:
            log_event(self.station_id, "ok", "SECURED", track=track)
            return True
        return False

    def departure_check(self, track):
        if track in self.unsecured:
            key = (track, ("UNSEC",))
            new = key not in self._blocked
            if new:
                self._blocked.add(key)
                log_event(self.station_id, "crit", "DEPARTURE_BLOCKED_UNSECURED", track=track)
            return {"allowed": False, "track": track, "signal": "RED", "new_block": new,
                    "reasons": [{"code": "UNSECURED", "track": track}]}
        blocking = [s for s in self.shoes.values() if s["track"] == track and s["status"] == "ON_RAIL"]
        if blocking:
            ids = [s["id"] for s in blocking]
            key = (track, tuple(ids))
            new = key not in self._blocked
            if new:
                self._blocked.add(key)
                log_event(self.station_id, "crit", "DEPARTURE_BLOCKED_SHOE", shoes=ids, track=track)
            return {"allowed": False, "track": track, "signal": "RED", "new_block": new,
                    "reasons": [{"code": "SHOE_ON_RAIL", "shoes": ids, "track": track}]}
        if any(k[0] == track for k in self._blocked):
            self._blocked = {k for k in self._blocked if k[0] != track}
            log_event(self.station_id, "ok", "DEPARTURE_ALLOWED", track=track)
        return {"allowed": True, "track": track, "signal": "GREEN", "reasons": []}

    # --- сценарий враждебных маршрутов (кейс ст. Копа) ---
    def hostile_route_scenario(self):
        il = Interlocking(self.layout)
        n = self.layout["n_tracks"]
        a_main = "A2" if n >= 2 else "A1"
        steps = []
        r = il.request_route(a_main)
        steps.append({"step": "ROUTE_REQUEST", "route": a_main, "result": r})
        il.occupied.add("LL1")  # поезд прошёл входной светофор и занял стрелочную горловину
        r = il.request_route("A1")
        steps.append({"step": "ROUTE_REQUEST", "route": "A1", "result": r})
        r = il.request_switch(3, "R")
        steps.append({"step": "SWITCH_REQUEST", "switch": 3, "pos": "R", "result": r})
        r = il.request_route("X0")
        steps.append({"step": "ROUTE_REQUEST", "route": "X0", "result": r})
        for s in steps:
            res = s["result"]
            if res["allowed"]:
                log_event(self.station_id, "ok", "ROUTE_SET", route=s["route"])
            else:
                log_event(self.station_id, "crit", "INTERLOCK_BLOCK", target=s.get("route") or f"СП{s['switch']}",
                          reasons=[x["code"] for x in res["reasons"]])
        return {"station": self.station_id, "steps": steps, "prevented": sum(1 for s in steps if not s["result"]["allowed"])}
