/* Shared helpers: DOM building, icons, API, formatting, toasts, modals, menus. No external libraries. */

export function h(tag, attrs, ...kids) {
  const el = tag === "svg" || tag === "path" || tag === "circle" || tag === "rect" || tag === "polyline" || tag === "line"
    ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  const props = {};
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value" || k === "checked") props[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  Object.assign(el, props);
  return el;
}

/* replaceChildren with the same rules as h(): nested arrays are flattened; null, undefined and false are skipped
   (the browser's own version would print them as text). */
const nativeReplace = Element.prototype.replaceChildren;
Element.prototype.replaceChildren = function (...kids) {
  return nativeReplace.apply(this, kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
};

/* ---------------------------------------------------------------- icons (simple line icons, 24px grid) */
const P = {
  projects: "M12 3 3 8l9 5 9-5-9-5Z|M3 13l9 5 9-5|M3 17.5l9 5 9-5",
  conversations: "M4 5h11a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9l-4 3v-3H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z|M17 9h3a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-1v3l-4-3h-3",
  commitments: "C12 12 9|M8 12l3 3 5-6",
  ask: "M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3Z|M5 15l.8 2.2L8 18l-2.2.8L5 21l-.8-2.2L2 18l2.2-.8L5 15Z|M19 14l.6 1.4L21 16l-1.4.6L19 18l-.6-1.4L17 16l1.4-.6L19 14Z",
  learned: "M3 5h6a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H3V5Z|M21 5h-6a3 3 0 0 0-3 3v12a2 2 0 0 1 2-2h7V5Z",
  settings: "M4 7h10|M18 7h2|C16 7 2|M4 17h2|M10 17h10|C8 17 2",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3Z|M8.5 12l2.5 2.5 4.5-5",
  arrowUR: "M7 17 17 7|M8 7h9v9",
  arrowR: "M5 12h14|M13 6l6 6-6 6",
  plus: "M12 5v14|M5 12h14",
  file: "M14 3H6v18h12V7l-4-4Z|M14 3v4h4|M9 13h6|M9 17h6",
  headphones: "M4 14v-2a8 8 0 0 1 16 0v2|M4 14h3v6H4z|M17 14h3v6h-3z",
  chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.7A8 8 0 1 1 21 12Z",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1|M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  thumb: "M7 10v11H3V10h4Z|M7 10l4-7a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2.3l-1.4 7A2 2 0 0 1 17.6 21H7",
  square: "M6 6h12v12H6z",
  copy: "M9 9h11v11H9z|M5 15H4V4h11v1",
  clip: "M21 11l-8.5 8.5a5 5 0 0 1-7-7L14 4a3.5 3.5 0 0 1 5 5l-8.6 8.5a2 2 0 0 1-2.8-2.8L15 7.3",
  lock: "M6 11h12v10H6z|M8 11V8a4 4 0 0 1 8 0v3",
  chevron: "M6 9l6 6 6-6",
  updown: "M8 9l4-4 4 4|M8 15l4 4 4-4",
  check: "M5 12l5 5 9-10",
  checks: "M2 12l5 5 9-10|M12 15l2 2 8-9",
  x: "M6 6l12 12|M18 6 6 18",
  circle: "C12 12 8",
  send: "M12 19V5|M6 11l6-6 6 6",
  phone: "M8 2h8v20H8z|M11 18h2",
  video: "M3 7h12v10H3z|M15 10l6-3v10l-6-3",
  pin: "M12 17v5|M8 3h8l-1 6 3 3v2H6v-2l3-3-1-6Z",
  users: "C9 8 3.5|M2 20c0-3.5 3-6 7-6s7 2.5 7 6|C17 8.5 2.5|M16 14c3 0 6 2 6 5",
  play: "M7 4l13 8-13 8V4Z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7|M20 4v7h-7",
};
export function icon(name, extra = {}) {
  const parts = (P[name] || "").split("|").filter(Boolean).map((d) => {
    if (d.startsWith("C")) { const [cx, cy, r] = d.slice(1).trim().split(/\s+/); return h("circle", { cx, cy, r }); }
    return h("path", { d });
  });
  return h("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linecap": "round", "stroke-linejoin": "round", ...extra }, parts);
}
export function logo() {
  const bars = [[6, 10, 14], [9, 7, 17], [12, 5, 19], [15, 8, 16], [18, 10, 14]];
  return h("span", { class: "logo" }, h("svg", { viewBox: "0 0 24 24", width: 18, height: 18 },
    bars.map(([x, y1, y2]) => h("line", { x1: x, y1, x2: x, y2, stroke: "#fff", "stroke-width": 1.8, "stroke-linecap": "round" }))));
}

/* ---------------------------------------------------------------- storage & API */
export const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { if (v === null || v === undefined) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* private mode */ } },
};
export const auth = { token: store.get("sync.token") };
export function setToken(t) { auth.token = t; store.set("sync.token", t); }

export async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers: {} };
  if (auth.token) init.headers["X-Sync-Token"] = auth.token;
  if (opts.body !== undefined) {
    if (opts.body instanceof FormData) init.body = opts.body;
    else { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  }
  let r;
  try { r = await fetch(path, init); } catch { throw new Error("Can't reach Sync. Is the backend running?"); }
  if (r.status === 401 && auth.token) { setToken(null); location.hash = "#/"; location.reload(); }
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}
export const wsUrl = (path) => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(auth.token || "")}`;

/* ---------------------------------------------------------------- feedback */
export function toast(text, ms = 2800) {
  let t = document.getElementById("toast");
  if (!t) { t = h("div", { id: "toast", class: "toast" }); document.body.append(t); }
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, ms);
}
export async function act(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); return r ?? true; } catch (e) { toast(e.message, 5000); return null; }
}
export function modal(title, body, actions = []) {
  const back = h("div", { class: "modal-back", onclick: (e) => { if (e.target === back) close(); } });
  const close = () => back.remove();
  back.append(h("div", { class: "modal" }, h("h2", null, title), body,
    actions.length ? h("div", { class: "row", style: "justify-content:flex-end;margin-top:22px" }, actions.map((a) => {
      const b = h("button", { class: `btn ${a.kind || ""}`, onclick: async () => { b.disabled = true; const keep = await a.run(close); b.disabled = false; if (!keep) close(); } }, a.label);
      return b;
    })) : null));
  document.body.append(back);
  setTimeout(() => back.querySelector("input,textarea,select")?.focus(), 0);
  return close;
}
export function confirmBox(title, text, label = "Confirm", kind = "danger") {
  return new Promise((resolve) => {
    let done = false;
    modal(title, h("p", { class: "muted" }, text), [
      { label: "Cancel", run: () => { done = true; resolve(false); } },
      { label, kind, run: () => { done = true; resolve(true); } }]);
    const back = document.body.lastChild;
    new MutationObserver((_, obs) => { if (!document.body.contains(back)) { obs.disconnect(); if (!done) resolve(false); } }).observe(document.body, { childList: true });
  });
}
/* A small dropdown: trigger element + options [{label, run}] */
export function dropdown(trigger, options) {
  const wrap = h("span", { class: "drop" }, trigger);
  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    document.querySelectorAll(".menu").forEach((m) => m.remove());
    const menu = h("div", { class: "menu" }, options().map((o) => h("button", { onclick: () => { menu.remove(); o.run(); } }, o.label)));
    wrap.append(menu);
    const off = () => { menu.remove(); document.removeEventListener("click", off); };
    setTimeout(() => document.addEventListener("click", off), 0);
  });
  return wrap;
}

/* ---------------------------------------------------------------- formatting */
export function date(iso) {
  if (!iso) return null;
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z");  // the server stores UTC
}
const DAY = { weekday: "short", day: "numeric", month: "short" };
export function when(iso, withTime = true) {
  const d = date(iso);
  if (!d) return "";
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const today = new Date(); const y = new Date(); y.setDate(today.getDate() - 1);
  const day = d.toDateString() === today.toDateString() ? "Today" : d.toDateString() === y.toDateString() ? "Yesterday" : d.toLocaleDateString("en-GB", DAY);
  return withTime ? `${day} · ${t}` : day;
}
export const shortDate = (iso) => { const d = date(iso); return d ? d.toLocaleDateString("en-GB", DAY) : ""; };
export const todayLong = () => new Date().toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
export function mmss(sec) {
  if (sec === null || sec === undefined) return "";
  sec = Math.max(0, Math.round(sec));
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}
export function minutes(s) {
  let sec = (s.stats || {}).duration_s;
  if (!sec && s.started_at && s.ended_at) sec = (date(s.ended_at) - date(s.started_at)) / 1000;
  if (!sec) return "";
  return sec < 60 ? `${Math.round(sec)} s` : `${Math.round(sec / 60)} min`;
}
const APPS = { "phone link": "Phone Link", zoom: "Zoom", teams: "Teams", meet: "Google Meet", webex: "Webex", whatsapp: "WhatsApp", slack: "Slack", discord: "Discord", sample: "Sample call" };
export const appName = (a) => APPS[(a || "").toLowerCase()] || (a ? a[0].toUpperCase() + a.slice(1) : "");
export function convTitle(s) {
  if (s.mode === "script") return s.title ? `Sample · ${s.title}` : "Sample call";
  if (s.title && s.title !== "Phone call") return s.title;
  if (s.caller) return `Call with ${s.caller}`;
  if (s.app) return `${appName(s.app)} call`;
  return s.mode === "paste" ? "Pasted conversation" : "Conversation";
}
export const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t);
export const stripPrefix = (t) => cap((t || "").replace(/^(Ask|Confirm|Caution|Known|Answer):\s*/i, ""));
export const TYPE_LABEL = { fact: "Fact", requirement: "Requirement", decision: "Decision", commitment: "Commitment", question: "Question",
  concern: "Concern", proposal: "Proposal", risk: "Risk", note: "Note" };
export const TYPES = Object.keys(TYPE_LABEL);
export const STATUSES = ["open", "proposed", "conditional", "agreed", "done"];
export function statusChip(status) {
  const cls = { agreed: "green", done: "green", open: "amber", conditional: "", proposed: "" }[status] ?? "";
  return h("span", { class: `chip ${cls}` }, cap(status));
}
export const KIND = { caution: "Caution", confirm: "Confirm", ask: "Ask", fact: "Already known", answer: "Answer", tip: "Tip" };

/* A quote line with where it came from: “…” / Priya · Thu 8 Oct · Scope follow-up · 08:42 ↗ */
export function sourceQuote(src, fallbackQuote) {
  const q = (src && src.quote) || fallbackQuote;
  if (!q && !src) return null;
  const bits = src ? [src.speaker ? cap(src.speaker) : "", shortDate(src.date), src.title, src.t != null ? mmss(src.t) : ""].filter(Boolean) : [];
  return h("div", { class: "quote" }, q ? `“${q.length > 220 ? q.slice(0, 219) + "…" : q}”` : null,
    src ? h("div", null, h("a", { class: "src", href: `#/conversations/${src.session_id}${src.t != null ? `?t=${src.t}` : ""}` }, icon("link"), bits.join(" · "), " ↗")) : null);
}

/* The small Markdown subset chat answers use (**bold**, bullets), built as DOM nodes, never innerHTML. */
export function markdown(text) {
  const inline = (line) => line.split(/(\*\*[^*]+\*\*)/).filter(Boolean)
    .map((part) => (part.startsWith("**") && part.endsWith("**") ? h("b", null, part.slice(2, -2)) : part));
  const out = []; let list = null;
  for (const raw of (text || "").replace(/\n{3,}/g, "\n\n").split("\n")) {
    const bullet = raw.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (bullet) { if (!list) { list = h("ul"); out.push(list); } list.append(h("li", null, inline(bullet[1]))); continue; }
    list = null;
    out.push(raw.trim() ? h("p", { style: "margin:0 0 8px" }, inline(raw)) : null);
  }
  return h("div", { class: "body" }, out);
}

export function segmented(options, value, onPick) {
  const wrap = h("div", { class: "seg" });
  const draw = (v) => wrap.replaceChildren(...options.map(([k, label]) => h("button", { type: "button", class: k === v ? "on" : "", onclick: () => { draw(k); onPick(k); } }, label)));
  draw(value);
  return wrap;
}

/* ---------------------------------------------------------------- history (append-only event log) */
const EVENT = { "item.created": "Added", "item.updated": "Changed", "item.deleted": "Removed", "item.superseded": "Replaced by newer information",
  "item.confirmed": "Confirmed", "item.rejected": "Rejected", "conflict.detected": "Conflict found", "conflict.resolved": "Conflict resolved",
  "person.added": "Person added", "person.updated": "Person changed", "person.removed": "Person removed", "project.created": "Project created",
  "project.updated": "Project details changed", "member.added": "Joined the project", "member.removed": "Removed from the project",
  "invite.created": "Invite link created", "invite.revoked": "Invite link turned off", "session.started": "Conversation started",
  "session.detected": "Call detected", "session.ended": "Conversation ended", "session.linked": "Conversation filed here",
  "session.deleted": "Conversation deleted", "session.skipped": "Call skipped", "recap.drafted": "Draft recap", "recap.refined": "Recap refined",
  "followup.drafted": "Follow-up drafted", "nudge.shown": "Nudge shown", "nudge.used": "Nudge used", "nudge.useful": "Nudge marked useful",
  "nudge.dismissed": "Nudge dismissed", "document.added": "Document added", "document.removed": "Document removed",
  "playbook.added": "Added", "playbook.updated": "Changed", "playbook.deleted": "Deleted", "playbook.reset": "Playbook reset" };
const ACTOR = { t0: "live", t1: "live", t2: "after the call" };
const FIELD = { text: "text", status: "status", owner: "owner", due: "due", answer: "answer", type: "type", name: "name", objective: "objective",
  context: "context", brief: "summary", org: "organisation", role: "role", notes: "notes", active: "on", condition: "condition", confirmed: "confirmed" };
const fmtVal = (v) => (v === null || v === undefined || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v).slice(0, 60) : String(v).slice(0, 90));
export const eventLabel = (t) => EVENT[t] || t.replace(/[._]/g, " ");
export function eventRow(e) {
  const a = e.after || {}; const b = e.before || {};
  const what = a.text || a.name || a.explanation || a.resolution || b.text || b.name || "";
  const changes = Object.keys(a).filter((k) => k in b && k in FIELD && !(k === "text" && what === a.text && !b.text)).map((k) => `${FIELD[k]}: ${fmtVal(b[k])} → ${fmtVal(a[k])}`);
  const by = e.user_name ? (ACTOR[e.actor] ? `Sync · ${e.user_name}'s call` : e.actor === "agent" ? `${e.user_name} via Ask Sync` : e.user_name) : "Sync";
  return [h("div", null, `${by} · ${when(e.ts)}`), h("div", null, h("b", null, e.type === "playbook.updated" && /learned/.test(e.reason || "") ? "Learned from your calls" : eventLabel(e.type)), what && !changes.length ? h("div", { class: "meta" }, String(what).slice(0, 160)) : null,
    changes.length ? h("div", { class: "meta" }, changes.join("   ")) : null,
    e.reason ? h("div", { class: "meta" }, cap(e.reason)) : null)];
}
export function historyGrid(events) {
  return events.length ? h("div", { class: "hist" }, events.map(eventRow)) : h("p", { class: "empty" }, "No history yet.");
}
