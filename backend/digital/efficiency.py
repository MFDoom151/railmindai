"""Индекс эффективности станции (0–100, A–E): прозрачная формула, категории и объяснимость (вклад факторов).

    Индекс = 100 · Σ w_k · f_k / Σ w_k,    f_k ∈ [0, 1]  — оценка фактора k по его измеренному значению x_k.

Оценки f_k — кусочно-линейные функции с порогами из конфигурации (config/default.yaml → index.params):
  throughput   f = clip((x − floor)/(1 − floor)),  x = обработано / запланировано за окно
  avg_dev      f = 1 − clip((x − good)/(bad − good)),  x — среднее отклонение от графика, мин (с весом приоритета)
  max_dev      то же для наибольшего отклонения
  track_load   f = 1 в рабочем диапазоне [util_lo, util_hi]; ниже — мягкий штраф (простой), выше — штраф до util_bad
  conflicts    f = 1 − clip(x / conflicts_cap),  x — взвешенное число конфликтов (crit = 1, warn = 0.4)
  queue_out    f = 1 − clip(x / queue_cap),  x — поездов, ожидающих приёма на подходе
  loco_idle / crew_idle — загрузка ресурса u: ниже res_lo — простой (штраф до res_idle_floor), выше res_hi — дефицит
Вклад фактора в потерю индекса: loss_k = 100 · w_k · (1 − f_k) / Σ w. Топ-5 по loss выводится как объяснение.
"""
from __future__ import annotations

from typing import Dict

FACTORS = ("throughput", "avg_dev", "max_dev", "track_load", "conflicts", "queue_outside", "loco_idle", "crew_idle")


def _clip(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def score_factor(key: str, x: float, p: Dict[str, float]) -> float:
    if key == "throughput":
        fl = p["throughput_floor"]
        return _clip((x - fl) / max(1e-9, 1 - fl))
    if key == "avg_dev":
        return 1 - _clip((x - p["dev_good"]) / max(1e-9, p["dev_bad"] - p["dev_good"]))
    if key == "max_dev":
        return 1 - _clip((x - p["maxdev_good"]) / max(1e-9, p["maxdev_bad"] - p["maxdev_good"]))
    if key == "track_load":
        lo, hi, bad = p["util_lo"], p["util_hi"], p["util_bad"]
        if x < lo:
            return 1 - 0.4 * (lo - x) / max(1e-9, lo)
        if x <= hi:
            return 1.0
        return 1 - _clip((x - hi) / max(1e-9, bad - hi))
    if key == "conflicts":
        return 1 - _clip(x / max(1e-9, p["conflicts_cap"]))
    if key == "queue_outside":
        return 1 - _clip(x / max(1e-9, p["queue_cap"]))
    if key in ("loco_idle", "crew_idle"):
        lo, hi, bad, fl = p["res_lo"], p["res_hi"], p["res_bad"], p["res_idle_floor"]
        if x < lo:
            return fl + (1 - fl) * x / max(1e-9, lo)
        if x <= hi:
            return 1.0
        return 1 - _clip((x - hi) / max(1e-9, bad - hi))
    raise KeyError(key)


def category(score: float, thr: Dict[str, float]) -> str:
    return "norm" if score >= thr["norm"] else "attention" if score >= thr["attention"] else "critical"


def letter(score: float, letters: Dict[str, float]) -> str:
    for l in ("A", "B", "C", "D"):
        if score >= letters[l]:
            return l
    return "E"


def compute_index(icfg, kpi: Dict[str, float]) -> dict:
    """icfg — IndexCfg; kpi — измеренные значения факторов (ключи как в FACTORS)."""
    w, p = icfg.weights, icfg.params
    tot = sum(w.values())
    factors = []
    score = 0.0
    for k in FACTORS:
        x = float(kpi.get(k, 0.0))
        f = score_factor(k, x, p)
        contrib = w[k] * f / tot * 100
        loss = w[k] * (1 - f) / tot * 100
        score += contrib
        factors.append({"key": k, "weight": round(w[k] / tot, 4), "raw": round(x, 3), "score": round(f, 3), "contrib": round(contrib, 2), "loss": round(loss, 2)})
    score = round(score, 1)
    top = sorted(factors, key=lambda f: f["loss"], reverse=True)[:5]
    return {"score": score, "category": category(score, icfg.thresholds), "letter": letter(score, icfg.letters), "factors": factors, "top": top}


def formula_doc(icfg) -> dict:
    tot = sum(icfg.weights.values())
    return {
        "formula": "Index = 100 * sum(w_k * f_k) / sum(w_k);  f_k in [0,1]",
        "weights": {k: round(v / tot, 4) for k, v in icfg.weights.items()},
        "thresholds": icfg.thresholds, "letters": icfg.letters, "params": icfg.params,
        "factors": {
            "throughput": "обработано / запланировано за окно 60 мин",
            "avg_dev": "среднее отклонение от графика, мин (прибытие/отправление), взвешено приоритетом",
            "max_dev": "наибольшее отклонение, мин",
            "track_load": "доля занятых путей; оптимум — рабочий диапазон util_lo..util_hi",
            "conflicts": "взвешенное число конфликтов (crit=1, warn=0.4)",
            "queue_outside": "число поездов, ожидающих приёма на подходе",
            "loco_idle": "загрузка локомотивов: простой (<res_lo) и дефицит (>res_hi) снижают оценку",
            "crew_idle": "загрузка бригад: то же",
        },
    }
