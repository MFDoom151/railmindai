"""Справочник узлов, ворот и станций КТЖ и магистральных участков.
Координаты станций — по OSM/Nominatim (для Алматы-1, Қарағанды, Ақтау, Петропавловска — приблизительные);
промежуточные точки магистралей схематичны и не являются геодезической трассой.

У каждой станции свой профиль (profile): «фирменная» проблема, стартовые параметры нагрузки, режим прибытия поездов,
структура грузов и окружение. Это делает станции разными по поведению и по сценарию нештатной ситуации."""

# problem — сценарий нештатной ситуации, который запускает демо-кнопка на этой станции
#   безопасность: shoe | hostile | unsecured
#   сбои:         switch_fail | switch_icing | false_occupancy | crew_absent | loco_fail | train_delay |
#                 track_full | border_hold | arrival_burst | power_fail
# arrivals: poisson | steady | bunched | burst;  cargo — веса типов вагонов; decor — окружение сцены.
STATIONS = {
    "almaty1": {"tier": "node", "lat": 43.3115, "lon": 76.9380, "names": {"ru": "Алматы-1", "kz": "Алматы-1", "en": "Almaty-1"},
                "reception": 4, "sorting": 3, "cap_day": 36, "base_load": 74, "phase": 0.2, "case": None,
                "geom": {"lx0": -88, "dx": 6.2, "dz": 4.2},
                "profile": {"problem": "track_full", "defaults": {"lambda": 3.4, "locos": 3, "crews": 3, "proc": 40}, "arrivals": "steady",
                            "cargo": {"box": 4, "container": 2, "gondola": 3, "tank": 1}, "decor": "mountains", "season": "summer"}},
    "shu": {"tier": "node", "lat": 43.6010, "lon": 73.7611, "names": {"ru": "Шу", "kz": "Шу", "en": "Shu"},
            "reception": 3, "sorting": 2, "cap_day": 30, "base_load": 60, "phase": 1.1, "case": None,
            "geom": {"lx0": -78, "dx": 6.0, "dz": 4.4},
            "profile": {"problem": "switch_fail", "defaults": {"lambda": 2.4, "locos": 2, "crews": 2, "proc": 30}, "arrivals": "poisson",
                        "cargo": {"box": 3, "tank": 3, "gondola": 2}, "decor": "junction", "season": "summer"}},
    "shymkent": {"tier": "node", "lat": 42.2991, "lon": 69.6090, "names": {"ru": "Шымкент", "kz": "Шымкент", "en": "Shymkent"},
                 "reception": 3, "sorting": 4, "cap_day": 32, "base_load": 68, "phase": 2.0, "case": None,
                 "geom": {"lx0": -84, "dx": 5.8, "dz": 4.2},
                 "profile": {"problem": "crew_absent", "defaults": {"lambda": 3.0, "locos": 3, "crews": 2, "proc": 30}, "arrivals": "steady",
                             "cargo": {"tank": 5, "box": 3, "platform": 1}, "decor": "refinery", "season": "summer"}},
    "astana": {"tier": "node", "lat": 51.1124, "lon": 71.5317, "names": {"ru": "Астана", "kz": "Астана", "en": "Astana"},
               "reception": 5, "sorting": 2, "cap_day": 34, "base_load": 54, "phase": 2.9, "case": None,
               "geom": {"lx0": -90, "dx": 5.5, "dz": 4.0},
               "profile": {"problem": "train_delay", "defaults": {"lambda": 3.6, "locos": 2, "crews": 2, "proc": 25}, "arrivals": "bunched",
                           "cargo": {"box": 4, "container": 3, "platform": 1}, "decor": "city", "season": "summer"}},
    "karaganda": {"tier": "node", "lat": 49.8000, "lon": 73.1400, "names": {"ru": "Қарағанды", "kz": "Қарағанды", "en": "Karaganda"},
                  "reception": 3, "sorting": 4, "cap_day": 32, "base_load": 71, "phase": 3.4, "case": None,
                  "geom": {"lx0": -82, "dx": 6.0, "dz": 4.3},
                  "profile": {"problem": "loco_fail", "defaults": {"lambda": 3.0, "locos": 3, "crews": 3, "proc": 45}, "arrivals": "poisson",
                              "cargo": {"gondola": 7, "box": 1, "platform": 1}, "decor": "coal", "season": "summer"}},
    "altynkol": {"tier": "gateway", "lat": 44.1649, "lon": 80.2954, "names": {"ru": "Алтынкөл", "kz": "Алтынкөл", "en": "Altynkol"},
                 "reception": 2, "sorting": 3, "cap_day": 22, "base_load": 78, "phase": 0.9, "case": None,
                 "geom": {"lx0": -74, "dx": 6.4, "dz": 4.2},
                 "profile": {"problem": "power_fail", "defaults": {"lambda": 2.2, "locos": 2, "crews": 2, "proc": 40}, "arrivals": "steady",
                             "cargo": {"container": 8, "box": 1}, "decor": "crane", "season": "summer"}},
    "dostyk": {"tier": "gateway", "lat": 45.2529, "lon": 82.4867, "names": {"ru": "Достық", "kz": "Достық", "en": "Dostyk"},
               "reception": 5, "sorting": 2, "cap_day": 32, "base_load": 90, "phase": 3.7, "case": None,
               "geom": {"lx0": -92, "dx": 5.6, "dz": 4.1},
               "profile": {"problem": "border_hold", "defaults": {"lambda": 3.4, "locos": 2, "crews": 2, "proc": 65}, "arrivals": "poisson",
                           "cargo": {"container": 6, "box": 3, "platform": 2}, "decor": "customs", "season": "summer"}},
    "aktau": {"tier": "gateway", "lat": 43.6200, "lon": 51.2000, "names": {"ru": "Ақтау (порт)", "kz": "Ақтау (порт)", "en": "Aktau (port)"},
              "reception": 2, "sorting": 4, "cap_day": 20, "base_load": 83, "phase": 1.4, "case": None,
              "geom": {"lx0": -80, "dx": 6.2, "dz": 4.6},
              "profile": {"problem": "arrival_burst", "defaults": {"lambda": 2.0, "locos": 2, "crews": 2, "proc": 40}, "arrivals": "burst",
                          "cargo": {"tank": 6, "container": 2, "gondola": 1}, "decor": "sea", "season": "summer"}},
    "petropavlovsk": {"tier": "gateway", "lat": 54.8700, "lon": 69.1500, "names": {"ru": "Петропавловск", "kz": "Петропавл", "en": "Petropavlovsk"},
                      "reception": 3, "sorting": 2, "cap_day": 22, "base_load": 52, "phase": 4.1, "case": None,
                      "geom": {"lx0": -76, "dx": 6.0, "dz": 4.2},
                      "profile": {"problem": "switch_icing", "defaults": {"lambda": 1.8, "locos": 2, "crews": 2, "proc": 30}, "arrivals": "poisson",
                                  "cargo": {"gondola": 4, "box": 4, "tank": 1}, "decor": "silos", "season": "winter"}},
    "pavlodar": {"tier": "gateway", "lat": 52.3003, "lon": 76.9724, "names": {"ru": "Павлодар", "kz": "Павлодар", "en": "Pavlodar"},
                 "reception": 3, "sorting": 3, "cap_day": 24, "base_load": 63, "phase": 2.6, "case": None,
                 "geom": {"lx0": -80, "dx": 6.0, "dz": 4.2},
                 "profile": {"problem": "false_occupancy", "defaults": {"lambda": 2.2, "locos": 2, "crews": 2, "proc": 35}, "arrivals": "poisson",
                             "cargo": {"tank": 3, "gondola": 4, "box": 2}, "decor": "chimneys", "season": "summer"}},
    "kopa": {"tier": "station", "lat": 43.5264, "lon": 75.8132, "names": {"ru": "Копа", "kz": "Қопа", "en": "Kopa"},
             "reception": 2, "sorting": 2, "cap_day": 14, "base_load": 69, "phase": 0.7, "case": "hostile_route",
             "geom": {"lx0": -66, "dx": 6.6, "dz": 4.2},
             "profile": {"problem": "hostile", "defaults": {"lambda": 2.4, "locos": 2, "crews": 2, "proc": 30}, "arrivals": "bunched",
                         "cargo": {"box": 4, "gondola": 3, "tank": 1}, "decor": "steppe", "season": "summer"}},
    "otar": {"tier": "station", "lat": 43.5400, "lon": 75.2119, "names": {"ru": "Отар", "kz": "Отар", "en": "Otar"},
             "reception": 3, "sorting": 2, "cap_day": 18, "base_load": 63, "phase": 1.6, "case": "shoe",
             "geom": {"lx0": -72, "dx": 6.0, "dz": 4.2},
             "profile": {"problem": "shoe", "defaults": {"lambda": 1.8, "locos": 2, "crews": 2, "proc": 35}, "arrivals": "poisson",
                         "cargo": {"box": 4, "gondola": 3, "platform": 1}, "decor": "steppe", "season": "summer"}},
    "shamalgan": {"tier": "station", "lat": 43.3797, "lon": 76.6264, "names": {"ru": "Шамалган", "kz": "Шамалған", "en": "Shamalgan"},
                  "reception": 2, "sorting": 2, "cap_day": 14, "base_load": 47, "phase": 2.4, "case": "unsecured",
                  "geom": {"lx0": -68, "dx": 6.4, "dz": 4.4},
                  "profile": {"problem": "unsecured", "defaults": {"lambda": 1.6, "locos": 2, "crews": 2, "proc": 30}, "arrivals": "steady",
                              "cargo": {"gondola": 4, "box": 3, "platform": 1}, "decor": "slope", "season": "summer"}},
}

# Участки между именованными станциями. via — промежуточные точки (lat, lon), схематично.
# cap — расчётная пропускная способность участка, пар поездов/сутки (демо-значения).
SECTIONS = [
    {"id": "almaty1-shamalgan", "a": "almaty1", "b": "shamalgan", "via": [], "cap": 48, "trunk": "Алматы – Шу"},
    {"id": "shamalgan-kopa", "a": "shamalgan", "b": "kopa", "via": [], "cap": 48, "trunk": "Алматы – Шу"},
    {"id": "kopa-otar", "a": "kopa", "b": "otar", "via": [], "cap": 48, "trunk": "Алматы – Шу"},
    {"id": "otar-shu", "a": "otar", "b": "shu", "via": [], "cap": 48, "trunk": "Алматы – Шу"},
    {"id": "shu-shymkent", "a": "shu", "b": "shymkent", "via": [(42.90, 71.37)], "cap": 44, "trunk": "Шу – Шымкент"},
    {"id": "shu-karaganda", "a": "shu", "b": "karaganda", "via": [(44.9, 73.7), (47.2183, 73.3604)], "cap": 40, "trunk": "Шу – Қарағанды"},
    {"id": "karaganda-astana", "a": "karaganda", "b": "astana", "via": [(50.6, 72.5)], "cap": 52, "trunk": "Қарағанды – Астана"},
    {"id": "astana-petropavlovsk", "a": "astana", "b": "petropavlovsk", "via": [(53.2885, 69.4223)], "cap": 40, "trunk": "Астана – Петропавловск"},
    {"id": "astana-pavlodar", "a": "astana", "b": "pavlodar", "via": [(51.9, 75.3)], "cap": 36, "trunk": "Астана – Павлодар"},
    {"id": "almaty1-altynkol", "a": "almaty1", "b": "altynkol", "via": [(43.6887, 77.1259), (44.0, 79.0)], "cap": 36, "trunk": "Алматы – Алтынкөл"},
    {"id": "almaty1-dostyk", "a": "almaty1", "b": "dostyk", "via": [(43.6887, 77.1259), (44.9, 78.5), (46.9531, 79.6817)], "cap": 34, "trunk": "Алматы – Достық"},
    {"id": "aktau-karaganda", "a": "aktau", "b": "karaganda", "via": [(45.3221, 55.1966), (47.7775, 67.6917)], "cap": 24, "trunk": "Ақтау – Қарағанды"},
]


def resolve_sections():
    out = []
    for s in SECTIONS:
        a, b = STATIONS[s["a"]], STATIONS[s["b"]]
        pts = [[a["lat"], a["lon"]]] + [[p[0], p[1]] for p in s["via"]] + [[b["lat"], b["lon"]]]
        out.append({"id": s["id"], "a": s["a"], "b": s["b"], "pts": pts, "cap": s["cap"], "trunk": s["trunk"]})
    return out
