"""Демо-прогноз KPI пропускной способности (+2ч/+6ч/+12ч): суточная сезонность + текущая загрузка + эффект оптимизации.
Это детерминированная демонстрационная модель, а не обученная на реальных данных ML-модель."""
import math
import time


def forecast(station, load_pct, opt_gain_pct=0.0, now_h=None):
    if now_h is None:
        lt = time.localtime()
        now_h = lt.tm_hour + lt.tm_min / 60
    cap_h = station["cap_day"] / 24.0
    pressure = max(0.0, load_pct - 70.0) / 220.0
    series = []
    for t in range(0, 13):
        h = now_h + t
        season = 0.86 + 0.10 * math.sin(2 * math.pi * (h - 7 - station["phase"]) / 24.0)
        base = cap_h * season * (1 - pressure) * 24.0   # поездов/сутки (экв.)
        opt = base * (1 + opt_gain_pct / 100.0)
        band = base * (0.025 + 0.006 * t)
        series.append({"t": t, "base": round(base, 2), "opt": round(opt, 2),
                       "lo": round(opt - band, 2), "hi": round(opt + band, 2)})
    return {"station_id": None, "unit": "trains/day-eq", "now_h": round(now_h, 2), "series": series,
            "horizons": {str(h): series[h] for h in (2, 6, 12)}}
