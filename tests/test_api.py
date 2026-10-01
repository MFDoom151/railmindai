"""Smoke-тесты бэкенда: python -m pytest tests  (или python tests/test_api.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

c = TestClient(main.app)


def test_network():
    r = c.get("/api/network").json()
    ids = {s["id"] for s in r["stations"]}
    for k in ("almaty1", "shu", "shymkent", "astana", "karaganda", "altynkol", "dostyk", "aktau", "petropavlovsk", "pavlodar", "kopa", "otar"):
        assert k in ids
    assert len(r["sections"]) >= 10 and all("load" in s for s in r["sections"])


def test_shoe_flow():
    c.post("/api/station/open", json={"station_id": "otar"})
    assert c.post("/api/safety/shoe/inject", json={"station_id": "otar", "shoe_id": 19, "track": 2}).json()["status"] == "ON_RAIL"
    assert not c.post("/api/safety/departure-check", json={"station_id": "otar", "track": 2}).json()["allowed"]
    c.post("/api/safety/shoe/remove", json={"station_id": "otar", "shoe_id": 19})
    assert c.post("/api/safety/departure-check", json={"station_id": "otar", "track": 2}).json()["allowed"]


def test_hostile():
    r = c.post("/api/safety/scenario/hostile-route", json={"station_id": "kopa"}).json()
    assert r["prevented"] == 3


def test_optimize_daily():
    r = c.post("/api/optimize", json={"station_id": "almaty1", "crews": 2, "apply": False}).json()
    assert r["savings"]["empty_km_pct"] > 0 and {"ROSPUSK", "PODFORM", "RASSTANOVKA"} <= set(r["ops"])


def test_disruption_under_1s():
    for kind, p in (("switch_fail", {"switch": 3}), ("train_delay", {"delay_min": 75}), ("crew_absent", {})):
        r = c.post("/api/disruption/replan", json={"station_id": "kopa", "kind": kind, "params": p, "now_min": 480}).json()
        assert 2 <= len(r["alternatives"]) <= 3 and r["compute_ms"] < 1000, (kind, r["compute_ms"])


def test_unsecured_flow():
    c.post("/api/station/open", json={"station_id": "shamalgan"})
    c.post("/api/safety/unsecured/inject", json={"station_id": "shamalgan", "track": 2})
    assert not c.post("/api/safety/departure-check", json={"station_id": "shamalgan", "track": 2}).json()["allowed"]
    assert c.post("/api/safety/unsecured/secure", json={"station_id": "shamalgan", "track": 2}).json()["secured"]
    assert c.post("/api/safety/departure-check", json={"station_id": "shamalgan", "track": 2}).json()["allowed"]


def test_every_station_has_distinct_profile():
    st = c.get("/api/network").json()["stations"]
    problems = [s["problem"] for s in st]
    assert len(set(problems)) == 13, problems
    layouts = {(s["tracks"]["reception"], s["tracks"]["sorting"]) for s in st}
    assert len(layouts) >= 7


def test_all_disruption_kinds():
    import disruption
    for kind in disruption.KINDS:
        r = c.post("/api/disruption/replan", json={"station_id": "karaganda", "kind": kind, "now_min": 480}).json()
        assert 2 <= len(r["alternatives"]) <= 4 and r["compute_ms"] < 1000, (kind, r.get("compute_ms"))


def test_telemetry_bottleneck():
    c.post("/api/station/open", json={"station_id": "kopa"})
    tel = {"t": 10, "resources": {"recv_total": 2, "recv_busy": 1, "sort_total": 2, "sort_busy": 2, "locos_total": 2, "locos_free": 1,
                                  "crews_total": 2, "crews_free": 1, "active_routes": ["A2"], "requested_routes": ["A1"]},
           "trains": [{"id": 1, "state": "waiting", "track": 1, "arr_t": 0, "wait_t0": 2, "reason": "TRACK", "n": 8}], "departed": []}
    c.post("/api/station/kopa/telemetry", json=tel)
    tel["t"] = 14
    r = c.post("/api/station/kopa/telemetry", json=tel).json()
    assert r["bottleneck"]["resource"] == "TRACK" and "LOCO" in r["bottleneck"]["available"]
    assert r["conflicts"] and r["conflicts"][0]["route"] == "A1"
    assert r["dwell"]["wait_min"]["TRACK"] > 0


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
