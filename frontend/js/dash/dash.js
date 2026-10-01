// Пульт диспетчера «Цифровая станция»: онлайн-поток, схема, диаграмма движения, индекс, конфликты, сбои и альтернативные планы,
// перемотка, отчёты, вход и настройки. Всё состояние приходит с бэкенда по WebSocket (SSE — запасной канал).
import { t, getLang, stName } from '../i18n.js';
import { StationStream } from './stream.js';
import { Scheme } from './scheme.js';
import { Diagram } from './diagram.js';
import { IndexWidget } from './indexw.js';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const hhmm = (m) => { m = Math.round(m) % 1440; if (m < 0) m += 1440; return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); };
const KINDS = ['train_delay', 'track_close', 'switch_fail', 'loco_down', 'crew_absent', 'power_fail', 'burst'];
const RING = 1800;

const D = {
  station: 'almaty1', stations: [], infra: null, formula: null, planner: null, live: null, ring: [], plan: null, plans: [], conflicts: [], alts: null, appliedAlt: null,
  alerts: [], selected: null, replay: null, playing: null, auth: { role: 'viewer', token: null, user: null, demo: null }, status: {}, thr: { norm: 75, attention: 50 },
  active: true, renderQ: false, lastDiag: 0, scale: 1, paused: false, resolved: new Set(), disTotal: 0, cfg: null,
};
let scheme, diagram, indexw, stream;

// ====================================================================================================================
async function api(path, opt = {}) {
  const headers = { 'Content-Type': 'application/json', ...(D.auth.token ? { Authorization: 'Bearer ' + D.auth.token } : {}), ...(opt.headers || {}) };
  const r = await fetch(path, { ...opt, headers, body: opt.body ? JSON.stringify(opt.body) : undefined });
  if (!r.ok) { let d = null; try { d = await r.json(); } catch (e) {} const err = new Error(`${r.status}`); err.status = r.status; err.detail = d; throw err; }
  return r.json();
}
function toast(msg, level = 'info', ms = 4200) {
  const d = document.createElement('div'); d.className = 'toast ' + level; d.textContent = msg; $('#toasts').appendChild(d); setTimeout(() => d.remove(), ms);
}
function need(role = 'dispatcher') {
  const rank = { viewer: 0, dispatcher: 1, admin: 2 };
  if (rank[D.auth.role] >= rank[role]) return true;
  toast(t('d_need_role'), 'warn'); openAuth(); return false;
}

// ====================================================================================================================
function planAt(sim) {
  if (!D.plans.length) return D.plan;
  let best = null;
  for (const p of D.plans) if (p.created <= sim + 0.01) best = p;
  return best || D.plans[0];
}
function expandMaint(infra, now) {
  const out = [];
  for (let d = Math.floor((now - 120) / 1440) - 1; d <= Math.floor((now + 600) / 1440) + 1; d++) for (const m of infra.maintenance) {
    const a = d * 1440 + m.from, b = d * 1440 + m.to; if (b > now - 60 && a < now + 480) out.push({ track: m.track, from: a, to: b, now: a <= now && now < b });
  }
  return out;
}
const curFrame = () => (D.replay != null ? D.ring[Math.min(D.replay, D.ring.length - 1)] : D.live);

// ====================================================================================================================
function onMessage(m) {
  switch (m.type) {
    case 'hello':
      D.infra = m.infra; D.formula = m.index_formula; D.planner = m.planner; D.thr = m.index_formula.thresholds; D.scale = m.sim.scale;
      scheme.setInfra(m.infra); diagram.setInfra(m.infra); $('#dAuto').checked = !!m.planner.auto_apply; D.cfgVersion = m.config_version;
      D.plans = []; D.plan = null; D.conflicts = []; D.alts = null; D.ring = []; D.resolved.clear(); D.selected = null; buildKinds(); renderThr();
      break;
    case 'plan':
      if (m.plan) { D.plan = m.plan; D.plans.push({ created: m.plan.created, version: m.plan.version, items: m.plan.items }); D.plans = D.plans.slice(-60); }
      break;
    case 'conflicts': D.conflicts = m.list; D.resolved.clear(); renderAlerts(); break;
    case 'alternatives': D.alts = m; D.appliedAlt = m.applied; renderAlts(); break;
    case 'alternatives_applied': D.appliedAlt = m.id; if (D.alts) D.alts.applied = m.id; renderAlts(); toast(t('al_PLAN_APPLIED', { alt: m.id.split('-').slice(1).join('-'), auto: m.auto ? t('d_auto') : '' }), 'ok'); break;
    case 'alerts': D.alerts = m.list; renderMarks(); break;
    case 'alert': D.alerts.push(m); D.alerts = D.alerts.slice(-200); alertToast(m); renderMarks(); break;
    case 'backfill': D.ring = m.frames.slice(-RING); syncSlider(); break;
    case 'state':
      D.live = m; D.ring.push(m); if (D.ring.length > RING) D.ring.shift(); D.paused = m.paused; D.scale = m.scale;
      if (D.replay != null) D.replay = Math.max(0, D.replay - (D.ring.length >= RING ? 1 : 0));
      syncSlider(); header(m); schedule(); break;
  }
}
let toastAcc = 0, toastTimer = null;
function alertToast(a) {                       // уведомления агрегируются: при всплеске не засоряем экран
  if (a.code !== 'DISRUPTION') return;
  toastAcc++; clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast(toastAcc === 1 ? t('al_DISRUPTION', { kind: t('dk_' + a.params.kind) }) : t('d_disr_n', { n: toastAcc }), 'warn'); toastAcc = 0; }, 700);
}

function onStatus(s) {
  D.status = s; const c = $('#dConn'); c.className = 'conn ' + s.state;
  const tr = s.transport.toUpperCase();
  const txt = { connecting: t('d_connecting'), online: `${t('d_online')} · ${s.hz ? s.hz.toFixed(1) : '–'} Гц · ${tr}${s.rtt != null ? ` · ${t('d_rtt')} ${s.rtt} мс` : ''}`, stale: `${t('d_stale')} · ${tr}`,
    offline: `${t('d_offline')} · ${t('d_retry', { s: s.retryIn, n: s.attempt })}` }[s.state];
  $('#dConnTxt').textContent = txt;
}

// ====================================================================================================================
function schedule() { if (D.renderQ || !D.active) return; D.renderQ = true; requestAnimationFrame(render); }

function header(f) {
  $('#kEff').textContent = Math.round(f.index.score); const L = $('#kLetter'); L.textContent = f.index.letter; L.style.background = { norm: '#4aa37a', attention: '#cf9a3a', critical: '#c9534f' }[f.index.cat];
  $('#kDwell').textContent = f.conflicts.crit + f.conflicts.warn; $('#kPrev').textContent = 'v' + f.plan_v; $('#kPrevN').textContent = '/ ' + (D.alerts.filter(a => a.code === 'DISRUPTION').length);
  $('#kIdxL').textContent = t('kpi_index') + ' · ' + (D.infra ? (D.infra.names[getLang()] || D.infra.names.ru) : '');
}

function render() {
  D.renderQ = false; const f = curFrame(); if (!f || !D.infra) return;
  const live = D.replay == null;
  const plan = planAt(f.sim);
  $('#dSimTime').textContent = hhmm(f.sim); $('#dPause').textContent = D.paused ? t('d_resume') : t('d_pause');
  // индекс и тренд
  const i1 = D.replay != null ? D.replay : D.ring.length - 1; const trend = D.ring.slice(Math.max(0, i1 - 150), i1 + 1).map(x => x.index.score);
  indexw.update(f.index, D.thr, trend);
  // ресурсы
  const busyT = f.tracks.filter(x => x.s === 'occupied' || x.s === 'sort_busy').length, closedT = f.tracks.filter(x => x.s === 'closed').length, nT = f.tracks.length;
  const down = f.locos.filter(x => x.s === 'down').length, busyL = f.locos.filter(x => x.s === 'busy').length;
  const lE = f.trains.some(x => x.ph === 'entering'), rE = f.trains.some(x => x.ph === 'departing');
  const rm = (label, val, max, col, extra = '') => `<div class="rm"><div class="t"><span>${label}</span><b class="num">${val} / ${max}${extra}</b></div><div class="bar"><i style="width:${max ? Math.min(100, val / max * 100) : 0}%;background:${col}"></i></div></div>`;
  $('#dRes').innerHTML = rm(t('d_r_tracks'), busyT, nT, busyT / nT > .85 ? '#c9534f' : '#5b8fc9', closedT ? ` · ${t('d_closed_n')} ${closedT}` : '') +
    rm(t('d_r_locos'), busyL, f.locos.length, busyL >= f.locos.length - down ? '#cf9a3a' : '#5b8fc9', down ? ` · ${t('d_down')} ${down}` : '') +
    rm(t('d_r_crews'), f.crews.busy, f.crews.total, f.crews.busy >= f.crews.total - f.crews.absent ? '#cf9a3a' : '#5b8fc9', f.crews.absent ? ` · ${t('d_absent')} ${f.crews.absent}` : '') +
    `<div class="rm"><div class="t"><span>${t('d_r_throats')}</span><b class="num">L ${lE ? '●' : '○'}  R ${rE ? '●' : '○'}${f.env.route_extra ? ' · +' + f.env.route_extra + ' мин' : ''}</b></div></div>`;
  // схема
  const maint = D.infra ? expandMaint(D.infra, f.sim) : [];
  scheme.update(f, plan, live ? D.conflicts : [], null, maint.filter(m => m.from < f.sim + 90));
  // диаграмма (не чаще 3 раз/с)
  const nowMs = performance.now();
  if (nowMs - D.lastDiag > 330 || D.replay != null) {
    D.lastDiag = nowMs;
    diagram.setData({ now: f.sim, plan, conflicts: live ? D.conflicts : [], closures: f.closures, maint, trains: Object.fromEntries(f.trains.map(x => [x.id, x])) });
  }
  // ввод-вывод
  const I = f.ingest; $('#dIngestTxt').textContent = t('d_ingest_txt', { recv: I.recv, applied: I.applied, dup: I.dup, inv: I.inv, stale: I.stale }); $('#dIngest').title = `latency avg ${I.lat_ms} ms · p95 ${I.p95} ms · tick ${f.tick_ms} ms`;
  $('#dConfN').textContent = f.conflicts.crit + f.conflicts.warn + (f.conflicts.info ? ' + ' + f.conflicts.info : ''); $('#dConfN').className = 'badge num ' + (f.conflicts.crit ? 'crit' : f.conflicts.warn ? 'warn' : 'ok');
  if (D.selected) renderTrainCard(f, plan);
  // перемотка
  const b = $('#dBanner');
  if (!live) { b.style.display = ''; const last = D.ring[D.ring.length - 1]; const ago = Math.max(0, Math.round(last.wall - f.wall)); b.textContent = t('d_replay_banner', { ago: `${Math.floor(ago / 60)}:${String(ago % 60).padStart(2, '0')}`, sim: hhmm(f.sim) }); } else b.style.display = 'none';
  $('#dRLabel').textContent = live ? `${t('d_live')} · ${hhmm(f.sim)}` : `${hhmm(f.sim)}`;
  if (D.paused) $('#dConnTxt').textContent += ' · ' + t('d_pause');
}

// ====================================================================================================================
function cfText(c) {
  const p = c.params || {};
  return t('cf_' + c.type, { track: c.track ?? p.track ?? '', trains: c.trains.join(', '), minutes: p.minutes ?? '', throat: p.throat ?? '', loco: p.loco ?? '', need: p.need ?? '', have: p.have ?? '', wagons: p.wagons ?? '', cap: p.cap ?? '' });
}
function renderAlerts() {
  const f = D.live; const cs = D.conflicts || [];
  let h = '';
  // рекомендации
  h += `<div class="sech">${t('d_recs')}</div>`;
  const recs = [];
  const best = D.alts && D.alts.stage === 'final' && !D.alts.applied ? D.alts.alts.find(a => a.recommended) : null;
  if (best) recs.push({ txt: `${t('alt_' + best.label)}: ${t('d_h_idx').toLowerCase()} ${best.index.score.toFixed(0)} (${best.delta_index >= 0 ? '+' : ''}${best.delta_index})`, act: best.id });
  const top = (f ? f.index.top : []).filter(x => x.loss >= 2).slice(0, 2);
  for (const x of top) recs.push({ txt: t('rc_' + x.key) });
  if (!recs.length) h += `<div class="rec ok">${t('rc_ok')}</div>`;
  for (const r of recs) h += `<div class="rec"><span>${r.txt}</span>${r.act ? `<button class="btn primary xs" data-apply="${r.act}">${t('d_apply')}</button>` : ''}</div>`;
  // конфликты
  h += `<div class="sech">${t('d_conf_sec')}</div>`;
  const shown = cs.filter(c => c.severity !== 'info' || cs.length < 6).slice(0, 14);
  if (!cs.length) h += `<div class="rec ok">${t('d_no_conf')}</div>`;
  for (const c of shown) {
    const ress = (c.resolutions || []).map((r, i) => {
      const label = t('rs_' + r.code, { ...r.params, res: t('d_res_' + (r.params.res || 'loco')) });
      const key = c.id + ':' + r.code;
      return `<button class="btn xs ${D.resolved.has(key) ? 'ok' : ''}" data-res="${encodeURIComponent(JSON.stringify({ code: r.code, params: r.params, key }))}" ${D.resolved.has(key) ? 'disabled' : ''}>${D.resolved.has(key) ? '✓ ' : ''}${label}${r.delta_min != null ? ` <span style="opacity:.7">(${r.delta_min > 0 ? '+' : ''}${r.delta_min} мин)</span>` : ''}</button>`;
    }).join('');
    h += `<div class="cf ${c.severity}"><div class="h"><span>${cfText(c)}</span><span class="badge ${c.severity === 'crit' ? 'crit' : c.severity === 'warn' ? 'warn' : 'mu'}">${t('d_sev_' + c.severity)}</span></div><div class="m num">${hhmm(c.t_from)}–${hhmm(c.t_to)}</div><div class="acts">${ress}</div></div>`;
  }
  $('#dAlerts').innerHTML = h;
  $$('#dAlerts [data-res]').forEach(b => b.onclick = async () => {
    if (!need()) return; const r = JSON.parse(decodeURIComponent(b.dataset.res)); b.disabled = true;
    try { await api(`/api/v1/stations/${D.station}/resolve`, { method: 'POST', body: { code: r.code, params: r.params } }); D.resolved.add(r.key); b.classList.add('ok'); b.textContent = '✓ ' + b.textContent; toast(t('d_applied'), 'ok'); } catch (e) { b.disabled = false; toast(String(e.message), 'crit'); }
  });
  $$('#dAlerts [data-apply]').forEach(b => b.onclick = () => applyAlt(b.dataset.apply));
}

// ---- карточка поезда ----
function renderTrainCard(f, plan) {
  const box = $('#dTrain'); const tr = f.trains.find(x => x.id === D.selected);
  if (!tr) { box.style.display = 'none'; return; }
  const it = plan ? plan.items.find(x => x.id === D.selected) : null;
  box.style.display = '';
  box.innerHTML = `<h4><span>${tr.id}</span><button class="btn xs" id="dTrainX">×</button></h4>
    <div class="r"><span>${t('tt_' + tr.type)}</span><b>${tr.w} ваг. · pr ${tr.pr}</b></div>
    <div class="r"><span>${t('ph_' + tr.ph)}</span><b>${tr.tr != null ? t('d_td_track') + ' ' + tr.tr : ''}</b></div>
    <div class="r"><span>${t('d_td_sched')}</span><b class="num">${hhmm(tr.sa)} → ${hhmm(tr.sd)}</b></div>
    <div class="r"><span>${t('d_td_eta')}</span><b class="num">${hhmm(tr.eta)}</b></div>
    ${it ? `<div class="r"><span>${t('d_td_plan')}</span><b class="num">${t('d_td_track')} ${it.track ?? '—'} · ${hhmm(it.arr)} → ${hhmm(it.dep)}</b></div>
    <div class="r"><span>${t('d_td_late')}</span><b class="num" style="color:${it.late >= 20 ? '#ec8b87' : it.late >= 8 ? '#e6bf72' : '#8fd0b1'}">+${it.late} мин</b></div>
    ${it.ops.length ? `<div class="r"><span>${t('d_td_ops')}</span><b></b></div>` + it.ops.map(o => `<div class="r"><span>${t('opn_' + o.kind)}</span><b class="num">${hhmm(o.start)}–${hhmm(o.end)}${o.loco ? ' · ' + o.loco.replace('ЧМЭ3-', 'ЧМЭ-') : ''}</b></div>`).join('') : ''}` : ''}`;
  $('#dTrainX').onclick = () => selectTrain(null);
}
function selectTrain(id) { D.selected = id; scheme.select(id); diagram.sel = id; $('#dTrain').style.display = id ? '' : 'none'; schedule(); diagram.draw(); }

// ====================================================================================================================
function buildKinds() {
  const sel = $('#dKind'); const tracks = D.infra ? D.infra.tracks.filter(x => x.kind === 'reception').map(x => x.idx) : [1];
  const mine = D.stations.find(s => s.id === D.station);
  sel.innerHTML = KINDS.map(k => `<option value="${k}">${t('dk_' + k)}</option>`).join('');
  sel.onchange = () => paramsFor(sel.value, tracks); paramsFor(sel.value, tracks);
}
function paramsFor(kind, tracks) {
  const num = (id, v, w = 62) => `<input class="inp" id="${id}" type="number" value="${v}" style="width:${w}px">`;
  const tsel = `<select class="inp" id="pTrack" style="width:62px">${tracks.map(x => `<option>${x}</option>`).join('')}</select>`;
  const h = { train_delay: num('pMin', 25) + t('d_p_min'), track_close: t('d_p_track') + ' ' + tsel + num('pMin', 60) + t('d_p_min'), switch_fail: t('d_p_track') + ' ' + tsel + num('pMin', 60) + t('d_p_min'),
    loco_down: num('pMin', 90) + t('d_p_min'), crew_absent: num('pN', 1, 44) + '× ' + num('pMin', 120) + t('d_p_min'), power_fail: num('pMin', 60) + t('d_p_min'), burst: num('pN', 4, 50) + '×' }[kind];
  $('#dParams').innerHTML = `<span class="lbl">${h}</span>`;
}
async function inject() {
  if (!need()) return; const kind = $('#dKind').value; const v = (id) => $('#' + id) ? +$('#' + id).value : undefined;
  const params = { train_delay: { min: v('pMin') }, track_close: { track: v('pTrack'), minutes: v('pMin') }, switch_fail: { track: v('pTrack'), minutes: v('pMin') }, loco_down: { minutes: v('pMin') },
    crew_absent: { n: v('pN'), minutes: v('pMin') }, power_fail: { minutes: v('pMin') }, burst: { n: v('pN') } }[kind];
  try { const r = await api(`/api/v1/stations/${D.station}/disruptions`, { method: 'POST', body: { kind, params } }); toast(`${t('dk_' + kind)} · ${r.accepted_ms} мс`, 'warn'); } catch (e) { toast(String(e.detail ? JSON.stringify(e.detail) : e.message), 'crit'); }
}
async function stress(n) {
  if (!need()) return; const t0 = performance.now();
  try { const r = await api(`/api/v1/stations/${D.station}/stress`, { method: 'POST', body: { multiplier: n } }); $('#dStressInfo').textContent = t('d_stress_done', { n: r.injected, ms: r.accepted_ms }); toast(t('d_stress_done', { n: r.injected, ms: r.accepted_ms }), 'warn', 6000); } catch (e) { toast(String(e.message), 'crit'); }
}
async function applyAlt(id) {
  if (!need()) return;
  try { await api(`/api/v1/stations/${D.station}/plan/apply`, { method: 'POST', body: { alt_id: id } }); } catch (e) { toast(String(e.detail?.detail || e.message), 'crit'); }
}
function renderAlts() {
  const a = D.alts, box = $('#dAlts');
  if (!a) { box.innerHTML = `<div class="dev-hint">${t('d_alts_empty')}</div>`; $('#dAltsTime').textContent = ''; return; }
  $('#dAltsTime').textContent = a.stage === 'final' ? t('d_alts_calc', { q: a.t_quick_ms, t: a.t_total_ms }) : t('d_alts_calc_q', { q: a.t_quick_ms });
  const kinds = (a.disruptions || []).map(d => d.kind === 'stress' ? `${t('d_stress')} ×${d.n}` : t('dk_' + d.kind)).join(', ');
  const card = (name, desc, ms, late, conf, chg, idx, delta, cls, btn, badge = '') => `<div class="altc ${cls}"><div class="ah"><span class="nm">${name}${badge}</span><span class="aidx num">${idx}${delta != null ? ` <small class="dlt ${delta >= 0 ? 'up' : 'dn'}">${delta >= 0 ? '+' : ''}${delta}</small>` : ''}</span></div>
    <div class="ds">${desc}</div><div class="am num"><span title="${t('d_h_time')}">⏱ ${ms}</span><span title="${t('d_h_late')}">Σ ${late}</span><span title="${t('d_h_conf')}">⚠ ${conf}</span><span title="${t('d_h_chg')}">⇄ ${chg}</span>${btn}</div></div>`;
  const b = a.baseline;
  let h = `<div class="lbl" style="margin-bottom:6px">${t('d_alts_disr')}: <b style="color:var(--tx)">${kinds}</b> <span style="opacity:.7">· ⏱ ${t('d_h_time').toLowerCase()} · Σ ${t('d_h_late').toLowerCase()} · ⚠ ${t('d_h_conf').toLowerCase()} · ⇄ ${t('d_h_chg').toLowerCase()}</span></div>`;
  h += card(t('alt_baseline'), t('altd_baseline'), '—', b.summary.late_sum, b.conflicts, '—', b.index.score.toFixed(0), null, 'base', '');
  for (const x of a.alts) {
    const applied = a.applied === x.id;
    h += card(t('alt_' + x.label), t('altd_' + x.label), x.ms >= 1 ? Math.round(x.ms) + ' мс' : '<1 мс', x.summary.late_sum, x.conflicts, x.changed_tracks, x.index.score.toFixed(0), x.delta_index, (x.recommended ? 'rec ' : '') + (applied ? 'applied' : ''),
      applied ? `<span class="badge ok" style="margin-left:auto">${t('d_applied')}</span>` : `<button class="btn ${x.recommended ? 'primary' : ''} xs" style="margin-left:auto" data-apply="${x.id}">${t('d_apply')}</button>`, x.recommended ? ` <span class="badge ok">${t('dis_rec')}</span>` : '');
  }
  box.innerHTML = h;
  $$('#dAlts [data-apply]').forEach(bt => bt.onclick = () => applyAlt(bt.dataset.apply));
  renderAlerts();
}

// ====================================================================================================================
function renderThr() { $('#dThrNote').textContent = t('d_thr_note', { n: D.thr.norm, a: D.thr.attention }); }
function openFormula() {
  const F = D.formula; if (!F) return;
  $('#fmBody').innerHTML = `<p style="margin-top:0">${t('d_formula_txt')}</p><table class="alt-t"><tr><th>${t('d_set_w')}</th><th>${t('d_weight')}</th><th></th></tr>` +
    Object.entries(F.weights).map(([k, w]) => `<tr><td><b>${t('f_' + k)}</b><div class="ds">${t('fh_' + k)}</div></td><td class="num">${(w * 100).toFixed(0)}%</td><td></td></tr>`).join('') + `</table><p class="lbl">${t('d_thr_note', { n: F.thresholds.norm, a: F.thresholds.attention })}</p>`;
  $('#fmModal').classList.add('on');
}

// ====================================================================================================================
// перемотка
function syncSlider() {
  const s = $('#dSlider'); s.max = Math.max(0, D.ring.length - 1);
  if (D.replay == null) s.value = s.max; else s.value = D.replay;
}
function renderMarks() {
  if (D.ring.length < 2) return; const w0 = D.ring[0].wall, w1 = D.ring[D.ring.length - 1].wall; const m = $('#dMarks');
  m.innerHTML = D.alerts.filter(a => a.wall >= w0 && (a.code === 'DISRUPTION')).map(a => `<i class="${a.level}" style="left:${((a.wall - w0) / (w1 - w0) * 100).toFixed(2)}%"></i>`).join('');
}
function goLive() { D.replay = null; clearInterval(D.playing); D.playing = null; $('#dPlay').textContent = t('d_play'); syncSlider(); schedule(); }
function onSlider(e) {
  const v = +e.target.value; if (v >= D.ring.length - 1) return goLive();
  D.replay = v; clearInterval(D.playing); D.playing = null; schedule(); diagram.draw();
}
function togglePlay() {
  if (D.playing) { clearInterval(D.playing); D.playing = null; $('#dPlay').textContent = t('d_play'); return; }
  if (D.replay == null) D.replay = Math.max(0, D.ring.length - 400);
  $('#dPlay').textContent = '❚❚'; D.playing = setInterval(() => { D.replay++; if (D.replay >= D.ring.length - 1) return goLive(); $('#dSlider').value = D.replay; schedule(); }, 120);
}

// ====================================================================================================================
// вход и настройки
function renderAuth() {
  const r = D.auth.role; $('#dRole').textContent = r === 'viewer' ? t('d_login') : `${D.auth.user} · ${t('d_role_' + r)}`;
  $('#dAuth').title = D.auth.open ? t('d_open_demo') : '';
  $('#aLogout').style.display = r === 'viewer' ? 'none' : ''; $('#aGo').style.display = r === 'viewer' ? '' : 'none';
}
async function openAuth() {
  try { const i = await api('/api/v1/auth/info'); D.auth.demo = i.dev_mode ? i.demo_users : null; } catch (e) {}
  const demo = $('#aDemo'); if (D.auth.demo) { demo.style.display = ''; demo.innerHTML = `${t('d_demo_hint')}<br>` + D.auth.demo.map(u => `<button class="btn xs" data-u="${u.user}" data-p="${u.password}" style="margin:3px 4px 0 0">${u.user} / ${u.password}</button>`).join(''); $$('#aDemo [data-u]').forEach(b => b.onclick = () => { $('#aUser').value = b.dataset.u; $('#aPass').value = b.dataset.p; }); } else demo.style.display = 'none';
  $('#aErr').style.display = 'none'; $('#authModal').classList.add('on'); setTimeout(() => $('#aUser').focus(), 50);
}
async function doLogin() {
  try {
    const r = await api('/api/v1/auth/login', { method: 'POST', body: { username: $('#aUser').value, password: $('#aPass').value } });
    D.auth = { ...D.auth, role: r.role, token: r.token, user: r.user }; try { sessionStorage.setItem('rt_tok', r.token); } catch (e) {}
    $('#authModal').classList.remove('on'); renderAuth(); toast(`${r.user} · ${t('d_role_' + r.role)}`, 'ok');
  } catch (e) { $('#aErr').style.display = ''; }
}
function logout() { D.auth = { ...D.auth, role: 'viewer', token: null, user: null }; try { sessionStorage.removeItem('rt_tok'); } catch (e) {} $('#authModal').classList.remove('on'); renderAuth(); }
async function restoreAuth() {
  let tok = null; try { tok = sessionStorage.getItem('rt_tok'); } catch (e) {}
  if (!tok) {                                           // открытый демо-режим: сервер сам выдаёт роль диспетчера
    try { const i = await api('/api/v1/auth/info'); if (i.open_demo && i.role === 'dispatcher') { D.auth = { ...D.auth, role: 'dispatcher', user: 'demo', open: true }; renderAuth(); } } catch (e) {}
    return;
  }
  try { const i = await api('/api/v1/auth/info', { headers: { Authorization: 'Bearer ' + tok } }); if (i.role !== 'viewer') { D.auth = { ...D.auth, role: i.role, token: tok, user: i.role }; renderAuth(); } } catch (e) {}
}

async function openSettings() {
  const body = $('#setBody'); $('#setModal').classList.add('on');
  let c; try { c = (await api('/api/v1/config')).config; } catch (e) { body.textContent = String(e.message); return; }
  D.cfg = c; const isAdmin = D.auth.role === 'admin'; const dis = isAdmin ? '' : 'disabled';
  const row = (label, id, val, step = 'any') => `<label>${label}</label><input class="inp" id="${id}" type="number" step="${step}" value="${val}" ${dis}>`;
  const w = c.index.weights; const names = Object.keys(w);
  body.innerHTML = `${isAdmin ? '' : `<div class="step no" style="margin-bottom:10px">${t('d_need_admin')}</div>`}
    <div class="sgrid"><h4>${t('d_set_index')} · ${t('d_set_w')}</h4>${names.map(k => row(t('f_' + k), 'sw_' + k, w[k], 0.01)).join('')}
    <h4>${t('d_set_thr')}</h4>${row(t('d_set_norm'), 'st_norm', c.index.thresholds.norm, 1)}${row(t('d_set_att'), 'st_att', c.index.thresholds.attention, 1)}
    <h4>${t('d_set_opt')}</h4>${row(t('d_set_tl'), 'sp_tl', c.planner.time_limit_s, 0.1)}${row(t('d_set_wl'), 'sp_wl', c.planner.w_late, 0.1)}${row(t('d_set_ww'), 'sp_ww', c.planner.w_wait, 0.1)}${row(t('d_set_wc'), 'sp_wc', c.planner.w_change, 0.5)}${row(t('d_set_hw'), 'sp_hw', c.planner.headway_min, 1)}
    <label>${t('d_set_eng')}</label><select class="inp" id="sp_eng" ${dis}><option ${c.planner.engine === 'cpsat' ? 'selected' : ''}>cpsat</option><option ${c.planner.engine === 'heuristic' ? 'selected' : ''}>heuristic</option></select>
    <h4>${t('d_set_sim')}</h4>${row(t('d_set_scale'), 'ss_scale', c.sim.time_scale, 0.1)}${row(t('d_set_noise') + ' · dup', 'ss_dup', c.sim.noise.dup_rate, 0.01)}${row('invalid', 'ss_inv', c.sim.noise.invalid_rate, 0.01)}${row('reorder', 'ss_reo', c.sim.noise.reorder_rate, 0.01)}</div>
    <div id="setMsg" class="lbl" style="margin-top:8px"></div>
    <div style="display:flex;gap:8px;margin-top:12px"><button class="btn primary" id="setSave" ${dis}>${t('d_save')}</button><button class="btn" id="setReset" ${dis}>${t('d_reset_cfg')}</button></div>`;
  if (!isAdmin) return;
  $('#setSave').onclick = async () => {
    const g = (id) => +$('#' + id).value; const weights = {}; for (const k of names) weights[k] = g('sw_' + k);
    const patch = { index: { weights, thresholds: { norm: g('st_norm'), attention: g('st_att') } }, planner: { time_limit_s: g('sp_tl'), w_late: g('sp_wl'), w_wait: g('sp_ww'), w_change: g('sp_wc'), headway_min: g('sp_hw'), engine: $('#sp_eng').value },
      sim: { time_scale: g('ss_scale'), noise: { dup_rate: g('ss_dup'), invalid_rate: g('ss_inv'), reorder_rate: g('ss_reo') } } };
    try { await api('/api/v1/config', { method: 'PUT', body: { patch } }); $('#setMsg').textContent = t('d_saved'); $('#setMsg').style.color = '#8fd0b1'; toast(t('d_saved'), 'ok'); stream.connect(D.station); }
    catch (e) { $('#setMsg').style.color = '#ec8b87'; $('#setMsg').textContent = e.status === 422 ? JSON.stringify(e.detail.detail).slice(0, 300) : String(e.message); }
  };
  $('#setReset').onclick = async () => { try { await api('/api/v1/config/override', { method: 'DELETE' }); toast(t('d_saved'), 'ok'); $('#setModal').classList.remove('on'); stream.connect(D.station); } catch (e) { toast(String(e.message), 'crit'); } };
}

// ====================================================================================================================
function connect(station) {
  D.station = station; D.live = null; D.ring = []; D.replay = null; goLive(); renderAlts(); renderAlerts(); $('#dTrain').style.display = 'none'; D.selected = null;
  stream.connect(station);
}

export function initDash({ stations }) {
  D.stations = stations;
  scheme = new Scheme($('#dScheme'), { onSelect: selectTrain });
  diagram = new Diagram($('#dDiagram'), { onSelect: selectTrain });
  indexw = new IndexWidget($('#dIndex'), { onFormula: openFormula });
  stream = new StationStream({ onMessage: onMessage, onStatus: onStatus });
  const sel = $('#dStation');
  const fill = () => { sel.innerHTML = D.stations.map(s => `<option value="${s.id}">${stName(s)}</option>`).join(''); sel.value = D.station; };
  fill(); sel.onchange = () => connect(sel.value);
  $('#dSpeed').onclick = async (e) => { const b = e.target.closest('button'); if (!b || !need()) return; $$('#dSpeed button').forEach(x => x.classList.toggle('on', x === b)); try { await api(`/api/v1/stations/${D.station}/sim`, { method: 'POST', body: { scale: +b.dataset.s } }); } catch (er) {} };
  $('#dPause').onclick = async () => { if (!need()) return; try { await api(`/api/v1/stations/${D.station}/sim`, { method: 'POST', body: { paused: !D.paused } }); } catch (er) {} };
  $('#dAuto').onchange = async (e) => { if (!need()) { e.target.checked = !e.target.checked; return; } try { await api(`/api/v1/stations/${D.station}/sim`, { method: 'POST', body: { auto_apply: e.target.checked } }); } catch (er) {} };
  $('#dInject').onclick = inject; $('#dStress5').onclick = () => stress(5); $('#dStress10').onclick = () => stress(10);
  $('#dZoom').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; $$('#dZoom button').forEach(x => x.classList.toggle('on', x === b)); diagram.setWindow(30, +b.dataset.a); };
  $('#dLive').onclick = goLive; $('#dPlay').onclick = togglePlay; $('#dSlider').oninput = onSlider;
  $('#dPdf').onclick = () => window.open(`/api/v1/stations/${D.station}/report.pdf?lang=${getLang()}`, '_blank');
  $('#dCsv').onclick = () => window.open(`/api/v1/stations/${D.station}/report.csv?lang=${getLang()}`, '_blank');
  $('#dSettings').onclick = openSettings; $('#dAuth').onclick = openAuth; $('#aGo').onclick = doLogin; $('#aLogout').onclick = logout;
  $('#aPass').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
  $$('[data-close]').forEach(b => b.onclick = () => $('#' + b.dataset.close).classList.remove('on'));
  renderAuth(); restoreAuth(); renderAlts(); renderAlerts();
  connect(D.station);
  return {
    activate(on) { D.active = on; if (on) { diagram._size(); diagram.draw(); schedule(); } },
    relabel() { fill(); indexw.relabel(); buildKinds(); renderThr(); renderAuth(); renderAlts(); renderAlerts(); onStatus(D.status.state ? D.status : { state: 'connecting', transport: 'ws', attempt: 0 }); schedule(); diagram.rows = null; diagram.draw(); },
    station: () => D.station,
    stream: () => stream,
    state: () => D,
  };
}
