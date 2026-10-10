/* What Sync learned about how you work: your playbook, the questions that worked, and every change. Yours alone. */
import { act, api, confirmBox, h, historyGrid, icon, modal, when } from "../ui.js";

const KINDS = { habit: "Habit", style: "Style", avoid: "Fewer nudges", effective_question: "Question that worked" };
const HINT = { habit: "Something you tend to leave open; Sync raises it earlier in your calls.", style: "How you like to write; used for follow-up drafts.",
  avoid: "Nudge types you keep dismissing; Sync shows them less.", effective_question: "A suggested question you actually asked; Sync suggests it again when it fits." };

function addModal(app, kind) {
  const sel = h("select", { class: "input" }, Object.entries(KINDS).map(([k, l]) => h("option", { value: k, selected: k === kind }, l)));
  const text = h("textarea", { class: "input", placeholder: kind === "effective_question" ? "e.g. What would stop you signing this week?" : "e.g. Keep follow-ups under 100 words, no bullet points." });
  modal("Teach Sync", h("div", null, h("div", { class: "field", style: "margin-top:0" }, h("label", null, "Kind"), sel),
    h("div", { class: "field" }, h("label", null, "What should Sync remember?"), text)), [
    { label: "Cancel", run: () => {} },
    { label: "Add", kind: "primary", run: async () => {
      if (!text.value.trim()) { text.focus(); return true; }
      if (!(await act(() => api("/me/playbook", { body: { kind: sel.value, text: text.value } }), "Added"))) return true;
      app.rerender(); return false;
    } }]);
}

function entryCard(app, e) {
  const card = h("div", { class: "card" });
  const view = () => card.replaceChildren(
    h("div", { class: "spread" }, h("span", { class: `chip ${e.active ? "green" : ""}` }, KINDS[e.kind] || e.kind), h("span", { class: "meta" }, e.active ? when(e.created_at, false) : "Turned off")),
    h("div", { style: `font-weight:600;font-size:15px;margin-top:10px;${e.active ? "" : "color:var(--muted)"}` }, e.text),
    h("div", { class: "meta", style: "margin-top:4px" }, HINT[e.kind] || ""),
    e.kind === "habit" && (e.evidence || []).length ? h("div", { class: "quote" }, "Seen in ", e.evidence.map((sid, i) => [i ? ", " : "", h("a", { class: "src", href: `#/conversations/${sid}` }, `conversation ${sid}`)])) : null,
    h("div", { class: "actions-dot", style: "margin-top:12px" },
      h("button", { class: "textbtn", onclick: () => {
        const t = h("textarea", { class: "input", value: e.text });
        card.replaceChildren(t, h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "btn small primary", onclick: async () => {
          const r = await act(() => api(`/me/playbook/${e.id}`, { method: "PATCH", body: { text: t.value } }), "Saved");
          if (r) { Object.assign(e, r); view(); }
        } }, "Save"), h("button", { class: "btn small", onclick: view }, "Cancel")));
      } }, "Edit"),
      h("button", { class: "textbtn", onclick: async () => {
        const r = await act(() => api(`/me/playbook/${e.id}`, { method: "PATCH", body: { active: !e.active } }), e.active ? "Turned off" : "Turned on");
        if (r) { Object.assign(e, r); view(); }
      } }, e.active ? "Turn off" : "Turn on"),
      h("button", { class: "textbtn red", onclick: async () => {
        if (await confirmBox("Delete this?", "Sync forgets it. The change history keeps a record.", "Delete") && await act(() => api(`/me/playbook/${e.id}`, { method: "DELETE" }), "Deleted")) card.remove();
      } }, "Delete")));
  view();
  return card;
}

export async function render(app, tab = "playbook") {
  if (!["playbook", "questions", "history"].includes(tab)) tab = "playbook";
  const [entries, history] = await Promise.all([api("/me/playbook"), api("/me/playbook/history")]);
  const playbook = entries.filter((e) => e.kind !== "effective_question");
  const questions = entries.filter((e) => e.kind === "effective_question");
  const body = tab === "history" ? h("div", { class: "card", style: "margin-top:24px" }, h("h2", null, "Change history"), historyGrid(history))
    : h("div", { style: "margin-top:24px" }, (tab === "playbook" ? playbook : questions).map((e) => entryCard(app, e)),
      !(tab === "playbook" ? playbook : questions).length ? h("div", { class: "panel" }, h("b", null, tab === "playbook" ? "Nothing learned yet" : "No questions yet"),
        h("p", { class: "muted", style: "margin:6px 0 0" }, tab === "playbook"
          ? "After a few real calls Sync notices what you tend to leave open and which nudges you ignore. You can also teach it directly."
          : "When you ask a question Sync suggested, it's saved here and suggested again in similar calls.")) : null);

  return {
    crumbs: ["What Sync learned"],
    node: h("div", null,
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, "What Sync learned · only you see this"), h("h1", null, "How you work, remembered."),
        h("p", { class: "lede" }, "Sync adapts to you from real calls (never sample calls). Edit, turn off or delete anything.")),
      h("div", { class: "head-actions" }, h("button", { class: "btn primary", onclick: () => addModal(app, tab === "questions" ? "effective_question" : "style") }, icon("plus"), "Teach Sync"))),
      h("div", { class: "tabs" }, [["playbook", `My playbook · ${playbook.length}`], ["questions", `Question bank · ${questions.length}`], ["history", "Change history"]]
        .map(([k, l]) => h("button", { class: k === tab ? "on" : "", onclick: () => app.go(`#/learned${k === "playbook" ? "" : "/" + k}`) }, l))),
      h("div", { class: "cols", style: "margin-top:0" }, body,
        h("div", { class: "rail", style: "padding-top:24px" },
          h("div", { class: "card" }, h("h2", null, "How Sync learns"),
            h("ul", { class: "tight-list" }, h("li", null, "Goals you leave open in 2 of your last 3 calls become a habit to raise earlier."),
              h("li", null, "Suggested questions you actually ask go into your question bank."),
              h("li", null, "Nudge types you dismiss often (and never use) are shown less."),
              h("li", null, "Every Useful and Dismiss tunes which nudges you see."))),
          h("div", { class: "card" }, h("h2", null, "Learning controls"),
            h("p", { class: "muted small", style: "margin-top:-6px" }, "Reset forgets everything above and how your nudges were tuned. Project memory is not touched."),
            h("button", { class: "btn danger small", onclick: async () => {
              if (await confirmBox("Reset my playbook?", "Sync forgets your habits, question bank and nudge tuning. This can't be undone.", "Reset") &&
                await act(() => api("/me/playbook/reset", { body: {} }), "Playbook reset")) app.rerender();
            } }, "Reset my playbook")),
          h("div", { class: "panel" }, h("div", { class: "row", style: "gap:8px" }, icon("lock", { width: 16, height: 16 }), h("b", null, "Private to you")),
            h("p", { class: "muted small", style: "margin:6px 0 0" }, "What Sync learns about you is never shared with teammates, even in shared projects."))))),
  };
}
