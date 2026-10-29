// App shell: socket, router, top bar, alert drawer.
import { store } from "./store.js";
import { connectSocket } from "./api.js";
import { h } from "./ui.js";
import * as f from "./fmt.js";
import * as overview from "./views/overview.js";
import * as logs from "./views/logs.js";
import * as apicalls from "./views/apicalls.js";
import * as tools from "./views/tools.js";
import * as settings from "./views/settings.js";
import * as gifs from "./views/gifs.js";
import * as cli from "./views/cli.js";

const $ = (id) => document.getElementById(id);
const views = {};
let mounted = false;

function mountViews() {
  views.overview = overview.mount($("view-overview")) || {};
  views.logs = logs.mount($("view-logs"), {
    onErrorCount: (n) => { const b = $("badge-logs"); b.hidden = !n; b.textContent = n; },
  }) || {};
  views.api = apicalls.mount($("view-api")) || {};
  views.settings = settings.mount($("view-settings")) || {};
  views.gifs = gifs.mount($("view-gifs")) || {};
  views.tools = tools.mount($("view-tools")) || {};
  views.cli = cli.mount($("view-cli")) || {};
  mounted = true;
}

function route() {
  const name = (location.hash || "#overview").slice(1);
  const key = views[name] || !mounted ? name : "overview";
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== `view-${key}`; });
  document.querySelectorAll(".nav a").forEach((a) => a.setAttribute("aria-selected", String(a.dataset.view === key)));
  const sec = $(`view-${key}`);
  $("view-title").textContent = sec?.dataset.title || "";
  document.title = `${sec?.dataset.title || ""} · LatoDash`;
  views[key]?.shown?.();
}
window.addEventListener("hashchange", route);

// ---- top bar
function renderLink() {
  const el = $("link-state");
  let state = store.state?.link?.state || "connecting", text;
  if (!store.socketUp) { state = "offline"; text = "dashboard offline"; }
  else if (state === "connected") text = `linked · ${store.state?.host || "server"}`;
  else if (state === "error") text = store.state?.link?.error || "server unreachable";
  else text = "connecting…";
  el.dataset.state = state;
  el.lastElementChild.textContent = text;
  el.title = text;
}

function renderTopbar() {
  const s = store.state;
  if (!s) return;
  const cfg = store.config || {};
  $("server-chip").textContent = `${cfg.server || ""}${s.host ? ` · ${s.host}` : ""}`;
  $("demo-chip").hidden = !cfg.demo;
  $("version").textContent = `v${cfg.version || ""}`;
  $("uptime").textContent = s.uptime ? f.duration(s.uptime) : "—";
  const chip = $("now-chip");
  const inf = s.inflight;
  let phase = "idle", text = "idle";
  if (inf) {
    phase = inf.phase;
    text = inf.phase === "prompt" ? `reading prompt ${Math.round((inf.progress || 0) * 100)}%` : inf.phase === "generating" ? "generating" : inf.phase;
  } else if (s.pending_reply) { phase = "waiting"; text = `waiting · ${s.pending_reply.who || "?"}`; }
  chip.dataset.phase = phase;
  chip.lastElementChild.textContent = text;

  const crit = s.alerts.filter((a) => a.level === "crit").length;
  const count = $("alert-count");
  count.hidden = !s.alerts.length;
  count.textContent = s.alerts.length;
  count.className = crit ? "crit" : "";
  if (!$("alert-drawer").hidden) renderDrawer();
  renderLink();
}

function tickClock() {
  if (!store.state) return;
  $("clock").textContent = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    .format(new Date(store.serverNow() * 1000));
}
setInterval(tickClock, 1000);

function renderDrawer() {
  const d = $("alert-drawer");
  const alerts = store.state?.alerts || [];
  d.replaceChildren(h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), "Alerts")),
    alerts.length ? h("div", { class: "alerts" }, alerts.map((a) => h("div", { class: "alert", dataset: { level: a.level } }, h("span"),
      h("div", {}, h("div", { class: "alert-title" }, a.title), a.detail ? h("div", { class: "alert-detail" }, a.detail) : null),
      h("div", { class: "alert-time" }, a.since ? f.hm(a.since) : ""))))
      : h("div", { class: "all-good" }, "No alerts — everything looks healthy"));
}
$("alert-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const d = $("alert-drawer");
  d.hidden = !d.hidden;
  if (!d.hidden) renderDrawer();
});
document.addEventListener("click", (e) => { if (!e.target.closest("#alert-drawer")) $("alert-drawer").hidden = true; });

// ---- boot
store.on("state", renderTopbar);
store.on("link", renderLink);
connectSocket({
  onMessage(msg) {
    store.apply(msg);
    if (msg.type === "snapshot" && !mounted) {
      mountViews();
      route();
      store.emit("snapshot", msg.data);
      store.emit("state", store.state);
      tickClock();
    }
  },
  onStatus(up) { store.socketUp = up; renderLink(); },
});
route();
