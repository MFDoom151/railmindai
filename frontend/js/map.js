// Макро-уровень: сеть КТЖ на подложке-фотографии карты-схемы + тепловая карта загруженности (Canvas 2D).
// Координаты — в пикселях фотографии (см. netdata.js). Станции кейса и участки между ними живые (загрузка с бэкенда),
// остальная сеть моделируется как фон: по всем ходам идёт имитация движения поездов.
import { stName, t, getLang } from './i18n.js';
import { IMG, STN_PX, SEC_PATH, RAIL } from './netdata.js';

const COL = { green: '#1f9d6b', yellow: '#e0a21a', red: '#d6453d' };
const INK = '#10243d', INK2 = '#4a5d76';
const LBL = { shu: 'left', otar: 'above', kopa: 'below', shamalgan: 'above', almaty1: 'below', altynkol: 'below', astana: 'above', pavlodar: 'right', karaganda: 'left', shymkent: 'below', aktau: 'above', dostyk: 'below', petropavlovsk: 'right' };
const W_W = IMG.w, W_H = IMG.h;

export function heatColor(load) {                 // 40→зелёный, 70→янтарный, 92→красный
  const stops = [[40, [31, 157, 107]], [70, [224, 162, 26]], [92, [214, 69, 61]]];
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
    this.stations = []; this.sections = [];
    this.view = { x: W_W / 2, y: W_H / 2, s: 1 };
    this.hover = null; this.hoverSec = null; this.selected = null; this.dragging = null; this.zoomAnim = null; this.dots = [];
    this.img = new Image(); this.img.src = IMG.src; this.imgOk = false; this.img.onload = () => { this.imgOk = true; };
    // фоновая сеть: пиксельные пути и «поезда» на них (детерминированно, без Math.random в геометрии)
    this.bg = RAIL.map(r => ({ ...r, _px: null }));
    this.bgDots = RAIL.flatMap((r, i) => Array.from({ length: Math.max(2, Math.round(pathLen(r.pts) / 120)) }, (_, k) => ({ i, p: (k + 0.37 * i) % 1, v: (k % 2 ? -1 : 1) * (0.004 + 0.002 * ((i + k) % 3)), pass: (i + k) % 3 === 0 })));
    this._bind();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
    this._loop = this._loop.bind(this); requestAnimationFrame(this._loop);
  }

  async loadBorder() { /* границы уже нарисованы на фотографии */ }
  setMode(m) { this.mode = m; }
  setData(stations, sections) {
    this.stations = stations.filter(s => STN_PX[s.id]).map(s => ({ ...s, px: STN_PX[s.id] }));
    this.sections = sections.filter(s => SEC_PATH[s.id]).map(s => ({ ...s, wpts: SEC_PATH[s.id] }));
    if (this.dots.length !== this.sections.length * 3) this.dots = this.sections.flatMap((s, i) => [0, 1, 2].map(k => ({ i, p: (k * 0.34 + i * 0.13) % 1, v: (k % 2 ? -1 : 1) * (0.012 + 0.004 * k), pass: k !== 1 })));
  }
  resize() {
    const p = this.c.parentElement, dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = p.clientWidth; this.h = p.clientHeight;
    if (!this.w || !this.h) return;
    this.c.width = this.w * dpr; this.c.height = this.h * dpr; this.c.style.width = this.w + 'px'; this.c.style.height = this.h + 'px'; this.dpr = dpr;
    this.base = Math.min(this.w / W_W, this.h / W_H) * 0.99;
  }
  tx(wx, wy) { const sc = this.base * this.view.s; return [this.w / 2 + (wx - this.view.x) * sc, this.h / 2 + (wy - this.view.y) * sc]; }
  st(s) { return this.tx(s.px[0], s.px[1]); }
  resetView(animate = true) { this.flyTo(W_W / 2, W_H / 2, 1, animate ? 700 : 0); }
  flyTo(x, y, s, dur = 900, done) {
    if (!dur) { Object.assign(this.view, { x, y, s }); done && done(); return; }
    this.zoomAnim = { from: { ...this.view }, to: { x, y, s }, t0: performance.now(), dur, done };
  }
  focusSection(id) {
    const sec = this.sections.find(x => x.id === id); if (!sec) return;
    const m = sec.wpts[Math.floor(sec.wpts.length / 2)]; this.flyTo(m[0], m[1], 3, 700);
  }
  zoomToStation(id) {
    const s = this.stations.find(z => z.id === id); if (!s) return;
    this.selected = id;
    this.flyTo(s.px[0], s.px[1], 4.5, 800, () => this.onOpen && this.onOpen(id));
  }

  _bind() {
    const c = this.c;
    const pos = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const pickSt = (mx, my) => {
      let best = null, bd = 1e9;
      for (const s of this.stations) { const [x, y] = this.st(s); const d = Math.hypot(x - mx, y - my); if (d < (s.tier === 'station' ? 11 : 15) && d < bd) { best = s; bd = d; } }
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
      this.view.s = Math.max(0.8, Math.min(8, this.view.s * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
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
    ctx.fillStyle = '#dcebf8'; ctx.fillRect(0, 0, w, h);
    const sc = this.view.s, k = this.base * sc;
    if (this.imgOk) {
      const [x0, y0] = this.tx(0, 0); ctx.imageSmoothingQuality = 'high'; ctx.drawImage(this.img, x0, y0, W_W * k, W_H * k);
      if (getLang() !== 'ru') {                                       // заголовок на самой фотографии русский — закрываем подписью на языке интерфейса
        const [cx, cy] = this.tx(205, 22); ctx.fillStyle = '#b7dcf7'; ctx.fillRect(cx, cy, 870 * k, 52 * k);
        ctx.textBaseline = 'middle'; ctx.fillStyle = '#244f8f'; ctx.textAlign = 'center'; ctx.font = `800 ${30 * k}px "Segoe UI", sans-serif`;
        const [tx0, ty0] = this.tx(650, 47); ctx.fillText(t('map_title'), tx0, ty0);
        ctx.font = `700 ${16 * k}px "Segoe UI", sans-serif`; const [sx0, sy0] = this.tx(1010, 50); ctx.fillStyle = '#2a6cb3'; ctx.fillText(t('map_title_sub'), sx0, sy0); ctx.textBaseline = 'alphabetic';
      }
      if (this.mode === 'heat') { ctx.fillStyle = 'rgba(255,255,255,.38)'; ctx.fillRect(x0, y0, W_W * k, W_H * k); }   // приглушить подложку, чтобы тепловая карта читалась
    }
    const byId = Object.fromEntries(this.stations.map(s => [s.id, s]));
    const heat = this.mode === 'heat';
    const lw = (v) => v * Math.min(2.2, 0.8 + sc * 0.22);

    // фоновая сеть (моделируется целиком)
    for (const r of this.bg) {
      r._px = r.pts.map(([x, y]) => this.tx(x, y));
      ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      pathStroke(ctx, r._px); ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.lineWidth = lw(3.4); ctx.stroke();
      pathStroke(ctx, r._px); ctx.strokeStyle = heat ? 'rgba(60,80,110,.55)' : 'rgba(22,62,120,.75)'; ctx.lineWidth = lw(1.6); ctx.stroke();
    }

    // тепловое свечение (только в режиме тепловой карты)
    if (heat) {
      for (const s of this.stations) if (s.load >= 70) {
        const [x, y] = this.st(s); const r = (30 + (s.load - 70) * 1.6) * Math.min(1.8, 0.8 + sc * 0.2); const c = s.load >= 85 ? '214,69,61' : '224,162,26';
        const g = ctx.createRadialGradient(x, y, 0, x, y, r); g.addColorStop(0, `rgba(${c},.45)`); g.addColorStop(1, `rgba(${c},0)`); ctx.fillStyle = g; ctx.fillRect(x - r, y - r, 2 * r, 2 * r);
      }
    }

    // участки между станциями кейса
    for (const sec of this.sections) {
      const pts = sec.wpts.map(([x, y]) => this.tx(x, y)); sec._px = pts;
      ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      const hov = this.hoverSec === sec.id;
      const wd = heat ? lw(3.2 + sec.load / 100 * 4.5) + (hov ? 2 : 0) : lw(3.6) + (hov ? 1.6 : 0);
      pathStroke(ctx, pts); ctx.strokeStyle = '#ffffff'; ctx.lineWidth = wd + lw(3); ctx.stroke();
      pathStroke(ctx, pts); ctx.strokeStyle = heat ? heatColor(sec.load) : (hov ? '#0a64c8' : '#1457b3'); ctx.lineWidth = wd; ctx.stroke();
    }

    // поезда: по участкам и по всей фоновой сети
    const drawTrain = (pts, p, pass, dir) => {
      const a = pathPoint(pts, p), b = pathPoint(pts, Math.min(1, Math.max(0, p + 0.01 * (dir || 1))));
      const ang = Math.atan2(b[1] - a[1], b[0] - a[0]) || 0, L = lw(5.2), Wd = lw(2.6);
      ctx.save(); ctx.translate(a[0], a[1]); ctx.rotate(ang);
      ctx.fillStyle = '#ffffff'; ctx.fillRect(-L - 1, -Wd - 1, 2 * L + 2, 2 * Wd + 2);
      ctx.fillStyle = pass ? '#1f78e0' : '#e8741a'; ctx.fillRect(-L, -Wd, 2 * L, 2 * Wd);
      ctx.restore();
    };
    for (const d of this.dots) {
      const sec = this.sections[d.i]; if (!sec || !sec._px) continue;
      d.p += d.v * 0.016; if (d.p > 1) d.p -= 1; if (d.p < 0) d.p += 1;
      drawTrain(sec._px, d.p, d.pass, Math.sign(d.v));
    }
    for (const d of this.bgDots) {
      const r = this.bg[d.i]; if (!r._px) continue;
      d.p += d.v * 0.016; if (d.p > 1) d.p -= 1; if (d.p < 0) d.p += 1;
      drawTrain(r._px, d.p, d.pass, Math.sign(d.v));
    }

    // станции
    const order = [...this.stations].sort((a, b) => rank(a) - rank(b));
    for (const s of order) {
      const [x, y] = this.st(s); const col = heat ? heatColor(s.load) : COL[s.status];
      const base = s.tier === 'node' ? 9 : s.tier === 'gateway' ? 8.5 : 6; const r = base * Math.min(1.5, 0.9 + sc * 0.1);
      const act = this.selected === s.id || this.hover === s.id;
      if (s.status === 'red' && !heat) { const p = 0.5 + 0.5 * Math.sin(now / 500); ctx.beginPath(); ctx.arc(x, y, r + 7 + p * 5, 0, 7); ctx.strokeStyle = `rgba(214,69,61,${0.55 - p * 0.4})`; ctx.lineWidth = 2; ctx.stroke(); }
      ctx.beginPath();
      if (s.tier === 'gateway') { ctx.moveTo(x, y - r - 2); ctx.lineTo(x + r + 2, y); ctx.lineTo(x, y + r + 2); ctx.lineTo(x - r - 2, y); ctx.closePath(); } else ctx.arc(x, y, r, 0, 7);
      ctx.fillStyle = col; ctx.fill(); ctx.lineWidth = 2.6; ctx.strokeStyle = '#fff'; ctx.stroke();
      ctx.lineWidth = 1; ctx.strokeStyle = act ? INK : 'rgba(16,36,61,.65)'; ctx.stroke();
      if (s.tier === 'node') { ctx.beginPath(); ctx.arc(x, y, r + 4.5, 0, 7); ctx.strokeStyle = act ? INK : 'rgba(16,36,61,.55)'; ctx.lineWidth = 1.4; ctx.stroke(); }
      const big = s.tier !== 'station';
      if (!big && sc < 2.2 && !act) continue;                       // подписи малых станций — только при приближении
      const label = stName(s), dir = LBL[s.id] || 'right';
      ctx.font = `${big ? 700 : 600} ${big ? 13 : 11.5}px "Segoe UI", Inter, sans-serif`;
      let lx = x + r + 8, ly = y + 4, al = 'left';
      if (dir === 'left') { lx = x - r - 8; al = 'right'; } else if (dir === 'above') { lx = x; ly = y - r - 9; al = 'center'; } else if (dir === 'below') { lx = x; ly = y + r + 19; al = 'center'; }
      ctx.textAlign = al; ctx.lineJoin = 'round'; ctx.lineWidth = 4; ctx.strokeStyle = 'rgba(255,255,255,.95)'; ctx.strokeText(label, lx, ly);
      ctx.fillStyle = INK; ctx.fillText(label, lx, ly);
      if (heat || sc > 2.6) {
        ctx.font = '700 11.5px "Segoe UI", sans-serif'; const txt = `${Math.round(s.load)}%`; ctx.lineWidth = 4; ctx.strokeStyle = 'rgba(255,255,255,.95)'; ctx.strokeText(txt, lx, ly + 14); ctx.fillStyle = heat ? INK : col; ctx.fillText(txt, lx, ly + 14);
      }
    }
    this._tooltip(byId);
  }

  _tooltip(byId) {
    const { ctx, w, h } = this; if (!this.mouse) return;
    let lines = null, col = INK;
    if (this.hover) {
      const s = byId[this.hover]; col = COL[s.status];
      lines = [[stName(s), INK, true], [t('tier_' + s.tier), INK2], [`${t('load')}: ${Math.round(s.load)}% · ${t('s_' + s.status)}`, col], [`${t('dwell_avg')}: ${s.dwell_min} ${t('u_min')}`, INK], [`${t('trains')}: ${s.trains} · ${t('idle_w')}: ${s.wagons_idle}`, INK]];
    } else if (this.hoverSec) {
      const sec = this.sections.find(x => x.id === this.hoverSec); if (!sec) return; col = COL[sec.status];
      lines = [[`${stName(byId[sec.a])} – ${stName(byId[sec.b])}`, INK, true], [sec.trunk, INK2], [`${t('sec_load')}: ${Math.round(sec.load)}% · ${t('s_' + sec.status)}`, col], [`${t('sec_pairs')}: ${sec.pairs} / ${sec.cap} ${t('u_pairs')}`, INK], [`${t('sec_queue')}: ${sec.queue}`, INK]];
    }
    if (!lines) return;
    const [mx, my] = this.mouse; ctx.font = '12px "Segoe UI", sans-serif';
    const tw = Math.max(...lines.map(l => ctx.measureText(l[0]).width)) + 26, th = lines.length * 18 + 14;
    const bx = Math.min(mx + 16, w - tw - 8), by = Math.min(my + 16, h - th - 8);
    ctx.fillStyle = 'rgba(255,255,255,.98)'; ctx.strokeStyle = '#b9c8da'; ctx.lineWidth = 1; rr(ctx, bx, by, tw, th, 6); ctx.fill(); ctx.stroke();
    ctx.fillStyle = col; ctx.fillRect(bx, by + 6, 3, th - 12);
    lines.forEach((l, i) => { ctx.font = `${l[2] ? 700 : 400} 12px "Segoe UI", sans-serif`; ctx.fillStyle = l[1]; ctx.textAlign = 'left'; ctx.fillText(l[0], bx + 13, by + 22 + i * 18); });
  }
}

const rank = (s) => (s.tier === 'node' ? 3 : s.tier === 'gateway' ? 2 : 1);
function pathStroke(ctx, pts) { ctx.beginPath(); pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); }
function pathLen(pts) { let l = 0; for (let i = 1; i < pts.length; i++) l += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); return l; }
function segDist(px, py, a, b) { const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy || 1; let t = ((px - a[0]) * dx + (py - a[1]) * dy) / l2; t = Math.max(0, Math.min(1, t)); return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy)); }
function pathPoint(pts, p) {
  const seg = []; let total = 0;
  for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); seg.push(d); total += d; }
  let d = p * total;
  for (let i = 0; i < seg.length; i++) { if (d <= seg[i] || i === seg.length - 1) { const k = seg[i] ? d / seg[i] : 0; return [pts[i][0] + (pts[i + 1][0] - pts[i][0]) * k, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * k]; } d -= seg[i]; }
  return pts[0];
}
function rr(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
