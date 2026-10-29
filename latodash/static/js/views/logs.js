// Logs: every journal line from the bot, Ollama, GPU services and kernel, filterable and searchable.
import { store } from "../store.js";
import { h, icon, ICONS } from "../ui.js";
import * as f from "../fmt.js";

const SOURCES = [["bot", "LatoChan", "--cyan"], ["ollama", "Ollama", "--accent"], ["system", "Services", "--blue"],
  ["kernel", "Kernel", "--crit"], ["dashboard", "Dashboard", "--ok"]];
const LEVELS = [["debug", "All"], ["info", "Info+"], ["warn", "Warnings+"], ["error", "Errors"]];
const ORDER = { debug: 0, info: 1, warn: 2, error: 3 };
const MAX_ROWS = 2500;

export function mount(root, { onErrorCount }) {
  const filters = { sources: new Set(SOURCES.map((s) => s[0])), level: "info", q: "", follow: true };
  let unseenErrors = 0;

  const box = h("div", { class: "logbox", role: "log", "aria-live": "off" });
  const search = h("input", { class: "input", type: "search", placeholder: "Search logs…  (/ to focus)" });
  const followBtn = h("button", { class: "btn sm", title: "Follow new lines" });
  const countEl = h("span", { class: "muted small num" });
  const jump = h("button", { class: "btn sm primary follow-hint", hidden: true, onclick: () => setFollow(true) }, "↓ Jump to live");

  const srcChips = SOURCES.map(([key, label, color]) => {
    const b = h("button", { class: "chip", "aria-pressed": "true", onclick: () => {
      filters.sources.has(key) ? filters.sources.delete(key) : filters.sources.add(key);
      b.setAttribute("aria-pressed", filters.sources.has(key));
      rerender();
    } }, h("i", { style: { background: `var(${color})` } }), label);
    return b;
  });
  const lvlChips = LEVELS.map(([key, label]) => {
    const b = h("button", { class: "chip", "aria-pressed": String(key === filters.level), onclick: () => {
      filters.level = key;
      lvlChips.forEach((c, i) => c.setAttribute("aria-pressed", String(LEVELS[i][0] === key)));
      rerender();
    } }, label);
    return b;
  });

  function setFollow(v) {
    filters.follow = v;
    followBtn.replaceChildren(icon(v ? ICONS.pause : ICONS.play), v ? "Following" : "Paused");
    jump.hidden = v;
    if (v) box.scrollTop = box.scrollHeight;
  }
  followBtn.onclick = () => setFollow(!filters.follow);

  let t;
  search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { filters.q = search.value.trim().toLowerCase(); rerender(); }, 150); });

  const exportBtn = h("button", { class: "btn sm", onclick: exportLogs }, icon(ICONS.export), "Export");

  root.replaceChildren(
    h("div", { class: "logs-toolbar" }, search, h("div", { class: "chips" }, srcChips), h("div", { class: "chips" }, lvlChips),
      h("span", { class: "spacer" }), countEl, followBtn, exportBtn),
    box, jump);
  setFollow(true);

  box.addEventListener("scroll", () => {
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    if (atBottom !== filters.follow) setFollow(atBottom);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !root.hidden && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
      e.preventDefault(); search.focus();
    }
  });

  const match = (e) => filters.sources.has(e.source) && ORDER[e.level] >= ORDER[filters.level]
    && (!filters.q || e.text.toLowerCase().includes(filters.q) || e.unit.toLowerCase().includes(filters.q));

  function highlight(text) {
    if (!filters.q) return text;
    const out = [];
    const low = text.toLowerCase();
    let i = 0, j;
    while ((j = low.indexOf(filters.q, i)) >= 0) {
      out.push(text.slice(i, j), h("mark", {}, text.slice(j, j + filters.q.length)));
      i = j + filters.q.length;
    }
    out.push(text.slice(i));
    return out;
  }

  const row = (e) => h("div", { class: `log-line l-${e.level} k-${e.kind}`, title: e.unit },
    h("span", { class: "log-time" }, f.time(e.ts)),
    h("span", { class: `log-src ${e.source}` }, e.source === "system" ? e.unit.replace(/\.(service|timer)$/, "") : e.source),
    h("span", { class: "log-msg" }, highlight(e.text)));

  function rerender() {
    const rows = store.logs.filter(match).slice(-MAX_ROWS);
    box.replaceChildren(...(rows.length ? rows.map(row) : [h("div", { class: "log-empty" }, "No log lines match these filters")]));
    countEl.textContent = `${rows.length} lines`;
    if (filters.follow) box.scrollTop = box.scrollHeight;
  }

  function exportLogs() {
    const lines = store.logs.filter(match).map((e) => `${new Date(e.ts * 1000).toISOString()} [${e.source}/${e.level}] ${e.text}`);
    const blob = new Blob([lines.join("\n")], { type: "text/plain" });
    const a = h("a", { href: URL.createObjectURL(blob), download: `latochan-logs-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.log` });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  let pending = [];
  let raf = 0;
  store.on("log", (e) => {
    if (e.level === "error" && root.hidden) { unseenErrors++; onErrorCount(unseenErrors); }
    if (!match(e)) return;
    pending.push(e);
    if (!raf) raf = requestAnimationFrame(flush);
  });
  function flush() {
    raf = 0;
    if (box.firstChild?.classList?.contains("log-empty")) box.replaceChildren();
    box.append(...pending.map(row));
    pending = [];
    while (box.childElementCount > MAX_ROWS) box.firstChild.remove();
    countEl.textContent = `${box.childElementCount} lines`;
    if (filters.follow) box.scrollTop = box.scrollHeight;
  }
  store.on("snapshot", rerender);

  return {
    shown() { unseenErrors = 0; onErrorCount(0); if (filters.follow) box.scrollTop = box.scrollHeight; },
  };
}
