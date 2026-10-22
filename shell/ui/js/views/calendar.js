// Calendar & reminders: a month over the user's real tasks and reminders
// (/ui/tasks). Adding, finishing, snoozing and deleting all go through the
// same task tracker MBUDDY uses when you ask by voice.
import { api } from "../api.js";
import { esc, header } from "./ui.js";

const pad = (n) => String(n).padStart(2, "0");
const key = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const clockTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export const calendar = {
  async render(el, ctx) {
    el.classList.add("sec");
    el.innerHTML = `<div class="top">${header("Calendar & reminders", "Say it or type it. I put it on the right day and remind you at the right time.")}</div>
      <form class="surface card stack" id="cal-form">
        <h3>Add a reminder</h3>
        <div class="frm3">
          <label><span class="label">What</span><input class="field" id="cal-title" maxlength="200" placeholder="Call the bank" required></label>
          <label><span class="label">Day</span><input class="field" type="date" id="cal-date" required></label>
          <label><span class="label">Time (optional)</span><input class="field" type="time" id="cal-time"></label>
          <button class="btn accent" type="submit">Add</button>
        </div>
        <p class="note">Without a time I use 9:00. You can also just say: “remind me to call the bank on Friday at 3:30”.</p>
      </form>
      <div class="grid12">
        <div class="surface card stack c8">
          <div class="row between"><div class="monthnav"><button class="btn icon" type="button" id="cal-prev" aria-label="Previous month">‹</button><b id="cal-title-m"></b><button class="btn icon" type="button" id="cal-next" aria-label="Next month">›</button></div>
            <button class="btn" type="button" id="cal-today">Today</button></div>
          <div class="cal" id="cal-grid" role="grid" aria-label="Month"></div>
        </div>
        <div class="surface card stack c4">
          <h3 id="cal-day-h">Day</h3>
          <div class="row" style="gap:var(--s3)"><svg class="clock" viewBox="0 0 60 60" aria-hidden="true"><circle cx="30" cy="30" r="27"/><g id="tk"></g>
            <line id="hh" x1="30" y1="30" x2="30" y2="16"/><line id="mh" x1="30" y1="30" x2="30" y2="9"/><circle class="pin" cx="30" cy="30" r="2.4"/></svg>
            <p class="think" id="cal-next-t"></p></div>
          <div class="list" id="cal-list"></div>
        </div>
        <div class="surface card stack c12" id="cal-nodate" hidden><h3>No day set</h3><div class="list" id="cal-nodate-l"></div></div>
      </div>`;
    const tk = el.querySelector("#tk");
    for (let i = 0; i < 12; i++) tk.insertAdjacentHTML("beforeend", `<line class="tk" x1="30" y1="5" x2="30" y2="8" transform="rotate(${i * 30} 30 30)"/>`);

    let tasks = [], month = new Date(), sel = key(new Date());
    month.setDate(1);
    const $ = (s) => el.querySelector(s);

    const byDay = () => {
      const m = {};
      tasks.filter((t) => t.due_date).forEach((t) => { (m[key(new Date(t.due_date))] ||= []).push(t); });
      Object.values(m).forEach((l) => l.sort((a, b) => a.due_date.localeCompare(b.due_date)));
      return m;
    };

    const drawGrid = () => {
      const days = byDay(), first = new Date(month), today = key(new Date());
      $("#cal-title-m").textContent = month.toLocaleDateString([], { month: "long", year: "numeric" });
      const lead = (first.getDay() + 6) % 7, start = new Date(first); start.setDate(1 - lead);
      let html = "MTWTFSS".split("").map((d) => `<div class="dow">${d}</div>`).join("");
      for (let i = 0; i < 42; i++) {
        const d = new Date(start); d.setDate(start.getDate() + i);
        const k = key(d), list = days[k] || [];
        html += `<button type="button" class="day ${d.getMonth() !== month.getMonth() ? "out" : ""} ${k === today ? "today" : ""}" data-day="${k}" aria-pressed="${k === sel}" aria-label="${d.toDateString()}${list.length ? `, ${list.length} reminder${list.length === 1 ? "" : "s"}` : ""}">
          <span class="n">${d.getDate()}</span>${list.length ? `<span class="dotrow">${list.slice(0, 4).map(() => "<i></i>").join("")}</span>${list.length > 4 ? `<span class="cnt">+${list.length - 4}</span>` : ""}` : ""}</button>`;
      }
      $("#cal-grid").innerHTML = html;
    };

    const setClock = (iso) => {
      const d = iso ? new Date(iso) : null;
      $("#hh").style.transform = d ? `rotate(${(d.getHours() % 12) * 30 + d.getMinutes() / 2}deg)` : "rotate(0deg)";
      $("#mh").style.transform = d ? `rotate(${d.getMinutes() * 6}deg)` : "rotate(0deg)";
    };

    const item = (t) => `<div class="item"><div class="grow"><div class="t">${esc(t.title)}</div>${t.due_date ? `<div class="s">${clockTime(t.due_date)}</div>` : ""}</div>
        <button class="btn" data-done="${t.task_id}" type="button">Done</button>
        ${t.due_date ? `<button class="btn" data-snooze="${t.task_id}" type="button" title="Remind me again in 10 minutes">In 10 min</button>` : ""}
        <button class="btn icon danger" data-del="${t.task_id}" data-title="${esc(t.title)}" type="button" aria-label="Delete ${esc(t.title)}"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg></button></div>`;

    const drawDay = () => {
      const d = new Date(sel + "T00:00"), list = byDay()[sel] || [];
      $("#cal-day-h").textContent = d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
      $("#cal-list").innerHTML = list.length ? list.map(item).join("") : `<div class="empty">Nothing on this day. Add a reminder above.</div>`;
      const next = list.find((t) => new Date(t.due_date) > new Date()) || list[0];
      setClock(next && next.due_date);
      $("#cal-next-t").textContent = next ? `${esc(next.title)} at ${clockTime(next.due_date)}` : "";
      const nodate = tasks.filter((t) => !t.due_date);
      $("#cal-nodate").hidden = !nodate.length;
      $("#cal-nodate-l").innerHTML = nodate.map(item).join("");
    };

    const load = async () => {
      const r = await api.get("/ui/tasks");
      if (!r.ok) { $("#cal-list").innerHTML = `<div class="empty">Couldn't load your reminders.</div>`; return; }
      tasks = r.tasks; drawGrid(); drawDay();
    };

    $("#cal-date").value = sel;
    $("#cal-prev").onclick = () => { month.setMonth(month.getMonth() - 1); drawGrid(); };
    $("#cal-next").onclick = () => { month.setMonth(month.getMonth() + 1); drawGrid(); };
    $("#cal-today").onclick = () => { month = new Date(); month.setDate(1); sel = key(new Date()); $("#cal-date").value = sel; drawGrid(); drawDay(); };
    $("#cal-grid").addEventListener("click", (e) => {
      const b = e.target.closest("[data-day]");
      if (!b) return;
      sel = b.dataset.day; $("#cal-date").value = sel; drawGrid(); drawDay();
    });
    const acts = async (e) => {
      const done = e.target.closest("[data-done]"), del = e.target.closest("[data-del]"), sn = e.target.closest("[data-snooze]");
      if (done) { await api.post(`/ui/tasks/${done.dataset.done}/done`); ctx.toast("Done."); await load(); }
      if (sn) { const r = await api.post(`/ui/tasks/${sn.dataset.snooze}/snooze`, { minutes: 10 }); ctx.toast(r.ok ? "I'll remind you again in 10 minutes." : (r.error || "Couldn't snooze.")); await load(); }
      if (del) {
        const row = del.closest(".item"); row.hidden = true;
        const timer = setTimeout(async () => { await api.del(`/ui/tasks/${del.dataset.del}`); load(); }, 5000);
        ctx.toast(`Deleted “${del.dataset.title}”.`, 5000, { label: "Undo", run: () => { clearTimeout(timer); row.hidden = false; } });
      }
    };
    $("#cal-list").addEventListener("click", acts);
    $("#cal-nodate-l").addEventListener("click", acts);
    $("#cal-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = $("#cal-title").value.trim(), day = $("#cal-date").value, time = $("#cal-time").value;
      if (!title || !day) return;
      const r = await api.post("/ui/tasks", { title, when: `${day} ${time || "09:00"}` });
      if (!r.ok) { ctx.toast(r.error || "Couldn't add that."); return; }
      $("#cal-title").value = ""; $("#cal-time").value = "";
      sel = day; const d = new Date(day + "T00:00"); month = new Date(d.getFullYear(), d.getMonth(), 1);
      await load();
      const hit = $(`[data-day="${sel}"]`); if (hit) { hit.classList.add("hitday"); }
      setClock(r.task.due_date);
      ctx.toast(`Reminder set for ${new Date(r.task.due_date).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}.`);
    });
    await load();
  },
};
