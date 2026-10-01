"""REST + WebSocket + SSE API «Цифровой станции» (OpenAPI: /docs).

Онлайн-поток:  WS /ws/v1/stream?station=…  (или SSE /api/v1/stations/{id}/stream)
Приём данных:  WS /ws/v1/ingest?station=…  и  POST /api/v1/ingest  (события датчиков/симулятора, нужна роль dispatcher)
История, конфигурация, отчёты, аутентификация — REST."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from stations import STATIONS

from . import config as cfgmod
from . import reports
from .auth import AUTH, RANK, require
from .efficiency import formula_doc
from .metrics import METRICS
from .service import HUB, ClientQueue

router = APIRouter()


class LoginIn(BaseModel):
    username: str
    password: str


class DisruptionIn(BaseModel):
    kind: str = Field(..., description="train_delay | track_close | switch_fail | loco_down | crew_absent | power_fail | burst")
    params: Dict[str, Any] = {}


class StressIn(BaseModel):
    multiplier: int = Field(5, ge=1, le=20)


class ApplyIn(BaseModel):
    alt_id: str


class ResolveIn(BaseModel):
    code: str
    params: Dict[str, Any] = {}


class HandoverIn(BaseModel):
    items: Dict[str, bool] = {}
    shoes_log: Optional[int] = Field(None, ge=0, le=999)
    shoes_fact: Optional[int] = Field(None, ge=0, le=999)
    note: str = Field("", max_length=500)
    night: bool = False
    user: str = Field("", max_length=64)


HANDOVER_ITEMS = ("shoes", "switches", "closures", "unsecured", "orders")
HANDOVER_NIGHT_ITEMS = ("night_report",)


class SimCtl(BaseModel):
    paused: Optional[bool] = None
    scale: Optional[float] = Field(None, gt=0, le=20)
    auto_apply: Optional[bool] = None


class ConfigPatch(BaseModel):
    patch: Dict[str, Any]


class IngestIn(BaseModel):
    station: str
    events: List[Dict[str, Any]]


def _sid(sid: str) -> str:
    if sid not in STATIONS:
        raise HTTPException(404, "unknown station")
    return sid


# ---------------------------------------------------------------------------------------------------------- служебные
@router.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "stations_active": list(HUB.rt), "store": HUB.store.counts() if HUB.store else None, "uptime_s": round(time.time() - METRICS.started)}


@router.get("/ready", tags=["ops"])
async def ready():
    return {"ready": HUB.store is not None}


@router.get("/metrics", tags=["ops"], response_class=PlainTextResponse)
async def metrics():
    for sid, r in HUB.rt.items():
        s = r.ingest.stats()
        for k in ("received", "applied", "invalid", "duplicates", "stale"):
            METRICS.set(f"railmind_ingest_{k}", s.get(k, 0), station=sid)
        METRICS.set("railmind_ws_clients", len(r.clients), station=sid)
        METRICS.set("railmind_replanning", int(r.replanning), station=sid)
        METRICS.set("railmind_ingest_latency_ms_p95", s.get("latency_ms_p95", 0), station=sid)
        METRICS.set("railmind_ws_dropped_frames", sum(c.dropped for c in r.clients), station=sid)
    if HUB.store:
        METRICS.set("railmind_store_written", HUB.store.written)
        METRICS.set("railmind_store_dropped", HUB.store.dropped)
    return METRICS.render()


# ---------------------------------------------------------------------------------------------------------- auth
@router.post("/api/v1/auth/login", tags=["auth"])
async def login(body: LoginIn, request: Request):
    tok = AUTH.login(body.username, body.password, request.client.host if request.client else "-")
    if not tok:
        raise HTTPException(401, "bad credentials")
    d = AUTH.verify(tok)
    return {"token": tok, "role": d["role"], "user": d["sub"], "expires": d["exp"]}


@router.get("/api/v1/auth/info", tags=["auth"])
async def auth_info(request: Request):
    role = AUTH.role_of(request)
    out = {"role": role, "dev_mode": AUTH.demo, "open_demo": AUTH.open_demo}
    if AUTH.demo:
        out["demo_users"] = [{"user": "dispatcher", "password": "dispatcher", "role": "dispatcher"}, {"user": "admin", "password": "admin", "role": "admin"}]
    return out


# ---------------------------------------------------------------------------------------------------------- станции
@router.get("/api/v1/stations", tags=["stations"])
async def stations():
    return [{"id": sid, "names": s["names"], "tier": s["tier"], "problem": s["profile"]["problem"],
             "tracks": {"reception": s["reception"], "sorting": s["sorting"]}, "active": sid in HUB.rt} for sid, s in STATIONS.items()]


@router.get("/api/v1/stations/{sid}/snapshot", tags=["stations"])
async def snapshot(sid: str):
    r = await HUB.get(_sid(sid))
    return {"frame": r.build_frame(), "infra": r.infra.to_dict(), "plan": r.plan.to_dict() if r.plan else None, "conflicts": r.conflicts, "alternatives": r.alts,
            "index": r.index, "ingest": r.ingest.stats()}


@router.get("/api/v1/stations/{sid}/plan", tags=["planning"])
async def get_plan(sid: str):
    r = await HUB.get(_sid(sid))
    return r.plan.to_dict() if r.plan else None


@router.get("/api/v1/stations/{sid}/conflicts", tags=["planning"])
async def get_conflicts(sid: str):
    r = await HUB.get(_sid(sid))
    return {"count": len(r.conflicts), "list": r.conflicts}


@router.get("/api/v1/stations/{sid}/alternatives", tags=["planning"])
async def get_alts(sid: str):
    r = await HUB.get(_sid(sid))
    return r.alts


@router.get("/api/v1/stations/{sid}/index", tags=["index"])
async def get_index(sid: str):
    r = await HUB.get(_sid(sid))
    return {"index": r.index, "formula": formula_doc(r.cfg.index)}


@router.get("/api/v1/index/formula", tags=["index"])
async def index_formula():
    return formula_doc(cfgmod.get().index)


@router.post("/api/v1/stations/{sid}/plan/apply", tags=["planning"], dependencies=[require("dispatcher")])
async def apply_plan(sid: str, body: ApplyIn):
    r = await HUB.get(_sid(sid))
    if not r.apply_alternative(body.alt_id):
        raise HTTPException(404, "alternative not found (expired or unknown)")
    return {"applied": body.alt_id, "plan_version": r.plan_version}


@router.get("/api/v1/stations/{sid}/handover", tags=["safety"])
async def get_handover(sid: str):
    """Контекст сдачи дежурства: что принимает сменщик (текущие закрытия, конфликты, нештатные ситуации) и признак ночной смены."""
    r = await HUB.get(_sid(sid))
    now = r.state.now
    closures = [{"track": c["track"], "until": round(c["to"], 1), "reason": c.get("reason")} for c in r.sim.closures if c["to"] > now]
    return {"sim": round(now, 1), "night": 0 <= (int(now) % 1440) // 60 < 6, "closures": closures, "conflicts": len(r.conflicts),
            "disruptions": len(r.sim.log), "plan_version": r.plan_version,
            "items": list(HANDOVER_ITEMS), "night_items": list(HANDOVER_NIGHT_ITEMS)}


@router.post("/api/v1/stations/{sid}/handover", tags=["safety"], dependencies=[require("dispatcher")])
async def post_handover(sid: str, body: HandoverIn):
    """Подтверждение сдачи дежурства. Все пункты чек-листа обязательны; расхождение инвентарных номеров башмаков требует пояснения."""
    r = await HUB.get(_sid(sid))
    need = list(HANDOVER_ITEMS) + (list(HANDOVER_NIGHT_ITEMS) if body.night else [])
    missing = [k for k in need if not body.items.get(k)]
    if missing:
        raise HTTPException(422, {"error": "checklist_incomplete", "missing": missing})
    mismatch = body.shoes_log is not None and body.shoes_fact is not None and body.shoes_log != body.shoes_fact
    if mismatch and not body.note.strip():
        raise HTTPException(422, {"error": "shoes_mismatch", "log": body.shoes_log, "fact": body.shoes_fact})
    r.alert("warn" if mismatch else "ok", "HANDOVER", {"night": body.night, "shoes_log": body.shoes_log, "shoes_fact": body.shoes_fact,
                                                      "mismatch": mismatch, "note": body.note.strip(), "user": body.user or "dispatcher"})
    return {"ok": True, "mismatch": mismatch}


@router.post("/api/v1/stations/{sid}/resolve", tags=["planning"], dependencies=[require("dispatcher")])
async def resolve(sid: str, body: ResolveIn):
    r = await HUB.get(_sid(sid))
    if body.code == "REPLAN":
        r.request_replan("manual", alts=True)
        return {"ok": True, "code": "REPLAN"}
    return r.resolve(body.code, body.params)


@router.post("/api/v1/stations/{sid}/disruptions", tags=["disruptions"], dependencies=[require("dispatcher")])
async def post_disruption(sid: str, body: DisruptionIn, wait: bool = Query(False, description="дождаться расчёта альтернатив")):
    r = await HUB.get(_sid(sid))
    info = r.inject(body.kind, body.params)
    if "error" in info:
        raise HTTPException(422, info["error"])
    if wait:
        await _wait_alts(r)
        info["alternatives"] = r.alts
    return info


@router.post("/api/v1/stations/{sid}/stress", tags=["disruptions"], dependencies=[require("dispatcher")])
async def post_stress(sid: str, body: StressIn, wait: bool = False):
    r = await HUB.get(_sid(sid))
    t0 = time.perf_counter()
    out = r.stress(body.multiplier)
    res = {"injected": len(out), "disruptions": out, "accepted_ms": round((time.perf_counter() - t0) * 1000, 1)}
    if wait:
        await _wait_alts(r)
        res["alternatives"] = r.alts
    return res


async def _wait_alts(r, timeout=15.0):
    t0 = time.time()
    await asyncio.sleep(0.05)
    while time.time() - t0 < timeout:
        if r.alts and r.alts.get("stage") == "final" and not r.replanning and not r.pending:
            return
        await asyncio.sleep(0.05)


@router.post("/api/v1/stations/{sid}/sim", tags=["simulation"], dependencies=[require("dispatcher")])
async def sim_control(sid: str, body: SimCtl):
    r = await HUB.get(_sid(sid))
    if body.paused is not None:
        r.paused = body.paused
    if body.scale is not None:
        r.scale = body.scale
    if body.auto_apply is not None:
        r.auto_apply = body.auto_apply
    return {"paused": r.paused, "scale": r.scale, "auto_apply": r.auto_apply}


@router.get("/api/v1/stations/{sid}/replay", tags=["history"])
async def replay(sid: str, seconds: int = Query(300, ge=5, le=900), step: int = Query(2, ge=1, le=20)):
    r = await HUB.get(_sid(sid))
    return {"frames": r.replay(seconds, step)}


@router.get("/api/v1/stations/{sid}/history", tags=["history"])
async def history(sid: str, minutes: float = Query(60, ge=1, le=72 * 60), limit: int = Query(1500, le=5000)):
    _sid(sid)
    snaps = HUB.store.snapshots(sid, time.time() - minutes * 60, None, limit)
    return {"count": len(snaps), "snapshots": [{"wall": s["wall"], "sim": s["sim"], "score": s["score"], "conflicts": s["frame"].get("conflicts"), "plan_v": s["frame"].get("plan_v")} for s in snaps]}


@router.get("/api/v1/stations/{sid}/events", tags=["history"])
async def events(sid: str, minutes: float = Query(60, ge=1, le=72 * 60), limit: int = Query(300, le=2000)):
    _sid(sid)
    return {"events": HUB.store.events(sid, time.time() - minutes * 60, limit)}


@router.get("/api/v1/stations/{sid}/plans", tags=["history"])
async def plans(sid: str, limit: int = 30):
    _sid(sid)
    return {"plans": HUB.store.plans(sid, limit)}


# ---------------------------------------------------------------------------------------------------------- отчёты
@router.get("/api/v1/stations/{sid}/report.csv", tags=["reports"])
async def report_csv(sid: str, lang: str = "ru"):
    r = await HUB.get(_sid(sid))
    return Response(reports.build_csv(r, lang).encode("utf-8"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="station_{sid}_report.csv"'})


@router.get("/api/v1/stations/{sid}/report.pdf", tags=["reports"])
async def report_pdf(sid: str, lang: str = "ru"):
    r = await HUB.get(_sid(sid))
    data = await asyncio.get_event_loop().run_in_executor(None, reports.build_pdf, r, lang)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="station_{sid}_report.pdf"'})


# ---------------------------------------------------------------------------------------------------------- конфигурация
@router.get("/api/v1/config", tags=["config"])
async def get_config():
    c = cfgmod.get()
    return {"version": cfgmod.STORE.version, "config": json.loads(c.model_dump_json())}


@router.put("/api/v1/config", tags=["config"], dependencies=[require("admin")])
async def put_config(body: ConfigPatch):
    try:
        c = cfgmod.STORE.update(body.patch)
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json()))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"version": cfgmod.STORE.version, "config": json.loads(c.model_dump_json())}


@router.post("/api/v1/config/reload", tags=["config"], dependencies=[require("admin")])
async def reload_config():
    cfgmod.STORE.reload()
    return {"version": cfgmod.STORE.version}


@router.delete("/api/v1/config/override", tags=["config"], dependencies=[require("admin")])
async def reset_config():
    cfgmod.STORE.reset_override()
    return {"version": cfgmod.STORE.version}


# ---------------------------------------------------------------------------------------------------------- приём данных
@router.post("/api/v1/ingest", tags=["ingest"], dependencies=[require("dispatcher")])
async def ingest(body: IngestIn):
    r = await HUB.get(_sid(body.station))
    before = dict(r.ingest.counters)
    r.ingest.push(body.events)
    r.ingest.flush(force=True)
    return {"received": len(body.events), **{k: r.ingest.counters.get(k, 0) - before.get(k, 0) for k in ("applied", "invalid", "duplicates", "stale")}, "invalid_reasons": dict(r.ingest.invalid_reasons)}


@router.websocket("/ws/v1/ingest")
async def ws_ingest(ws: WebSocket, station: str):
    if station not in STATIONS or RANK[AUTH.role_of(ws)] < RANK["dispatcher"]:
        await ws.close(code=4401)
        return
    await ws.accept()
    r = await HUB.get(station)
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            evs = msg if isinstance(msg, list) else [msg]
            r.ingest.push(evs)
            r.ingest.flush(force=True)
            await ws.send_text(json.dumps({"ack": len(evs), "invalid": r.ingest.counters.get("invalid", 0), "dup": r.ingest.counters.get("duplicates", 0)}))
    except (WebSocketDisconnect, json.JSONDecodeError):
        return


# ---------------------------------------------------------------------------------------------------------- онлайн-поток
@router.websocket("/ws/v1/stream")
async def ws_stream(ws: WebSocket, station: str, backfill: int = 450):
    if station not in STATIONS:
        await ws.close(code=4404)
        return
    await ws.accept()
    r = await HUB.get(station)
    q = ClientQueue()
    r.clients.add(q)
    METRICS.inc("railmind_ws_connections_total", station=station)
    try:
        for m in r.hello(min(max(backfill, 0), 1800)):
            await ws.send_text(m)

        async def sender():
            try:
                while True:
                    try:
                        m = await asyncio.wait_for(q.get(), timeout=2.0)
                    except asyncio.TimeoutError:
                        m = json.dumps({"type": "hb", "t": time.time()})
                    r.last_client = time.time()
                    await ws.send_text(m)
            except (WebSocketDisconnect, RuntimeError):
                return

        async def receiver():
            try:
                while True:
                    txt = await ws.receive_text()
                    try:
                        d = json.loads(txt)
                    except json.JSONDecodeError:
                        continue
                    if d.get("cmd") == "ping":
                        q.put(json.dumps({"type": "pong", "t": d.get("t"), "srv": time.time()}))
            except (WebSocketDisconnect, RuntimeError):
                return

        done, pend = await asyncio.wait([asyncio.ensure_future(sender()), asyncio.ensure_future(receiver())], return_when=asyncio.FIRST_COMPLETED)
        for p in pend:
            p.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        r.clients.discard(q)
        r.last_client = time.time()


@router.get("/api/v1/stations/{sid}/stream", tags=["stream"])
async def sse_stream(sid: str, request: Request, backfill: int = 300):
    r = await HUB.get(_sid(sid))

    async def gen():
        q = ClientQueue()
        r.clients.add(q)
        try:
            for m in r.hello(min(backfill, 900)):
                yield f"data: {m}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    m = await asyncio.wait_for(q.get(), timeout=2.0)
                    yield f"data: {m}\n\n"
                except asyncio.TimeoutError:
                    yield ": hb\n\n"
                r.last_client = time.time()
        finally:
            r.clients.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
