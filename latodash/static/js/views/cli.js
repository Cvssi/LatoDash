// Brain CLI: the live view from "LatoChan brain.bat" (lato-watch) plus direct, streamed access to the model.
import { store } from "../store.js";
import { api } from "../api.js";
import { h, confirmDialog } from "../ui.js";
import * as f from "../fmt.js";

const MAX_LINES = 3000;
const HISTORY_KEY = "latodash.cli.history";
const COMMANDS = {
  "/help": "show this help",
  "/clear": "clear the screen",
  "/reset": "start a new conversation with the model",
  "/model": "/model <name> — talk to another model (no name: show current)",
  "/models": "list installed models",
  "/persona": "/persona on|off — use LatoChan's persona as the system prompt",
  "/think": "/think on|off — model thinking for CLI questions",
  "/temp": "/temp <0-2> — sampling temperature (blank = model default)",
  "/ollama": "/ollama on|off — also show Ollama runner lines in the stream",
  "/say": "/say <text> — post to Discord as LatoChan (asks first)",
  "/mem": "/mem [name] — list her memory notes",
  "/thinking": "/thinking on|off|toggle — the BOT's thinking mode",
  "/restart": "restart the LatoChan service (asks first)",
  "/status": "service, model and speed summary",
  "/history": "show this conversation",
  "/stop": "stop the current answer (also Esc)",
};

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]").slice(-200); } catch { return []; }
}
function saveHistory(hist) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(hist.slice(-200))); } catch { /* storage unavailable */ }
}

export function mount(root) {
  const opts = { model: null, persona: true, think: false, temperature: null, showOllama: false };
  let messages = [];
  let abort = null;
  let held = [];
  const history = loadHistory();
  let histIdx = history.length;

  const body = h("div", { class: "term-body", tabindex: "0" });
  const input = h("input", { type: "text", autocomplete: "off", spellcheck: "false", "aria-label": "CLI input",
    placeholder: "ask the model anything, or /help" });
  const promptEl = h("span", { class: "prompt" }, "user@server ›");
  const hint = h("span", { class: "hint" }, "Enter send · ↑↓ history · Tab complete · Esc stop");
  const headTitle = h("span", {}, "lato-watch — live brain");

  const modelSel = h("select", { class: "select", "aria-label": "Model" });
  const sw = (label, checked, onchange) => {
    const i = h("input", { type: "checkbox" });
    i.checked = checked;
    i.onchange = () => onchange(i.checked);
    return { el: h("label", { class: "switch" }, i, h("span", { class: "track" }), h("span", { class: "small" }, label)), i };
  };
  const swPersona = sw("Persona", true, (v) => { opts.persona = v; sys(`persona ${v ? "on" : "off"}`); });
  const swThink = sw("Think", false, (v) => { opts.think = v; sys(`thinking ${v ? "on" : "off"} for CLI questions`); });
  const swOllama = sw("Ollama lines", false, (v) => { opts.showOllama = v; sys(`ollama lines ${v ? "shown" : "hidden"}`); });
  modelSel.onchange = () => { opts.model = modelSel.value; sys(`model → ${opts.model}`); };

  root.replaceChildren(
    h("div", { class: "cli-bar" }, modelSel, swPersona.el, swThink.el, swOllama.el, h("span", { class: "spacer" }),
      h("button", { class: "btn sm", onclick: () => run("/reset") }, "New conversation"),
      h("button", { class: "btn sm ghost", onclick: () => run("/clear") }, "Clear")),
    h("div", { class: "term" },
      h("div", { class: "term-head" }, h("span", { class: "lights" }, h("i"), h("i"), h("i")), headTitle),
      body,
      h("div", { class: "term-input" }, promptEl, input, hint)),
  );
  root.addEventListener("click", (e) => { if (!window.getSelection()?.toString() && e.target.closest(".term")) input.focus(); });

  // ---- output helpers
  const nearBottom = () => body.scrollHeight - body.scrollTop - body.clientHeight < 60;
  function line(...parts) {
    const stick = nearBottom();
    const el = h("div", { class: "t-line" }, ...parts);
    body.append(el);
    while (body.childElementCount > MAX_LINES) body.firstChild.remove();
    if (stick) body.scrollTop = body.scrollHeight;
    return el;
  }
  const span = (cls, text) => h("span", { class: cls }, text);
  const sys = (text, cls = "t-sys") => line(span("t-dash", "» "), span(cls, text));

  // ---- live brain stream (same colours as lato-watch)
  const KIND_CLASS = { in: "t-in", raw: "t-raw", think: "t-think", replied: "t-ok", quiet: "t-ok", memory: "t-think" };
  const LABEL = { in: "IN  ", raw: "RAW ", think: "THINK", replied: "", quiet: "" };
  function logLine(e) {
    const cls = e.level === "error" ? "t-err" : e.level === "warn" ? "t-warn" : KIND_CLASS[e.kind] || (e.source === "dashboard" ? "t-dash" : "t-sys");
    let text = e.text;
    if (e.source === "bot" && LABEL[e.kind] !== undefined && LABEL[e.kind]) text = text.replace(/^(IN {3}|RAW {2}|THINK|FIRE)\s*/, `${LABEL[e.kind]} `);
    const src = e.source === "bot" ? "" : `[${e.source === "system" ? e.unit.replace(/\.(service|timer)$/, "") : e.source}] `;
    return line(span("t-time", `${f.time(e.ts)} `), span(cls, src + text));
  }
  const wanted = (e) => e.source === "bot" || (e.source === "dashboard" && e.kind !== "cli") || e.source === "kernel"
    || (e.source === "system" && e.level !== "info")
    || (opts.showOllama && e.source === "ollama" && (e.level !== "debug" || ["progress", "new_prompt", "task_start", "stop"].includes(e.kind)));
  store.on("log", (e) => {
    if (!wanted(e)) return;
    if (abort) held.push(e); else logLine(e);
  });

  function banner() {
    sys("connected to the brain. lines below are what LatoChan sees and decides, live.", "t-dim");
    sys("type a question to talk to the model directly, or /help for commands.", "t-dim");
    const recent = store.logs.filter(wanted).slice(-25);
    if (recent.length) { line(span("t-dim", "── last lines ──")); recent.forEach(logLine); line(span("t-dim", "── live ──")); }
  }

  // ---- model selector
  async function refreshModels(print = false) {
    try {
      const res = await api.get("/api/models");
      const names = res.models.map((m) => m.name);
      const def = res.live || store.state?.settings?.live_model || store.config?.default_model || "latochan";
      const current = opts.model || names.find((n) => n.split(":")[0] === def) || names[0];
      modelSel.replaceChildren(...names.map((n) => h("option", { value: n }, n)));
      modelSel.value = current;
      opts.model = current;
      if (print) {
        const loaded = new Set((res.loaded || []).map((m) => m.name));
        names.forEach((n) => line(span("t-sys", `  ${loaded.has(n) ? "●" : "○"} ${n}`), n === opts.model ? span("t-ok", "  ← current") : ""));
      }
    } catch (err) {
      if (print) sys(`couldn't list models: ${err.message}`, "t-err");
    }
  }

  // ---- talking to the model
  async function ask(text) {
    messages.push({ role: "user", content: text });
    if (messages.length > 40) messages = messages.slice(-40);
    line(span("t-you", "you › "), span("t-ai", text));
    const out = line(span("t-bot", `${(opts.model || "model").replace(":latest", "")} › `));
    const thinkingEl = h("span", { class: "t-thinking" });
    const textEl = h("span", { class: "t-ai" });
    const cursor = h("span", { class: "t-cursor" });
    out.append(thinkingEl, textEl, cursor);
    let reply = "";
    let thoughts = "";
    abort = new AbortController();
    promptEl.textContent = "generating… (Esc to stop)";
    const t0 = performance.now();
    let first = null;
    try {
      await api.chat({ messages, model: opts.model, persona: opts.persona, think: opts.think,
        temperature: opts.temperature, max_tokens: 1024 }, (ev) => {
        const stick = nearBottom();
        if (ev.type === "thinking") { thoughts += ev.text; thinkingEl.textContent = `(thinking) ${thoughts}\n`; }
        else if (ev.type === "token") { if (first == null) first = performance.now(); reply += ev.text; textEl.textContent = reply; }
        else if (ev.type === "done") {
          const s = ev.stats;
          line(span("t-dim", `  ─ ${s.gen_tokens} tok · ${s.gen_tps?.toFixed(1) ?? "—"} tok/s · prompt ${s.prompt_tokens} tok @ ${s.prompt_tps?.toFixed(0) ?? "—"} tok/s`
            + `${s.load_s > 0.5 ? ` · load ${s.load_s.toFixed(1)}s` : ""} · first token ${first ? ((first - t0) / 1000).toFixed(1) : "—"}s · ${s.wall_s.toFixed(1)}s`));
        } else if (ev.type === "error") { sys(`error: ${ev.message}`, "t-err"); }
        if (stick) body.scrollTop = body.scrollHeight;
      }, abort.signal);
    } catch (err) {
      if (err.name === "AbortError") sys("stopped", "t-warn");
      else sys(`error: ${err.message}`, "t-err");
    } finally {
      cursor.remove();
      if (thoughts && !reply) thinkingEl.textContent = `(thinking) ${thoughts}`;
      if (reply.trim()) messages.push({ role: "assistant", content: reply.trim() });
      else messages.pop(); // don't keep an unanswered question in the context
      abort = null;
      promptEl.textContent = "user@server ›";
      held.forEach(logLine);
      held = [];
    }
  }

  // ---- commands
  async function run(raw) {
    const text = raw.trim();
    if (!text) return;
    if (!text.startsWith("/")) { if (abort) { sys("still answering — Esc to stop first", "t-warn"); return; } await ask(text); return; }
    const [cmd, ...rest] = text.split(/\s+/);
    const arg = text.slice(cmd.length).trim();
    const onoff = (v) => (v === "on" ? true : v === "off" ? false : null);
    switch (cmd) {
      case "/help":
        line(span("t-ok", "commands"));
        Object.entries(COMMANDS).forEach(([c, d]) => line(span("t-dash", `  ${c.padEnd(10)} `), span("t-sys", d)));
        line(span("t-dim", "  anything not starting with / goes straight to the model (multi-turn; /reset to forget)."));
        break;
      case "/clear": body.replaceChildren(); break;
      case "/reset": messages = []; sys("new conversation — the model forgot this chat"); break;
      case "/model":
        if (!arg) sys(`current model: ${opts.model}`);
        else { opts.model = arg; if ([...modelSel.options].some((o) => o.value === arg)) modelSel.value = arg; sys(`model → ${arg}`); }
        break;
      case "/models": await refreshModels(true); break;
      case "/persona": {
        const v = onoff(rest[0]); if (v == null) { sys(`persona is ${opts.persona ? "on" : "off"}`); break; }
        opts.persona = v; swPersona.i.checked = v; sys(`persona ${v ? "on" : "off"}`); break;
      }
      case "/think": {
        const v = onoff(rest[0]); if (v == null) { sys(`CLI thinking is ${opts.think ? "on" : "off"}`); break; }
        opts.think = v; swThink.i.checked = v; sys(`thinking ${v ? "on" : "off"} for CLI questions`); break;
      }
      case "/ollama": {
        const v = onoff(rest[0]); if (v == null) { sys(`ollama lines ${opts.showOllama ? "shown" : "hidden"}`); break; }
        opts.showOllama = v; swOllama.i.checked = v; sys(`ollama lines ${v ? "shown" : "hidden"}`); break;
      }
      case "/temp": {
        if (!arg) { opts.temperature = null; sys("temperature → model default"); break; }
        const t = Number(arg);
        if (Number.isNaN(t) || t < 0 || t > 2) { sys("temperature must be between 0 and 2", "t-err"); break; }
        opts.temperature = t; sys(`temperature → ${t}`); break;
      }
      case "/say": {
        if (!arg) { sys("usage: /say <text>", "t-warn"); break; }
        if (!(await confirmDialog({ title: "Post to Discord?", body: `LatoChan will say: “${arg.slice(0, 200)}”`, confirm: "Post" }))) { sys("cancelled"); break; }
        try { const r = await api.post("/api/tools/say", { text: arg }); sys(`posted (${r.output})`, "t-ok"); } catch (err) { sys(`say failed: ${err.message}`, "t-err"); }
        break;
      }
      case "/mem":
        try {
          const r = await api.get(`/api/memory/list${arg ? `?who=${encodeURIComponent(arg)}` : ""}`);
          (r.output || "(no notes)").split("\n").forEach((l) => line(span("t-think", l)));
        } catch (err) { sys(`memory list failed: ${err.message}`, "t-err"); }
        break;
      case "/thinking": {
        const mode = rest[0] || "toggle";
        if (!["on", "off", "toggle"].includes(mode)) { sys("usage: /thinking on|off|toggle", "t-warn"); break; }
        try { const r = await api.post("/api/tools/thinking", { mode }); sys(r.output, "t-ok"); } catch (err) { sys(`failed: ${err.message}`, "t-err"); }
        break;
      }
      case "/restart":
        if (!(await confirmDialog({ title: "Restart LatoChan?", danger: true, confirm: "Restart", body: "She goes offline for ~10 s and forgets recent chat history." }))) { sys("cancelled"); break; }
        sys("restarting LatoChan…", "t-warn");
        try { const r = await api.post("/api/tools/restart-bot"); r.output.split("\n").forEach((l) => sys(l, "t-ok")); } catch (err) { sys(`restart failed: ${err.message}`, "t-err"); }
        break;
      case "/status": {
        const s = store.state;
        if (!s) { sys("no data yet"); break; }
        const cfg = store.config;
        for (const u of [cfg.bot_unit, cfg.ollama_unit]) {
          const v = s.services?.[u];
          sys(`${u}: ${v?.active || "?"}${v?.since ? ` (up ${f.duration(store.serverNow() - v.since)})` : ""}`, v?.active === "active" ? "t-ok" : "t-err");
        }
        const m = s.ollama?.loaded?.[0];
        sys(`loaded: ${m ? `${m.name} · ${f.bytes(m.vram)} VRAM · ctx ${m.ctx}` : "nothing"}`);
        const l = s.stats.llm;
        sys(`24h: ${l.calls} requests · ${f.compact(l.prompt_tokens)} in / ${f.compact(l.gen_tokens)} out · avg ${l.gen_tps ?? "—"} tok/s`);
        sys(`bot thinking mode: ${s.settings?.thinking ? "ON" : "off"} · alerts: ${s.alerts.length ? s.alerts.map((a) => a.title).join("; ") : "none"}`);
        break;
      }
      case "/history":
        if (!messages.length) sys("conversation is empty");
        messages.forEach((m) => line(span(m.role === "user" ? "t-you" : "t-bot", m.role === "user" ? "you › " : "model › "), span("t-ai", m.content)));
        break;
      case "/stop": if (abort) abort.abort(); else sys("nothing to stop"); break;
      default: sys(`unknown command ${cmd} — /help`, "t-warn");
    }
  }

  // ---- input handling
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const v = input.value;
      input.value = "";
      if (v.trim() && history.at(-1) !== v) { history.push(v); saveHistory(history); }
      histIdx = history.length;
      run(v);
    } else if (e.key === "ArrowUp") {
      if (histIdx > 0) { histIdx--; input.value = history[histIdx]; e.preventDefault(); }
    } else if (e.key === "ArrowDown") {
      if (histIdx < history.length) { histIdx++; input.value = history[histIdx] ?? ""; e.preventDefault(); }
    } else if (e.key === "Tab") {
      e.preventDefault();
      if (input.value.startsWith("/")) {
        const hits = Object.keys(COMMANDS).filter((c) => c.startsWith(input.value.split(" ")[0]));
        if (hits.length === 1) input.value = `${hits[0]} `;
        else if (hits.length > 1) sys(hits.join("  "), "t-dim");
      }
    } else if (e.key === "Escape" || (e.key === "c" && e.ctrlKey && !window.getSelection()?.toString())) {
      if (abort) { e.preventDefault(); abort.abort(); }
    }
  });

  let started = false;
  store.on("snapshot", () => {
    headTitle.textContent = `${store.config?.server || "server"} — lato-watch + direct model access`;
    if (!started) { started = true; banner(); refreshModels(); }
  });
  return { shown() { input.focus(); body.scrollTop = body.scrollHeight; } };
}
