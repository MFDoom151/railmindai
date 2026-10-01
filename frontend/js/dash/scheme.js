// Схема станции (SVG): пути, парки, платформы, горловины, стрелки, депо; занятость путей, поезда и локомотивы в движении.
// Поезда рисуются «змейкой» вдоль маршрута (подход → горловина → путь → отправление), поэтому корректно проходят изгибы.
import { t as T } from '../i18n.js';

const NS = 'http://www.w3.org/2000/svg';
const ZS = 4.3;                        // вертикальный масштаб схемы
const WAG = 3.33;                      // длина вагона, ед. схемы (15 м)
const STOP_GAP = 8;                    // отступ головы состава от конца пути
const COL = { freight: '#5b8fc9', passenger: '#4aa37a', transit: '#8f9bab' };
const el = (n, a = {}, parent) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); if (parent) parent.appendChild(e); return e; };

function polyLen(p) { let l = 0; for (let i = 1; i < p.length; i++) l += Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]); return l; }
function pointAt(p, s) {
  if (s <= 0) return p[0];
  for (let i = 1; i < p.length; i++) { const d = Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]); if (s <= d) { const k = d ? s / d : 0; return [p[i - 1][0] + (p[i][0] - p[i - 1][0]) * k, p[i - 1][1] + (p[i][1] - p[i - 1][1]) * k]; } s -= d; }
  return p[p.length - 1];
}
function subPath(p, s0, s1) {          // фрагмент ломаной между расстояниями s0..s1
  const total = polyLen(p); s0 = Math.max(0, s0); s1 = Math.min(total + 400, s1);
  const pts = [pointAt(p, s0)]; let acc = 0;
  for (let i = 1; i < p.length; i++) { const d = Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]); acc += d; if (acc > s0 && acc < s1) pts.push(p[i]); }
  if (s1 > total) { const a = p[p.length - 2], b = p[p.length - 1], d = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1; pts.push([b[0] + (b[0] - a[0]) / d * (s1 - total), b[1] + (b[1] - a[1]) / d * (s1 - total)]); } else pts.push(pointAt(p, s1));
  return 'M' + pts.map(q => q[0].toFixed(1) + ',' + q[1].toFixed(1)).join('L');
}

export class Scheme {
  constructor(host, hooks = {}) {
    this.host = host; this.hooks = hooks; this.infra = null; this.sel = null; this.items = new Map(); this.locoEls = new Map(); this.raf = null; this.last = null;
    this.svg = el('svg', { class: 'scheme', preserveAspectRatio: 'xMidYMid meet' }); host.appendChild(this.svg);
    this.tip = document.createElement('div'); this.tip.className = 'tip'; host.appendChild(this.tip);
  }

  setInfra(infra) {
    this.infra = infra; this.items.clear(); this.locoEls.clear();
    const g = infra.geometry; this.g = g; this.n = g.n;
    this.tracks = Object.fromEntries(g.tracks.map(t => [t.idx, t]));
    const dz = g.n > 1 ? g.tracks[1].z : 4.2; this.dz = dz;
    const H = (g.n * dz + 6) * ZS + 38;
    this.svg.setAttribute('viewBox', `-172 -26 344 ${H}`);
    this.svg.innerHTML = '';
    const defs = el('defs', {}, this.svg);
    const pat = el('pattern', { id: 'hatch', width: 5, height: 5, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' }, defs); el('rect', { width: 5, height: 5, fill: 'rgba(201,83,79,.12)' }, pat); el('line', { x1: 0, y1: 0, x2: 0, y2: 5, stroke: '#c9534f', 'stroke-width': 1.6 }, pat);
    const pat2 = el('pattern', { id: 'hatchm', width: 5, height: 5, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(-45)' }, defs); el('rect', { width: 5, height: 5, fill: 'rgba(207,154,58,.08)' }, pat2); el('line', { x1: 0, y1: 0, x2: 0, y2: 5, stroke: '#cf9a3a', 'stroke-width': 1.2 }, pat2);
    const bg = el('g', { class: 'bg' }, this.svg), lines = el('g', {}, this.svg); this.gTrack = el('g', {}, this.svg); this.gOver = el('g', {}, this.svg); this.gTrain = el('g', {}, this.svg); this.gTop = el('g', {}, this.svg);
    const Y = (z) => z * ZS;
    // платформы и подписи парков
    for (const t of infra.tracks) {
      const tr = this.tracks[t.idx]; if (!tr) continue;
      if (t.platform) el('rect', { x: tr.x0 + 4, y: Y(tr.z) + 3, width: tr.x1 - tr.x0 - 8, height: 4, rx: 1, fill: '#2a3a4e' }, bg);
    }
    // главный путь и подход
    el('line', { x1: -168, y1: 0, x2: 168, y2: 0, class: 'rail main' }, lines);
    const lab = (txt, x, y, cls = 'lbl', anchor = 'start') => { const e = el('text', { x, y, class: cls, 'text-anchor': anchor }, this.gTop); e.textContent = txt; return e; };
    this.labApproach = lab(T('d_approach').toUpperCase(), -166, -9, 'lbl dim'); this.labMain = lab(T('d_main').toUpperCase(), 166, -9, 'lbl dim', 'end');
    // лестницы горловин
    const poly = (pts) => 'M' + pts.map(p => `${p[0]},${Y(p[1])}`).join('L');
    el('path', { d: poly(g.left_lead), class: 'rail lead' }, lines); el('path', { d: poly(g.right_lead), class: 'rail lead' }, lines);
    // пути
    this.trackEls = {};
    for (const t of infra.tracks) {
      const tr = this.tracks[t.idx]; const y = Y(tr.z);
      const grp = el('g', { class: 'trk' }, this.gTrack);
      const base = el('line', { x1: tr.x0, y1: y, x2: tr.x1, y2: y, class: 'rail ' + (t.kind === 'sorting' ? 'sort' : 'recv') }, grp);
      const occ = el('line', { x1: tr.x0, y1: y, x2: tr.x1, y2: y, class: 'rail occ', opacity: 0 }, grp);
      const hatch = el('rect', { x: tr.x0, y: y - 4, width: tr.x1 - tr.x0, height: 8, fill: 'url(#hatch)', opacity: 0 }, grp);
      const mark = el('text', { x: (tr.x0 + tr.x1) / 2, y: y + 2.2, class: 'lbl bad', 'text-anchor': 'middle', opacity: 0 }, grp); mark.textContent = '✕  ' + T('d_closed_n').toUpperCase();
      const l1 = el('text', { x: tr.x0 - 6, y: y + 2.4, class: 'lbl ' + (t.kind === 'sorting' ? 'sortc' : 'recvc'), 'text-anchor': 'end' }, grp); l1.textContent = `${t.park}${t.idx}`;
      const l2 = el('text', { x: tr.x1 + 6, y: y + 2.4, class: 'lbl dim', 'text-anchor': 'start' }, grp); l2.textContent = `${Math.round(t.length_m)} м`;
      const sbar = el('line', { x1: tr.x0 + 6, y1: y + 3.6, x2: tr.x1 - 6, y2: y + 3.6, stroke: '#cf9a3a', 'stroke-width': 1.4, opacity: 0, 'stroke-dasharray': '3 2' }, grp);
      const maint = el('rect', { x: tr.x0, y: y - 4, width: tr.x1 - tr.x0, height: 8, fill: 'url(#hatchm)', opacity: 0, stroke: '#cf9a3a', 'stroke-dasharray': '2 2', 'stroke-width': .5 }, grp);
      this.trackEls[t.idx] = { occ, hatch, mark, sbar, maint, y };
    }
    // стрелки
    for (const s of g.switches) el('circle', { cx: s.x, cy: Y(s.z), r: 1.1, class: 'sw' }, this.gTrack);
    // депо
    const dp = g.depot, dy = Y(dp.z);
    el('line', { x1: dp.x0 - 6, y1: dy, x2: dp.x1, y2: dy, class: 'rail depot' }, lines); el('line', { x1: dp.x1, y1: dy, x2: dp.merge_x, y2: 0, class: 'rail depot' }, lines);
    lab(T('d_depot').toUpperCase() + ' · ЧМЭ3', dp.x0 - 6, dy - 6, 'lbl dim');
    // горловины
    this.thL = el('circle', { cx: g.left_lead[0][0], cy: -10, r: 3, class: 'throat' }, this.gTop); this.thR = el('circle', { cx: -g.left_lead[0][0], cy: -10, r: 3, class: 'throat' }, this.gTop);
    lab('L', g.left_lead[0][0] + 6, -7.6, 'lbl dim'); lab('R', -g.left_lead[0][0] + 6, -7.6, 'lbl dim');
    // пути движения
    const rl = g.right_lead; this.pathA = {}; this.pathD = {};
    for (const t of infra.tracks) {
      const tr = this.tracks[t.idx]; const i = t.idx; const head = tr.x1 - STOP_GAP;
      this.pathA[i] = [[-168, 0], ...g.left_lead.slice(0, i + 1).map(p => [p[0], Y(p[1])]), [head, Y(tr.z)]];
      this.pathD[i] = [[head, Y(tr.z)], [tr.x1, Y(tr.z)], ...rl.slice(0, i).reverse().map(p => [p[0], Y(p[1])]), [rl[0][0], 0], [168, 0]];
    }
    this.pathMain = [[-168, 0], [168, 0]];
    this.holdS = (g.left_lead[0][0] + 168) - 26;          // где поезд ждёт сигнала у входа
  }

  // ---------------------------------------------------------------------------------------------------------------
  update(frame, plan, conflicts, closuresAll, maintActive) {
    if (!this.infra || !frame) return;
    this.last = frame;
    const planItems = plan ? Object.fromEntries(plan.items.map(i => [i.id, i])) : {};
    const lateOf = (id) => (planItems[id] ? planItems[id].late : 0);
    // состояние путей
    const closed = new Set(frame.closures.map(c => c.track));
    const maint = new Map(maintActive.map(m => [m.track, m]));
    for (const tk of frame.tracks) {
      const e = this.trackEls[tk.idx]; if (!e) continue;
      const isClosed = closed.has(tk.idx) || (maint.has(tk.idx) && maint.get(tk.idx).now);
      e.hatch.setAttribute('opacity', isClosed ? 1 : 0); e.mark.setAttribute('opacity', closed.has(tk.idx) ? 1 : 0);
      e.occ.setAttribute('opacity', tk.s === 'occupied' ? 1 : 0);
      e.sbar.setAttribute('opacity', tk.s === 'sort_busy' ? 1 : 0);
      const mt = maint.get(tk.idx); e.maint.setAttribute('opacity', mt && !mt.now ? 1 : 0);
    }
    // поезда
    const seen = new Set();
    const queue = frame.trains.filter(x => x.ph === 'waiting').sort((a, b) => a.eta - b.eta);
    const approaching = frame.trains.filter(x => x.ph === 'approach');
    const wcount = { n: 0 };
    for (const tr of frame.trains) {
      seen.add(tr.id);
      let item = this.items.get(tr.id);
      if (!item) item = this._mk(tr);
      item.data = tr; item.late = lateOf(tr.id);
      const len = (tr.type === 'transit' ? 0.5 : 1) * tr.w * WAG + 5;
      let path, head;
      const track = tr.tr;
      if (tr.type === 'transit') { path = this.pathMain; head = tr.ph === 'entering' ? this.holdS + tr.pg * (168 - this.holdS) : tr.ph === 'departing' ? 168 + tr.pg * (168 + len) : tr.ph === 'waiting' ? this.holdS : tr.pg * this.holdS; }
      else if (['approach', 'waiting', 'entering', 'on_track', 'processing', 'ready'].includes(tr.ph)) {
        const tk = track ?? this._planTrack(tr.id, planItems) ?? this.infra.tracks[0].idx;
        path = this.pathA[tk] || this.pathA[this.infra.tracks[0].idx];
        const total = polyLen(path);
        if (tr.ph === 'approach') head = tr.pg * this.holdS;
        else if (tr.ph === 'waiting') { const qi = queue.indexOf(tr); head = this.holdS - Math.max(0, qi) * 60; }
        else if (tr.ph === 'entering') head = this.holdS + tr.pg * (total - this.holdS);
        else head = total;
      } else { // departing
        path = this.pathD[track] || this.pathD[this.infra.tracks[0].idx]; head = tr.pg * (polyLen(path) + len);
      }
      item.path = path; item.len = len; item.target = head;
      if (item.cur == null) item.cur = head;
      if (item.stale !== tr.stale) { item.stale = tr.stale; item.body.setAttribute('opacity', tr.stale ? .45 : 1); }
      this._style(item, tr);
    }
    for (const [id, it] of this.items) if (!seen.has(id)) { it.g.remove(); this.items.delete(id); }
    // локомотивы
    const depot = this.g.depot; let k = 0;
    for (const lo of frame.locos) {
      let le = this.locoEls.get(lo.id);
      if (!le) { const gg = el('g', { class: 'loco' }, this.gTrain); le = { g: gg, r: el('rect', { width: 8, height: 5.2, rx: 1.2 }, gg), tx: el('text', { class: 'lbl tiny', 'text-anchor': 'middle', y: -2 }, gg) }; le.tx.textContent = lo.id.replace('ЧМЭ3-', 'ЧМ'); this.locoEls.set(lo.id, le); }
      let x, y;
      if (lo.s === 'busy' && lo.tr != null && this.trackEls[lo.tr]) {
        const tr = frame.trains.find(q => q.tr === lo.tr && q.ph !== 'approach' && q.ph !== 'waiting'); const tk = this.tracks[lo.tr];
        const len = tr ? tr.w * WAG + 5 : 40; x = tk.x1 - STOP_GAP - len - 5; y = this.trackEls[lo.tr].y;
      } else { x = depot.x0 + 8 + k * 12; y = depot.z * ZS; k++; }
      le.g.setAttribute('transform', `translate(${x.toFixed(1)},${(y - 2.6).toFixed(1)})`);
      le.g.setAttribute('class', 'loco ' + lo.s);
    }
    // горловины
    this.thL.setAttribute('class', 'throat' + (frame.trains.some(x => x.ph === 'entering') ? ' busy' : ''));
    this.thR.setAttribute('class', 'throat' + (frame.trains.some(x => x.ph === 'departing') ? ' busy' : ''));
    // конфликты
    this.gOver.innerHTML = '';
    const byTrack = new Set();
    for (const c of conflicts || []) {
      if (c.severity === 'info' || c.track == null || !this.trackEls[c.track] || byTrack.has(c.track)) continue; byTrack.add(c.track);
      const tk = this.tracks[c.track];
      const r = el('rect', { x: tk.x0 - 2, y: this.trackEls[c.track].y - 5.5, width: tk.x1 - tk.x0 + 4, height: 11, rx: 3, class: 'conf-ring ' + c.severity }, this.gOver);
    }
    this._animate();
  }

  _planTrack(id, items) { const it = items[id]; return it ? it.track : null; }

  _mk(tr) {
    const g = el('g', { class: 'train' }, this.gTrain);
    const body = el('path', { class: 'tr-body', fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, g);
    const prog = el('path', { class: 'tr-prog', fill: 'none', 'stroke-linecap': 'butt' }, g);
    const tx = el('text', { class: 'tr-lbl', 'text-anchor': 'middle' }, g); tx.textContent = tr.id;
    const sub = el('text', { class: 'tr-sub', 'text-anchor': 'middle' }, g);
    g.addEventListener('mouseenter', (e) => this._tip(e, item)); g.addEventListener('mousemove', (e) => this._tip(e, item)); g.addEventListener('mouseleave', () => { this.tip.style.display = 'none'; });
    g.addEventListener('click', () => this.hooks.onSelect && this.hooks.onSelect(tr.id));
    const item = { g, body, prog, tx, sub, cur: null, target: 0, data: tr, path: null, len: 0, late: 0 };
    this.items.set(tr.id, item);
    return item;
  }

  _style(item, tr) {
    const base = COL[tr.type] || '#8f9bab';
    const late = item.late;
    const warn = late >= 8, crit = late >= 20;
    item.body.setAttribute('stroke', base);
    item.g.setAttribute('class', `train ph-${tr.ph} ${crit ? 'crit' : warn ? 'warn' : ''} ${this.sel === tr.id ? 'sel' : ''}`);
    item.sub.textContent = (tr.ph === 'waiting' ? `⏱ ${T('d_wait')}` : tr.ph === 'processing' && tr.op ? T('opn_' + tr.op) : late >= 5 ? `+${late}` : '');
  }

  _tip(e, item) {
    const d = item.data, r = this.host.getBoundingClientRect();
    this.tip.style.display = 'block'; this.tip.style.left = Math.min(r.width - 210, e.clientX - r.left + 12) + 'px'; this.tip.style.top = (e.clientY - r.top + 14) + 'px';
    this.tip.innerHTML = `<b>${d.id}</b> · ${T('tt_' + d.type)}<br>${T('ph_' + d.ph)}${d.tr != null ? ` · ${T('d_td_track')} ${d.tr}` : ''}<br>${d.w} ваг. · pr ${d.pr}${item.late ? `<br><span class="bad">+${item.late} мин</span>` : ''}`;
  }

  select(id) { this.sel = id; for (const it of this.items.values()) it.g.classList.toggle('sel', it.data.id === id); }

  // плавное движение между кадрами 2 Гц
  _animate() {
    if (this.raf) return;
    const step = () => {
      let busy = false;
      for (const it of this.items.values()) {
        if (!it.path) continue;
        const d = it.target - it.cur;
        if (Math.abs(d) > 0.25) { it.cur += d * 0.22; busy = true; } else it.cur = it.target;
        const tail = it.cur - it.len;
        it.body.setAttribute('d', subPath(it.path, tail, it.cur));
        const mid = pointAt(it.path, Math.max(0, it.cur - it.len / 2));
        it.tx.setAttribute('x', mid[0].toFixed(1)); it.tx.setAttribute('y', (mid[1] + 1.7).toFixed(1));
        it.sub.setAttribute('x', mid[0].toFixed(1)); it.sub.setAttribute('y', (mid[1] - 6.4).toFixed(1));
        if (it.data.ph === 'processing' && it.data.nops) { const f = Math.min(1, (it.data.od + 0.5) / it.data.nops); it.prog.setAttribute('d', subPath(it.path, tail, tail + it.len * f)); it.prog.setAttribute('opacity', 1); } else it.prog.setAttribute('opacity', 0);
      }
      this.raf = busy ? requestAnimationFrame(step) : null;
    };
    this.raf = requestAnimationFrame(step);
  }
}
