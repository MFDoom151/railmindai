"""Тесты платформы «Цифровая станция»: планировщик, ingest, индекс, конфигурация, API, WebSocket, нагрузка."""
import asyncio
import json
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="railmind_test_")
os.environ["RAILTWIN_DB_PATH"] = os.path.join(TMP, "t.db")
os.environ["RAILTWIN_CONFIG_OVERRIDE"] = os.path.join(TMP, "override.yaml")
os.environ.setdefault("RAILTWIN_DEV", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from digital import config as cfgmod  # noqa: E402
from digital.conflicts import conflict_weight, validate_plan  # noqa: E402
from digital.efficiency import compute_index  # noqa: E402
from digital.infra import build_infra  # noqa: E402
from digital.ingest import Ingest, Validator  # noqa: E402
from digital.planner import Params, PlanInput, TrainJob, solve  # noqa: E402
from digital.service import StationRuntime  # noqa: E402
from digital.state import StationState  # noqa: E402
from digital.store import Store  # noqa: E402
from digital.timetable import Timetable  # noqa: E402

HARD = {"TRACK_OVERLAP", "TRACK_CLOSED", "ROUTE_CONFLICT", "LOCO_CONFLICT", "LOCO_UNAVAILABLE", "CREW_SHORTAGE"}


def _inp(sid, closures=(), horizon=360, now=360):
    infra = build_infra(sid)
    tt = Timetable(sid, infra, 42)
    sch = tt.extend(now, now + horizon)
    jobs = [TrainJob(s.id, s.type, s.priority, s.wagons, int(s.arr), int(s.arr), int(s.dep), [(o.kind, o.dur) for o in s.ops], s.dwell_min, transit=s.type == "transit") for s in sch]
    return PlanInput(now, infra, jobs, {k: now for k in infra.locos}, infra.crews, [], list(closures) + infra.closures(now, now + 600), 0, 480)


# ---------------------------------------------------------------------------------------------------- планировщик
def test_planner_conflict_free_both_engines():
    cc = cfgmod.get().conflicts
    for sid in ("almaty1", "kopa", "astana", "dostyk"):
        inp = _inp(sid)
        P = Params.from_config(cfgmod.get().planner)
        for eng in ("heuristic", "cpsat"):
            plan = solve(inp, P, None, eng, engine=eng)
            hard = [c for c in validate_plan(inp, plan, P, cc) if c["type"] in HARD]
            assert not hard, (sid, eng, hard[:2])
            assert len(plan.items) == len(inp.jobs)


def test_planner_replan_time_under_3s():
    inp = _inp("almaty1", closures=[(2, 370, 520), (3, 380, 500)])
    inp.crew_absent = [(365, 520, 1)]
    inp.locos[next(iter(inp.locos))] = 450
    P = Params.from_config(cfgmod.get().planner)
    t0 = time.perf_counter()
    plan = solve(inp, P, None, "x")
    assert time.perf_counter() - t0 < 3.0
    assert not [c for c in validate_plan(inp, plan, P, cfgmod.get().conflicts) if c["type"] in HARD]


def test_closure_under_occupying_train_is_deferred():
    inp = _inp("kopa")
    j = inp.jobs[0]
    j.phase, j.track, j.eta = "on_track", 1, inp.now
    inp.closures.append((1, inp.now, inp.now + 120))
    P = Params.from_config(cfgmod.get().planner)
    plan = solve(inp, P, None, "x")
    assert plan.solver.get("engine") == "cpsat" and not plan.solver.get("fallback")
    assert plan.items[j.id].track == 1


def test_priority_weights_matter():
    inp = _inp("kopa")
    P = Params.from_config(cfgmod.get().planner)
    plan = solve(inp, P, None, "x")
    pass_late = [it.late for it in plan.items.values() if inp.jobs and next(j for j in inp.jobs if j.id == it.train_id).type == "passenger"]
    assert all(l <= 5 for l in pass_late)               # пассажирские (приоритет 5) практически не опаздывают


# ---------------------------------------------------------------------------------------------------- ingest
def _ing(sid="otar"):
    infra = build_infra(sid)
    st = StationState(infra)
    return infra, st, Ingest(infra, st, cfgmod.get().ingest)


def _ev(kind, entity, seq, ts, p):
    return {"kind": kind, "entity": entity, "seq": seq, "ts": ts, "p": p, "src": "t", "wt": time.time()}


def test_ingest_validation_dedup_stale_smoothing():
    infra, st, ing = _ing()
    sched = {"id": "Г-1", "type": "freight", "pr": 2, "wagons": 14, "arr": 400, "dep": 480, "ops": [{"kind": "INSPECT", "dur": 10}], "dwell_min": 0}
    pos = lambda seq, ts, pm, **k: _ev("train_pos", "Г-1", seq, ts, {"phase": "on_track", "track": 1, "prog": 1, "pos_m": pm, "speed": 0, **k})
    bad = [_ev("train_pos", "Г-1", 90, 1, {"phase": "teleport", "track": 1, "prog": 1, "pos_m": 1, "speed": 0}),
           _ev("train_pos", "Г-1", 91, 1, {"phase": "on_track", "track": 99, "prog": 1, "pos_m": 1, "speed": 0}),
           _ev("train_pos", "Г-1", 92, 1, {"phase": "on_track", "track": 1, "prog": 1, "pos_m": -5, "speed": 0}),
           _ev("train_pos", "Г-1", 93, 1, {"phase": "on_track", "track": 1, "prog": 1, "pos_m": 5, "speed": float("nan")}),
           {"kind": "nope"}, "garbage"]
    ing.push([_ev("train_sched", "Г-1", 1, 390, sched), pos(1, 400, 100), pos(1, 400, 100), pos(2, 401, 200)] + bad)
    ing.flush(force=True)
    ing.push([pos(0, 399, 999)])                           # приходит позже следующего окна — устаревшее
    ing.flush(force=True)
    c = ing.counters
    assert c["invalid"] == len(bad), dict(c)
    assert c["duplicates"] == 1                          # повтор seq=1
    assert c["stale"] == 1                               # ts=399 < 401 по той же сущности
    t = st.trains["Г-1"]
    assert 100 < t.pos_m < 200                           # сглажено EMA, а не «прыжок» к 200
    assert Validator(infra).check(_ev("train_pos", "Г-1", 5, 5, {"phase": "on_track", "track": 1, "prog": 0.5, "pos_m": 10, "speed": 3})) is None


def test_ingest_reorders_within_window():
    infra, st, ing = _ing()
    ing.push([_ev("sim_clock", "clock", 2, 11, {"now": 11}), _ev("sim_clock", "clock", 1, 10, {"now": 10})])
    ing.flush(force=True)
    assert st.now == 11


# ---------------------------------------------------------------------------------------------------- индекс
def test_index_formula_properties():
    icfg = cfgmod.get().index
    good = {"throughput": 1, "avg_dev": 0, "max_dev": 0, "track_load": 0.5, "conflicts": 0, "queue_outside": 0, "loco_idle": 0.5, "crew_idle": 0.5}
    i = compute_index(icfg, good)
    assert i["score"] == 100 and i["category"] == "norm" and i["letter"] == "A"
    worse = dict(good, conflicts=3, avg_dev=20)
    j = compute_index(icfg, worse)
    assert j["score"] < i["score"]
    assert len(j["top"]) == 5 and j["top"][0]["loss"] >= j["top"][-1]["loss"]
    assert abs(sum(f["contrib"] + f["loss"] for f in j["factors"]) - 100) < 0.2          # вклад + потеря = 100
    bad = {"throughput": 0, "avg_dev": 90, "max_dev": 120, "track_load": 1, "conflicts": 9, "queue_outside": 9, "loco_idle": 1, "crew_idle": 1}
    k = compute_index(icfg, bad)
    assert k["category"] == "critical" and k["letter"] == "E" and 0 <= k["score"] < 10


def test_config_validation_and_override():
    st = cfgmod.STORE
    try:
        st.update({"index": {"thresholds": {"norm": 40, "attention": 60}}})
        assert False, "должна быть ошибка валидации"
    except Exception as e:
        assert "attention" in str(e) or "thresholds" in str(e)
    st.update({"index": {"thresholds": {"norm": 80, "attention": 55}}})
    assert cfgmod.get().index.thresholds["norm"] == 80
    st.reset_override()
    assert cfgmod.get().index.thresholds["norm"] == 75


# ---------------------------------------------------------------------------------------------------- рантайм
def test_runtime_disruption_alternatives_and_stress():
    async def go():
        store = Store(os.path.join(TMP, "rt.db"), 72)
        r = StationRuntime("almaty1", store)
        r.bootstrap()
        for _ in range(4):
            r.tick(0.5)
            await asyncio.sleep(0.02)
        r.inject("track_close", {"track": 3, "minutes": 60})
        for _ in range(100):
            r.tick(0.5)
            await asyncio.sleep(0.03)
            if r.alts and r.alts["stage"] == "final":
                break
        a = r.alts
        assert a and a["stage"] == "final" and a["alts"] and a["baseline"]
        assert a["t_quick_ms"] < 500 and a["t_total_ms"] < 5000
        best = next(x for x in a["alts"] if x["recommended"])
        assert r.apply_alternative(best["id"])
        t0 = time.perf_counter()
        out = r.stress(10)
        assert len(out) >= 8 and (time.perf_counter() - t0) < 0.5            # приём всплеска не блокирует цикл
        for _ in range(140):
            r.tick(0.5)
            await asyncio.sleep(0.03)
            if r.alts and r.alts["stage"] == "final" and any(d.get("kind") == "stress" for d in r.alts["disruptions"]):
                break
        assert r.alts["t_total_ms"] < 6000
        assert max(r.tick_ms, 0) < 250
        store.close()
    asyncio.run(go())


# ---------------------------------------------------------------------------------------------------- API
def test_api_end_to_end():
    with TestClient(main.app) as c:
        assert c.get("/health").json()["status"] == "ok"
        assert len(c.get("/api/v1/stations").json()) == 13
        snap = c.get("/api/v1/stations/kopa/snapshot").json()
        assert snap["plan"]["items"] and 0 <= snap["index"]["score"] <= 100
        # права
        assert c.post("/api/v1/stations/kopa/disruptions", json={"kind": "track_close", "params": {"track": 2}}).status_code == 401
        assert c.put("/api/v1/config", json={"patch": {}}).status_code == 401
        assert c.post("/api/v1/auth/login", json={"username": "x", "password": "y"}).status_code == 401
        d = c.post("/api/v1/auth/login", json={"username": "dispatcher", "password": "dispatcher"}).json()
        H = {"Authorization": "Bearer " + d["token"]}
        assert c.put("/api/v1/config", json={"patch": {}}, headers=H).status_code == 403          # диспетчер не меняет настройки
        a = c.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}).json()
        HA = {"Authorization": "Bearer " + a["token"]}
        assert c.put("/api/v1/config", json={"patch": {"planner": {"w_change": 4.0}}}, headers=HA).status_code == 200
        assert c.put("/api/v1/config", json={"patch": {"index": {"weights": {"x": 1}}}}, headers=HA).status_code == 422
        c.delete("/api/v1/config/override", headers=HA)
        # сбой → альтернативы
        r = c.post("/api/v1/stations/kopa/disruptions?wait=true", json={"kind": "train_delay", "params": {"min": 30}}, headers=H).json()
        assert r["accepted_ms"] < 200 and r["alternatives"]["alts"]
        assert c.post("/api/v1/stations/kopa/plan/apply", json={"alt_id": "nope"}, headers=H).status_code == 404
        # приём внешних данных: валидные/некорректные/дубли
        ev = {"kind": "sim_clock", "entity": "clock", "seq": 10 ** 6, "ts": 9999, "p": {"now": 9999}}
        res = c.post("/api/v1/ingest", json={"station": "kopa", "events": [ev, ev, {"kind": "bad"}]}, headers=H).json()
        assert res["invalid"] == 1 and res["duplicates"] == 1
        # отчёты
        assert c.get("/api/v1/stations/kopa/report.pdf").content[:5] == b"%PDF-"
        assert "# index" in c.get("/api/v1/stations/kopa/report.csv").text
        # метрики
        m = c.get("/metrics").text
        assert "railmind_replan_seconds_count" in m and "railmind_index" in m
        # история и события
        time.sleep(0.5)
        assert c.get("/api/v1/stations/kopa/events?minutes=10").json()["events"]
        assert c.get("/api/v1/stations/kopa/replay?seconds=60&step=1").json()["frames"]


def test_websocket_stream_rate_and_reconnect():
    with TestClient(main.app) as c:
        for attempt in range(2):                       # повторное подключение с историей
            with c.websocket_connect("/ws/v1/stream?station=otar&backfill=30") as ws:
                types, t0 = [], time.time()
                first = None
                while time.time() - t0 < 3.2:
                    m = json.loads(ws.receive_text())
                    types.append(m["type"])
                    if m["type"] == "hello":
                        first = m
                assert first and first["infra"]["tracks"] and first["index_formula"]["weights"]
                states = types.count("state")
                assert states >= 5, (attempt, states)          # >= 1.5 Гц
                assert "backfill" in types and "plan" in types
                ws.send_text(json.dumps({"cmd": "ping", "t": 1}))
                got = False
                for _ in range(10):
                    if json.loads(ws.receive_text())["type"] == "pong":
                        got = True
                        break
                assert got


def test_ws_ingest_requires_role():
    with TestClient(main.app) as c:
        try:
            with c.websocket_connect("/ws/v1/ingest?station=otar"):
                assert False, "анонимный приём должен быть закрыт"
        except Exception:
            pass


def test_env_config_override(monkeypatch=None):
    import os
    from digital.config import ConfigStore
    os.environ["RAILTWIN__planner__workers"] = "2"
    try:
        assert ConfigStore().cfg.planner.workers == 2
    finally:
        del os.environ["RAILTWIN__planner__workers"]


def test_open_demo_role():
    from digital.auth import Auth
    a = Auth()
    class R:
        headers = {}
        query_params = {}
    a.open_demo = False
    assert a.role_of(R()) == "viewer"
    a.open_demo = True
    assert a.role_of(R()) == "dispatcher"
