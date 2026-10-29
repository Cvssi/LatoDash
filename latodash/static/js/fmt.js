// Formatting helpers. Everything returns plain strings (never HTML).

const timeFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const shortFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

export const time = (ts) => (ts ? timeFmt.format(new Date(ts * 1000)) : "—");
export const hm = (ts) => (ts ? shortFmt.format(new Date(ts * 1000)) : "—");
export const dayTime = (ts) => (ts ? dayFmt.format(new Date(ts * 1000)) : "—");

export function bytes(n, digits = 1) {
  if (n == null || Number.isNaN(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = Math.abs(n);
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${(Math.sign(n) * v).toFixed(i === 0 ? 0 : digits)} ${units[i]}`;
}

export const rate = (bps) => (bps == null ? "—" : `${bytes(bps)}/s`);

export function num(n, digits = 0) {
  if (n == null || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function compact(n) {
  if (n == null) return "—";
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
  return num(n);
}

export const pct = (v, digits = 0) => (v == null ? "—" : `${Number(v).toFixed(digits)}%`);
export const temp = (v) => (v == null ? "—" : `${Math.round(v)}°C`);

export function ms(v) {
  if (v == null) return "—";
  if (v < 1) return `${Math.round(v * 1000)} µs`;
  if (v < 1000) return `${v.toFixed(v < 10 ? 1 : 0)} ms`;
  return `${(v / 1000).toFixed(v < 10000 ? 2 : 1)} s`;
}

export function duration(sec) {
  if (sec == null || sec < 0) return "—";
  sec = Math.floor(sec);
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

export function ago(ts, now) {
  if (!ts) return "—";
  return `${duration(now - ts)} ago`;
}
