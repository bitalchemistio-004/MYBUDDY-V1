// Tasks & reminders, Memory, Activity: real data from the backend.
import { api } from "../api.js";
import { esc, header, fmtDue } from "./ui.js";

export const tasks = {
  async render(el, ctx) {
    el.innerHTML = `${header("Tasks & reminders", "What you asked MBUDDY to remember to do. Reminders with a time are spoken and notified.")}
      <form class="surface card row" id="t-form" style="flex-wrap:wrap">
        <label class="grow" style="flex:2;min-width:220px"><span class="label">New task</span><input class="field" id="t-title" maxlength="200" placeholder="Call the bank" required style="margin-top:6px"></label>
        <label style="flex:1;min-width:180px"><span class="label">When (optional)</span><input class="field" id="t-when" placeholder="in 20 minutes · at 17:00" style="margin-top:6px"></label>
        <button class="btn accent" type="submit" style="align-self:flex-end">Add</button>
      </form>
      <div class="surface card stack"><div class="label">Pending</div><div class="list" id="t-list"></div></div>`;
    const list = el.querySelector("#t-list");
    const load = async () => {
      const r = await api.get("/ui/tasks").catch(() => null);
      if (!r || !r.ok) { list.innerHTML = `<div class="empty">Couldn't load tasks.</div>`; return; }
      list.innerHTML = r.tasks.length ? r.tasks.map((t) => `
        <div class="item"><div class="grow"><div class="t">${esc(t.title)}</div>${t.due_date ? `<div class="s">${fmtDue(t.due_date)}</div>` : ""}</div>
          <button class="btn" data-done="${t.task_id}" type="button">Done</button>
          <button class="btn icon danger" data-del="${t.task_id}" data-title="${esc(t.title)}" type="button" aria-label="Delete ${esc(t.title)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>`).join("")
        : `<div class="empty">No pending tasks. Try saying “remind me in 10 minutes to stretch”.</div>`;
    };
    list.addEventListener("click", async (e) => {
      const done = e.target.closest("[data-done]"), del = e.target.closest("[data-del]");
      if (done) { await api.post(`/ui/tasks/${done.dataset.done}/done`); ctx.toast("Done."); load(); }
      if (del) {
        // Deleting is destructive: hide the row at once, delete for real after
        // 5 s unless the user presses Undo.
        const row = del.closest(".item"); row.hidden = true;
        const timer = setTimeout(async () => { await api.del(`/ui/tasks/${del.dataset.del}`); load(); }, 5000);
        ctx.toast(`Deleted “${del.dataset.title}”.`, 5000, { label: "Undo", run: () => { clearTimeout(timer); row.hidden = false; } });
      }
    });
    el.querySelector("#t-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = el.querySelector("#t-title").value.trim(), when = el.querySelector("#t-when").value.trim();
      const r = await api.post("/ui/tasks", { title, when });
      if (!r.ok) { ctx.toast(r.error || "Couldn't add that."); return; }
      el.querySelector("#t-title").value = ""; el.querySelector("#t-when").value = "";
      ctx.toast(r.task.due_date ? `Reminder set for ${fmtDue(r.task.due_date)}.` : "Added.");
      load();
    });
    await load();
  },
};

export const memory = {
  async render(el, ctx) {
    el.innerHTML = `${header("Memory", "What MBUDDY knows about you. Only what you told it; delete anything you don't want kept.")}
      <div class="surface card stack"><div class="label">Preferences</div><div class="list" id="p-list"><div class="empty">Loading…</div></div></div>
      <div class="surface card stack"><div class="label">Facts</div><div class="list" id="m-list"><div class="empty">Loading…</div></div></div>`;
    const lists = { preference: el.querySelector("#p-list"), fact: el.querySelector("#m-list") };
    const empty = {
      preference: "None yet. Say how you like answers, like “from now on, use bullet points”, and MBUDDY follows it every time.",
      fact: "Nothing yet. Tell MBUDDY something about yourself, like “my sister's name is Lina”.",
    };
    let items = { preference: [], fact: [] };
    const draw = () => {
      for (const kind of ["preference", "fact"]) {
        lists[kind].innerHTML = items[kind].length ? items[kind].map((f, i) => `
          <div class="item"><div class="grow">${esc(f)}</div><button class="btn danger" type="button" data-kind="${kind}" data-i="${i}">Forget</button></div>`).join("")
          : `<div class="empty">${empty[kind]}</div>`;
      }
    };
    const take = (r) => { items = { preference: r.preferences || [], fact: r.facts || [] }; draw(); };
    el.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-kind]");
      if (!b) return;
      const kind = b.dataset.kind, text = items[kind][Number(b.dataset.i)];
      if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = "Sure?"; return; }
      const r = await api.del("/ui/memory", { [kind]: text });
      if (r.ok) { take(r); ctx.toast("Forgotten."); } else ctx.toast(r.error || "Couldn't forget that.");
    });
    const r = await api.get("/ui/memory").catch(() => null);
    if (!r || !r.ok) {
      for (const l of Object.values(lists)) l.innerHTML = `<div class="empty">I couldn't read what I remember. Check Status, then open this page again.</div>`;
      return;
    }
    take(r);
  },
};

export const activity = {
  async render(el) {
    el.innerHTML = `${header("Activity", "Everything MBUDDY actually did, newest first. It only says it did something when it's here.")}
      <div class="surface card stack"><div class="label">Since MBUDDY started</div><div class="list" id="a-list"><div class="empty">Loading…</div></div></div>`;
    const r = await api.get("/ui/overview").catch(() => null);
    if (!r || !r.ok) { el.querySelector("#a-list").innerHTML = `<div class="empty">I couldn't read the action log. Check Status, then open this page again.</div>`; return; }
    const items = r.recent_actions.slice().reverse();
    el.querySelector("#a-list").innerHTML = items.length ? items.map((a) => `
      <div class="item"><span class="chip">${esc(a.time)}</span><div class="grow"><div class="t">${esc(a.tool.replace(/_/g, " "))}</div><div class="s">${esc(a.result)}</div></div></div>`).join("")
      : `<div class="empty">No actions yet. When I set a timer, change the volume, open an app or save something, it is listed here with the time.</div>`;
  },
};
