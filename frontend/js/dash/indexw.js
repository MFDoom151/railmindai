// Виджет индекса эффективности: шкала 0–100 с зонами порогов, буква A–E, категория, динамика и топ-5 факторов потерь.
import { t } from '../i18n.js';

const NS = 'http://www.w3.org/2000/svg';
const CAT = { norm: '#4aa37a', attention: '#cf9a3a', critical: '#c9534f' };
const fmt = {
  throughput: v => Math.round(v * 100) + ' %', avg_dev: v => v.toFixed(1) + ' мин', max_dev: v => v.toFixed(0) + ' мин', track_load: v => Math.round(v * 100) + ' %',
  conflicts: v => v.toFixed(1), queue_outside: v => v.toFixed(0), loco_idle: v => Math.round(v * 100) + ' %', crew_idle: v => Math.round(v * 100) + ' %',
};

function arc(cx, cy, r, a0, a1) {                 // углы в градусах: 180 (слева) … 0 (справа)
  const p = (a) => [cx + r * Math.cos(a * Math.PI / 180), cy - r * Math.sin(a * Math.PI / 180)];
  const [x0, y0] = p(a0), [x1, y1] = p(a1);
  return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${Math.abs(a0 - a1) > 180 ? 1 : 0} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
}

export class IndexWidget {
  constructor(host, hooks = {}) {
    this.host = host; this.hooks = hooks; this.sig = ''; this.trend = [];
    host.innerHTML = `
      <div class="gauge-wrap">
        <svg viewBox="0 0 200 118" class="gauge"></svg>
        <div class="g-center"><div class="g-score num" id="gScore">–</div><div class="g-cat" id="gCat"></div></div>
        <div class="g-letter" id="gLetter">–</div>
      </div>
      <div class="lbl" style="margin:2px 0 2px">${t('d_trend')}</div>
      <svg viewBox="0 0 200 38" class="spark" preserveAspectRatio="none"></svg>
      <div class="top-h"><span>${t('d_top5')}</span><button class="link" id="gFormula">${t('d_formula')}</button></div>
      <div id="gTop"></div>`;
    this.svg = host.querySelector('.gauge'); this.spark = host.querySelector('.spark');
    host.querySelector('#gFormula').onclick = () => hooks.onFormula && hooks.onFormula();
  }

  relabel() { const h = this.host; h.querySelector('.lbl').textContent = t('d_trend'); h.querySelector('.top-h span').textContent = t('d_top5'); h.querySelector('#gFormula').textContent = t('d_formula'); this.sig = ''; }

  update(idx, thr, trend) {
    const sig = JSON.stringify([idx.score, idx.cat, idx.top.map(f => [f.key, f.loss, f.raw]), thr, trend.length && trend[trend.length - 1]]);
    if (sig === this.sig) return; this.sig = sig;
    const col = CAT[idx.cat];
    const s = this.svg; s.innerHTML = '';
    const mk = (n, a) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); s.appendChild(e); return e; };
    const ang = (v) => 180 - v * 1.8;
    mk('path', { d: arc(100, 100, 80, 180, 0), fill: 'none', stroke: '#d7e2f0', 'stroke-width': 13, 'stroke-linecap': 'butt' });
    mk('path', { d: arc(100, 100, 80, ang(0), ang(thr.attention)), fill: 'none', stroke: 'rgba(201,83,79,.35)', 'stroke-width': 13 });
    mk('path', { d: arc(100, 100, 80, ang(thr.attention), ang(thr.norm)), fill: 'none', stroke: 'rgba(207,154,58,.35)', 'stroke-width': 13 });
    mk('path', { d: arc(100, 100, 80, ang(thr.norm), ang(100)), fill: 'none', stroke: 'rgba(74,163,122,.35)', 'stroke-width': 13 });
    if (idx.score > 0.5) mk('path', { d: arc(100, 100, 80, ang(0), ang(Math.max(0.6, idx.score))), fill: 'none', stroke: col, 'stroke-width': 13, 'stroke-linecap': 'butt' });
    for (const v of [thr.attention, thr.norm]) { const a = ang(v) * Math.PI / 180; mk('line', { x1: 100 + 71 * Math.cos(a), y1: 100 - 71 * Math.sin(a), x2: 100 + 89 * Math.cos(a), y2: 100 - 89 * Math.sin(a), stroke: '#0e141c', 'stroke-width': 1.6 }); }
    const na = ang(idx.score) * Math.PI / 180;
    mk('line', { x1: 100 + 54 * Math.cos(na), y1: 100 - 54 * Math.sin(na), x2: 100 + 90 * Math.cos(na), y2: 100 - 90 * Math.sin(na), stroke: '#10243d', 'stroke-width': 2.2, 'stroke-linecap': 'round' });
    for (const [v, tx] of [[0, '0'], [100, '100']]) { const a = ang(v) * Math.PI / 180; const e = mk('text', { x: 100 + 80 * Math.cos(a), y: 114, 'text-anchor': 'middle', class: 'g-tick' }); e.textContent = tx; }
    const h = this.host;
    h.querySelector('#gScore').textContent = idx.score.toFixed(0); h.querySelector('#gScore').style.color = col;
    h.querySelector('#gCat').textContent = t('d_cat_' + idx.cat); h.querySelector('#gCat').style.color = col;
    const L = h.querySelector('#gLetter'); L.textContent = idx.letter; L.style.background = col;
    // динамика
    const sp = this.spark; sp.innerHTML = '';
    const sm = (n, a) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); sp.appendChild(e); return e; };
    const Y = (v) => 36 - v / 100 * 34;
    for (const v of [thr.attention, thr.norm]) sm('line', { x1: 0, x2: 200, y1: Y(v), y2: Y(v), stroke: 'rgba(129,144,163,.28)', 'stroke-dasharray': '2 3', 'stroke-width': .6 });
    if (trend.length > 1) {
      const d = trend.map((v, i) => `${(i / (trend.length - 1) * 200).toFixed(1)},${Y(v).toFixed(1)}`);
      sm('path', { d: 'M' + d.join('L') + `L200,38L0,38Z`, fill: col, opacity: .12 }); sm('path', { d: 'M' + d.join('L'), fill: 'none', stroke: col, 'stroke-width': 1.4, 'vector-effect': 'non-scaling-stroke' });
    }
    // топ-5
    const mx = Math.max(5, ...idx.top.map(f => f.loss));
    h.querySelector('#gTop').innerHTML = idx.top.map(f => {
      const w = Math.min(100, f.loss / mx * 100), c = f.loss >= 6 ? '#c9534f' : f.loss >= 2 ? '#cf9a3a' : '#5b8fc9';
      return `<div class="fct" title="${t('fh_' + f.key)} · ${t('d_weight')} ${(f.weight * 100).toFixed(0)}% · ${t('d_score')} ${f.score.toFixed(2)}">
        <div class="fct-top"><span>${t('f_' + f.key)}</span><b class="num" style="color:${f.loss >= 0.05 ? c : '#0f6d49'}">${f.loss >= 0.05 ? '−' + f.loss.toFixed(1) : '0.0'}</b></div>
        <div class="bar"><i style="width:${w}%;background:${c}"></i></div><div class="fct-raw num">${fmt[f.key](f.raw)}</div></div>`;
    }).join('');
  }
}
