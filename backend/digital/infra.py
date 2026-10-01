"""Цифровая модель инфраструктуры станции: пути, парки, платформы, горловины, ресурсы и окна ТО.
Геометрия берётся из топологии станции (layout.py), логика — из конфигурации."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from layout import UNIT_M, build_layout
from stations import STATIONS

from . import config as cfgmod
from .models import DAY

WAGON_M = 15.0


@dataclass
class Track:
    idx: int
    kind: str                     # reception | sorting
    park: str                     # П — парк приёма/отправления, С — сортировочный, Пл — пассажирский
    length_m: float
    cap_wagons: int
    platform: bool = False

    def to_dict(self):
        return {"idx": self.idx, "kind": self.kind, "park": self.park, "length_m": round(self.length_m), "cap": self.cap_wagons, "platform": self.platform}


@dataclass
class Infra:
    station_id: str
    names: dict
    tracks: Dict[int, Track]
    layout: dict
    locos: List[str]
    crews: int
    maintenance: List[Tuple[int, int, int]] = field(default_factory=list)   # (track, from_min_of_day, to_min_of_day)

    @property
    def reception(self):
        return [t.idx for t in self.tracks.values() if t.kind == "reception"]

    @property
    def sorting(self):
        return [t.idx for t in self.tracks.values() if t.kind == "sorting"]

    @property
    def platforms(self):
        return [t.idx for t in self.tracks.values() if t.platform]

    def closures(self, a: float, b: float) -> List[Tuple[int, int, int]]:
        """Окна ТО, развёрнутые на абсолютные минуты в интервале [a, b]."""
        out = []
        d0 = int(a // DAY) - 1
        for d in range(d0, int(b // DAY) + 2):
            for tr, f, t in self.maintenance:
                s, e = d * DAY + f, d * DAY + t
                if e > a and s < b:
                    out.append((tr, s, e))
        return out

    def to_dict(self):
        L = self.layout
        return {"station": self.station_id, "names": self.names, "tracks": [t.to_dict() for t in self.tracks.values()],
                "locos": self.locos, "crews": self.crews, "maintenance": [{"track": t, "from": f, "to": e} for t, f, e in self.maintenance],
                "geometry": {"tracks": L["tracks"], "left_lead": L["left_lead"], "right_lead": L["right_lead"], "depot": L["depot"],
                             "switches": [{"id": s["id"], "x": s["x"], "z": s["z"], "side": s["side"]} for s in L["switches"]],
                             "signals": L["signals"], "n": L["n_tracks"]}}


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def build_infra(station_id: str) -> Infra:
    st = STATIONS[station_id]
    L = build_layout(st)
    c = cfgmod.get()
    n_plat = c.platforms.get(station_id, c.platforms.get("default", 1))
    tracks: Dict[int, Track] = {}
    rec_seen = 0
    for t in L["tracks"]:
        if t["idx"] == 0:
            continue
        length = (t["x1"] - t["x0"]) * UNIT_M
        plat = t["kind"] == "reception" and rec_seen < n_plat
        if t["kind"] == "reception":
            rec_seen += 1
        park = "Пл" if plat else ("П" if t["kind"] == "reception" else "С")
        tracks[t["idx"]] = Track(t["idx"], t["kind"], park, length, int(length // WAGON_M), plat)
    d = st["profile"]["defaults"]
    mw = c.maintenance.get(station_id, c.maintenance.get("default", []))
    maint = [(int(m["track"]), _hhmm(m["from"]), _hhmm(m["to"])) for m in mw if int(m["track"]) in tracks]
    return Infra(station_id, st["names"], tracks, L, [f"ЧМЭ3-{i + 1}" for i in range(int(d["locos"]))], int(d["crews"]), maint)
