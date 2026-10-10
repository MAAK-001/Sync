/* All projects, plus the "New project" dialog used across the app. */
import { act, api, h, icon, modal, when } from "../ui.js";

export function newProjectModal(app, after) {
  const name = h("input", { class: "input", placeholder: "e.g. Acme renewal" });
  const objective = h("input", { class: "input", placeholder: "What a good outcome looks like" });
  const context = h("textarea", { class: "input", placeholder: "Background Sync should know: who, what, numbers, deadlines…" });
  modal("New project", h("div", null,
    h("div", { class: "field", style: "margin-top:0" }, h("label", null, "Name"), name),
    h("div", { class: "field" }, h("label", null, "Objective"), objective),
    h("div", { class: "field" }, h("label", null, "Context"), context,
      h("div", { class: "hint" }, "Optional. Calls you file under this project will add to it automatically."))), [
    { label: "Cancel", run: () => {} },
    { label: "Create project", kind: "primary", run: async () => {
      if (!name.value.trim()) { name.focus(); return true; }
      const p = await act(() => api("/projects", { body: { name: name.value, objective: objective.value, context: context.value } }), "Project created");
      if (!p) return true;
      await app.loadProjects();
      if (after) after(p); else app.go(`#/projects/${p.id}`);
      return false;
    } }]);
}

export function projectRow(p) {
  return h("a", { class: "rowlink", href: `#/projects/${p.id}` },
    h("div", null, h("b", null, p.name),
      h("div", { class: "meta" }, [p.objective || "No objective yet", `${p.items} items`, `${p.sessions} conversation${p.sessions === 1 ? "" : "s"}`,
        p.members.length > 1 ? `Shared with ${p.members.length - 1}` : "Personal"].join(" · "))),
    h("div", { class: "row" }, p.status === "closed" ? h("span", { class: "chip" }, "Closed") : null,
      h("span", { class: "meta" }, when(p.updated_at, false)), icon("arrowR")));
}

export async function render(app) {
  const list = await app.loadProjects();
  const open = list.filter((p) => p.status !== "closed");
  const closed = list.filter((p) => p.status === "closed");
  return {
    crumbs: ["Projects"],
    node: h("div", null,
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, "Projects"), h("h1", null, "Your projects."),
        h("p", { class: "lede" }, "Everything Sync knows about each piece of work: facts, numbers, commitments, people and every conversation.")),
      h("div", { class: "head-actions" }, h("button", { class: "btn primary", onclick: () => newProjectModal(app) }, icon("plus"), "New project"))),
      h("div", { style: "margin-top:30px" }, open.length ? open.map(projectRow)
        : h("div", { class: "panel" }, h("h2", null, "No projects yet"), h("p", { class: "muted" }, "Create one, or file your next call under a new project when Sync asks."))),
      closed.length ? [h("div", { class: "section-label" }, h("div", { class: "eyebrow" }, "Closed")), closed.map(projectRow)] : null),
  };
}
