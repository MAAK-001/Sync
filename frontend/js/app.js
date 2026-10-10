/* Sync app shell: sidebar, top bar, router, live events, sign-in. Pages live in ./pages/. */
import { act, api, auth, convTitle, h, icon, logo, setToken, store, todayLong, wsUrl } from "./ui.js";
import * as home from "./pages/home.js";
import * as projects from "./pages/projects.js";
import * as project from "./pages/project.js";
import * as conversations from "./pages/conversations.js";
import * as conversation from "./pages/conversation.js";
import * as commitments from "./pages/commitments.js";
import * as ask from "./pages/ask.js";
import * as learned from "./pages/learned.js";
import * as settings from "./pages/settings.js";
import * as setup from "./pages/setup.js";

export const state = { me: null, projects: [], health: {}, live: [] };
const busHandlers = new Set();
let cleanups = [];
let renderId = 0;
export const app = {
  state,
  wantSample: false,
  onBus(fn) { busHandlers.add(fn); cleanups.push(() => busHandlers.delete(fn)); },
  onLeave(fn) { cleanups.push(fn); },
  rerender: () => render(),
  go: (hash) => { if (location.hash === hash) render(); else location.hash = hash; },
  async loadProjects() { state.projects = await api("/projects"); drawSide(); return state.projects; },
  pinned() { const id = Number(store.get(`sync.pin.${state.me?.id}`)); return state.projects.find((p) => p.id === id) || null; },
  pin(id) { store.set(`sync.pin.${state.me?.id}`, id ? String(id) : null); drawSide(); },
};

const NAV = [["projects", "Projects", "#/"], ["conversations", "Conversations", "#/conversations"], ["commitments", "Commitments", "#/commitments"],
  ["ask", "Ask Sync", "#/ask"], ["learned", "What Sync learned", "#/learned"], ["settings", "Settings", "#/settings"]];
const ROUTES = [
  [/^#?\/?$/, "projects", home], [/^#\/projects$/, "projects", projects], [/^#\/projects\/(\d+)(?:\/(\w+))?$/, "projects", project],
  [/^#\/conversations$/, "conversations", conversations], [/^#\/conversations\/(\d+)(?:\?t=([\d.]+))?$/, "conversations", conversation],
  [/^#\/commitments$/, "commitments", commitments], [/^#\/ask$/, "ask", ask], [/^#\/learned(?:\/(\w+))?$/, "learned", learned],
  [/^#\/settings(?:\/(\w+))?$/, "settings", settings],
];

/* ---------------------------------------------------------------- shell */
const side = h("aside", { class: "side" });
const crumbs = h("div", { class: "crumbs" });
const view = h("div", { class: "page" });
const shell = h("div", { class: "shell" }, side, h("div", { class: "main" },
  h("header", { class: "topbar" }, crumbs, h("div", { class: "topright" },
    h("span", { class: "local" }, icon("shield"), ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname) ? "Local only" : "On your network"),
    h("span", null, todayLong()))), view));

let currentSection = "projects";
function drawSide() {
  const me = state.me;
  const pinned = app.pinned();
  const recent = pinned || state.projects[0];
  const h0 = state.health;
  const mineLive = state.live.find((s) => s.user_id === me?.id);
  const engine = h0.ollama === "ok" && Object.values(h0.models_ready || {}).every(Boolean);
  side.replaceChildren(
    h("a", { class: "brand", href: "#/" }, logo(), "Sync"),
    h("div", { class: "ws" }, h("span", null, me ? (state.projects.some((p) => p.members.length > 1) ? "Personal + shared" : "Personal workspace") : "Setting up"),
      icon("updown", { width: 14, height: 14, style: "color:var(--muted)" })),
    h("nav", { class: `nav ${me ? "" : "disabled"}` }, NAV.map(([key, label, href]) =>
      h("a", { href, class: key === currentSection ? "on" : "" }, icon(key), label))),
    recent ? h("div", null, h("div", { class: "side-label" }, pinned ? "Pinned project" : "Recent project"),
      h("a", { class: "pinned", href: `#/projects/${recent.id}` }, recent.name)) : null,
    h("div", { class: "side-spacer" }),
    me ? h("div", { class: "status-card" },
      h("div", { class: engine ? "ok" : "bad" }, engine ? "Local engine ready" : h0.ollama ? "Local engine not ready" : "Checking engine…"),
      h("div", { class: h0.desktop_agent ? "" : "bad" }, h0.desktop_agent ? "Windows agent connected" : "Desktop agent not running"),
      mineLive ? h("a", { class: "live", href: `#/conversations/${mineLive.id}` }, `Listening · ${convTitle(mineLive)}`) : h("div", null, "Not listening")) : null,
    me ? h("a", { class: "me-chip", href: "#/settings/profile" }, h("span", { class: "avatar" }, (me.name || "?")[0].toUpperCase()),
      h("span", null, h("b", null, me.name), h("small", null, `${me.nudge_mode[0].toUpperCase()}${me.nudge_mode.slice(1)} nudges`))) : null);
}

async function refreshStatus() {
  if (!state.me) return;
  try {
    const [health, sessions] = await Promise.all([api("/health"), api("/sessions?limit=20")]);
    state.health = health;
    state.live = sessions.filter((s) => s.status === "live");
  } catch { /* offline: keep the last state */ }
  drawSide();
}

/* ---------------------------------------------------------------- router */
async function render() {
  cleanups.forEach((f) => { try { f(); } catch { /* ignore */ } });
  cleanups = [];
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  const id = ++renderId;
  const hash = location.hash || "#/";
  for (const [re, section, page] of ROUTES) {
    const m = hash.match(re);
    if (!m) continue;
    currentSection = section;
    drawSide();
    try {
      const out = await page.render(app, ...m.slice(1));
      if (id !== renderId) return;
      crumbs.replaceChildren(...crumbTrail(out.crumbs || []));
      view.replaceChildren(out.node);
      window.scrollTo(0, 0);
    } catch (e) {
      if (id === renderId) view.replaceChildren(h("div", { class: "card" }, h("h2", null, "Something went wrong"), h("p", { class: "muted" }, e.message || String(e))));
    }
    return;
  }
  view.replaceChildren(h("p", { class: "muted" }, "Page not found."));
}
function crumbTrail(parts) {
  const out = [h("a", { href: "#/" }, "Workspace")];
  for (const p of parts) out.push(h("span", null, "/"), typeof p === "string" ? p : h("a", { href: p[1] }, p[0]));
  return out;
}

/* ---------------------------------------------------------------- live events */
function connectEvents() {
  const ws = new WebSocket(wsUrl("/ws/events"));
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (["session.live", "ended", "recap.refined", "project.linked"].includes(ev.type)) refreshStatus();
    if (ev.type === "session.live" && app.wantSample && ev.app === "sample" && ev.owner_id === state.me.id) {
      app.wantSample = false;
      location.hash = `#/conversations/${ev.session_id}`;
    }
    busHandlers.forEach((fn) => fn(ev));
  };
  ws.onclose = () => setTimeout(connectEvents, 2000);
}

/* ---------------------------------------------------------------- start */
async function start() {
  try {
    state.me = await api("/me");
    await app.loadProjects();
  } catch (e) {
    view.replaceChildren(h("div", { class: "card" }, h("h2", null, "Can't reach Sync"), h("p", { class: "muted" }, e.message)));
    return;
  }
  connectEvents();
  refreshStatus();
  setInterval(refreshStatus, 15000);
  window.addEventListener("hashchange", render);
  await render();
}

document.body.append(shell);
(async () => {
  const hash = location.hash || "";
  if (auth.token) { start(); return; }
  drawSide();
  let info;
  try { info = await api("/setup"); } catch (e) {
    view.replaceChildren(h("div", { class: "card" }, h("h2", null, "Can't reach Sync"), h("p", { class: "muted" }, "Start the backend: .venv/Scripts/python -m sync.main")));
    return;
  }
  const done = (token) => { setToken(token); location.hash = location.hash.startsWith("#/join/") ? "#/" : location.hash; start(); };
  if (hash.startsWith("#/join/")) {
    const out = await setup.join(hash.slice(7), done);
    crumbs.replaceChildren(...crumbTrail(["Join a project"]));
    view.replaceChildren(out);
    return;
  }
  if (!info.has_users) {
    crumbs.replaceChildren(...crumbTrail(["Set up"]));
    view.replaceChildren(setup.firstRun(done));
    return;
  }
  crumbs.replaceChildren(...crumbTrail(["Sign in"]));
  view.replaceChildren(setup.signIn(async () => {
    const r = await act(() => api("/signin/this-computer", { body: {} }));
    if (r) done(r.token);
  }));
})();
