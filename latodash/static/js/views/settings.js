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

// The chatter mix: four shares of one pie, shown as a single control that always adds up to 100%.
const MIX = [
  { key: "mix_ignore", label: "On read", tone: "muted" },
  { key: "mix_react", label: "React", tone: "info" },
  { key: "mix_gif", label: "Gif", tone: "warn" },
  { key: "mix_reply", label: "Reply", tone: "ok" },
];
const MIX_KEYS = MIX.map((m) => m.key);

// Set one share and rebalance the others so the total stays 100. Moving any share takes from / gives to "Reply"
// first, then the rest (last first); moving "Reply" itself spreads the difference over the others in proportion.
export function rebalance(values, key, want, step = 5) {
  const v = Object.fromEntries(MIX_KEYS.map((k) => [k, Math.max(0, Math.round(Number(values[k]) || 0))]));
  const total = MIX_KEYS.reduce((a, k) => a + v[k], 0);
  if (total !== 100) {                 // fix up odd values from the file first (scale, remainder to reply)
    const t = total || 1;
    for (const k of MIX_KEYS) v[k] = total ? Math.round((v[k] * 100) / t / step) * step : (k === "mix_reply" ? 100 : 0);
    v.mix_reply += 100 - MIX_KEYS.reduce((a, k) => a + v[k], 0);
  }
  want = Math.min(100, Math.max(0, Math.round(want / step) * step));
  let delta = want - v[key];           // > 0: others give, < 0: others receive
  v[key] = want;
  const others = MIX_KEYS.filter((k) => k !== key);
  if (key !== "mix_reply") {
    const order = ["mix_reply", ...others.filter((k) => k !== "mix_reply").reverse()];
    for (const k of order) {
      if (!delta) break;
      const d = delta > 0 ? Math.min(delta, v[k]) : delta;
      v[k] -= d; delta -= d;
    }
  } else {
    const pool = others.reduce((a, k) => a + v[k], 0);
    let left = delta;
    others.forEach((k, i) => {
      let d = i === others.length - 1 ? left
        : delta > 0 ? Math.round(((pool ? v[k] / pool : 1 / others.length) * delta) / step) * step
          : Math.round(((pool ? v[k] / pool : (k === "mix_ignore" ? 1 : 0)) * delta) / step) * step;
      d = delta > 0 ? Math.min(d, v[k]) : d;
      v[k] -= d; left -= d;
    });
    if (left) {                        // couldn't place it all (others hit 0): cap the share that moved
      v[key] -= left;
    }
  }
  for (const k of MIX_KEYS) v[k] = Math.max(0, v[k]);
  return v;
}

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

  function makeMix(specs) {
    const by = Object.fromEntries(specs.map((s) => [s.key, s]));
    const segs = MIX.map((m) => h("div", { class: `mix-seg tone-${m.tone}`, title: by[m.key].label }));
    const legend = MIX.map((m) => h("span", { class: "mix-key" }, h("i", { class: `tone-${m.tone}` }), m.label, h("b", { class: "num" })));
    const reset = h("button", { class: "btn ghost sm set-reset", title: "Back to default (always reply)", "aria-label": "Back to default" }, icon(ICONS.restart));
    reset.onclick = () => { for (const k of MIX_KEYS) draft[k] = by[k].default; syncAll(); };
    const sliders = MIX.map((m) => {
      const spec = by[m.key];
      const input = h("input", { type: "range", min: 0, max: 100, step: spec.step || 5, "aria-label": spec.label });
      const out = h("span", { class: "set-val" });
      input.oninput = () => { Object.assign(draft, rebalance(draft, m.key, Number(input.value), spec.step || 5)); syncAll(); };
      const row = h("div", { class: "mix-row" },
        h("div", {}, h("div", { class: "set-name" }, h("i", { class: `mix-dot tone-${m.tone}` }), spec.label), h("div", { class: "set-help" }, spec.help)),
        h("div", { class: "set-ctl" }, input, out));
      return { key: m.key, row, input, out };
    });
    const row = h("div", { class: "set-row mix" },
      h("div", { class: "mix-head" },
        h("div", {}, h("div", { class: "set-name" }, "When she overhears chatter"),
          h("div", { class: "set-help" }, "Drag any slider; the others make room so it always adds up to 100%.")),
        reset),
      h("div", { class: "mix-bar", role: "img" }, ...segs),
      h("div", { class: "mix-legend" }, ...legend),
      ...sliders.map((s) => s.row));
    return { row, sync() {
      const vals = MIX.map((m) => Number(draft[m.key]) || 0);
      const total = vals.reduce((a, b) => a + b, 0) || 1;
      MIX.forEach((m, i) => {
        const pct = Math.round((vals[i] * 100) / total);
        segs[i].style.width = `${(vals[i] * 100) / total}%`;
        segs[i].textContent = pct >= 12 ? `${m.label} ${pct}%` : "";
        legend[i].querySelector("b").textContent = `${pct}%`;
        sliders[i].input.value = vals[i];
        sliders[i].out.textContent = `${pct}%`;
        sliders[i].row.dataset.dirty = String(draft[m.key] !== saved[m.key]);
      });
      row.querySelector(".mix-bar").setAttribute("aria-label", MIX.map((m, i) => `${m.label} ${Math.round((vals[i] * 100) / total)}%`).join(", "));
      row.dataset.changed = String(MIX_KEYS.some((k) => draft[k] !== by[k].default));
      row.dataset.dirty = String(MIX_KEYS.some((k) => draft[k] !== saved[k]));
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
    status.replaceChildren(...[
      pill(v.thinking ? "Thinking on" : "Thinking off", v.thinking ? "warn" : ""),
      pill(v.sleep_mode ? "Sleep mode on" : "Always awake", v.sleep_mode ? "info" : "warn"),
      pill(!v.gifs_enabled ? "Gifs off" : v.gif_only ? "Gif mode: gifs only" : `Gifs ${v.gif_reply_chance}% of replies`, !v.gifs_enabled ? "warn" : v.gif_only ? "accent" : ""),
      v.mix_reply !== undefined && v.mix_reply < 100 ? pill(`Chatter: replies ${v.mix_reply}%`, "info") : null].filter(Boolean));
  }

  function build() {
    rows.clear();
    const cards = doc.groups.map((g) => {
      const specs = doc.schema.filter((s) => s.group === g.id);
      const mixSpecs = specs.filter((s) => MIX_KEYS.includes(s.key));
      const list = specs.filter((s) => !MIX_KEYS.includes(s.key)).map((s) => { const r = makeRow(s); rows.set(s.key, r); return r.row; });
      if (mixSpecs.length === MIX_KEYS.length) { const r = makeMix(mixSpecs); rows.set("mix", r); list.unshift(r.row); }
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
