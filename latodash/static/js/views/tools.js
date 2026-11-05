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
    const res = await run(pull, null, () => api.post(endpoint));
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
  const powerBtn = btn("Turn LatoChan off", ICONS.power);
  let botOn = null;       // last known state from the server snapshot
  powerBtn.onclick = async () => {
    const turnOn = botOn === false;
    if (!turnOn && !(await confirmDialog({ title: "Turn LatoChan off?", danger: true, confirm: "Turn off",
      body: "She goes offline and stays off (even after a server reboot) until you turn her back on here. Her chat history and notes are kept." }))) return;
    const res = await run(powerBtn, ctlOut, () => api.post("/api/tools/bot-power", { on: turnOn }), turnOn ? "LatoChan is on" : "LatoChan is off");
    if (res) { botOn = res.on; paintPower(); }
  };
  function paintPower() {
    const label = botOn === false ? "Turn LatoChan on" : "Turn LatoChan off";
    powerBtn.lastChild.textContent = label;
    powerBtn.classList.toggle("primary", botOn === false);
    powerBtn.classList.toggle("danger", botOn !== false);
    restartBot.disabled = botOn === false;
  }
  const restartOllama = btn("Restart Ollama", ICONS.restart);
  const wakeBtn = btn("Wake server", ICONS.power, "sm");
  wakeBtn.title = "Wake-on-LAN: powers the server on if it is shut down (needs [wake] mac in config.toml)";
  wakeBtn.onclick = async () => {
    await run(wakeBtn, ctlOut, () => api.post("/api/tools/wake"), "Wake packet sent");
  };
  const thinkInput = h("input", { type: "checkbox" });
  const thinkSwitch = h("label", { class: "switch" }, thinkInput, h("span", { class: "track" }),
    h("span", {}, h("b", {}, "Thinking mode"), h("div", { class: "muted small" }, "slower: ~30–90 s per reply")));
  restartBot.onclick = async () => {
    if (!(await confirmDialog({ title: "Restart LatoChan?", danger: true, confirm: "Restart",
      body: "She goes offline for ~10 s. Chat history is kept (LatoChan 2.0 saves it)." }))) return;
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
  const control = card("LatoChan control", "On/off, Restart.bat, thinking toggle.bat. More knobs are in the Settings tab.",
    status, h("div", { class: "row section-gap" }, powerBtn, restartBot, restartOllama, wakeBtn),
    h("div", { class: "section-gap" }, thinkSwitch), ctlOut);

  // ---- models
  const modelList = h("div", { class: "list" }, h("div", { class: "empty" }, "Loading…"));
  const refreshModels = btn("Refresh", null, "sm");
  const liveName = h("code", {}, "…");
  const NOTES = {
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
          body: "Loading another model can evict models already in GPU memory." }))) return;
        const res = await run(b, null, () => api.post(isLoaded ? "/api/models/unload" : "/api/models/load", { model: m.name }));
        if (res) { toast(isLoaded ? `${m.name} unloaded` : `${m.name} loaded`, res.seconds ? `${res.seconds}s` : ""); setTimeout(loadModels, 1200); }
      };
      let use = null;
      if (isChat && !isLive) {
        use = btn("Use for LatoChan", ICONS.brain, "sm primary");
        use.onclick = async () => {
          if (!(await confirmDialog({ title: `Switch LatoChan to ${base(m.name)}?`, confirm: "Switch & restart",
            body: `She restarts on ${base(m.name)} (about 10 s offline; chat history is kept). The first reply after that also loads the model (~40–90 s). You can switch back here any time.` }))) return;
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
  const sayText = h("textarea", { class: "textarea", rows: 4, maxlength: 2000, placeholder: "Message to post as LatoChan (<@id> pings someone; never @everyone/@here)" });
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
  const say = card("Post as LatoChan", "Same as sudo lato-say on the server. <@id> pings that person; @everyone/@here never work.",
    sayText, h("div", { class: "row section-gap" }, h("div", { class: "field", style: { flex: 1 } }, sayChan), sayCount, sayBtn), sayOut);

  // ---- reminders (the pinned "LatoChan state" message; LatoChan pings you when one is due)
  const remList = h("div", { class: "list" }, h("div", { class: "empty" }, "Loading…"));
  const remRefresh = btn("Refresh", null, "sm");
  const remText = h("input", { class: "input", maxlength: 300, placeholder: "Remind me to…" });
  const remWhen = h("input", { class: "input", type: "datetime-local", title: "Server time; leave empty for no time" });
  const remAdd = btn("Add reminder", ICONS.check, "primary");
  const remOut = h("pre", { class: "output" });
  const fmtWhen = (w) => w ? new Date(w).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "no time";
  function renderReminders(list) {
    if (!list.length) { remList.replaceChildren(h("div", { class: "empty" }, "No reminders.")); return; }
    remList.replaceChildren(...list.map((r) => {
      const status = r.done ? pill("done", "ok") : r.sent ? pill("sent", "") : r.when ? pill("pending", "accent") : pill("no time", "");
      const doneBtn = btn(r.done ? "Undo" : "Done", r.done ? null : ICONS.check, "sm");
      doneBtn.onclick = async () => { const res = await run(doneBtn, remOut, () => api.post("/api/reminders", { op: "done", id: r.id, done: !r.done })); if (res) renderReminders(res.reminders); };
      const del = btn("Delete", ICONS.trash, "sm danger");
      del.onclick = async () => {
        if (!(await confirmDialog({ title: "Delete this reminder?", danger: true, confirm: "Delete", body: `“${r.text.slice(0, 160)}”` }))) return;
        const res = await run(del, remOut, () => api.post("/api/reminders", { op: "remove", id: r.id }), "Reminder deleted");
        if (res) renderReminders(res.reminders);
      };
      const when = h("input", { class: "input", type: "datetime-local", value: r.when || "", title: "change the time (server time); clear it for no time", style: { maxWidth: "210px" } });
      when.onchange = async () => {
        const res = await run(when, remOut, () => api.post("/api/reminders", { op: "update", id: r.id, when: when.value || null }), when.value ? `Moved to ${fmtWhen(when.value)}` : "Time removed");
        if (res) renderReminders(res.reminders);
      };
      return h("div", { class: "list-row", style: { gridTemplateColumns: "1fr auto" } },
        h("div", {}, h("div", { style: { textDecoration: r.done ? "line-through" : "none" } }, h("b", {}, r.text)),
          h("div", { class: "muted small" }, fmtWhen(r.when))),
        h("div", { class: "row", style: { gap: "6px" } }, status, when, doneBtn, del));
    }));
  }
  async function loadReminders() {
    try { renderReminders((await withBusy(remRefresh, () => api.get("/api/reminders"))).reminders || []); }
    catch (err) { remList.replaceChildren(h("div", { class: "empty" }, err.message)); }
  }
  remRefresh.onclick = loadReminders;
  remAdd.onclick = async () => {
    const text = remText.value.trim();
    if (!text) { remText.focus(); return; }
    const res = await run(remAdd, remOut, () => api.post("/api/reminders", { op: "add", text, when: remWhen.value || null }),
      remWhen.value ? `Reminder set for ${fmtWhen(remWhen.value)}` : "Reminder added (no time)");
    if (res) { remText.value = ""; remWhen.value = ""; renderReminders(res.reminders); }
  };
  remText.addEventListener("keydown", (e) => { if (e.key === "Enter") remAdd.click(); });
  const remindersCard = h("div", { class: "card tool-card" },
    h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), "Reminders"), remRefresh),
    h("p", { class: "desc" }, "LatoChan pings you in the main channel when one is due (checked every 2 min). Times use the server-configured timezone. Same list as the pinned 📋 message too."),
    remList, h("div", { class: "row section-gap" }, h("div", { class: "field", style: { flex: 2 } }, remText),
      h("div", { class: "field", style: { flex: 1 } }, remWhen), remAdd), remOut);

  // ---- lights (lato-lights on the server; local LAN control, no cloud)
  const SWATCH = { red: "#ff3030", orange: "#ff8a1f", yellow: "#ffd23f", green: "#2ee86a", cyan: "#20e3ff", blue: "#3d6bff",
    purple: "#9b4dff", pink: "#ff5fb8", magenta: "#ff2bff", warm: "#ffc27a", white: "#fff6e8", cool: "#d8ecff" };
  const lightBody = h("div", { class: "list" }, h("div", { class: "empty" }, "Loading…"));
  const lightsRefresh = btn("Refresh", null, "sm");
  const lightOut = h("pre", { class: "output" });
  async function lightDo(button, device, change, label) {
    const res = await run(button, null, () => api.post("/api/lights", { device, ...change }));
    if (res) { lightOut.textContent = ""; renderLights([{ name: device, state: res.state }], true); toast(label || "Light updated", device); }
  }
  let lightsDoc = { devices: [] };
  function renderLights(devs, partial = false) {
    if (partial) lightsDoc.devices = lightsDoc.devices.map((d) => devs.find((x) => x.name === d.name) ? { ...d, ...devs.find((x) => x.name === d.name), error: undefined } : d);
    else lightsDoc = devs;
    const list = lightsDoc.devices || [];
    if (!lightsDoc.has_key && !partial) { lightBody.replaceChildren(h("div", { class: "empty" }, "Not set up yet: no lamp key on the server (see LIGHTS-SETUP.md).")); return; }
    if (!list.length) { lightBody.replaceChildren(h("div", { class: "empty" }, "No lights yet: sudo lato-lights add NAME IP")); return; }
    lightBody.replaceChildren(...list.map((d) => {
      const st = d.state;
      const on = btn("On", null, "sm" + (st?.on ? " primary" : "")); on.onclick = () => lightDo(on, d.name, { power: "on" }, "Light on");
      const off = btn("Off", null, "sm" + (st && !st.on ? " primary" : "")); off.onclick = () => lightDo(off, d.name, { power: "off" }, "Light off");
      const slider = h("input", { type: "range", min: 1, max: 100, step: 1, value: st?.brightness || 50, style: { flex: 1 } });
      const pct = h("span", { class: "muted small num" }, `${st?.brightness ?? "?"}%`);
      slider.oninput = () => { pct.textContent = `${slider.value}%`; };
      slider.onchange = () => lightDo(slider, d.name, { brightness: Number(slider.value) }, `Brightness ${slider.value}%`);
      const swatch = (name, key) => {
        const b = h("button", { class: "btn sm", title: name, "aria-label": name,
          style: { background: SWATCH[name], width: "28px", height: "28px", padding: 0, borderRadius: "50%", border: "2px solid var(--line)",
            forcedColorAdjust: "none" } });   // swatch colours are data, not theme: never let high-contrast modes recolour them
        b.onclick = () => lightDo(b, d.name, { [key]: name }, name);
        return b;
      };
      // white light presets: a labelled button each (a bare circle doesn't say "warm/white/cool")
      const whiteBtn = (name) => {
        const b = h("button", { class: "btn sm", title: `${name} white light` },
          h("span", { style: { display: "inline-block", width: "12px", height: "12px", borderRadius: "50%", background: SWATCH[name],
            border: "1px solid var(--line)", marginRight: "6px", verticalAlign: "-1px", forcedColorAdjust: "none" } }),
          name[0].toUpperCase() + name.slice(1));
        b.onclick = () => lightDo(b, d.name, { temperature: name }, `${name} white`);
        return b;
      };
      return h("div", { class: "list-row", style: { gridTemplateColumns: "1fr", gap: "8px" } },
        h("div", { class: "row" }, h("b", {}, d.name), h("span", { class: "muted small num" }, d.host || ""),
          h("span", { class: "spacer" }), d.error ? pill(d.error, "crit") : pill(st?.on ? "on" : "off", st?.on ? "ok" : ""), on, off),
        h("div", { class: "row" }, h("span", { class: "muted small" }, "Brightness"), slider, pct),
        h("div", { class: "row", style: { flexWrap: "wrap", gap: "6px" } },
          ...(lightsDoc.colors || Object.keys(SWATCH).slice(0, 9)).map((c) => swatch(c, "color"))),
        h("div", { class: "row", style: { gap: "6px" } },
          ...(lightsDoc.temperatures || ["warm", "white", "cool"]).map((t) => whiteBtn(t))));
    }));
  }
  async function loadLights() {
    try { renderLights(await withBusy(lightsRefresh, () => api.get("/api/lights"))); }
    catch (err) { lightBody.replaceChildren(h("div", { class: "empty" }, err.message)); }
  }
  lightsRefresh.onclick = loadLights;
  const lightsCard = h("div", { class: "card tool-card" },
    h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), "Lights"), lightsRefresh),
    h("p", { class: "desc" }, "Smart lights on your LAN, controlled locally (no cloud). Same as lato-lights on the server."),
    lightBody, lightOut);

  // ---- friends (lato-friends on the server; live on her next message)
  const friendList = h("div", { class: "list" }, h("div", { class: "empty" }, "Loading…"));
  const friendCount = h("span", { class: "muted small num" }, "");
  const fId = h("input", { class: "input", inputmode: "numeric", maxlength: 20, placeholder: "Discord user ID" });
  const fName = h("input", { class: "input", maxlength: 32, placeholder: "name (optional)" });
  const fAdd = btn("Add friend", ICONS.check, "primary");
  const fOut = h("pre", { class: "output" });
  function renderFriends(list) {
    const nt = list.filter((x) => x.trusted).length;
    friendCount.textContent = `${list.length} friend${list.length === 1 ? "" : "s"}${nt ? ` · ${nt} trusted` : ""}`;
    if (!list.length) { friendList.replaceChildren(h("div", { class: "empty" }, "No friends yet: everyone but you is a stranger to her.")); return; }
    friendList.replaceChildren(...list.map((fr) => {
      const rm = btn("Remove", ICONS.trash, "sm danger");
      rm.onclick = async () => {
        if (!(await confirmDialog({ title: `Remove ${fr.name || fr.id}?`, danger: true, confirm: "Remove",
          body: "She'll treat them as a stranger from her next message on (brushes them off, no notes, no gifs). You can add them back any time." }))) return;
        const res = await run(rm, fOut, () => api.post("/api/friends", { op: "remove", id: fr.id }), `${fr.name || fr.id} removed`);
        if (res) renderFriends(res.friends);
      };
      const trustInput = h("input", { type: "checkbox", checked: !!fr.trusted, "aria-label": `${fr.name || fr.id} is trusted` });
      trustInput.onchange = async () => {
        const op = trustInput.checked ? "trust" : "untrust";
        const res = await run(trustInput, fOut, () => api.post("/api/friends", { op, id: fr.id }),
          `${fr.name || fr.id} ${op === "trust" ? "is now trusted" : "is no longer trusted"}`);
        if (res) renderFriends(res.friends); else trustInput.checked = !trustInput.checked;
      };
      const trustSw = h("label", { class: "switch", title: "Trusted friends may ask for more than chat (e.g. the lights). Each feature's \"who\" setting decides." },
        trustInput, h("span", { class: "track" }), h("span", { class: "small" }, "Trusted"));
      return h("div", { class: "list-row", style: { gridTemplateColumns: "1fr auto auto auto" } },
        h("span", {}, h("b", {}, fr.name || "(name appears within ~10 min)")),
        h("span", { class: "muted small num" }, fr.id), trustSw, rm);
    }));
  }
  async function loadFriends() {
    try { renderFriends((await api.get("/api/friends")).friends || []); }
    catch (err) { friendList.replaceChildren(h("div", { class: "empty" }, err.message)); }
  }
  fAdd.onclick = async () => {
    const id = fId.value.trim();
    if (!/^\d{17,20}$/.test(id)) { toast("That isn't a Discord user ID", "17–20 digits. Discord: Settings → Advanced → Developer Mode, then right-click them → Copy User ID.", "error", 7000); fId.focus(); return; }
    const res = await run(fAdd, fOut, () => api.post("/api/friends", { op: "add", id, name: fName.value.trim() }), "Friend added");
    if (res) { fId.value = ""; fName.value = ""; renderFriends(res.friends); }
  };
  const friendsCard = card("Friends", "People she chats with normally; everyone else is a stranger she brushes off. Trusted friends may also ask for things like the lights (each feature's \"who\" setting in Settings decides). Live on her next message, no restart. Same as lato-friends on the server.",
    friendList, h("div", { class: "row section-gap" }, h("div", { class: "field", style: { flex: 1 } }, fId),
      h("div", { class: "field", style: { flex: 1 } }, fName), fAdd), h("div", { class: "row" }, friendCount), fOut);

  // ---- editors
  const sizesBtn = btn("Prompt sizes", ICONS.list);
  const persona = editorCard({
    title: "Persona", endpoint: "/api/persona", pullLabel: "Pull persona", pushLabel: "Push persona",
    sub: `Download / Upload persona .bat — edits ${store.config?.persona_path || "the persona file"}: who she is, no [[tags]] (the bot adds its rules and each enabled module's text). Live on her next message, no restart.`,
    confirmBody: "Replaces the persona on the server. The current version is kept as a timestamped .bak file.", extra: [sizesBtn],
  });
  sizesBtn.onclick = () => run(sizesBtn, persona.out, () => api.get("/api/prompt"));
  const listBtn = btn("List notes", ICONS.list);
  const memory = editorCard({
    title: "Memory", endpoint: "/api/memory", pullLabel: "Pull memory", pushLabel: "Push memory",
    sub: "Download / Upload memory .bat — her per-person notes (lato-memory export/import). Pull a fresh copy before editing. A stale export is rejected.",
    confirmBody: "Imports this file into memory.db. The server backs up the database first (keeps the last 5).", extra: [listBtn],
  });
  listBtn.onclick = () => run(listBtn, memory.out, () => api.get("/api/memory/list"));

  window.addEventListener("beforeunload", (e) => { if (persona.isDirty() || memory.isDirty()) { e.preventDefault(); e.returnValue = ""; } });

  root.replaceChildren(h("div", { class: "grid g-3" }, control, models, say), h("div", { class: "grid g-2" }, friendsCard, lightsCard),
    h("div", { class: "grid" }, remindersCard),
    h("div", { class: "grid g-2" }, persona.el, memory.el));

  function renderStatus(s) {
    const cfg = store.config || {};
    const bot = s?.services?.[cfg.bot_unit], oll = s?.services?.[cfg.ollama_unit];
    const tone = (x) => (x?.active === "active" ? "ok" : x ? "crit" : "");
    if (bot?.active && !powerBtn.disabled) { botOn = bot.active === "active" || bot.active === "activating"; paintPower(); }
    status.replaceChildren(pill(`LatoChan ${bot?.active || "?"}`, tone(bot)), pill(`Ollama ${oll?.active || "?"}`, tone(oll)),
      bot?.since ? h("span", { class: "muted small" }, `up ${f.duration(store.serverNow() - bot.since)}`) : null);
    if (!thinkInput.disabled && s?.settings && document.activeElement !== thinkInput) thinkInput.checked = !!s.settings.thinking;
  }
  store.on("state", renderStatus);
  let modelsLoaded = false;
  return { shown() { if (!modelsLoaded) { modelsLoaded = true; loadModels(); } loadFriends(); loadLights(); loadReminders(); } };
}
