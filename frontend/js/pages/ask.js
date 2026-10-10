/* Ask Sync: chat about everything, or one project. Answers cite where they came from; changes need your OK. */
import { act, api, auth, dropdown, h, icon, logo, markdown, TYPE_LABEL } from "../ui.js";

const threads = new Map();  // project id ("all") -> messages, kept while the app is open
const GENERAL = ["What did I promise this week?", "What's still open across my projects?", "Which questions are unanswered?"];
const PROJECT = ["Where do things stand?", "What did we agree on price?", "What did they promise us, and by when?", "Summarise the last call"];

function sourceShelf(sources) {
  return sources.length ? h("div", { class: "list tight" }, sources.slice(0, 6).map((s) => h("div", null,
    h("div", { class: "row", style: "gap:6px" }, h("span", { class: "chip" }, s.kind === "said" ? "Said" : s.kind === "doc" ? "Document" : TYPE_LABEL[s.kind] || s.kind),
      s.project ? h("span", { class: "meta" }, s.project) : null),
    h("div", { class: "small", style: "margin-top:4px" }, `“${s.text.length > 160 ? s.text.slice(0, 159) + "…" : s.text}”`),
    h("div", { class: "meta" }, s.source))))
    : h("p", { class: "empty" }, "Sources for the latest answer appear here.");
}

function changeCard(ev) {
  const r = ev.result || {}; const a = ev.args || {};
  if (r.error) return null;
  const what = { create_project: `Created project “${r.name || a.name}”`, add_item: `Added ${TYPE_LABEL[a.type] || "note"}: ${a.text}`,
    update_item: `Updated an item${r.changes ? ": " + Object.entries(r.changes).map(([k, v]) => `${k} → ${v}`).join(", ") : ""}`,
    add_person: `Added ${a.name} to the project` }[ev.name];
  if (!what || r.needs_confirmation) return null;
  return h("div", { class: "agent-card" }, h("div", { class: "eyebrow", style: "color:var(--green)" }, "Changed by Sync · recorded in history"), h("div", { style: "margin-top:6px;font-weight:600" }, what));
}

function confirmCard(ev) {
  const card = h("div", { class: "agent-card", style: "border-color:var(--amber-line);background:var(--amber-soft)" });
  card.append(h("div", { class: "eyebrow k-caution" }, "Needs your OK"), h("div", { style: "margin:6px 0 12px;font-weight:600" }, ev.description),
    h("div", { class: "row" }, h("button", { class: "btn small primary", onclick: async () => {
      const r = await act(() => api(`/chat/confirm/${ev.id}`, { body: {} }));
      card.replaceChildren(h("div", { class: "meta" }, r && !r.error ? "✓ Done. Recorded in the project history." : `Not changed: ${(r && r.error) || "error"}`));
      ev.done = true;
    } }, "Confirm"), h("button", { class: "btn small", onclick: () => { card.replaceChildren(h("div", { class: "meta" }, "Not changed.")); ev.done = true; } }, "Cancel")));
  return ev.done ? null : card;
}

export async function render(app) {
  const projects = app.state.projects.filter((p) => p.status !== "closed");
  let pid = app.askProject && projects.some((p) => p.id === app.askProject) ? app.askProject : null;
  app.askProject = null;
  let brief = null;
  const msgsEl = h("div", { class: "thread" });
  const shelf = h("div");
  const suggestions = h("div");
  const chip = h("span");
  const box = h("textarea", { placeholder: "Ask about a project, a person, a number or a promise…", rows: 2 });
  const send = h("button", { class: "btn primary small" }, icon("send"), "Send");
  let busy = false;
  const thread = () => { const k = pid ?? "all"; if (!threads.has(k)) threads.set(k, []); return threads.get(k); };

  const drawMsg = (m) => (m.role === "user"
    ? h("div", { class: "you-msg", style: "margin-top:22px" }, h("span", { class: "eyebrow" }, "You"), h("b", null, m.content))
    : h("div", { class: "sync-msg" }, h("div", { class: "who" }, logo(), "Sync", m.sources?.length ? h("span", { class: "chip green" }, icon("link"), "Source-linked") : null),
      m.content ? markdown(m.content) : h("div", { class: "body muted" }, m.pending ? "Thinking… (the local model may take a moment, longer while it finishes a recap)" : ""),
      !m.pending && m.content ? (m.sources || []).slice(0, 2).map((x) => h("div", { class: "quote" }, `“${x.text.length > 140 ? x.text.slice(0, 139) + "…" : x.text}”`,
        h("div", { class: "meta" }, [x.source, x.project].filter(Boolean).join(" · ")))) : null,
      (m.events || []).map((ev) => (ev.type === "confirm" ? confirmCard(ev) : changeCard(ev)))));
  const drawThread = () => {
    const t = thread();
    msgsEl.replaceChildren(...(t.length ? t.map(drawMsg) : [h("div", { class: "panel", style: "margin-top:24px" },
      h("b", null, pid ? `Ask about ${projects.find((p) => p.id === pid).name}` : "Ask about anything you've discussed"),
      h("p", { class: "muted", style: "margin:6px 0 0" }, "Sync answers from your calls, project memory and documents, and says where each answer came from. It can also add or update things for you — every change is recorded with your name."))]));
    const last = [...t].reverse().find((m) => m.role === "assistant");
    shelf.replaceChildren(sourceShelf(last?.sources || []));
  };
  const drawSuggestions = () => {
    const name = pid ? projects.find((p) => p.id === pid).name : "";
    const qs = pid ? [...PROJECT, ...(brief?.open_questions?.length ? [`Which questions are still open on ${name}?`] : [])] : GENERAL;
    suggestions.replaceChildren(...qs.map((q) => h("button", { class: "suggest", onclick: () => { box.value = q; ask(); } }, h("span", null, q), icon("arrowR"))));
  };
  const drawChip = () => chip.replaceChildren(dropdown(h("button", { class: "chip line drop-trigger", style: "padding:6px 10px;font-size:13px" },
    icon("projects"), pid ? projects.find((p) => p.id === pid).name : "All projects", icon("chevron")),
  () => [{ label: "All projects", run: () => pick(null) }, ...projects.map((p) => ({ label: p.name, run: () => pick(p.id) }))]));
  const pick = async (id) => {
    pid = id; brief = null; drawChip(); drawThread(); drawSuggestions();
    if (pid) { try { brief = await api(`/projects/${pid}/brief`); drawSuggestions(); } catch { /* ignore */ } }
  };

  async function ask() {
    const q = box.value.trim();
    if (!q || busy) return;
    busy = true; send.disabled = true; box.value = "";
    const t = thread();
    t.push({ role: "user", content: q });
    const reply = { role: "assistant", content: "", sources: [], events: [], pending: true };
    t.push(reply);
    drawThread();
    const replyEl = () => msgsEl.lastChild;
    const redraw = () => { const n = drawMsg(reply); replyEl().replaceWith(n); n.scrollIntoView({ block: "end", behavior: "smooth" }); };
    try {
      const res = await fetch("/chat", { method: "POST", headers: { "Content-Type": "application/json", "X-Sync-Token": auth.token },
        body: JSON.stringify({ project_id: pid, messages: t.filter((m) => m !== reply).map((m) => ({ role: m.role, content: m.content })) }) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `${res.status}`);
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      let raf = 0;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
          if (!line.startsWith("data:")) continue;
          const ev = JSON.parse(line.slice(5));
          if (ev.type === "sources") { reply.sources = ev.sources; shelf.replaceChildren(sourceShelf(ev.sources)); }
          else if (ev.type === "token") { reply.content += ev.text; reply.pending = false; }
          else if (ev.type === "tool" || ev.type === "confirm") { reply.events.push(ev); if (ev.type === "tool" && !ev.result?.error && ev.name !== "search_memory" && ev.name !== "get_project") app.loadProjects(); }
          if (!raf) raf = requestAnimationFrame(() => { raf = 0; redraw(); });
        }
      }
      reply.pending = false;
      // a confirm card replaces its tool card
      reply.events = reply.events.filter((ev) => !(ev.type === "tool" && ev.result?.needs_confirmation));
      if (!reply.content && !reply.events.length) reply.content = "I couldn't find anything on that.";
    } catch (e) {
      reply.pending = false;
      reply.content = `Sorry, something went wrong: ${e.message}`;
    }
    redraw(); shelf.replaceChildren(sourceShelf(reply.sources));
    busy = false; send.disabled = false; box.focus();
  }
  send.onclick = ask;
  box.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });

  drawChip(); drawThread(); drawSuggestions();
  if (pid) pick(pid);
  setTimeout(() => box.focus(), 0);
  return {
    crumbs: ["Ask Sync"],
    node: h("div", null,
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, "Ask Sync · private to you"), h("h1", null, "Ask about anything you've discussed."),
        h("div", { class: "row", style: "margin-top:4px" }, chip, h("span", { class: "meta" }, "Answers cite the call, the person and the date."))),
      h("div", { class: "head-actions" }, h("button", { class: "btn", onclick: () => { threads.set(pid ?? "all", []); drawThread(); } }, "New chat"))),
      h("div", { class: "cols" },
        h("div", null, msgsEl, h("div", { class: "composer" }, box, h("div", { class: "spread" }, h("span", { class: "meta" }, "Enter to send · Shift+Enter for a new line"), send))),
        h("div", { class: "rail" }, h("div", { class: "card" }, h("h2", null, "Continue the thread"), suggestions),
          h("div", { class: "card" }, h("h2", null, "Source shelf"), shelf)))),
  };
}
