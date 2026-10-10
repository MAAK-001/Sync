/* One conversation: the live companion while a call is on, the recap review once it ends. */
import { act, api, appName, confirmBox, convTitle, date, dropdown, h, icon, KIND, minutes, mmss, modal, sourceQuote, stripPrefix,
  toast, TYPE_LABEL, when, wsUrl } from "../ui.js";
import { itemNode } from "./project.js";

const NUDGE_CLASS = { caution: "caution", answer: "answer", fact: "fact" };

/* "Project: Acme ⌄", the user's choice (Sync never guesses). */
function projectPicker(app, s, onLinked) {
  const proj = app.state.projects.find((p) => p.id === s.project_id);
  const label = h("span", null, proj ? proj.name : "Choose a project");
  if (!s.is_owner) return h("span", { class: "meta" }, `Project: ${proj ? proj.name : "not filed yet"}`);
  const link = async (body) => {
    const r = await act(() => api(`/sessions/${s.id}/link`, { body }));
    if (!r) return;
    toast(body.new_project_name !== undefined ? `New project “${r.name}”` : `Filed under “${r.name}”`);
    s.project_id = r.project_id;
    await app.loadProjects();
    onLinked(r);
  };
  return dropdown(h("button", { class: `drop-trigger project-pick ${proj ? "" : "unset"}` }, h("span", { class: "muted" }, "Project:"), label, icon("chevron")), () => [
    ...app.state.projects.filter((p) => p.status !== "closed" && p.id !== s.project_id).map((p) => ({ label: p.name, run: () => link({ project_id: p.id }) })),
    { label: "+ New project…", run: () => {
      const name = h("input", { class: "input", placeholder: s.caller ? `e.g. ${s.caller}` : "Project name" });
      modal("File under a new project", h("div", null, h("div", { class: "field", style: "margin-top:0" }, h("label", null, "Name"), name),
        h("div", { class: "hint" }, "Leave it blank to name it after this call. You can rename it any time.")), [
        { label: "Cancel", run: () => {} },
        { label: "Create & file", kind: "primary", run: async () => { await link({ new_project: true, new_project_name: name.value.trim() }); } }]);
    } }]);
}

/* ================================================================= live companion */

function liveView(app, s) {
  const snap = s.live;
  const owner = s.is_owner;
  let names = snap.speakers || {};
  const nudges = (s.nudges || []).slice();
  let items = snap.items || [];
  let criteria = snap.criteria || [];
  let tab = "transcript";
  const started = date(snap.started_at || s.started_at);

  const who = (u) => (u.channel === "you" ? (owner ? "You" : s.owner_name) : names[u.speaker] || u.name || (u.speaker ? `Speaker ${u.speaker}` : "Them"));
  const tx = h("div", { class: "transcript" });
  const utt = (u) => h("div", { class: "utt" }, h("div", { class: "time" }, mmss(u.t)), h("div", null, h("b", null, who(u))), h("div", { class: "say" }, u.text));
  const drawTranscript = () => {
    tx.replaceChildren(...(transcript.length ? transcript.map(utt) : [h("p", { class: "empty", style: "padding:16px 0" }, "Listening… the transcript appears here as people speak.")]));
    tx.scrollTop = tx.scrollHeight;
  };
  const transcript = (snap.transcript || []).slice();

  const ledger = h("div");
  const drawLedger = () => ledger.replaceChildren(...(items.length ? items.slice().reverse().map((i) => h("div", { class: "utt", style: "grid-template-columns:110px 1fr" },
    h("div", { class: "eyebrow", style: "padding-top:3px" }, TYPE_LABEL[i.type] || i.type),
    h("div", null, h("b", null, i.text), h("div", { class: "meta" }, [i.status, i.owner === "me" ? (owner ? "yours" : `${s.owner_name}'s`) : i.owner, i.due ? `due ${i.due}` : ""].filter(Boolean).join(" · ")))))
    : [h("p", { class: "empty", style: "padding:16px 0" }, "Facts, numbers, commitments and questions appear here as they're said.")]));

  const tabBody = h("div");
  const tabs = h("div", { class: "tabs", style: "margin-top:34px" });
  const drawTabs = () => {
    tabs.replaceChildren(...[["transcript", "Live transcript"], ["ledger", `Live ledger · ${items.length}`]].map(([k, l]) => h("button", { class: k === tab ? "on" : "", onclick: () => { tab = k; drawTabs(); } }, l)));
    if (tab === "transcript") { tabBody.replaceChildren(tx); drawTranscript(); } else { tabBody.replaceChildren(ledger); drawLedger(); }
  };

  // the active nudge: one at a time, the newest one still showing
  const active = h("div");
  const earlier = h("div");
  let showHistory = false;
  const drawNudges = () => {
    const live = nudges.filter((n) => n.outcome === "shown");
    const n = live[live.length - 1];
    active.replaceChildren(n ? h("div", { class: `nudge-card ${NUDGE_CLASS[n.kind] || ""}` },
      h("div", { class: "spread" }, h("span", { class: "k" }, `${KIND[n.kind] || n.kind} · Active nudge`), h("span", { class: "meta" }, `One at a time · ${mmss(n.t)}`)),
      h("div", { class: "title" }, stripPrefix(n.text)),
      n.why ? h("div", { class: "muted" }, n.why) : null,
      owner ? h("div", { class: "row", style: "margin-top:16px" },
        h("button", { class: "btn small", onclick: () => feedback(n, "useful") }, icon("thumb"), "Useful"),
        h("button", { class: "btn small", onclick: () => feedback(n, "dismissed") }, icon("x"), "Dismiss")) : null)
      : h("div", { class: "nudge-card quiet" }, h("span", { class: "k" }, "Nudges"),
        h("div", { class: "muted", style: "margin-top:8px" }, "Nothing needs your attention. Sync speaks up only when something is missed, contradicted or worth asking.")));
    const past = nudges.filter((x) => x !== n);
    earlier.replaceChildren(past.length ? h("div", null, h("div", { class: "spread", style: "margin-top:12px" },
      h("span", { class: "meta" }, `Earlier: ${stripPrefix(past[past.length - 1].text).slice(0, 90)}`),
      h("button", { class: "link-arrow", onclick: () => { showHistory = !showHistory; drawNudges(); } }, showHistory ? "Hide history" : `Nudge history · ${past.length}`)),
    showHistory ? h("div", { class: "list tight", style: "margin-top:8px" }, past.slice().reverse().map((x) => h("div", { class: "spread" },
      h("div", null, h("span", { class: `eyebrow k-${x.kind}` }, KIND[x.kind] || x.kind), h("div", null, stripPrefix(x.text))),
      h("span", { class: "meta", style: "white-space:nowrap" }, `${mmss(x.t)} · ${x.outcome}`)))) : null) : null);
  };
  const feedback = async (n, action) => {
    const r = await act(() => api(`/nudges/${n.id}/feedback`, { body: { action } }));
    if (r) { n.outcome = action; drawNudges(); }
  };

  const goals = h("div");
  const drawGoals = () => goals.replaceChildren(...(criteria.length ? criteria.map((c) => h("div", { class: "goal" },
    c.status === "covered" ? icon("check", { class: "ok" }) : icon("circle", { class: c.status === "asked" ? "open" : "" }),
    h("div", null, c.text, c.status === "asked" ? h("span", { class: "meta" }, " · asked, not answered") : null)))
    : [h("p", { class: "empty" }, s.project_id ? "No goals for this call yet." : "Choose a project to load its goals and memory.")]));

  // private questions and catch-up (answers come from memory, this call and documents, instantly)
  const answers = h("div");
  const ask = h("input", { class: "input", placeholder: "What did we agree on price?" });
  const askNow = async () => {
    const q = ask.value.trim();
    if (!q) return;
    ask.value = "";
    answers.replaceChildren(h("p", { class: "meta" }, "Looking…"));
    const r = await act(() => api(`/sessions/${s.id}/ask`, { body: { question: q } }));
    if (!r) { answers.replaceChildren(); return; }
    answers.replaceChildren(h("div", { class: "you-msg", style: "margin-top:12px" }, h("span", { class: "meta" }, "You asked"), h("b", null, q)),
      ...(r.answers.length ? r.answers.slice(0, 3).map((a) => h("div", { class: "quote" }, a.text, h("div", { class: "meta" }, a.source)))
        : [h("p", { class: "empty" }, "Nothing on record matches that.")]));
  };
  ask.addEventListener("keydown", (e) => { if (e.key === "Enter") askNow(); });

  // close the loop (the wrap-up card)
  const loop = h("div");
  const drawLoop = (card) => {
    if (!card) {
      loop.replaceChildren(h("div", { class: "loop" }, h("div", null, h("div", { class: "eyebrow" }, "Before you finish"), h("h2", { style: "margin:8px 0 0" }, "Close the loop.")),
        h("div", null, h("p", { class: "muted", style: "margin:0 0 12px" }, "When the call starts to wrap up, Sync lists what's still open: goals not covered, questions unanswered, commitments without a date or an owner."),
          owner ? h("button", { class: "btn small", onclick: async () => { const r = await act(() => api(`/sessions/${s.id}/wrapup`, { body: {} })); if (r) drawLoop(r); } }, "Wrap up now") : null)));
      return;
    }
    const groups = [["Goals not covered", (card.open_criteria || []).map((c) => c.question || c.text)], ["Unanswered", card.unanswered], ["Asked for, not given", card.asked_for],
      ["No owner yet", card.unowned], ["No date yet", card.missing_dates], ["No time set", card.no_time], ["Conflicts", card.conflicts], ["Parked", card.parked],
      ["Say back", card.say_back]].filter(([, xs]) => xs && xs.length);
    loop.replaceChildren(h("div", { class: "loop" }, h("div", null, h("div", { class: "eyebrow" }, "Before you finish"), h("h2", { style: "margin:8px 0 0" }, "Close the loop.")),
      groups.length ? h("div", { class: "loop-items" }, groups.map(([t, xs]) => h("div", null, h("div", { class: "label" }, t), h("ul", { class: "tight-list" }, xs.slice(0, 4).map((x) => h("li", null, x))))))
        : h("p", { class: "muted", style: "margin:0" }, "Nothing left open. Good call.")));
  };

  // live bar
  const clock = h("span");
  const tick = () => { clock.textContent = started ? mmss((Date.now() - started) / 1000) : ""; };
  tick();
  const timer = setInterval(tick, 1000);
  app.onLeave(() => clearInterval(timer));
  const catchup = h("div");
  const end = async (del) => {
    if (del && !(await confirmBox("Stop and delete?", "Listening stops and this conversation is deleted: transcript, notes and nudges. Nothing is filed.", "Stop and delete"))) return;
    await act(() => api(`/sessions/${s.id}/end`, { body: { delete: del } }), del ? "Stopped and deleted" : "Stopped. Writing the recap…");
    if (del) app.go("#/conversations");
  };

  const title = h("h1", null, convTitle(s));
  const projSlot = h("span");
  const drawPicker = () => projSlot.replaceChildren(projectPicker(app, s, () => drawPicker()));
  drawPicker();

  // live updates
  const ws = new WebSocket(wsUrl(`/ws/sessions/${s.id}`));
  let closedByUs = false;
  app.onLeave(() => { closedByUs = true; ws.close(); });
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    switch (ev.type) {
      case "state": names = ev.speakers || names; criteria = ev.criteria || criteria; items = ev.items || items;
        transcript.splice(0, transcript.length, ...(ev.transcript || [])); drawTabs(); drawGoals(); break;
      case "transcript": transcript.push(ev); if (tab === "transcript") { const near = tx.scrollHeight - tx.scrollTop - tx.clientHeight < 80; tx.querySelector(".empty")?.remove(); tx.append(utt(ev)); if (near) tx.scrollTop = tx.scrollHeight; } break;
      case "speakers": names = ev.speakers; if (tab === "transcript") drawTranscript(); break;
      case "nudge": if (!nudges.some((n) => n.id === ev.id)) nudges.push({ ...ev, outcome: "shown" }); drawNudges(); break;
      case "nudge.outcome": { const n = nudges.find((x) => x.id === ev.id); if (n) { n.outcome = ev.outcome; drawNudges(); } break; }
      case "criteria": criteria = ev.criteria; drawGoals(); break;
      case "item": { const i = items.findIndex((x) => x.id === ev.item.id); if (i >= 0) items[i] = ev.item; else items.push(ev.item); drawTabs(); break; }
      case "wrapup": drawLoop(ev); break;
      case "project.linked": s.project_id = ev.project_id; app.loadProjects().then(drawPicker); break;
      case "ended": closedByUs = true; ws.close(); if (ev.deleted) app.go("#/conversations"); else setTimeout(() => app.rerender(), 600); break;
      default:
    }
  };
  ws.onclose = () => { if (!closedByUs) setTimeout(() => app.rerender(), 1500); };

  drawTabs(); drawNudges(); drawGoals(); drawLoop(null);
  return h("div", null,
    h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, owner ? "Live companion · visible only to you" : `Live · ${s.owner_name}'s call`), title,
      h("div", { class: "row" }, projSlot, h("span", { class: "meta" }, [appName(s.app), s.caller].filter(Boolean).join(" · "))))),
    h("div", { class: "livebar" },
      h("div", { class: "row" }, h("span", { class: "dot-live" }, "Listening"), h("span", { class: "muted" }, `· ${appName(s.app) || (s.mode === "script" ? "Sample call" : "In person")} ·`), clock),
      owner ? h("div", { class: "row" },
        h("button", { class: "btn small", onclick: async () => {
          catchup.replaceChildren(h("p", { class: "meta" }, "Catching up…"));
          const r = await act(() => api(`/sessions/${s.id}/catchup`, { body: {} }));
          catchup.replaceChildren(r ? h("div", { class: "card flat green", style: "margin-top:14px" }, h("div", { class: "eyebrow", style: "color:var(--green)" }, "Catch me up"), r.lines.map((l) => h("div", null, l))) : "");
        } }, icon("refresh"), "Catch me up"),
        h("button", { class: "btn small primary", onclick: () => end(false) }, icon("square"), "Stop"),
        h("button", { class: "btn small danger", onclick: () => end(true) }, "Stop and delete")) : null),
    catchup,
    h("div", { class: "cols" },
      h("div", null, active, earlier, tabs, tabBody),
      h("div", { class: "rail" },
        h("div", { class: "card" }, h("h2", null, "Exit-criteria coverage"), h("p", { class: "meta", style: "margin-top:-8px" }, "What this call needs to cover, from the project's open questions and goals."), goals),
        owner ? h("div", { class: "card" }, h("h2", null, "Ask Sync · Private"), h("div", { class: "row", style: "flex-wrap:nowrap" }, ask, h("button", { class: "btn small", onclick: askNow }, icon("send"))),
          h("p", { class: "meta" }, "Answers come from this call, the project's memory and your documents. Nobody else sees them."), answers) : null)),
    loop);
}

/* ================================================================= recap review */

async function recapView(app, s, focusT) {
  const [rc, transcript] = await Promise.all([api(`/sessions/${s.id}/recap`), api(`/sessions/${s.id}/transcript`)]);
  const recap = rc.recap || {};
  const proj = app.state.projects.find((p) => p.id === s.project_id);
  const status = rc.status;
  const items = rc.items;
  const pending = items.filter((i) => !i.confirmed);
  const done = items.filter((i) => i.confirmed);

  // keep the page fresh while the deeper model refines the recap
  if (status !== "refined" && s.ended_at && Date.now() - date(s.ended_at) < 20 * 60 * 1000) {
    app.onBus((ev) => { if (ev.type === "recap.refined" && ev.session_id === s.id) app.rerender(); });
    const poll = setInterval(async () => {
      try { const x = await api(`/sessions/${s.id}`); if (x.recap_status !== s.recap_status) app.rerender(); } catch { /* ignore */ }
    }, 12000);
    app.onLeave(() => clearInterval(poll));
  }

  const proposal = (it) => {
    const box = h("div");
    const draw = () => {
      if (it.confirmed) { box.replaceChildren(itemNode(it, { onChanged: () => {} })); return; }
      box.replaceChildren(h("div", { class: "item" },
        h("div", { class: "top" }, h("span", { class: "eyebrow" }, it.type === "commitment" ? `${it.owner === "me" ? "Mine" : it.owner ? "Theirs" : "No owner yet"} · Commitment` : TYPE_LABEL[it.type] || it.type),
          h("span", { class: "chip" }, "Awaiting review")),
        h("div", { class: "text" }, it.text),
        [it.owner_label ? `Owner: ${it.owner_label}` : "", it.due ? `Due ${it.due}` : "", it.answer ? `Answer: ${it.answer}` : ""].some(Boolean)
          ? h("div", { class: "meta", style: "margin-top:4px" }, [it.owner_label ? `Owner: ${it.owner_label}` : "", it.due ? `Due ${it.due}` : "", it.answer ? `Answer: ${it.answer}` : ""].filter(Boolean).join(" · ")) : null,
        sourceQuote(it.source, it.quote),
        h("div", { class: "row", style: "margin-top:14px" },
          h("button", { class: "btn", onclick: async () => {
            if (await act(() => api(`/sessions/${s.id}/recap/confirm`, { body: { item_ids: [it.id] } }))) { it.confirmed = true; draw(); counts(); }
          } }, it.type === "commitment" || it.type === "decision" ? "Confirm as agreed" : "Confirm"),
          h("button", { class: "btn", onclick: () => box.replaceChildren(itemNode(it, { edit: true, onCancel: draw, onChanged: counts })) }, "Edit"),
          h("button", { class: "btn", onclick: async () => {
            if (await act(() => api(`/sessions/${s.id}/recap/confirm`, { body: { item_ids: [], reject_ids: [it.id] } }), "Rejected")) { box.remove(); items.splice(items.indexOf(it), 1); counts(); }
          } }, "Reject"))));
    };
    draw();
    return box;
  };
  const countEl = h("span", { class: "meta" });
  const ended = date(s.ended_at);
  const clock = (d) => (d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "");
  const people = Object.values((s.stats || {}).speakers || {});
  const counts = () => { const n = items.filter((i) => !i.confirmed).length; countEl.textContent = n ? `${n} awaiting review` : "All reviewed"; };
  counts();

  const finish = async () => {
    if (!s.project_id && s.is_owner) { toast("Choose a project first, so the updates have somewhere to go."); return; }
    if (await act(() => api(`/sessions/${s.id}/recap/confirm`, { body: { item_ids: null } }), "Review finished. The project is up to date.")) app.go(s.project_id ? `#/projects/${s.project_id}` : "#/conversations");
  };

  const list = (title, xs) => (xs && xs.length ? h("div", { style: "margin-top:16px" }, h("div", { class: "label" }, title), h("ul", { class: "tight-list" }, xs.map((x) => h("li", null, x)))) : null);
  const stillOpen = [list("Open questions", recap.open_questions), list("Goals not covered", recap.open_criteria), list("Asked for, not given yet", recap.asked_for),
    list("No owner yet", recap.unowned), list("Conflicts", recap.conflicts)].filter(Boolean);
  const facts = [list("Flagged privately during the call", recap.flagged), list("Answers Sync gave you", recap.answers), list("Facts & numbers", recap.facts)].filter(Boolean);

  // follow-up draft
  const draft = h("textarea", { class: "input", style: "min-height:200px", placeholder: "Sync writes a short follow-up from the recap. Edit it before you send it yourself." });
  const followBtn = h("button", { class: "btn", onclick: async () => {
    followBtn.disabled = true; followBtn.textContent = "Writing…";
    const r = await act(() => api(`/sessions/${s.id}/followup`, { body: {} }));
    followBtn.disabled = false; followBtn.textContent = "Write again";
    if (r) draft.value = r.text;
  } }, "Write follow-up");

  let near = null;
  if (focusT != null) near = transcript.reduce((best, u) => (best === null || Math.abs(u.t - focusT) < Math.abs(best.t - focusT) ? u : best), null);
  const txBox = h("div", { class: "transcript", style: "max-height:420px" }, transcript.map((u) => h("div", { class: `utt ${u === near ? "hl" : ""}` }, h("div", { class: "time" }, mmss(u.t)),
    h("div", null, h("b", null, u.channel === "you" ? (s.is_owner ? "You" : s.owner_name) : u.speaker || "Them")), h("div", { class: "say" }, u.text))));
  const txCard = h("div", { class: "card", style: "margin-top:24px" }, h("h2", null, "Transcript"), txBox);
  const nudges = s.nudges || [];

  const banner = status === "refined"
    ? h("div", { class: "banner" }, h("b", null, icon("checks"), "Recap refined"), h("span", null, `Instant draft saved at ${clock(ended)} → refined with the deeper model and checked against the project. Items below still need your review.`))
    : status === "refining" || (Date.now() - date(s.ended_at || s.started_at) < 20 * 60 * 1000 && status !== "none")
      ? h("div", { class: "banner amber" }, h("b", null, icon("refresh"), "Refining the recap"), h("span", null, "You're seeing the draft from the live call. This page updates by itself when the deeper pass is done (a few minutes on this laptop)."))
      : h("div", { class: "banner amber" }, h("b", null, "Draft recap"), h("span", null, "Built from what Sync caught live. The deeper pass didn't run (is the local engine running?)."));

  return h("div", null,
    h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, s.mode === "paste" ? "Pasted conversation" : "Call ended · not listening"),
      h("h1", null, `Review the ${convTitle(s)} recap.`),
      h("p", { class: "lede" }, [s.caller || (s.title && s.mode !== "script" ? "" : s.title), `${when(s.started_at)}${ended ? "–" + clock(ended) : ""}`, appName(s.app),
        [s.is_owner ? app.state.me.name : s.owner_name, ...people].join(", ")].filter(Boolean).join(" · ")),
      h("div", { class: "row", style: "margin-top:14px" }, projectPicker(app, s, () => app.rerender()))),
    h("div", { class: "head-actions" }, h("button", { class: "btn primary", onclick: finish }, icon("checks"), "Finish review"))),
    banner,
    !s.project_id && s.is_owner ? h("div", { class: "card amber", style: "margin-top:20px" }, h("b", null, "Which project is this about?"),
      h("p", { class: "muted", style: "margin:4px 0 0" }, "Choose one from “Project” above. Sync then checks what was said against what the project already knows. Until then it's only in your conversations.")) : null,
    h("div", { class: "cols" },
      h("div", null,
        recap.summary ? h("div", null, h("div", { class: "eyebrow" }, "Summary"), h("p", { class: "summary" }, recap.summary)) : null,
        (recap.decisions || []).length || (recap.next_steps || []).length ? h("div", { class: "two", style: "margin-top:8px" },
          list("Decided", recap.decisions), list("Next steps", recap.next_steps)) : null,
        h("div", { class: "section-label" }, h("div", { class: "eyebrow" }, "Proposed project updates"), h("span", null, countEl, h("span", { class: "meta" }, " · nothing confirmed automatically"))),
        items.length ? [pending.map(proposal), done.length ? h("details", { class: "confirmed-block" }, h("summary", null, `Already confirmed · ${done.length}`), done.map(proposal)) : null]
          : h("p", { class: "empty" }, "Nothing was captured from this conversation."),
        stillOpen.length ? h("div", { class: "card", style: "margin-top:28px" }, h("h2", null, "Still open"), stillOpen) : null,
        facts.length ? h("div", { class: "card" }, h("h2", null, "Facts & feasibility"), facts) : null),
      h("div", { class: "rail" },
        h("div", { class: "panel" }, h("div", { class: "spread" }, h("h2", { style: "margin:0" }, "Follow-up draft"), h("span", { class: "meta" }, "Editable")),
          h("div", { style: "margin-top:14px" }, draft),
          h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "btn primary", onclick: async () => {
            if (!draft.value.trim()) { toast("Write the follow-up first."); return; }
            await navigator.clipboard?.writeText(draft.value).catch(() => {}); toast("Copied");
          } }, icon("copy"), "Copy draft"), followBtn),
          h("p", { class: "meta", style: "margin:12px 0 0" }, "You review and send it yourself. Sync never sends automatically.")),
        h("div", { class: "card" }, h("h2", null, "Source conversation"),
          h("p", { style: "margin:-4px 0 14px" }, `${minutes(s) ? minutes(s) + " transcript" : `${transcript.length}-line transcript`} kept on this computer. Audio was never stored.`),
          h("button", { class: "btn", onclick: () => { txCard.hidden = !txCard.hidden; if (!txCard.hidden) txCard.scrollIntoView({ behavior: "smooth" }); } }, icon("file"), h("span", null, "Open transcript")),
          h("p", { class: "meta", style: "margin:12px 0 0" }, "Each quote's link opens the transcript at the moment it was said.")),
        nudges.length ? h("div", { class: "card" }, h("h2", null, "Nudges during the call"), h("div", { class: "list tight" }, nudges.map((n) => h("div", null,
          h("div", { class: "spread" }, h("span", { class: `eyebrow k-${n.kind}` }, KIND[n.kind] || n.kind), h("span", { class: "meta" }, `${mmss(n.t)} · ${n.outcome}`)),
          h("div", { class: "small" }, stripPrefix(n.text)))))) : null,
        s.is_owner ? h("button", { class: "textbtn red", style: "align-self:flex-start", onclick: async () => {
          if (!(await confirmBox("Delete this conversation?", "The transcript and recap are deleted. Items already confirmed into the project stay there.", "Delete"))) return;
          if (await act(() => api(`/sessions/${s.id}/end`, { body: { delete: true } }), "Deleted")) app.go("#/conversations");
        } }, "Delete conversation") : null)),
    (() => {
      txCard.hidden = !near;
      if (near) {
        setTimeout(() => {
          const el = txBox.querySelector(".hl");
          if (!el) return;
          txBox.scrollTop = el.offsetTop - txBox.clientHeight / 2;
          txCard.scrollIntoView({ block: "start" });
        }, 150);
      }
      return txCard;
    })());
}

export async function render(app, sid, t) {
  const s = await api(`/sessions/${sid}`);
  if (!app.state.projects.length) await app.loadProjects();
  const node = s.status === "live" && s.live ? liveView(app, s)
    : s.status === "detected" ? h("div", { class: "card" }, h("h2", null, "Sync is about to join this call"), h("p", { class: "muted" }, "The countdown is running on your screen. Skip it with Ctrl+Alt+S."))
      : await recapView(app, s, t == null ? null : Number(t));
  if (s.status === "detected") app.onBus((ev) => { if (ev.session_id === s.id) app.rerender(); });
  return { crumbs: [["Conversations", "#/conversations"], convTitle(s)], node };
}
