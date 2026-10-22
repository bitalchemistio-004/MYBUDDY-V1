// What I learned: the brain, honestly. Every item is a real note in the user's
// vault with the evidence the brain itself keeps (api/learned_api.py).
import { api } from "../api.js";
import { esc, header } from "./ui.js";

const GROUPS = [["all", "Everything"], ["lessons", "Lessons"], ["skills", "Skills"], ["about you", "About you"], ["facts", "Facts"], ["apps", "Apps"]];
const MODE_LABEL = { off: "Brain off", memory: "Memory", lessons: "+ Lessons", skills: "+ Skills", full: "Full loop" };

export const learned = {
  async render(el, ctx) {
    el.classList.add("sec");
    el.innerHTML = `<div class="top">${header("What I learned", "Only things I checked myself, or that you told me. You can make me stop using any of them.")}</div><div id="ln-body" class="stack"></div>`;
    const body = el.querySelector("#ln-body");
    let data = null, group = "all";

    const evidence = (it) => it.checked
      ? `<div class="ev" title="${it.successes} of ${it.checked} checks passed"><i style="width:${Math.round(100 * it.successes / it.checked)}%"></i></div>`
      : `<div class="note">Not checked yet.</div>`;

    const drawScore = (s) => {
      if (!s) return `<div class="empty">No benchmark has been run yet. Run <code>tools\\bench_brain.py</code> to measure whether my notes really help.</div>`;
      const top = Math.max(...s.modes.map((m) => m.passed / Math.max(1, m.of)));
      return `<div class="bars">${s.modes.map((m) => `<div class="br ${m.passed / Math.max(1, m.of) === top && top > 0 ? "hi" : ""}"><span>${esc(MODE_LABEL[m.mode] || m.mode)}</span>
          <div class="track"><i style="width:${Math.round(100 * m.passed / Math.max(1, m.of))}%"></i></div><em>${m.passed}/${m.of}</em></div>`).join("")}</div>
        <p class="note">Same tasks, same model; only what I may remember changes. ${esc(s.measure)}. Measured ${esc(s.measured.replace("T", " "))}.</p>`;
    };

    const draw = () => {
      if (!data.linked) {
        body.innerHTML = `<div class="surface card"><div class="empty">My notes aren't linked to an Obsidian vault yet. Link one in Settings, and what I learn will show up here.</div></div>`;
        return;
      }
      const items = data.items.filter((it) => group === "all" || it.group === group);
      const c = data.counts;
      body.innerHTML = `
        <div class="grid12">
          <div class="surface card stack c7"><h3>Am I getting better? <span class="chip">brain score</span></h3>${drawScore(data.score)}</div>
          <div class="surface card stack c5"><h3>This week</h3>
            <div class="row" style="flex-wrap:wrap;gap:var(--s4)">
              <div class="stat"><b>${data.week.new}</b><div class="note">new notes</div></div>
              <div class="stat"><b>${data.week.from_user}</b><div class="note">from you</div></div>
              <div class="stat"><b>${data.week.skills}</b><div class="note">skills checked</div></div></div>
            <p class="note">${Object.keys(c).length ? Object.entries(c).map(([k, v]) => `${v} ${k}`).join(" · ") : "Nothing learned yet."}</p></div>
          <div class="surface card stack c12"><h3>Lessons, skills and what I know about you</h3>
            <div class="seg" role="group" aria-label="Show">${GROUPS.map(([v, l]) => `<button type="button" data-group="${v}" aria-pressed="${v === group}">${l}${v === "all" ? ` (${data.items.length})` : c[v] ? ` (${c[v]})` : ""}</button>`).join("")}</div>
            <div>${items.length ? items.map((it) => `
              <div class="lrow"><div class="top2"><b>${esc(it.title)}</b><button class="btn" type="button" data-forget="${esc(it.id)}" data-title="${esc(it.title)}">Stop using this</button></div>
                ${it.text ? `<p>${esc(it.text)}</p>` : ""}
                <div class="metas"><span class="chip">${esc(it.group)}</span><span class="chip">${it.source === "user" ? "you told me" : "I learned it"}</span>
                  ${it.status === "unverified" ? `<span class="chip" style="color:var(--warn)">not verified</span>` : ""}
                  ${it.checked ? `<span class="chip">checked ${it.successes} of ${it.checked}</span>` : ""}${it.uses ? `<span class="chip">used ${it.uses}×</span>` : ""}</div>
                ${evidence(it)}</div>`).join("")
              : `<div class="empty">${data.items.length ? "Nothing in this group." : "Nothing yet. When I check my own work, or you correct me, it shows up here."}</div>`}</div>
          </div>
        </div>`;
    };

    const load = async () => {
      const r = await api.get("/ui/learned");
      if (!r.ok) { body.innerHTML = `<div class="surface card"><div class="empty">Couldn't load this. ${esc(r.error || "")}</div></div>`; return; }
      data = r; draw();
    };
    body.addEventListener("click", async (e) => {
      const g = e.target.closest("[data-group]");
      if (g) { group = g.dataset.group; draw(); return; }
      const f = e.target.closest("[data-forget]");
      if (!f) return;
      if (f.dataset.confirm !== "1") { f.dataset.confirm = "1"; f.textContent = "Sure?"; return; }
      const r = await api.del("/ui/learned", { id: f.dataset.forget });
      if (r.ok) { ctx.toast(`I'll stop using “${f.dataset.title}”. The note stays in your vault.`); await load(); } else ctx.toast(r.error || "Couldn't do that.");
    });
    await load();
  },
};
