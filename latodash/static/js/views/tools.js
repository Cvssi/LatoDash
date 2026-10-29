// Tools: the LatoChan .bat files as buttons, plus persona/memory editors and model control.
import { store } from "../store.js";
import { api } from "../api.js";
import { h, icon, ICONS, toast, confirmDialog, withBusy, pill } from "../ui.js";
import * as f from "../fmt.js";

function card(title, sub, ...body) {
  return h("div", { class: "card tool-card" },
    h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), title)),
    sub ? h("p", { class: "desc" }, sub) : null, ...body);
}

const btn = (label, ic, cls = "") => h("button", { class: `btn ${cls}` }, ic ? icon(ic) : null, label);

async function run(button, out, fn, okTitle) {
  try {
    const res = await withBusy(button, fn);
    if (out) out.textContent = res?.output ?? "";
    if (okTitle) toast(okTitle, typeof res?.output === "string" ? res.output.split("\n")[0].slice(0, 120) : "");
    return res;
  } catch (err) {
    if (out) out.textContent = [err.message, err.output].filter(Boolean).join("\n\n");
    toast("Failed", err.message, "error", 7000);
    return null;
  }
}

/** A text editor bound to a server file with Pull / Push. */
function editorCard({ title, sub, endpoint, pullLabel, pushLabel, confirmBody, extra = [] }) {
  const area = h("textarea", { class: "textarea editor", spellcheck: "false", placeholder: `Press "${pullLabel}" to load the server copy` });
  const meta = h("span", { class: "editor-meta" }, "not loaded");
  const out = h("pre", { class: "output" });
  const pull = btn(pullLabel, ICONS.download);
  const push = btn(pushLabel, ICONS.upload, "primary");
  let loaded = null;
  const updateMeta = (note) => {
    const dirty = loaded !== null && area.value !== loaded;
    meta.replaceChildren(loaded === null ? "not loaded" : `${f.num(area.value.length)} chars`, note ? ` · ${note}` : "",
      dirty ? h("span", { class: "dirty" }, " · unsaved changes") : "");
    push.disabled = loaded === null || !dirty;
  };
  area.addEventListener("input", () => updateMeta());
  pull.onclick = async () => {
    if (loaded !== null && area.value !== loaded &&
        !(await confirmDialog({ title: "Discard your edits?", body: "Pulling replaces the editor with the server copy.", confirm: "Discard & pull", danger: true }))) return;
    const res = await run(pull, null, () => api.get(endpoint));
    if (!res) return;
    loaded = res.content;
    area.value = res.content;
    out.textContent = "";
    updateMeta(`pulled ${f.time(Date.now() / 1000)}${res.saved_to ? " · PC copy updated" : ""}`);
    toast(`${title} downloaded`, res.saved_to ? `saved to ${res.saved_to}` : "");
  };
  push.onclick = async () => {
    if (!(await confirmDialog({ title: `${pushLabel}?`, body: confirmBody, confirm: pushLabel }))) return;
    const res = await run(push, out, () => api.put(endpoint, { content: area.value }));
    if (!res) return;
    loaded = area.value;
    updateMeta(`pushed ${f.time(Date.now() / 1000)}`);
    out.textContent = res.backup ? `Server backup: ${res.backup}` : res.output || "";
    toast(`${title} uploaded`, res.backup ? "old version kept as a .bak file" : "live on her next message");
  };
  area.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); if (!push.disabled) push.click(); }
    if (e.key === "Tab") { e.preventDefault(); area.setRangeText("  ", area.selectionStart, area.selectionEnd, "end"); updateMeta(); }
  });
  updateMeta();
  const el = card(title, sub, h("div", { class: "row" }, pull, push, ...extra, h("span", { class: "spacer" }), meta),
    h("div", { class: "section-gap" }, area), out);
  return { el, isDirty: () => loaded !== null && area.value !== loaded, out };
}

export function mount(root) {
  // ---- bot control
  const ctlOut = h("pre", { class: "output" });
  const status = h("div", { class: "row" });
  const restartBot = btn("Restart LatoChan", ICONS.restart, "danger");
  const restartOllama = btn("Restart Ollama", ICONS.restart);
  const thinkInput = h("input", { type: "checkbox" });
  const thinkSwitch = h("label", { class: "switch" }, thinkInput, h("span", { class: "track" }),
    h("span", {}, h("b", {}, "Thinking mode"), h("div", { class: "muted small" }, "slower: ~30–90 s per reply")));
  restartBot.onclick = async () => {
    if (!(await confirmDialog({ title: "Restart LatoChan?", danger: true, confirm: "Restart",
      body: "She goes offline for ~10 s and forgets the in-memory chat history (last 12 exchanges per channel)." }))) return;
    await run(restartBot, ctlOut, () => api.post("/api/tools/restart-bot"), "LatoChan restarted");
  };
  restartOllama.onclick = async () => {
    if (!(await confirmDialog({ title: "Restart Ollama?", danger: true, confirm: "Restart Ollama",
      body: "Unloads the model. Her next reply will include a cold load (~40 s)." }))) return;
    await run(restartOllama, ctlOut, () => api.post("/api/tools/restart-ollama"), "Ollama restarted");
  };
  thinkInput.onchange = async () => {
    const want = thinkInput.checked ? "on" : "off";
    thinkInput.disabled = true;
    try {
      const res = await api.post("/api/tools/thinking", { mode: want });
      thinkInput.checked = res.thinking;
      ctlOut.textContent = res.output;
      toast(`Thinking mode ${res.thinking ? "ON" : "OFF"}`, "applies to her next message");
    } catch (err) {
      thinkInput.checked = !thinkInput.checked;
      toast("Couldn't change thinking mode", err.message, "error");
    } finally { thinkInput.disabled = false; }
  };
  const control = card("LatoChan control", "Restart the bot or Ollama and toggle thinking mode. More knobs are in the Settings tab.",
    status, h("div", { class: "row section-gap" }, restartBot, restartOllama),
    h("div", { class: "section-gap" }, thinkSwitch), ctlOut);

  // ---- models
  const modelList = h("div", { class: "list" }, h("div", { class: "empty" }, "Loading…"));
  const refreshModels = btn("Refresh", null, "sm");
  const liveName = h("code", {}, "…");
  const NOTES = {  // optional per-model notes shown in the list
    "nomic-embed-text": "embeddings for memory search (not a chat model)",
  };
  const base = (n) => n.replace(/:latest$/, "");
  async function loadModels() {
    try {
      const res = await withBusy(refreshModels, () => api.get("/api/models"));
      renderModels(res.models, res.live);
    } catch (err) { modelList.replaceChildren(h("div", { class: "empty" }, err.message)); }
  }
  refreshModels.onclick = loadModels;
  function renderModels(models, live) {
    liveName.textContent = live || "?";
    const loaded = new Set((store.state?.ollama?.loaded || []).map((m) => m.name));
    modelList.replaceChildren(...models.map((m) => {
      const isLoaded = loaded.has(m.name);
      const isLive = base(m.name) === base(live || "");
      const isChat = !/embed/.test(m.name);
      const b = btn(isLoaded ? "Unload" : "Load", isLoaded ? ICONS.eject : ICONS.play, "sm");
      b.onclick = async () => {
        if (!isLoaded && loaded.size && !(await confirmDialog({ title: `Load ${m.name}?`, confirm: "Load",
          body: "The Vega has 8 GB. Loading another model will push the current one out, and LatoChan's next reply will reload hers (~40 s)." }))) return;
        const res = await run(b, null, () => api.post(isLoaded ? "/api/models/unload" : "/api/models/load", { model: m.name }));
        if (res) { toast(isLoaded ? `${m.name} unloaded` : `${m.name} loaded`, res.seconds ? `${res.seconds}s` : ""); setTimeout(loadModels, 1200); }
      };
      let use = null;
      if (isChat && !isLive) {
        use = btn("Use for LatoChan", ICONS.brain, "sm primary");
        use.onclick = async () => {
          if (!(await confirmDialog({ title: `Switch LatoChan to ${base(m.name)}?`, confirm: "Switch & restart",
            body: `She restarts on ${base(m.name)} (about 10 s offline, and she forgets the last few messages of chat history). The first reply after that also loads the model (~40–90 s). You can switch back here any time.` }))) return;
          const res = await run(use, null, () => api.post("/api/models/activate", { model: base(m.name) }), "LatoChan switched");
          if (res) setTimeout(loadModels, 1500);
        };
      }
      const details = m.details || m;
      return h("div", { class: "list-row model-row" },
        h("div", {}, h("div", { class: "model-name" }, m.name),
          h("div", { class: "model-meta" }, [NOTES[base(m.name)], details.parameter_size || details.params, details.quantization_level || details.quant, f.bytes(m.size)].filter(Boolean).join(" · "))),
        h("div", { class: "row" }, isLive ? pill("LatoChan's brain", "accent") : null, isLoaded ? pill("in VRAM", "ok") : null,
          h("span", { class: "spacer" }), use, b));
    }));
  }
  const models = h("div", { class: "card tool-card" },
    h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), "Models"), refreshModels),
    h("p", { class: "desc" }, "LatoChan runs on ", liveName, ". Switch her brain, or load and unload models in VRAM."),
    modelList);

  // ---- say
  const sayText = h("textarea", { class: "textarea", rows: 4, maxlength: 2000, placeholder: "Message to post as LatoChan (pings only you)" });
  const sayChan = h("input", { class: "input", inputmode: "numeric", placeholder: "main channel" });
  const sayCount = h("span", { class: "muted small num" }, "0 / 2000");
  const sayBtn = btn("Post as LatoChan", ICONS.send, "primary");
  const sayOut = h("pre", { class: "output" });
  sayText.addEventListener("input", () => { sayCount.textContent = `${sayText.value.length} / 2000`; });
  sayBtn.onclick = async () => {
    const text = sayText.value.trim();
    if (!text) { sayText.focus(); return; }
    if (!(await confirmDialog({ title: "Post to Discord?", confirm: "Post", body: `LatoChan will say: “${text.slice(0, 200)}${text.length > 200 ? "…" : ""}”` }))) return;
    const res = await run(sayBtn, sayOut, () => api.post("/api/tools/say", { text, channel: sayChan.value.trim() || null }), "Posted");
    if (res) sayText.value = "";
  };
  const say = card("Post as LatoChan", "Same as sudo lato-say on the server. Mentions ping only you.",
    sayText, h("div", { class: "row section-gap" }, h("div", { class: "field", style: { flex: 1 } }, sayChan), sayCount, sayBtn), sayOut);

  // ---- editors
  const persona = editorCard({
    title: "Persona", endpoint: "/api/persona", pullLabel: "Pull persona", pushLabel: "Push persona",
    sub: `Download / Upload persona .bat — edits ${store.config?.persona_path || "the persona file"}. Live on her next message, no restart.`,
    confirmBody: "Replaces the persona on the server. The current version is kept as a timestamped .bak file.",
  });
  const listBtn = btn("List notes", ICONS.list);
  const memory = editorCard({
    title: "Memory", endpoint: "/api/memory", pullLabel: "Pull memory", pushLabel: "Push memory",
    sub: "Download / Upload memory .bat — her per-person notes (lato-memory export/import). Push is rejected if notes changed after your pull. Preserve edits, pull again, then merge.",
    confirmBody: "Imports this file into memory.db. The server retains a private backup. Stale snapshots are rejected without changing notes.", extra: [listBtn],
  });
  listBtn.onclick = () => run(listBtn, memory.out, () => api.get("/api/memory/list"));

  window.addEventListener("beforeunload", (e) => { if (persona.isDirty() || memory.isDirty()) { e.preventDefault(); e.returnValue = ""; } });

  root.replaceChildren(h("div", { class: "grid g-3" }, control, models, say), h("div", { class: "grid g-2" }, persona.el, memory.el));

  function renderStatus(s) {
    const cfg = store.config || {};
    const bot = s?.services?.[cfg.bot_unit], oll = s?.services?.[cfg.ollama_unit];
    const tone = (x) => (x?.active === "active" ? "ok" : x ? "crit" : "");
    status.replaceChildren(pill(`LatoChan ${bot?.active || "?"}`, tone(bot)), pill(`Ollama ${oll?.active || "?"}`, tone(oll)),
      bot?.since ? h("span", { class: "muted small" }, `up ${f.duration(store.serverNow() - bot.since)}`) : null);
    if (!thinkInput.disabled && s?.settings && document.activeElement !== thinkInput) thinkInput.checked = !!s.settings.thinking;
  }
  store.on("state", renderStatus);
  let modelsLoaded = false;
  return { shown() { if (!modelsLoaded) { modelsLoaded = true; loadModels(); } } };
}
