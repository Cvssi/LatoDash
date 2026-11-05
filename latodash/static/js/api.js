// HTTP + WebSocket client for the LatoDash backend.

export class ApiError extends Error {
  constructor(status, message, output = "") {
    super(message);
    this.status = status;
    this.output = output;
  }
}

async function request(method, path, body, { signal } = {}) {
  const res = await fetch(path, {
    method,
    signal,
    headers: { "X-LatoDash": "1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    let msg = data?.error || res.statusText;
    if (Array.isArray(data?.detail)) msg = data.detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join("; ");
    throw new ApiError(res.status, msg, data?.output || "");
  }
  return data;
}

export const api = {
  get: (p, o) => request("GET", p, undefined, o),
  post: (p, b = {}, o) => request("POST", p, b, o),
  put: (p, b, o) => request("PUT", p, b, o),

  /** Streams NDJSON events from POST /api/chat. onEvent receives each parsed object. */
  async chat(body, onEvent, signal) {
    const res = await fetch("/api/chat", {
      method: "POST", signal,
      headers: { "X-LatoDash": "1", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const msg = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.error || res.statusText;
      throw new ApiError(res.status, msg);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) onEvent(JSON.parse(line));
      }
    }
  },
};

/** Live socket with automatic reconnect. handlers: {onMessage(msg), onStatus(up: boolean)} */
export function connectSocket({ onMessage, onStatus }) {
  let delay = 1000;
  let ws;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => { delay = 1000; onStatus(true); };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "batch") msg.items.forEach(onMessage);
      else onMessage(msg);
    };
    ws.onclose = () => {
      onStatus(false);
      setTimeout(open, delay);
      delay = Math.min(delay * 1.7, 10000);
    };
    ws.onerror = () => ws.close();
  };
  open();
  return () => { ws.onclose = null; ws.close(); };
}
