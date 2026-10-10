/* Project memory: everything a project knows, editable by every member, with an append-only history. */
import { act, api, cap, confirmBox, convTitle, dropdown, h, historyGrid, icon, minutes, modal, appName, sourceQuote, statusChip,
  STATUSES, stripPrefix, toast, TYPE_LABEL, TYPES, when } from "../ui.js";
import { convStatus } from "./conversations.js";

/* "Brief me": where things stand, instantly, before a call. */
export async function briefModal(pid) {
  let b;
  try { b = await api(`/projects/${pid}/brief`); } catch (e) { toast(e.message); return; }
  const sec = (title, rows) => (rows && rows.length ? h("div", { style: "margin-top:18px" }, h("div", { class: "label" }, title),
    h("ul", { class: "tight-list" }, rows.map((r) => h("li", null, r)))) : null);
  modal(`Brief · ${b.project.name}`, h("div", null,
    b.project.objective ? h("p", { class: "muted", style: "margin-top:0" }, b.project.objective) : null,
    b.project.status ? h("div", { class: "panel", style: "padding:14px 16px" }, b.project.status) : null,
    b.last_call ? h("div", { style: "margin-top:16px" }, h("div", { class: "label" }, "Last conversation"), h("p", { style: "margin:0" }, b.last_call)) : null,
    sec("Open conflicts", b.conflicts), sec("Open questions", b.open_questions), sec("Our commitments", b.our_commitments),
    sec("Their commitments", b.their_commitments), sec("Ask next", b.suggested_questions), sec("Already known", b.known),
    sec("People", b.people.map((p) => [p.name, p.role, p.org].filter(Boolean).join(" · ") + (p.concerns.length ? ` — cares about ${p.concerns.join(", ")}` : ""))),
    [b.conflicts, b.open_questions, b.our_commitments, b.their_commitments, b.known, b.people].every((x) => !x.length) && !b.last_call
      ? h("p", { class: "empty" }, "Nothing on record yet. File a call under this project, or add items by hand.") : null),
  [{ label: "Close", run: () => {} }]);
}

function detailsModal(app, p) {
  const name = h("input", { class: "input", value: p.name });
  const objective = h("input", { class: "input", value: p.objective });
  const brief = h("textarea", { class: "input", value: p.brief });
  const context = h("textarea", { class: "input", value: p.context });
  modal("Edit project details", h("div", null,
    h("div", { class: "field", style: "margin-top:0" }, h("label", null, "Name"), name),
    h("div", { class: "field" }, h("label", null, "Objective"), objective),
    h("div", { class: "field" }, h("label", null, "Where things stand"), brief, h("div", { class: "hint" }, "The project summary. Sync updates it after each call; edit it any time.")),
    h("div", { class: "field" }, h("label", null, "Context"), context)), [
    { label: "Cancel", run: () => {} },
    { label: "Save", kind: "primary", run: async () => {
      const r = await act(() => api(`/projects/${p.id}`, { method: "PATCH", body: { name: name.value, objective: objective.value, brief: brief.value, context: context.value } }), "Saved");
      if (!r) return true;
      await app.loadProjects(); app.rerender(); return false;
    } }]);
}

/* ---------------------------------------------------------------- items */

function itemEditor(it, onSave, onCancel) {
  const type = h("select", { class: "input" }, TYPES.map((t) => h("option", { value: t, selected: t === it.type }, TYPE_LABEL[t])));
  const status = h("select", { class: "input" }, STATUSES.map((s) => h("option", { value: s, selected: s === it.status }, cap(s))));
  const text = h("textarea", { class: "input", style: "min-height:64px", value: it.text || "" });
  const owner = h("input", { class: "input", value: it.owner === "me" ? "" : it.owner || "", placeholder: "Owner (blank = no one)" });
  const mine = h("input", { type: "checkbox", checked: it.owner === "me" });
  const due = h("input", { class: "input", value: it.due || "", placeholder: "Due, e.g. Friday 16 Oct" });
  const answer = h("input", { class: "input", value: it.answer || "", placeholder: "Answer (for questions)" });
  return h("div", { class: "item-edit-box" },
    h("div", { class: "item-edit" }, type, h("div", { class: "full-mid" }), status, h("div", { class: "full" }, text),
      h("div", { class: "full row", style: "gap:12px;flex-wrap:nowrap" }, h("label", { class: "row small", style: "white-space:nowrap" }, mine, "Mine"), owner, due),
      h("div", { class: "full" }, answer)),
    h("div", { class: "row", style: "margin-top:10px" },
      h("button", { class: "btn primary small", onclick: () => {
        if (!text.value.trim()) { text.focus(); return; }
        onSave({ type: type.value, status: status.value, text: text.value, owner: mine.checked ? "me" : owner.value, due: due.value, answer: answer.value });
      } }, "Save"),
      h("button", { class: "btn small", onclick: onCancel }, "Cancel")));
}

export function itemNode(it, { onChanged, eventsFor, showProject, edit = false, onCancel = null } = {}) {
  const box = h("div", { class: "item" });
  const view = () => {
    const metaBits = [it.owner_label ? `Owner: ${it.owner_label}` : "", it.due ? `Due ${it.due}` : "", it.condition ? `If ${it.condition}` : "",
      it.speaker_label && !it.source && it.created_by !== "user" && it.created_by !== "agent" ? `Said by ${it.speaker_label}` : "", it.added_by && it.created_by === "user" ? `Added by ${it.added_by}` : ""].filter(Boolean);
    const hist = h("div");
    const actions = h("div", { class: "actions-dot" },
      it.type === "commitment" && it.status !== "done" ? h("button", { class: "textbtn green", onclick: () => save({ status: "done" }, "Marked done") }, "Mark done") : null,
      h("button", { class: "textbtn", onclick: () => { box.replaceChildren(itemEditor(it, (body) => save(body, "Saved"), view)); } }, "Edit"),
      eventsFor ? h("button", { class: "textbtn", onclick: async () => {
        if (hist.childNodes.length) { hist.replaceChildren(); return; }
        const evs = (await eventsFor()).filter((e) => e.entity === "item" && e.entity_id === it.id);
        hist.replaceChildren(h("div", { class: "mini-history" }, historyGrid(evs)));
      } }, "History") : null,
      h("button", { class: "textbtn", onclick: async () => {
        if (!(await confirmBox("Remove this item?", "It disappears from the project. The history keeps a record of what it said.", "Remove"))) return;
        if (await act(() => api(`/items/${it.id}`, { method: "DELETE" }), "Removed")) { box.remove(); onChanged && onChanged(); }
      } }, "Delete"));
    box.replaceChildren(
      h("div", { class: "top" }, h("div", { class: "row", style: "gap:8px" }, h("span", { class: "chip" }, TYPE_LABEL[it.type] || it.type), statusChip(it.status),
        it.confirmed === false ? h("span", { class: "chip amber" }, "Awaiting review") : null,
        showProject && it.project ? h("span", { class: "meta" }, it.project) : null), actions),
      h("div", { class: "text" }, it.text),
      it.answer ? h("div", { style: "margin-top:4px" }, h("span", { class: "muted" }, "Answer: "), it.answer) : null,
      metaBits.length ? h("div", { class: "meta", style: "margin-top:4px" }, metaBits.join(" · ")) : null,
      sourceQuote(it.source, it.quote),
      hist);
  };
  const save = async (body, msg) => {
    const r = await act(() => api(`/items/${it.id}`, { method: "PATCH", body }), msg);
    if (r) { Object.assign(it, r, { source: it.source, project: it.project }); view(); onChanged && onChanged(); }
  };
  if (edit) box.replaceChildren(itemEditor(it, (body) => save(body, "Saved"), () => (onCancel ? onCancel() : view())));
  else view();
  return box;
}

function addItemForm(pid, onAdded) {
  return itemEditor({ type: "fact", status: "open", text: "" }, async (body) => {
    if (body.type === "commitment" || body.type === "decision") body.status = body.status === "open" ? "agreed" : body.status;
    const r = await act(() => api(`/projects/${pid}/items`, { body }), "Added");
    if (r) onAdded(r);
  }, () => onAdded(null));
}

function conflictCard(c, items, onDone) {
  const a = items.find((i) => i.id === c.item_a); const b = items.find((i) => i.id === c.item_b);
  const resolve = async (keep, label) => {
    if (await act(() => api(`/conflicts/${c.id}/resolve`, { body: { resolution: label, keep_item_id: keep } }), "Resolved")) onDone();
  };
  return h("div", { class: "card amber", style: "margin-bottom:16px" },
    h("div", { class: "eyebrow k-caution" }, "Conflict"), h("div", { style: "font-weight:600;margin:8px 0" }, c.explanation),
    h("div", { class: "row" },
      a ? h("button", { class: "btn small", onclick: () => resolve(a.id, `kept: ${a.text}`) }, `Keep “${a.text.slice(0, 50)}”`) : null,
      b ? h("button", { class: "btn small", onclick: () => resolve(b.id, `kept: ${b.text}`) }, `Keep “${b.text.slice(0, 50)}”`) : null,
      h("button", { class: "textbtn", onclick: () => resolve(null, "both are fine") }, "Both are fine")));
}

async function itemsTab(app, p, ctx) {
  const items = await api(`/projects/${p.id}/items`);
  const conflicts = (await api(`/projects/${p.id}/conflicts`)).filter((c) => c.status === "open");
  let ftype = ""; let fstatus = "";
  const list = h("div");
  const formSlot = h("div");
  const draw = () => {
    const rows = items.filter((i) => (!ftype || i.type === ftype) && (!fstatus || i.status === fstatus)).slice().reverse();
    list.replaceChildren(...(rows.length ? rows.map((i) => itemNode(i, { eventsFor: ctx.events, onChanged: ctx.refreshRail }))
      : [h("p", { class: "empty", style: "padding:20px 0" }, items.length ? "Nothing matches these filters." : "Nothing on record yet. File a call under this project, or add an item.")]));
  };
  draw();
  const typeSel = h("select", { class: "input slim pill", onchange: () => { ftype = typeSel.value; draw(); } }, h("option", { value: "" }, "All types"), TYPES.map((t) => h("option", { value: t }, TYPE_LABEL[t])));
  const statusSel = h("select", { class: "input slim pill", onchange: () => { fstatus = statusSel.value; draw(); } }, h("option", { value: "" }, "All statuses"), STATUSES.map((s) => h("option", { value: s }, cap(s))));
  return h("div", null,
    conflicts.map((c) => conflictCard(c, items, () => app.rerender())),
    h("div", { class: "spread", style: "margin:24px 0 4px" }, h("div", { class: "eyebrow" }, "Project items"), h("div", { class: "row" }, typeSel, statusSel,
      h("button", { class: "btn", onclick: () => {
        formSlot.replaceChildren(h("div", { class: "card", style: "margin-top:12px" }, h("div", { class: "label" }, "New item"), addItemForm(p.id, (r) => {
          formSlot.replaceChildren();
          if (r) app.rerender();
        })));
      } }, icon("plus"), "Add item"))),
    formSlot, list, await recentHistory(p, ctx));
}

async function recentHistory(p, ctx) {
  const evs = (await ctx.events()).filter((e) => !e.type.startsWith("nudge.")).slice(0, 4);
  if (!evs.length) return null;
  return h("div", { class: "panel", style: "margin-top:32px" }, h("div", { class: "spread" }, h("h2", { style: "margin:0" }, "Append-only history"),
    h("a", { class: "link-arrow", href: `#/projects/${p.id}/history`, style: "color:var(--muted)" }, "View full history", icon("arrowUR"))),
  h("div", { style: "margin-top:16px" }, historyGrid(evs)));
}

async function sessionsTab(app, p) {
  const sessions = await api(`/projects/${p.id}/sessions`);
  return h("div", { style: "margin-top:12px" }, sessions.length ? sessions.map((s) => h("a", { class: "rowlink", href: `#/conversations/${s.id}` },
    h("div", null, h("b", null, convTitle(s)), h("div", { class: "meta" }, [when(s.started_at), appName(s.app) || (s.mode === "paste" ? "Pasted" : ""), minutes(s),
      s.user_id !== app.state.me.id ? `${s.owner_name}'s call` : ""].filter(Boolean).join(" · ")),
      (s.recap || {}).summary ? h("div", { class: "muted small", style: "margin-top:6px;max-width:680px" }, s.recap.summary) : null),
    h("div", { class: "row" }, convStatus(s), icon("arrowR"))))
    : h("p", { class: "empty", style: "padding:20px 0" }, "No conversations filed here yet. When Sync joins a call it asks which project it's about."));
}

async function peopleTab(app, p) {
  const people = await api(`/projects/${p.id}/people`);
  const wrap = h("div");
  const row = (x) => {
    const r = h("div", { class: "item" });
    const view = () => r.replaceChildren(h("div", { class: "spread" }, h("div", null, h("b", null, x.name), h("div", { class: "meta" }, [x.role, x.org].filter(Boolean).join(" · ") || "No role yet"),
      (x.concerns || []).length ? h("div", { class: "small", style: "margin-top:4px" }, "Cares about: ", x.concerns.join(", ")) : null,
      x.notes ? h("div", { class: "small muted", style: "margin-top:4px" }, x.notes) : null),
    h("div", { class: "actions-dot" }, h("button", { class: "textbtn", onclick: edit }, "Edit"), h("button", { class: "textbtn red", onclick: async () => {
      if (await confirmBox(`Remove ${x.name}?`, "They are removed from this project's people. The history keeps a record.", "Remove") &&
        await act(() => api(`/people/${x.id}`, { method: "DELETE" }), "Removed")) r.remove();
    } }, "Delete"))));
    const edit = () => {
      const f = { name: h("input", { class: "input", value: x.name }), role: h("input", { class: "input", value: x.role, placeholder: "Role" }),
        org: h("input", { class: "input", value: x.org, placeholder: "Organisation" }), notes: h("input", { class: "input", value: x.notes || "", placeholder: "Notes" }) };
      r.replaceChildren(h("div", { class: "item-edit", style: "grid-template-columns:1fr 1fr 1fr" }, f.name, f.role, f.org, h("div", { class: "full" }, f.notes)),
        h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "btn primary small", onclick: async () => {
          const res = await act(() => api(`/people/${x.id}`, { method: "PATCH", body: { name: f.name.value, role: f.role.value, org: f.org.value, notes: f.notes.value } }), "Saved");
          if (res) { Object.assign(x, res); view(); }
        } }, "Save"), h("button", { class: "btn small", onclick: view }, "Cancel")));
    };
    view();
    return r;
  };
  const f = { name: h("input", { class: "input", placeholder: "Name" }), role: h("input", { class: "input", placeholder: "Role" }), org: h("input", { class: "input", placeholder: "Organisation" }) };
  wrap.append(h("div", { class: "card flat", style: "margin-top:20px" }, h("div", { class: "label" }, "Add a person"),
    h("div", { class: "row", style: "flex-wrap:nowrap" }, f.name, f.role, f.org, h("button", { class: "btn small", onclick: async () => {
      if (!f.name.value.trim()) { f.name.focus(); return; }
      const res = await act(() => api(`/projects/${p.id}/people`, { body: { name: f.name.value, role: f.role.value, org: f.org.value } }), "Added");
      if (res) app.rerender();
    } }, icon("plus"), "Add"))),
  people.length ? people.map(row) : h("p", { class: "empty", style: "padding:20px 0" }, "No people yet. Sync adds the people it hears on calls filed here."));
  return wrap;
}

export function docsCard(docs, projectId, onChange, { compact = false } = {}) {
  const file = h("input", { type: "file", accept: ".txt,.md,.pdf,.csv", hidden: true, onchange: async () => {
    for (const f of file.files) {
      const fd = new FormData(); fd.append("file", f);
      if (projectId) fd.append("project_id", projectId);
      const r = await act(() => api("/documents", { body: fd }));
      if (r) toast(`Added “${r.name}” · ${r.facts.length} number${r.facts.length === 1 ? "" : "s"} found`);
    }
    onChange();
  } });
  const shown = compact ? docs.slice(0, 5) : docs;
  return h("div", null,
    shown.length ? h("div", { class: "list tight" }, shown.map((d) => h("div", { class: "spread" },
      h("div", null, h("div", { class: "row", style: "gap:6px" }, icon("file", { width: 14, height: 14 }), h("b", { style: "font-weight:500" }, d.name)),
        h("div", { class: "meta" }, [d.project_id ? `Added by ${d.added_by}` : "Personal · all your calls", `${d.facts} number${d.facts === 1 ? "" : "s"}`, when(d.created_at, false)].join(" · "))),
      compact ? null : h("button", { class: "textbtn red", onclick: async () => {
        if (await confirmBox(`Remove “${d.name}”?`, "Sync stops using it in calls and answers.", "Remove") && await act(() => api(`/documents/${d.id}`, { method: "DELETE" }), "Removed")) onChange();
      } }, "Remove"))))
      : h("p", { class: "empty" }, "No documents yet."),
    h("label", { class: "btn small", style: "margin-top:12px" }, icon("clip"), "Add document", file));
}

async function docsTab(app, p) {
  const docs = await api(`/documents?project_id=${p.id}`);
  return h("div", { class: "card", style: "margin-top:20px" }, h("h2", null, "Documents & notes"),
    h("p", { class: "muted small", style: "margin-top:-6px" }, "Price lists, contracts, policies. Sync answers questions from them during calls and warns when someone states a number that disagrees. Project documents are shared with every member; personal ones apply to all your calls."),
    docsCard(docs, p.id, () => app.rerender()));
}

async function membersTab(app, p) {
  const m = await api(`/projects/${p.id}/members`);
  const me = app.state.me;
  const link = m.invite_code ? `${location.origin}${location.pathname}#/join/${m.invite_code}` : "";
  return h("div", { class: "two", style: "margin-top:20px" },
    h("div", { class: "card" }, h("h2", null, "Members"),
      h("div", { class: "list tight" }, m.members.map((u) => h("div", { class: "spread" },
        h("div", { class: "row" }, h("span", { class: "avatar" }, u.name[0].toUpperCase()), h("div", null, h("b", { style: "font-weight:500" }, u.id === me.id ? `${u.name} (you)` : u.name),
          h("div", { class: "meta" }, u.id === m.created_by ? "Created the project" : `Joined ${when(u.added_at, false)}`))),
        (u.id === me.id || m.created_by === me.id) && m.members.length > 1 ? h("button", { class: "textbtn red", onclick: async () => {
          const self = u.id === me.id;
          if (!(await confirmBox(self ? "Leave this project?" : `Remove ${u.name}?`, self ? "You lose access to its memory and conversations." : `${u.name} loses access to this project.`, self ? "Leave" : "Remove"))) return;
          if (await act(() => api(`/projects/${p.id}/members/${u.id}`, { method: "DELETE" }), self ? "You left the project" : "Removed")) {
            await app.loadProjects(); app.go(self ? "#/" : `#/projects/${p.id}/members`);
          }
        } }, u.id === me.id ? "Leave" : "Remove") : null)))),
    h("div", { class: "card" }, h("h2", null, "Invite a teammate"),
      h("p", { class: "muted small", style: "margin-top:-6px" }, "Anyone with the link can join on this Sync (same laptop or your network). Members see and edit everything in this project; every change shows who made it."),
      link ? [h("input", { class: "input", value: link, readonly: true, onclick: (e) => e.target.select() }),
        h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "btn small primary", onclick: async () => { await navigator.clipboard?.writeText(link).catch(() => {}); toast("Invite link copied"); } }, icon("copy"), "Copy link"),
          h("button", { class: "btn small", onclick: async () => { if (await act(() => api(`/projects/${p.id}/invite`, { body: {} }), "New link created; the old one no longer works")) app.rerender(); } }, "New link"),
          h("button", { class: "textbtn red", onclick: async () => { if (await act(() => api(`/projects/${p.id}/invite`, { method: "DELETE" }), "Invite link turned off")) app.rerender(); } }, "Turn off"))]
        : h("button", { class: "btn primary small", onclick: async () => { if (await act(() => api(`/projects/${p.id}/invite`, { body: {} }), "Invite link created")) app.rerender(); } }, icon("link"), "Create invite link")));
}

async function historyTab(app, p, ctx) {
  const evs = (await ctx.events()).filter((e) => !e.type.startsWith("nudge."));
  return h("div", { class: "card", style: "margin-top:20px" }, h("div", { class: "spread" }, h("h2", null, "Append-only history"), h("span", { class: "meta" }, "Nothing here can be edited or deleted")),
    historyGrid(evs));
}

const TABS = [["items", "Items"], ["conversations", "Conversations"], ["people", "People"], ["documents", "Documents"], ["members", "Members"], ["history", "History"]];

export async function render(app, pid, tab = "items") {
  pid = Number(pid);
  if (!TABS.some(([k]) => k === tab)) tab = "items";
  const p = await api(`/projects/${pid}`);
  const b = p.brief_card;
  let eventsCache = null;
  const ctx = { events: async () => (eventsCache ??= await api(`/projects/${pid}/events?limit=500`)), refreshRail: () => { eventsCache = null; } };
  const body = await { items: itemsTab, conversations: sessionsTab, people: peopleTab, documents: docsTab, members: membersTab, history: historyTab }[tab](app, p, ctx);
  const docs = await api(`/documents?project_id=${pid}`);
  const pinned = app.pinned()?.id === pid;
  const more = dropdown(h("button", { class: "btn" }, "More", icon("chevron")), () => [
    { label: "Edit details", run: () => detailsModal(app, p) },
    { label: pinned ? "Unpin from sidebar" : "Pin to sidebar", run: () => { app.pin(pinned ? null : pid); toast(pinned ? "Unpinned" : "Pinned"); app.rerender(); } },
    { label: p.status === "closed" ? "Reopen project" : "Close project", run: async () => {
      if (await act(() => api(`/projects/${pid}`, { method: "PATCH", body: { status: p.status === "closed" ? "active" : "closed" } }), p.status === "closed" ? "Reopened" : "Closed")) { await app.loadProjects(); app.rerender(); }
    } }]);

  const rail = h("div", { class: "rail" },
    h("div", { class: "panel" }, h("div", { class: "spread" }, h("div", { class: "eyebrow" }, "Current brief"), h("button", { class: "textbtn", onclick: () => detailsModal(app, p) }, "Edit")),
      h("p", { style: "margin:10px 0 0" }, p.brief || b.last_call || "No summary yet. It's written after the first call filed here."),
      h("div", { class: "tagline" }, h("span", { class: "chip line" }, `${b.open_questions.length} open question${b.open_questions.length === 1 ? "" : "s"}`),
        h("span", { class: "chip line" }, `${b.our_commitments.length + b.their_commitments.length} open commitment${b.our_commitments.length + b.their_commitments.length === 1 ? "" : "s"}`),
        b.conflicts.length ? h("span", { class: "chip amber" }, `${b.conflicts.length} conflict${b.conflicts.length === 1 ? "" : "s"}`) : null),
      b.suggested_questions.length ? h("div", { style: "margin-top:14px" }, h("div", { class: "label" }, "Ask next"), h("ul", { class: "tight-list" }, b.suggested_questions.map((q) => h("li", null, stripPrefix(q))))) : null),
    h("div", { class: "card" }, h("div", { class: "spread" }, h("h2", { style: "margin:0" }, "People"), h("a", { class: "textbtn green", href: `#/projects/${pid}/people` }, "Manage")),
      b.people.length ? h("div", { class: "list tight", style: "margin-top:10px" }, b.people.slice(0, 5).map((x) => h("div", null, h("b", { style: "font-weight:500" }, x.name),
        h("div", { class: "meta" }, [x.role, x.org].filter(Boolean).join(" · ") || "—")))) : h("p", { class: "empty" }, "No people yet.")),
    h("div", { class: "card" }, h("div", { class: "spread" }, h("h2", { style: "margin:0" }, "Documents & notes"), h("a", { class: "textbtn green", href: `#/projects/${pid}/documents` }, "Manage")),
      h("div", { style: "margin-top:10px" }, docsCard(docs, pid, () => app.rerender(), { compact: true }))),
    h("div", { class: "card" }, h("div", { class: "spread" }, h("h2", { style: "margin:0" }, "Members"), h("a", { class: "textbtn green", href: `#/projects/${pid}/members` }, "Invite")),
      h("p", { class: "meta", style: "margin:8px 0 0" }, p.members.map((m) => (m.id === app.state.me.id ? "You" : m.name)).join(", "))));

  return {
    crumbs: [["Projects", "#/projects"], p.name],
    node: h("div", null,
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, `Project memory${p.members.length > 1 ? " · shared" : ""}${p.status === "closed" ? " · closed" : ""}`),
        h("h1", null, p.name), h("p", { class: "lede" }, p.objective ? `Objective: ${p.objective}` : "No objective yet. Add one under More → Edit details.")),
      h("div", { class: "head-actions" }, more,
        h("button", { class: "btn", onclick: () => { app.askProject = pid; app.go("#/ask"); } }, icon("chat"), "Ask Sync"),
        h("button", { class: "btn primary", onclick: () => briefModal(pid) }, icon("file"), "Brief me"))),
      h("div", { class: "tabs" }, TABS.map(([k, label]) => h("button", { class: k === tab ? "on" : "", onclick: () => app.go(`#/projects/${pid}${k === "items" ? "" : "/" + k}`) }, label))),
      h("div", { class: "cols", style: "margin-top:0" }, h("div", null, body), h("div", { style: "padding-top:24px" }, rail))),
  };
}
