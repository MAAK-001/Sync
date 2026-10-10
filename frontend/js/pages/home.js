/* Workspace overview: your projects, quick start, open commitments and recent conversations. */
import { api, appName, cap, convTitle, h, icon, minutes, statusChip, when } from "../ui.js";
import { newProjectModal } from "./projects.js";
import { briefModal } from "./project.js";
import { playSample, convStatus } from "./conversations.js";

function greeting() {
  const hr = new Date().getHours();
  return hr < 5 ? "Good evening" : hr < 12 ? "Good morning" : hr < 17 ? "Good afternoon" : "Good evening";
}

function bigCard(p, last) {
  return h("div", { class: "card project-card" },
    h("div", { class: "spread", style: "align-items:flex-start" }, h("div", null, h("h2", { class: "pc-name" }, p.name),
      h("p", { class: "meta", style: "margin:0;max-width:420px" }, p.objective || "No objective yet. Add one so Sync knows what a good outcome is.")),
      h("a", { href: `#/projects/${p.id}`, class: "pc-open", title: "Open project" }, icon("arrowUR"))),
    p.brief ? h("p", { class: "pc-brief" }, p.brief) : null,
    h("div", { class: "tagline" }, p.members.length > 1 ? h("span", { class: "chip green" }, `Shared with ${p.members.length - 1}`) : null,
      h("span", { class: "chip" }, `${p.items} item${p.items === 1 ? "" : "s"}`), p.status === "closed" ? h("span", { class: "chip" }, "Closed") : null),
    h("hr", { class: "divider" }),
    h("div", { class: "spread" }, h("span", { class: "meta" }, last ? `Last conversation · ${when(last.started_at, false)}${minutes(last) ? " · " + minutes(last) : ""}` : `${p.sessions ? `${p.sessions} conversations` : "No conversations yet"} · updated ${when(p.updated_at, false)}`),
      h("a", { class: "btn", href: `#/projects/${p.id}` }, "Open project")));
}

function compactRow(p) {
  return h("a", { class: "rowcard", href: `#/projects/${p.id}` },
    h("div", null, h("b", null, p.name), h("div", { class: "meta" }, [p.objective || "No objective yet", `${p.sessions} conversation${p.sessions === 1 ? "" : "s"}`].join(" · "))),
    h("span", { class: "chip line" }, p.members.length > 1 ? "Shared" : `${p.items} items`));
}

function commitmentRow(c) {
  return h("div", { class: "c-row" },
    h("div", { class: "spread", style: "align-items:flex-start" }, h("b", null, c.text), statusChip(c.status)),
    h("div", { class: "meta", style: "margin-top:6px" }, [c.owner_label && c.whose !== "mine" ? cap(c.owner_label) : "", c.project || "Not filed", c.due ? `Due ${c.due}` : "No date yet"].filter(Boolean).join(" · ")));
}

function quickItem(ic, title, sub, onclick) {
  return h("button", { class: "qs-item", onclick }, icon(ic), h("span", null, h("b", null, title), h("span", null, sub)));
}

export async function render(app) {
  const [projects, sessions, commitments] = await Promise.all([app.loadProjects(), api("/sessions?limit=8"), api("/commitments")]);
  const me = app.state.me;
  const pinned = app.pinned();
  const open = projects.filter((p) => p.status !== "closed");
  const featured = pinned || open[0];
  const rest = open.filter((p) => p !== featured).slice(0, 4);
  const lastFor = (p) => sessions.find((s) => s.project_id === p.id && s.status === "ended");
  const live = sessions.filter((s) => s.status === "live" && s.user_id === me.id);
  const openC = commitments.filter((c) => c.status !== "done");
  const mine = openC.filter((c) => c.whose !== "theirs").slice(0, 3);
  const theirs = openC.filter((c) => c.whose === "theirs").slice(0, 3);

  const liveMine = live[0];
  const quick = h("div", { class: "panel quick" }, h("h2", null, "Quick start"),
    h("p", { class: "muted", style: "margin:-4px 0 10px" }, "Sync remembers by project. Start with a conversation, a brief, or a question."),
    quickItem("chat", "Ask about a project", "Answers with where they came from", () => app.go("#/ask")),
    featured ? quickItem("file", `Brief me on ${featured.name}`, "Get context before your next call", () => briefModal(featured.id)) : null,
    liveMine ? quickItem("headphones", "Open live companion", "Sync is listening now", () => app.go(`#/conversations/${liveMine.id}`))
      : quickItem("play", "Play a sample call", "See the live companion work", () => playSample(app)),
    h("p", { class: "meta", style: "margin:12px 0 0" }, "Phone Link calls are heard through this laptop's microphone. ", h("a", { href: "#/settings" }, "Listening settings")));

  return {
    crumbs: ["Overview"],
    node: h("div", null,
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, "Your workspace"), h("h1", null, `${greeting()}, ${me.name}.`),
        h("p", { class: "lede" }, openC.length ? "Pick up the thread. Here's what needs your attention today." : "Pick up the thread, or start something new.")),
      h("div", { class: "head-actions" }, h("button", { class: "btn primary", onclick: () => newProjectModal(app) }, icon("plus"), "New project"))),
      live.map((s) => h("a", { class: "banner live-banner", href: `#/conversations/${s.id}` }, h("b", { class: "dot-live" }, "Listening"),
        h("span", null, `${convTitle(s)} · ${appName(s.app) || "In person"}`), h("span", { style: "margin-left:auto" }, "Open live companion ↗"))),
      h("div", { class: "cols" },
        h("div", null,
          h("div", { class: "spread", style: "margin-bottom:12px" }, h("div", { class: "eyebrow" }, "Projects"),
            h("a", { class: "link-arrow", href: "#/projects" }, "View all projects", icon("arrowUR"))),
          featured ? [bigCard(featured, lastFor(featured)), rest.map(compactRow)]
            : h("div", { class: "card" }, h("h2", null, "Start your first project"),
              h("p", { class: "muted" }, "A project is where Sync keeps what was said, agreed and promised. You can also create one when Sync asks which project a call belongs to."),
              h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => newProjectModal(app) }, icon("plus"), "New project"),
                h("button", { class: "btn", onclick: () => playSample(app) }, icon("play"), "Play a sample call")))),
        h("div", { class: "rail" }, quick)),

      h("div", { class: "spread", style: "margin:44px 0 18px" }, h("h2", { style: "margin:0;font-size:22px" }, "Keep the loop closed"),
        h("a", { class: "link-arrow", href: "#/commitments" }, "All commitments", icon("arrowUR"))),
      h("div", { class: "two" },
        h("div", { class: "card" }, h("div", { class: "spread" }, h("h2", { style: "margin:0" }, "Mine"), h("span", { class: "meta" }, me.name)),
          mine.length ? mine.map(commitmentRow) : h("p", { class: "empty", style: "margin-top:12px" }, "Nothing open.")),
        h("div", { class: "card" }, h("div", { class: "spread" }, h("h2", { style: "margin:0" }, "Theirs"), h("span", { class: "meta" }, "Others")),
          theirs.length ? theirs.map(commitmentRow) : h("p", { class: "empty", style: "margin-top:12px" }, "Nothing open."))),

      h("div", { class: "section-label", style: "margin-top:40px" }, h("div", { class: "eyebrow" }, "Recent conversations"),
        h("a", { class: "link-arrow", href: "#/conversations" }, "View all", icon("arrowUR"))),
      sessions.length ? h("div", null, sessions.slice(0, 5).map((s) => h("a", { class: "rowlink", href: `#/conversations/${s.id}` },
        h("div", null, h("b", null, convTitle(s)), h("div", { class: "meta" }, [when(s.started_at), appName(s.app) || (s.mode === "paste" ? "Pasted" : "In person"), minutes(s),
          (projects.find((p) => p.id === s.project_id) || {}).name || "Not filed"].filter(Boolean).join(" · "))),
        h("div", { class: "row" }, convStatus(s), icon("arrowR")))))
        : h("p", { class: "empty" }, "No conversations yet. Sync joins your calls automatically, or play a sample call to see it work.")),
  };
}
