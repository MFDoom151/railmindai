"""Конфигурация «Цифровой станции»: YAML + переопределение + проверка (pydantic). Горячая перезагрузка без перекомпиляции."""
from __future__ import annotations

import copy
import os
import threading
from pathlib import Path
from typing import Dict, List

import yaml
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config" / "default.yaml"


class Noise(BaseModel):
    dup_rate: float = Field(0.03, ge=0, le=0.5)
    invalid_rate: float = Field(0.01, ge=0, le=0.5)
    reorder_rate: float = Field(0.05, ge=0, le=0.5)
    drop_rate: float = Field(0.01, ge=0, le=0.5)
    jitter_m: float = Field(3.0, ge=0, le=50)


class SimCfg(BaseModel):
    tick_hz: float = Field(2.0, ge=1, le=20)
    time_scale: float = Field(1.0, gt=0, le=20)
    horizon_min: int = Field(480, ge=120, le=1440)
    seed: int = 42
    noise: Noise = Noise()


class IngestCfg(BaseModel):
    ema_alpha: float = Field(0.55, gt=0, le=1)
    reorder_window_ms: int = Field(400, ge=0, le=5000)
    dedup_window: int = Field(8000, ge=100)
    stale_after_s: float = Field(4.0, gt=0)


class IndexCfg(BaseModel):
    weights: Dict[str, float]
    thresholds: Dict[str, float]
    letters: Dict[str, float]
    params: Dict[str, float]

    @field_validator("weights")
    @classmethod
    def _w(cls, v):
        need = {"throughput", "avg_dev", "max_dev", "track_load", "conflicts", "queue_outside", "loco_idle", "crew_idle"}
        if set(v) != need:
            raise ValueError(f"weights must have exactly: {sorted(need)}")
        if any(x < 0 for x in v.values()) or sum(v.values()) <= 0:
            raise ValueError("weights must be non-negative with positive sum")
        return v

    @field_validator("thresholds")
    @classmethod
    def _t(cls, v):
        if not (0 <= v.get("attention", -1) < v.get("norm", -1) <= 100):
            raise ValueError("thresholds: need 0 <= attention < norm <= 100")
        return v


class PlannerCfg(BaseModel):
    engine: str = Field("cpsat", pattern="^(cpsat|heuristic)$")
    time_limit_s: float = Field(1.5, gt=0.05, le=30)
    workers: int = Field(4, ge=1, le=32)
    horizon_min: int = Field(480, ge=120, le=1440)
    headway_min: int = Field(2, ge=0, le=15)
    route_in_min: int = Field(3, ge=1, le=15)
    route_out_min: int = Field(3, ge=1, le=15)
    shunt_move_min: int = Field(4, ge=1, le=20)
    readiness_min: int = Field(5, ge=0, le=30)
    w_late: float = Field(1.0, ge=0)
    w_wait: float = Field(0.6, ge=0)
    w_change: float = Field(3.0, ge=0)
    priority_weights: Dict[int, float]
    freight_on_platform: bool = False
    auto_apply: bool = False


class ConflictsCfg(BaseModel):
    late_warn_min: float = 8
    late_crit_min: float = 20
    eta_replan_min: float = 4


class StorageCfg(BaseModel):
    history_interval_s: float = Field(5, ge=0.5)
    retention_h: float = Field(72, ge=1, le=720)
    db_path: str = "data/railtwin.db"
    replay_buffer_s: int = Field(900, ge=60, le=3600)


class Config(BaseModel):
    sim: SimCfg = SimCfg()
    ingest: IngestCfg = IngestCfg()
    index: IndexCfg
    planner: PlannerCfg
    conflicts: ConflictsCfg = ConflictsCfg()
    storage: StorageCfg = StorageCfg()
    platforms: Dict[str, int] = {"default": 1}
    maintenance: Dict[str, List[dict]] = {"default": []}


def _deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class ConfigStore:
    """Хранит действующую конфигурацию; override-файл накладывается поверх default.yaml."""

    def __init__(self):
        self._lock = threading.RLock()
        self.default_path = Path(os.environ.get("RAILTWIN_CONFIG", DEFAULT_PATH))
        self.override_path = Path(os.environ.get("RAILTWIN_CONFIG_OVERRIDE", ROOT / "data" / "config.override.yaml"))
        self.version = 0
        self._listeners = []
        self.cfg = self._load()

    def _read(self, p: Path) -> dict:
        if not p.exists():
            return {}
        with p.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def _env_patch() -> dict:
        """RAILTWIN__planner__workers=2  →  {"planner": {"workers": 2}} (удобно для хостинга без правки файлов)."""
        patch: dict = {}
        for k, v in os.environ.items():
            if not k.startswith("RAILTWIN__"):
                continue
            path = k[len("RAILTWIN__"):].lower().split("__")   # на Windows os.environ приводит имена к верхнему регистру
            try:
                val = yaml.safe_load(v)
            except Exception:
                val = v
            d = patch
            for part in path[:-1]:
                d = d.setdefault(part, {})
            d[path[-1]] = val
        return patch

    def _load(self) -> Config:
        data = _deep_merge(_deep_merge(self._read(self.default_path), self._read(self.override_path)), self._env_patch())
        return Config.model_validate(data)

    def on_change(self, fn):
        self._listeners.append(fn)

    def reload(self) -> Config:
        with self._lock:
            self.cfg = self._load()
            self.version += 1
        for fn in self._listeners:
            fn(self.cfg)
        return self.cfg

    def update(self, patch: dict) -> Config:
        """Проверить и сохранить изменения. Бросает ValueError/ValidationError, если конфигурация некорректна."""
        with self._lock:
            current = self._read(self.override_path)
            merged_override = _deep_merge(current, patch)
            data = _deep_merge(_deep_merge(self._read(self.default_path), merged_override), self._env_patch())
            new_cfg = Config.model_validate(data)             # проверка до записи
            self.override_path.parent.mkdir(parents=True, exist_ok=True)
            with self.override_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(merged_override, f, allow_unicode=True, sort_keys=False)
            self.cfg = new_cfg
            self.version += 1
        for fn in self._listeners:
            fn(self.cfg)
        return self.cfg

    def reset_override(self):
        with self._lock:
            if self.override_path.exists():
                self.override_path.unlink()
        return self.reload()


STORE = ConfigStore()


def get() -> Config:
    return STORE.cfg
