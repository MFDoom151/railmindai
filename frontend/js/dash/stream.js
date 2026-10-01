// Клиент онлайн-потока: WebSocket (основной) → SSE (запасной), reconnect с экспоненциальной задержкой и джиттером,
// сторожевой таймер «данные устарели / нет связи», измерение частоты кадров и RTT.
export class StationStream {
  constructor({ onMessage, onStatus }) {
    this.onMessage = onMessage; this.onStatus = onStatus || (() => {});
    this.station = null; this.ws = null; this.es = null; this.attempt = 0; this.fails = 0; this.transport = 'ws';
    this.timer = null; this.watch = null; this.pinger = null; this.closed = true; this.lastMsg = 0; this.rtt = null;
    this.frames = []; this.hz = 0; this.state = 'connecting'; this.retryAt = 0; this.countdown = null;
  }

  connect(station) {
    this.close(true); this.station = station; this.closed = false; this.attempt = 0; this.fails = 0; this.transport = 'ws';
    this._open();
    this.watch = setInterval(() => this._watchdog(), 700);
  }

  close(keepStatus) {
    this.closed = true; clearTimeout(this.timer); clearInterval(this.watch); clearInterval(this.pinger); clearInterval(this.countdown);
    if (this.ws) { try { this.ws.onclose = null; this.ws.close(); } catch (e) {} this.ws = null; }
    if (this.es) { try { this.es.close(); } catch (e) {} this.es = null; }
    if (!keepStatus) this._status('offline');
  }

  _status(state, extra = {}) {
    this.state = state;
    this.onStatus({ state, attempt: this.attempt, transport: this.transport, hz: this.hz, rtt: this.rtt, retryIn: Math.max(0, Math.ceil((this.retryAt - Date.now()) / 1000)), ...extra });
  }

  _open() {
    if (this.closed) return;
    this._status('connecting');
    const st = encodeURIComponent(this.station);
    if (this.transport === 'ws') {
      let ws;
      try { ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/v1/stream?station=${st}&backfill=900`); } catch (e) { return this._failed(); }
      this.ws = ws;
      ws.onopen = () => { this._onOpen(); this.pinger = setInterval(() => { try { ws.send(JSON.stringify({ cmd: 'ping', t: performance.now() })); } catch (e) {} }, 2000); };
      ws.onmessage = (e) => this._msg(e.data);
      ws.onclose = () => { clearInterval(this.pinger); if (!this.closed) this._failed(); };
      ws.onerror = () => {};
    } else {
      const es = new EventSource(`/api/v1/stations/${st}/stream?backfill=900`);
      this.es = es;
      es.onopen = () => this._onOpen();
      es.onmessage = (e) => this._msg(e.data);
      es.onerror = () => { es.close(); this.es = null; if (!this.closed) this._failed(); };
    }
  }

  _onOpen() { this.attempt = 0; this.lastMsg = Date.now(); this._status('online'); }

  _msg(data) {
    const now = Date.now(); this.lastMsg = now;
    let m; try { m = JSON.parse(data); } catch (e) { return; }
    if (m.type === 'pong') { this.rtt = Math.round(performance.now() - m.t); return; }
    if (m.type === 'hb') { return; }
    if (m.type === 'state') {
      this.frames.push(now); while (this.frames.length && now - this.frames[0] > 4000) this.frames.shift();
      this.hz = this.frames.length > 1 ? (this.frames.length - 1) / ((now - this.frames[0]) / 1000) : 0;
    }
    if (this.state !== 'online') this._status('online');
    this.onMessage(m);
  }

  _failed() {
    if (this.closed) return;
    clearInterval(this.pinger);
    this.fails++; this.attempt++;
    if (this.transport === 'ws' && this.fails >= 3) { this.transport = 'sse'; this.fails = 0; }       // WS недоступен → SSE
    const delay = Math.min(15000, 1000 * 2 ** Math.min(this.attempt - 1, 4)) * (0.8 + Math.random() * 0.4);
    this.retryAt = Date.now() + delay;
    this._status('offline');
    clearInterval(this.countdown); this.countdown = setInterval(() => this._status('offline'), 500);
    clearTimeout(this.timer); this.timer = setTimeout(() => { clearInterval(this.countdown); this._open(); }, delay);
  }

  _watchdog() {
    if (this.closed || this.state === 'offline' || this.state === 'connecting') return;
    const age = Date.now() - this.lastMsg;
    if (age > 9000) { if (this.ws) { try { this.ws.close(); } catch (e) {} } if (this.es) { this.es.close(); this.es = null; this._failed(); } }
    else if (age > 3200 && this.state !== 'stale') this._status('stale');
    else if (age <= 3200 && this.state === 'stale') this._status('online');
    else if (this.state === 'online') this._status('online');            // обновляем Гц и RTT в индикаторе
  }
}
