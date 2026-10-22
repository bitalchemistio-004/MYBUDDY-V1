// Routines: steps the user saved, run with one click or one word. Every step is
// a real thing MBUDDY can already do (core/routines.py); nothing runs by itself.
import { api } from "../api.js";
import { esc, header } from "./ui.js";

const when = (iso) => (iso ? new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "");

export const routines = {
  timer: null,
  destroy() { clearInterval(this.timer); this.timer = null; },

  async render(el, ctx) {
    const self = this;
    el.classList.add("sec");
    el.innerHTML = `<div class="top">${header("Routines", "Steps you do the same way, saved once and run with one click or one word. Nothing runs unless you start it.")}
      <button class="btn accent" id="rt-new" type="button">New routine</button></div>
      <div id="rt-form"></div><div id="rt-list" class="stack"></div>`;
    const $ = (s) => el.querySelector(s);
    let data = null, editing = null;   // editing: {id|null, name, steps: []}

    const stepText = (s) => `${s.label || data.step_types[s.type].label}${s.name || s.query || s.url ? `: ${s.name || s.query || s.url}` : ""}${s.minutes ? `: ${s.minutes} min` : ""}`;

    const drawForm = () => {
      if (!editing) { $("#rt-form").innerHTML = ""; return; }
      const types = data.step_types;
      $("#rt-form").innerHTML = `<form class="surface card stack" id="rt-f"><h3>${editing.id ? "Edit routine" : "New routine"}</h3>
        <label class="stack" style="gap:4px"><span class="label">Name</span><input class="field" id="rt-name" maxlength="60" value="${esc(editing.name)}" placeholder="Start my work day" required></label>
        <div class="label">Steps, in order</div>
        ${editing.steps.length ? editing.steps.map((s, i) => `<div class="item"><span class="chip">${i + 1}</span><div class="grow"><div class="t">${esc(stepText(s))}</div></div>
          <button class="btn small" type="button" data-up="${i}" ${i ? "" : "disabled"} aria-label="Move up">↑</button>
          <button class="btn small" type="button" data-down="${i}" ${i < editing.steps.length - 1 ? "" : "disabled"} aria-label="Move down">↓</button>
          <button class="btn small danger" type="button" data-rm="${i}" aria-label="Remove step ${i + 1}">Remove</button></div>`).join("") : `<p class="note">No steps yet. Add the first one below.</p>`}
        <div class="frm3"><label><span class="label">Add a step</span><select class="field" id="rt-type">${Object.entries(types).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("")}</select></label>
          <label style="grid-column: span 2"><span class="label" id="rt-fl">Program name</span><input class="field" id="rt-val" maxlength="200"></label>
          <button class="btn" type="button" id="rt-addstep">Add step</button></div>
        <p class="note" id="rt-err" role="alert"></p>
        <div class="btnrow"><button class="btn accent" type="submit">Save routine</button><button class="btn" type="button" id="rt-cancel">Cancel</button></div></form>`;
      const label = () => {
        const t = $("#rt-type").value, f = { open_app: ["Program name", "Notepad"], play_music: ["What to play", "calm piano"], open_url: ["Web address", "https://"], focus: ["Minutes (5 to 180)", "25"] }[t];
        $("#rt-fl").textContent = f[0]; $("#rt-val").placeholder = f[1]; $("#rt-val").type = t === "focus" ? "number" : "text";
      };
      label(); $("#rt-type").addEventListener("change", label);
    };

    const drawList = () => {
      const list = data.routines;
      $("#rt-list").innerHTML = list.length ? list.map((r) => `
        <section class="surface card stack"><h3>${esc(r.name)} <span class="chip">${r.steps.length} step${r.steps.length === 1 ? "" : "s"}</span></h3>
          <div class="btnrow" style="gap:6px">${r.steps.map((s, i) => `<span class="chip">${i + 1}. ${esc(stepText(s))}</span>`).join("")}</div>
          ${r.last_run ? `<p class="think">Last run ${esc(when(r.last_run.t))}: ${r.last_run.ok ? `all ${r.last_run.results.length} steps worked.` : `stopped at step ${r.last_run.results[r.last_run.results.length - 1].step}: ${esc(r.last_run.results[r.last_run.results.length - 1].text)}`}</p>`
            : `<p class="note">Never run. You can also say “run my ${esc(r.name.toLowerCase())} routine”.</p>`}
          <div class="btnrow"><button class="btn accent" type="button" data-run="${esc(r.id)}" ${r.running ? "disabled" : ""}>${r.running ? "Running…" : "Run"}</button>
            <button class="btn" type="button" data-edit="${esc(r.id)}">Edit</button>
            <button class="btn danger" type="button" data-del="${esc(r.id)}" data-name="${esc(r.name)}">Delete</button></div></section>`).join("")
        : `<section class="surface card"><div class="empty">No routines yet. Make one: for example open your notes, start your music, and begin a focus sprint. The steps are real things I can do.</div></section>`;
    };

    const load = async () => {
      const r = await api.get("/ui/routines");
      if (!r.ok) { $("#rt-list").innerHTML = `<section class="surface card"><div class="empty">Couldn't load routines. ${esc(r.error || "")}</div></section>`; return; }
      data = r; drawForm(); drawList();
    };

    el.addEventListener("click", async (e) => {
      const t = e.target, q = (s) => t.closest(s);
      if (q("#rt-new")) { editing = { id: null, name: "", steps: [] }; drawForm(); $("#rt-name")?.focus(); return; }
      if (q("#rt-cancel")) { editing = null; drawForm(); return; }
      if (q("#rt-addstep") && editing) {
        const type = $("#rt-type").value, raw = $("#rt-val").value.trim(), field = data.step_types[type].field;
        const step = { type, [field]: type === "focus" ? Number(raw) : raw };
        editing.name = $("#rt-name").value;
        const check = await api.post("/ui/routines", { name: "…check", steps: [step], id: "__check__" });   // validates the step on the server
        if (check.error && !/No such routine/.test(check.error)) { $("#rt-err").textContent = check.error; return; }
        editing.steps.push(step); drawForm(); $("#rt-val")?.focus(); return;
      }
      const mv = q("[data-up]") || q("[data-down]");
      if (mv && editing) {
        const i = Number(mv.dataset.up ?? mv.dataset.down), j = mv.dataset.up !== undefined ? i - 1 : i + 1;
        editing.name = $("#rt-name").value;
        [editing.steps[i], editing.steps[j]] = [editing.steps[j], editing.steps[i]]; drawForm(); return;
      }
      const rm = q("[data-rm]");
      if (rm && editing) { editing.name = $("#rt-name").value; editing.steps.splice(Number(rm.dataset.rm), 1); drawForm(); return; }
      const run = q("[data-run]");
      if (run) {
        const r = await api.post("/ui/routines/run", { id: run.dataset.run });
        ctx.toast(r.ok ? "Started. Watch the buddy." : (r.error || "Couldn't start that."));
        await load(); return;
      }
      const ed = q("[data-edit]");
      if (ed) { const r = data.routines.find((x) => x.id === ed.dataset.edit); editing = { id: r.id, name: r.name, steps: r.steps.map((s) => ({ ...s })) }; drawForm(); window.scrollTo && $("#rt-form").scrollIntoView(); return; }
      const del = q("[data-del]");
      if (del) {
        if (del.dataset.confirm !== "1") { del.dataset.confirm = "1"; del.textContent = "Sure?"; return; }
        const r = await api.del("/ui/routines", { id: del.dataset.del });
        if (r.ok) { ctx.toast(`Deleted “${del.dataset.name}”.`); await load(); }
      }
    });
    el.addEventListener("submit", async (e) => {
      if (e.target.id !== "rt-f") return;
      e.preventDefault();
      const name = $("#rt-name").value.trim();
      const r = await api.post("/ui/routines", { id: editing.id || undefined, name, steps: editing.steps });
      if (!r.ok) { $("#rt-err").textContent = r.error || "Couldn't save that."; return; }
      ctx.toast("Saved."); editing = null; data = r; drawForm(); drawList();
    });
    await load();
    self.timer = setInterval(() => { if (document.visibilityState === "visible" && !editing) load(); }, 4000);   // shows a running routine finishing
  },
};
