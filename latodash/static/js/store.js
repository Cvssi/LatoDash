// Client-side state fed by the WebSocket. Views subscribe to named events.

const LIMITS = { metrics: 900, logs: 6000, generations: 3000, apiCalls: 3000 };

class Store {
  constructor() {
    this.config = null;
    this.state = null;
    this.metrics = [];
    this.logs = [];
    this.generations = [];
    this.apiCalls = [];
    this.socketUp = false;
    this.serverOffset = 0; // server clock - local clock (s)
    this._listeners = new Map();
  }

  on(event, fn) {
    if (!this._listeners.has(event)) this._listeners.set(event, new Set());
    this._listeners.get(event).add(fn);
    return () => this._listeners.get(event).delete(fn);
  }

  emit(event, payload) {
    for (const fn of this._listeners.get(event) || []) {
      try { fn(payload); } catch (err) { console.error(`listener for ${event} failed`, err); }
    }
  }

  serverNow() { return Date.now() / 1000 + this.serverOffset; }

  _push(arr, item, key) {
    arr.push(item);
    if (arr.length > LIMITS[key]) arr.splice(0, arr.length - LIMITS[key]);
  }

  _setState(state) {
    this.state = state;
    if (state?.server_time) this.serverOffset = state.server_time - Date.now() / 1000;
  }

  apply(msg) {
    switch (msg.type) {
      case "snapshot": {
        const d = msg.data;
        this.config = d.config;
        this._setState(d.state);
        this.metrics = d.metrics || [];
        this.logs = d.logs || [];
        this.generations = d.generations || [];
        this.apiCalls = d.api_calls || [];
        this.emit("snapshot", d);
        this.emit("state", this.state);
        break;
      }
      case "metrics": this._push(this.metrics, msg.data, "metrics"); this.emit("metrics", msg.data); break;
      case "state": this._setState(msg.data); this.emit("state", msg.data); break;
      case "log": this._push(this.logs, msg.data, "logs"); this.emit("log", msg.data); break;
      case "gen": this._push(this.generations, msg.data, "generations"); this.emit("gen", msg.data); break;
      case "gen_update": {
        const g = this.generations.findLast((x) => x.task === msg.data.task);
        if (g) Object.assign(g, msg.data);
        this.emit("gen", msg.data);
        break;
      }
      case "api": this._push(this.apiCalls, msg.data, "apiCalls"); this.emit("api", msg.data); break;
      case "link":
        if (this.state) this.state.link = msg.data;
        this.emit("link", msg.data);
        break;
      default: break;
    }
  }
}

export const store = new Store();
