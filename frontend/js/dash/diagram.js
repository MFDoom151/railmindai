// Диаграмма движения: время по X, пути / горловины / локомотивы по Y. План (контур) и факт (заливка), операции, опоздания,
// ожидание на подходе, окна ТО и закрытия, конфликты, линия «сейчас». Canvas — быстро при частом обновлении.
import { t } from '../i18n.js';

const TYPE_COL = { freight: [91, 143, 201], passenger: [74, 163, 122], transit: [143, 155, 171] };
const OP_COL = { INSPECT: '#9db4cf', ROSPUSK: '#cf9a3a', PODFORM: '#d9b25d' };
const hhmm = (m) => { m = Math.round(m) % 1440; if (m < 0) m += 1440; return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); };

export class Diagram {
  constructor(host, hooks = {}) {
    this.host = host; this.hooks = hooks; this.infra = null; this.data = null; this.hits = []; this.back = 30; this.ahead = 180; this.hover = null; this.sel = null;
    this.cv = document.createElement('canvas'); host.appendChild(this.cv); this.ctx = this.cv.getContext('2d');
    this.tip = document.createElement('div'); this.tip.className = 'tip'; host.appendChild(this.tip);
    new ResizeObserver(() => { this._size(); this.draw(); }).observe(host);
    this.cv.addEventListener('mousemove', (e) => this._move(e)); this.cv.addEventListener('mouseleave', () => { this.hover = null; this.tip.style.display = 'none'; });
    this.cv.addEventListener('click', (e) => { const h = this._hit(e); if (h && h.id && this.hooks.onSelect) this.hooks.onSelect(h.id); });
    this._size();
  }

  setInfra(infra) { this.infra = infra; this.rows = null; }
  setWindow(back, ahead) { this.back = back; this.ahead = ahead; this.draw(); }
  setData(d) { this.data = d; this.draw(); }

  _size() {
    const r = this.host.getBoundingClientRect(), dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.W = Math.max(200, r.width); this.H = Math.max(120, r.height);
    this.cv.width = this.W * dpr; this.cv.height = this.H * dpr; this.cv.style.width = this.W + 'px'; this.cv.style.height = this.H + 'px'; this.dpr = dpr;
  }

  _layout() {
    const inf = this.infra; const rows = [];
    rows.push({ key: 'main', label: t('d_main'), kind: 'main' });
    for (const tk of inf.tracks) rows.push({ key: 't' + tk.idx, label: `${tk.park}${tk.idx}`, kind: tk.kind, idx: tk.idx });
    rows.push({ key: 'L', label: t('d_throat_l'), kind: 'throat' }); rows.push({ key: 'R', label: t('d_throat_r'), kind: 'throat' });
    for (const l of inf.locos) rows.push({ key: 'l:' + l, label: l.replace('ЧМЭ3-', 'ЧМЭ-'), kind: 'loco' });
    this.rows = rows;
  }

  draw() {
    const { ctx, W, H, dpr } = this; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    if (!this.infra || !this.data) return;
    if (!this.rows) this._layout();
    const d = this.data, now = d.now, t0 = now - this.back, t1 = now + this.ahead;
    const L = 76, R = 8, T = 20, B = 4, rows = this.rows;
    const gap = 3; const sepExtra = 6;
    const nSep = 2; const avail = H - T - B - nSep * sepExtra;
    const rh = Math.max(13, Math.min(30, avail / rows.length));
    const X = (m) => L + (m - t0) / (t1 - t0) * (W - L - R);
    const rowY = {}; let y = T;
    rows.forEach((r, i) => { if (r.key === 'L' || r.key === 'l:' + this.infra.locos[0]) y += sepExtra; rowY[r.key] = y; y += rh; });
    this.hits = []; this.X = X; this.L = L; this.W2 = W - R;
    ctx.font = '11px "Segoe UI", sans-serif'; ctx.textBaseline = 'middle';
    // фон строк и подписи
    rows.forEach((r, i) => {
      const yy = rowY[r.key];
      ctx.fillStyle = i % 2 ? 'rgba(16,36,61,.025)' : 'rgba(16,36,61,.055)'; ctx.fillRect(L, yy, W - L - R, rh);
      ctx.fillStyle = r.kind === 'sorting' ? '#5a58a0' : r.kind === 'receiption' ? '#1a5fb4' : r.kind === 'loco' ? '#9a6208' : r.kind === 'throat' ? '#5f7591' : '#4a6283';
      if (r.kind === 'reception') ctx.fillStyle = '#1a5fb4';
      ctx.textAlign = 'right'; ctx.fillText(r.label, L - 8, yy + rh / 2);
    });
    // область прошлого + сетка
    ctx.fillStyle = 'rgba(16,36,61,.07)'; ctx.fillRect(L, T, Math.max(0, X(now) - L), H - T - B);
    const step = this.ahead > 240 ? 60 : 30;
    ctx.strokeStyle = 'rgba(60,90,130,.16)'; ctx.lineWidth = 1; ctx.fillStyle = '#5f7591'; ctx.textAlign = 'center';
    for (let m = Math.ceil(t0 / step) * step; m <= t1; m += step) { const x = Math.round(X(m)) + .5; ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, H - B); ctx.stroke(); ctx.fillText(hhmm(m), x, 9); }
    // закрытия и ТО
    const hatch = (x0, x1, yy, col, fillCol) => {
      ctx.save(); ctx.beginPath(); ctx.rect(x0, yy + 1, x1 - x0, rh - 2); ctx.clip(); ctx.fillStyle = fillCol; ctx.fillRect(x0, yy + 1, x1 - x0, rh - 2);
      ctx.strokeStyle = col; ctx.lineWidth = 1; for (let k = -rh; k < x1 - x0 + rh; k += 7) { ctx.beginPath(); ctx.moveTo(x0 + k, yy + rh); ctx.lineTo(x0 + k + rh, yy); ctx.stroke(); } ctx.restore();
    };
    for (const c of d.closures || []) { const yy = rowY['t' + c.track]; if (yy == null) continue; const a = Math.max(L, X(c.from)), b = Math.min(W - R, X(c.to)); if (b > a) hatch(a, b, yy, '#c9534f', 'rgba(201,83,79,.10)'); }
    for (const m of d.maint || []) { const yy = rowY['t' + m.track]; if (yy == null) continue; const a = Math.max(L, X(m.from)), b = Math.min(W - R, X(m.to)); if (b > a) hatch(a, b, yy, '#cf9a3a', 'rgba(207,154,58,.07)'); }

    // план
    const plan = d.plan; const bad = new Set();
    for (const c of d.conflicts || []) if (c.severity !== 'info') for (const tid of c.trains) bad.add(tid);
    const trainsById = d.trains || {};
    if (plan) {
      for (const it of plan.items) {
        const tr = trainsById[it.id]; if (!tr && !it.transit) continue;
        const col = TYPE_COL[(tr && tr.type) || (it.transit ? 'transit' : 'freight')];
        const rgb = col.join(',');
        const rowKey = it.transit ? 'main' : 't' + it.track; const yy = rowY[rowKey]; if (yy == null) continue;
        const x0 = X(it.arr), x1 = X(it.dep + (it.transit ? 0 : 0)); if (x1 < L || x0 > W - R) continue;
        const bx = Math.max(L, x0), bw = Math.max(2, Math.min(W - R, x1) - bx);
        const sel = this.sel === it.id, isBad = bad.has(it.id);
        const pastX = Math.min(X(now), x1);
        // план (контур, лёгкая заливка)
        ctx.fillStyle = `rgba(${rgb},.20)`; ctx.fillRect(bx, yy + 2, bw, rh - 4);
        // факт: до линии «сейчас»
        if (pastX > bx) { ctx.fillStyle = `rgba(${rgb},.78)`; ctx.fillRect(bx, yy + 2, pastX - bx, rh - 4); }
        ctx.lineWidth = sel ? 2 : 1; ctx.strokeStyle = sel ? '#10243d' : isBad ? '#e0645f' : `rgba(${rgb},.95)`; ctx.strokeRect(bx + .5, yy + 2.5, bw - 1, rh - 5);
        // операции
        for (const o of it.ops || []) {
          const a = Math.max(bx, X(o.start)), b = Math.min(bx + bw, X(o.end)); if (b <= a) continue;
          ctx.fillStyle = OP_COL[o.kind] || '#9db4cf'; ctx.fillRect(a, yy + rh - 7, b - a, 3.5);
          if (o.loco && rowY['l:' + o.loco] != null) {
            const ly = rowY['l:' + o.loco]; ctx.fillStyle = OP_COL[o.kind]; ctx.globalAlpha = o.end <= now ? .85 : .5; ctx.fillRect(a, ly + 2, b - a, rh - 4); ctx.globalAlpha = 1;
            if (b - a > 36) { ctx.fillStyle = '#10161f'; ctx.textAlign = 'left'; ctx.font = '10px "Segoe UI"'; ctx.fillText(it.id, a + 3, ly + rh / 2); ctx.font = '11px "Segoe UI"'; }
            this.hits.push({ x: a, y: ly, w: b - a, h: rh, id: it.id, it, op: o });
          }
          // манёвр в горловине L
          if (o.kind !== 'INSPECT') { const lx = X(o.start), lw = Math.max(2, X(o.start + 4) - lx); ctx.fillStyle = 'rgba(207,154,58,.75)'; ctx.fillRect(Math.max(L, lx), rowY.L + 3, lw, rh - 6); }
        }
        // метка поезда
        if (bw > 30 && !it.transit) { ctx.fillStyle = '#10243d'; ctx.textAlign = 'left'; ctx.fillText(it.id, bx + 4, yy + rh / 2 - 2); }
        // опоздание (красный хвост у верхней кромки) и ожидание на подходе
        if (it.late > 0) { const a = Math.max(L, X(it.sched_dep)); ctx.fillStyle = '#e0645f'; ctx.fillRect(a, yy + 1, Math.max(2, x1 - a), 2.5); ctx.fillRect(a, yy + 1, 1.5, 6); }
        if (it.arr > it.eta + 0.5) { ctx.strokeStyle = '#d1a03c'; ctx.setLineDash([3, 3]); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(Math.max(L, X(it.eta)), yy + rh / 2); ctx.lineTo(bx, yy + rh / 2); ctx.stroke(); ctx.setLineDash([]); }
        // горловины: приём / отправление
        const rin = 3; ctx.fillStyle = `rgba(${rgb},.9)`;
        if (!(tr && ['entering', 'on_track', 'processing', 'ready', 'departing'].includes(tr.ph))) ctx.fillRect(Math.max(L, X(it.arr)), rowY.L + 3, Math.max(2, X(it.arr + rin) - X(it.arr)), rh - 6);
        ctx.fillRect(Math.max(L, X(it.dep)), rowY.R + 3, Math.max(2, X(it.dep + rin) - X(it.dep)), rh - 6);
        this.hits.push({ x: bx, y: yy, w: bw, h: rh, id: it.id, it });
      }
    }
    // конфликты
    for (const c of d.conflicts || []) {
      if (c.severity === 'info') continue;
      const keys = [];
      if (c.track != null && !c.params.sorting) keys.push('t' + c.track);
      if (c.type === 'ROUTE_CONFLICT') keys.push(c.params.throat);
      if (c.type.startsWith('LOCO')) keys.push(...this.infra.locos.map(l => 'l:' + l));
      if (c.type === 'CREW_SHORTAGE') keys.push(...this.infra.locos.map(l => 'l:' + l));
      for (const k of keys) { const yy = rowY[k]; if (yy == null) continue; const a = Math.max(L, X(c.t_from)), b = Math.min(W - R, X(Math.max(c.t_to, c.t_from + 2))); if (b > a) { ctx.fillStyle = c.severity === 'crit' ? 'rgba(224,100,95,.30)' : 'rgba(209,160,60,.22)'; ctx.fillRect(a, yy, b - a, rh); ctx.strokeStyle = c.severity === 'crit' ? '#e0645f' : '#d1a03c'; ctx.lineWidth = 1; ctx.strokeRect(a + .5, yy + .5, b - a - 1, rh - 1); } }
    }
    // линия «сейчас»
    const nx = Math.round(X(now)) + .5; ctx.strokeStyle = '#0d4a9c'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(nx, T - 2); ctx.lineTo(nx, H - B); ctx.stroke();
    ctx.fillStyle = '#0d4a9c'; ctx.textAlign = 'left'; ctx.font = '600 10px "Segoe UI"'; ctx.fillText(t('d_dg_now') + ' ' + hhmm(now), nx + 4, T - 8 + 8); ctx.font = '11px "Segoe UI"';
  }

  _hit(e) {
    const r = this.cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    for (let i = this.hits.length - 1; i >= 0; i--) { const h = this.hits[i]; if (x >= h.x && x <= h.x + h.w && y >= h.y && y <= h.y + h.h) return h; }
    return null;
  }

  _move(e) {
    const h = this._hit(e), r = this.host.getBoundingClientRect();
    if (!h) { this.tip.style.display = 'none'; return; }
    const it = h.it, tr = this.data.trains && this.data.trains[it.id];
    this.tip.style.display = 'block'; this.tip.style.left = Math.min(r.width - 230, e.clientX - r.left + 12) + 'px'; this.tip.style.top = (e.clientY - r.top + 14) + 'px';
    this.tip.innerHTML = `<b>${it.id}</b>${tr ? ' · ' + t('tt_' + tr.type) : ''}<br>${t('d_td_track')} ${it.track ?? '—'} · ${hhmm(it.arr)} → ${hhmm(it.dep)}` +
      (it.late ? `<br><span class="bad">${t('d_td_late')}: +${it.late}</span>` : '') + (it.wait ? `<br><span class="warnc">${t('d_dg_wait')}: ${it.wait}</span>` : '') +
      (h.op ? `<br>${t('opn_' + h.op.kind)} ${hhmm(h.op.start)}–${hhmm(h.op.end)} · ${h.op.loco || ''}` : '');
  }
}
