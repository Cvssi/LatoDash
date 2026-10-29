// API calls: every Ollama HTTP request, Discord reply, plus per-request LLM token stats.
import { store } from "../store.js";
import { h } from "../ui.js";
import * as f from "../fmt.js";

const isError = (c) => c.status === 0 || c.status >= 500 || (c.status >= 400 && c.method !== "HEAD");
const SERVICES = [["ollama", "Ollama"], ["discord", "Discord"]];

function kpiCard(label) {
  const v = h("div", { class: "kpi-value" }, "—"), foot = h("div", { class: "kpi-foot" }, " ");
  return { el: h("div", { class: "card kpi" }, h("div", { class: "kpi-label" }, label), v, foot), v, foot };
}

export function mount(root) {
  const k = { calls: kpiCard("Calls · 24h"), errors: kpiCard("Errors · 24h"), lat: kpiCard("Avg latency"),
              p95: kpiCard("Chat p95"), llm: kpiCard("LLM requests · 24h"), health: kpiCard("Health checks") };
  const shown = new Set(SERVICES.map((s) => s[0]));
  let errorsOnly = false;

  const genBody = h("tbody");
  const callBody = h("tbody");
  const chips = SERVICES.map(([key, label]) => {
    const b = h("button", { class: "chip", "aria-pressed": "true", onclick: () => {
      shown.has(key) ? shown.delete(key) : shown.add(key);
      b.setAttribute("aria-pressed", String(shown.has(key)));
      renderCalls();
    } }, label);
    return b;
  });
  const errChip = h("button", { class: "chip", "aria-pressed": "false", onclick: () => {
    errorsOnly = !errorsOnly; errChip.setAttribute("aria-pressed", String(errorsOnly)); renderCalls();
  } }, "Errors only");

  root.replaceChildren(
    h("div", { class: "grid g-kpi" }, Object.values(k).map((x) => x.el)),
    h("div", { class: "grid g-2" },
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), "LLM requests"),
          h("div", { class: "card-sub" }, "token usage & speed per request (from llama.cpp timings)")),
        h("div", { class: "table-wrap", style: { maxHeight: "calc(100vh - 330px)" } },
          h("table", {}, h("thead", {}, h("tr", {}, ["Time", "Source", "Prompt", "Output", "Prompt speed", "Gen speed", "Total", "Context"]
            .map((c, i) => h("th", { class: i >= 2 ? "num" : "" }, c)))), genBody))),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), "API calls"),
          h("div", { class: "row" }, h("div", { class: "chips" }, chips, errChip))),
        h("div", { class: "table-wrap", style: { maxHeight: "calc(100vh - 330px)" } },
          h("table", {}, h("thead", {}, h("tr", {}, ["Time", "Service", "Request", "Status", "Latency"]
            .map((c, i) => h("th", { class: i >= 3 ? "num" : "" }, c)))), callBody)))),
  );

  function renderKpis() {
    const s = store.state?.stats;
    if (!s) return;
    k.calls.v.textContent = f.num(s.api.calls);
    k.calls.foot.textContent = SERVICES.map(([key, label]) => `${label} ${s.api.by_service[key] || 0}`).join(" · ");
    k.errors.v.textContent = f.num(s.api.errors);
    k.errors.el.dataset.tone = s.api.errors ? "warn" : "ok";
    k.errors.foot.textContent = s.api.calls ? `${((100 * s.api.errors) / s.api.calls).toFixed(1)}% error rate` : " ";
    k.lat.v.textContent = f.ms(s.api.avg_ms);
    k.lat.foot.textContent = "Ollama HTTP, server side";
    k.p95.v.textContent = f.ms(s.api.chat_p95_ms);
    k.p95.foot.textContent = "POST /api/chat";
    k.llm.v.textContent = f.num(s.llm.calls);
    k.llm.foot.textContent = `bot ${s.llm.by_source.bot} · CLI ${s.llm.by_source.cli} · p50 ${f.ms(s.llm.p50_ms)}`;
    k.health.v.textContent = f.num(s.api.health.count);
    k.health.foot.textContent = s.api.health.last_latency_ms != null ? `last ${f.ms(s.api.health.last_latency_ms)} · ${f.hm(s.api.health.last_ts)}` : "hidden from the table";
  }

  function genRow(g) {
    return h("tr", {},
      h("td", { class: "num", title: f.dayTime(g.ts) }, f.time(g.ts)),
      h("td", {}, h("span", { class: `svc-tag ${g.source}` }, g.source === "cli" ? "cli" : "bot")),
      h("td", { class: "num" }, f.num(g.prompt_tokens)),
      h("td", { class: "num" }, f.num(g.gen_tokens)),
      h("td", { class: "num" }, g.prompt_tps != null ? `${g.prompt_tps.toFixed(1)}/s` : "—"),
      h("td", { class: "num" }, g.gen_tps != null ? `${g.gen_tps.toFixed(1)}/s` : "—"),
      h("td", { class: "num" }, f.ms(g.total_ms)),
      h("td", { class: "num muted" }, g.ctx_used ? `${f.num(g.ctx_used)}${g.truncated ? " ✂" : ""}` : "—"));
  }

  function renderGens() {
    const rows = store.generations.slice(-400).reverse();
    genBody.replaceChildren(...(rows.length ? rows.map(genRow) : [h("tr", {}, h("td", { colspan: 8, class: "empty" }, "No LLM requests in the last 24 h"))]));
  }

  function callRow(c) {
    return h("tr", {},
      h("td", { class: "num", title: f.dayTime(c.ts) }, f.time(c.ts)),
      h("td", {}, h("span", { class: `svc-tag ${c.service}` }, c.service)),
      h("td", { class: "ellipsis mono small", title: c.detail || c.path }, `${c.method} ${c.path}`),
      h("td", { class: "num" }, h("span", { class: `code-status s${String(c.status)[0]}` }, c.status || "ERR")),
      h("td", { class: "num" }, c.latency_ms != null ? f.ms(c.latency_ms) : "—"));
  }

  function renderCalls() {
    const rows = store.apiCalls.filter((c) => shown.has(c.service) && (!errorsOnly || isError(c))).slice(-500).reverse();
    callBody.replaceChildren(...(rows.length ? rows.map(callRow) : [h("tr", {}, h("td", { colspan: 5, class: "empty" }, "No calls match"))]));
  }

  store.on("snapshot", () => { renderKpis(); renderGens(); renderCalls(); });
  store.on("state", renderKpis);
  store.on("gen", () => { if (!root.hidden) renderGens(); });
  store.on("api", (c) => {
    if (!root.hidden && shown.has(c.service) && (!errorsOnly || isError(c))) callBody.prepend(callRow(c));
  });
  return { shown() { renderGens(); renderCalls(); } };
}
