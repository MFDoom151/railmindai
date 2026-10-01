import { t, getLang, setLang, applyI18n, stName } from './i18n.js';
import { KzMap, heatColor } from './map.js';
import { StationScene } from './scene3d.js';
import { runExperiment, crewSweep } from './sim.js';
import { initDash } from './dash/dash.js';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const HU = { ru: 'ч', kz: 'сағ', en: 'h' };
const COL = { green: '#3f9d78', yellow: '#d1a03c', red: '#cf5a54' };
const OPCOL = { ROSPUSK: '#5b8fc9', PODFORM: '#4aa37a', RASSTANOVKA: '#cf9a3a' };
const CAUSECOL = { TRACK: '#5b8fc9', LOCO: '#cf9a3a', CREW: '#8f8fb5', ROUTE: '#c9534f' };
const AX = { axisLine: { lineStyle: { color: '#c3d1e2' } }, axisLabel: { color: '#5f7591', fontSize: 10 }, splitLine: { lineStyle: { color: '#e1e9f3' } } };
const TIP = { backgroundColor: '#ffffff', borderColor: '#b9c9dd', textStyle: { color: '#10243d', fontSize: 11.5 } };

const S = { view: 'A', net: null, sel: 'almaty1', st: null, twinLoading: null, scene: null, plan: null, lastSim: null, charts: {}, local: [], backendLog: [],
  alertTrack: null, layer: 'ops', pane: 'sim', tel: null, dis: null, disAlt: null, simBusy: false, mapMode: 'scheme', fc: {} };

async function api(path, body) {
  const r = await fetch(path, body !== undefined ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined);
  if (!r.ok) throw new Error(path + ' ' + r.status);
  return r.json();
}
const LOC = () => ({ ru: 'ru-RU', kz: 'kk-KZ', en: 'en-GB' })[getLang()] || 'ru-RU';       // дата и время — на языке интерфейса
const fmtMin = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(Math.round(m % 60)).padStart(2, '0')}`;

// ===================== сеть / карта =====================
const map = new KzMap($('#mapCanvas'), {
  onSelect: (id) => { S.sel = id; renderNetList(); loadForecast('A'); updateProbBtn(); },
  onOpen: (id) => openStation(id),
});
map.loadBorder('kz_border.json');
$('#mapReset').onclick = () => map.resetView();
$$('#mapMode button').forEach(b => b.onclick = () => { S.mapMode = b.dataset.mode; map.setMode(S.mapMode); $$('#mapMode button').forEach(x => x.classList.toggle('on', x === b)); renderLegend(); });

function renderLegend() {
  const box = $('#legendBox');
  if (S.mapMode === 'heat') box.innerHTML = `<div class="lbl" style="font-weight:600;margin-bottom:2px">${t('heat_legend')}</div><div class="lg-bar"></div><div style="display:flex;justify-content:space-between;font-size:10.5px;color:var(--mu)"><span>40</span><span>70</span><span>90+</span></div>`;
  else box.innerHTML = `<div class="lbl" style="font-weight:600;margin-bottom:3px">${t('legend')}</div>
    <div class="lg-row"><i style="background:${COL.green};border-radius:50%"></i>${t('lg_green')}</div><div class="lg-row"><i style="background:${COL.yellow};border-radius:50%"></i>${t('lg_yellow')}</div><div class="lg-row"><i style="background:${COL.red};border-radius:50%"></i>${t('lg_red')}</div>
    <div style="border-top:1px solid var(--line);margin:5px 0"></div>
    <div class="lg-row"><i style="background:#8896a9;border-radius:50%;width:12px;height:12px"></i>${t('lg_node')}</div><div class="lg-row"><i style="background:#8896a9;transform:rotate(45deg) scale(.85)"></i>${t('lg_gateway')}</div><div class="lg-row"><i style="background:#8896a9;border-radius:50%;width:7px;height:7px"></i>${t('lg_station')}</div>`;
}

async function refreshNetwork() {
  try { S.net = await api('/api/network'); } catch (e) { return; }
  map.setData(S.net.stations, S.net.sections);
  renderNetList(); renderHot(); updateProbBtn();
  if (S.st) { const s = S.net.stations.find(x => x.id === S.st.id); if (s) $('#stLoad').innerHTML = `<span style="color:${COL[s.status]}">${Math.round(s.load)}%</span>`; }
}
function renderNetList() {
  if (!S.net) return;
  const rank = { node: 0, gateway: 1, station: 2 };
  const list = [...S.net.stations].sort((a, b) => rank[a.tier] - rank[b.tier] || b.load - a.load);
  let last = null, html = '';
  for (const s of list) {
    if (s.tier !== last) { html += `<div class="lbl" style="padding:8px 10px 2px;text-transform:uppercase;letter-spacing:.05em;font-size:10px">${t('tier_' + s.tier).split(' (')[0]}</div>`; last = s.tier; }
    html += `<div class="row ${S.sel === s.id ? 'on' : ''}" data-id="${s.id}">
      <div style="min-width:0"><div style="font-weight:600"><span class="dotc" style="background:${COL[s.status]}"></span>${stName(s)}${s.optimized ? ' <span class="badge ok" style="margin-left:4px">AI</span>' : ''}</div>
      <div class="lbl" style="margin-left:16px">${t('dwell_avg')}: <span class="num">${s.dwell_min}</span> ${t('u_min')}</div></div>
      <div style="width:96px"><div class="num" style="text-align:right;font-weight:650;color:${COL[s.status]}">${Math.round(s.load)}%</div><div class="bar" style="margin-top:3px"><i style="width:${s.load}%;background:${COL[s.status]}"></i></div></div></div>`;
  }
  $('#netList').innerHTML = html;
  $$('#netList .row').forEach(r => r.onclick = () => { S.sel = r.dataset.id; renderNetList(); loadForecast('A'); updateProbBtn(); map.zoomToStation(r.dataset.id); });
}
function renderHot() {
  if (!S.net) return;
  const by = Object.fromEntries(S.net.stations.map(s => [s.id, s]));
  const top = [...S.net.sections].sort((a, b) => b.load - a.load).slice(0, 5);
  $('#hotList').innerHTML = top.map(s => `<div class="row" data-sec="${s.id}"><div style="min-width:0"><div style="font-weight:600;font-size:12.5px">${stName(by[s.a])} – ${stName(by[s.b])}</div><div class="lbl">${s.trunk} · ${t('sec_queue')}: <span class="num">${s.queue}</span></div></div>
    <div style="width:96px"><div class="num" style="text-align:right;font-weight:650;color:${heatColor(s.load)}">${Math.round(s.load)}%</div><div class="bar" style="margin-top:3px"><i style="width:${s.load}%;background:${heatColor(s.load)}"></i></div></div></div>`).join('');
  $$('#hotList .row').forEach(r => r.onclick = () => map.focusSection(r.dataset.sec));
}

// ===================== виды и вкладки =====================
function showView(v) {
  S.view = v; if (v !== 'D') S.lastObs = v;
  $('#viewD').style.display = v === 'D' ? 'flex' : 'none'; $('#viewA').style.display = v === 'A' ? 'flex' : 'none'; $('#viewB').style.display = v === 'B' ? 'flex' : 'none';
  $('#tabObs').classList.toggle('on', v !== 'D'); $('#tabCtl').classList.toggle('on', v === 'D');
  $('#subnav').style.display = v === 'D' ? 'none' : 'flex'; $('#subA').classList.toggle('on', v === 'A'); $('#subB').classList.toggle('on', v === 'B');
  $('#demoBar').style.display = v === 'B' ? '' : 'none'; $('#demoBar').title = t('sandbox_h');
  map.paused = v !== 'A'; if (S.scene) S.scene.paused = v !== 'B';
  if (S.dash) S.dash.activate(v === 'D');
  if (v === 'A') map.resize(); else if (v === 'B') { S.scene && S.scene.resize(); resizeCharts(); }
  showHint(v);
}
// Подсказка «что на этом экране»: одна строка, закрывается и больше не мешает в рамках сессии
function showHint(v) {
  const h = $('#hint'); let seen = false; try { seen = sessionStorage.getItem('rt_hint_' + v) === '1'; } catch (e) {}
  $('#hintTxt').textContent = t('hint_' + v); h.style.display = seen ? 'none' : 'flex'; h.dataset.v = v;
}
$('#hintX').onclick = () => { const h = $('#hint'); try { sessionStorage.setItem('rt_hint_' + h.dataset.v, '1'); } catch (e) {} h.style.display = 'none'; };
$('#tabObs').onclick = () => { if (S.view === 'D') showView(S.lastObs || 'A'); };
$('#tabCtl').onclick = () => showView('D');
$('#subA').onclick = () => { showView('A'); map.resetView(); };
$('#subB').onclick = () => openStation(S.st ? S.st.id : S.sel);
$('#toCtl').onclick = () => { const id = S.view === 'B' && S.st ? S.st.id : S.sel; if (id && S.dash) S.dash.select(id); showView('D'); };
function setPane(p) {
  S.pane = p; $$('#sideTabs button').forEach(b => b.classList.toggle('on', b.dataset.pane === p)); $$('.pane').forEach(x => x.classList.toggle('on', x.id === 'pane-' + p));
  requestAnimationFrame(resizeCharts);
  if (p === 'ai') loadForecast('B');
}
$$('#sideTabs button').forEach(b => b.onclick = () => setPane(b.dataset.pane));
function resizeCharts() { Object.values(S.charts).forEach(c => { try { c.resize(); } catch (e) {} }); }

// ===================== цифровой двойник =====================
function ensureScene() {
  if (S.scene) return S.scene;
  S.scene = new StationScene($('#scene'), {
    departureCheck: (track) => api('/api/safety/departure-check', { station_id: S.st.id, track }),
    onStats: (s) => { $('#hTrains').textContent = s.trains; $('#hLocos').textContent = s.locos; $('#hQueue').textContent = s.queue; $('#hDone').textContent = s.done; $('#hKm').textContent = s.km.toFixed(1); },
    onBlocked: (track, r) => onShoeBlocked(track, r), onUnblocked: (track) => onShoeUnblocked(track),
    onPick: (item, e) => onPick(item),
    onSensors: () => renderLayerInfo(),
    onRecovered: (kind, id) => { pushLocal('ok', kind === 'switch' ? t('ev_DIS_REPAIR', { sw: id }) : kind === 'track' ? t('ev_DIS_TRACK', { n: id }) : kind === 'storage' ? t('ev_DIS_STORAGE') : t('ev_DIS_CREW')); },
  });
  return S.scene;
}

async function openStation(id) {
  if (S.twinLoading) { await S.twinLoading; if (S.st && S.st.id === id) { showView('B'); return; } }
  S.twinLoading = (async () => {
    showView('B'); $('#loadVeil').style.display = 'grid';
    try {
      await api('/api/station/open', { station_id: id });
      const st = await api('/api/stations/' + id); st.safety = await api('/api/safety/state/' + id);
      S.st = st; S.sel = id; S.plan = null; S.lastSim = null; S.dis = null; S.tel = null; hideAlert(); closePick(); $('#btnSecure').style.display = 'none'; S.unsecTrain = null;
      const sc = ensureScene(); await new Promise(r => requestAnimationFrame(r)); sc.resize();
      const pf = st.meta.profile, d = pf.defaults; $('#pLambda').value = d.lambda; $('#pLocos').value = d.locos; $('#pCrews').value = d.crews; $('#pProc').value = d.proc;
      sc.load(st.layout, st.meta, st.safety); sc.paused = false; sc.setLayer(S.layer);
      applyParams(); renderProfile(); renderDisButtons(); updateProbBtn(); fillStationSelect(); renderShoes(); renderAI(); renderDis(); renderResources(null);
      $('#simRes').style.display = 'none'; $('#ilSteps').innerHTML = ''; loadForecast('B'); refreshLog();
    } catch (e) { console.error(e); }
    $('#loadVeil').style.display = 'none';
  })();
  await S.twinLoading; S.twinLoading = null;
}
function renderProfile() {
  if (!S.st) return; const pr = S.st.meta.profile.problem;
  const chip = $('#profChip'); chip.title = t('pd_' + pr);                 // подробности — во всплывающей подсказке
  chip.innerHTML = `<b>${t('prof_title')}:</b> <span style="color:var(--warn)">${t('prob_' + pr)}</span> <span style="color:var(--mu)">· ${t('pd_' + pr)}</span>`;
}
function fillStationSelect() {
  const sel = $('#stSel'); if (!S.net) return;
  const order = ['almaty1', 'shu', 'shymkent', 'astana', 'karaganda', 'altynkol', 'dostyk', 'aktau', 'petropavlovsk', 'pavlodar', 'otar', 'kopa', 'shamalgan'];
  sel.innerHTML = order.map(id => { const s = S.net.stations.find(x => x.id === id); return s ? `<option value="${id}">${stName(s)}</option>` : ''; }).join('');
  sel.value = S.st.id; sel.onchange = () => openStation(sel.value);
}
$$('#camSeg button').forEach(b => b.onclick = () => S.scene && S.scene.setCamera(b.dataset.cam));
$$('#layerSeg button').forEach(b => b.onclick = () => setLayer(b.dataset.layer));
function setLayer(l) {
  S.layer = l; $$('#layerSeg button').forEach(b => b.classList.toggle('on', b.dataset.layer === l)); closePick();
  if (S.scene && S.scene.L) S.scene.setLayer(l);
  renderLayerInfo();
}
function renderLayerInfo() {
  const box = $('#layerInfo'); const sc = S.scene;
  if (S.layer === 'ops' || !sc || !sc.L) { box.style.display = 'none'; return; }
  box.style.display = '';
  if (S.layer === 'iot') {
    const sn = sc.sensors, f = sn.filter(x => x.state === 'fault').length, a = sn.filter(x => x.state === 'alarm').length;
    box.innerHTML = `<b>${t('iot_title')}</b> · <span class="num">${t('iot_sum', { n: sn.length, f, a })}</span><br><span style="color:var(--mu)">${t('iot_hint')}</span>`;
  } else box.innerHTML = `<b>${t('layer_cctv')}</b> · <span class="num">${sc.cams.length}</span><br><span style="color:var(--mu)">${t('iot_hint')}</span>`;
}

// ---- выбор датчика / камеры ----
function badgeFor(st) { return st === 'fault' || st === 'alarm' ? 'crit' : st === 'occupied' ? 'warn' : st === 'free' ? 'mu' : 'ok'; }
function onPick(item) {
  if (item.type === 'camera') { openCctv(item.cam); return; }
  const info = S.scene.sensorInfo(item); const box = $('#pickCard'); box.style.display = '';
  const rows = [[t('sn_since'), new Date(info.since).toLocaleTimeString(LOC())]];
  if (info.type === 'switch') rows.unshift([t('rs_ROUTE_s') + ' / №', `${info.ref} (${S.scene.sw[info.ref].pos})`]); else rows.unshift([t('station') + ' · №', info.track]);
  box.innerHTML = `<div style="display:flex;justify-content:space-between"><h4>${info.id}</h4><button class="btn sm" id="pickX" style="padding:0 7px">×</button></div>
    <div class="lbl" style="margin-bottom:6px">${t('sn_' + info.type)}</div><span class="badge ${badgeFor(info.state)}">${t('sn_' + info.state)}</span>
    <div style="margin-top:8px">${rows.map(r => `<div class="r"><span>${r[0]}</span><b class="num">${r[1]}</b></div>`).join('')}</div>`;
  $('#pickX').onclick = closePick;
}
function closePick() { $('#pickCard').style.display = 'none'; }

// ---- CCTV ----
let cctvTimer = null, cctvFrozen = false;
function openCctv(cam) {
  const modal = $('#cctvModal'); modal.classList.add('on'); cctvFrozen = false; $('#cctvFreeze').textContent = t('cctv_snap');
  $('#cctvTitle').textContent = `${cam.id} · ${t('z_' + cam.zone)} · ${stName(S.net.stations.find(x => x.id === S.st.id))}`; $('#cctvId').textContent = `${cam.id}  ${t('z_' + cam.zone).toUpperCase()}`;
  const canvas = $('#cctvCanvas');
  const tick = () => { if (!cctvFrozen) { try { S.scene.renderCamera(cam.id, canvas); } catch (e) { console.warn(e); } } $('#cctvTime').textContent = new Date().toLocaleString(LOC()); };
  tick(); clearInterval(cctvTimer); cctvTimer = setInterval(tick, 200);
}
function closeCctv() { $('#cctvModal').classList.remove('on'); clearInterval(cctvTimer); cctvTimer = null; }
$('#cctvClose').onclick = closeCctv; $('#cctvModal').onclick = (e) => { if (e.target.id === 'cctvModal') closeCctv(); };
$('#cctvFreeze').onclick = () => { cctvFrozen = !cctvFrozen; $('#cctvFreeze').textContent = cctvFrozen ? t('cctv_resume') : t('cctv_snap'); };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCctv(); });

// ===================== ресурсы и простой =====================
async function pollTelemetry() {
  if (S.view !== 'B' || !S.scene || !S.scene.L || document.hidden || !S.st) return;
  const tel = S.scene.telemetry(); if (!tel) return;
  try { S.tel = await api(`/api/station/${S.st.id}/telemetry`, tel); renderResources(S.tel); } catch (e) { /* offline */ }
}
setInterval(pollTelemetry, 1500);

function syncResToggle() {
  const p = $('#resPanel'), b = $('#resToggle'); if (!p || !b) return;
  b.textContent = p.classList.contains('compact') ? t('res_more') : t('res_less');
}
$('#resToggle').addEventListener('click', () => { $('#resPanel').classList.toggle('compact'); syncResToggle(); setTimeout(() => window.dispatchEvent(new Event('resize')), 50); });
function renderResources(a) {
  syncResToggle();
  const grid = $('#resGrid'); const names = ['TRACK', 'LOCO', 'CREW', 'ROUTE'];
  if (!a) { grid.innerHTML = names.map(k => `<div class="tile"><div class="nm">${t('rs_' + k)}</div><div class="big num">–</div></div>`).join('') + `<div class="diag"><div class="t">${t('diag_title')}</div></div>`; return; }
  const sc = { ok: 'var(--ok)', tight: 'var(--warn)', deficit: 'var(--crit)' };
  const det = (k, it) => {
    const d = it.detail; const out = [];
    if (k === 'TRACK') { out.push(t('rs_detail_track', { r: d.recv_free, s: d.sort_free })); if (d.blocked.length) out.push(t('rs_detail_blocked', { b: d.blocked.join(',') })); }
    if (k === 'CREW' && d.absent) out.push(t('rs_detail_absent', { n: d.absent }));
    if (k === 'ROUTE' && d.active.length) out.push(t('rs_detail_route', { a: d.active.join(',') }));
    if (it.waiting) out.push(`<b style="color:#b8352e">${t('rs_waiting', { n: it.waiting })}</b>`);
    return out.join(' · ');
  };
  const tiles = names.map(k => { const it = a.resources[k];
    return `<div class="tile ${it.status}"><div class="nm" title="${t('rs_' + k)}">${t('rs_' + k)}</div>
      <div class="big num">${it.free}<small> / ${it.total} ${t('rs_free')}</small></div>
      <div class="bar" style="margin:5px 0 3px"><i style="width:${Math.min(100, it.util_pct)}%;background:${sc[it.status]}"></i></div>
      <div class="st"><span>${t('rs_status_' + it.status)}</span><span style="text-align:right">${det(k, it)}</span></div></div>`; }).join('');
  const bn = a.bottleneck, d = a.dwell; let diag;
  const share = ['TRACK', 'LOCO', 'CREW', 'ROUTE'].map(k => `<i style="width:${d.wait_share[k]}%;background:${CAUSECOL[k]}" title="${t('rs_' + k)}: ${d.wait_min[k]} ${t('u_min')}"></i>`).join('');
  const dwell = `<div class="dw"><div>${t('dw_now')}<b class="num">${d.avg_now_min} ${t('u_min')}</b></div><div>${t('dw_max')}<b class="num">${d.max_now_min}</b></div><div>${t('dw_done')}<b class="num">${d.avg_done_min ?? '–'}</b></div><div>${t('dw_wagon')}<b class="num">${d.wagon_wait_h}</b></div></div>
    <div class="share" title="${t('dw_share')}">${share}</div>`;
  if (bn) {
    const tn = (k) => t('rs_' + k).toLowerCase();
    const cause = t('bn_' + bn.cause, { res: tn(bn.resource), avail: bn.available.map(tn).join(', ') || '–', sw: (bn.params.failed || [])[0], tracks: (bn.params.blocked || []).join(','), req: bn.params.conflict });
    diag = `<div class="diag ${bn.severity === 'crit' ? 'crit' : 'warn'}"><div class="t">${t('bn_title', { res: t('rs_' + bn.resource) })}</div><div class="d">${cause}</div><div class="w num">${t('bn_wait', { n: bn.waiting, avg: bn.avg_wait_min, max: bn.max_wait_min })}</div>${dwell}</div>`;
  } else {
    const tight = names.filter(k => a.resources[k].status !== 'ok' && k !== 'ROUTE' || (k === 'ROUTE' && a.resources[k].free === 0));
    diag = tight.length >= 2
      ? `<div class="diag warn"><div class="t">${t('diag_title')}</div><div class="d">${t('diag_tight', { list: tight.map(k => t('rs_' + k).toLowerCase()).join(', ') })}</div>${dwell}</div>`
      : `<div class="diag"><div class="t">${t('diag_title')}</div><div class="d">${t('diag_ok')}</div>${dwell}</div>`;
  }
  grid.innerHTML = tiles + diag;
}

// ===================== параметры и симуляция =====================
function setFill(el) { el.style.setProperty('--p', (el.value - el.min) / (el.max - el.min) * 100 + '%'); }
function params() { return { lambda: +$('#pLambda').value, locos: +$('#pLocos').value, crews: +$('#pCrews').value, proc: +$('#pProc').value }; }
function applyParams() {
  const p = params();
  $('#vLambda').textContent = p.lambda.toFixed(1) + ' ' + t('u_perh'); $('#vLocos').textContent = p.locos; $('#vCrews').textContent = p.crews; $('#vProc').textContent = p.proc + ' ' + t('u_min');
  ['#pLambda', '#pLocos', '#pCrews', '#pProc'].forEach(s => setFill($(s)));
  if (S.scene && S.scene.L) S.scene.setParams(p);
}
['#pLambda', '#pLocos', '#pCrews', '#pProc'].forEach(s => $(s).addEventListener('input', applyParams));

async function runSim() {
  if (S.simBusy) return; S.simBusy = true;
  const btns = [$('#btnRun'), $('#btnSim')]; btns.forEach(b => b.disabled = true);
  setPane('sim'); const box = $('#runStatus'); box.style.display = ''; $('#simRes').style.display = 'none';
  $('#runId').textContent = '#run-' + Math.random().toString(16).slice(2, 8); $('#runMsg').textContent = t('run_queue'); $('#runBar').style.width = '6%';
  await sleep(600); $('#runMsg').textContent = t('run_exec') + '…';
  for (let i = 1; i <= 14; i++) { $('#runBar').style.width = (6 + i * 6.7) + '%'; await sleep(60); }
  const p = params(); const dp = { ...p, recv: S.st.meta.reception, sort: S.st.meta.sorting };
  const res = runExperiment(dp, 24); const sweep = crewSweep(dp); S.lastSim = { dp, res, sweep };
  $('#runMsg').textContent = '✓ ' + t('run_done'); $('#runBar').style.width = '100%';
  renderSim(); pushLocal('ok', t('ev_SIM', { thr: Math.round(res.throughput) }));
  btns.forEach(b => b.disabled = false); S.simBusy = false; await sleep(1000); box.style.display = 'none';
}
function renderSim() {
  if (!S.lastSim) return; const { dp, res, sweep } = S.lastSim; $('#simRes').style.display = '';
  $('#rThr').textContent = res.throughput.toFixed(1); $('#rCap').textContent = res.capacity.toFixed(0); $('#rUtil').textContent = res.util.toFixed(0) + '%';
  $('#rWait').textContent = res.wait.toFixed(0); $('#rDwell').textContent = res.dwell.toFixed(0); $('#rQ').textContent = res.maxQ.toFixed(1);
  const v = $('#rVerdict'); const over = res.offered > res.capacity * 0.97 || res.util > 90; v.className = 'step ' + (over ? '' : 'yes');
  if (!over) v.textContent = '✓ ' + t('r_verdict_ok');
  else if (res.bottleneck === 'crew') { const better = sweep.find(x => x.thr > res.throughput * 1.08 && x.crews > dp.crews); v.textContent = '⚠ ' + t('r_verdict_crew') + (better ? ` ${better.crews} → ${better.thr.toFixed(0)} ${t('u_day')}` : ''); }
  else v.textContent = '⚠ ' + t(res.bottleneck === 'loco' ? 'r_verdict_loco' : 'r_verdict_track');
  $('#apiJson').textContent = JSON.stringify({ POST: '/anylogic-cloud/emu/experiments/run', model: 'RailMindAI_Shunting_DES', station: S.st.id, inputs: { arrival_rate_per_h: dp.lambda, shunting_locos: dp.locos, shunting_crews: dp.crews, process_time_min: dp.proc, reception_tracks: dp.recv, sorting_tracks: dp.sort, horizon_h: 24, replications: 24 }, outputs: { throughput_per_day: +res.throughput.toFixed(1), ci95: +res.ci95.toFixed(1), capacity_per_day: +res.capacity.toFixed(0) } }, null, 1);
  drawSimCharts();
}
function chart(id) { const el = document.getElementById(id); let c = S.charts[id]; if (!c || c.getDom() !== el) { if (c) c.dispose(); c = echarts.init(el, null, { renderer: 'canvas' }); S.charts[id] = c; } return c; }
function drawSimCharts() {
  const { res, sweep, dp } = S.lastSim; const hrs = [...Array(24).keys()].map(h => String(h).padStart(2, '0') + ':00');
  chart('chQueue').setOption({ animationDuration: 400, grid: { left: 34, right: 36, top: 22, bottom: 22 }, tooltip: { trigger: 'axis', ...TIP },
    legend: { top: 0, textStyle: { color: '#5f7591', fontSize: 10 }, itemWidth: 12, itemHeight: 6 },
    xAxis: { type: 'category', data: hrs, ...AX, axisLabel: { ...AX.axisLabel, interval: 3 } }, yAxis: [{ type: 'value', ...AX }, { type: 'value', max: 100, ...AX, splitLine: { show: false } }],
    series: [{ name: t('s_queue'), type: 'line', smooth: true, showSymbol: false, data: res.hourlyQ.map(v => +v.toFixed(2)), lineStyle: { color: '#5b8fc9', width: 2 }, areaStyle: { color: 'rgba(91,143,201,.15)' } },
      { name: t('s_util'), type: 'line', yAxisIndex: 1, smooth: true, showSymbol: false, data: res.hourlyU.map(v => +v.toFixed(0)), lineStyle: { color: '#cf9a3a', width: 2 } }] }, true);
  chart('chCrews').setOption({ animationDuration: 400, grid: { left: 34, right: 12, top: 14, bottom: 22 }, tooltip: { trigger: 'axis', ...TIP },
    xAxis: { type: 'category', data: sweep.map(s => s.crews), ...AX }, yAxis: { type: 'value', ...AX },
    series: [{ type: 'bar', barWidth: '46%', data: sweep.map(s => ({ value: +s.thr.toFixed(1), itemStyle: { color: s.crews === Math.min(dp.crews, dp.locos) ? '#5b8fc9' : '#a9bfdc', borderRadius: [3, 3, 0, 0] } })), label: { show: true, position: 'top', color: '#8190a3', fontSize: 10 },
      markLine: { silent: true, symbol: 'none', lineStyle: { color: '#c9534f', type: 'dashed' }, label: { color: '#b8352e', fontSize: 10, formatter: () => (dp.lambda * 24).toFixed(0) + ' ' + t('u_day') }, data: [{ yAxis: dp.lambda * 24 }] } }] }, true);
}

// ===================== прогноз =====================
async function loadForecast(which) {
  const id = which === 'A' ? S.sel : (S.st && S.st.id); if (!id) return;
  try { const f = await api('/api/forecast/' + id); S.fc[which] = f; drawForecast(which, f); } catch (e) { /* offline */ }
}
function drawForecast(which, f) {
  const el = which === 'A' ? 'fcA' : 'fcB'; if (!document.getElementById(el).offsetWidth) return; const c = chart(el); const h = HU[getLang()];
  if (which === 'A') { const s = S.net && S.net.stations.find(x => x.id === f.station_id); $('#fcAName').textContent = s ? stName(s) : ''; }
  const cats = f.series.map(p => p.t === 0 ? t('fc_now') : '+' + p.t + h); const lo = f.series.map(p => p.lo), band = f.series.map(p => +(p.hi - p.lo).toFixed(2));
  const mark = [2, 6, 12].map(k => ({ coord: [k, f.series[k].opt], value: f.series[k].opt.toFixed(1), label: { show: true, formatter: '+' + k + h + '\n' + f.series[k].opt.toFixed(1), color: '#dde4ed', fontSize: 10, offset: [0, -16] } }));
  c.setOption({ animationDuration: 500, grid: { left: 40, right: 14, top: 34, bottom: 24 }, tooltip: { trigger: 'axis', ...TIP },
    legend: { top: 2, textStyle: { color: '#5f7591', fontSize: 10 }, itemWidth: 14, itemHeight: 6, data: [t('fc_base'), t('fc_opt')] },
    xAxis: { type: 'category', data: cats, boundaryGap: false, ...AX }, yAxis: { type: 'value', scale: true, ...AX },
    series: [{ type: 'line', name: 'lo', data: lo, stack: 'b', symbol: 'none', lineStyle: { opacity: 0 }, tooltip: { show: false }, silent: true },
      { type: 'line', name: 'band', data: band, stack: 'b', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(91,143,201,.14)' }, tooltip: { show: false }, silent: true },
      { type: 'line', name: t('fc_base'), data: f.series.map(p => p.base), smooth: true, showSymbol: false, lineStyle: { color: '#7a8ea8', width: 2, type: 'dashed' } },
      { type: 'line', name: t('fc_opt'), data: f.series.map(p => p.opt), smooth: true, showSymbol: false, lineStyle: { color: f.optimized ? '#4aa37a' : '#5b8fc9', width: 2.5 },
        markPoint: { symbol: 'circle', symbolSize: 8, itemStyle: { color: '#5b8fc9', borderColor: '#fff', borderWidth: 1.5 }, data: mark },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: 'rgba(129,144,163,.35)', type: 'dotted' }, label: { show: false }, data: [{ xAxis: 2 }, { xAxis: 6 }, { xAxis: 12 }] } }] }, true);
}

// ===================== Gantt =====================
function drawGantt(id, schedule, { min = 0, max = 1440, now = null, nLocos = 1 } = {}) {
  const c = chart(id); const cats = [...Array(nLocos).keys()].map(i => 'ЧМЭ3-' + (i + 1));
  const data = schedule.map(s => ({ value: [s.loco - 1, s.start, s.end, s.op, s.from, s.to, s.wagons], itemStyle: { color: OPCOL[s.op] || '#5b8fc9' } }));
  c.setOption({ animation: false, grid: { left: 58, right: 14, top: 8, bottom: 38 }, tooltip: { ...TIP, formatter: (p) => p.value ? `<b>${t('op_' + p.value[3])}</b><br>${t('ai_task_tip', { op: fmtMin(p.value[1]) + '–' + fmtMin(p.value[2]), from: p.value[4], to: p.value[5], wagons: p.value[6] })}` : '' },
    legend: { bottom: 0, textStyle: { color: '#5f7591', fontSize: 10 }, itemWidth: 10, itemHeight: 8, data: Object.keys(OPCOL).map(k => ({ name: t('op_' + k), itemStyle: { color: OPCOL[k] } })) },
    xAxis: { type: 'value', min, max, interval: Math.max(30, Math.round((max - min) / 6 / 30) * 30), ...AX, axisLabel: { ...AX.axisLabel, formatter: (v) => fmtMin(v) } },
    yAxis: { type: 'category', data: cats, inverse: true, ...AX, splitLine: { show: false } },
    series: [{ type: 'custom', name: 'ops', data, encode: { x: [1, 2], y: 0 }, renderItem: (params, api) => { const cat = api.value(0); const a = api.coord([api.value(1), cat]), b = api.coord([api.value(2), cat]); const hh = api.size([0, 1])[1] * 0.58;
        return { type: 'rect', shape: { x: a[0], y: a[1] - hh / 2, width: Math.max(1.5, b[0] - a[0]), height: hh, r: 2 }, style: api.style() }; } },
      ...Object.keys(OPCOL).map(k => ({ type: 'bar', name: t('op_' + k), data: [], itemStyle: { color: OPCOL[k] } })),
      ...(now != null ? [{ type: 'scatter', data: [], markLine: { silent: true, symbol: 'none', lineStyle: { color: '#10243d', type: 'dashed', width: 1 }, label: { formatter: t('ai_now'), color: '#e6ebf2', fontSize: 10, position: 'insideEndTop' }, data: [{ xAxis: now }] } }] : [])] }, true);
}

// ===================== AI-график =====================
async function runAI() {
  if (!S.st) return; const btns = [$('#btnAI'), $('#btnAI2')]; btns.forEach(b => b.disabled = true); setPane('ai');
  try {
    const plan = await api('/api/optimize', { station_id: S.st.id, crews: Math.min(params().locos, params().crews) || 1, apply: true });
    S.plan = plan; S.scene.setOptimized(true); renderAI(); pushLocal('ok', t('ev_AI', { pct: '−' + plan.savings.empty_km_pct }));
    await refreshNetwork(); loadForecast('B');
  } catch (e) { console.error(e); }
  btns.forEach(b => b.disabled = false);
}
function renderAI() {
  const p = S.plan; $('#aiIdle').style.display = p ? 'none' : ''; $('#aiBody').style.display = p ? '' : 'none'; if (!p) return;
  $('#aiEB').textContent = p.baseline.empty_km.toFixed(2) + ' km'; $('#aiEO').textContent = p.optimized.empty_km.toFixed(2) + ' km (−' + p.savings.empty_km_pct + '%)';
  $('#aiTot').textContent = (p.baseline.empty_km + p.baseline.loaded_km).toFixed(2) + ' → ' + (p.optimized.empty_km + p.optimized.loaded_km).toFixed(2) + ' km'; $('#aiGain').textContent = '+' + p.savings.throughput_gain_pct + '%';
  const d = p.savings.dwell_pct; $('#aiDwell').textContent = p.baseline.dwell_wagon_h.toFixed(1) + ' → ' + p.optimized.dwell_wagon_h.toFixed(1) + ' ' + (getLang() === 'en' ? 'wagon·h' : 'ваг·ч') + ' (' + (d > 0 ? '−' + d : '+' + Math.abs(d)) + '%)';
  $('#aiOps').textContent = Object.entries(p.ops).map(([k, v]) => `${t('op_' + k)} ${v}`).join(' · ');
  const lt = new Date(); drawGantt('chGantt', p.schedule, { nLocos: p.locos, now: lt.getHours() * 60 + lt.getMinutes() });
}
async function resetAI() { await api('/api/optimize/reset', { station_id: S.st.id }); S.plan = null; S.scene.setOptimized(false); renderAI(); refreshNetwork(); loadForecast('B'); }

// ===================== внезапные сбои =====================
const KIND_NAME = new Proxy({}, { get: (_, k) => 'prob_' + k });
const DIS_KINDS = ['switch_fail', 'switch_icing', 'false_occupancy', 'train_delay', 'crew_absent', 'loco_fail', 'track_full', 'border_hold', 'arrival_burst', 'power_fail'];
const problemOf = (id) => { const s = S.net && S.net.stations.find(x => x.id === id); return (S.st && S.st.id === id ? S.st.meta.profile.problem : s && s.problem) || 'shoe'; };
function renderDisButtons() {
  const mine = problemOf(S.st ? S.st.id : S.sel);
  $('#disBtns').innerHTML = DIS_KINDS.map(k => `<button class="btn dis-btn ${k === mine ? 'primary' : ''}" data-kind="${k}" style="text-align:left;justify-content:flex-start;font-weight:600">${t('prob_' + k)}</button>`).join('');
  $$('.dis-btn').forEach(b => b.onclick = () => runDisruption(b.dataset.kind));
  const hint = $('#disHint'); if (hint) hint.textContent = DIS_KINDS.includes(mine) ? t('dis_prof_hint', { name: t('prob_' + mine) }) : t('dis_hint');
}
function renderDis() { $('#disRes').style.display = S.dis ? '' : 'none'; $('#disHint').style.display = S.dis ? 'none' : ''; if (S.dis) renderDisResult(); }

async function runDisruption(kind) {
  if (!S.st || !S.scene.L) await ensureTwin(); setPane('dis'); const sc = S.scene, p = params();
  const freeTracks = (kinds, notLast) => sc.L.tracks.filter(x => kinds.includes(x.kind) && (!notLast || x.idx < sc.n) && !sc.blockedTracks().has(x.idx)).sort((u, v) => (sc.resRecv.has(u.idx) || sc.resSort.has(u.idx)) - (sc.resRecv.has(v.idx) || sc.resSort.has(v.idx)));
  let prm = {};
  if (kind === 'switch_fail') { const j = (freeTracks(['reception'], true)[0] || sc.L.tracks.find(x => x.idx === 1)).idx; prm = { switch: 2 * j + 1, repair_min: 45 }; sc.failSwitch(prm.switch, 45); }
  else if (kind === 'switch_icing') {
    const js = freeTracks(['reception', 'sorting'], true).slice(0, 2).map(x => x.idx); if (js.length < 2) js.push(1);
    prm = { switches: js.map(j => 2 * j + 1), repair_min: 60 }; prm.switches.forEach(s => sc.failSwitch(s, 60));
  } else if (kind === 'false_occupancy') { const j = (freeTracks(['reception', 'sorting'])[0] || sc.L.tracks[1]).idx; prm = { track: j, repair_min: 30 }; sc.blockTrack(j, 30); }
  else if (kind === 'train_delay') { prm = { delay_min: 75 }; sc.delayArrivals(75); }
  else if (kind === 'crew_absent') { prm = {}; sc.setCrewAbsent(1, 120); }
  else if (kind === 'loco_fail') { prm = {}; sc.removeLocos(1, 120); }
  else if (kind === 'track_full') { prm = { hold_min: 120 }; sc.occupyStorage(120); }
  else if (kind === 'border_hold') { prm = { hold_min: 90 }; sc.setProcMult(1.8, 90); }
  else if (kind === 'arrival_burst') { prm = { n: 4 }; sc.burstArrivals(4); }
  else if (kind === 'power_fail') { prm = {}; sc.setRouteExtra(2.2, 60); }
  pushLocal('warn', t('ev_DIS_START', { kind: t('prob_' + kind) })); sc.setCamera('persp');
  const t0 = performance.now(); const lt = new Date();
  try {
    const r = await api('/api/disruption/replan', { station_id: S.st.id, kind, params: prm, locos: p.locos, crews: p.crews, now_min: lt.getHours() * 60 + lt.getMinutes() });
    r.rtt = Math.round(performance.now() - t0); r.sel = r.alternatives.find(a => a.recommended)?.id; S.dis = r; S.disApplied = null; renderDis();
  } catch (e) { console.error(e); }
}
function altEffect(kind, a) {
  const sc = S.scene, info = S.dis.info, later = (ms, fn) => setTimeout(() => sc.alive && fn(), ms);
  if (kind === 'switch_fail' && a.code === 'MANUAL_THROW') later(4000, () => sc.repairSwitch(info.switch));
  if (kind === 'switch_icing' && a.code === 'HEAT_ON') later(10000, () => (info.switches || []).forEach(s => sc.repairSwitch(s)));
  if (kind === 'switch_icing' && a.code === 'MANUAL_THROW') later(4000, () => (info.switches || []).forEach(s => sc.repairSwitch(s)));
  if (kind === 'false_occupancy' && a.code === 'VISUAL_PASS') later(4000, () => sc.unblockTrack(info.track));
  if (kind === 'train_delay' && a.code === 'ADD_LOCO') sc.addReserveLoco(120);
  if (kind === 'crew_absent' && a.code === 'STANDBY') sc.setCrewAbsent(1, 40);
  if (kind === 'loco_fail' && a.code === 'BORROW_LOCO') sc.removeLocos(1, 50);
  if (kind === 'track_full' && a.code === 'EVACUATE') later(12000, () => sc.clearStorage());
  if (kind === 'border_hold' && a.code === 'EXTRA_TEAM') sc.setProcMult(1.25, 90);
  if (kind === 'border_hold' && a.code === 'PRECLEARED_FIRST') sc.setProcMult(1.4, 90);
  if (kind === 'border_hold' && a.code === 'ADD_LOCO_PARALLEL') sc.addReserveLoco(120);
  if (kind === 'arrival_burst' && a.code === 'ADD_LOCO') sc.addReserveLoco(120);
  if (kind === 'power_fail' && a.code === 'BACKUP_POWER') later(10000, () => sc.setRouteExtra(0.6, 60));
}
function disInfoText(r) {
  const i = r.info, k = r.kind;
  if (k === 'switch_fail') return t('dis_info_switch', { sw: i.switch, tracks: i.blocked.join(','), n: i.tasks_affected, rep: i.repair_min });
  if (k === 'switch_icing') return t('dis_info_icing', { sw: (i.switches || []).join(', №'), tracks: i.blocked.join(','), n: i.tasks_affected, rep: i.repair_min });
  if (k === 'false_occupancy') return t('dis_info_false', { tracks: i.blocked.join(','), n: i.tasks_affected, rep: i.repair_min });
  if (k === 'train_delay') return t('dis_info_delay', { d: i.delay_min, task: i.train_task });
  if (k === 'crew_absent') return t('dis_info_crew', { a: i.crews_before, b: i.crews_after, s: i.standby_min });
  if (k === 'loco_fail') return t('dis_info_loco', { a: i.locos_before, b: i.locos_after, s: i.borrow_min });
  if (k === 'track_full') return t('dis_info_full', { tracks: i.blocked.join(','), n: i.tasks_affected, rep: i.hold_min });
  if (k === 'border_hold') return t('dis_info_border', { rep: i.hold_min, n: i.tasks_affected });
  if (k === 'arrival_burst') return t('dis_info_burst', { b: i.burst, w: i.window_min, n: i.tasks_affected });
  return t('dis_info_power', { x: i.extra_min, w: i.window_min });
}
function renderDisResult() {
  const r = S.dis, i = r.info; const f = (v, d = 1) => (v > 0 ? '+' : '') + v.toFixed(d);
  const kindInfo = disInfoText(r);
  $('#disInfo').innerHTML = `<b>${t(KIND_NAME[r.kind])}.</b> ${kindInfo}`;
  $('#disMs').textContent = t('dis_compute', { ms: r.compute_ms }) + ` · RTT ${r.rtt} ms`;
  const na = r.no_action;
  $('#disNoAct').innerHTML = `<h4>${t('dis_noaction')}</h4><div class="mrow"><div>${t('m_late')}<b class="num r">${na.late_min}</b></div><div>${t('m_dwell')}<b class="num r">${na.dwell_wagon_h}</b></div><div>${t('m_empty')}<b class="num">${na.empty_km}</b></div></div>`;
  $('#disAlts').innerHTML = r.alternatives.map(a => {
    const m = a.metrics, p = { ...a.params, tracks: a.params.tracks ?? (i.blocked || []).join(','), sw: i.switch, track: i.track, crews: i.crews_after, locos: i.locos_after, min: a.params.min ?? i.repair_min ?? i.standby_min ?? i.borrow_min };
    const col = (v) => v < -0.05 ? 'g' : v > 0.05 ? 'r' : '';
    return `<div class="alt ${a.recommended ? 'rec' : ''} ${S.disApplied === a.id ? 'applied' : ''}" data-id="${a.id}"><h4><span>${t('alt_' + a.code)}</span>${a.recommended ? `<span class="badge ok">${t('dis_rec')}</span>` : ''}</h4>
      <p>${t('alt_' + a.code + '_d', p)}${a.flags.length ? '<br>' + a.flags.map(x => `<span class="badge warn" style="margin-right:4px">${t('fl_' + x)}</span>`).join('') : ''}</p>
      <div class="mrow"><div>${t('m_late')}<b class="num ${col(m.d_late_min)}">${m.late_min} <small>(${f(m.d_late_min)})</small></b></div><div>${t('m_dwell')}<b class="num ${col(m.d_dwell_pct)}">${m.dwell_wagon_h} <small>(${f(m.d_dwell_pct)}%)</small></b></div><div>${t('m_empty')}<b class="num">${m.empty_km}</b></div></div>
      <button class="btn sm ${a.recommended ? 'ok' : ''}" data-apply="${a.id}" ${S.disApplied === a.id ? 'disabled' : ''}>${S.disApplied === a.id ? t('dis_applied') : t('dis_apply')}</button></div>`;
  }).join('');
  $$('#disAlts .alt').forEach(el => el.onclick = (e) => { if (e.target.dataset.apply) return; r.sel = el.dataset.id; drawDisGantt(); });
  $$('#disAlts [data-apply]').forEach(b => b.onclick = () => { const a = r.alternatives.find(x => x.id === b.dataset.apply); S.disApplied = a.id; r.sel = a.id; altEffect(r.kind, a); pushLocal('ok', t('ev_DIS_APPLY', { alt: t('alt_' + a.code) })); renderDisResult(); });
  drawDisGantt();
}
function drawDisGantt() {
  const r = S.dis; const a = r.alternatives.find(x => x.id === r.sel) || r.alternatives[0]; const n = Math.max(...a.schedule.map(s => s.loco));
  const min = Math.floor((Math.min(...a.schedule.map(s => s.start)) - 10) / 30) * 30, max = Math.ceil((Math.max(...a.schedule.map(s => s.end)) + 10) / 30) * 30;
  drawGantt('chDisGantt', a.schedule, { min, max, now: r.now_min, nLocos: n });
}

// ===================== безопасность =====================
async function refreshLog() { try { const r = await api('/api/safety/log?limit=40'); S.backendLog = r.events; renderFeed(); } catch (e) { /* offline */ } }
function pushLocal(level, text) { S.local.unshift({ ts: Date.now() / 1000, level, text, station: S.st ? S.st.id : null }); S.local = S.local.slice(0, 30); renderFeed(); }
// Справочные ссылки на нормативную базу (по материалам актов расследования); задаются данными и правятся без изменения логики
const NORM = { PROHIBITED_SIGNAL_MOVE: 'п. 372 гл. 11 ИДП', HOSTILE_ROUTE: 'п. 372 гл. 11 ИДП', SHOE_ON_RAIL: 'п. 3.12 ТРА станции; п. 23/32 долж. инструкции', UNSECURED: 'п. 390 гл. 11 ИДП; Приказ №445-ЦЗ' };
const EV_NORM = { DEPARTURE_BLOCKED_SHOE: 'SHOE_ON_RAIL', SHOE_DETECTED: 'SHOE_ON_RAIL', DEPARTURE_BLOCKED_UNSECURED: 'UNSECURED', UNSECURED_DETECTED: 'UNSECURED' };
const normTag = (code) => NORM[code] ? `<span class="norm" title="${t('norm_hint')}">${NORM[code]}</span>` : '';
function evText(e) { const p = { ...e.params }; if (p.shoes) p.shoes = '№' + p.shoes.join(', №'); if (e.code === 'INTERLOCK_BLOCK') p.reasons = (p.reasons || []).map(c => t('rc_' + c)).join(' · '); const nc = EV_NORM[e.code] || (e.code === 'INTERLOCK_BLOCK' && (e.params.reasons || []).find(c => NORM[c])); return t('ev_' + e.code, p) + (nc ? ' ' + normTag(nc) : ''); }
function renderFeed() {
  const all = [...S.backendLog.map(e => ({ ts: e.ts, level: e.level, text: evText(e), station: e.station })), ...S.local].sort((a, b) => b.ts - a.ts).slice(0, 40);
  const stn = (id) => { const s = S.net && S.net.stations.find(x => x.id === id); return s ? stName(s) : ''; };
  const html = all.length ? all.map(e => `<div class="ev ${e.level}"><span class="tm num">${new Date(e.ts * 1000).toLocaleTimeString(LOC())}</span><span><b style="color:var(--accent-2)">${stn(e.station)}</b> ${e.text}</span></div>`).join('') : `<div class="lbl" style="padding:6px">${t('saf_none')}</div>`;
  $$('.feed').forEach(f => f.innerHTML = html);
}
async function renderShoes() {
  if (!S.st) return; let snap; try { snap = await api('/api/safety/state/' + S.st.id); } catch (e) { return; }
  const uns = (snap.unsecured || []).map(tk => `<div class="step no" style="display:flex;justify-content:space-between"><span>${t('saf_unsec_row', { track: tk })}</span><b style="color:#b8352e">IoT</b></div>`).join('');
  $('#shoeList').innerHTML = uns + (snap.shoes.length ? snap.shoes.map(s => `<div class="step no" style="display:flex;justify-content:space-between"><span>${t('saf_shoe_row', { shoe: s.id, track: s.track, rfid: s.rfid })}</span><b style="color:#b8352e">${t('saf_onrail')}</b></div>`).join('') : (uns ? '' : `<div class="step yes">✓ ${t('saf_none')}</div>`));
}
function showAlert(title, text, ok = false) { const a = $('#alertBox'); a.className = 'alert' + (ok ? ' ok' : ''); a.innerHTML = `<div class="t">${title}</div><div class="d">${text}</div>`; a.style.display = ''; }
function hideAlert() { $('#alertBox').style.display = 'none'; $('#btnRemove').style.display = 'none'; $('#btnSecure').style.display = 'none'; S.alertTrack = null; }
function onShoeBlocked(track, r) {
  S.alertTrack = track; const shoe = (r.reasons[0] && r.reasons[0].shoes[0]) || 19;
  showAlert(t('alert_title'), t('alert_shoe', { shoe, track }) + ' ' + normTag('SHOE_ON_RAIL')); $('#btnRemove').style.display = ''; $('#btnRemove').dataset.shoe = shoe;
  S.scene && S.scene.flashSignal('Ч' + track, 60000); refreshLog(); renderShoes(); refreshNetwork();
}
function onShoeUnblocked(track) { showAlert('✓', t('alert_ok', { track }), true); $('#btnRemove').style.display = 'none'; refreshLog(); renderShoes(); setTimeout(() => { if (S.alertTrack === null) hideAlert(); }, 4500); S.alertTrack = null; }

async function ensureTwin() { if (S.view !== 'B' || !S.st) await openStation(S.st ? S.st.id : S.sel || 'almaty1'); }
async function demoShoe() {
  const cur = S.st ? S.st.id : 'otar';
  if (S.view !== 'B' || !S.st || S.st.id !== cur) await openStation(cur);
  setPane('saf'); setLayer('ops'); const sc = S.scene; hideAlert();
  const tr = sc.forceReadyTrain(); if (!tr) return; sc.setCamera('persp'); const track = tr.track;
  await api('/api/safety/shoe/inject', { station_id: S.st.id, shoe_id: 19, track }); sc.addShoe(19, track, true); renderShoes(); refreshLog();
  const tt = sc.byIdx[track]; sc.focus(tt.x1 - 20, tt.z, 70); await sleep(900); sc.releaseHold(tr);
}
async function removeShoe() { const id = +($('#btnRemove').dataset.shoe || 19); await api('/api/safety/shoe/remove', { station_id: S.st.id, shoe_id: id, track: S.alertTrack || 1 }); S.scene.removeShoe(id); renderShoes(); refreshLog(); }

async function hostileScenario() {
  if (!S.st) return; const sc = S.scene, box = $('#ilSteps'); box.innerHTML = '';
  const sid = S.st.id; const res = await api('/api/safety/scenario/hostile-route', { station_id: sid }); sc.setCamera('close'); let n = 0;
  for (const s of res.steps) {
    if (!S.st || S.st.id !== sid) return;
    const ok = s.result.allowed;
    const title = s.step === 'SWITCH_REQUEST' ? t('r_SWITCH_REQUEST_no', { switch: s.switch, pos: s.pos }) : ok ? t('r_ROUTE_REQUEST_ok', { route: s.route }) : t('r_ROUTE_REQUEST_no', { route: s.route });
    const reasons = ok ? '' : '<ul>' + s.result.reasons.map(r => `<li>${t('c_' + r.code, { ...r, sections: (r.sections || []).join(', ') })}${normTag(r.code)}</li>`).join('') + '</ul>';
    box.insertAdjacentHTML('beforeend', `<div class="step ${ok ? 'yes' : 'no'}"><b>${ok ? '✓' : '✗'}</b> ${title}${reasons}</div>`);
    if (ok) { sc.applyRoute(s.route); sc.setSignal('Н', 'GREEN'); }
    else {
      n++; const ids = new Set(); if (s.switch) ids.add(s.switch); const rt = sc.L.routes[s.route]; if (rt) Object.keys(rt.switches).forEach(k => ids.add(+k)); s.result.reasons.forEach(r => r.switch && ids.add(r.switch));
      sc.flashSwitches([...ids], 5000); sc.flashSignal('Н', 5000); showAlert(t('saf_routes'), t('alert_hostile', { n }) + ' ' + normTag('HOSTILE_ROUTE') + ' — ' + title);
    }
    refreshLog(); await sleep(1500);
  }
  refreshNetwork(); await sleep(2500); if (!S.st || S.st.id !== sid) return; sc.clearSwitches('L'); sc.setSignal('Н', 'RED'); hideAlert();
}

// ===================== сценарий станции =====================
function updateProbBtn() {
  const id = S.st ? S.st.id : S.sel, pr = problemOf(id);
  $('#btnProb').textContent = t('demo_prob', { name: t('prob_' + pr) });
}
async function runStationScenario() {
  const id = S.view === 'B' && S.st ? S.st.id : S.sel; const pr = problemOf(id);
  if (S.view !== 'B' || !S.st || S.st.id !== id) await openStation(id);
  pushLocal('warn', t('ev_PROB', { name: t('prob_' + pr) }));
  if (pr === 'shoe') return demoShoe();
  if (pr === 'unsecured') return demoUnsecured();
  if (pr === 'hostile') { setPane('saf'); setLayer('ops'); return hostileScenario(); }
  setLayer('ops'); return runDisruption(pr);
}
async function demoUnsecured() {
  if (S.view !== 'B' || !S.st) await ensureTwin();
  setPane('saf'); const sc = S.scene; hideAlert(); S.unsecTrain = null;
  const tr = sc.forceUnsecuredTrain(); if (!tr) return; S.unsecTrain = tr; const track = tr.track;
  await api('/api/safety/unsecured/inject', { station_id: S.st.id, track }); setLayer('iot');
  const tt = sc.byIdx[track]; sc.focus(tt.x1 - 25, tt.z, 70);
  showAlert(t('alert_unsec'), t('alert_unsec_d', { track }) + ' ' + normTag('UNSECURED')); $('#btnSecure').style.display = ''; renderShoes(); refreshLog(); refreshNetwork();
}
async function secureNow() {
  const tr = S.unsecTrain; if (!tr) return;
  await api('/api/safety/unsecured/secure', { station_id: S.st.id, track: tr.track }); S.scene.secureTrain(tr); S.scene.releaseHold(tr);
  showAlert('✓', t('alert_unsec_ok'), true); $('#btnSecure').style.display = 'none'; S.unsecTrain = null; renderShoes(); refreshLog(); setTimeout(() => { if (S.alertTrack === null) hideAlert(); }, 4500);
}

// ===================== демо-кнопки =====================
$('#btnSim').onclick = async () => { await ensureTwin(); runSim(); };
$('#btnRun').onclick = runSim;
$('#btnProb').onclick = runStationScenario; $('#btnShoe2').onclick = demoShoe; $('#btnRemove').onclick = removeShoe; $('#btnUnsec').onclick = demoUnsecured; $('#btnSecure').onclick = secureNow;
$('#btnAI').onclick = async () => { await ensureTwin(); runAI(); };
$('#btnAI2').onclick = runAI; $('#btnAIreset').onclick = resetAI; $('#btnHostile').onclick = hostileScenario;

// ===================== язык / часы =====================
$$('.langs button').forEach(b => { b.onclick = () => setLang(b.dataset.lang); });
function markLang() { try { showHint(S.view); renderUserChip(); $('#demoBar').title = t('sandbox_h'); } catch (e) {} $$('.langs button').forEach(b => b.classList.toggle('on', b.dataset.lang === getLang())); }
document.addEventListener('langchange', () => {
  if (S.dash) S.dash.relabel(); try { renderLgChart(); renderDemoList(); } catch (e) {}
  markLang(); renderLegend(); renderNetList(); updateProbBtn(); renderProfile(); renderDisButtons(); renderHot(); if (S.st) { fillStationSelect(); renderShoes(); } renderAI(); applyParams(); renderFeed(); renderLayerInfo();
  renderResources(S.tel); if (S.dis) renderDisResult(); if (S.lastSim) renderSim(); for (const k in S.fc) drawForecast(k, S.fc[k]);
});
const KZ_MONTH = ['қаңтар', 'ақпан', 'наурыз', 'сәуір', 'мамыр', 'маусым', 'шілде', 'тамыз', 'қыркүйек', 'қазан', 'қараша', 'желтоқсан'];
const fmtDT = (d) => getLang() === 'kz'
  ? `${d.getDate()} ${KZ_MONTH[d.getMonth()]} ${d.getFullYear()}, ${d.toLocaleTimeString('en-GB')}`      // браузерный kk-KZ даёт «2026 M10 1», поэтому месяц собираем сами
  : d.toLocaleString(LOC(), { dateStyle: 'medium', timeStyle: 'medium' });
setInterval(() => { $('#clock').textContent = fmtDT(new Date()); }, 1000);
// компактный режим для низких экранов (ноутбуки 1366×768): меньше «рамки», больше места под схему
const fitCompact = () => document.documentElement.classList.toggle('compact', window.innerHeight < 860);
window.addEventListener('resize', fitCompact); fitCompact();
window.addEventListener('resize', resizeCharts);

// ===================== запуск =====================
applyI18n(); markLang(); applyParams(); renderLegend(); renderResources(null);
refreshNetwork().then(() => { renderNetList(); loadForecast('A'); refreshLog(); });
setInterval(() => { if (!document.hidden) refreshNetwork(); }, 5000);
setInterval(() => { if (!document.hidden) { if (S.view === 'B') renderShoes(); refreshLog(); } }, 5000);
window.__rt = S;
S.open = openStation; S.runScenario = runStationScenario;
// ===================== вход и рабочее место =====================
const U = {
  user() { try { return JSON.parse(sessionStorage.getItem('rt_user') || 'null'); } catch (e) { return null; } },
  token() { try { return sessionStorage.getItem('rt_tok'); } catch (e) { return null; } },
};
const dspName = (u) => {                                   // «Диспетчер ст. X» на языке интерфейса (станция берётся из списка станций)
  if (!u) return '';
  const st = (S.stList || []).find(x => x.id === u.station);
  return u.role === 'dispatcher' && st ? t('u_dsp', { st: stName(st) }) : (u.name || u.user);
};
const ROLE_RU = { dispatcher: 'd_role_dispatcher', admin: 'd_role_admin', viewer: 'd_role_viewer' };
function renderUserChip() {
  const u = U.user(), tok = U.token();
  $('#uName').textContent = tok && u ? dspName(u) : t('u_guest');
  $('#uRole').textContent = tok && u ? t(ROLE_RU[u.role] || 'd_role_viewer') : '';
  $('#uRole').style.display = tok && u ? '' : 'none';
  $('#uBtn').textContent = tok ? t('u_signout') : t('u_signin');
}
$('#uBtn').onclick = () => {
  try { if (U.token()) { sessionStorage.removeItem('rt_tok'); sessionStorage.removeItem('rt_user'); } sessionStorage.removeItem('rt_guest'); } catch (e) {}
  location.reload();
};
$('#lgForm').onsubmit = async (e) => {
  e.preventDefault(); $('#lgErr').style.display = 'none';
  try {
    const r = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: $('#lgUser').value.trim(), password: $('#lgPass').value }) });
    if (!r.ok) throw new Error(r.status); const d = await r.json();
    sessionStorage.setItem('rt_tok', d.token); sessionStorage.setItem('rt_user', JSON.stringify({ user: d.user, name: d.name, role: d.role, station: d.station })); sessionStorage.removeItem('rt_guest');
    location.reload();
  } catch (err) { $('#lgErr').style.display = ''; }
};
$('#lgGuest').onclick = () => { try { sessionStorage.setItem('rt_guest', '1'); } catch (e) {} document.documentElement.classList.add('authed'); renderUserChip(); };
function renderLgChart() {
  const el = $('#lgChart'); if (!el || !window.echarts) return;
  const c = window.echarts.getInstanceByDom(el) || window.echarts.init(el);
  const hrs = Array.from({ length: 25 }, (_, i) => 6 + i * 0.5), lab = hrs.map(h => `${String(Math.floor(h)).padStart(2, '0')}:${h % 1 ? '30' : '00'}`);
  const base = hrs.map(h => 88 + 3 * Math.sin(h * 1.7));
  const fail = base.map((v, i) => hrs[i] < 10 ? v : Math.max(38, v - 52 * (1 - Math.exp(-(hrs[i] - 10) * 1.3)) + (hrs[i] > 15 ? (hrs[i] - 15) * 1.6 : 0)));
  const fix = base.map((v, i) => hrs[i] < 10 ? v : (hrs[i] < 10.5 ? v - 34 * (1 - (hrs[i] - 10) / 0.5 * 0.75) : v - 9 * Math.exp(-(hrs[i] - 10.5) * 0.9)));
  c.setOption({
    animationDuration: 1400, grid: { left: 34, right: 26, top: 40, bottom: 26 },
    legend: { top: 0, left: 0, itemWidth: 14, itemHeight: 3, textStyle: { color: '#3b4f68', fontSize: 11.5 } },
    tooltip: { trigger: 'axis', valueFormatter: (v) => v.toFixed(0) },
    xAxis: { type: 'category', data: lab, boundaryGap: false, axisLabel: { color: '#667a93', interval: 3 }, axisLine: { lineStyle: { color: '#c3d1e2' } } },
    yAxis: { type: 'value', min: 30, max: 100, splitLine: { lineStyle: { color: '#e1e9f3' } }, axisLabel: { color: '#667a93' } },
    series: [
      { name: t('lg_s1'), type: 'line', smooth: true, showSymbol: false, data: fail.map(v => +v.toFixed(1)), lineStyle: { color: '#d2453c', width: 2.5 }, areaStyle: { color: 'rgba(210,69,60,.10)' },
        markLine: { symbol: 'none', silent: true, lineStyle: { color: '#8a9bb0', type: 'dashed' }, label: { formatter: '10:00', color: '#667a93', position: 'insideEndBottom' }, data: [{ xAxis: '10:00' }] } },
      { name: t('lg_s2'), type: 'line', smooth: true, showSymbol: false, data: fix.map(v => +v.toFixed(1)), lineStyle: { color: '#1a6fd1', width: 3 }, areaStyle: { color: 'rgba(26,111,209,.14)' } },
    ],
  }, true);
  setTimeout(() => c.resize(), 60);
}
let demoUsers = [];
function renderDemoList() {
  $('#lgDemo').style.display = demoUsers.length ? '' : 'none';
  $('#lgDemoList').innerHTML = demoUsers.map(u => `<div class="lg-demo-row"><div><b>${dspName({ role: 'dispatcher', station: u.station, name: u.name, user: u.user })}</b><br><span class="mono">${u.user} / ${u.password}</span></div><button type="button" class="btn sm" data-u="${u.user}" data-p="${u.password}">${t('lg_demo_use')}</button></div>`).join('');
  $$('#lgDemoList button').forEach(b => b.onclick = () => { $('#lgUser').value = b.dataset.u; $('#lgPass').value = b.dataset.p; $('#lgForm').requestSubmit(); });
}
async function initLanding() {
  renderLgChart(); window.addEventListener('resize', () => { try { window.echarts.getInstanceByDom($('#lgChart')).resize(); } catch (e) {} });
  try { S.stList = await (await fetch('/api/v1/stations')).json(); } catch (e) { S.stList = []; }
  renderUserChip();
  try { const i = await (await fetch('/api/v1/auth/info')).json(); demoUsers = i.demo_users || []; renderDemoList(); } catch (e) { /* без демо-списка */ }
}
initLanding();

fetch('/api/v1/stations').then(r => r.json()).then(list => { S.dash = initDash({ stations: list }); showView('A'); }).catch(() => { showView('A'); });
