"""Нагрузочный сценарий: N подписчиков WebSocket + повторные всплески нештатных ситуаций (x5…x10 одновременно).
Измеряет частоту кадров у клиентов, максимальный разрыв между кадрами, время до первого ответа (быстрый вариант плана)
и до финальных альтернатив, а также приём всплеска сервером.

    python tools/load_test.py --station almaty1 --clients 20 --bursts 3 --multiplier 10
Выходной код 1, если нарушены целевые показатели (кадры ≥ 1 Гц, быстрый ответ < 500 мс, финал < 6 с).
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.request

import websockets

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def call(base, path, body=None, token=None, method="POST"):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    return json.loads(urllib.request.urlopen(req).read())


async def client(i, a, stats, stop):
    gaps, last, n = [], None, 0
    async with websockets.connect(f"ws://{a.host}:{a.port}/ws/v1/stream?station={a.station}&backfill=30", max_size=2 ** 24) as ws:
        while not stop.is_set():
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            except asyncio.TimeoutError:
                gaps.append(3.0)
                continue
            now = time.time()
            if m["type"] == "state":
                n += 1
                if last is not None:
                    gaps.append(now - last)
                last = now
            if i == 0 and m["type"] == "alternatives":
                stats["alt_msgs"].append((now, m["stage"], m.get("t_quick_ms"), m.get("t_total_ms")))
    stats["frames"].append(n)
    stats["gaps"].append(max(gaps) if gaps else 0)


async def main(a):
    base = f"http://{a.host}:{a.port}"
    tok = call(base, "/api/v1/auth/login", {"username": a.user, "password": a.password})["token"]
    stats = {"frames": [], "gaps": [], "alt_msgs": []}
    stop = asyncio.Event()
    tasks = [asyncio.create_task(client(i, a, stats, stop)) for i in range(a.clients)]
    await asyncio.sleep(3)
    t_start = time.time()
    results = []
    loop = asyncio.get_event_loop()
    for b in range(a.bursts):
        t0 = time.time()
        r = await loop.run_in_executor(None, lambda: call(base, f"/api/v1/stations/{a.station}/stress", {"multiplier": a.multiplier}, tok))
        accepted = (time.time() - t0) * 1000
        t_post = t0                                     # отсчёт от момента отправки запроса
        for _ in range(200):
            await asyncio.sleep(0.05)
            fin = [x for x in stats["alt_msgs"] if x[0] >= t_post and x[1] == "final"]
            quick = [x for x in stats["alt_msgs"] if x[0] >= t_post and x[1] == "quick"]
            if fin:
                break
        results.append({"accepted_ms": accepted, "server_accept_ms": r["accepted_ms"], "quick_seen_ms": (quick[0][0] - t_post) * 1000 if quick else None,
                        "final_seen_ms": (fin[0][0] - t_post) * 1000 if fin else None, "injected": r["injected"]})
        await asyncio.sleep(2.0)
    dur = time.time() - t_start + 3
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    fps = [f / dur for f in stats["frames"]]
    print(f"клиентов: {a.clients} · всплесков: {a.bursts} × {a.multiplier} · длительность {dur:.1f} с")
    print(f"кадры/с на клиента: min {min(fps):.2f} / avg {statistics.mean(fps):.2f}; макс. разрыв между кадрами: {max(stats['gaps']):.2f} с")
    for i, r in enumerate(results, 1):
        print(f"всплеск {i}: внесено {r['injected']} | HTTP {r['accepted_ms']:.0f} мс (сервер {r['server_accept_ms']} мс) | быстрый план у клиента через "
              f"{(r['quick_seen_ms'] or -1):.0f} мс | финальные альтернативы через {(r['final_seen_ms'] or -1):.0f} мс")
    ok = min(fps) >= 1.0 and all(r["quick_seen_ms"] is not None and r["quick_seen_ms"] < 500 and r["final_seen_ms"] is not None and r["final_seen_ms"] < 6000 for r in results)
    print("РЕЗУЛЬТАТ:", "OK" if ok else "ЦЕЛИ НЕ ДОСТИГНУТЫ")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--station", default="almaty1")
    ap.add_argument("--clients", type=int, default=10)
    ap.add_argument("--bursts", type=int, default=3)
    ap.add_argument("--multiplier", type=int, default=10)
    ap.add_argument("--user", default="dispatcher")
    ap.add_argument("--password", default="dispatcher")
    asyncio.run(main(ap.parse_args()))
