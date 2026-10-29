// Settings: every live knob of LatoChan, generated from the schema the server reports (botsettings.py).
import { store } from "../store.js";
import { api } from "../api.js";
import { h, icon, ICONS, toast, confirmDialog, withBusy, pill } from "../ui.js";

// A setting only matters while these switches are on; its row is greyed out otherwise.
const SLEEP = ["sleep_mode"];
const CHIME = ["sleep_mode", "chimes_enabled"];
const DEPENDS = {
  awake_min_minutes: SLEEP, awake_max_minutes: SLEEP, friends_can_wake: SLEEP, chimes_enabled: SLEEP,
  chime_min_hours: CHIME, chime_max_hours: CHIME, chime_wait_seconds: CHIME, quiet_hours_enabled: CHIME,
  quiet_from_hour: [...CHIME, "quiet_hours_enabled"], quiet_to_hour: [...CHIME, "quiet_hours_enabled"],
  gif_chance: ["gifs_enabled"], gif_reply_chance: ["gifs_enabled"], gif_only_chance: ["gifs_enabled"], gif_only: ["gifs_enabled"], wake_gif_chance: ["gifs_enabled", "sleep_mode"], gif_cooldown_seconds: ["gifs_enabled"],
  chatter_reply_chance: SLEEP,
};

const num = (v) => String(Number(Number(v).toFixed(3)));
function show(spec, v) {
  if (spec.kind === "bool") return v ? "on" : "off";
  if (spec.kind === "choice") return String(v);
  if (spec.unit === "%") return `${num(v)}%`;
  if (spec.unit === "o'clock") return `${String(v).padStart(2, "0")}:00`;
  return spec.unit ? `${num(v)} ${spec.unit}` : num(v);
}

export function mount(root) {
  let doc = null;          // {schema, groups, defaults}
  let saved = {};          // what the server has
  let draft = {};          // what the page shows
  const rows = new Map();  // key -> {row, sync}
  const body = h("div", { class: "empty" }, "Loading…");
  const bar = h("div", { class: "savebar", hidden: true });
  const status = h("div", { class: "row" });

  const changedKeys = () => Object.keys(draft).filter((k) => draft[k] !== saved[k]);

  function makeControl(spec) {
    const key = spec.key;
    if (spec.kind === "bool") {
      const input = h("input", { type: "checkbox" });
      input.onchange = () => { draft[key] = input.checked; syncAll(); };
      return { el: h("label", { class: "switch" }, input, h("span", { class: "track" })), set: (v) => { input.checked = !!v; } };
    }
    if (spec.kind === "choice") {
      const sel = h("select", { class: "select" }, spec.choices.map((c) => h("option", { value: c }, c)));
      sel.onchange = () => { draft[key] = sel.value; syncAll(); };
      return { el: sel, set: (v) => { sel.value = v; } };
    }
    const input = h("input", { type: "range", min: spec.min, max: spec.max, step: spec.step || (spec.kind === "int" ? 1 : "any") });
    const out = h("span", { class: "set-val" });
    input.oninput = () => { draft[key] = Number(input.value); syncAll(); };
    return { el: h("div", { class: "set-ctl", style: { flex: 1 } }, input, out), set: (v) => { input.value = v; out.textContent = show(spec, v); } };
  }

  function makeRow(spec) {
    const ctl = makeControl(spec);
    const reset = h("button", { class: "btn ghost sm set-reset", title: `Back to default (${show(spec, spec.default)})`, "aria-label": "Back to default" }, icon(ICONS.restart));
    reset.onclick = () => { draft[spec.key] = spec.default; syncAll(); };
    const row = h("div", { class: "set-row" },
      h("div", {}, h("div", { class: "set-name" }, spec.label), spec.help ? h("div", { class: "set-help" }, spec.help) : null),
      h("div", { class: "set-ctl" }, ctl.el), reset);
    return { row, spec, sync() {
      const v = draft[spec.key];
      ctl.set(v);
      row.dataset.dirty = String(v !== saved[spec.key]);
      row.dataset.changed = String(v !== spec.default);
      row.dataset.off = String((DEPENDS[spec.key] || []).some((d) => !draft[d]));
    } };
  }

  function syncAll() {
    for (const r of rows.values()) r.sync();
    const n = changedKeys().length;
    bar.hidden = n === 0;
    bar.querySelector(".bar-text").textContent = `${n} unsaved change${n === 1 ? "" : "s"}`;
    renderStatus();
  }

  function renderStatus() {
    const v = draft;
    status.replaceChildren(
      pill(v.thinking ? "Thinking on" : "Thinking off", v.thinking ? "warn" : ""),
      pill(v.sleep_mode ? "Sleep mode on" : "Always awake", v.sleep_mode ? "info" : "warn"),
      pill(!v.gifs_enabled ? "Gifs off" : v.gif_only ? "Gif mode: gifs only" : `Gifs ${v.gif_reply_chance}% of replies`, !v.gifs_enabled ? "warn" : v.gif_only ? "accent" : ""));
  }

  function build() {
    rows.clear();
    const cards = doc.groups.map((g) => {
      const specs = doc.schema.filter((s) => s.group === g.id);
      const list = specs.map((s) => { const r = makeRow(s); rows.set(s.key, r); return r.row; });
      return h("div", { class: "card tool-card" },
        h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), g.label)),
        g.help ? h("p", { class: "desc" }, g.help) : null, ...list);
    });
    const resetAll = h("button", { class: "btn danger" }, icon(ICONS.restart), "Reset everything to defaults");
    resetAll.onclick = async () => {
      if (!(await confirmDialog({ title: "Reset all settings?", danger: true, confirm: "Reset all",
        body: "Every setting goes back to its default (thinking off, sleep mode on, gifs 100%). Takes effect on her next message." }))) return;
      try {
        const res = await withBusy(resetAll, () => api.post("/api/bot-settings/reset", { keys: [] }));
        saved = { ...res.values }; draft = { ...res.values }; syncAll();
        toast("Settings reset", "back to defaults");
      } catch (err) { toast("Couldn't reset", err.message, "error", 7000); }
    };
    const left = cards.filter((_, i) => i % 2 === 0), right = cards.filter((_, i) => i % 2 === 1);
    body.replaceChildren(
      h("div", { class: "set-intro" }, status, h("span", { class: "muted small" }, "Changes reach her on her next message — no restart. Greyed-out rows don't matter while their master switch is off."), resetAll),
      h("div", { class: "grid g-2", style: { alignItems: "start" } }, h("div", { class: "set-col" }, left), h("div", { class: "set-col" }, right)));
    body.className = "";
    syncAll();
  }

  const discard = h("button", { class: "btn ghost" }, "Discard");
  const save = h("button", { class: "btn primary" }, icon(ICONS.check), "Save changes");
  discard.onclick = () => { draft = { ...saved }; syncAll(); };
  save.onclick = async () => {
    const keys = changedKeys();
    if (!keys.length) return;
    const patch = Object.fromEntries(keys.map((k) => [k, draft[k]]));
    try {
      const res = await withBusy(save, () => api.put("/api/bot-settings", { values: patch }));
      saved = { ...res.values }; draft = { ...res.values }; syncAll();
      toast("Settings saved", `${keys.length} change${keys.length === 1 ? "" : "s"} · live on her next message`);
    } catch (err) { toast("Couldn't save", err.message, "error", 8000); }
  };
  bar.append(h("span", { class: "bar-text" }), h("span", { class: "spacer" }), discard, save);

  async function load() {
    try {
      const res = await api.get("/api/bot-settings");
      doc = res; saved = { ...res.values }; draft = { ...res.values };
      build();
    } catch (err) { body.className = "empty"; body.replaceChildren(`Couldn't load settings: ${err.message}`); }
  }

  // Someone changed settings.json elsewhere (lato-config, the Tools tab): follow along for rows I haven't touched.
  store.on("state", (s) => {
    if (!doc || !s?.settings) return;
    let moved = false;
    for (const spec of doc.schema) {
      const v = s.settings[spec.key];
      if (v === undefined || v === saved[spec.key]) continue;
      const untouched = draft[spec.key] === saved[spec.key];
      saved[spec.key] = v;
      if (untouched) { draft[spec.key] = v; moved = true; }
    }
    if (moved) syncAll();
  });

  root.replaceChildren(body, bar);
  window.addEventListener("beforeunload", (e) => { if (doc && changedKeys().length) { e.preventDefault(); e.returnValue = ""; } });
  return { shown() { if (!doc || !changedKeys().length) load(); } };
}
