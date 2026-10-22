// Today: what is coming (agenda), what to do (tasks) and what to remember (notes), in one place. Plain lists, one + to add (a sheet
// slides up), a tap to tick a task, and removing anything can be undone. Events are also written into the phone's own calendar.
import { esc, clock, dayLabel, timeOf, z2 } from "../util.js";
import { rowGestures } from "../gestures.js";
import { tipHtml, wireTips } from "../tips.js";
import { buzz } from "../haptics.js";

let off = null, tick = null;
const TAB_KEY = "mbp_today_tab";
const TABS = [["agenda", "Agenda"], ["tasks", "Tasks"], ["notes", "Notes"]];
const getTab = () => { try { return localStorage.getItem(TAB_KEY) || "agenda"; } catch { return "agenda"; } };
const setTab = (t) => { try { localStorage.setItem(TAB_KEY, t); } catch { /* fine */ } };
const localInput = (d) => `${d.getFullYear()}-${z2(d.getMonth() + 1)}-${z2(d.getDate())}T${z2(d.getHours())}:${z2(d.getMinutes())}`;
const nextHour = () => { const d = new Date(Date.now() + 3600000); d.setMinutes(0, 0, 0); return localInput(d); };
const X = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>`;
const CHECK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>`;

export default {
  render(el, ctx, sub = [], only = null) {
    const { store } = ctx;
    let tab = only || (TABS.some(([t]) => t === sub[0]) ? sub[0] : getTab());
    const now = new Date();

    const agenda = () => {
      const today = store.dayKey(), end = store.dayKey(new Date(Date.now() + 14 * 86400000));
      const items = [
        ...store.events.list().map((e) => ({ at: e.start, text: e.title, sub: e.place, kind: "event", id: e.id })),
        ...store.tasks.list().filter((t) => !t.done && store.placeOf("task", t) === "today").map((t) => ({ at: t.when, text: t.title, sub: t.when.slice(0, 10) < today ? dayLabel(t.when.slice(0, 10)) : "", kind: "task", id: t.id })),
      ].filter((i) => i.at.slice(0, 10) <= end && (i.at.slice(0, 10) >= today || i.kind === "task")).sort((a, b) => a.at.localeCompare(b.at));
      // what is late is one group at the top, not a heading for each past day
      const dk = (i) => (i.at.slice(0, 10) < today ? "overdue" : i.at.slice(0, 10));
      const days = [...new Set(items.map(dk))];
      const nowStamp = localInput(new Date());
      const f = store.focus.state();
      return (f ? `<div class="pa-focus"><span>Focus</span><b id="t-left">${clock((f.endsAt - Date.now()) / 1000)}</b><button class="pa-link" id="t-fstop" type="button">Stop</button></div>` : "")
        + (days.length ? days.map((d) => `<h3 class="pa-day${d === "overdue" ? " late" : ""}">${d === "overdue" ? "Overdue" : esc(dayLabel(d))}</h3><ul class="pa-rows pa-timeline">${items.filter((i) => dk(i) === d).map((i, k, arr) => `${d === today && k === arr.findIndex((x) => x.at >= nowStamp) ? `<li class="pa-nowline" aria-hidden="true"><span>Now</span></li>` : ""}
          <li class="pa-row-item pa-ag ${i.kind}" style="--i:${k}" data-open="${i.kind}:${i.id}" tabindex="0"><time>${i.kind === "note" ? "Note" : esc(timeOf(i.at))}</time><i class="pa-ag-dot" aria-hidden="true"></i><div class="grow"><div class="t">${esc(i.text)}</div><div class="s">${i.kind === "event" ? "Event" : i.kind === "note" ? "Sticky note" : "Task to do"}${i.sub ? " · " + esc(i.sub) : ""}</div></div>
            ${i.kind === "event" ? `<button class="pa-x" type="button" data-evdel="${i.id}" aria-label="Remove ${esc(i.text)}">${X}</button>` : ""}</li>`).join("")}</ul>`).join("")
          : `<div class="pa-none"><b>Nothing with a time in the next two weeks.</b><span>Tap + to add an event, or tell me: “dentist on Friday at 10”. Tasks without a time are in Do, notes in Notes.</span></div>`);
    };

    const tasks = () => {
      // a task with a time lives in Today; Do is the list of things to do with no time attached (and what you have finished)
      const list = store.tasks.list().filter((t) => t.done || store.placeOf("task", t) === "do"), open = list.filter((t) => !t.done), done = list.filter((t) => t.done);
      const row = (t, k) => `<li class="pa-row-item${t.done ? " is-done" : ""}" style="--i:${k}" data-open="task:${t.id}" tabindex="0">
          <button class="pa-check" type="button" data-done="${t.id}" aria-pressed="${t.done}" aria-label="${t.done ? "Not done" : "Done"}: ${esc(t.title)}">${CHECK}</button>
          <div class="grow"><div class="t">${esc(t.title)}</div>${t.when ? `<div class="s">${esc(dayLabel(t.when.slice(0, 10)))} ${esc(timeOf(t.when))}</div>` : ""}</div>
          <button class="pa-x" type="button" data-del="${t.id}" aria-label="Remove ${esc(t.title)}">${X}</button></li>`;
      const todayKey = store.dayKey();
      const rest = only === "tasks" ? open.slice(1) : open;       // in Do the next task is the card above, not repeated in the list
      const groups = [["Overdue", rest.filter((t) => t.when && t.when.slice(0, 10) < todayKey)], ["Today", rest.filter((t) => t.when && t.when.slice(0, 10) === todayKey)],
        ["Later", rest.filter((t) => t.when && t.when.slice(0, 10) > todayKey)], ["Anytime", rest.filter((t) => !t.when)]].filter(([, l]) => l.length);
      let k = 0;
      const openHtml = groups.map(([name, l]) => `<h3 class="pa-day${name === "Overdue" ? " late" : ""}">${name} · ${l.length}</h3><ul class="pa-rows">${l.map((t) => row(t, k++)).join("")}</ul>`).join("");
      const next = open[0];
      const nextCard = only === "tasks" && next ? `<section class="pa-next" data-open="task:${next.id}" tabindex="0"><span class="pa-kind">Next up</span><div class="pa-next-t">${esc(next.title)}</div>${next.when ? `<div class="s">${esc(dayLabel(next.when.slice(0, 10)))} ${esc(timeOf(next.when))}</div>` : ""}
        <div class="pa-actions"><button class="pa-btn go" type="button" data-done="${next.id}">Done</button><button class="pa-btn" type="button" data-focus="25">Focus 25 min</button></div></section>` : "";
      return open.length || done.length ? `${nextCard}${openHtml}${done.length ? `<details class="pa-done-list"><summary>Done · ${done.length}</summary><ul class="pa-rows">${done.map(row).join("")}</ul></details>` : ""}`
        : `<div class="pa-none"><b>Nothing to do.</b><span>Tap + to add a task. Give it a time and it moves to Today.</span></div>`;
    };

    // sticky notes: each is stuck to a day. Today first, then what is coming, then the past. A new one drops on and is pinned; a removed one is peeled off.
    let freshNext = false;
    const notes = () => {
      const list = store.notes.list();
      if (!list.length) return `<div class="pa-none"><b>No notes.</b><span>Tap + to stick one to a day, or tell me: “note: Ali likes green tea”.</span></div>`;
      const today = store.dayKey(), dayOf = (n) => n.day || store.dayKey(new Date(n.created));
      const newest = freshNext ? list.reduce((a, b) => (b.created > a.created ? b : a)) : null; freshNext = false;
      const PASTEL = ["#ffe27a", "#ffb4a2", "#b8f2c9", "#cfc0ff", "#a8dcf5", "#ffd3e8"], TILT = [-1.6, 1.1, -.8, 1.5, -1.2, .9];
      const days = [...new Set(list.map(dayOf))];
      const rank = (d) => (d === today ? 0 : d > today ? 1 : 2);
      days.sort((a, b) => rank(a) - rank(b) || (rank(a) === 2 ? b.localeCompare(a) : a.localeCompare(b)));
      let k = 0;
      return days.map((d) => `<h3 class="pa-day">${d === today ? "Today" : esc(dayLabel(d))}</h3><div class="pa-sticky-wall">${list.filter((n) => dayOf(n) === d).map((n) => {
        const c = k++;
        return `<div class="pa-sticky${newest && newest.id === n.id ? " dropping" : ""}" style="--i:${c};background:${PASTEL[c % 6]};--t:${TILT[c % 6]}deg" data-open="note:${n.id}" tabindex="0"><i class="pa-pin" aria-hidden="true"></i>
          <div class="t">${esc(n.text)}</div><button class="pa-x" type="button" data-ndel="${n.id}" aria-label="Peel off this note">${X}</button></div>`;
      }).join("")}</div>`).join("");
    };

    const draw = () => {
      const HEAD = { agenda: ["Today", now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })], tasks: ["Do", "Things to do, no time attached"], notes: ["Notes", "Pinned to the day you choose"] }[tab];
      el.innerHTML = `<header class="pa-head pa-head-add"><div><h1>${HEAD[0]}</h1><p>${esc(HEAD[1])}</p></div><button class="pa-fab" id="t-add" type="button" aria-label="Add ${tab === "tasks" ? "a task" : tab === "notes" ? "a note" : "an event"}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg><span>${tab === "tasks" ? "Task" : tab === "notes" ? "Note" : "Event"}</span></button></header>
        ${only ? "" : `<div class="pa-seg" role="tablist" aria-label="Today">${TABS.map(([id, label]) => `<button type="button" role="tab" data-tab="${id}" aria-selected="${tab === id}">${label}</button>`).join("")}<i class="pa-seg-mark" aria-hidden="true"></i></div>`}
        <div class="pa-list-wrap" id="t-body">${(tab !== "notes" && (store.tasks.list().length + store.events.list().length) > 0) ? tipHtml(store, "swipe", "Swipe a row right to finish it, left to push it back an hour or remove it. Press and hold a row for more.") : ""}${tab === "tasks" ? tasks() : tab === "notes" ? notes() : agenda()}</div>`;
      const seg = el.querySelector(".pa-seg");
      if (seg) {
        const mark = seg.querySelector(".pa-seg-mark");
        const place = () => { const b = seg.querySelector(`[data-tab="${tab}"]`); mark.style.transform = `translateX(${b.offsetLeft}px)`; mark.style.width = b.offsetWidth + "px"; };
        requestAnimationFrame(place);
        seg.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { if (tab === b.dataset.tab) return; tab = b.dataset.tab; setTab(tab); draw(); }));
      }
      el.querySelector("#t-add").addEventListener("click", add);
      wire();
    };

    // swiping a row: right finishes a task; left puts a task back an hour, or removes an event or a note (with Undo)
    const swipe = (li, dir) => {
      const [kind, id] = li.dataset.open.split(":");
      if (kind === "task") {
        const t = store.tasks.list().find((x) => x.id === id); if (!t) return false;
        if (dir === "right") { li.classList.add("ticking"); setTimeout(() => store.tasks.done(id, !t.done), 220); return true; }
        const was = t.when || ""; store.tasks.snooze(id, 60);
        ctx.toast("Moved an hour later.", 4500, { label: "Undo", run: () => store.tasks.update(id, { when: was }) });
        return true;
      }
      if (dir !== "left") return false;
      if (kind === "event") { setTimeout(() => { store.events.remove(id); undo("Event", () => store.events.restore(id)); }, 200); return true; }
      if (kind === "note") { setTimeout(() => { store.notes.remove(id); undo("Note", () => store.notes.restore(id)); }, 200); return true; }
      return false;
    };
    // pressing and holding a row: its quick menu
    const hold = (li) => {
      const [kind, id] = li.dataset.open.split(":");
      const copy = (text) => { try { navigator.clipboard.writeText(text).then(() => ctx.toast("Copied.")).catch(() => ctx.toast("Could not copy.")); } catch { ctx.toast("Could not copy."); } };
      const tomorrowAt = (hm) => { const d = new Date(Date.now() + 86400000); return `${store.dayKey(d)}T${hm}`; };
      ctx.sheet((body, close) => {
        const items = [];
        if (kind === "task") {
          const t = store.tasks.list().find((x) => x.id === id); if (!t) { close(); return; }
          items.push([t.done ? "Put it back" : "Finish", () => store.tasks.done(id, !t.done)]);
          items.push(["In one hour (moves to Today)", () => store.tasks.snooze(id, 60)]);
          items.push(["Tomorrow morning (moves to Today)", () => store.tasks.update(id, { when: tomorrowAt("09:00") })]);
          items.push(["Start a 10 minute timer for it", () => { try { window.Native && window.Native.act("timer", JSON.stringify({ seconds: 600, label: t.title })); ctx.toast("Timer started."); } catch { ctx.toast("No timer here."); } }]);
          items.push(["Copy", () => copy(t.title)]);
          items.push(["Remove", () => { store.tasks.remove(id); undo("Task", () => store.tasks.restore(id)); }]);
        } else if (kind === "event") {
          const e = store.events.list().find((x) => x.id === id); if (!e) { close(); return; }
          if (e.place) items.push(["Directions", () => { try { window.Native.act("navigate", JSON.stringify({ place: e.place })); } catch { ctx.toast("Could not open maps."); } }]);
          items.push(["Copy", () => copy(e.title + (e.place ? ", " + e.place : ""))]);
          items.push(["Remove", () => { store.events.remove(id); undo("Event", () => store.events.restore(id)); }]);
        } else if (kind === "note") {
          const n = store.notes.list().find((x) => x.id === id); if (!n) { close(); return; }
          items.push(["Move to tomorrow", () => store.notes.update(id, { day: store.dayKey(new Date(Date.now() + 86400000)) })]);
          items.push(["Ask the buddy about it", () => ctx.say(`About my note "${n.text.slice(0, 300)}": what should I know or do?`)]);
          items.push(["Copy", () => copy(n.text)]);
          items.push(["Remove", () => { store.notes.remove(id); undo("Note", () => store.notes.restore(id)); }]);
        }
        body.insertAdjacentHTML("beforeend", `<div class="pa-menu">${items.map(([label], i) => `<button type="button" class="pa-menu-b${label === "Remove" ? " danger" : ""}" data-m="${i}">${esc(label)}</button>`).join("")}</div>`);
        body.querySelectorAll("[data-m]").forEach((b) => b.addEventListener("click", () => { buzz("tick"); close(); items[Number(b.dataset.m)][1](); }));
      });
    };

    const undo = (what, restore) => ctx.toast(`${what} removed.`, 4500, { label: "Undo", run: restore });
    const wire = () => {
      el.querySelectorAll("[data-done]").forEach((b) => b.addEventListener("click", () => {
        const t = store.tasks.list().find((x) => x.id === b.dataset.done);
        (b.closest("li") || b.closest("section")).classList.add("ticking");
        setTimeout(() => store.tasks.done(b.dataset.done, !(t && t.done)), 360);       // the tick plays before the list changes
      }));
      el.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => { const id = b.dataset.del; leave(b, () => { store.tasks.remove(id); undo("Task", () => store.tasks.restore(id)); }); }));
      el.querySelectorAll("[data-evdel]").forEach((b) => b.addEventListener("click", () => { const id = b.dataset.evdel; leave(b, () => { store.events.remove(id); undo("Event", () => store.events.restore(id)); }); }));
      el.querySelectorAll("[data-ndel]").forEach((b) => b.addEventListener("click", () => { const id = b.dataset.ndel; leave(b, () => { store.notes.remove(id); undo("Note", () => store.notes.restore(id)); }); }));
      const stop = el.querySelector("#t-fstop"); if (stop) stop.addEventListener("click", () => store.focus.stop());
      el.querySelectorAll("[data-focus]").forEach((bt) => bt.addEventListener("click", () => {
        const m = Number(bt.dataset.focus); store.focus.start(m);
        try { window.Native && window.Native.act("timer", JSON.stringify({ seconds: m * 60, label: "Focus is over" })); } catch { /* the sprint still counts here */ }
        store.log.add(`Focus sprint, ${m} min`); ctx.toast(`Focus for ${m} minutes. I'll tell you when it is over.`);
      }));
      rowGestures(el, { onSwipe: swipe, onHold: hold });
      wireTips(el, store);
      el.querySelectorAll("[data-open]").forEach((li) => {
        const go = (e) => { if (e.target.closest("button")) return; li.classList.add("pressed"); setTimeout(() => li.classList.remove("pressed"), 220); const [k, id] = li.dataset.open.split(":"); details(k, id); };
        li.addEventListener("click", go);
        li.addEventListener("keydown", (e) => { if (e.key === "Enter") go(e); });
      });
    };

    // the details of one thing: everything about it, editable, saved as you go, and what you can do with it
    const details = (kind, id) => ctx.sheet((body, close) => {
      const save = (fn) => { try { fn(); } catch { /* the field keeps its text */ } };
      if (kind === "task") {
        const t = store.tasks.list().find((x) => x.id === id); if (!t) { close(); return; }
        body.insertAdjacentHTML("beforeend", `<div class="pa-detail">
          <span class="pa-kind">Task${t.done ? " · done" : ""}</span>
          <textarea class="pa-title-edit" id="d-title" rows="1" maxlength="200" aria-label="Task">${esc(t.title)}</textarea>
          <label class="pa-label" for="d-when">When</label><input class="pa-field" type="datetime-local" id="d-when" value="${esc(t.when || "")}">
          <label class="pa-label" for="d-notes">Notes</label><textarea class="pa-field" id="d-notes" rows="3" maxlength="2000" placeholder="Anything to remember about it">${esc(t.notes || "")}</textarea>
          <div class="pa-actions">
            <button class="pa-btn go" id="d-done" type="button">${t.done ? "Not done yet" : "Mark done"}</button>
            <button class="pa-btn" id="d-later" type="button">In an hour</button>
            <button class="pa-btn" id="d-tomorrow" type="button">Tomorrow</button>
          </div>
          <button class="pa-btn danger" id="d-del" type="button">Remove task</button>
          <p class="pa-meta">Added ${esc(new Date(t.created).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}</p></div>`);
        const $ = (s) => body.querySelector(s);
        grow($("#d-title"));
        $("#d-title").addEventListener("input", (e) => grow(e.target));
        $("#d-title").addEventListener("change", (e) => save(() => e.target.value.trim() && store.tasks.update(id, { title: e.target.value.trim() })));
        $("#d-when").addEventListener("change", (e) => save(() => store.tasks.update(id, { when: e.target.value || "" })));
        $("#d-notes").addEventListener("change", (e) => save(() => store.tasks.update(id, { notes: e.target.value })));
        $("#d-done").addEventListener("click", () => { store.tasks.done(id, !t.done); close(); ctx.toast(t.done ? "Back on the list." : "Done. Nice."); });
        $("#d-later").addEventListener("click", () => { store.tasks.snooze(id, 60); close(); ctx.toast("Moved an hour later."); });
        $("#d-tomorrow").addEventListener("click", () => { const d = new Date(Date.now() + 86400000); const hm = (t.when || "").slice(11, 16) || "09:00"; store.tasks.update(id, { when: `${store.dayKey(d)}T${hm}`, done: false }); close(); ctx.toast("Moved to tomorrow."); });
        $("#d-del").addEventListener("click", () => { store.tasks.remove(id); close(); undo("Task", () => store.tasks.restore(id)); });
      } else if (kind === "event") {
        const ev = store.events.list().find((x) => x.id === id); if (!ev) { close(); return; }
        body.insertAdjacentHTML("beforeend", `<div class="pa-detail">
          <span class="pa-kind">Event · ${esc(dayLabel(ev.start.slice(0, 10)))} ${esc(timeOf(ev.start))}</span>
          <textarea class="pa-title-edit" id="d-title" rows="1" maxlength="200" aria-label="Event">${esc(ev.title)}</textarea>
          <label class="pa-label" for="d-start">Starts</label><input class="pa-field" type="datetime-local" id="d-start" value="${esc(ev.start.slice(0, 16))}">
          <label class="pa-label" for="d-place">Where</label><input class="pa-field" id="d-place" maxlength="100" value="${esc(ev.place || "")}" placeholder="Add a place">
          <div class="pa-actions">${ev.place ? `<button class="pa-btn go" id="d-map" type="button">Directions</button>` : ""}<button class="pa-btn" id="d-cal" type="button">Open in my calendar</button></div>
          <button class="pa-btn danger" id="d-del" type="button">Remove event</button></div>`);
        const $ = (s) => body.querySelector(s);
        grow($("#d-title")); $("#d-title").addEventListener("input", (e) => grow(e.target));
        $("#d-title").addEventListener("change", (e) => save(() => e.target.value.trim() && store.events.update(id, { title: e.target.value.trim() })));
        $("#d-start").addEventListener("change", (e) => save(() => e.target.value && store.events.update(id, { start: e.target.value })));
        $("#d-place").addEventListener("change", (e) => save(() => store.events.update(id, { place: e.target.value.trim() })));
        const map = $("#d-map"); if (map) map.addEventListener("click", () => { try { window.Native.act("navigate", JSON.stringify({ place: ev.place })); } catch { /* not in the app */ } close(); });
        $("#d-cal").addEventListener("click", () => { try { window.Native.act("calendar", JSON.stringify({ title: ev.title, start: new Date(ev.start).toISOString(), place: ev.place || "" })); } catch { /* not in the app */ } close(); });
        $("#d-del").addEventListener("click", () => { store.events.remove(id); close(); undo("Event", () => store.events.restore(id)); });
      } else {
        const n = store.notes.list().find((x) => x.id === id); if (!n) { close(); return; }
        body.insertAdjacentHTML("beforeend", `<div class="pa-detail">
          <span class="pa-kind">Note · ${esc(new Date(n.created).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }))}</span>
          <textarea class="pa-field pa-note-edit" id="d-text" rows="7" maxlength="2000" aria-label="Note">${esc(n.text)}</textarea>
          <label class="pa-label" for="d-day">Stuck to</label><input class="pa-field" type="date" id="d-day" value="${esc(n.day || store.dayKey(new Date(n.created)))}">
          <div class="pa-actions"><button class="pa-btn go" id="d-copy" type="button">Copy</button><button class="pa-btn" id="d-ask" type="button">Ask me about it</button></div>
          <button class="pa-btn danger" id="d-del" type="button">Remove note</button></div>`);
        const $ = (s) => body.querySelector(s);
        $("#d-text").addEventListener("change", (e) => save(() => e.target.value.trim() && store.notes.update(id, { text: e.target.value.trim() })));
        $("#d-day").addEventListener("change", (e) => save(() => e.target.value && store.notes.update(id, { day: e.target.value })));
        $("#d-copy").addEventListener("click", () => { navigator.clipboard && navigator.clipboard.writeText($("#d-text").value).then(() => ctx.toast("Copied.")).catch(() => ctx.toast("Couldn't copy.")); });
        $("#d-ask").addEventListener("click", () => { close(); ctx.say(`About my note "${n.text.slice(0, 300)}": what should I know or do?`); });
        $("#d-del").addEventListener("click", () => { store.notes.remove(id); close(); undo("Note", () => store.notes.restore(id)); });
      }
    });
    // a title field that grows with its text
    const grow = (ta) => { const fit = () => { ta.style.height = "auto"; ta.style.height = Math.max(ta.scrollHeight, 34) + "px"; }; fit(); requestAnimationFrame(() => requestAnimationFrame(fit)); setTimeout(fit, 450); };
    // a removed row slides away before it goes
    const leave = (btn, then) => {
      const li = btn.closest("li") || btn.closest(".pa-sticky"), sticky = li.classList.contains("pa-sticky");
      li.classList.add(sticky ? "peeling" : "leaving"); setTimeout(then, sticky ? 460 : 280);
    };

    // + : a sheet for what this tab holds
    const add = () => ctx.sheet((body, close) => {
      if (tab === "notes") {
        body.insertAdjacentHTML("beforeend", `<h2>New note</h2><form class="pa-sheet-form" id="f"><textarea class="pa-field" id="f-text" rows="4" maxlength="2000" placeholder="Write a note" aria-label="Note"></textarea>
          <label class="pa-label" for="f-day">Stick it to</label><input class="pa-field" type="date" id="f-day" value="${store.dayKey()}">
          <div class="pa-sheet-alt"><button type="button" class="pa-pill" data-d="0">Today</button><button type="button" class="pa-pill" data-d="1">Tomorrow</button><button type="button" class="pa-pill" data-d="7">In a week</button></div>
          <button class="pa-btn go" type="submit">Pin it</button></form>`);
        body.querySelectorAll("[data-d]").forEach((bt) => bt.addEventListener("click", () => { body.querySelector("#f-day").value = store.dayKey(new Date(Date.now() + Number(bt.dataset.d) * 86400000)); }));
        body.querySelector("#f").addEventListener("submit", (e) => {
          e.preventDefault();
          freshNext = true;
          if (store.notes.add(body.querySelector("#f-text").value, body.querySelector("#f-day").value)) { store.log.add("Saved a note"); close(); } else { freshNext = false; ctx.toast("Write something first."); }
        });
      } else if (tab === "tasks") {
        body.insertAdjacentHTML("beforeend", `<h2>New task</h2><form class="pa-sheet-form" id="f"><input class="pa-field" id="f-title" placeholder="What needs doing?" maxlength="200" aria-label="Task">
          <label class="pa-label" for="f-when">Time (optional): with one it goes to Today</label><input class="pa-field" type="datetime-local" id="f-when">
          <button class="pa-btn go" type="submit">Add task</button></form>`);
        body.querySelector("#f").addEventListener("submit", (e) => { e.preventDefault(); const t = store.tasks.add({ title: body.querySelector("#f-title").value, when: body.querySelector("#f-when").value }); if (t) { store.log.add(`Task: ${t.title}`); close(); if (t.when) ctx.toast("It has a time, so it is in Today."); } else ctx.toast("Type what needs doing."); });
      } else {
        body.insertAdjacentHTML("beforeend", `<h2>New event</h2><form class="pa-sheet-form" id="f"><input class="pa-field" id="f-title" placeholder="What is it?" maxlength="200" aria-label="Event">
          <label class="pa-label" for="f-start">Starts</label><input class="pa-field" type="datetime-local" id="f-start" value="${nextHour()}">
          <input class="pa-field" id="f-place" placeholder="Where (optional)" maxlength="100" aria-label="Place">
          <button class="pa-btn go" type="submit">Add to my calendar</button>
          <div class="pa-sheet-alt"><span>Or a focus sprint:</span>${[15, 25, 45].map((m) => `<button type="button" class="pa-pill" data-fm="${m}">${m} min</button>`).join("")}</div></form>`);
        body.querySelector("#f").addEventListener("submit", (e) => {
          e.preventDefault();
          const title = body.querySelector("#f-title").value.trim(), start = body.querySelector("#f-start").value;
          if (!title || !start) { ctx.toast("Give it a name and a time."); return; }
          const ev = store.events.add({ title, start, place: body.querySelector("#f-place").value.trim() });
          try { window.Native && window.Native.act("calendar", JSON.stringify({ title: ev.title, start: new Date(start).toISOString(), place: ev.place })); } catch { /* the event is still kept here */ }
          store.log.add(`Event: ${ev.title}`); close();
        });
        body.querySelectorAll("[data-fm]").forEach((b) => b.addEventListener("click", () => {
          const m = Number(b.dataset.fm); store.focus.start(m);
          try { window.Native && window.Native.act("timer", JSON.stringify({ seconds: m * 60, label: "Focus is over" })); } catch { /* the sprint still counts here */ }
          store.log.add(`Focus sprint, ${m} min`); close();
        }));
      }
    });

    draw();
    if (sub[0] === "add") setTimeout(add, 350);          // the launcher shortcut "Add task"
    off = store.onChange((what) => { if (["tasks", "events", "notes", "focus"].includes(what)) { el.querySelector("#t-body").innerHTML = tab === "tasks" ? tasks() : tab === "notes" ? notes() : agenda(); wire(); } });
    tick = setInterval(() => { const l = el.querySelector("#t-left"), f = store.focus.state(); if (l && f) l.textContent = clock((f.endsAt - Date.now()) / 1000); }, 1000);
  },
  destroy() { off && off(); clearInterval(tick); },
};
