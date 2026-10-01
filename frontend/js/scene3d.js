// Микро-уровень: 3D-двойник станции (Three.js). Топология приходит из бэкенда (/api/stations/{id}).
// Ресурсы станции: пути, маневровые локомотивы (ЧМЭ3), составительские бригады, стрелочные маршруты (горловина).
import * as THREE from 'three';

const U = 4.5;                       // м на единицу сцены
const WL = 3.35;                     // шаг вагонов
const LOCO_GAP = 3.6;                // центр ЧМЭ3 -> центр крайнего вагона
const FRONT_GAP = 4.2;               // центр линейного локомотива -> центр первого вагона
const MAIN_VIS = 210;                // видимая длина главного пути
const SCALE = 2;                     // минут модельного времени в секунде времени сцены (см. backend/dynamics.py)
const KIND_COL = { main: 0x8795a8, reception: 0x5b8fc9, sorting: 0x8f8fb5, depot: 0xcf9a3a, lead: 0x56667c };
const ST_COL = { ok: 0x4aa37a, occupied: 0xcf9a3a, fault: 0xc9534f, free: 0x66758a, secured: 0x4aa37a, alarm: 0xc9534f };
const WAGON_COLS = [0x6b4a3a, 0x4b5563, 0x3d5b53, 0x57677e, 0x7a5a36, 0x3b4a63, 0x666d78, 0x6e3a3a];
const CONT_COLS = [0xb94a45, 0x3d6aa8, 0xc9953a, 0x4f8a5e, 0x4a8fb0, 0xb96a3a, 0x8e9aab];

const lerp = (a, b, t) => a + (b - a) * t;
const rnd = (a, b) => a + Math.random() * (b - a);
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

class Path {
  constructor(pts) {
    this.pts = pts.map(p => ({ x: p[0], z: p[1] })); this.cum = [0];
    for (let i = 1; i < this.pts.length; i++) this.cum.push(this.cum[i - 1] + Math.hypot(this.pts[i].x - this.pts[i - 1].x, this.pts[i].z - this.pts[i - 1].z));
    this.len = this.cum[this.cum.length - 1];
    this.ang = []; for (let i = 1; i < this.pts.length; i++) this.ang.push(Math.atan2(this.pts[i].z - this.pts[i - 1].z, this.pts[i].x - this.pts[i - 1].x));
  }
  pose(d) {
    const p = this.pts, n = p.length;
    if (d <= 0) { const a = this.ang[0]; return { x: p[0].x + Math.cos(a) * d, z: p[0].z + Math.sin(a) * d, a }; }
    if (d >= this.len) { const a = this.ang[n - 2]; const e = d - this.len; return { x: p[n - 1].x + Math.cos(a) * e, z: p[n - 1].z + Math.sin(a) * e, a }; }
    let i = 0; while (i < n - 2 && this.cum[i + 1] < d) i++;
    const sl = this.cum[i + 1] - this.cum[i], k = (d - this.cum[i]) / sl;
    let a = this.ang[i];
    const w = 1.4, off0 = d - this.cum[i], off1 = this.cum[i + 1] - d;
    if (off0 < w && i > 0) a = angBlend(this.ang[i - 1], this.ang[i], 0.5 + 0.5 * off0 / w);
    else if (off1 < w && i < n - 2) a = angBlend(this.ang[i], this.ang[i + 1], 0.5 * (1 - off1 / w));
    return { x: lerp(p[i].x, p[i + 1].x, k), z: lerp(p[i].z, p[i + 1].z, k), a };
  }
}
function angBlend(a, b, t) { let d = b - a; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI; return a + d * t; }

class Mutex {                                           // горловина: один замкнутый маршрут за раз
  constructor() { this.locked = false; this.q = []; this.owner = null; }
  acquire(tag) { return new Promise(res => { if (!this.locked) { this.locked = true; this.owner = tag; res(); } else this.q.push({ res, tag }); }); }
  release() { const n = this.q.shift(); if (n) { this.owner = n.tag; n.res(); } else { this.locked = false; this.owner = null; } }
  pending() { return this.q.map(x => x.tag); }
}

function plate(ctx, w, h, r, fill, stroke) {
  ctx.fillStyle = fill; ctx.strokeStyle = stroke; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(r, 1); ctx.arcTo(w - 1, 1, w - 1, h - 1, r); ctx.arcTo(w - 1, h - 1, 1, h - 1, r); ctx.arcTo(1, h - 1, 1, 1, r); ctx.arcTo(1, 1, w - 1, 1, r); ctx.closePath(); ctx.fill(); ctx.stroke();
}
function makeLabel(text, { color = '#dde4ed', bg = 'rgba(20,28,39,.92)', border = '#3a4b61', w = 128, h = 48, font = '600 24px "Segoe UI", Arial, sans-serif', scale = 0.035 } = {}) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false, depthTest: false }));
  sp.renderOrder = 10; sp.scale.set(w * scale, h * scale, 1); sp.userData = { cv: c, opts: { color, bg, border, w, h, font } };
  relabel(sp, text); return sp;
}
function relabel(sp, text, color, border) {
  const { cv, opts } = sp.userData; const x = cv.getContext('2d'); const { w, h } = opts; x.clearRect(0, 0, w, h);
  plate(x, w, h, 7, opts.bg, border || opts.border);
  x.fillStyle = color || opts.color; x.font = opts.font; x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText(text, w / 2, h / 2 + 1);
  sp.material.map.colorSpace = THREE.SRGBColorSpace; sp.material.map.needsUpdate = true;
}

const G = { box: new THREE.BoxGeometry(1, 1, 1), cyl: new THREE.CylinderGeometry(1, 1, 1, 20), sph: new THREE.SphereGeometry(1, 14, 10), cone: new THREE.ConeGeometry(1, 1, 24, 1, true) };
const M = {};
// Светлая «дневная» тема: тёмные цвета окружения (здания, горы, балласт, платформы) смешиваются со светлым тоном, подвижной состав не трогаем
const DECOR = /^(bld|bldroof|bwin|mtn|twr|coal|bunker|cust|custroof|emb|platform|platlong|ballast|swbase|elev|ship|shipb|sleeper|mast|gant|pole|stem|gate|rfidp|camh)/;
const lighten = (c, k) => { const a = new THREE.Color(c), b = new THREE.Color(0xb4c3d6); return a.lerp(b, k); };
const mat = (key, col, o = {}) => (M[key] ||= new THREE.MeshStandardMaterial({ color: DECOR.test(key) ? lighten(col, 0.62) : col, roughness: 0.75, metalness: 0.15, ...o }));
function bx(parent, w, h, d, x, y, z, m) { const me = new THREE.Mesh(G.box, m); me.scale.set(w, h, d); me.position.set(x, y, z); parent.add(me); return me; }

let CARGO_W = { box: 3, tank: 1, gondola: 1, platform: 1 };
function pickCargo() {
  const tot = Object.values(CARGO_W).reduce((a, b) => a + b, 0); let r = Math.random() * tot;
  for (const k in CARGO_W) { r -= CARGO_W[k]; if (r <= 0) return k; }
  return 'box';
}

function buildWagon(forceType) {
  const g = new THREE.Group(); g.userData.veh = true;
  const type = forceType || pickCargo();
  bx(g, 3.1, 0.22, 1.25, 0, 0.55, 0, mat('frame', 0x1a212c));
  for (const sx of [-1, 1]) bx(g, 0.95, 0.34, 1.2, sx * 1.05, 0.3, 0, mat('bogie', 0x0f141c));
  const col = pick(WAGON_COLS);
  if (type === 'box') { bx(g, 3.0, 1.25, 1.35, 0, 1.3, 0, mat('w' + col, col)); bx(g, 3.05, 0.1, 1.4, 0, 1.95, 0, mat('roof', 0x2a303b)); }
  else if (type === 'tank') { const t = new THREE.Mesh(G.cyl, mat('tank' + col, pick([0x2a3441, 0x8d98a8, 0x3a4554]), { metalness: 0.5, roughness: 0.5 })); t.scale.set(0.68, 2.7, 0.68); t.rotation.z = Math.PI / 2; t.position.y = 1.35; g.add(t); }
  else if (type === 'gondola') { bx(g, 3.0, 1.0, 1.35, 0, 1.2, 0, mat('g' + col, col)); bx(g, 2.7, 0.08, 1.1, 0, 1.68, 0, mat('gin', 0x0b0f16)); }
  else { bx(g, 3.0, 0.12, 1.3, 0, 0.78, 0, mat('plat', 0x3b4252)); const n = type === 'container' ? 2 : (Math.random() < 0.5 ? 1 : 2); for (let i = 0; i < n; i++) { const cc = pick(CONT_COLS); const L = n === 1 ? 2.8 : 1.35; bx(g, L, 1.15, 1.2, n === 1 ? 0 : (i ? 0.75 : -0.75), 1.4, 0, mat('c' + cc, cc, { roughness: 0.6 })); } }
  return g;
}

function buildLoco(livery) {
  const g = new THREE.Group(); g.userData.veh = true;
  const body = livery === 'line' ? 0x2a5a96 : 0x8a2f33, stripe = livery === 'line' ? 0xd2a43c : 0xcbd3de;
  bx(g, 4.0, 0.34, 1.5, 0, 0.55, 0, mat('lframe', 0x141a24));
  for (const sx of [-1, 1]) bx(g, 1.15, 0.38, 1.35, sx * 1.2, 0.32, 0, mat('bogie', 0x0f141c));
  bx(g, 2.5, 1.2, 1.4, 0.7, 1.3, 0, mat('lb' + body, body, { metalness: 0.3, roughness: 0.55 }));
  bx(g, 1.4, 1.75, 1.5, -1.2, 1.55, 0, mat('lb' + body, body, { metalness: 0.3, roughness: 0.55 }));
  bx(g, 1.5, 0.1, 1.6, -1.2, 2.47, 0, mat('lroof', 0x1f2430));
  bx(g, 4.02, 0.14, 1.52, 0, 0.95, 0, mat('stripe' + stripe, stripe));
  const win = mat('win', 0x9db8cc, { emissive: 0x2a4a62, emissiveIntensity: 0.5, roughness: 0.25 });
  bx(g, 0.06, 0.6, 1.1, -1.92, 1.95, 0, win); bx(g, 0.5, 0.6, 0.06, -1.2, 1.95, 0.76, win); bx(g, 0.5, 0.6, 0.06, -1.2, 1.95, -0.76, win);
  const hl = mat('hl', 0xfff3d0, { emissive: 0xfff3d0, emissiveIntensity: 1.4 });
  for (const sz of [-0.5, 0.5]) { const s = new THREE.Mesh(G.sph, hl); s.scale.set(0.16, 0.16, 0.16); s.position.set(2.02, 1.05, sz); g.add(s); }
  return g;
}

function buildShoe() {
  const g = new THREE.Group(); const m = mat('shoe', 0xd9822b, { emissive: 0x7a3d00, emissiveIntensity: 0.5 });
  bx(g, 0.5, 0.18, 0.2, 0, 0.26, 0.74, m); const w = bx(g, 0.28, 0.2, 0.2, 0.2, 0.3, 0.74, m); w.rotation.z = -0.5; return g;
}

// =====================================================================
export class StationScene {
  constructor(container, hooks = {}) {
    this.el = container; this.hooks = hooks; this.alive = true; this.paused = false;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.0;
    container.appendChild(this.renderer.domElement); this.renderer.domElement.style.display = 'block';
    this.scene = new THREE.Scene(); this.scene.background = new THREE.Color(0xdbe8f6);
    this.scene.fog = new THREE.Fog(0xdbe8f6, 260, 620);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.5, 1200);
    this.cam = { theta: 0.0, phi: 0.72, r: 160, tx: -4, ty: 0, tz: 11, goal: null, auto: false };
    this._lights(); this._ground();
    this.world = new THREE.Group(); this.scene.add(this.world);
    this.tweens = []; this.waiters = []; this.layer = 'ops'; this.pickables = [];
    this.params = { lambda: 2.2, locos: 2, crews: 2, proc: 35 };
    this.stats = { trains: 0, locos: 0, queue: 0, done: 0, km: 0 };
    this.optimized = false; this.clock = new THREE.Clock(); this._statT = 0; this._sensT = 0;
    this.ray = new THREE.Raycaster(); this.mouse = new THREE.Vector2();
    this._bindControls();
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(container);
    this.resize();
    this._anim = this._anim.bind(this); this._raf = requestAnimationFrame(this._anim);
  }

  _lights() {
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.95));
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xa9bccf, 0.8));
    const d = new THREE.DirectionalLight(0xffffff, 1.05); d.position.set(-80, 140, 90); this.scene.add(d);
    const d2 = new THREE.DirectionalLight(0xcfe0f5, 0.35); d2.position.set(120, 60, -80); this.scene.add(d2);
  }
  _ground() {
    this.groundMat = new THREE.MeshStandardMaterial({ color: 0xc5d3c4, roughness: 0.95, metalness: 0 });
    const gm = new THREE.Mesh(new THREE.PlaneGeometry(1400, 1400), this.groundMat);
    gm.rotation.x = -Math.PI / 2; gm.position.y = -0.02; this.scene.add(gm);
    const grid = new THREE.GridHelper(900, 150, 0xaebed0, 0xbccadb); grid.material.transparent = true; grid.material.opacity = 0.5; this.scene.add(grid);
  }
  resize() {
    const w = this.el.clientWidth || 800, h = this.el.clientHeight || 500;
    this.renderer.setSize(w, h, false); this.renderer.domElement.style.width = w + 'px'; this.renderer.domElement.style.height = h + 'px';
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    // пока пользователь не двигал камеру вручную, станция всегда целиком в кадре при любом размере окна
    if (!this.userCam && this.camMode && this.camMode !== 'orbit' && this.L) this.setCamera(this.camMode);
  }

  // ---------- камера и выбор объектов ----------
  _bindControls() {
    const el = this.renderer.domElement; let drag = null;
    el.addEventListener('contextmenu', e => e.preventDefault());
    el.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, pan: e.button === 2 || e.shiftKey }; el.setPointerCapture(e.pointerId); });
    el.addEventListener('pointermove', e => {
      if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY; const c = this.cam;
      if (Math.abs(e.clientX - drag.x0) + Math.abs(e.clientY - drag.y0) > 4) { c.auto = false; c.goal = null; this.userCam = true; }
      if (drag.pan) { const k = c.r * 0.0016, rx = Math.cos(c.theta), rz = -Math.sin(c.theta), fx = -Math.sin(c.theta), fz = -Math.cos(c.theta); c.tx += -rx * dx * k + fx * dy * k; c.tz += -rz * dx * k + fz * dy * k; }
      else if (!c.goal) { c.theta -= dx * 0.005; c.phi = Math.max(0.05, Math.min(1.5, c.phi - dy * 0.005)); }
    });
    el.addEventListener('pointerup', e => {
      const moved = drag && Math.abs(e.clientX - drag.x0) + Math.abs(e.clientY - drag.y0) > 4; drag = null;
      if (!moved) this._pick(e);
    });
    el.addEventListener('wheel', e => { e.preventDefault(); this.userCam = true; this.cam.r = Math.max(30, Math.min(420, this.cam.r * (e.deltaY > 0 ? 1.1 : 0.9))); this.cam.goal = null; }, { passive: false });
  }
  _pick(e) {
    if (this.layer === 'ops' || !this.pickables.length) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    this.mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(this.mouse, this.camera);
    const hit = this.ray.intersectObjects(this.pickables.filter(o => o.parent && o.parent.visible), false)[0];
    if (hit && hit.object.userData.item) this.hooks.onPick && this.hooks.onPick(hit.object.userData.item, e);
  }
  fitR() { return Math.max(70, Math.min(300, 92 / (0.384 * this.camera.aspect))); }
  setCamera(mode) {
    const c = this.cam, R = this.fitR();
    this.camMode = mode; this.userCam = false;
    if (mode === 'top') c.goal = { theta: 0, phi: 0.06, r: R * 0.9, tx: 0, tz: 13 };
    else if (mode === 'persp') c.goal = { theta: 0.1, phi: 0.72, r: R, tx: -4, tz: 11 };
    else if (mode === 'close') c.goal = { theta: -0.55, phi: 0.6, r: R * 0.62, tx: -35, tz: 12 };
    c.auto = mode === 'orbit' ? !c.auto : false;
    if (mode === 'orbit' && c.auto) c.goal = { theta: c.theta, phi: 0.7, r: R * 1.05, tx: -4, tz: 11 };
  }
  focus(x, z, r = 50) { this.userCam = true; this.cam.auto = false; this.cam.goal = { theta: -0.4, phi: 0.7, r, tx: x, tz: z }; }
  _updateCam(dt) {
    const c = this.cam;
    if (c.goal) {
      const k = 1 - Math.exp(-dt * 3.2);
      for (const key of ['theta', 'phi', 'r', 'tx', 'tz']) if (c.goal[key] !== undefined) c[key] = lerp(c[key], c.goal[key], k);
      if (Math.abs(c.r - c.goal.r) < 0.5 && !c.auto) c.goal = null;
    }
    if (c.auto) c.theta += dt * 0.12;
    const sp = Math.sin(c.phi), cp = Math.cos(c.phi);
    this.camera.position.set(c.tx + c.r * sp * Math.sin(c.theta), c.r * cp, c.tz + c.r * sp * Math.cos(c.theta));
    this.camera.lookAt(c.tx, 0, c.tz);
  }

  // ---------- загрузка станции ----------
  clear() {
    this.alive = false;
    for (const w of this.waiters) w.resolve(false); this.waiters = []; this.tweens = [];
    this.world.traverse(o => { if (o.material && o.material.map) o.material.map.dispose(); });
    this.scene.remove(this.world); this.world = new THREE.Group(); this.scene.add(this.world); this.pickables = [];
  }

  load(layout, meta, safety) {
    this.clear(); this.alive = true; this.epoch = (this.epoch || 0) + 1;
    this.L = layout; this.meta = meta; this.n = layout.n_tracks;
    this.byIdx = Object.fromEntries(layout.tracks.map(t => [t.idx, t]));
    this.neckMutex = new Mutex(); this.rightMutex = new Mutex();
    this.trains = []; this.resRecv = new Set(); this.resSort = new Set(); this.locoPool = []; this.locoTotal = 0; this.locoBusy = 0;
    this.crewBusy = 0; this.crewsAbsent = 0; this.absentUntil = 0; this.locoExtra = 0; this.extraUntil = 0;
    this.failedSw = new Map(); this.sw = {}; this.sig = {}; this.shoes = {}; this.outside = 0; this.outsideSince = 0; this.waitCrewQ = 0;
    this.departedQ = []; this.activeRoutes = new Set(); this.trackLabels = {}; this.sensors = []; this.cams = [];
    this.stats = { trains: 0, locos: 0, queue: 0, done: 0, km: 0 };
    this.t = 0; this.trainSeq = 0; this.nextArrival = 4;
    this.profile = (meta && meta.profile) || {}; CARGO_W = this.profile.cargo || { box: 3, tank: 1, gondola: 1, platform: 1 };
    this.procMult = 1; this.procUntil = 0; this.routeExtra = 0; this.routeUntil = 0; this.burstLeft = 0; this.locoLost = 0; this.lostUntil = 0;
    this.extraBlocked = new Map(); this.storage = []; this.bunchNext = false; this.burstCountdown = 3;
    this.groundMat.color.setHex(this.profile.season === 'winter' ? 0xe6edf5 : 0xc5d3c4);
    this._buildStatic(); this._buildDecor(); this._buildIot(); this._buildCctv(); this.setLayer(this.layer);
    this.minLen = Math.min(...layout.tracks.filter(t => t.idx > 0).map(t => t.x1 - t.x0));
    this.maxWag = Math.max(5, Math.min(11, Math.floor((this.minLen - 28) / WL)));
    this.syncLocos();
    const recv = layout.tracks.filter(t => t.kind === 'reception'), sort = layout.tracks.filter(t => t.kind === 'sorting');
    this.spawnStatic(recv[0].idx, 'wait');
    if (recv.length > 2) this.spawnStatic(recv[2].idx, 'wait');
    if (sort.length) this.spawnStatic(sort[sort.length - 1].idx, 'sorted');
    if (safety && safety.shoes) for (const s of safety.shoes) this.addShoe(s.id, s.track, false);
    this.cam.theta = 0; this.cam.phi = 1.0; this.cam.r = this.fitR() * 1.15; this.cam.tx = -4; this.cam.tz = 11; this.cam.auto = false;
    this.camMode = "persp"; this.userCam = false; this.cam.goal = { theta: 0.12, phi: 0.72, r: this.fitR(), tx: -4, tz: 11 };
  }

  _buildStatic() {
    const L = this.L, W = this.world; this.sleepData = [];
    const addPoly = (pts, kind) => { for (let i = 1; i < pts.length; i++) this._seg(W, pts[i - 1], pts[i], kind); };
    addPoly([[-MAIN_VIS, 0], [MAIN_VIS, 0]], 'main');
    for (const t of L.tracks) if (t.idx > 0) addPoly([[t.x0, t.z], [t.x1, t.z]], t.kind);
    addPoly(L.left_lead, 'lead'); addPoly(L.right_lead, 'lead');
    const dp = L.depot; addPoly([[dp.x0 - 6, dp.z], [dp.x1, dp.z], [dp.merge_x, 0]], 'depot');
    const sg = new THREE.InstancedMesh(G.box, mat('sleeper', 0x3a3028, { roughness: 1 }), this.sleepData.length);
    const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3(0.38, 0.1, 2.3), ps = new THREE.Vector3();
    this.sleepData.forEach((s, i) => { q.setFromEuler(new THREE.Euler(0, -s.a, 0)); ps.set(s.x, 0.11, s.z); m4.compose(ps, q, sc); sg.setMatrixAt(i, m4); });
    W.add(sg);
    for (const t of L.tracks) {
      if (t.idx === 0) { const l = makeLabel('ГЛАВНЫЙ', { color: '#aab6c6', w: 128, h: 36, scale: 0.03, font: '600 18px "Segoe UI", Arial' }); l.position.set(-165, 1.6, 1.6); W.add(l); continue; }
      const col = t.kind === 'reception' ? '#7fa9d6' : '#aaa8cf';
      const a = makeLabel(String(t.idx), { color: col, border: col, w: 56, h: 40, scale: 0.03 }), b = makeLabel(String(t.idx), { color: col, border: col, w: 56, h: 40, scale: 0.03 });
      a.position.set(t.x1 + 3.4, 1.7, t.z); b.position.set(t.x0 - 3.4, 1.7, t.z); W.add(a); W.add(b); this.trackLabels[t.idx] = { a, b, col };
    }
    const dl = makeLabel('ЧМЭ3 · ДЕПО', { color: '#cf9a3a', border: '#8a6a2a', w: 150, h: 36, scale: 0.03, font: '600 17px "Segoe UI", Arial' }); dl.position.set((dp.x0 + dp.x1) / 2 - 2, 3.4, dp.z - 3.4); W.add(dl);
    for (const s of L.switches) {
      const g = new THREE.Group(); g.position.set(s.x, 0.16, s.z);
      bx(g, 1.3, 0.1, 1.3, 0, 0, 0, mat('swbase', 0x0b1522));
      const lampM = new THREE.MeshStandardMaterial({ color: 0x4aa37a, emissive: 0x4aa37a, emissiveIntensity: 0.9 });
      const lamp = new THREE.Mesh(G.sph, lampM); lamp.scale.set(0.34, 0.34, 0.34); lamp.position.set(0, 0.5, s.side === 'L' ? -1.4 : 1.4); g.add(lamp);
      const blade = bx(g, 1.5, 0.12, 0.12, 0, 0.12, 0, mat('blade', 0xc3ccd8));
      const lb = makeLabel(String(s.id), { w: 48, h: 34, scale: 0.022, font: '600 18px "Segoe UI", Arial', color: '#cf9a3a', border: '#6a5324' }); lb.position.set(0, 2.1, s.side === 'L' ? -2.2 : 2.2); g.add(lb);
      W.add(g); this.sw[s.id] = { g, lampM, blade, pos: 'N', locked: false, side: s.side, head: s.head, x: s.x, z: s.z };
    }
    for (const s of L.signals) this._signal(W, s);
    const post = new THREE.Group(); post.position.set(-6, 0, -14);
    bx(post, 18, 5, 7, 0, 2.5, 0, mat('bld', 0x1c2735)); bx(post, 18.4, 0.5, 7.4, 0, 5.2, 0, mat('bldroof', 0x111926));
    const wm = mat('bwin', 0x8fa8bd, { emissive: 0x2f4a60, emissiveIntensity: 0.6 });
    for (let i = -3; i <= 3; i++) bx(post, 1.4, 1.5, 0.1, i * 2.3, 2.9, 3.55, wm);
    W.add(post);
    const pl = makeLabel('ДСП · ПОСТ ЭЦ', { color: '#9fb2c8', w: 150, h: 36, scale: 0.03, font: '600 17px "Segoe UI", Arial' }); pl.position.set(-6, 8, -14); W.add(pl);
    bx(W, 90, 0.5, 3.2, 0, 0.25, -3.6, mat('platform', 0x2a3646));
    for (const x of [-120, -50, 40, 120]) { const m = new THREE.Group(); m.position.set(x, 0, -8); bx(m, 0.35, 11, 0.35, 0, 5.5, 0, mat('mast', 0x4a5666)); const lm = new THREE.Mesh(G.sph, new THREE.MeshStandardMaterial({ color: 0xf2e6c9, emissive: 0xf2e6c9, emissiveIntensity: 1.2 })); lm.scale.set(0.9, 0.5, 0.9); lm.position.y = 11; m.add(lm); W.add(m); }
    this.rfid = [];
    for (const t of L.tracks) if (t.idx > 0) {
      const r = new THREE.Group(); r.position.set(t.x1 - 2.5, 0, t.z);
      bx(r, 0.2, 3.2, 0.2, 0, 1.6, -1.5, mat('rfidp', 0x3a4756)); bx(r, 0.2, 3.2, 0.2, 0, 1.6, 1.5, mat('rfidp', 0x3a4756)); bx(r, 0.25, 0.25, 3.2, 0, 3.2, 0, mat('rfidp', 0x3a4756));
      const lm = new THREE.MeshStandardMaterial({ color: 0x5b8fc9, emissive: 0x5b8fc9, emissiveIntensity: 0.5 }); const s = new THREE.Mesh(G.sph, lm); s.scale.set(0.26, 0.26, 0.26); s.position.set(0, 3.45, 0); r.add(s);
      W.add(r); this.rfid[t.idx] = { lm, g: r };
    }
  }

  _buildDecor() {
    const W = this.world, kind = this.profile.decor || 'steppe', n = this.n, zf = n * this.L.dz + 14;
    const snow = this.profile.season === 'winter';
    const g = new THREE.Group(); W.add(g);
    g.position.z = { mountains: 18, refinery: 20, city: 14, sea: 22, silos: 18, chimneys: 16, slope: 12, steppe: 8 }[kind] || 0;   // ближе к кадру
    const cyl = (r, h, x, y, z, col, mm = {}) => { const m = new THREE.Mesh(G.cyl, mat('dc' + col + (mm.metalness || 0), col, mm)); m.scale.set(r, h, r); m.position.set(x, y, z); g.add(m); return m; };
    const lab = (txt, x, y, z, col = '#9fb2c8') => { const l = makeLabel(txt, { color: col, w: 170, h: 34, scale: 0.03, font: '600 16px "Segoe UI", Arial' }); l.position.set(x, y, z); g.add(l); };
    if (kind === 'mountains') {
      for (let i = -6; i <= 6; i++) { const h = 40 + Math.abs(Math.sin(i * 1.7)) * 45, r = 28 + Math.abs(Math.cos(i * 2.3)) * 18; const m = new THREE.Mesh(G.cone, mat('mtn', 0x1b2735, { side: THREE.DoubleSide })); m.scale.set(r, h, r); m.position.set(i * 50, h / 2 - 2, -150 - Math.abs(i) * 6); g.add(m); const tip = new THREE.Mesh(G.cone, mat('mtnsnow', 0x6d7f95)); tip.scale.set(r * 0.32, h * 0.3, r * 0.32); tip.position.set(i * 50, h * 0.85, -150 - Math.abs(i) * 6); g.add(tip); }
      lab('ТРАНС-ИЛИЙСКИЙ АЛАТАУ', 0, 70, -150);
    } else if (kind === 'junction') {
      for (const x of [-105, 105]) { bx(g, 0.5, 12, 0.5, x, 6, -6, mat('gant', 0x4a5666)); bx(g, 0.5, 12, 0.5, x, 6, zf, mat('gant', 0x4a5666)); bx(g, 0.6, 0.6, zf + 6, x, 12, zf / 2 - 3, mat('gant', 0x4a5666)); }
      lab('УЗЕЛ · РАЗВЕТВЛЕНИЕ ЛИНИЙ', 0, 16, -30);
    } else if (kind === 'refinery') {
      for (let i = 0; i < 6; i++) cyl(7, 12, 30 + i * 18, 6, -48 - (i % 2) * 18, 0x8d98a8, { metalness: 0.4 });
      cyl(2.2, 48, 150, 24, -60, 0x56637a); lab('НПЗ · НАЛИВНАЯ ЭСТАКАДА', 80, 22, -50);
    } else if (kind === 'city') {
      for (let i = 0; i < 14; i++) { const h = 14 + (i * 37 % 40); bx(g, 10 + (i % 3) * 3, h, 10, -110 + i * 17, h / 2, -70 - (i % 4) * 8, mat('twr' + (i % 3), [0x26364a, 0x2e4058, 0x1e2d3f][i % 3])); }
      bx(g, 60, 0.4, 5, 0, 0.2, -9, mat('platlong', 0x34475c)); lab('ВОКЗАЛЬНЫЙ КОМПЛЕКС', 0, 30, -70);
    } else if (kind === 'coal') {
      for (let i = 0; i < 7; i++) { const m = new THREE.Mesh(G.cone, mat('coal', 0x0a0d12)); m.scale.set(16 + (i % 3) * 5, 14 + (i % 4) * 4, 16); m.position.set(-90 + i * 32, 8, zf + 14 + (i % 2) * 8); g.add(m); }
      bx(g, 40, 12, 14, 70, 6, -30, mat('bunker', 0x2d3a4c)); lab('УГОЛЬНЫЕ СКЛАДЫ', -20, 20, zf + 14);
    } else if (kind === 'crane') {
      for (const x of [30, 62]) { bx(g, 0.9, 16, 0.9, x, 8, -6.5, mat('crane', 0xb35a3c)); bx(g, 0.9, 16, 0.9, x, 8, zf, mat('crane', 0xb35a3c)); bx(g, 1.1, 1.1, zf + 7, x, 16, zf / 2 - 3, mat('crane', 0xb35a3c)); }
      for (let i = 0; i < 12; i++) bx(g, 5, 2.6, 2.4, 20 + (i % 6) * 6, 1.3 + Math.floor(i / 6) * 2.6, zf + 6 + (i % 2) * 3, mat('cont' + (i % 4), CONT_COLS[i % 4]));
      lab('КОНТЕЙНЕРНЫЙ ТЕРМИНАЛ', 40, 20, -6.5);
    } else if (kind === 'customs') {
      bx(g, 34, 6, 12, 40, 3, -30, mat('cust', 0x2c3d52)); bx(g, 38, 0.6, 16, 40, 6.3, -30, mat('custroof', 0x1b2635)); bx(g, 0.5, 5, 0.5, -120, 2.5, -6, mat('gate', 0xcf9a3a)); bx(g, 14, 0.5, 0.5, -113, 4.8, -6, mat('gate2', 0xc9534f));
      for (const x of [20, 70]) { bx(g, 0.9, 15, 0.9, x, 7.5, -6.5, mat('crane', 0x5f7592)); bx(g, 0.9, 15, 0.9, x, 7.5, zf, mat('crane', 0x5f7592)); bx(g, 1.1, 1.1, zf + 7, x, 15, zf / 2 - 3, mat('crane', 0x5f7592)); }
      lab('ТАМОЖНЯ · ПЕРЕГРУЗ / СМЕНА КОЛЕИ', 40, 12, -30, '#e6bf72');
    } else if (kind === 'sea') {
      const sea = new THREE.Mesh(new THREE.PlaneGeometry(1400, 500), new THREE.MeshStandardMaterial({ color: 0x4a93c4, roughness: 0.35, metalness: 0.2 })); sea.rotation.x = -Math.PI / 2; sea.position.set(0, 0, -300); g.add(sea);
      bx(g, 90, 6, 16, 20, 3, -78, mat('ship', 0x3a4a5e)); bx(g, 20, 12, 14, 52, 9, -78, mat('shipb', 0x56667c));
      for (const x of [-20, 20]) { bx(g, 1, 24, 1, x, 12, -48, mat('crane', 0xb35a3c)); bx(g, 40, 1, 1, x + 14, 24, -48, mat('crane', 0xb35a3c)); }
      for (let i = 0; i < 4; i++) cyl(6, 10, 70 + i * 14, 5, -22, 0x7d8a9c, { metalness: 0.4 });
      lab('МОРСКОЙ ПОРТ АҚТАУ', 20, 30, -60);
    } else if (kind === 'silos') {
      for (let i = 0; i < 8; i++) cyl(5, 26, 10 + (i % 4) * 11.5, 13, -40 - Math.floor(i / 4) * 12, 0xb9c2cf);
      bx(g, 8, 36, 8, 70, 18, -40, mat('elev', 0x8f9bab)); lab('ЗЕРНОВОЙ ЭЛЕВАТОР', 40, 40, -40);
    } else if (kind === 'chimneys') {
      for (let i = 0; i < 3; i++) { cyl(2.4, 60, 30 + i * 22, 30, -80, 0xa0524a); cyl(2.45, 8, 30 + i * 22, 56, -80, 0xe8ecf2); }
      for (let i = 0; i < 4; i++) cyl(7, 11, -60 + i * 18, 5.5, -50, 0x7d8a9c, { metalness: 0.4 }); lab('ПРОМЗОНА · ТЭЦ', 40, 66, -80);
    } else if (kind === 'slope') {
      const emb = new THREE.Mesh(G.box, mat('emb', 0x1d2a3a)); emb.scale.set(300, 18, 60); emb.position.set(0, 4, -55); emb.rotation.x = -0.28; g.add(emb);
      lab('УКЛОН · ГРАДИЕНТ 1.2‰', -40, 24, -50, '#e6bf72');
    } else {                                   // steppe
      for (let i = 0; i < 10; i++) { bx(g, 0.3, 9, 0.3, -140 + i * 32, 4.5, -22, mat('pole', 0x4a5666)); bx(g, 4, 0.2, 0.2, -140 + i * 32, 8.6, -22, mat('pole', 0x4a5666)); }
    }
    if (snow) { const sn = new THREE.Mesh(G.box, new THREE.MeshStandardMaterial({ color: 0xcfd8e3, roughness: 1 })); sn.scale.set(420, 0.15, 6); sn.position.set(0, 0.05, -4.2); g.add(sn); const sn2 = sn.clone(); sn2.position.z = zf + 3; g.add(sn2); lab('ЗИМА · −28 °C', -150, 6, 10, '#bcd0e6'); }
  }

  _seg(W, a, b, kind) {
    const dx = b[0] - a[0], dz = b[1] - a[1], len = Math.hypot(dx, dz), ang = Math.atan2(dz, dx);
    const g = new THREE.Group(); g.position.set((a[0] + b[0]) / 2, 0, (a[1] + b[1]) / 2); g.rotation.y = -ang;
    bx(g, len + 0.3, 0.1, 2.9, 0, 0.05, 0, mat('ballast', 0x2a3546, { roughness: 1 }));
    for (const s of [-0.72, 0.72]) bx(g, len + 0.1, 0.16, 0.13, 0, 0.2, s, mat('rail', 0xaab4c2, { metalness: 0.8, roughness: 0.35 }));
    const strip = new THREE.Mesh(G.box, new THREE.MeshBasicMaterial({ color: KIND_COL[kind] || 0x56667c, transparent: true, opacity: kind === 'lead' ? 0.35 : 0.6 }));
    strip.scale.set(len, 0.02, 0.4); strip.position.set(0, 0.13, 0); g.add(strip); W.add(g);
    const n = Math.max(1, Math.floor(len / 1.15));
    for (let i = 0; i < n; i++) { const t = (i + 0.5) / n; this.sleepData.push({ x: a[0] + dx * t, z: a[1] + dz * t, a: ang }); }
  }

  _signal(W, s) {
    const g = new THREE.Group(); g.position.set(s.x, 0, s.z);
    bx(g, 0.18, 4.4, 0.18, 0, 2.2, 0, mat('pole', 0x5d6b7d)); bx(g, 0.5, 1.9, 0.5, 0, 4.5, 0, mat('shead', 0x0b1220));
    const lamps = {};
    [['RED', 0xe0463f, 5.15], ['YELLOW', 0xe0a82e, 4.5], ['GREEN', 0x3cc17b, 3.85]].forEach(([k, col, y]) => {
      const m = new THREE.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 0.06 });
      const sph = new THREE.Mesh(G.sph, m); sph.scale.set(0.3, 0.3, 0.3); sph.position.set(0, y, 0.3); g.add(sph); lamps[k] = m;
    });
    const lb = makeLabel(s.id, { w: 56, h: 34, scale: 0.022, font: '600 18px "Segoe UI", Arial' }); lb.position.set(0, 6.6, 0); g.add(lb);
    const lockM = new THREE.MeshBasicMaterial({ color: 0xc9534f, transparent: true, opacity: 0 });
    const ring = new THREE.Mesh(new THREE.TorusGeometry(1.2, 0.08, 8, 28), lockM); ring.rotation.x = Math.PI / 2; ring.position.y = 0.3; g.add(ring);
    W.add(g); this.sig[s.id] = { g, lamps, state: 'RED', lockM, blink: false };
    this.setSignal(s.id, 'RED');
  }

  // ---------- IoT и CCTV ----------
  _buildIot() {
    const grp = new THREE.Group(); this.iotGroup = grp; this.world.add(grp);
    const mk = (it, x, y, z, text) => {
      const m = new THREE.MeshBasicMaterial({ color: ST_COL.ok }); const s = new THREE.Mesh(G.sph, m); s.scale.set(1.0, 1.0, 1.0); s.position.set(x, y, z);
      s.userData.item = it; grp.add(s); this.pickables.push(s);
      bx(grp, 0.05, y - 0.3, 0.05, x, (y - 0.3) / 2, z, mat('stem', 0x5d6b7d));
      const lb = makeLabel(text, { w: 74, h: 30, scale: 0.02, font: '600 15px "Segoe UI", Arial', color: '#aab6c6' }); lb.position.set(x, y + 1.4, z); grp.add(lb);
      Object.assign(it, { mesh: s, mat: m, state: 'ok', since: Date.now() }); this.sensors.push(it);
    };
    for (const s of this.L.sensors) {
      if (s.type === 'switch') { const sw = this.sw[s.ref]; mk({ id: s.id, type: 'switch', ref: s.ref, x: sw.x, z: sw.z }, sw.x, 3.2, sw.z + (sw.side === 'L' ? 3.1 : -3.1), s.id); }
      else { const t = this.byIdx[s.track]; mk({ id: s.id, type: 'securing', track: s.track, x: t.x1 - 9, z: t.z }, t.x1 - 9, 3.6, t.z, s.id); this.sensors[this.sensors.length - 1].state = 'free'; }
    }
  }
  _buildCctv() {
    const grp = new THREE.Group(); this.cctvGroup = grp; this.world.add(grp);
    for (const c of this.L.cameras) {
      const g = new THREE.Group(); g.position.set(c.x, 0, c.z);
      bx(g, 0.3, c.y, 0.3, 0, c.y / 2, 0, mat('mast', 0x4a5666));
      const head = bx(g, 1.6, 0.9, 1.1, 0, c.y + 0.3, 0, mat('camh', 0x2c3a4c, { metalness: 0.4 }));
      const dir = new THREE.Vector3(c.tx - c.x, -c.y, c.tz - c.z); const len = dir.length();
      const cone = new THREE.Mesh(G.cone, new THREE.MeshBasicMaterial({ color: 0x5b8fc9, transparent: true, opacity: 0.10, side: THREE.DoubleSide, depthWrite: false }));
      cone.scale.set(len * 0.45, len, len * 0.45); cone.position.set(dir.x / 2, c.y + 0.3 + dir.y / 2, dir.z / 2);
      cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), dir.clone().normalize()); g.add(cone);
      head.userData.item = { id: c.id, type: 'camera', cam: c, state: 'ok' }; this.pickables.push(head);
      const lb = makeLabel(c.id, { w: 84, h: 32, scale: 0.024, font: '600 16px "Segoe UI", Arial', color: '#7fa9d6', border: '#3d5878' }); lb.position.set(0, c.y + 2.2, 0); g.add(lb);
      grp.add(g); this.cams.push({ ...c });
    }
  }
  setLayer(l) {
    this.layer = l;
    if (this.iotGroup) this.iotGroup.visible = l === 'iot';
    if (this.cctvGroup) this.cctvGroup.visible = l === 'cctv';
    if (l === 'iot') this._updateSensors(true);
  }
  _updateSensors(force) {
    const vehs = []; this.world.children.forEach(ch => { if (ch.userData.veh) vehs.push(ch.position); });
    for (const s of this.sensors) {
      let st = s.state;
      if (s.type === 'switch') {
        if (this.failedSw.has(s.ref)) st = 'fault';
        else { const sw = this.sw[s.ref]; st = vehs.some(p => Math.abs(p.x - sw.x) < 4.2 && Math.abs(p.z - sw.z) < 2.6) ? 'occupied' : 'ok'; }
      } else {
        const sh = Object.values(this.shoes).find(x => x.track === s.track && x.alert && !x.removing);
        const tr = this.trains.find(x => x.track === s.track && x.static);
        st = sh || (tr && tr.unsecured) ? 'alarm' : tr && tr.shoes ? 'secured' : 'free';
      }
      if (st !== s.state || force) { if (st !== s.state) s.since = Date.now(); s.state = st; s.mat.color.setHex(ST_COL[st]); }
    }
  }
  renderCamera(id, canvas) {
    const c = this.cams.find(x => x.id === id); if (!c || !this.L) return;
    const W = 640, H = 360;
    this._rt ||= new THREE.WebGLRenderTarget(W, H, { colorSpace: THREE.SRGBColorSpace }); this._cc ||= new THREE.PerspectiveCamera(62, W / H, 0.5, 900); this._buf ||= new Uint8Array(W * H * 4);
    this._cc.position.set(c.x, c.y + 0.4, c.z); this._cc.lookAt(c.tx, 0, c.tz);
    const vis = [this.iotGroup.visible, this.cctvGroup.visible]; this.iotGroup.visible = false; this.cctvGroup.visible = false;
    this.renderer.setRenderTarget(this._rt); this.renderer.render(this.scene, this._cc); this.renderer.setRenderTarget(null);
    this.iotGroup.visible = vis[0]; this.cctvGroup.visible = vis[1];
    this.renderer.readRenderTargetPixels(this._rt, 0, 0, W, H, this._buf);
    const ctx = canvas.getContext('2d'); if (canvas.width !== W) { canvas.width = W; canvas.height = H; }
    const img = ctx.createImageData(W, H);
    for (let y = 0; y < H; y++) img.data.set(this._buf.subarray((H - 1 - y) * W * 4, (H - y) * W * 4), y * W * 4);
    ctx.putImageData(img, 0, 0);
  }
  sensorInfo(it) { return it.type === 'camera' ? { id: it.id, type: 'camera', zone: it.cam.zone } : { id: it.id, type: it.type, state: it.state, ref: it.ref, track: it.track, since: it.since }; }

  // ---------- сигналы, стрелки, отказы ----------
  setSignal(id, state, blink = false) { const s = this.sig[id]; if (!s) return; s.state = state; s.blink = blink; for (const k in s.lamps) s.lamps[k].emissiveIntensity = k === state ? 2.6 : 0.06; }
  setSwitch(id, pos, locked = false) {
    const s = this.sw[id]; if (!s) return; s.pos = pos; s.locked = locked;
    s.blade.rotation.y = pos === 'R' ? (s.side === 'L' ? 0.5 : -0.5) : 0;
    const col = this.failedSw.has(id) || locked ? 0xc9534f : pos === 'R' ? 0x5b8fc9 : 0x4aa37a;
    s.lampM.color.setHex(col); s.lampM.emissive.setHex(col);
  }
  applyRoute(key) {
    const r = this.L.routes[key]; if (!r) return; this.activeRoutes.add(key);
    for (const k in r.switches) this.setSwitch(+k, r.switches[k]);
  }
  clearSwitches(side) {
    for (const id in this.sw) if (!side || this.sw[id].side === side) this.setSwitch(+id, 'N');
    for (const k of [...this.activeRoutes]) if (!side || (side === 'L' ? k[0] === 'A' : k[0] === 'D')) this.activeRoutes.delete(k);
  }
  flashSwitches(ids, ms = 5000) {
    ids.forEach(id => this.setSwitch(id, this.sw[id]?.pos || 'N', true));
    setTimeout(() => ids.forEach(id => this.alive && this.setSwitch(id, this.sw[id]?.pos || 'N', false)), ms);
  }
  flashSignal(id, ms = 4000) { const s = this.sig[id]; if (s) s.alertUntil = this.t + ms / 1000; }

  failSwitch(id, repairMin = 45) { this.failedSw.set(id, this.t + repairMin / SCALE); this.setSwitch(id, this.sw[id].pos); this._refreshTrackLabels(); }
  repairSwitch(id) { this.failedSw.delete(id); this.setSwitch(id, this.sw[id].pos); this._refreshTrackLabels(); }
  blockedTracks() {
    const out = new Set(this.extraBlocked ? this.extraBlocked.keys() : []);
    for (const id of this.failedSw.keys()) { const s = this.sw[id]; if (s.side === 'L') { if (s.head === 0) this.L.tracks.forEach(t => t.idx && out.add(t.idx)); else out.add(s.head); } }
    return out;
  }
  departureBlocked(ti) { for (const id of this.failedSw.keys()) { const s = this.sw[id]; if (s.side === 'R' && (s.head === 0 || s.head === ti)) return true; } return false; }
  _refreshTrackLabels() {
    const b = this.blockedTracks();
    for (const idx in this.trackLabels) {
      const l = this.trackLabels[idx], bad = b.has(+idx), txt = bad ? idx + ' ✕' : idx;
      relabel(l.a, txt, bad ? '#e07a74' : l.col, bad ? '#c9534f' : l.col); relabel(l.b, txt, bad ? '#e07a74' : l.col, bad ? '#c9534f' : l.col);
    }
  }
  blockTrack(idx, minSim) { this.extraBlocked.set(idx, this.t + minSim / SCALE); this._refreshTrackLabels(); this._falseMark(idx); }
  unblockTrack(idx) {
    this.extraBlocked.delete(idx); this._refreshTrackLabels();
    if (this.falseMarks && this.falseMarks[idx]) { this.world.remove(this.falseMarks[idx]); delete this.falseMarks[idx]; }
  }
  _falseMark(idx) {
    const t = this.byIdx[idx]; this.falseMarks = this.falseMarks || {};
    const m = new THREE.Mesh(G.box, new THREE.MeshBasicMaterial({ color: 0xc9534f, transparent: true, opacity: 0.3 })); m.scale.set(t.x1 - t.x0, 0.3, 2.6); m.position.set((t.x0 + t.x1) / 2, 0.3, t.z); this.world.add(m); this.falseMarks[idx] = m;
  }
  occupyStorage(minSim) {                      // отстой вагонов на сортировочных путях (переполнение)
    const so = this.L.tracks.filter(x => x.kind === 'sorting' && !this.resSort.has(x.idx)); const take = so.slice(0, Math.max(1, Math.ceil(this.L.tracks.filter(x => x.kind === 'sorting').length / 2)));
    const made = [];
    for (const t of take) {
      const tr = this.makeTrain(Math.min(this.maxWag, 10), 'gondola'); tr.track = t.idx; tr.static = true; tr.state = 'storage'; tr.storage = true; tr.xF0 = t.x1 - 10; tr.arrT = this.t - 40;
      this._static(this.wagonItems(tr), tr.xF0, t.z); this.resSort.add(t.idx); this.trains.push(tr); made.push(t.idx);
      const sh = [buildShoe(), buildShoe()]; sh[0].position.set(tr.xF0 - 5.2, 0, t.z); sh[1].position.set(tr.xF0 - 8.5, 0, t.z); sh.forEach(x => this.world.add(x)); tr.shoes = sh;
    }
    this.storageUntil = this.t + minSim / SCALE; return made;
  }
  clearStorage() {
    for (const tr of this.trains.filter(x => x.storage)) { tr.wagons.forEach(w => this.world.remove(w)); if (tr.shoes) tr.shoes.forEach(x => this.world.remove(x)); this.resSort.delete(tr.track); }
    this.trains = this.trains.filter(x => !x.storage); this.storageUntil = 0;
  }
  setProcMult(m, minSim) { this.procMult = m; this.procUntil = this.t + minSim / SCALE; }
  setRouteExtra(sec, minSim) { this.routeExtra = sec; this.routeUntil = this.t + minSim / SCALE; }
  burstArrivals(n) { this.burstLeft += n; this.nextArrival = 1; }
  removeLocos(n, minSim) { this.locoLost = n; this.lostUntil = this.t + minSim / SCALE; this.syncLocos(); }
  restoreLocos() { this.locoLost = 0; this.lostUntil = 0; this.syncLocos(); }
  forceUnsecuredTrain() {
    const ti = this.pickTrack('sorting') ?? this.pickTrack('reception'); if (ti == null) return null;
    const t = this.byIdx[ti]; const n = Math.min(this.maxWag, 8);
    const tr = this.makeTrain(n); tr.track = ti; tr.static = true; tr.state = 'ready'; tr.xF0 = t.x1 - 34; tr.arrT = this.t - 25; tr.unsecured = true; tr.creep = true; tr.hold = true;
    this._static(this.wagonItems(tr), tr.xF0, t.z); this.trains.push(tr); (t.kind === 'sorting' ? this.resSort : this.resRecv).add(ti); return tr;
  }
  secureTrain(tr) {
    tr.creep = false; tr.unsecured = false; const t = this.byIdx[tr.track];
    const sh = [buildShoe(), buildShoe()]; sh[0].position.set(tr.xF0 - 5.2, 0, t.z); sh[1].position.set(tr.xF0 - 8.5, 0, t.z); sh.forEach(x => this.world.add(x)); tr.shoes = sh;
  }
  setCrewAbsent(n, minSim) { this.crewsAbsent = n; this.absentUntil = this.t + minSim / SCALE; }
  endCrewAbsent() { this.crewsAbsent = 0; this.absentUntil = 0; }
  delayArrivals(minSim) { this.nextArrival += minSim / SCALE; }
  addReserveLoco(minSim) { this.locoExtra = 1; this.extraUntil = this.t + minSim / SCALE; this.syncLocos(); }

  // ---------- башмаки ----------
  addShoe(id, track, alert = true) {
    if (this.shoes[id]) return; const t = this.byIdx[track]; if (!t) return;
    const tr = this.trains.find(x => x.track === track && x.static);
    const x = tr ? tr.xF0 - WL : (t.x0 + t.x1) / 2;
    const g = buildShoe(); g.position.set(x - 0.9, 0, t.z); this.world.add(g);
    const lb = makeLabel(`Башмак №${id}`, { color: '#e8a36a', border: '#c9534f', w: 130, h: 36, scale: 0.03, font: '600 18px "Segoe UI", Arial' }); lb.position.set(x - 0.9, 3.4, t.z); this.world.add(lb);
    this.shoes[id] = { g, lb, track, alert, born: this.t };
  }
  removeShoe(id) { const s = this.shoes[id]; if (s) s.removing = this.t; }

  // ---------- параметры ----------
  setParams(p) { Object.assign(this.params, p); this.syncLocos(); }
  setOptimized(on) { this.optimized = !!on; }
  locoTarget() { return Math.max(1, Math.min(5, this.params.locos + this.locoExtra - (this.locoLost || 0))); }
  crewsEff() { return Math.max(0, this.params.crews - this.crewsAbsent); }
  syncLocos() {
    const want = this.locoTarget();
    while (this.locoTotal < want) {
      const mesh = buildLoco('shunt'); const lo = { mesh, busy: false, id: ++this.locoTotal, x: 0 };
      const lb = makeLabel('ЧМЭ3-' + lo.id, { color: '#cf9a3a', border: '#8a6a2a', w: 100, h: 32, scale: 0.026, font: '600 17px "Segoe UI", Arial' }); lb.position.set(0, 4.4, 0); mesh.add(lb);
      this.world.add(mesh); this.locoPool.push(lo); this._parkLocos();
    }
    while (this.locoTotal > want && this.locoPool.length) { const lo = this.locoPool.pop(); this.world.remove(lo.mesh); this.locoTotal--; this._parkLocos(); }
  }
  _parkX(k) { return this.L.depot.x0 + 4 + 5 * k; }
  _parkLocos() { this.locoPool.forEach((lo, k) => { lo.x = this._parkX(k); lo.mesh.position.set(lo.x, 0, this.L.depot.z); lo.mesh.rotation.y = Math.PI; }); }

  // ---------- перемещения ----------
  move(items, path, s0, s1, speed, opts = {}) { return new Promise(resolve => { this.tweens.push({ items, path, s: s0, s0, s1, speed, resolve, epoch: this.epoch, loco: opts.loco }); }); }
  sleep(sec) { return new Promise(resolve => this.waiters.push({ t: this.t + sec, resolve, epoch: this.epoch, cond: null })); }
  waitUntil(cond) { return new Promise(resolve => this.waiters.push({ cond, resolve, epoch: this.epoch })); }
  _applyItems(items, path, s) { for (const it of items) { const p = path.pose(s - it.off); it.obj.position.set(p.x, 0, p.z); it.obj.rotation.y = -p.a; } }
  _static(items, xF0, z) { for (const it of items) { it.obj.position.set(xF0 - it.off, 0, z); it.obj.rotation.y = 0; } }

  makeTrain(n, forceType) {
    const wagons = []; for (let i = 0; i < n; i++) { const w = buildWagon(forceType); this.world.add(w); wagons.push(w); }
    return { id: ++this.trainSeq, n, wagons, track: null, static: false, state: 'new', xF0: 0, hold: false, arrT: this.t };
  }
  wagonItems(tr, base = 0) { return tr.wagons.map((w, k) => ({ obj: w, off: base + WL * k })); }

  spawnStatic(trackIdx, state) {
    const t = this.byIdx[trackIdx]; const n = Math.min(this.maxWag, Math.floor(rnd(6, 10)));
    const tr = this.makeTrain(n); tr.track = trackIdx; tr.static = true; tr.state = state; tr.arrT = this.t - rnd(2, 12);
    tr.xF0 = t.x1 - (state === 'sorted' ? 10 : 14.2); this._static(this.wagonItems(tr), tr.xF0, t.z);
    (state === 'wait' ? this.resRecv : this.resSort).add(trackIdx); this.trains.push(tr);
    if (state === 'wait') this.process(tr); else this.depart(tr, 3 + Math.random() * 3);
  }

  hasFree(kind) { const set = kind === 'reception' ? this.resRecv : this.resSort, b = this.blockedTracks(); return this.L.tracks.some(t => t.kind === kind && !set.has(t.idx) && !b.has(t.idx)); }
  pickTrack(kind, nearTo) {
    const set = kind === 'reception' ? this.resRecv : this.resSort, b = this.blockedTracks();
    const c = this.L.tracks.filter(t => t.kind === kind && !set.has(t.idx) && !b.has(t.idx)); if (!c.length) return null;
    if (nearTo != null && this.optimized) c.sort((a, z) => Math.abs(a.idx - nearTo) - Math.abs(z.idx - nearTo)); else c.sort(() => Math.random() - 0.5);
    return c[0].idx;
  }

  // ---------- жизненный цикл состава ----------
  async arrive() {
    const ep = this.epoch; const ti = this.pickTrack('reception'); if (ti == null) return;
    this.resRecv.add(ti);
    const t = this.byIdx[ti], n = Math.floor(rnd(6, this.maxWag + 1));
    const tr = this.makeTrain(n); tr.track = ti; tr.state = 'arriving'; this.trains.push(tr);
    const loco = buildLoco('line'); this.world.add(loco);
    const stopFront = t.x1 - 10;
    const path = new Path([[-MAIN_VIS - 40, 0], ...this.L.left_lead.slice(0, ti + 1), [stopFront, t.z]]);
    const items = [{ obj: loco, off: 0 }, ...this.wagonItems(tr, FRONT_GAP)];
    this._applyItems(items, path, 0);
    await this.neckMutex.acquire('A' + ti); if (ep !== this.epoch) return;
    if (this.routeExtra) { await this.sleep(this.routeExtra); if (ep !== this.epoch) return; }
    this.setSignal('Н', 'GREEN'); this.applyRoute('A' + ti);
    await this.move(items, path, 0, path.len, 58); if (ep !== this.epoch) return;
    this.setSignal('Н', 'RED'); this.clearSwitches('L'); this.neckMutex.release();
    tr.xF0 = stopFront - FRONT_GAP; tr.static = true; this._static(this.wagonItems(tr), tr.xF0, t.z);
    const rp = new Path([[stopFront, t.z], [t.x1, t.z], ...this.L.right_lead.slice(0, ti).reverse(), [MAIN_VIS + 60, 0]]);
    this.process(tr);
    await this.rightMutex.acquire('D' + ti); if (ep !== this.epoch) return;
    this.applyRoute('D' + ti);
    await this.move([{ obj: loco, off: 0 }], rp, 0, rp.len, 58); if (ep !== this.epoch) return;
    this.clearSwitches('R'); this.rightMutex.release(); this.world.remove(loco);
  }

  _missing(ti) {                          // чего не хватает составу для маневровой операции
    if (this.blockedTracks().has(ti)) return 'ROUTE';
    if (!this.hasFree('sorting')) return 'TRACK';
    if (this.locoPool.length === 0) return 'LOCO';
    if (this.crewsEff() - this.crewBusy <= 0) return 'CREW';
    return null;
  }

  async process(tr) {
    const ep = this.epoch; tr.state = 'waiting'; tr.waitT0 = this.t; this.waitCrewQ++;
    const ti = tr.track, t = this.byIdx[ti];
    let lo = null, bi = null;
    while (!lo) {
      const ok = await this.waitUntil(() => this._missing(ti) === null);
      if (!ok || ep !== this.epoch) return;
      if (this._missing(ti) !== null) continue;
      bi = this.pickTrack('sorting', ti); if (bi == null) continue;
      lo = this.locoPool.pop();
    }
    this.waitCrewQ--; this.resSort.add(bi); lo.busy = true; this.locoBusy++; this.crewBusy++;
    tr.state = 'shunt'; tr.waitT0 = null;
    const b = this.byIdx[bi], n = tr.n;
    const xTail = tr.xF0 - WL * (n - 1), xc = xTail - LOCO_GAP;
    const dep = this.L.depot, px = lo.x;
    const p0 = new Path([[px, dep.z], [dep.x1, dep.z], [dep.merge_x, 0], ...this.L.left_lead.slice(0, ti + 1), [xc, t.z]]);
    lo.mesh.rotation.y = Math.PI;
    tr.routeT0 = this.t; tr.routeWait = true;
    await this.neckMutex.acquire('A' + ti); if (ep !== this.epoch) return; tr.routeWait = false;
    if (this.routeExtra) { await this.sleep(this.routeExtra); if (ep !== this.epoch) return; }
    this.applyRoute('A' + ti);
    await this.move([{ obj: lo.mesh, off: 0 }], p0, 0, p0.len, 62, { loco: true }); if (ep !== this.epoch) return;
    await this.sleep(0.5); if (ep !== this.epoch) return;
    const m = Math.min(ti, bi), stepLen = Math.hypot(this.L.dx, this.L.dz);
    const p1 = new Path([[xc, t.z], [t.x0, t.z], ...this.L.left_lead.slice(0, ti).reverse(), [-MAIN_VIS + 10, 0]]);
    const pullItems = [{ obj: lo.mesh, off: 0 }, ...tr.wagons.map((w, k) => ({ obj: w, off: LOCO_GAP + WL * (n - 1 - k) }))];
    const tailOff = LOCO_GAP + WL * (n - 1) + 1.6;
    const dPm = (xc - t.x0) + (ti - m) * stepLen;
    const sPull = Math.min(p1.len - 2, dPm + tailOff + 3);
    tr.static = false;
    await this.move(pullItems, p1, 0, sPull, 38, { loco: true }); if (ep !== this.epoch) return;
    await this.sleep(0.35); if (ep !== this.epoch) return;
    const xFin = b.x1 - 10;
    const p2 = new Path([[-MAIN_VIS + 10, 0], ...this.L.left_lead.slice(0, bi + 1), [xFin, b.z]]);
    const dPi = (xc - t.x0); const e = (sPull - LOCO_GAP - WL * (n - 1)) - dPi;
    const uPi = (MAIN_VIS - 10 + this.L.left_lead[0][0]) + ti * stepLen; const s2 = uPi - e;
    const pushItems = [...tr.wagons.map((w, k) => ({ obj: w, off: WL * k })), { obj: lo.mesh, off: WL * (n - 1) + LOCO_GAP }];
    this.clearSwitches('L'); this.applyRoute('A' + bi); this.resRecv.delete(ti);
    await this.move(pushItems, p2, s2, p2.len, 38, { loco: true }); if (ep !== this.epoch) return;
    this.neckMutex.release(); this.clearSwitches('L');
    tr.track = bi; tr.xF0 = xFin; tr.static = true; tr.state = 'processing';
    this._static(this.wagonItems(tr), tr.xF0, b.z);
    const xl = xFin - WL * (n - 1) - LOCO_GAP; lo.mesh.position.set(xl, 0, b.z); lo.mesh.rotation.y = 0;
    const sh = [buildShoe(), buildShoe()]; sh[0].position.set(xFin - 5.2, 0, b.z); sh[1].position.set(xFin - 8.5, 0, b.z); sh.forEach(s => this.world.add(s)); tr.shoes = sh;
    const dur = Math.max(3, this.params.proc * 0.42) * (this.optimized ? 0.9 : 1) * (this.procMult || 1);
    const bar = this._bar(); bar.group.position.set(xFin - WL * (n / 2), 3.8, b.z); this.world.add(bar.group); tr.bar = bar;
    let el = 0;
    while (el < dur) { await this.sleep(0.1); if (ep !== this.epoch) return; el += 0.1; bar.set(el / dur); lo.mesh.position.x = xl + Math.sin(el * 2.2) * 1.2; }
    lo.mesh.position.x = xl; this.world.remove(bar.group); tr.bar = null;
    tr.routeT0 = this.t; tr.routeWait = true;
    await this.neckMutex.acquire('A' + bi); if (ep !== this.epoch) return; tr.routeWait = false;
    this.applyRoute('A' + bi);
    const spot = this._parkX(this.locoPool.length);
    const p3 = new Path([[xl, b.z], [b.x0, b.z], ...this.L.left_lead.slice(0, bi).reverse(), [dep.merge_x, 0], [dep.x1, dep.z], [spot, dep.z]]);
    await this.move([{ obj: lo.mesh, off: 0 }], p3, 0, p3.len, 62, { loco: true }); if (ep !== this.epoch) return;
    this.clearSwitches('L'); this.neckMutex.release();
    lo.x = spot; lo.busy = false; this.locoBusy--; this.crewBusy--; lo.mesh.rotation.y = Math.PI; this.locoPool.push(lo);
    this.syncLocos();
    this.depart(tr, 0.3);
  }

  _bar() {
    const g = new THREE.Group();
    bx(g, 8, 0.5, 0.2, 0, 0, 0, new THREE.MeshBasicMaterial({ color: 0x0b1522 }));
    const fm = new THREE.MeshBasicMaterial({ color: 0x5b8fc9 }); const f = bx(g, 8, 0.36, 0.24, 0, 0, 0.02, fm);
    const o = { group: g, set: (k) => { k = Math.min(1, Math.max(0.001, k)); f.scale.x = 8 * k; f.position.x = -4 + 4 * k; fm.color.setHex(k > 0.85 ? 0x4aa37a : 0x5b8fc9); } };
    o.set(0.001); return o;
  }

  async depart(tr, delay = 0.5) {
    const ep = this.epoch; const ti = tr.track, t = this.byIdx[ti];
    if (tr.departing) return; tr.departing = true; tr.state = 'ready';
    if (delay) await this.sleep(delay); if (ep !== this.epoch) return;
    while (tr.hold) { const ok = await this.waitUntil(() => !tr.hold); if (!ok || ep !== this.epoch) return; }
    let blockedOnce = false;
    for (;;) {
      this.rfid[ti] && (this.rfid[ti].scan = this.t + 0.8);
      let r = { allowed: true };
      try { r = this.hooks.departureCheck ? await this.hooks.departureCheck(ti) : r; } catch (e) { r = { allowed: true }; }
      if (ep !== this.epoch) return;
      if (r.allowed) break;
      if (!blockedOnce) { blockedOnce = true; tr.blocked = true; this.setSignal('Ч' + ti, 'RED', true); this.hooks.onBlocked && this.hooks.onBlocked(ti, r); }
      await this.sleep(0.9); if (ep !== this.epoch) return;
    }
    if (blockedOnce) { tr.blocked = false; this.hooks.onUnblocked && this.hooks.onUnblocked(ti); }
    if (tr.shoes) { tr.shoes.forEach(s => this.world.remove(s)); tr.shoes = null; }
    tr.routeT0 = this.t; tr.routeWait = true;
    await this.waitUntil(() => !this.departureBlocked(ti)); if (ep !== this.epoch) return;
    await this.rightMutex.acquire('D' + ti); if (ep !== this.epoch) return; tr.routeWait = false;
    if (this.routeExtra) { await this.sleep(this.routeExtra); if (ep !== this.epoch) return; }
    this.setSignal('Ч' + ti, 'GREEN'); this.applyRoute('D' + ti);
    const loco = buildLoco('line'); this.world.add(loco);
    const x0 = tr.xF0 + FRONT_GAP; loco.position.set(x0, 0, t.z); loco.scale.setScalar(0.01);
    for (let k = 0; k < 8; k++) { loco.scale.setScalar(0.01 + 0.99 * (k + 1) / 8); await this.sleep(0.05); if (ep !== this.epoch) return; }
    const path = new Path([[x0, t.z], [t.x1, t.z], ...this.L.right_lead.slice(0, ti).reverse(), [MAIN_VIS + 70, 0]]);
    tr.static = false;
    await this.move([{ obj: loco, off: 0 }, ...this.wagonItems(tr, FRONT_GAP)], path, 0, path.len + WL * tr.n + 12, 55); if (ep !== this.epoch) return;
    this.setSignal('Ч' + ti, 'RED'); this.clearSwitches('R'); this.rightMutex.release();
    this.world.remove(loco); tr.wagons.forEach(w => this.world.remove(w));
    this.trains = this.trains.filter(x => x !== tr); this.resSort.delete(ti); this.resRecv.delete(ti); this.stats.done++;
    this.departedQ.push({ id: tr.id, arr_t: tr.arrT, dep_t: this.t });
  }

  forceReadyTrain() {
    let tr = null; const ti = this.pickTrack('sorting') ?? this.pickTrack('reception');
    if (ti != null) {
      const t = this.byIdx[ti]; const n = Math.min(this.maxWag, 8);
      tr = this.makeTrain(n); tr.track = ti; tr.static = true; tr.state = 'ready'; tr.xF0 = t.x1 - 10; tr.arrT = this.t - 20;
      this._static(this.wagonItems(tr), tr.xF0, t.z); this.trains.push(tr); (t.kind === 'sorting' ? this.resSort : this.resRecv).add(ti);
    } else tr = this.trains.find(x => x.static && x.state === 'processing');
    if (tr) tr.hold = true; return tr;
  }
  releaseHold(tr) { tr.hold = false; if (tr.state === 'ready') this.depart(tr, 0.2); }

  _nextInterval() {                           // режим прибытия задаётся профилем станции
    const mean = 60 / this.params.lambda, mode = this.profile.arrivals || 'poisson';
    if (this.burstLeft > 0) { this.burstLeft--; return 3.5; }
    if (mode === 'steady') return mean * (0.9 + Math.random() * 0.2);
    if (mode === 'bunched') { this.bunchNext = !this.bunchNext; return this.bunchNext ? 3.5 : mean * 1.8; }
    if (mode === 'burst') { if (--this.burstCountdown <= 0) { this.burstCountdown = 3; this.burstLeft = 2; return mean * 1.4; } return mean * 1.1; }
    return Math.max(3, -Math.log(1 - Math.random()) * mean);
  }

  // ---------- телеметрия для бэкенда ----------
  telemetry() {
    if (!this.L) return null;
    const trains = [];
    for (const tr of this.trains) {
      let reason = null, t0 = null;
      if (tr.state === 'waiting') { reason = this._missing(tr.track); t0 = tr.waitT0; }
      else if (tr.routeWait) { reason = 'ROUTE'; t0 = tr.routeT0; }
      trains.push({ id: tr.id, state: tr.state, track: tr.track, arr_t: tr.arrT, n: tr.n, reason, wait_t0: t0 ?? this.t });
    }
    if (this.outside) trains.push({ id: 'EXT', state: 'outside', track: 0, arr_t: this.outsideSince, n: 8, reason: 'TRACK', wait_t0: this.outsideSince });
    const crewsTot = this.crewsEff();
    return {
      t: +this.t.toFixed(2),
      resources: {
        recv_total: this.L.tracks.filter(t => t.kind === 'reception').length, recv_busy: this.resRecv.size,
        sort_total: this.L.tracks.filter(t => t.kind === 'sorting').length, sort_busy: this.resSort.size,
        blocked_tracks: [...this.blockedTracks()], failed_switches: [...this.failedSw.keys()],
        locos_total: this.locoTotal, locos_free: this.locoPool.length,
        crews_total: crewsTot, crews_free: Math.max(0, crewsTot - this.crewBusy), crews_absent: this.crewsAbsent,
        active_routes: [this.neckMutex.owner, this.rightMutex.owner].filter(Boolean),
        requested_routes: [...this.neckMutex.pending(), ...this.rightMutex.pending()],
      },
      trains, departed: this.departedQ.splice(0),
    };
  }

  // ---------- главный цикл ----------
  _anim() {
    const dt = Math.min(this.clock.getDelta(), 0.05);
    if (this.paused) { this._raf = requestAnimationFrame(this._anim); return; }
    this.t += dt; this._updateCam(dt);
    if (this.L) this._tick(dt);
    this.renderer.render(this.scene, this.camera);
    this._raf = requestAnimationFrame(this._anim);
  }

  _tick(dt) {
    for (let i = this.tweens.length - 1; i >= 0; i--) {
      const tw = this.tweens[i]; if (tw.epoch !== this.epoch) { this.tweens.splice(i, 1); continue; }
      const total = Math.abs(tw.s1 - tw.s0), done = Math.abs(tw.s - tw.s0), left = total - done;
      const f = Math.max(0.18, Math.min(1, done / 8 + 0.18, left / 9 + 0.12));
      const step = Math.min(tw.speed * f * dt * (this.optimized ? 1.08 : 1), left);
      tw.s += Math.sign(tw.s1 - tw.s0) * step; if (tw.loco) this.stats.km += step * U / 1000;
      this._applyItems(tw.items, tw.path, tw.s);
      if (left - step <= 1e-6) { this.tweens.splice(i, 1); tw.resolve(true); }
    }
    for (let i = 0; i < this.waiters.length; i++) {
      const w = this.waiters[i]; if (w.epoch !== this.epoch) { this.waiters.splice(i--, 1); continue; }
      if (w.cond ? w.cond() : this.t >= w.t) { this.waiters.splice(i--, 1); w.resolve(true); }
    }
    if (this.procMult !== 1 && this.t >= this.procUntil) this.procMult = 1;
    if (this.routeExtra && this.t >= this.routeUntil) this.routeExtra = 0;
    if (this.locoLost && this.t >= this.lostUntil) this.restoreLocos();
    if (this.storageUntil && this.t >= this.storageUntil) { this.clearStorage(); this.hooks.onRecovered && this.hooks.onRecovered('storage'); }
    for (const [idx, until] of [...this.extraBlocked]) if (this.t >= until) { this.unblockTrack(idx); this.hooks.onRecovered && this.hooks.onRecovered('track', idx); }
    for (const tr of this.trains) if (tr.creep) {                       // незакреплённый состав медленно «уходит» по уклону
      const t = this.byIdx[tr.track]; tr.xF0 = Math.min(t.x1 - 6, tr.xF0 + 0.7 * dt); this._static(this.wagonItems(tr), tr.xF0, t.z);
    }
    if (this.crewsAbsent && this.t >= this.absentUntil) { this.crewsAbsent = 0; this.hooks.onRecovered && this.hooks.onRecovered('crew'); }
    if (this.locoExtra && this.t >= this.extraUntil) { this.locoExtra = 0; this.syncLocos(); }
    for (const [id, until] of [...this.failedSw]) if (this.t >= until) { this.repairSwitch(id); this.hooks.onRecovered && this.hooks.onRecovered('switch', id); }
    this.nextArrival -= dt;
    if (this.nextArrival <= 0) {
      const room = this.trains.length < this.n - 1 && this.hasFree('reception');
      if (room && !this.neckMutex.locked) { this.arrive(); this.nextArrival = this._nextInterval(); this.outside = 0; }
      else { if (!room) { if (!this.outside) this.outsideSince = this.t; this.outside = 1; } this.nextArrival = 0.5; }
    }
    const T = this.t;
    for (const id in this.sig) { const s = this.sig[id]; const alert = (s.alertUntil && T < s.alertUntil) || s.blink; s.lockM.opacity = alert ? 0.3 + 0.3 * Math.sin(T * 8) : 0; if (s.blink) s.lamps.RED.emissiveIntensity = 1.2 + 1.5 * Math.sin(T * 8); }
    this.rfid.forEach(r => { if (!r) return; const on = r.scan && T < r.scan; r.lm.emissiveIntensity = on ? 2.4 : 0.5; r.lm.color.setHex(on ? 0xe8d28a : 0x5b8fc9); r.lm.emissive.setHex(on ? 0xe8d28a : 0x5b8fc9); });
    for (const id in this.shoes) {
      const s = this.shoes[id];
      if (s.removing) { const k = (T - s.removing) / 0.9; s.g.position.y = k * 4; s.lb.position.y = 3.4 + k * 4; if (k >= 1) { this.world.remove(s.g); this.world.remove(s.lb); delete this.shoes[id]; } }
    }
    this._statT += dt; this._sensT += dt;
    if (this.layer === 'iot' && this._sensT > 0.4) { this._sensT = 0; this._updateSensors(false); this.hooks.onSensors && this.hooks.onSensors(); }
    if (this._statT > 0.4) {
      this._statT = 0;
      this.stats.trains = this.trains.length; this.stats.locos = this.locoBusy; this.stats.queue = this.waitCrewQ + this.outside;
      this.hooks.onStats && this.hooks.onStats({ ...this.stats });
    }
  }

  dispose() { this.alive = false; this.L = null; cancelAnimationFrame(this._raf); this.ro.disconnect(); this.renderer.dispose(); this.renderer.domElement.remove(); }
}
