"""Внешний «датчик»: шлёт на /ws/v1/ingest сырые события со станции (с дублями, мусором и перепутанным порядком) и печатает,
как их нормализует сервер. Демонстрирует приём данных по WebSocket независимо от встроенного симулятора.

    python tools/ws_client_demo.py --station otar --seconds 15 [--user dispatcher --password dispatcher]
"""
import argparse
import asyncio
import json
import math
import random
import time
import urllib.request
import sys

import websockets

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def login(base, user, pw):
    req = urllib.request.Request(f"{base}/api/v1/auth/login", data=json.dumps({"username": user, "password": pw}).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())["token"]


async def main(a):
    base = f"http://{a.host}:{a.port}"
    tok = login(base, a.user, a.password)
    rnd = random.Random(7)
    seq = 0
    async with websockets.connect(f"ws://{a.host}:{a.port}/ws/v1/ingest?station={a.station}&token={tok}") as ws:
        now_min = 12 * 60.0
        sched = {"id": "Х-9001", "type": "freight", "pr": 2, "wagons": 14, "arr": now_min + 5, "dep": now_min + 95,
                 "ops": [{"kind": "INSPECT", "dur": 10}], "dwell_min": 0, "cargo": "box"}
        seq += 1
        await ws.send(json.dumps([{"kind": "train_sched", "entity": "Х-9001", "seq": seq, "ts": now_min, "p": sched, "src": "ext"}]))
        t_end = time.time() + a.seconds
        sent = 0
        while time.time() < t_end:
            batch = []
            for _ in range(10):
                seq += 1
                pos = 100 + 40 * math.sin(seq / 7) + rnd.gauss(0, 3)
                ev = {"kind": "train_pos", "entity": "Х-9001", "seq": seq, "ts": now_min + seq * 0.05, "src": "ext",
                      "p": {"phase": "on_track", "track": 1, "prog": 1.0, "pos_m": pos, "speed": abs(rnd.gauss(0, 1))}}
                r = rnd.random()
                if r < 0.10:
                    batch.append(dict(ev))                                                   # дубликат
                elif r < 0.18:
                    ev["p"] = {**ev["p"], "pos_m": -10}                                      # мусор
                elif r < 0.25:
                    ev["seq"] -= 1000                                                        # «старое» событие
                    ev["ts"] -= 50
                batch.append(ev)
            rnd.shuffle(batch)                                                               # переупорядочивание
            await ws.send(json.dumps(batch))
            sent += len(batch)
            ack = json.loads(await ws.recv())
            print(f"отправлено {sent:4d} | ack {ack}")
            await asyncio.sleep(0.5)
    print("готово: недопустимые события отброшены, дубли удалены, остальное применено к состоянию станции")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--station", default="otar")
    ap.add_argument("--seconds", type=float, default=12)
    ap.add_argument("--user", default="dispatcher")
    ap.add_argument("--password", default="dispatcher")
    asyncio.run(main(ap.parse_args()))
