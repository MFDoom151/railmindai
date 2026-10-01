"""Мини-отчёт по станции: CSV (несколько секций) и PDF (индекс и его факторы, конфликты, сбои, план, график индекса и занятость путей)."""
from __future__ import annotations

import csv
import io
import time
from pathlib import Path
from typing import Dict

from fpdf import FPDF

from .models import hhmm
from .service import StationRuntime

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
T = {
    "ru": {"title": "Отчёт «Цифровая станция»", "station": "Станция", "gen": "Сформирован", "sim": "Модельное время", "index": "Индекс эффективности",
           "factors": "Факторы индекса (вклад в потерю)", "conf": "Активные конфликты", "dis": "Нештатные ситуации и события", "plan": "План (ближайшие поезда)",
           "timeline": "Индекс за последние минуты", "gantt": "Занятость путей по плану", "f_key": "Фактор", "f_w": "Вес", "f_raw": "Значение", "f_s": "Оценка", "f_loss": "Потеря",
           "none": "нет", "train": "Поезд", "track": "Путь", "eta": "Прогноз", "arr": "Приём", "dep": "Отпр.", "late": "Опозд.", "type": "Тип", "cat": {"norm": "Норма", "attention": "Внимание", "critical": "Критично"},
           "note": "Данные демонстрационные (симулятор)."},
    "en": {"title": "Digital Station report", "station": "Station", "gen": "Generated", "sim": "Simulation time", "index": "Efficiency index",
           "factors": "Index factors (contribution to loss)", "conf": "Active conflicts", "dis": "Disruptions and events", "plan": "Plan (upcoming trains)",
           "timeline": "Index over the last minutes", "gantt": "Track occupancy in the plan", "f_key": "Factor", "f_w": "Weight", "f_raw": "Value", "f_s": "Score", "f_loss": "Loss",
           "none": "none", "train": "Train", "track": "Track", "eta": "ETA", "arr": "Arrival", "dep": "Dep.", "late": "Late", "type": "Type", "cat": {"norm": "Normal", "attention": "Attention", "critical": "Critical"},
           "note": "Demonstration data (simulator)."},
    "kz": {"title": "«Цифрлық станция» есебі", "station": "Станция", "gen": "Құрылды", "sim": "Модельдік уақыт", "index": "Тиімділік индексі",
           "factors": "Индекс факторлары (жоғалтуға үлесі)", "conf": "Белсенді қақтығыстар", "dis": "Төтенше жағдайлар мен оқиғалар", "plan": "Жоспар (жақын пойыздар)",
           "timeline": "Соңғы минуттардағы индекс", "gantt": "Жоспар бойынша жолдардың бос еместігі", "f_key": "Фактор", "f_w": "Салмақ", "f_raw": "Мәні", "f_s": "Баға", "f_loss": "Шығын",
           "none": "жоқ", "train": "Пойыз", "track": "Жол", "eta": "Болжам", "arr": "Қабылдау", "dep": "Жөнелту", "late": "Кешігу", "type": "Түрі", "cat": {"norm": "Норма", "attention": "Назар", "critical": "Критикалық"},
           "note": "Демонстрациялық деректер (симулятор)."},
}
FACT = {
    "ru": {"throughput": "Пропускная способность", "avg_dev": "Среднее отклонение от графика", "max_dev": "Наибольшее отклонение", "track_load": "Загрузка путей",
           "conflicts": "Конфликты", "queue_outside": "Очередь на подходе", "loco_idle": "Загрузка локомотивов", "crew_idle": "Загрузка бригад"},
    "en": {"throughput": "Throughput", "avg_dev": "Average schedule deviation", "max_dev": "Max deviation", "track_load": "Track load",
           "conflicts": "Conflicts", "queue_outside": "Approach queue", "loco_idle": "Locomotive utilization", "crew_idle": "Crew utilization"},
    "kz": {"throughput": "Өткізу қабілеті", "avg_dev": "Кестеден орташа ауытқу", "max_dev": "Ең үлкен ауытқу", "track_load": "Жолдар жүктемесі",
           "conflicts": "Қақтығыстар", "queue_outside": "Жақындаудағы кезек", "loco_idle": "Локомотивтер жүктемесі", "crew_idle": "Бригадалар жүктемесі"},
}
TYPES = {"freight": {"ru": "груз.", "en": "freight", "kz": "жүк"}, "passenger": {"ru": "пасс.", "en": "pass.", "kz": "жолаушы"}, "transit": {"ru": "транзит", "en": "transit", "kz": "транзит"}}


def _name(rt: StationRuntime, lang: str):
    n = rt.infra.names
    return n.get(lang) or n.get("ru")


def build_csv(rt: StationRuntime, lang: str = "ru") -> str:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    L = T.get(lang, T["ru"])
    w.writerow(["# railtwin report", rt.sid, _name(rt, lang), time.strftime("%Y-%m-%d %H:%M:%S"), "sim", hhmm(rt.state.now)])
    w.writerow([])
    w.writerow(["# index", "score", "category", "letter"])
    w.writerow(["", rt.index.get("score"), rt.index.get("category"), rt.index.get("letter")])
    w.writerow(["# index_factors", "key", "weight", "raw", "score", "loss"])
    for f in rt.index.get("factors", []):
        w.writerow(["", f["key"], f["weight"], f["raw"], f["score"], f["loss"]])
    w.writerow([])
    w.writerow(["# plan", "train", "type", "track", "eta", "arrival", "sched_dep", "departure", "late_min", "wait_min"])
    kinds = {t.id: t.type for t in rt.state.trains.values()}
    if rt.plan:
        for it in sorted(rt.plan.items.values(), key=lambda i: i.arr):
            if it.train_id in kinds:
                w.writerow(["", it.train_id, kinds[it.train_id], it.track if it.track is not None else "main", hhmm(it.eta), hhmm(it.arr), hhmm(it.sched_dep), hhmm(it.dep), it.late, it.wait_out])
    w.writerow([])
    w.writerow(["# conflicts", "type", "severity", "from", "to", "trains", "track"])
    for c in rt.conflicts:
        w.writerow(["", c["type"], c["severity"], hhmm(c["t_from"]), hhmm(c["t_to"]), " ".join(c["trains"]), c["track"]])
    w.writerow([])
    w.writerow(["# events", "wall", "sim", "code", "level", "details"])
    for a in rt.alerts:
        w.writerow(["", time.strftime("%H:%M:%S", time.localtime(a["wall"])), hhmm(a["sim"]), a["code"], a["level"], str(a["params"])])
    w.writerow([])
    w.writerow(["# index_timeline", "wall", "sim", "score"])
    for f in list(rt.frames)[::4]:
        w.writerow(["", time.strftime("%H:%M:%S", time.localtime(f["wall"])), hhmm(f["sim"]), f["index"]["score"]])
    return "﻿" + out.getvalue()


class _PDF(FPDF):
    pass


def build_pdf(rt: StationRuntime, lang: str = "ru") -> bytes:
    L = T.get(lang, T["ru"])
    F = FACT.get(lang, FACT["ru"])
    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(True, 12)
    pdf.add_font("DV", "", str(FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DV", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.add_page()
    pdf.set_font("DV", "B", 16)
    pdf.cell(0, 9, L["title"], ln=1)
    pdf.set_font("DV", "", 10)
    pdf.cell(0, 6, f"{L['station']}: {_name(rt, lang)}   |   {L['gen']}: {time.strftime('%Y-%m-%d %H:%M:%S')}   |   {L['sim']}: {hhmm(rt.state.now)}", ln=1)
    pdf.ln(2)
    idx = rt.index
    cat = idx.get("category", "norm")
    col = {"norm": (46, 140, 99), "attention": (190, 140, 40), "critical": (190, 70, 65)}[cat]
    pdf.set_font("DV", "B", 13)
    pdf.cell(0, 8, L["index"], ln=1)
    pdf.set_fill_color(*col)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("DV", "B", 22)
    pdf.cell(34, 14, f"{idx.get('score', 0):.0f}", border=0, align="C", fill=True)
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("DV", "B", 13)
    pdf.cell(0, 14, f"   {idx.get('letter')}  —  {L['cat'][cat]}", ln=1)
    pdf.ln(1)
    # факторы
    pdf.set_font("DV", "B", 10)
    pdf.cell(0, 7, L["factors"], ln=1)
    pdf.set_font("DV", "B", 8.5)
    widths = [62, 20, 28, 24, 24]
    for h, wd in zip([L["f_key"], L["f_w"], L["f_raw"], L["f_s"], L["f_loss"]], widths):
        pdf.cell(wd, 6, h, border=1)
    pdf.ln()
    pdf.set_font("DV", "", 8.5)
    for f in sorted(idx.get("factors", []), key=lambda f: -f["loss"]):
        row = [F.get(f["key"], f["key"]), f"{f['weight']:.2f}", f"{f['raw']}", f"{f['score']:.2f}", f"{f['loss']:.1f}"]
        for v, wd in zip(row, widths):
            pdf.cell(wd, 5.5, v, border=1)
        pdf.ln()
    pdf.ln(2)
    # график индекса
    pdf.set_font("DV", "B", 10)
    pdf.cell(0, 7, L["timeline"], ln=1)
    fr = list(rt.frames)[::3]
    x0, y0, W, H = pdf.get_x(), pdf.get_y(), 170, 28
    pdf.set_draw_color(160, 160, 160)
    pdf.rect(x0, y0, W, H)
    for lvl in (50, 75):
        yy = y0 + H - H * lvl / 100
        pdf.set_draw_color(220, 220, 220)
        pdf.line(x0, yy, x0 + W, yy)
    if len(fr) > 1:
        pdf.set_draw_color(40, 90, 160)
        pdf.set_line_width(0.5)
        for a, b in zip(fr, fr[1:]):
            xa = x0 + W * fr.index(a) / (len(fr) - 1)
            xb = x0 + W * (fr.index(b)) / (len(fr) - 1)
            pdf.line(xa, y0 + H - H * a["index"]["score"] / 100, xb, y0 + H - H * b["index"]["score"] / 100)
        pdf.set_line_width(0.2)
    pdf.set_y(y0 + H + 3)
    # конфликты
    pdf.set_font("DV", "B", 10)
    pdf.cell(0, 7, L["conf"], ln=1)
    pdf.set_font("DV", "", 8.5)
    if not rt.conflicts:
        pdf.cell(0, 5.5, L["none"], ln=1)
    for c in rt.conflicts[:14]:
        pdf.cell(0, 5, f"[{c['severity']}] {c['type']}  {hhmm(c['t_from'])}-{hhmm(c['t_to'])}  {', '.join(c['trains'])}  track={c['track']}", ln=1)
    pdf.ln(1)
    # события
    pdf.set_font("DV", "B", 10)
    pdf.cell(0, 7, L["dis"], ln=1)
    pdf.set_font("DV", "", 8.5)
    ev = list(rt.alerts)[-14:]
    if not ev:
        pdf.cell(0, 5.5, L["none"], ln=1)
    for a in ev:
        pdf.cell(0, 5, f"{time.strftime('%H:%M:%S', time.localtime(a['wall']))}  sim {hhmm(a['sim'])}  {a['code']}  {str({k: v for k, v in a['params'].items()})[:110]}", ln=1)
    # план
    pdf.add_page()
    pdf.set_font("DV", "B", 10)
    pdf.cell(0, 7, L["plan"], ln=1)
    pdf.set_font("DV", "B", 8.5)
    cols = [(L["train"], 26), (L["type"], 20), (L["track"], 14), (L["eta"], 20), (L["arr"], 20), (L["dep"], 20), (L["late"], 18)]
    for h, wd in cols:
        pdf.cell(wd, 6, h, border=1)
    pdf.ln()
    pdf.set_font("DV", "", 8.5)
    kinds = {t.id: t.type for t in rt.state.trains.values()}
    items = sorted([it for it in (rt.plan.items.values() if rt.plan else []) if it.train_id in kinds], key=lambda i: i.arr)[:28]
    for it in items:
        vals = [it.train_id, TYPES[kinds[it.train_id]][lang if lang in ("ru", "en", "kz") else "ru"], str(it.track) if it.track is not None else "—", hhmm(it.eta), hhmm(it.arr), hhmm(it.dep), str(it.late)]
        for v, (_, wd) in zip(vals, cols):
            pdf.cell(wd, 5.5, v, border=1)
        pdf.ln()
    pdf.ln(3)
    # занятость путей (Гант по плану)
    pdf.set_font("DV", "B", 10)
    pdf.cell(0, 7, L["gantt"], ln=1)
    tracks = list(rt.infra.tracks)
    now = rt.state.now
    t_lo, t_hi = now - 10, now + 240
    x0, y0, W = pdf.get_x() + 12, pdf.get_y(), 158
    rh = 6
    pdf.set_font("DV", "", 7.5)
    for i, tr in enumerate(tracks):
        yy = y0 + i * rh
        pdf.set_xy(pdf.l_margin, yy)
        pdf.cell(11, rh, f"{L['track']} {tr}")
        pdf.set_draw_color(225, 225, 225)
        pdf.rect(x0, yy, W, rh - 0.6)
    for it in items:
        if it.track is None:
            continue
        a, b = max(it.arr, t_lo), min(it.dep, t_hi)
        if b <= a:
            continue
        i = tracks.index(it.track)
        pdf.set_fill_color(*((90, 140, 200) if kinds[it.train_id] == "freight" else (70, 160, 120)))
        pdf.rect(x0 + W * (a - t_lo) / (t_hi - t_lo), y0 + i * rh + 0.6, max(0.8, W * (b - a) / (t_hi - t_lo)), rh - 1.8, style="F")
    pdf.set_y(y0 + len(tracks) * rh + 3)
    pdf.set_font("DV", "", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 5, L["note"], ln=1)
    return bytes(pdf.output())
