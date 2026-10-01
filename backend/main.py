"""RailTwin KZ — FastAPI бэкенд (B2G API) + раздача SPA."""
import math
import time
import zlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from disruption import KINDS, replan
from dynamics import StationDynamics, route_conflicts
from forecast import forecast as make_forecast
from layout import build_layout
from optimizer import optimize
from safety import LOG, SafetyState
from stations import SECTIONS, STATIONS, resolve_sections
from digital import api as digital_api
from digital.metrics import setup_logging
from digital.service import HUB

FRONT = Path(__file__).resolve().parent.parent / "frontend"
DESCRIPTION = """
**Цифровая станция** — платформа планирования и оптимизации работы железнодорожной станции.

* Онлайн: `WS /ws/v1/stream?station=…` (или SSE), частота ≥ 1 Гц.
* ИИ-планирование (OR-Tools CP-SAT + эвристика), конфликты, индекс эффективности (0–100, A–E) с объяснением.
* Нештатные ситуации → мгновенный пересчёт и 2–4 альтернативных плана с влиянием на индекс.
* История (SQLite, 72 ч), перемотка, отчёты PDF/CSV, конфигурация без перекомпиляции, метрики `/metrics`.

Роли: viewer (без входа), dispatcher, admin — `POST /api/v1/auth/login`.
"""
TAGS = [{"name": n, "description": d} for n, d in [
    ("stations", "Станции и текущее состояние"), ("planning", "План, конфликты, альтернативы"), ("disruptions", "Нештатные ситуации и нагрузочный сценарий"),
    ("index", "Индекс эффективности"), ("history", "История и перемотка"), ("reports", "Отчёты PDF/CSV"), ("config", "Конфигурация (admin)"),
    ("ingest", "Приём данных"), ("stream", "Онлайн-поток"), ("auth", "Аутентификация"), ("ops", "Health-check и метрики")]]


@asynccontextmanager
async def lifespan(app_):
    setup_logging()
    HUB.start()
    yield
    await HUB.stop()


app = FastAPI(title="RailTwin KZ — Цифровая станция API", version="3.0", description=DESCRIPTION, openapi_tags=TAGS, lifespan=lifespan)
app.include_router(digital_api.router)


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Статика всегда ревалидируется — при правках интерфейса не нужно чистить кэш браузера."""
    resp = await call_next(request)
    p = request.url.path
    if not p.startswith(("/api", "/ws", "/metrics", "/health")) and "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-cache"
    return resp

LAYOUTS = {sid: build_layout(cfg) for sid, cfg in STATIONS.items()}
SAFETY = {sid: SafetyState(sid, LAYOUTS[sid], cfg["case"]) for sid, cfg in STATIONS.items()}
DYN = {sid: StationDynamics(sid, LAYOUTS[sid]) for sid in STATIONS}
OPT: Dict[str, dict] = {}   # station_id -> применённый суточный план
KPI = {"index": 88, "dwell": -32, "prevented": 100, "prevented_count": 0}


def status_of(load):
    return "red" if load >= 85 else "yellow" if load >= 65 else "green"


def current_load(sid):
    cfg = STATIONS[sid]
    t = time.time() / 60.0
    load = cfg["base_load"] + 5 * math.sin(t / 3 + cfg["phase"]) + 2 * math.sin(t * 1.7 + cfg["phase"] * 2)
    if sid in OPT:
        load -= OPT[sid]["savings"]["throughput_gain_pct"] * 1.1
    return round(max(5, min(99, load)), 1)


def dwell_model(load, cap_day):
    """Ожидаемый простой состава (мин): обслуживание + очередь (форма M/M/c), демонстрационная модель."""
    rho = min(0.97, load / 100.0)
    service = 70.0
    return round(service * (1 + rho ** 3 / (1 - rho) * 0.35), 0)


class OptReq(BaseModel):
    station_id: str
    crews: int = 2
    apply: bool = True


class ShoeReq(BaseModel):
    station_id: str
    shoe_id: int = 19
    track: int = 1


class DepReq(BaseModel):
    station_id: str
    track: int


class StationReq(BaseModel):
    station_id: str


class DisruptReq(BaseModel):
    station_id: str
    kind: str
    params: Dict[str, Any] = {}
    locos: int = 2
    crews: int = 2
    now_min: Optional[float] = None


class RouteCheckReq(BaseModel):
    station_id: str
    request: List[str]
    active: List[str] = []
    failed_switches: List[int] = []
    blocked_tracks: List[int] = []


def _st(sid):
    if sid not in STATIONS:
        raise HTTPException(404, "unknown station")
    return sid


@app.get("/api/health")
def health():
    return {"status": "ok", "anylogic_cloud": "emulated", "iot": "simulated", "cctv": "simulated"}


@app.get("/api/network")
def network():
    st, loads = [], {}
    for sid, cfg in STATIONS.items():
        load = current_load(sid)
        loads[sid] = load
        st.append({"id": sid, "tier": cfg["tier"], "names": cfg["names"], "lat": cfg["lat"], "lon": cfg["lon"],
                   "load": load, "status": status_of(load), "cap_day": cfg["cap_day"],
                   "tracks": {"reception": cfg["reception"], "sorting": cfg["sorting"]},
                   "case": cfg["case"], "optimized": sid in OPT, "problem": cfg["profile"]["problem"],
                   "trains": int(2 + load / 18), "wagons_idle": int(load * 1.4),
                   "dwell_min": dwell_model(load, cfg["cap_day"])})
    t = time.time() / 60.0
    secs = []
    for s in resolve_sections():
        la, lb = loads[s["a"]], loads[s["b"]]
        h = zlib.crc32(s["id"].encode()) % 628 / 100.0
        load = max(8, min(99, 0.6 * max(la, lb) + 0.4 * min(la, lb) + 6 * math.sin(t / 2.5 + h)))
        s["load"] = round(load, 1)
        s["status"] = status_of(load)
        s["pairs"] = round(s["cap"] * load / 100.0, 1)
        s["queue"] = int(load / 22)
        secs.append(s)
    kpi = dict(KPI)
    kpi["index"] = min(97, 88 + 3 * len(OPT))
    kpi["red_nodes"] = sum(1 for x in st if x["status"] == "red")
    return {"stations": st, "sections": secs, "kpi": kpi, "ts": time.time()}


@app.get("/api/stations/{sid}")
def station(sid: str):
    _st(sid)
    cfg = STATIONS[sid]
    return {"id": sid, "meta": {k: cfg[k] for k in ("tier", "names", "lat", "lon", "case", "cap_day", "reception", "sorting", "profile")},
            "load": current_load(sid), "layout": LAYOUTS[sid], "safety": SAFETY[sid].snapshot()}


@app.post("/api/station/open")
def station_open(req: StationReq):
    """Сброс динамического состояния станции при открытии двойника."""
    sid = _st(req.station_id)
    SAFETY[sid] = SafetyState(sid, LAYOUTS[sid], STATIONS[sid]["case"])
    DYN[sid].reset()
    return {"ok": True}


@app.post("/api/station/{sid}/telemetry")
def telemetry(sid: str, tel: Dict[str, Any]):
    return DYN[_st(sid)].ingest(tel)


@app.post("/api/routes/check")
def routes_check(req: RouteCheckReq):
    sid = _st(req.station_id)
    out = []
    for rid in req.request:
        rs = route_conflicts(LAYOUTS[sid], rid, req.active, req.failed_switches, req.blocked_tracks)
        out.append({"route": rid, "allowed": not rs, "reasons": rs})
    return {"results": out}


@app.post("/api/disruption/replan")
def disruption(req: DisruptReq):
    sid = _st(req.station_id)
    if req.kind not in KINDS:
        raise HTTPException(400, "unknown disruption kind")
    return replan(sid, LAYOUTS[sid], req.kind, req.params, req.locos, req.crews, req.now_min)


@app.post("/api/optimize")
def api_optimize(req: OptReq):
    _st(req.station_id)
    res = optimize(req.station_id, LAYOUTS[req.station_id], req.crews)
    if req.apply:
        OPT[req.station_id] = res
    return res


@app.post("/api/optimize/reset")
def optimize_reset(req: StationReq):
    OPT.pop(req.station_id, None)
    return {"ok": True}


@app.get("/api/forecast/{sid}")
def api_forecast(sid: str):
    _st(sid)
    gain = OPT[sid]["savings"]["throughput_gain_pct"] if sid in OPT else 0.0
    f = make_forecast(STATIONS[sid], current_load(sid), gain)
    f["station_id"] = sid
    f["optimized"] = sid in OPT
    return f


@app.get("/api/safety/state/{sid}")
def safety_state(sid: str):
    return SAFETY[_st(sid)].snapshot()


@app.post("/api/safety/shoe/inject")
def shoe_inject(req: ShoeReq):
    return SAFETY[_st(req.station_id)].inject_shoe(req.shoe_id, req.track)


@app.post("/api/safety/shoe/remove")
def shoe_remove(req: ShoeReq):
    return {"removed": SAFETY[_st(req.station_id)].remove_shoe(req.shoe_id)}


@app.post("/api/safety/unsecured/inject")
def unsecured_inject(req: DepReq):
    return SAFETY[_st(req.station_id)].inject_unsecured(req.track)


@app.post("/api/safety/unsecured/secure")
def unsecured_secure(req: DepReq):
    ok = SAFETY[_st(req.station_id)].secure(req.track)
    if ok:
        SAFETY[req.station_id]._blocked = {k for k in SAFETY[req.station_id]._blocked if k[0] != req.track}
    return {"secured": ok}


@app.post("/api/safety/departure-check")
def departure_check(req: DepReq):
    res = SAFETY[_st(req.station_id)].departure_check(req.track)
    if not res["allowed"] and res.get("new_block"):
        KPI["prevented_count"] += 1
    return res


@app.post("/api/safety/scenario/hostile-route")
def hostile(req: StationReq):
    res = SAFETY[_st(req.station_id)].hostile_route_scenario()
    KPI["prevented_count"] += res["prevented"]
    return res


@app.post("/api/safety/reset")
def safety_reset(req: StationReq):
    sid = _st(req.station_id)
    SAFETY[sid] = SafetyState(sid, LAYOUTS[sid], STATIONS[sid]["case"])
    return SAFETY[sid].snapshot()


@app.get("/api/safety/log")
def safety_log(limit: int = 50):
    return {"events": list(LOG)[:limit]}


@app.get("/")
def index():
    return FileResponse(FRONT / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=str(FRONT)), name="static")
