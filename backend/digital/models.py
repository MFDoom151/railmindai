"""Общие модели «Цифровой станции»: фазы поезда, типы операций, расписание, план. Время — минуты модельных суток (float/int)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

PHASES = ("scheduled", "approach", "waiting", "entering", "on_track", "processing", "ready", "departing", "departed")
TRAIN_TYPES = ("freight", "passenger", "transit")
LOCO_OPS = ("ROSPUSK", "PODFORM")          # требуют локомотив + бригаду + горловину + сортировочный путь
OP_KINDS = ("INSPECT",) + LOCO_OPS
DAY = 1440


def hhmm(m: float) -> str:
    m = int(round(m)) % DAY
    return f"{m // 60:02d}:{m % 60:02d}"


@dataclass
class OpSpec:
    kind: str
    dur: int                      # минут


@dataclass
class TrainSched:
    """Запись расписания (как приходит из событий train_sched)."""
    id: str
    type: str
    priority: int
    wagons: int
    arr: float                    # плановое время прибытия
    dep: float                    # плановое время отправления
    ops: List[OpSpec] = field(default_factory=list)
    dwell_min: int = 0            # минимальная стоянка на пути (для пассажирских)
    cargo: str = ""
    name: str = ""

    def to_dict(self):
        return {"id": self.id, "type": self.type, "pr": self.priority, "wagons": self.wagons, "arr": self.arr, "dep": self.dep,
                "ops": [{"kind": o.kind, "dur": o.dur} for o in self.ops], "dwell_min": self.dwell_min, "cargo": self.cargo, "name": self.name}

    @staticmethod
    def from_dict(d):
        return TrainSched(d["id"], d["type"], int(d["pr"]), int(d["wagons"]), float(d["arr"]), float(d["dep"]),
                          [OpSpec(o["kind"], int(o["dur"])) for o in d.get("ops", [])], int(d.get("dwell_min", 0)), d.get("cargo", ""), d.get("name", ""))


@dataclass
class PlanOp:
    kind: str
    start: int
    end: int
    loco: Optional[str] = None
    sort_track: Optional[int] = None

    def to_dict(self):
        return {"kind": self.kind, "start": self.start, "end": self.end, "loco": self.loco, "sort_track": self.sort_track}


@dataclass
class PlanItem:
    train_id: str
    track: Optional[int]          # None для транзита (главный путь)
    arr: int                      # начало маршрута приёма
    dep: int                      # начало маршрута отправления
    eta: int
    sched_dep: int
    ops: List[PlanOp] = field(default_factory=list)
    transit: bool = False

    @property
    def wait_out(self):
        return max(0, self.arr - self.eta)

    @property
    def late(self):
        return max(0, self.dep - self.sched_dep)

    def to_dict(self):
        return {"id": self.train_id, "track": self.track, "arr": self.arr, "dep": self.dep, "eta": self.eta, "sched_dep": self.sched_dep,
                "wait": self.wait_out, "late": self.late, "transit": self.transit, "ops": [o.to_dict() for o in self.ops]}


@dataclass
class Plan:
    version: int
    created: float                # модельное время построения
    items: Dict[str, PlanItem]
    solver: dict = field(default_factory=dict)
    label: str = ""
    kpi: dict = field(default_factory=dict)

    def to_dict(self):
        return {"version": self.version, "created": self.created, "label": self.label, "solver": self.solver, "kpi": self.kpi,
                "items": [it.to_dict() for it in self.items.values()]}
