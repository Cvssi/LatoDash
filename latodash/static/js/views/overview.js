// Overview: KPIs, live inference, health, hardware, token throughput, recent activity.
import { store } from "../store.js";
import { h, icon, pill } from "../ui.js";
import { TimeChart, BarChart, gauge, tone, css } from "../charts.js";
import * as f from "../fmt.js";

const CHECK = "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z";

function card(title, body, { sub, actions, cls = "" } = {}) {
  return h("div", { class: `card ${cls}` },
    h("div", { class: "card-head" },
      h("div", { class: "card-title" }, h("span", { class: "dot" }), title),
      sub ? h("div", { class: "card-sub" }, sub) : null, actions || null),
    body);
}

function kpi(label, cls = "") {
  const value = h("div", { class: `kpi-value ${cls}` }, "—");
  const foot = h("div", { class: "kpi-foot" }, " ");
  const el = h("div", { class: "card kpi" }, h("div", { class: "kpi-label" }, label), value, foot);
  return {
    el,
    set(v, unit, footText, t) {
      value.replaceChildren(v ?? "—", unit ? h("small", {}, unit) : "");
      foot.textContent = footText || " ";
      foot.title = footText || "";
      if (t) el.dataset.tone = t; else delete el.dataset.tone;
    },
  };
}

function stat(k) {
  const v = h("div", { class: "v" }, "—");
  return { el: h("div", { class: "stat" }, h("div", { class: "k" }, k), v), v };
}

export function mount(root) {
  // ---- KPI row
  const k = {
    bot: kpi("LatoChan"), model: kpi("Model", "kpi-sm"), speed: kpi("Gen speed"),
    tokens: kpi("Tokens · 24h"), replies: kpi("Replies · 24h"), health: kpi("Health"),
  };

  // ---- live inference
  const inferTitle = h("div", { class: "infer-title" }, "Idle");
  const inferSub = h("div", { class: "muted small" }, "waiting for messages");
  const prog = h("div", { class: "progress" }, h("i"));
  const ctxBar = h("div", { class: "bar-line" }, h("i"));
  const ctxText = h("span", { class: "num small" }, "—");
  const lastIn = h("div", { class: "last-msg" }, h("span", { class: "muted" }, "No messages yet"));
  const lastGen = h("div", { class: "stats" });
  const infer = h("div", { class: "infer", dataset: { phase: "idle" } },
    h("div", { class: "infer-state" }, h("div", { class: "infer-icon" }, h("i")), h("div", {}, inferTitle, inferSub)),
    prog,
    h("div", {}, h("div", { class: "row small muted" }, "Context window", h("span", { class: "spacer" }), ctxText), ctxBar),
    lastIn, lastGen);

  const alertsBox = h("div", { class: "alerts" });

  // ---- hardware
  const cpuG = gauge(), gpuG = gauge(), memG = gauge();
  const cpuS = { temp: stat("Package temp"), mhz: stat("Clock"), load: stat("Load 1/5/15"), proc: stat("Ollama CPU") };
  const gpuS = { junction: stat("Hotspot"), edge: stat("Edge / Mem"), power: stat("Power"), fan: stat("Fan"),
                 clocks: stat("Core / Mem clk"), volt: stat("VDDGFX") };
  const memS = { swap: stat("Swap"), disk: stat("Disk /"), rx: stat("Net ↓"), tx: stat("Net ↑"),
                 ollama: stat("Ollama RSS"), bot: stat("Bot RSS") };
  const vramBar = h("div", { class: "bar-line" }, h("i"));
  const vramText = h("span", { class: "num small" }, "—");
  const cores = h("div", { class: "cores" });
  const cpuCanvas = h("canvas", { class: "chart" }), gpuCanvas = h("canvas", { class: "chart" }), netCanvas = h("canvas", { class: "chart" });

  const cpuCard = card("CPU", [h("div", { class: "hw" }, cpuG.el, h("div", { class: "stats" }, Object.values(cpuS).map((s) => s.el))), cores, cpuCanvas],
    { sub: "Xeon E5-2680 v4 · 14c/28t" });
  const gpuCard = card("GPU", [h("div", { class: "hw" }, gpuG.el, h("div", { class: "stats" }, Object.values(gpuS).map((s) => s.el))),
    h("div", { class: "section-gap" }, h("div", { class: "row small muted" }, "VRAM", h("span", { class: "spacer" }), vramText), vramBar), gpuCanvas],
    { sub: "Radeon RX Vega 56" });
  const memCard = card("Memory · Disk · Network", [h("div", { class: "hw" }, memG.el, h("div", { class: "stats" }, Object.values(memS).map((s) => s.el))), netCanvas],
    { sub: "32 GB DDR4 ECC" });

  // ---- throughput charts
  const speedCanvas = h("canvas", { class: "chart tall" });
  const hourlyCanvas = h("canvas", { class: "chart tall" });
  const legend = (items) => h("div", { class: "legend" }, items.map(([c, t]) => h("span", {}, h("i", { style: { background: c } }), t)));

  // ---- lists
  const apiList = h("div", { class: "list" });
  const errList = h("div", { class: "list" });
  const svcList = h("div", { class: "list" });

  root.replaceChildren(
    h("div", { class: "grid g-kpi" }, Object.values(k).map((x) => x.el)),
    h("div", { class: "grid g-21" },
      card("Live inference", infer, { sub: "llama.cpp runner via Ollama" }),
      card("Health & alerts", alertsBox)),
    h("div", { class: "grid g-3" }, cpuCard, gpuCard, memCard),
    h("div", { class: "grid g-2" },
      card("Token speed · 24h", [legend([[css("--ok"), "generation tok/s"], [css("--accent"), "prompt tok/s"], [css("--blue"), "CLI requests"]]), speedCanvas],
        { sub: "one dot per LLM request" }),
      card("Tokens per hour · 24h", [legend([[css("--accent"), "prompt"], [css("--ok"), "generated"]]), hourlyCanvas])),
    h("div", { class: "grid g-3" },
      card("Recent API calls", apiList, { actions: h("a", { class: "btn sm ghost", href: "#api" }, "All →") }),
      card("Warnings & errors", errList, { actions: h("a", { class: "btn sm ghost", href: "#logs" }, "Logs →") }),
      card("Services", svcList)),
  );

  // ---- charts
  const accent = css("--accent"), ok = css("--ok"), cyan = css("--cyan"), magenta = css("--magenta"), blue = css("--blue"), warn = css("--warn");
  const cpuChart = new TimeChart(cpuCanvas, {
    window: 300, yMax: 100, axisFmt: (v) => `${v}%`,
    series: [{ label: "CPU", color: accent, fill: true, value: (p) => p.cpu?.pct, fmt: (v) => f.pct(v, 1) },
             { label: "Temp", color: warn, value: (p) => p.cpu?.temp, fmt: f.temp, width: 1.2 }],
  });
  const gpuChart = new TimeChart(gpuCanvas, {
    window: 300, yMax: 110, axisFmt: (v) => `${v}`,
    series: [{ label: "Busy", color: accent, fill: true, value: (p) => p.gpu?.busy, fmt: (v) => f.pct(v) },
             { label: "Hotspot", color: warn, value: (p) => p.gpu?.temp_junction, fmt: f.temp, width: 1.2 },
             { label: "Power", color: cyan, value: (p) => p.gpu?.power_w, fmt: (v) => (v == null ? "—" : `${v} W`), width: 1.2 }],
  });
  const netChart = new TimeChart(netCanvas, {
    window: 300, axisFmt: (v) => f.bytes(v, 0).replace(" ", ""),
    series: [{ label: "↓ rx", color: cyan, fill: true, value: (p) => p.net?.rx_bps, fmt: f.rate },
             { label: "↑ tx", color: magenta, value: (p) => p.net?.tx_bps, fmt: f.rate }],
  });
  const speedChart = new TimeChart(speedCanvas, {
    window: 86400, dots: true, lines: 4, now: () => store.serverNow(), axisFmt: (v) => `${Math.round(v)}`,
    timeFmt: (t) => f.dayTime(t),
    series: [{ label: "gen", color: ok, value: (g) => (g.source === "cli" ? null : g.gen_tps), fmt: (v) => (v == null ? "—" : `${v.toFixed(1)} tok/s`), line: true },
             { label: "prompt", color: accent, value: (g) => g.prompt_tps, fmt: (v) => (v == null ? "—" : `${v.toFixed(1)} tok/s`) },
             { label: "cli gen", color: blue, value: (g) => (g.source === "cli" ? g.gen_tps : null), fmt: (v) => (v == null ? "—" : `${v.toFixed(1)} tok/s`) }],
    extraTip: (g) => [[null, `${g.prompt_tokens} in → ${g.gen_tokens} out · ${f.ms(g.total_ms)}`]],
  });
  const hourlyChart = new BarChart(hourlyCanvas, {
    axisFmt: (v) => f.compact(v), valueFmt: (v) => f.num(v),
    series: [{ label: "prompt", color: accent }, { label: "generated", color: ok }],
  });

  // ---- renderers
  function renderMetrics() {
    const m = store.metrics.at(-1);
    cpuChart.setPoints(store.metrics); gpuChart.setPoints(store.metrics); netChart.setPoints(store.metrics);
    if (!m) return;
    const c = m.cpu || {}, g = m.gpu || {}, mem = m.mem || {}, cg = m.cgroups || {};
    cpuG.set(c.pct, f.pct(c.pct), "cpu", tone(c.pct, 75, 92));
    cpuS.temp.v.textContent = f.temp(c.temp);
    cpuS.mhz.v.textContent = c.mhz ? `${f.num(c.mhz)} MHz` : "—";
    cpuS.load.v.textContent = c.load ? c.load.map((x) => x.toFixed(2)).join(" ") : "—";
    const oc = cg["ollama.service"]?.cores;
    cpuS.proc.v.textContent = oc == null ? "—" : `${oc.toFixed(2)} cores`;
    if (c.cores?.length) {
      if (cores.children.length !== c.cores.length) cores.replaceChildren(...c.cores.map(() => h("i", {}, h("b"))));
      c.cores.forEach((v, i) => { cores.children[i].firstChild.style.height = `${Math.min(100, v)}%`; cores.children[i].title = `core ${i}: ${f.pct(v)}`; });
    }
    gpuG.set(g.busy, f.pct(g.busy), "busy", tone(g.temp_junction, 95, 100));
    gpuS.junction.v.textContent = f.temp(g.temp_junction);
    gpuS.junction.v.className = `v ${g.temp_junction >= 100 ? "crit" : g.temp_junction >= 95 ? "warn" : ""}`;
    gpuS.edge.v.textContent = `${f.temp(g.temp_edge)} / ${f.temp(g.temp_mem)}`;
    gpuS.power.v.textContent = g.power_w == null ? "—" : `${g.power_w} / ${g.power_cap_w ?? "?"} W`;
    gpuS.fan.v.textContent = g.fan_rpm == null ? "—" : `${f.num(g.fan_rpm)} rpm${g.fan_pct != null ? ` · ${g.fan_pct}%` : ""}`;
    gpuS.clocks.v.textContent = `${g.sclk_mhz ?? "—"} / ${g.mclk_mhz ?? "—"} MHz`;
    gpuS.volt.v.textContent = g.vddgfx_mv == null ? "—" : `${g.vddgfx_mv} mV`;
    const vp = g.vram_total ? (100 * g.vram_used) / g.vram_total : null;
    vramBar.firstChild.style.width = `${vp ?? 0}%`;
    vramBar.firstChild.style.background = vp > 98.5 ? css("--crit") : "";
    vramText.textContent = g.vram_total ? `${f.bytes(g.vram_used)} / ${f.bytes(g.vram_total)} · ${f.pct(vp)}` : "—";
    const mp = mem.total ? (100 * mem.used) / mem.total : null;
    memG.set(mp, f.pct(mp), "ram used", tone(mp, 85, 95));
    memS.swap.v.textContent = mem.swap_total ? `${f.bytes(mem.swap_used)} / ${f.bytes(mem.swap_total, 0)}` : "—";
    memS.disk.v.textContent = mem.disk_total ? `${f.bytes(mem.disk_used, 0)} / ${f.bytes(mem.disk_total, 0)}` : "—";
    memS.rx.v.textContent = f.rate(m.net?.rx_bps);
    memS.tx.v.textContent = f.rate(m.net?.tx_bps);
    memS.ollama.v.textContent = f.bytes(cg["ollama.service"]?.rss);
    memS.bot.v.textContent = f.bytes(cg["latochan.service"]?.rss);
  }

  function renderState(s) {
    if (!s) return;
    const cfg = store.config || {};
    const now = store.serverNow();
    const bot = s.services?.[cfg.bot_unit];
    k.bot.set(bot ? (bot.active === "active" ? "online" : bot.active) : "—", "",
      bot?.since ? `up ${f.duration(now - bot.since)}${bot.restarts ? ` · ${bot.restarts} auto-restarts` : ""}` : "",
      bot ? (bot.active === "active" ? "ok" : "crit") : null);
    const loaded = s.ollama?.loaded || [];
    const main = loaded[0];
    k.model.set(main ? main.name.replace(":latest", "") : (s.ollama?.ok === false ? "API down" : "none loaded"), "",
      `${s.settings?.live_model ? `latochan uses ${s.settings.live_model} · ` : ""}${main ? `${f.bytes(main.vram)} VRAM · ctx ${f.num(main.ctx)}` : "loads on next message"}`,
      main ? null : (s.ollama?.ok === false ? "crit" : "warn"));
    const llm = s.stats?.llm || {};
    k.speed.set(llm.last?.gen_tps != null ? llm.last.gen_tps.toFixed(1) : "—", "tok/s",
      `avg ${llm.gen_tps != null ? llm.gen_tps.toFixed(1) : "—"} · prompt ${llm.prompt_tps != null ? llm.prompt_tps.toFixed(0) : "—"} tok/s`);
    k.tokens.set(f.compact((llm.prompt_tokens || 0) + (llm.gen_tokens || 0)), "",
      `${f.compact(llm.prompt_tokens)} in · ${f.compact(llm.gen_tokens)} out · ${llm.calls || 0} calls`);
    const b = s.stats?.bot || {};
    k.replies.set(f.num(b.replied), "", `${b.in || 0} in · ${b.quiet || 0} skipped · avg ${b.reply_avg_s ?? "—"}s`);
    const crit = s.alerts.filter((a) => a.level === "crit").length, warnN = s.alerts.filter((a) => a.level === "warn").length;
    k.health.set(crit ? `${crit} critical` : warnN ? `${warnN} warning${warnN > 1 ? "s" : ""}` : "all good", "",
      s.settings?.thinking ? "thinking mode ON" : "thinking mode off", crit ? "crit" : warnN ? "warn" : "ok");

    // alerts
    if (!s.alerts.length) {
      alertsBox.replaceChildren(h("div", { class: "all-good" }, icon(CHECK), "Everything looks healthy"),
        h("div", { class: "muted small section-gap" }, `Ollama API ${s.ollama?.latency_ms != null ? f.ms(s.ollama.latency_ms) : "—"} · `,
          `${s.stats?.api?.health?.count ?? 0} health checks · telemetry every ${cfg.sample_interval || 2}s`));
    } else {
      alertsBox.replaceChildren(...s.alerts.map((a) => h("div", { class: "alert", dataset: { level: a.level } }, h("span"),
        h("div", {}, h("div", { class: "alert-title" }, a.title), a.detail ? h("div", { class: "alert-detail" }, a.detail) : null),
        h("div", { class: "alert-time" }, a.since ? f.hm(a.since) : ""))));
    }

    // live inference
    const inf = s.inflight, pend = s.pending_reply;
    let phase = "idle";
    if (inf) phase = inf.phase;
    else if (pend) phase = "waiting";
    infer.dataset.phase = phase;
    if (inf) {
      const el = now - inf.started;
      const titles = { queued: "Request queued", prompt: "Reading the prompt", generating: "Generating reply", finishing: "Finishing" };
      inferTitle.textContent = titles[inf.phase] || inf.phase;
      inferSub.textContent = `task ${inf.task} · ${f.duration(el)}${inf.prompt_tps ? ` · ${inf.prompt_tps.toFixed(0)} tok/s prompt` : ""}`;
      prog.classList.toggle("indeterminate", inf.phase !== "prompt");
      prog.firstChild.style.width = inf.phase === "prompt" ? `${(inf.progress || 0) * 100}%` : "";
    } else if (pend) {
      inferTitle.textContent = "Waiting for the model";
      inferSub.textContent = `${pend.who || "?"} · ${f.duration(now - pend.since)}`;
      prog.classList.add("indeterminate");
    } else {
      inferTitle.textContent = "Idle";
      inferSub.textContent = s.stats?.llm?.last ? `last request ${f.ago(s.stats.llm.last.ts, now)}` : "waiting for messages";
      prog.classList.remove("indeterminate");
      prog.firstChild.style.width = "0";
    }
    const last = s.stats?.llm?.last;
    const ctxMax = inf?.ctx || last?.ctx || cfg.num_ctx || 8192;
    const ctxUsed = inf?.prompt_tokens || last?.ctx_used;
    const cp = ctxUsed ? (100 * ctxUsed) / ctxMax : 0;
    ctxBar.firstChild.style.width = `${cp}%`;
    ctxBar.firstChild.style.background = cp > 90 ? css("--warn") : "";
    ctxText.textContent = ctxUsed ? `${f.num(ctxUsed)} / ${f.num(ctxMax)} tokens` : "—";
    if (last) {
      const items = [["Prompt", `${f.num(last.prompt_tokens)} tok @ ${last.prompt_tps?.toFixed(0) ?? "—"}/s`],
        ["Output", `${f.num(last.gen_tokens)} tok @ ${last.gen_tps?.toFixed(1) ?? "—"}/s`],
        ["Total", f.ms(last.total_ms)], ["Source", last.source === "cli" ? "brain CLI" : "LatoChan"]];
      lastGen.replaceChildren(...items.map(([kk, vv]) => h("div", { class: "stat" }, h("div", { class: "k" }, kk), h("div", { class: "v" }, vv))));
    }

    // services
    svcList.replaceChildren(...(cfg.units || []).map((u) => {
      const sv = s.services?.[u];
      const st = sv?.active || "unknown";
      const t = st === "active" ? "ok" : st === "failed" ? "crit" : st === "inactive" && sv?.result === "success" ? "" : "warn";
      return h("div", { class: "list-row svc-row" }, h("span", { class: "svc-name", title: u }, u),
        h("span", { class: "muted small num" }, sv?.since && st === "active" ? f.duration(now - sv.since) : sv?.sub || ""),
        pill(st === "inactive" && sv?.result === "success" ? "done" : st, t));
    }));
  }

  function renderLastMessage() {
    const lastInEv = store.logs.findLast((e) => e.kind === "in");
    const lastRaw = store.logs.findLast((e) => e.kind === "raw");
    if (!lastInEv) return;
    lastIn.replaceChildren(...[
      h("div", {}, h("span", { class: "who" }, `${lastInEv.data?.who || "?"} · #${lastInEv.data?.channel || "?"} · ${f.time(lastInEv.ts)}`)),
      h("div", {}, lastInEv.data?.text || lastInEv.text),
      lastRaw && lastRaw.ts >= lastInEv.ts ? h("div", { class: "reply section-gap" }, `↳ ${lastRaw.data?.reply ?? lastRaw.text}`) : null,
    ].filter(Boolean));
  }

  function renderGens() {
    speedChart.setPoints(store.generations.map((g) => ({ ...g, t: g.ts })));
    const hourly = store.state?.stats?.hourly || [];
    hourlyChart.setBars(hourly.map((b) => ({
      label: `${f.hm(b.t)}–${f.hm(b.t + 3600)}`, axis: f.hm(b.t), values: [b.prompt, b.gen], tip: [`${b.calls} requests`],
    })));
  }

  function renderApi() {
    const calls = store.apiCalls.slice(-7).reverse();
    apiList.replaceChildren(...(calls.length ? calls.map((c) => h("div", { class: "list-row", style: { gridTemplateColumns: "auto 1fr auto auto" } },
      h("span", { class: `svc-tag ${c.service}` }, c.service),
      h("span", { class: "ellipsis mono small", title: c.detail || c.path }, `${c.method} ${c.path}`),
      h("span", { class: `code-status s${String(c.status)[0]}` }, c.status || "ERR"),
      h("span", { class: "muted small num" }, c.latency_ms != null ? f.ms(c.latency_ms) : f.hm(c.ts)))) : [h("div", { class: "empty" }, "No calls yet")]));
    const errs = store.logs.filter((e) => e.level === "error" || e.level === "warn").slice(-7).reverse();
    errList.replaceChildren(...(errs.length ? errs.map((e) => h("div", { class: "list-row", style: { gridTemplateColumns: "auto 1fr auto" } },
      pill(e.level, e.level === "error" ? "crit" : "warn"),
      h("span", { class: "ellipsis small", title: e.text }, e.text),
      h("span", { class: "muted small num" }, f.hm(e.ts)))) : [h("div", { class: "empty" }, "Nothing logged at warning level or above 🎉")]));
  }

  store.on("metrics", renderMetrics);
  store.on("state", (s) => { renderState(s); renderGens(); });
  store.on("gen", renderGens);
  store.on("api", renderApi);
  store.on("log", (e) => { if (e.kind === "in" || e.kind === "raw") renderLastMessage(); if (e.level === "error" || e.level === "warn") renderApi(); });
  store.on("snapshot", () => { renderMetrics(); renderGens(); renderApi(); renderLastMessage(); });
}
