"""Генератор расписания станции (детерминированный): грузовые поезда с переработкой, пассажирские (платформы), транзит.
Нагрузка и состав потока определяются профилем станции (stations.py) и числом путей. Все данные — синтетические."""
from __future__ import annotations

import random
import zlib
from typing import List

from stations import STATIONS

from .infra import Infra
from .models import OpSpec, TrainSched

CARGO_RU = {"box": "крытые", "tank": "цистерны", "gondola": "полувагоны", "platform": "платформы", "container": "контейнеры"}


class Timetable:
    def __init__(self, station_id: str, infra: Infra, seed: int):
        self.sid = station_id
        self.infra = infra
        self.prof = STATIONS[station_id]["profile"]
        self.base_load = STATIONS[station_id]["base_load"]
        self.rng = random.Random(zlib.crc32(station_id.encode()) ^ seed)
        self.n_f = self.n_p = self.n_t = 0
        self.next_f = self.next_p = self.next_t = None
        self.horizon_to = None
        cap = [t.cap_wagons for t in infra.tracks.values() if t.kind == "reception" and not t.platform] or [20]
        self.max_wag = max(10, min(26, max(cap)))
        R = max(1.0, len(infra.reception) - 0.5 * len(infra.platforms))
        util = self.base_load / 100 * 1.0
        self.dwell_h = 1.7
        self.rate_f = max(0.6, R * util / self.dwell_h)               # грузовых в час
        self.plat = len(infra.platforms)

    # -- генераторы отдельных поездов --
    def _freight(self, arr: float, burst=False) -> TrainSched:
        r = self.rng
        self.n_f += 1
        w = r.randint(12, self.max_wag)
        kinds = r.choices(["full", "pod", "ros", "insp"], [0.5, 0.2, 0.1, 0.2])[0]
        ops = [OpSpec("INSPECT", int(8 + 0.3 * w))]
        if kinds in ("full", "ros"):
            ops.append(OpSpec("ROSPUSK", int(12 + 0.7 * w + r.randint(-2, 3))))
        if kinds in ("full", "pod"):
            ops.append(OpSpec("PODFORM", int(10 + 0.5 * w + r.randint(-2, 3))))
        pr = r.choices([1, 2, 3], [0.15, 0.65, 0.2])[0]
        cargo = r.choices(list(self.prof["cargo"].keys()), list(self.prof["cargo"].values()))[0]
        total = sum(o.dur for o in ops) + 5
        slack = r.randint(18, 40)
        dep = arr + total + slack + (10 if burst else 0)
        return TrainSched(f"Г-{2000 + self.n_f * 7 + r.randint(0, 6)}", "freight", pr, w, arr, dep, ops, 0, cargo)

    def _passenger(self, arr: float) -> TrainSched:
        r = self.rng
        self.n_p += 1
        dwell = r.randint(7, 15)
        return TrainSched(f"П-{10 + self.n_p * 3:03d}", "passenger", 5, r.randint(8, 16), arr, arr + dwell, [], dwell, "")

    def _transit(self, arr: float) -> TrainSched:
        self.n_t += 1
        return TrainSched(f"Т-{3300 + self.n_t * 11}", "transit", 4, self.rng.randint(30, 50), arr, arr + 3, [], 0, "")

    def extra_burst(self, now: float, n: int) -> List[TrainSched]:
        out, t = [], now + 8
        for _ in range(n):
            out.append(self._freight(t, burst=True))
            t += self.rng.randint(3, 7)
        return out

    def extend(self, start: float, until: float) -> List[TrainSched]:
        """Добавить поезда на интервал (start, until]. Вызывается повторно по мере движения времени."""
        r = self.rng
        out: List[TrainSched] = []
        if self.next_f is None:
            self.next_f = start + r.uniform(0, 30)
            self.next_p = start + r.uniform(20, 70) if self.plat else None
            self.next_t = start + r.uniform(10, 40)
        while self.next_f <= until:
            out.append(self._freight(self.next_f))
            self.next_f += max(8.0, 60.0 / self.rate_f * r.uniform(0.55, 1.45))
        if self.plat:
            gap = 110 / self.plat
            while self.next_p <= until:
                out.append(self._passenger(self.next_p))
                self.next_p += gap * r.uniform(0.8, 1.2)
        while self.next_t <= until:
            out.append(self._transit(self.next_t))
            self.next_t += r.uniform(45, 75)
        out.sort(key=lambda t: t.arr)
        return out
