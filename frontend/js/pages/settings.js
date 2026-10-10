/* Settings: when Sync listens (auto-listen & privacy), your profile and nudges, and this computer's runtime. */
import { act, api, h, icon, segmented, toast } from "../ui.js";
import { docsCard } from "./project.js";
import { playSample } from "./conversations.js";

const APPS = [["phone link", "Phone Link", "phone"], ["zoom", "Zoom", "video"], ["teams", "Microsoft Teams", "video"], ["meet", "Google Meet", "video"],
  ["whatsapp", "WhatsApp", "phone"], ["webex", "Webex", "video"], ["slack", "Slack huddles", "headphones"], ["discord", "Discord", "headphones"], ["other", "Any other app", "headphones"]];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MODES = [["quiet", "Quiet", "Only critical gaps: contradictions, over-promises, answers"], ["normal", "Normal", "Timely, relevant prompts"], ["active", "Active", "More frequent prompts and suggested questions"]];
const localIso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
const list = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
const PRIVATE = ["Speech recognition, AI models and memory run on this computer", "Audio is never stored; only the text transcript",
  "Only you see nudges and Ask Sync answers", "Nothing is sent or shared unless you invite a teammate to a project", "Every change is recorded with who made it"];

function privacyCard() {
  return h("div", { class: "panel" }, h("h2", null, "Private by default"),
    h("div", { class: "stack" }, PRIVATE.map((t) => h("div", { class: "row", style: "flex-wrap:nowrap;align-items:flex-start;gap:8px" },
      icon("check", { width: 15, height: 15, style: "color:var(--green);flex:none;margin-top:3px" }), h("span", { class: "small" }, t)))),
    h("p", { class: "meta", style: "margin:14px 0 0" }, "Other participants are not notified. Make sure recording is allowed where you are."));
}

async function listeningTab(app) {
  const cfg = await api("/me/autolisten");
  const draft = JSON.parse(JSON.stringify(cfg));
  const val = h("span", { class: "chip line" }, `${draft.countdown} seconds`);
  const range = h("input", { type: "range", min: 0, max: 10, value: draft.countdown, oninput: () => { draft.countdown = Number(range.value); val.textContent = `${draft.countdown} seconds`; } });
  const contacts = h("input", { class: "input", value: (draft.never_contacts || []).join(", "), placeholder: "Names or numbers, separated by commas" });
  const keywords = h("input", { class: "input", value: (draft.never_title_keywords || []).join(", ") });
  const wh = draft.work_hours || { days: [0, 1, 2, 3, 4], start: "09:00", end: "19:00" };
  const whOn = h("input", { type: "checkbox", checked: !!draft.work_hours });
  const start = h("input", { class: "input slim", type: "time", value: wh.start });
  const end = h("input", { class: "input slim", type: "time", value: wh.end });
  const days = new Set(wh.days);
  const dayRow = h("div", { class: "seg" });
  const drawDays = () => dayRow.replaceChildren(...DAYS.map((d, i) => h("button", { type: "button", class: days.has(i) ? "on" : "", onclick: () => { days.has(i) ? days.delete(i) : days.add(i); drawDays(); } }, d)));
  drawDays();
  const logSkipped = h("input", { type: "checkbox", checked: draft.log_skipped });

  const save = async () => {
    const body = { mode: draft.mode, countdown: draft.countdown, apps: draft.apps, never_contacts: list(contacts.value), never_title_keywords: list(keywords.value),
      work_hours: whOn.checked ? { days: [...days].sort(), start: start.value || "00:00", end: end.value || "23:59" } : null, log_skipped: logSkipped.checked };
    if (await act(() => api("/me/autolisten", { method: "PUT", body }), "Saved")) app.rerender();
  };
  const paused = cfg.paused_until && new Date(cfg.paused_until) > new Date();
  const pause = async (until) => { if (await act(() => api("/me/autolisten", { method: "PUT", body: { paused_until: until } }), until ? "Auto-listen paused" : "Auto-listen resumed")) app.rerender(); };
  const tomorrow = () => { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(8, 0, 0, 0); return d; };

  const main = h("div", null,
    h("div", { class: "card" }, h("div", { class: "spread" }, h("div", null, h("h2", { style: "margin:0" }, "Auto-listen"),
      h("p", { class: "meta", style: "margin:4px 0 0" }, "Sync watches which app is using your microphone — never the audio — and joins when a call starts.")),
      segmented([["auto", "Auto"], ["ask", "Ask every time"], ["off", "Off"]], draft.mode, (v) => { draft.mode = v; })),
    h("hr", { class: "divider" }),
    h("div", { class: "spread" }, h("div", null, h("b", null, "Skip countdown"), h("div", { class: "meta" }, "Before Sync starts, a countdown on screen lets you skip the call (Ctrl+Alt+S). Nothing is kept.")), val),
    h("div", { style: "margin-top:10px" }, range)),

    h("div", { class: "card" }, h("h2", null, "Per-app rules"),
      h("div", { class: "list tight" }, APPS.map(([k, label, ic]) => h("div", { class: "spread" }, h("span", { class: "row", style: "gap:10px" }, icon(ic, { width: 16, height: 16, style: "color:var(--muted)" }), label),
        segmented([["auto", "Always"], ["ask", "Ask"], ["never", "Never"]], draft.apps[k] || "ask", (v) => { draft.apps[k] = v; })))),
      h("p", { class: "meta", style: "margin:12px 0 0" }, "Phone Link calls are heard through this laptop's microphone and speakers.")),

    h("div", { class: "card" }, h("h2", null, "Never listen"),
      h("div", { class: "field", style: "margin-top:0" }, h("label", null, "These contacts"), contacts),
      h("div", { class: "field" }, h("label", null, "Meetings whose title contains"), keywords, h("div", { class: "hint" }, "Separate with commas.")),
      h("div", { class: "field" }, h("label", { class: "row", style: "gap:8px" }, whOn, "Only during working hours"),
        h("div", { class: "row", style: "margin-top:8px" }, dayRow, start, h("span", { class: "muted" }, "to"), end)),
      h("div", { class: "field" }, h("label", { class: "row", style: "gap:8px;font-weight:400" }, logSkipped, "Note skipped calls in my history (app and time only)"))));

  const rail = h("div", { class: "rail" },
    h("div", { class: `card ${paused ? "amber" : ""}` }, h("h2", null, "Pause auto-listen"),
      paused ? [h("p", { style: "margin:-6px 0 12px" }, `Paused until ${new Date(cfg.paused_until).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false })}.`),
        h("button", { class: "btn small primary", onclick: () => pause(null) }, "Resume now")]
        : [h("p", { class: "meta", style: "margin-top:-6px" }, "Sync won't join any call until then."),
          h("div", { class: "row" }, h("button", { class: "btn small", onclick: () => pause(localIso(new Date(Date.now() + 3600e3))) }, "For 1 hour"),
            h("button", { class: "btn small", onclick: () => pause(localIso(tomorrow())) }, "Until tomorrow"))],
      h("p", { class: "meta", style: "margin:14px 0 0" }, "During a call: ", h("span", { class: "kbd" }, "Ctrl+Alt+S"), " stops listening.")),
    privacyCard());
  return { main, rail, save };
}

async function profileTab(app) {
  const me = await api("/me");
  const docs = await api("/documents");
  const name = h("input", { class: "input", value: me.name });
  const langs = h("input", { class: "input", value: (me.languages || []).join(", ") });
  let mode = me.nudge_mode;
  const choices = h("div", { class: "choices" });
  const draw = () => choices.replaceChildren(...MODES.map(([k, l, d]) => h("button", { class: `choice ${mode === k ? "on" : ""}`, onclick: () => { mode = k; draw(); } }, h("b", null, l), h("span", null, d))));
  draw();
  const save = async () => {
    if (!name.value.trim()) { name.focus(); return; }
    const r = await act(() => api("/me", { method: "PUT", body: { name: name.value, languages: list(langs.value), nudge_mode: mode } }), "Saved");
    if (r) { app.state.me = r; app.rerender(); }
  };
  const main = h("div", null,
    h("div", { class: "card" }, h("h2", null, "Your basics"),
      h("div", { class: "field", style: "margin-top:0" }, h("label", null, "Name"), name, h("div", { class: "hint" }, "Teammates in shared projects see this name next to your changes.")),
      h("div", { class: "field" }, h("label", null, "Conversation languages"), langs, h("div", { class: "hint" }, "Speech recognition is tuned for English."))),
    h("div", { class: "card" }, h("h2", null, "Nudges"), h("p", { class: "meta", style: "margin-top:-6px" }, "How often Sync speaks up during your calls. Sync also tunes this from your Useful and Dismiss clicks."), choices),
    h("div", { class: "card" }, h("h2", null, "Personal documents"),
      h("p", { class: "meta", style: "margin-top:-6px" }, "Used in all your calls (project documents live in each project). Price lists, policies, guides: Sync answers from them and warns before you over-promise."),
      docsCard(docs.filter((d) => !d.project_id), null, () => app.rerender())));
  return { main, rail: h("div", { class: "rail" }, privacyCard()), save };
}

async function agentTab(app) {
  const hl = await api("/health");
  const models = Object.entries(hl.models_ready || {});
  const ok = (good, text, bad) => h("div", { class: "row", style: "gap:8px;flex-wrap:nowrap" }, h("span", { class: `dot ${good ? "ok" : "bad"}` }), h("span", null, good ? text : bad));
  const keyOut = h("div");
  const main = h("div", null,
    h("div", { class: "card" }, h("h2", null, "Local runtime"),
      h("div", { class: "stack" },
        ok(hl.ollama === "ok", "Ollama is running", `Ollama: ${hl.ollama || "unknown"}. Start it, then reload.`),
        models.map(([m, ready]) => ok(ready, `Model ${m} ready`, `Model ${m} missing — run: ollama pull ${m}`)),
        ok(hl.stt && hl.stt.installed && hl.stt.vad, `Speech recognition ready (${hl.stt && hl.stt.model})`, `Speech recognition model not installed: ${typeof hl.stt === "string" ? hl.stt : (hl.stt || {}).model}`),
        ok(hl.desktop_agent, "Windows desktop agent connected", "Desktop agent not running — start it with: .venv\\Scripts\\python -m sync.desktop.agent"),
        hl.live_sessions ? h("div", { class: "meta" }, `${hl.live_sessions} conversation${hl.live_sessions === 1 ? "" : "s"} live right now`) : null),
      h("p", { class: "meta", style: "margin:14px 0 0" }, `Live model ${hl.live_model} · After-call model ${hl.deep_model} · Chat model ${hl.chat_model}${hl.llm_enabled ? "" : " · models turned off"}`)),
    h("div", { class: "card" }, h("h2", null, "On-screen nudges"),
      h("p", { class: "muted", style: "margin-top:-6px" }, "While Sync listens, nudges appear in a see-through strip at the top of your screen, over any app; clicks pass straight through. When a call starts, a small window asks which project it's about. Nudges are also shown on the conversation's page here."),
      h("div", { class: "hist", style: "grid-template-columns:130px 1fr" },
        h("div", null, h("span", { class: "kbd" }, "Ctrl+Alt+S")), h("div", null, "Skip the countdown, or stop listening"),
        h("div", null, h("span", { class: "kbd" }, "Ctrl+Alt+L")), h("div", null, "Listen to this call (when Sync asks first)"),
        h("div", null, h("span", { class: "kbd" }, "Ctrl+Alt+N")), h("div", null, "Hide or show nudges on screen"))),
    h("div", { class: "card" }, h("h2", null, "Try it"),
      h("p", { class: "muted", style: "margin-top:-6px" }, "Play a scripted call through the live engine. Nudges appear on screen and on the conversation page exactly as in a real call."),
      h("button", { class: "btn", onclick: () => playSample(app) }, icon("play"), "Play a sample call")),
    h("div", { class: "card" }, h("h2", null, "Desktop agent on another laptop"),
      h("p", { class: "muted", style: "margin-top:-6px" }, "To use Sync from a second laptop on your network, start Sync here with SYNC_HOST=0.0.0.0, then create a key and give it to that laptop's desktop agent. The key is shown once."),
      h("button", { class: "btn small", onclick: async () => {
        const r = await act(() => api("/me/agent-key", { body: {} }));
        if (r) keyOut.replaceChildren(h("div", { class: "field" }, h("label", null, "Run on the other laptop"),
          h("input", { class: "input", readonly: true, value: `python -m sync.desktop.agent --server http://${location.hostname === "127.0.0.1" || location.hostname === "localhost" ? "THIS-LAPTOP-IP" : location.hostname}:${location.port || 80} --token ${r.token}`, onclick: (e) => e.target.select() })));
      } }, icon("lock"), "Create key"), keyOut));
  return { main, rail: h("div", { class: "rail" }, privacyCard()), save: null };
}

const TABS = [["listening", "Listening & privacy"], ["profile", "Profile & nudges"], ["runtime", "Desktop & runtime"]];

export async function render(app, tab = "listening") {
  if (!TABS.some(([k]) => k === tab)) tab = "listening";
  const t = await { listening: listeningTab, profile: profileTab, runtime: agentTab }[tab](app);
  return {
    crumbs: ["Settings"],
    node: h("div", null,
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, "Settings"), h("h1", null, tab === "listening" ? "You decide when Sync listens." : tab === "profile" ? "Make Sync yours." : "This computer."),
        h("p", { class: "lede" }, tab === "listening" ? "Auto-listen, per-app rules and privacy. Changes apply to the next call." : tab === "profile" ? "Your name, languages, how often Sync nudges, and documents for all your calls." : "Local models, speech recognition, the desktop agent and on-screen nudges.")),
      t.save ? h("div", { class: "head-actions" }, h("button", { class: "btn primary", onclick: t.save }, "Save changes")) : null),
      h("div", { class: "tabs" }, TABS.map(([k, l]) => h("button", { class: k === tab ? "on" : "", onclick: () => app.go(`#/settings${k === "listening" ? "" : "/" + k}`) }, l))),
      h("div", { class: "cols" }, h("div", { class: "stack-lg" }, t.main), t.rail)),
  };
}
