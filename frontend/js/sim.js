// Дискретно-событийная модель маневровой работы станции (эмуляция эксперимента AnyLogic Cloud).
// Ресурсы: приёмо-отправочные пути, маневровые бригады (ЧМЭ3), сортировочные пути.
// Поток: прибытие -> занятие приёмо-отправочного пути -> бригада + сортировочный путь -> обработка -> отправление.

function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

class Heap {
  constructor() { this.a = []; }
  push(x) { const a = this.a; a.push(x); let i = a.length - 1; while (i > 0) { const p = (i - 1) >> 1; if (a[p].t <= a[i].t) break; [a[p], a[i]] = [a[i], a[p]]; i = p; } }
  pop() { const a = this.a; const top = a[0]; const last = a.pop(); if (a.length) { a[0] = last; let i = 0; for (;;) { let l = 2 * i + 1, r = l + 1, m = i; if (l < a.length && a[l].t < a[m].t) m = l; if (r < a.length && a[r].t < a[m].t) m = r; if (m === i) break; [a[m], a[i]] = [a[i], a[m]]; i = m; } } return top; }
  get size() { return this.a.length; }
}

const HORIZON = 24 * 60, WARM = 120;
const OVERHEAD = 9;       // мин: вытягивание/осаживание, сцепка (горловина)
const DEPART = 6;         // мин: подготовка и отправление

function runOnce(p, seed) {
  const rnd = mulberry32(seed);
  const expo = (mean) => -Math.log(1 - rnd()) * mean;
  const logn = (mean, cv) => { const s2 = Math.log(1 + cv * cv), m = Math.log(mean) - s2 / 2; const u = Math.sqrt(-2 * Math.log(1 - rnd())) * Math.cos(2 * Math.PI * rnd()); return Math.exp(m + Math.sqrt(s2) * u); };
  const ev = new Heap();
  let freeRecv = p.recv, freeCrew = p.crews, freeLoco = p.locos, freeSort = p.sort;
  const qTrack = [], qCrew = [];
  let served = 0, sumWait = 0, sumDwell = 0, crewBusyArea = 0, locoBusyArea = 0, lastT = 0, qArea = 0, maxQ = 0, blocked = 0;
  const hourly = new Array(24).fill(0), hourlyQ = new Array(24).fill(0), hourlyU = new Array(24).fill(0);
  const inSys = new Map();
  let id = 0;

  const mean = 60 / p.lambda;
  ev.push({ t: expo(mean), type: 'arr' });
  const acc = (t) => {
    const dt = Math.max(0, Math.min(t, HORIZON) - lastT);
    if (lastT >= WARM) {
      qArea += qCrew.length * dt;
      crewBusyArea += (p.crews - freeCrew) * dt;
      locoBusyArea += (p.locos - freeLoco) * dt;
    }
    const h = Math.min(23, Math.floor(lastT / 60));
    hourlyQ[h] += (qCrew.length + qTrack.length) * dt;
    hourlyU[h] += (p.crews - freeCrew) * dt;
    lastT = Math.min(t, HORIZON);
  };

  const tryStartTrack = (t) => {
    while (qTrack.length && freeRecv > 0) { const tr = qTrack.shift(); freeRecv--; tr.recvAt = t; qCrew.push(tr); tryStartService(t); }
  };
  const tryStartService = (t) => {
    while (qCrew.length && freeCrew > 0 && freeLoco > 0 && freeSort > 0) {
      const tr = qCrew.shift(); freeCrew--; freeLoco--; freeSort--;
      tr.waitCrew = t - tr.arr;
      const dur = OVERHEAD + logn(p.proc, 0.35);
      ev.push({ t: t + dur, type: 'svc', tr });
    }
    maxQ = Math.max(maxQ, qCrew.length + qTrack.length);
  };

  while (ev.size) {
    const e = ev.pop();
    if (e.t > HORIZON) break;
    acc(e.t);
    if (e.type === 'arr') {
      const tr = { id: ++id, arr: e.t };
      if (qTrack.length > 12) blocked++; else { qTrack.push(tr); tryStartTrack(e.t); }
      ev.push({ t: e.t + expo(mean), type: 'arr' });
    } else if (e.type === 'svc') {
      freeCrew++; freeLoco++; freeRecv++;
      ev.push({ t: e.t + DEPART, type: 'dep', tr: e.tr });
      tryStartTrack(e.t); tryStartService(e.t);
    } else if (e.type === 'dep') {
      freeSort++;
      const tr = e.tr;
      if (tr.arr >= WARM) { served++; sumWait += tr.waitCrew; sumDwell += e.t - tr.arr; hourly[Math.min(23, Math.floor(e.t / 60))]++; }
      tryStartService(e.t);
    }
  }
  acc(HORIZON);
  const span = HORIZON - WARM;
  return {
    served: served * (1440 / span),
    wait: served ? sumWait / served : 0,
    dwell: served ? sumDwell / served : 0,
    util: crewBusyArea / (span * p.crews), utilLoco: locoBusyArea / (span * p.locos),
    maxQ, blocked,
    hourlyQ: hourlyQ.map(v => v / 60), hourlyU: hourlyU.map(v => v / 60 / p.crews),
  };
}

export function runExperiment(params, reps = 24) {
  const agg = { served: 0, wait: 0, dwell: 0, util: 0, utilLoco: 0, maxQ: 0, blocked: 0 };
  const hq = new Array(24).fill(0), hu = new Array(24).fill(0);
  const servs = [];
  for (let i = 0; i < reps; i++) {
    const r = runOnce(params, 1000 + i * 7919);
    for (const k in agg) agg[k] += r[k];
    servs.push(r.served);
    r.hourlyQ.forEach((v, h) => hq[h] += v); r.hourlyU.forEach((v, h) => hu[h] += v);
  }
  for (const k in agg) agg[k] /= reps;
  const mu = agg.served;
  const sd = Math.sqrt(servs.reduce((s, v) => s + (v - mu) ** 2, 0) / (reps - 1));
  const svc = OVERHEAD + params.proc;
  const capCrew = params.crews * 1440 / svc, capLoco = params.locos * 1440 / svc, capSort = params.sort * 1440 / (svc + DEPART), capRecv = params.recv * 1440 / (svc + 20);
  const capacity = Math.min(capCrew, capLoco, capSort, capRecv);
  const bottleneck = capacity === capCrew ? 'crew' : capacity === capLoco ? 'loco' : 'track';
  return {
    throughput: agg.served, ci95: 1.96 * sd / Math.sqrt(reps), capacity,
    wait: agg.wait, dwell: agg.dwell, util: agg.util * 100, utilLoco: agg.utilLoco * 100, maxQ: agg.maxQ, blocked: agg.blocked,
    offered: params.lambda * 24, bottleneck,
    hourlyQ: hq.map(v => v / reps), hourlyU: hu.map(v => v / reps * 100), capCrew, capLoco, capSort, capRecv,
  };
}

// Чувствительность: пропускная способность при 1..5 бригадах
export function crewSweep(params) {
  const out = [];
  for (let c = 1; c <= 5; c++) out.push({ crews: c, thr: runExperiment({ ...params, crews: c, locos: c }, 10).throughput });
  return out;
}
