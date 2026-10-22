// Search: one box over your tasks, events, notes and what we said, opened from the magnifier at the top. Nothing is indexed or sent
// anywhere: it looks through what is on this phone as you type.
import { esc } from "../util.js";

/** Matches of [q] in the store, grouped: [{ kind, label, route, rows: [{ text, sub }] }]. Every word has to appear (any order). */
export function searchAll(store, q) {
  const words = String(q || "").toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const hit = (s) => { const l = String(s || "").toLowerCase(); return words.every((w) => l.includes(w)); };
  const day = (s) => String(s || "").slice(0, 10);
  const groups = [
    { kind: "tasks", label: "Tasks", route: "#/do", rows: store.tasks.list().filter((t) => hit(t.title + " " + (t.notes || ""))).map((t) => ({ text: t.title, sub: (t.done ? "Done" : "To do") + (t.when ? " · " + day(t.when) : "") })) },
    { kind: "events", label: "Events", route: "#/today", rows: store.events.list().filter((e) => hit(e.title + " " + (e.place || ""))).map((e) => ({ text: e.title, sub: day(e.start) + (e.place ? " · " + e.place : "") })) },
    { kind: "notes", label: "Notes", route: "#/remember", rows: store.notes.list().filter((n) => hit(n.text)).map((n) => ({ text: n.text.slice(0, 140), sub: n.day || "" })) },
    { kind: "chat", label: "Conversation", route: "#/buddy", rows: store.convo.list(200).filter((c) => hit(c.you + " " + c.me)).reverse().slice(0, 20).map((c) => ({ text: c.you, sub: String(c.me || "").slice(0, 100) })) },
  ];
  return groups.filter((g) => g.rows.length);
}

export default {
  render(el, ctx) {
    el.innerHTML = `<header class="pa-head"><h1>Search</h1><p>Tasks, events, notes and what we said</p></header>
      <input class="pa-field" id="q" type="search" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Search" aria-label="Search">
      <div id="out"></div>`;
    const q = el.querySelector("#q"), out = el.querySelector("#out");
    const draw = () => {
      const g = searchAll(ctx.store, q.value);
      if (!q.value.trim()) { out.innerHTML = `<p class="pa-note">Type a word. It looks through everything on this phone.</p>`; return; }
      out.innerHTML = g.length
        ? g.map((x) => `<h3 class="pa-day">${x.label} · ${x.rows.length}</h3>${x.rows.map((r) => `<a class="pa-hit" href="${x.route}"><b>${esc(r.text)}</b>${r.sub ? `<span>${esc(r.sub)}</span>` : ""}</a>`).join("")}`).join("")
        : `<p class="pa-note">Nothing matches "${esc(q.value.trim())}".</p>`;
    };
    q.addEventListener("input", draw); draw(); setTimeout(() => q.focus(), 80);
  },
};
