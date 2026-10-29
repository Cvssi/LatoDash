// Dependency-free canvas charts: time-series lines/areas, stacked bars, and SVG ring gauges.
import { h } from "./ui.js";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

class CanvasChart {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.tip = h("div", { class: "chart-tip", hidden: true });
    canvas.parentElement.classList.add("chart-wrap");
    canvas.parentElement.append(this.tip);
    this.hoverX = null;
    this._raf = 0;
    new ResizeObserver(() => this.schedule()).observe(canvas);
    canvas.addEventListener("mousemove", (e) => { this.hoverX = e.offsetX; this.schedule(); });
    canvas.addEventListener("mouseleave", () => { this.hoverX = null; this.tip.hidden = true; this.schedule(); });
  }

  schedule() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this._paint(); });
  }

  _paint() {
    const { canvas, ctx } = this;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, hgt = canvas.clientHeight;
    if (!w || !hgt) return;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(hgt * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(hgt * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, hgt);
    this.draw(ctx, w, hgt);
  }

  _grid(ctx, w, hgt, pad, yMin, yMax, fmt, lines = 3) {
    ctx.strokeStyle = css("--line");
    ctx.fillStyle = css("--dim");
    ctx.lineWidth = 1;
    ctx.font = `10px ${css("--mono")}`;
    ctx.textBaseline = "middle";
    for (let i = 0; i <= lines; i++) {
      const y = Math.round(pad.t + ((hgt - pad.t - pad.b) * i) / lines) + 0.5;
      ctx.beginPath();
      ctx.setLineDash(i === lines ? [] : [2, 4]);
      ctx.moveTo(pad.l, y);
      ctx.lineTo(w - pad.r, y);
      ctx.stroke();
      const v = yMax - ((yMax - yMin) * i) / lines;
      ctx.fillText(fmt(Math.abs(v) >= 10 ? Math.round(v) : Math.round(v * 10) / 10), 0, y);
    }
    ctx.setLineDash([]);
  }

  _showTip(x, y, lines) {
    this.tip.replaceChildren(...lines.map(([color, text]) =>
      h("div", {}, color ? h("span", { style: { color } }, "● ") : null, text)));
    this.tip.style.left = `${x}px`;
    this.tip.style.top = `${y}px`;
    this.tip.hidden = false;
  }
}

/** Time-series chart over a sliding window. series: [{label, color, value(p), fill, fmt}] */
export class TimeChart extends CanvasChart {
  constructor(canvas, opts) {
    super(canvas);
    this.o = { window: 300, yMin: 0, yMax: null, axisFmt: (v) => Math.round(v), dots: false, lines: 3, timeFmt: null, ...opts };
    this.points = [];
  }

  setPoints(points) { this.points = points; this.schedule(); }

  draw(ctx, w, hgt) {
    const o = this.o;
    const pad = { l: 34, r: 6, t: 8, b: 6 };
    const now = o.now ? o.now() : (this.points.at(-1)?.t ?? Date.now() / 1000);
    const t0 = now - o.window;
    const pts = this.points.filter((p) => p.t >= t0);
    let yMax = o.yMax;
    if (yMax == null) {
      yMax = 1;
      for (const p of pts) for (const s of o.series) yMax = Math.max(yMax, s.value(p) ?? 0);
      yMax *= 1.15;
    }
    const yMin = o.yMin;
    this._grid(ctx, w, hgt, pad, yMin, yMax, o.axisFmt, o.lines);
    const X = (t) => pad.l + ((t - t0) / o.window) * (w - pad.l - pad.r);
    const Y = (v) => pad.t + (1 - (Math.min(Math.max(v, yMin), yMax) - yMin) / (yMax - yMin)) * (hgt - pad.t - pad.b);

    for (const s of o.series) {
      const seg = pts.map((p) => [X(p.t), s.value(p)]).filter(([, v]) => v != null && !Number.isNaN(v));
      if (!seg.length) continue;
      if (s.fill && !o.dots) {
        const grad = ctx.createLinearGradient(0, pad.t, 0, hgt - pad.b);
        grad.addColorStop(0, s.color + "55");
        grad.addColorStop(1, s.color + "00");
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.moveTo(seg[0][0], Y(yMin));
        for (const [x, v] of seg) ctx.lineTo(x, Y(v));
        ctx.lineTo(seg.at(-1)[0], Y(yMin));
        ctx.closePath();
        ctx.fill();
      }
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width || 1.6;
      ctx.lineJoin = "round";
      if (!o.dots || s.line) {
        ctx.beginPath();
        seg.forEach(([x, v], i) => (i ? ctx.lineTo(x, Y(v)) : ctx.moveTo(x, Y(v))));
        ctx.globalAlpha = o.dots ? 0.35 : 1;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
      if (o.dots) {
        ctx.fillStyle = s.color;
        for (const [x, v] of seg) { ctx.beginPath(); ctx.arc(x, Y(v), 2.6, 0, Math.PI * 2); ctx.fill(); }
      }
    }

    if (this.hoverX != null && pts.length) {
      let best = pts[0];
      for (const p of pts) if (Math.abs(X(p.t) - this.hoverX) < Math.abs(X(best.t) - this.hoverX)) best = p;
      const x = X(best.t);
      ctx.strokeStyle = css("--line-2");
      ctx.beginPath(); ctx.moveTo(x + 0.5, pad.t); ctx.lineTo(x + 0.5, hgt - pad.b); ctx.stroke();
      const tf = o.timeFmt || ((t) => new Date(t * 1000).toLocaleTimeString("en-GB"));
      this._showTip(x, 0, [[null, tf(best.t)], ...o.series.map((s) => [s.color, `${s.label}: ${s.fmt ? s.fmt(s.value(best)) : s.value(best)}`]),
        ...(o.extraTip ? o.extraTip(best) : [])]);
    }
  }
}

/** Stacked bars. bars: [{label, values: [..], tip}] ; series: [{label, color}] */
export class BarChart extends CanvasChart {
  constructor(canvas, opts) {
    super(canvas);
    this.o = { axisFmt: (v) => Math.round(v), ...opts };
    this.bars = [];
  }

  setBars(bars) { this.bars = bars; this.schedule(); }

  draw(ctx, w, hgt) {
    const pad = { l: 34, r: 4, t: 8, b: 16 };
    const totals = this.bars.map((b) => b.values.reduce((a, v) => a + v, 0));
    const yMax = Math.max(1, ...totals) * 1.12;
    this._grid(ctx, w, hgt, pad, 0, yMax, this.o.axisFmt);
    const n = this.bars.length || 1;
    const slot = (w - pad.l - pad.r) / n;
    const bw = Math.max(2, slot * 0.68);
    let hover = -1;
    if (this.hoverX != null) hover = Math.floor((this.hoverX - pad.l) / slot);
    ctx.font = `10px ${css("--mono")}`;
    ctx.textBaseline = "alphabetic";
    this.bars.forEach((b, i) => {
      const x = pad.l + i * slot + (slot - bw) / 2;
      let y = hgt - pad.b;
      b.values.forEach((v, j) => {
        const bh = (v / yMax) * (hgt - pad.t - pad.b);
        ctx.fillStyle = this.o.series[j].color;
        ctx.globalAlpha = hover === -1 || hover === i ? 1 : 0.45;
        ctx.beginPath();
        ctx.roundRect(x, y - bh, bw, bh, j === b.values.length - 1 ? [3, 3, 0, 0] : 0);
        ctx.fill();
        y -= bh;
      });
      ctx.globalAlpha = 1;
      if (b.axis && (i % Math.ceil(n / 8) === 0)) {
        ctx.fillStyle = css("--dim");
        ctx.fillText(b.axis, x, hgt - 3);
      }
    });
    if (hover >= 0 && hover < this.bars.length) {
      const b = this.bars[hover];
      this._showTip(pad.l + hover * slot + slot / 2, 0, [[null, b.label],
        ...this.o.series.map((s, j) => [s.color, `${s.label}: ${this.o.valueFmt ? this.o.valueFmt(b.values[j]) : b.values[j]}`]),
        ...(b.tip || []).map((t) => [null, t])]);
    }
  }
}

/** 270° ring gauge. Returns {el, set(value0to100, text, sub, color)} */
export function gauge() {
  const ns = "http://www.w3.org/2000/svg";
  const R = 44, C = 2 * Math.PI * R, ARC = C * 0.75;
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 100 100");
  const mk = (cls) => {
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("cx", 50); c.setAttribute("cy", 50); c.setAttribute("r", R);
    c.setAttribute("fill", "none"); c.setAttribute("stroke-width", 8); c.setAttribute("stroke-linecap", "round");
    c.setAttribute("class", cls);
    c.setAttribute("stroke-dasharray", `${ARC} ${C}`);
    return c;
  };
  const track = mk("track"), bar = mk("bar");
  bar.setAttribute("stroke-dashoffset", ARC);
  svg.append(track, bar);
  const b = h("b", {}, "—"), s = h("span", {}, "");
  const el = h("div", { class: "gauge" }, svg, h("div", { class: "gauge-text" }, h("div", {}, b, s)));
  return {
    el,
    set(value, text, sub, color) {
      const v = Math.max(0, Math.min(100, value ?? 0));
      bar.setAttribute("stroke-dashoffset", ARC * (1 - v / 100));
      bar.setAttribute("stroke", color || css("--accent"));
      b.textContent = text;
      s.textContent = sub;
    },
  };
}

/** Pick a tone colour for a value against warn/crit thresholds. */
export function tone(v, warn, crit) {
  if (v == null) return css("--dim");
  if (v >= crit) return css("--crit");
  if (v >= warn) return css("--warn");
  return null;
}

export { css };
