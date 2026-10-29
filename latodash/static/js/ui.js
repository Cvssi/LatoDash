// Small DOM toolkit: safe element builder, toasts, confirm dialog, button busy-state.

/**
 * h("div", {class: "x", onclick: fn}, "text", child) — children are text-safe (strings become text nodes).
 */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

/** Inline SVG icon from a path string. */
export function icon(path, cls = "") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  if (cls) svg.setAttribute("class", cls);
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", path);
  svg.append(p);
  return svg;
}

export const ICONS = {
  restart: "M12 5V2L8 6l4 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7",
  download: "M5 20h14v-2H5zm7-3 6-6h-4V3h-4v8H6z",
  upload: "M5 20h14v-2H5zM12 3 6 9h4v6h4V9h4z",
  send: "M3 20.5 21 12 3 3.5v6.6L15 12 3 13.9z",
  check: "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z",
  brain: "M9 3a3 3 0 0 0-3 3 3 3 0 0 0-2 5.2A3 3 0 0 0 6 16a3 3 0 0 0 3 3 2 2 0 0 0 2-2V5a2 2 0 0 0-2-2m6 0a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2 3 3 0 0 0 3-3 3 3 0 0 0 2-4.8A3 3 0 0 0 18 6a3 3 0 0 0-3-3",
  chip: "M9 3v2H7a2 2 0 0 0-2 2v2H3v2h2v2H3v2h2v2a2 2 0 0 0 2 2h2v2h2v-2h2v2h2v-2h2a2 2 0 0 0 2-2v-2h2v-2h-2v-2h2V9h-2V7a2 2 0 0 0-2-2h-2V3h-2v2h-2V3zm0 6h6v6H9z",
  eject: "M5 17h14v2H5zm7-12L5.3 15h13.4z",
  play: "M8 5v14l11-7z",
  list: "M4 6h2v2H4zm4 0h12v2H8zm-4 5h2v2H4zm4 0h12v2H8zm-4 5h2v2H4zm4 0h12v2H8z",
  trash: "M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zM19 4h-3.5l-1-1h-5l-1 1H5v2h14z",
  export: "M14 3v2h3.6l-9.8 9.8 1.4 1.4L19 6.4V10h2V3zM5 5h6V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6h-2v6H5z",
  pause: "M6 5h4v14H6zm8 0h4v14h-4z",
};

export function toast(title, detail = "", kind = "ok", ms = 4200) {
  const box = document.getElementById("toasts");
  const t = h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" },
    h("div", {}, h("b", {}, title), detail ? h("span", { class: "muted small" }, detail) : null));
  box.append(t);
  setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, ms);
}

/** Promise<boolean>. Escape/backdrop = cancel. */
export function confirmDialog({ title, body, confirm = "Confirm", danger = false }) {
  const dlg = document.getElementById("modal");
  return new Promise((resolve) => {
    const done = (v) => { dlg.close(); resolve(v); };
    const ok = h("button", { class: `btn ${danger ? "danger" : "primary"}`, onclick: () => done(true) }, confirm);
    dlg.replaceChildren(
      h("div", { class: "modal-body" }, h("h3", {}, title), h("p", {}, body)),
      h("div", { class: "modal-foot" }, h("button", { class: "btn ghost", onclick: () => done(false) }, "Cancel"), ok),
    );
    dlg.onclose = () => resolve(false);
    dlg.onclick = (e) => { if (e.target === dlg) done(false); };
    dlg.showModal();
    ok.focus();
  });
}

/** Runs fn while the button shows a spinner; returns fn's result. */
export async function withBusy(btn, fn) {
  btn.classList.add("busy");
  btn.disabled = true;
  try { return await fn(); } finally { btn.classList.remove("busy"); btn.disabled = false; }
}

export function pill(text, tone = "") {
  return h("span", { class: `pill ${tone}` }, h("i"), text);
}
