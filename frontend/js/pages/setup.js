/* First run (create your profile in three steps), joining a shared project, and signing in on this computer. */
import { act, api, auth, h, icon, segmented, setToken } from "../ui.js";

const MODES = [["quiet", "Quiet", "Only critical gaps"], ["normal", "Normal", "Timely, relevant prompts"], ["active", "Active", "More frequent prompts"]];

function stepper(n) {
  const names = ["Your basics", "Nudges", "Listening & privacy"];
  return h("div", { class: "steps" }, names.map((name, i) => i + 1 < n
    ? h("span", { class: "done" }, "✓ ", name)
    : h("span", { class: i + 1 === n ? "cur" : "" }, `0${i + 1}  ${name}`)), h("span", null, `Step ${n} of 3`));
}

export function firstRun(done) {
  const data = { name: "", languages: "English", mode: "normal", listen: "auto", countdown: 5 };
  const docs = [];
  const root = h("div");
  const privacy = h("div", { class: "panel" }, h("h2", null, "Private by default"),
    h("p", { class: "muted small" }, "Everything runs on this computer: speech recognition, the AI models and your project memory. Audio is never stored. Only you are notified when Sync is listening; other participants are not. You are responsible for consent under local law."));

  const draw = (step) => {
    let body;
    if (step === 1) {
      const name = h("input", { class: "input", value: data.name, placeholder: "Your first name" });
      const langs = h("input", { class: "input", value: data.languages });
      body = [h("div", { class: "eyebrow", style: "margin-top:28px" }, "Make Sync yours"), h("h1", null, "Welcome to Sync."),
        h("p", { class: "lede" }, "Sync listens to your calls with you, nudges you when something is missed, and remembers every project."),
        h("div", { class: "card", style: "margin-top:28px;max-width:560px" },
          h("div", { class: "field", style: "margin-top:0" }, h("label", null, "Name"), name),
          h("div", { class: "field" }, h("label", null, "Conversation languages"), langs, h("div", { class: "hint" }, "Speech recognition is tuned for English."))),
        h("div", { class: "row", style: "margin-top:28px" }, h("button", { class: "btn primary", onclick: () => {
          if (!name.value.trim()) { name.focus(); return; }
          data.name = name.value.trim(); data.languages = langs.value; draw(2);
        } }, "Continue to nudges"))];
      setTimeout(() => name.focus(), 0);
    } else if (step === 2) {
      const choices = h("div", { class: "choices" });
      const drawChoices = () => choices.replaceChildren(...MODES.map(([k, l, d]) => h("button", { class: `choice ${data.mode === k ? "on" : ""}`, onclick: () => { data.mode = k; drawChoices(); } },
        h("b", null, l), h("span", null, d))));
      drawChoices();
      const file = h("input", { type: "file", accept: ".txt,.md,.pdf", multiple: true, onchange: () => { docs.splice(0, docs.length, ...file.files); list.replaceChildren(...docs.map((f) => h("div", { class: "small", style: "color:var(--green)" }, `${f.name} · ready ✓`))); } });
      const list = h("div");
      body = [h("div", { class: "eyebrow", style: "margin-top:28px" }, "Make Sync yours"), h("h1", null, "How often should Sync nudge?"),
        h("p", { class: "lede" }, "You can change this any time in Settings."),
        h("div", { class: "cols" }, h("div", null, h("div", { class: "eyebrow", style: "margin-bottom:12px" }, "Nudges"), choices,
          h("div", { class: "row", style: "margin-top:32px;justify-content:space-between" },
            h("button", { class: "btn", onclick: () => draw(1) }, "Back"),
            h("button", { class: "btn primary", onclick: () => draw(3) }, "Continue to listening & privacy"))),
        h("div", { class: "rail" }, h("div", { class: "card" }, h("h2", null, "Optional documents"),
          h("p", { class: "muted small" }, "Price lists, policies, guides. Sync answers questions from them and warns before you over-promise. Files stay on this computer."),
          h("label", { class: "btn", style: "margin-top:6px" }, icon("clip"), "Add documents", h("span", { hidden: true }, file)), list)))];
    } else {
      const countdown = h("input", { type: "range", min: 0, max: 10, value: data.countdown, oninput: () => { data.countdown = Number(countdown.value); val.textContent = `${data.countdown} seconds`; } });
      const val = h("span", { class: "chip line" }, `${data.countdown} seconds`);
      body = [h("div", { class: "eyebrow", style: "margin-top:28px" }, "You're in control"), h("h1", null, "When should Sync listen?"),
        h("p", { class: "lede" }, "Sync only watches which app is using your microphone — never the audio — until it joins a call."),
        h("div", { class: "cols" }, h("div", null, h("div", { class: "card" },
          h("h2", null, "Auto-listen"),
          segmented([["auto", "Auto"], ["ask", "Ask every time"], ["off", "Off"]], data.listen, (v) => { data.listen = v; }),
          h("p", { class: "muted small", style: "margin-top:12px" }, "Auto joins calls after a short countdown you can skip. Ask waits for your approval. Off never joins on its own."),
          h("hr", { class: "divider" }), h("div", { class: "spread" }, h("b", null, "Skip countdown"), val), countdown,
          h("p", { class: "muted small" }, "During the countdown nothing is recorded. Skip it with Ctrl+Alt+S and nothing is kept.")),
          h("div", { class: "row", style: "margin-top:28px;justify-content:space-between" },
            h("button", { class: "btn", onclick: () => draw(2) }, "Back"),
            h("button", { class: "btn primary", onclick: finish }, "Start using Sync"))),
        h("div", { class: "rail" }, privacy))];
    }
    root.replaceChildren(stepper(step), ...body);
  };

  async function finish(e) {
    e.target.disabled = true;
    const r = await act(() => api("/users", { body: { name: data.name } }));
    if (!r) { e.target.disabled = false; return; }
    setToken(r.token);
    const langs = data.languages.split(",").map((x) => x.trim()).filter(Boolean);
    await act(() => api("/me", { method: "PUT", body: { nudge_mode: data.mode, languages: langs.length ? langs : ["English"] } }));
    await act(() => api("/me/autolisten", { method: "PUT", body: { mode: data.listen, countdown: data.countdown } }));
    for (const f of docs) {
      const fd = new FormData(); fd.append("file", f);
      await act(() => api("/documents", { body: fd }));
    }
    done(r.token);
  }
  draw(1);
  return root;
}

export async function join(code, done) {
  let info;
  try { info = await api(`/join/${code}`); } catch (e) {
    return h("div", { class: "center-page" }, h("h1", null, "Invite link"), h("p", { class: "lede" }, e.message), h("a", { href: "#/", class: "btn", style: "margin-top:20px" }, "Go to Sync"));
  }
  const name = h("input", { class: "input", placeholder: "Your first name" });
  return h("div", { class: "center-page" },
    h("div", { class: "eyebrow" }, "Shared project"), h("h1", null, `Join ${info.project}.`),
    h("p", { class: "lede" }, `Shared by ${info.members.join(", ")}. Everything in this project — calls, notes, numbers and history — is shared with you, and your changes are shown with your name.`),
    auth.token ? null : h("div", { class: "field" }, h("label", null, "Your name"), name),
    h("button", { class: "btn primary", style: "margin-top:22px", onclick: async (e) => {
      if (!auth.token && !name.value.trim()) { name.focus(); return; }
      e.target.disabled = true;
      const r = await act(() => api("/join", { body: { code, name: name.value.trim() } }));
      e.target.disabled = false;
      if (!r) return;
      location.hash = `#/projects/${r.project_id}`;
      done(r.token || auth.token);
    } }, "Join project"));
}

export function signIn(onThisComputer) {
  const link = h("input", { class: "input", placeholder: "Paste the invite link a teammate sent you" });
  return h("div", { class: "center-page" },
    h("div", { class: "eyebrow" }, "Welcome back"), h("h1", null, "Sign in to Sync."),
    h("p", { class: "lede" }, "Sync is already set up. If this is your computer, sign in as its owner. To join a teammate's project, open their invite link."),
    h("div", { class: "card", style: "margin-top:26px" },
      h("button", { class: "btn primary", onclick: onThisComputer }, "I set up Sync on this computer"),
      h("hr", { class: "divider" }),
      h("label", { class: "label" }, "Invite link"), link,
      h("button", { class: "btn", style: "margin-top:12px", onclick: () => {
        const m = link.value.match(/join\/([\w-]+)/);
        if (m) { location.hash = `#/join/${m[1]}`; location.reload(); }
      } }, "Continue")));
}
