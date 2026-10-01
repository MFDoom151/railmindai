// Макро-уровень: схема сети КТЖ и тепловая карта загруженности (Canvas 2D).
// Граница — Natural Earth (упрощена), координаты станций — OSM; трассы между станциями схематичны.
import { stName, t } from './i18n.js';

const COL = { green: '#3f9d78', yellow: '#d1a03c', red: '#cf5a54' };
const LON0 = 46, LON1 = 88, LAT0 = 40.3, LAT1 = 55.8, K = Math.cos(48 * Math.PI / 180);
const proj = (lat, lon) => [(lon - LON0) * K, (LAT1 - lat)];
const LBL = { shu: 'left', otar: 'above', kopa: 'below', shamalgan: 'above', almaty1: 'below', altynkol: 'below', astana: 'above', pavlodar: 'right', karaganda: 'left', shymkent: 'below', aktau: 'above' };
const W_W = (LON1 - LON0) * K, W_H = LAT1 - LAT0;

export function heatColor(load) {                 // 40→зелёный, 70→янтарный, 92→красный
  const stops = [[40, [63, 157, 120]], [70, [209, 160, 60]], [92, [207, 90, 84]]];
  if (load <= stops[0][0]) return `rgb(${stops[0][1].join(',')})`;
  for (let i = 1; i < stops.length; i++) if (load <= stops[i][0]) {
    const [a, ca] = stops[i - 1], [b, cb] = stops[i], k = (load - a) / (b - a);
    return `rgb(${ca.map((v, j) => Math.round(v + (cb[j] - v) * k)).join(',')})`;
  }
  return `rgb(${stops[2][1].join(',')})`;
}

export class KzMap {
  constructor(canvas, { onSelect, onOpen }) {
    this.c = canvas; this.ctx = canvas.getContext('2d');
    this.onSelect = onSelect; this.onOpen = onOpen; this.mode = 'scheme'; this.paused = false;
    this.border = null; this.stations = []; this.sections = [];
    this.view = { x: W_W / 2, y: W_H / 2, s: 1 };
    this.hover = null; this.hoverSec = null; this.selected = null; this.dragging = null; this.zoomAnim = null; this.dots = [];
    this._bind();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
    this._loop = this._loop.bind(this); requestAnimationFrame(this._loop);
  }

  async loadBorder(url) {
    try { const g = await (await fetch(url)).json(); this.border = g.geometry.coordinates.map(poly => poly[0].map(([lon, lat]) => proj(lat, lon))); }
    catch (e) { this.border = null; }
  }
  setMode(m) { this.mode = m; }
  setData(stations, sections) {
    this.stations = stations; this.sections = sections;
    if (this.dots.length !== sections.length * 2) this.dots = sections.flatMap((s, i) => [{ i, p: Math.random(), v: 0.014 + Math.random() * 0.012 }, { i, p: Math.random(), v: -(0.012 + Math.random() * 0.012) }]);
  }
  resize() {
    const p = this.c.parentElement, dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = p.clientWidth; this.h = p.clientHeight;
    if (!this.w || !this.h) return;
    this.c.width = this.w * dpr; this.c.height = this.h * dpr; this.c.style.width = this.w + 'px'; this.c.style.height = this.h + 'px'; this.dpr = dpr;
    this.base = Math.min(this.w / (W_W * 1.04), this.h / (W_H * 1.1));
  }
  tx(wx, wy) { const sc = this.base * this.view.s; return [this.w / 2 + (wx - this.view.x) * sc, this.h / 2 + (wy - this.view.y) * sc]; }
  st(s) { const [wx, wy] = proj(s.lat, s.lon); return this.tx(wx, wy); }
  resetView(animate = true) { this.flyTo(W_W / 2, W_H / 2, 1, animate ? 700 : 0); }
  flyTo(x, y, s, dur = 900, done) {
    if (!dur) { Object.assign(this.view, { x, y, s }); done && done(); return; }
    this.zoomAnim = { from: { ...this.view }, to: { x, y, s }, t0: performance.now(), dur, done };
  }
  focusSection(id) {
    const sec = this.sections.find(x => x.id === id); if (!sec) return;
    const m = sec.pts[Math.floor(sec.pts.length / 2)]; const [wx, wy] = proj(m[0], m[1]); this.flyTo(wx, wy, 3, 700);
  }
  zoomToStation(id) {
    const s = this.stations.find(z => z.id === id); if (!s) return;
    const [wx, wy] = proj(s.lat, s.lon); this.selected = id;
    this.flyTo(wx, wy, 7.5, 900, () => this.onOpen && this.onOpen(id));
  }

  _bind() {
    const c = this.c;
    const pos = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const pickSt = (mx, my) => {
      let best = null, bd = 1e9;
      for (const s of this.stations) { const [x, y] = this.st(s); const d = Math.hypot(x - mx, y - my); if (d < (s.tier === 'station' ? 10 : 14) && d < bd) { best = s; bd = d; } }
      return best;
    };
    const pickSec = (mx, my) => {
      let best = null, bd = 9;
      for (const sec of this.sections) {
        const px = sec._px; if (!px) continue;
        for (let i = 1; i < px.length; i++) { const d = segDist(mx, my, px[i - 1], px[i]); if (d < bd) { bd = d; best = sec; } }
      }
      return best;
    };
    c.addEventListener('mousemove', (e) => {
      const [mx, my] = pos(e); this.mouse = [mx, my];
      if (this.dragging) { const sc = this.base * this.view.s; this.view.x = this.dragging.vx - (mx - this.dragging.mx) / sc; this.view.y = this.dragging.vy - (my - this.dragging.my) / sc; this.dragging.moved = true; this.zoomAnim = null; return; }
      const h = pickSt(mx, my); this.hover = h ? h.id : null; this.hoverSec = h ? null : (pickSec(mx, my)?.id || null);
      c.style.cursor = h ? 'pointer' : 'grab';
    });
    c.addEventListener('mousedown', (e) => { const [mx, my] = pos(e); this.dragging = { mx, my, vx: this.view.x, vy: this.view.y, moved: false }; });
    window.addEventListener('mouseup', (e) => {
      if (!this.dragging) return; const moved = this.dragging.moved; this.dragging = null; if (moved) return;
      const r = c.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return;
      const [mx, my] = pos(e); const h = pickSt(mx, my);
      if (h) { this.selected = h.id; this.onSelect && this.onSelect(h.id); this.zoomToStation(h.id); }
    });
    c.addEventListener('mouseleave', () => { this.hover = null; this.hoverSec = null; this.mouse = null; });
    c.addEventListener('wheel', (e) => {
      e.preventDefault(); const [mx, my] = pos(e); const sc0 = this.base * this.view.s;
      const wx = this.view.x + (mx - this.w / 2) / sc0, wy = this.view.y + (my - this.h / 2) / sc0;
      this.view.s = Math.max(0.8, Math.min(14, this.view.s * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      const sc1 = this.base * this.view.s; this.view.x = wx - (mx - this.w / 2) / sc1; this.view.y = wy - (my - this.h / 2) / sc1; this.zoomAnim = null;
    }, { passive: false });
  }

  _loop(now) {
    requestAnimationFrame(this._loop);
    if (this.paused || !this.w) return;
    if (this.zoomAnim) {
      const a = this.zoomAnim, k = Math.min(1, (now - a.t0) / a.dur), e = k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      this.view.s = Math.exp(Math.log(a.from.s) * (1 - e) + Math.log(a.to.s) * e);
      this.view.x = a.from.x + (a.to.x - a.from.x) * e; this.view.y = a.from.y + (a.to.y - a.from.y) * e;
      if (k >= 1) { const d = a.done; this.zoomAnim = null; d && d(); }
    }
    this.draw(now);
  }

  draw(now) {
    const { ctx, w, h, dpr } = this; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#0e141c'; ctx.fillRect(0, 0, w, h);
    this._grid();
    if (this.border) {
      ctx.beginPath();
      for (const ring of this.border) { ring.forEach(([x, y], i) => { const [sx, sy] = this.tx(x, y); i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy); }); ctx.closePath(); }
      ctx.fillStyle = '#131c28'; ctx.fill(); ctx.strokeStyle = '#4c5f78'; ctx.lineWidth = 1.3; ctx.stroke();
    }
    const byId = Object.fromEntries(this.stations.map(s => [s.id, s]));
    const heat = this.mode === 'heat', sc = this.view.s;

    // тепловое свечение (только в режиме тепловой карты)
    if (heat) {
      ctx.save(); ctx.globalCompositeOperation = 'lighter';
      for (const sec of this.sections) if (sec.load >= 70 && sec._px) {
        const mid = sec._px[Math.floor(sec._px.length / 2)]; const r = (26 + (sec.load - 70) * 1.6) * Math.min(1.6, 0.7 + sc * 0.2);
        const g = ctx.createRadialGradient(mid[0], mid[1], 0, mid[0], mid[1], r); const c = sec.load >= 85 ? '207,90,84' : '209,160,60';
        g.addColorStop(0, `rgba(${c},.20)`); g.addColorStop(1, `rgba(${c},0)`); ctx.fillStyle = g; ctx.fillRect(mid[0] - r, mid[1] - r, 2 * r, 2 * r);
      }
      for (const s of this.stations) if (s.load >= 70) {
        const [x, y] = this.st(s); const r = (34 + (s.load - 70) * 2.2) * Math.min(1.6, 0.7 + sc * 0.2); const c = s.load >= 85 ? '207,90,84' : '209,160,60';
        const g = ctx.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, `rgba(${c},.34)`); g.addColorStop(1, `rgba(${c},0)`); ctx.fillStyle = g; ctx.fillRect(x - r, y - r, 2 * r, 2 * r);
      }
      ctx.restore();
    }

    // участки
    for (const sec of this.sections) {
      const pts = sec.pts.map(([la, lo]) => { const [wx, wy] = proj(la, lo); return this.tx(wx, wy); }); sec._px = pts;
      const path = () => { ctx.beginPath(); pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); };
      ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      const hov = this.hoverSec === sec.id;
      if (heat) {
        const wd = 3 + sec.load / 100 * 6 + (hov ? 2 : 0);
        path(); ctx.strokeStyle = '#0a0f15'; ctx.lineWidth = wd + 3; ctx.stroke();
        path(); ctx.strokeStyle = heatColor(sec.load); ctx.lineWidth = wd; ctx.stroke();
      } else {
        path(); ctx.strokeStyle = hov ? '#8aa0bd' : '#506279'; ctx.lineWidth = 4.2; ctx.stroke();
        path(); ctx.strokeStyle = '#131c28'; ctx.lineWidth = 2.2; ctx.stroke();
        path(); ctx.strokeStyle = hov ? '#e2e8f0' : '#a9b6c8'; ctx.lineWidth = 1.1; ctx.setLineDash([5, 6]); ctx.stroke(); ctx.setLineDash([]);
      }
    }
    // поезда
    for (const d of this.dots) {
      const sec = this.sections[d.i]; if (!sec || !sec._px) continue;
      d.p += d.v * 0.016; if (d.p > 1) d.p -= 1; if (d.p < 0) d.p += 1;
      const pt = pathPoint(sec._px, d.p); const s = Math.max(2.4, 2.6 * Math.min(2, sc ** 0.4));
      ctx.fillStyle = heat ? 'rgba(240,244,250,.85)' : '#dbe4ef'; ctx.fillRect(pt[0] - s, pt[1] - s * 0.6, s * 2, s * 1.2);
    }
    // станции
    const order = [...this.stations].sort((a, b) => rank(a) - rank(b));
    for (const s of order) {
      const [x, y] = this.st(s); const col = heat ? heatColor(s.load) : COL[s.status];
      const base = s.tier === 'node' ? 8.5 : s.tier === 'gateway' ? 8 : 5.5; const r = base * Math.min(1.5, 0.9 + sc * 0.08);
      const act = this.selected === s.id || this.hover === s.id;
      ctx.beginPath();
      if (s.tier === 'gateway') { ctx.moveTo(x, y - r - 1); ctx.lineTo(x + r + 1, y); ctx.lineTo(x, y + r + 1); ctx.lineTo(x - r - 1, y); ctx.closePath(); } else ctx.arc(x, y, r, 0, 7);
      ctx.fillStyle = col; ctx.fill(); ctx.lineWidth = s.tier === 'station' ? 1.5 : 2.2; ctx.strokeStyle = act ? '#ffffff' : '#0e141c'; ctx.stroke();
      if (s.tier === 'node') { ctx.beginPath(); ctx.arc(x, y, r + 3.5, 0, 7); ctx.strokeStyle = act ? '#ffffff' : '#8896a9'; ctx.lineWidth = 1.2; ctx.stroke(); }
      if (s.status === 'red' && !heat) { const p = 0.5 + 0.5 * Math.sin(now / 500); ctx.beginPath(); ctx.arc(x, y, r + 6 + p * 4, 0, 7); ctx.strokeStyle = `rgba(207,90,84,${0.5 - p * 0.35})`; ctx.lineWidth = 1.5; ctx.stroke(); }
      const big = s.tier !== 'station';
      if (!big && sc < 2.6 && !act) continue;                       // подписи малых станций — только при приближении
      const label = stName(s), dir = LBL[s.id] || 'right';
      ctx.font = `${big ? 600 : 500} ${big ? 12.5 : 11}px "Segoe UI", Inter, sans-serif`;
      let lx = x + r + 7, ly = y + 4, al = 'left';
      if (dir === 'left') { lx = x - r - 7; al = 'right'; } else if (dir === 'above') { lx = x; ly = y - r - 8; al = 'center'; } else if (dir === 'below') { lx = x; ly = y + r + 17; al = 'center'; }
      ctx.textAlign = al; ctx.lineWidth = 3.5; ctx.strokeStyle = 'rgba(14,20,28,.92)'; ctx.strokeText(label, lx, ly);
      ctx.fillStyle = big ? '#e6ebf2' : '#a9b6c8'; ctx.fillText(label, lx, ly);
      if (heat || sc > 3) {
        ctx.font = '600 11px "Segoe UI", sans-serif'; const txt = `${Math.round(s.load)}%`; ctx.lineWidth = 3.5; ctx.strokeStyle = 'rgba(14,20,28,.92)'; ctx.strokeText(txt, lx, ly + 13); ctx.fillStyle = col; ctx.fillText(txt, lx, ly + 13);
      }
    }
    this._tooltip(byId);
  }

  _tooltip(byId) {
    const { ctx, w, h } = this; if (!this.mouse) return;
    let lines = null, col = '#dde4ed';
    if (this.hover) {
      const s = byId[this.hover]; col = COL[s.status];
      lines = [[stName(s), '#e6ebf2', true], [t('tier_' + s.tier), '#8b99ab'], [`${t('load')}: ${Math.round(s.load)}% · ${t('s_' + s.status)}`, col], [`${t('dwell_avg')}: ${s.dwell_min} ${t('u_min')}`, '#c4cedb'], [`${t('trains')}: ${s.trains} · ${t('idle_w')}: ${s.wagons_idle}`, '#c4cedb']];
    } else if (this.hoverSec) {
      const sec = this.sections.find(x => x.id === this.hoverSec); if (!sec) return; col = COL[sec.status];
      lines = [[`${stName(byId[sec.a])} – ${stName(byId[sec.b])}`, '#e6ebf2', true], [sec.trunk, '#8b99ab'], [`${t('sec_load')}: ${Math.round(sec.load)}% · ${t('s_' + sec.status)}`, col], [`${t('sec_pairs')}: ${sec.pairs} / ${sec.cap} ${t('u_pairs')}`, '#c4cedb'], [`${t('sec_queue')}: ${sec.queue}`, '#c4cedb']];
    }
    if (!lines) return;
    const [mx, my] = this.mouse; ctx.font = '12px "Segoe UI", sans-serif';
    const tw = Math.max(...lines.map(l => ctx.measureText(l[0]).width)) + 24, th = lines.length * 18 + 14;
    const bx = Math.min(mx + 16, w - tw - 8), by = Math.min(my + 16, h - th - 8);
    ctx.fillStyle = 'rgba(16,23,33,.97)'; ctx.strokeStyle = '#33465d'; ctx.lineWidth = 1; rr(ctx, bx, by, tw, th, 6); ctx.fill(); ctx.stroke();
    ctx.fillStyle = col; ctx.fillRect(bx, by + 6, 3, th - 12);
    lines.forEach((l, i) => { ctx.font = `${l[2] ? 600 : 400} 12px "Segoe UI", sans-serif`; ctx.fillStyle = l[1]; ctx.textAlign = 'left'; ctx.fillText(l[0], bx + 13, by + 22 + i * 18); });
  }

  _grid() {
    const { ctx, w, h } = this; ctx.save(); ctx.strokeStyle = 'rgba(120,145,175,.07)'; ctx.lineWidth = 1; ctx.font = '10px "Segoe UI", sans-serif'; ctx.fillStyle = 'rgba(130,150,175,.45)'; ctx.textAlign = 'left';
    for (let lon = 50; lon <= 85; lon += 5) { const [x] = this.tx((lon - LON0) * K, 0); ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); ctx.fillText(lon + '°E', x + 3, h - 6); }
    for (let lat = 42; lat <= 54; lat += 2) { const [, y] = this.tx(0, LAT1 - lat); ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); ctx.fillText(lat + '°N', 4, y - 3); }
    ctx.restore();
  }
}

const rank = (s) => (s.tier === 'node' ? 3 : s.tier === 'gateway' ? 2 : 1);
function segDist(px, py, a, b) { const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy || 1; let t = ((px - a[0]) * dx + (py - a[1]) * dy) / l2; t = Math.max(0, Math.min(1, t)); return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy)); }
function pathPoint(pts, p) {
  const seg = []; let total = 0;
  for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); seg.push(d); total += d; }
  let d = p * total;
  for (let i = 0; i < seg.length; i++) { if (d <= seg[i] || i === seg.length - 1) { const k = seg[i] ? d / seg[i] : 0; return [pts[i][0] + (pts[i + 1][0] - pts[i][0]) * k, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * k]; } d -= seg[i]; }
  return pts[0];
}
function rr(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
