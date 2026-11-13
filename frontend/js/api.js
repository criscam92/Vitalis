"use strict";

/* Cliente REST + streaming WebSocket (Rol C) */

const API = {
  async request(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload = await res.json();
        detail = typeof payload.detail === "string"
          ? payload.detail
          : JSON.stringify(payload.detail);
      } catch (e) { /* sin cuerpo JSON */ }
      throw new Error(detail);
    }
    return res.json();
  },
  start(body) { return this.request("/api/sim/start", { method: "POST", body }); },
  pause() { return this.request("/api/sim/pause", { method: "POST" }); },
  resume() { return this.request("/api/sim/resume", { method: "POST" }); },
  reset(body) { return this.request("/api/sim/reset", { method: "POST", body }); },
  step(body) { return this.request("/api/sim/step", { method: "POST", body }); },
  patchParams(body) { return this.request("/api/params", { method: "PATCH", body }); },
  state() { return this.request("/api/state"); },
  datasetUrl() { return "/api/dataset"; },
  async downloadDataset() {
    const res = await fetch(this.datasetUrl());
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload = await res.json();
        detail = typeof payload.detail === "string"
          ? payload.detail
          : JSON.stringify(payload.detail);
      } catch (e) { /* sin cuerpo JSON */ }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "vitalis_llamadas.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};

const Realtime = {
  _ws: null,
  _retry: 0,
  _timer: null,
  onState: null,
  onStatus: null,

  /** ¿El WebSocket está abierto? El respaldo REST solo se usa si no lo está. */
  isOpen() {
    return this._ws !== null && this._ws.readyState === WebSocket.OPEN;
  },

  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/sim`;
    try {
      this._ws = new WebSocket(url);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }
    this._ws.onopen = () => {
      this._retry = 0;
      if (this.onStatus) this.onStatus(true);
    };
    this._ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "state" && this.onState) this.onState(msg.data);
      } catch (e) {}
    };
    this._ws.onclose = () => {
      if (this.onStatus) this.onStatus(false);
      this._scheduleReconnect();
    };
    this._ws.onerror = () => {
      try { this._ws.close(); } catch (e) { /* ya cerrado */ }
    };
  },

  _scheduleReconnect() {
    this._retry += 1;
    const delay = Math.min(1000 * this._retry, 8000);
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this.connect(), delay);
  },
};
