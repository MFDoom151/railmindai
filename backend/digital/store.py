"""Хранилище краткосрочной истории (SQLite): снимки состояния, события (сбои, конфликты, смена плана), планы.
Запись — в фоновом потоке (не блокирует цикл реального времени), хранение — retention_h часов (по умолчанию 72)."""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, station TEXT, wall REAL, sim REAL, score REAL, payload TEXT);
CREATE INDEX IF NOT EXISTS ix_snap ON snapshots(station, wall);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, station TEXT, wall REAL, sim REAL, type TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS ix_ev ON events(station, wall);
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY, station TEXT, wall REAL, sim REAL, version INTEGER, label TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS ix_plan ON plans(station, wall);
"""


class Store:
    def __init__(self, path: str, retention_h: float = 72):
        p = Path(path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(p)
        self.retention = retention_h * 3600
        self.q: "queue.Queue" = queue.Queue(maxsize=20000)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.dropped = 0
        self.written = 0
        self.t = threading.Thread(target=self._loop, name="store-writer", daemon=True)
        self.t.start()
        self._last_prune = 0.0

    # ------------------------------------------------------------------------------------------------------------
    def _put(self, item):
        try:
            self.q.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    def snapshot(self, station, wall, sim, score, payload: dict):
        self._put(("INSERT INTO snapshots(station,wall,sim,score,payload) VALUES(?,?,?,?,?)", (station, wall, sim, score, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))))

    def event(self, station, wall, sim, etype, payload: dict):
        self._put(("INSERT INTO events(station,wall,sim,type,payload) VALUES(?,?,?,?,?)", (station, wall, sim, etype, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))))

    def plan(self, station, wall, sim, version, label, payload: dict):
        self._put(("INSERT INTO plans(station,wall,sim,version,label,payload) VALUES(?,?,?,?,?,?)", (station, wall, sim, version, label, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))))

    def _loop(self):
        while not self.stop.is_set() or not self.q.empty():
            batch = []
            try:
                batch.append(self.q.get(timeout=1.0))
                while len(batch) < 500:
                    batch.append(self.q.get_nowait())
            except queue.Empty:
                pass
            if batch:
                try:
                    with self.lock:
                        for sql, args in batch:
                            self.db.execute(sql, args)
                        self.db.commit()
                    self.written += len(batch)
                except Exception:                                  # pragma: no cover
                    pass
            if time.time() - self._last_prune > 300:
                self.prune()

    def flush(self, timeout=3.0):
        t0 = time.time()
        while not self.q.empty() and time.time() - t0 < timeout:
            time.sleep(0.05)
        time.sleep(0.1)

    def prune(self):
        self._last_prune = time.time()
        cut = time.time() - self.retention
        with self.lock:
            for tb in ("snapshots", "events", "plans"):
                self.db.execute(f"DELETE FROM {tb} WHERE wall < ?", (cut,))
            self.db.commit()

    # ------------------------------------------------------------------------------------------------------------
    def snapshots(self, station, since: float, until: Optional[float] = None, limit: int = 2000) -> List[dict]:
        until = until or time.time() + 1
        with self.lock:
            rows = self.db.execute("SELECT wall,sim,score,payload FROM snapshots WHERE station=? AND wall BETWEEN ? AND ? ORDER BY wall DESC LIMIT ?", (station, since, until, limit)).fetchall()
        return [{"wall": r[0], "sim": r[1], "score": r[2], "frame": json.loads(r[3])} for r in reversed(rows)]

    def events(self, station, since: float = 0, limit: int = 500, types: Optional[List[str]] = None) -> List[dict]:
        with self.lock:
            rows = self.db.execute("SELECT wall,sim,type,payload FROM events WHERE station=? AND wall>=? ORDER BY wall DESC LIMIT ?", (station, since, limit)).fetchall()
        out = [{"wall": r[0], "sim": r[1], "type": r[2], "payload": json.loads(r[3])} for r in reversed(rows)]
        return [e for e in out if not types or e["type"] in types]

    def plans(self, station, limit=50) -> List[dict]:
        with self.lock:
            rows = self.db.execute("SELECT wall,sim,version,label,payload FROM plans WHERE station=? ORDER BY wall DESC LIMIT ?", (station, limit)).fetchall()
        return [{"wall": r[0], "sim": r[1], "version": r[2], "label": r[3], "summary": json.loads(r[4])} for r in rows]

    def counts(self) -> dict:
        with self.lock:
            return {tb: self.db.execute(f"SELECT COUNT(*) FROM {tb}").fetchone()[0] for tb in ("snapshots", "events", "plans")}

    def close(self):
        self.stop.set()
        self.t.join(timeout=3)
        with self.lock:
            self.db.close()
