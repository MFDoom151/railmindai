"""Топология станции (JSON) — «лестничная» горловина: главный путь, приёмо-отправочные и сортировочные пути,
стрелки, светофоры, маршруты и секции. Координаты — условные единицы сцены (1 ед. ≈ 4.5 м)."""
import math

UNIT_M = 4.5
DZ = 4.2          # шаг между путями по Z
DX = 6.0          # шаг головок путей по X
LX0 = -80.0       # головка стрелки P0 (вход на «лестницу»)
MAIN_X = 150.0    # границы главного пути
DEPOT = {"x0": -142.0, "x1": -122.0, "z": -DZ, "merge_x": -112.0}


def build_layout(cfg: dict) -> dict:
    g = cfg.get("geom", {})
    DZ = g.get("dz", globals()["DZ"])
    DX = g.get("dx", globals()["DX"])
    LX0 = g.get("lx0", globals()["LX0"])
    kinds = ["reception"] * cfg["reception"] + ["sorting"] * cfg["sorting"]
    n = len(kinds)
    tracks = [{"id": "T0", "idx": 0, "kind": "main", "z": 0.0, "x0": -MAIN_X, "x1": MAIN_X}]
    for i, k in enumerate(kinds, start=1):
        hx = LX0 + DX * i
        tracks.append({"id": f"T{i}", "idx": i, "kind": k, "z": round(i * DZ, 2),
                       "x0": round(hx, 2), "x1": round(-hx, 2)})

    left_lead = [[round(LX0 + DX * k, 2), round(k * DZ, 2)] for k in range(0, n + 1)]
    right_lead = [[-x, z] for x, z in left_lead]

    switches = []
    for j in range(0, n):
        switches.append({"id": 2 * j + 1, "side": "L", "head": j, "x": left_lead[j][0], "z": left_lead[j][1], "pos": "N"})
        switches.append({"id": 2 * j + 2, "side": "R", "head": j, "x": right_lead[j][0], "z": right_lead[j][1], "pos": "N"})

    signals = [{"id": "Н", "kind": "entry", "x": -MAIN_X + 38, "z": -2.0, "track": 0, "state": "RED"}]
    for i in range(1, n + 1):
        signals.append({"id": f"Ч{i}", "kind": "exit", "x": round(-(LX0 + DX * i) - 3.5, 2),
                        "z": round(i * DZ - 1.7, 2), "track": i, "state": "RED"})

    sections = ["ML", "M", "MR"] + [f"LL{k}" for k in range(1, n + 1)] + [f"LR{k}" for k in range(1, n + 1)] \
        + [f"T{i}" for i in range(1, n + 1)]

    routes = {}
    for i in range(1, n + 1):
        sw = {1: "R"}
        for j in range(1, i):
            sw[2 * j + 1] = "N"
        if i < n:
            sw[2 * i + 1] = "R"
        routes[f"A{i}"] = {"id": f"A{i}", "type": "arrival", "track": i, "signal": "Н", "switches": sw,
                           "sections": ["ML"] + [f"LL{k}" for k in range(1, i + 1)] + [f"T{i}"]}
        sw = {2: "R"}
        for j in range(1, i):
            sw[2 * j + 2] = "N"
        if i < n:
            sw[2 * i + 2] = "R"
        routes[f"D{i}"] = {"id": f"D{i}", "type": "departure", "track": i, "signal": f"Ч{i}", "switches": sw,
                           "sections": [f"T{i}"] + [f"LR{k}" for k in range(i, 0, -1)] + ["MR"]}
    routes["X0"] = {"id": "X0", "type": "through", "track": 0, "signal": "Н", "switches": {1: "N", 2: "N"},
                    "sections": ["ML", "M", "MR"]}

    zmid = round(n * DZ / 2, 1)
    cameras = [
        {"id": "CAM-01", "zone": "neck_left", "x": -100.0, "y": 11.0, "z": -9.0, "tx": -66.0, "tz": round(zmid * 0.8, 1)},
        {"id": "CAM-02", "zone": "shunting", "x": 12.0, "y": 13.0, "z": -9.0, "tx": 6.0, "tz": zmid},
        {"id": "CAM-03", "zone": "neck_right", "x": 100.0, "y": 11.0, "z": -9.0, "tx": 66.0, "tz": round(zmid * 0.8, 1)},
        {"id": "CAM-04", "zone": "depot", "x": -118.0, "y": 9.0, "z": 5.0, "tx": -132.0, "tz": DEPOT["z"]},
    ]
    sensors = [{"id": f"SW-{s['id']}", "type": "switch", "ref": s["id"], "x": s["x"], "z": s["z"]} for s in switches]
    sensors += [{"id": f"FX-{t['idx']}", "type": "securing", "track": t["idx"]} for t in tracks if t["idx"] > 0]

    lead_step_m = round(math.hypot(DX, DZ) * UNIT_M, 1)
    return {
        "unit_m": UNIT_M, "dz": DZ, "dx": DX, "n_tracks": n, "lead_step_m": lead_step_m,
        "tracks": tracks, "left_lead": left_lead, "right_lead": right_lead,
        "main_x": MAIN_X, "depot": DEPOT,
        "switches": switches, "signals": signals, "sections": sections, "routes": routes,
        "cameras": cameras, "sensors": sensors,
    }
