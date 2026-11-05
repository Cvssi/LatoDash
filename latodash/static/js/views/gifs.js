// GIFs: the library behind [[GIF: mood]] — paste, import from a file or the PC folder, remove, back up.
import { api } from "../api.js";
import { h, icon, ICONS, toast, confirmDialog, withBusy, pill } from "../ui.js";

const MOOD_OK = /^[a-z_]{1,24}$/;
const norm = (m) => m.trim().toLowerCase().replace(/[\s-]+/g, "_");
const LINK = /https:\/\/[^\s"'<>\\`]+/g;
const MAX_FILE = 1_000_000;

/** Pulls links out of pasted/file text. Returns {moods: {mood: [links]}, found, bad}. A "mood: link" line, or a JSON
 *  object {mood: [links]}, routes links to that mood; everything else goes to defaultMood. */
export function parseImport(text, defaultMood) {
  const moods = {};
  let found = 0, bad = 0;
  const put = (mood, link) => {
    const m = norm(mood || "");
    if (!MOOD_OK.test(m)) { bad++; return; }
    const arr = (moods[m] ||= []);
    if (!arr.includes(link)) { arr.push(link); found++; }
  };
  const t = text.trim();
  if (t.startsWith("{") || t.startsWith("[")) {
    try {
      const j = JSON.parse(t);
      if (Array.isArray(j)) j.forEach((x) => (typeof x === "string" && x.startsWith("https://") ? put(defaultMood, x.trim()) : bad++));
      else for (const [m, list] of Object.entries(j)) (Array.isArray(list) ? list : []).forEach((x) => (typeof x === "string" && x.startsWith("https://") ? put(m, x.trim()) : bad++));
      return { moods, found, bad };
    } catch { /* not JSON after all: fall through to plain text */ }
  }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const tagged = line.match(/^([A-Za-z_ -]{1,24})\s*[:=,]\s*(https:\/\/\S+)$/);
    if (tagged) { put(tagged[1], tagged[2]); continue; }
    const links = line.match(LINK);
    if (links) links.forEach((l) => put(defaultMood, l));
    else bad++;
  }
  return { moods, found, bad };
}

function shortLink(link) {
  try {
    const u = new URL(link);
    const tail = (u.pathname + u.search).replace(/^\/+/, "");
    return `${u.hostname}/${tail}`.slice(0, 70);
  } catch { return link.slice(0, 70); }
}

export function mount(root) {
  let lib = { moods: {}, known: [], limits: { per_mood: 300 } };
  const cards = h("div", { class: "mood-grid" });
  const stats = h("div", { class: "stat-line" });
  const pcInfo = h("div", { class: "drop-note" }, "Checking the PC folder…");
  let loaded = false;

  async function call(button, op, moods, okTitle) {
    try {
      const res = await withBusy(button, () => api.post("/api/gifs", { op, moods }));
      lib.moods = res.moods;
      render();
      if (okTitle) toast(okTitle, `${res.added || 0} added · ${res.removed || 0} removed${res.skipped?.length ? ` · ${res.skipped.length} ignored` : ""}`);
      return res;
    } catch (err) { toast("Gif update failed", err.message, "error", 7000); return null; }
  }

  // ---- add / import card
  const moodSel = h("select", { class: "select auto" });
  const newMood = h("input", { class: "input", placeholder: "new mood name", maxlength: 24, hidden: true });
  const paste = h("textarea", { class: "textarea", rows: 6, placeholder: "Paste links here — one per line, or many on a line.\nTip: start a line with “happy: https://…” to send it to a specific mood.\nA JSON file like {\"happy\": [\"https://…\"]} works too." });
  const fileIn = h("input", { type: "file", accept: ".txt,.csv,.json,.md,text/plain,application/json", hidden: true });
  const info = h("span", { class: "muted small" });
  const addBtn = h("button", { class: "btn primary" }, icon(ICONS.upload), "Add to library");
  const fileBtn = h("button", { class: "btn" }, icon(ICONS.download), "Choose a file…");

  const targetMood = () => (moodSel.value === "__new" ? norm(newMood.value) : moodSel.value);
  moodSel.onchange = () => { newMood.hidden = moodSel.value !== "__new"; if (!newMood.hidden) newMood.focus(); refreshInfo(); };
  const refreshInfo = () => {
    const t = targetMood();
    const r = parseImport(paste.value, MOOD_OK.test(t) ? t : "");
    info.textContent = paste.value.trim() ? `${r.found} link${r.found === 1 ? "" : "s"} ready${r.bad ? ` · ${r.bad} line${r.bad === 1 ? "" : "s"} will be ignored` : ""}` : "";
  };
  paste.addEventListener("input", refreshInfo);
  newMood.addEventListener("input", refreshInfo);
  fileBtn.onclick = () => fileIn.click();
  fileIn.onchange = async () => {
    const f = fileIn.files?.[0];
    fileIn.value = "";
    if (!f) return;
    if (f.size > MAX_FILE) { toast("File too big", "links files should be well under 1 MB", "error"); return; }
    paste.value = (paste.value.trim() ? paste.value.trimEnd() + "\n" : "") + (await f.text());
    refreshInfo();
    toast(`Loaded ${f.name}`, "check the count, then press Add to library");
  };
  addBtn.onclick = async () => {
    const t = targetMood();
    const r = parseImport(paste.value, MOOD_OK.test(t) ? t : "");
    if (!r.found) { toast("No links found", "https:// links only", "error"); return; }
    const res = await call(addBtn, "add", r.moods, "Added to the library");
    if (res) { paste.value = ""; refreshInfo(); }
  };
  const addCard = h("div", { class: "card tool-card" },
    h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), "Add or import links")),
    h("p", { class: "desc" }, "Links go into /var/lib/latochan/gifs/<mood>/links.txt. She picks a mood and posts a random link from it."),
    h("div", { class: "row" }, h("span", { class: "muted small" }, "Mood for plain links"), moodSel, newMood),
    h("div", { class: "section-gap" }, paste),
    h("div", { class: "row section-gap" }, addBtn, fileBtn, fileIn, h("span", { class: "spacer" }), info),
    h("div", { class: "drop-note" }, "Only https:// links are kept; duplicates are skipped. Discord shows the gif itself, so use direct or Tenor/Giphy page links."));

  // ---- PC folder card
  const pcAdd = h("button", { class: "btn" }, icon(ICONS.download), "Import from PC (add)");
  const pcReplace = h("button", { class: "btn danger" }, icon(ICONS.download), "Import & replace");
  const pcExport = h("button", { class: "btn" }, icon(ICONS.upload), "Copy server → PC");
  pcAdd.onclick = async () => {
    try {
      const res = await withBusy(pcAdd, () => api.post("/api/gifs/pc/import", { mode: "add" }));
      lib.moods = res.moods; render(); toast("Imported from the PC folder", `${res.added} new link${res.added === 1 ? "" : "s"}`);
    } catch (err) { toast("Couldn't import", err.message, "error", 7000); }
  };
  pcReplace.onclick = async () => {
    if (!(await confirmDialog({ title: "Replace the server library?", danger: true, confirm: "Import & replace",
      body: "For every mood that has links on your PC, the server list is replaced by the PC list. Moods that are empty on the PC are left alone. Tip: use “Copy server → PC” first if you want a backup." }))) return;
    try {
      const res = await withBusy(pcReplace, () => api.post("/api/gifs/pc/import", { mode: "replace" }));
      lib.moods = res.moods; render(); toast("Server library replaced", `${res.added} links now come from the PC`);
    } catch (err) { toast("Couldn't import", err.message, "error", 7000); }
  };
  pcExport.onclick = async () => {
    try {
      const res = await withBusy(pcExport, () => api.post("/api/gifs/pc/export"));
      toast("Copied to the PC", `${res.written} folder${res.written === 1 ? "" : "s"} updated (old files kept as links.txt.pc-backup)`);
      loadPc();
    } catch (err) { toast("Couldn't copy", err.message, "error", 7000); }
  };
  async function loadPc() {
    try {
      const res = await api.get("/api/gifs/pc");
      const total = Object.values(res.moods).reduce((a, l) => a + l.length, 0);
      pcInfo.textContent = `${res.folder} — ${total} link${total === 1 ? "" : "s"} in ${Object.values(res.moods).filter((l) => l.length).length} mood folders`;
    } catch (err) { pcInfo.textContent = err.message; }
  }
  const pcCard = h("div", { class: "card tool-card" },
    h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), "PC folder")),
    h("p", { class: "desc" }, "Your latochan\\gifs\\<mood>\\links.txt files — the same ones “Upload gifs to server.bat” sends. Edit them in Notepad, then pull them in here."),
    stats, h("div", { class: "row" }, pcAdd, pcReplace, pcExport), pcInfo);

  // ---- mood cards
  function moodCard(mood, links) {
    const list = h("div", { class: "mood-links" }, links.map((l) => h("div", { class: "link-row" },
      h("a", { href: l, target: "_blank", rel: "noopener noreferrer", title: l }, shortLink(l)),
      (() => {
        const x = h("button", { class: "btn ghost sm", title: "Remove this link", "aria-label": "Remove link" }, icon(ICONS.trash));
        x.onclick = () => call(x, "remove", { [mood]: [l] }, null);
        return x;
      })())));
    const input = h("input", { class: "input", placeholder: "paste a link and press Enter" });
    const add = h("button", { class: "btn sm" }, "Add");
    const submit = async () => {
      const r = parseImport(input.value, mood);
      if (!r.found) { toast("That isn't an https:// link", "", "error"); return; }
      if (await call(add, "add", r.moods, null)) input.value = "";
    };
    add.onclick = submit;
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
    const clear = h("button", { class: "btn ghost sm", title: "Remove every link in this mood" }, icon(ICONS.trash), "Clear");
    clear.onclick = async () => {
      if (!(await confirmDialog({ title: `Clear all ${mood} gifs?`, danger: true, confirm: "Clear", body: `${links.length} link${links.length === 1 ? "" : "s"} will be removed from the server library. This can't be undone.` }))) return;
      call(clear, "replace", { [mood]: [] }, "Mood cleared");
    };
    return h("div", { class: "card mood-card" },
      h("div", { class: "card-head" }, h("div", { class: "card-title" }, h("span", { class: "dot" }), mood.replace(/_/g, " ")),
        h("div", { class: "row", style: { gap: "6px" } }, pill(`${links.length}`, links.length ? "ok" : "warn"), links.length ? clear : null)),
      links.length ? list : h("div", { class: "mood-empty" }, "Empty — if she picks this mood she just won't post a gif."),
      h("div", { class: "mood-add" }, input, add));
  }

  function render() {
    const moods = Object.keys(lib.moods);
    const total = moods.reduce((a, m) => a + lib.moods[m].length, 0);
    const empty = moods.filter((m) => !lib.moods[m].length);
    const current = moodSel.value;
    moodSel.replaceChildren(...moods.map((m) => h("option", { value: m }, m.replace(/_/g, " "))), h("option", { value: "__new" }, "＋ new mood…"));
    moodSel.value = moods.includes(current) || current === "__new" ? current : (moods[0] || "__new");
    newMood.hidden = moodSel.value !== "__new";
    stats.replaceChildren(pill(`${total} link${total === 1 ? "" : "s"}`, total ? "ok" : "warn"), pill(`${moods.length} moods`, ""),
      empty.length ? pill(`${empty.length} empty`, "warn") : pill("every mood has gifs", "ok"));
    cards.replaceChildren(...moods.map((m) => moodCard(m, lib.moods[m])));
    refreshInfo();
  }

  async function load() {
    try { lib = await api.get("/api/gifs"); loaded = true; render(); }
    catch (err) { cards.replaceChildren(h("div", { class: "empty" }, `Couldn't load the gif library: ${err.message}`)); }
    loadPc();
  }

  root.replaceChildren(h("div", { class: "grid g-2", style: { alignItems: "start" } }, addCard, pcCard), cards);
  return { shown() { load(); } };
}
