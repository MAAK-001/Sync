/* Every conversation you can see: live ones, recaps to review, sample calls and pasted chats. */
import { act, api, appName, convTitle, h, icon, minutes, modal, segmented, toast, when } from "../ui.js";

export function convStatus(s) {
  if (s.status === "live") return h("span", { class: "chip red" }, "● Live");
  if (s.status === "detected") return h("span", { class: "chip" }, "Starting");
  if (!s.project_id) return h("span", { class: "chip amber" }, "Not filed");
  if (s.recap_status === "refining") return h("span", { class: "chip amber" }, "Refining recap");
  if (s.recap_status === "refined") return h("span", { class: "chip green" }, "Recap ready");
  return h("span", { class: "chip" }, "Draft recap");
}

/* Pick a scripted sample call and play it as your own live call (nudges, overlay and project choice included). */
export async function playSample(app) {
  let samples;
  try { samples = await api("/samples"); } catch (e) { toast(e.message); return; }
  if (!samples.length) { toast("No sample calls are installed."); return; }
  if (app.state.live.some((s) => s.user_id === app.state.me.id)) { toast("You already have a conversation live. Stop it first."); return; }
  const close = modal("Play a sample call", h("div", null,
    h("p", { class: "muted", style: "margin-top:0" }, "A scripted conversation plays in real time as if you were on a call. Watch the nudges here and on your screen. Sample calls never change what Sync learns about you."),
    h("div", { class: "list" }, samples.map((s) => h("button", { class: "suggest", onclick: async () => {
      close();
      const r = await act(() => api(`/samples/${s.name}/play`, { body: {} }));
      if (r) { app.wantSample = true; toast(`Starting “${r.title}” · about ${Math.round(r.seconds / 60) || 1} min`); }
    } }, h("span", null, h("b", null, s.title), h("div", { class: "meta" }, `${s.caller ? s.caller + " · " : ""}${s.lines} lines`)), icon("play"))))),
  [{ label: "Close", run: () => {} }]);
}

function pasteModal(app) {
  const title = h("input", { class: "input", placeholder: "e.g. WhatsApp chat with Priya" });
  const proj = h("select", { class: "input" }, h("option", { value: "" }, "Choose later"),
    app.state.projects.map((p) => h("option", { value: p.id }, p.name)));
  const text = h("textarea", { class: "input", style: "min-height:200px", placeholder: "Me: Can you do Thursday?\nPriya: Thursday works. I'll send the contract by Friday." });
  modal("Paste a conversation", h("div", null,
    h("p", { class: "muted", style: "margin-top:0" }, "A chat, email thread or meeting notes. One “Name: text” line per message; use “Me:” or your own name for yours. Sync files the facts, commitments and questions as it does for calls."),
    h("div", { class: "field" }, h("label", null, "Title"), title),
    h("div", { class: "field" }, h("label", null, "Project"), proj),
    h("div", { class: "field" }, h("label", null, "Conversation"), text)), [
    { label: "Cancel", run: () => {} },
    { label: "Add conversation", kind: "primary", run: async () => {
      if (!text.value.trim()) { text.focus(); return true; }
      const r = await act(() => api("/sessions/paste", { body: { text: text.value, title: title.value.trim() || "Pasted conversation", project_id: proj.value ? Number(proj.value) : null } }), "Added. The recap is being written.");
      if (!r) return true;
      app.go(`#/conversations/${r.id}`);
      return false;
    } }]);
}

export async function render(app) {
  const [sessions] = await Promise.all([api("/sessions?limit=200"), app.loadProjects()]);
  const pname = (id) => (app.state.projects.find((p) => p.id === id) || {}).name;
  let filter = "all";
  const list = h("div");
  const draw = () => {
    const rows = sessions.filter((s) => filter === "all" || (filter === "live" ? s.status === "live" : filter === "unfiled" ? !s.project_id && s.status === "ended" : s.user_id === app.state.me.id));
    list.replaceChildren(...(rows.length ? rows.map((s) => h("a", { class: "rowlink", href: `#/conversations/${s.id}` },
      h("div", null, h("b", null, convTitle(s)),
        h("div", { class: "meta" }, [when(s.started_at), appName(s.app) || (s.mode === "paste" ? "Pasted" : "In person"), minutes(s),
          pname(s.project_id) || "Not filed", s.user_id !== app.state.me.id ? `${s.owner_name}'s call` : ""].filter(Boolean).join(" · "))),
      h("div", { class: "row" }, (s.stats || {}).nudges ? h("span", { class: "meta" }, `${s.stats.nudges} nudge${s.stats.nudges === 1 ? "" : "s"}`) : null, convStatus(s), icon("arrowR"))))
      : [h("p", { class: "empty" }, filter === "all" ? "No conversations yet. Sync joins your calls automatically; you can also play a sample call or paste a chat." : "Nothing here.")]));
  };
  draw();
  return {
    crumbs: ["Conversations"],
    node: h("div", null,
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, "Conversations"), h("h1", null, "Every conversation."),
        h("p", { class: "lede" }, "Live calls, recaps to review, and the transcripts behind every item Sync filed.")),
      h("div", { class: "head-actions" }, h("button", { class: "btn", onclick: () => pasteModal(app) }, icon("clip"), "Paste a conversation"),
        h("button", { class: "btn primary", onclick: () => playSample(app) }, icon("play"), "Play a sample call"))),
      h("div", { style: "margin-top:28px" }, segmented([["all", "All"], ["mine", "Mine"], ["live", "Live"], ["unfiled", "Not filed"]], filter, (v) => { filter = v; draw(); })),
      h("div", { style: "margin-top:12px" }, list)),
  };
}
