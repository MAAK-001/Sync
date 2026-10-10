/* Every promise across your projects and conversations: yours, your team's and theirs. */
import { api, h, segmented } from "../ui.js";
import { itemNode } from "./project.js";

export async function render(app) {
  const all = await api("/commitments");
  let show = "open";
  const cols = h("div");
  const draw = () => {
    const rows = all.filter((c) => (show === "all" ? true : show === "done" ? c.status === "done" : c.status !== "done"));
    const group = (title, hint, xs) => h("div", null, h("div", { class: "section-label", style: "margin-top:24px" }, h("div", null, h("h2", { style: "margin:0" }, title), h("div", { class: "meta" }, hint)),
      h("span", { class: "chip line" }, String(xs.length))),
    xs.length ? xs.map((c) => itemNode(c, { showProject: true, onChanged: () => { if (show !== "all") setTimeout(draw, 400); } })) : h("p", { class: "empty" }, "Nothing here."));
    cols.replaceChildren(h("div", { class: "two" },
      h("div", null, group("Mine", "What you promised", rows.filter((c) => c.whose === "mine")),
        rows.some((c) => c.whose === "team") ? group("My team's", "Promised by teammates in shared projects", rows.filter((c) => c.whose === "team")) : null),
      group("Theirs", "What others promised you", rows.filter((c) => c.whose === "theirs"))));
  };
  draw();
  const open = all.filter((c) => c.status !== "done");
  return {
    crumbs: ["Commitments"],
    node: h("div", null,
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, "Keep the loop closed"), h("h1", null, "Commitments."),
        h("p", { class: "lede" }, `${open.length} open · ${open.filter((c) => !c.due).length} without a date. Every one links back to the moment it was said.`))),
      h("div", { style: "margin-top:26px" }, segmented([["open", "Open"], ["done", "Done"], ["all", "All"]], show, (v) => { show = v; draw(); })),
      cols),
  };
}
