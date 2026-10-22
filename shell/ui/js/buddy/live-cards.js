// The cards that show what is really happening: the PC's numbers, the job in
// progress, what is playing, the reminders. Each one fills an element and
// returns update(status), called with the backend's live state (/ui/live).
// Nothing here has example data: a card with nothing to show says so, and
// says how to make something appear. Styles: css/cards.css.
import { api, BASE } from "../api.js";
import { bubbleView, stageOf, paintAppIcon } from "./bubble.js";
import { pcTiles, musicView, upcoming, eventDays, whenText, monthGrid, clockAngles } from "./cards.js";

const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const TICKS = Array.from({ length: 12 }, (_, i) => `<line x1="30" y1="5" x2="30" y2="8" transform="rotate(${i * 30} 30 30)"/>`).join("");

// ---- the PC strip: one tile per number this PC really reports ----
export function pcStrip(el) {
  let shown = "";
  return {
    update(status) {
      const tiles = pcTiles(status && status.pc);
      const html = tiles.map((t) => `<span class="${t.bad ? "bad" : ""}">${esc(t.label)}<b>${esc(t.text)}</b></span>`).join("");
      if (html === shown) return;
      shown = html;
      el.hidden = !tiles.length;
      el.innerHTML = html;
    },
  };
}

// ---- the job in progress ----
export function workCard(el) {
  el.innerHTML = `<h3>Right now <span class="chip" data-p="chip">idle</span></h3>
    <div class="wk"><span class="appic" data-p="ic">M</span><div class="grow"><div class="wk-app" data-p="app">Nothing running</div><div class="wk-task" data-p="task">Ask me for something</div></div><b class="wk-pct" data-p="pct"></b></div>
    <div class="pipe" data-p="pipe"><span>Plan</span><span>Do</span><span>Check</span></div>
    <div class="think" data-p="think"></div><div class="prog" data-p="prog"><i></i></div>`;
  const p = Object.fromEntries([...el.querySelectorAll("[data-p]")].map((n) => [n.dataset.p, n]));
  let stage = 0, shownJob = null;
  return {
    update(status) {
      const job = status && status.job;
      if (job && job.id !== shownJob) { shownJob = job.id; stage = 0; }
      if (job && job.running) stage = stageOf(job.phase) ?? stage;
      const v = bubbleView(job, stage);
      el.classList.toggle("done", !!v && v.end === "done");
      el.classList.toggle("fail", !!v && v.end === "fail");
      if (!v) {
        p.ic.dataset.icon = ""; p.ic.textContent = "M"; p.ic.style.background = ""; p.ic.classList.remove("run", "real");
        p.chip.textContent = "idle"; p.chip.className = "chip";
        p.app.textContent = "Nothing running"; p.task.textContent = "Ask me for something"; p.pct.textContent = "";
        p.think.textContent = "When I work in an app or learn something, every step shows here.";
        p.think.classList.remove("typing");
        p.prog.className = "prog"; p.prog.firstElementChild.style.width = "0";
        [...p.pipe.children].forEach((s) => { s.className = ""; });
        return;
      }
      paintAppIcon(p.ic, v, BASE);
      p.ic.classList.toggle("run", v.running);
      p.chip.textContent = v.running ? "working" : v.end === "done" ? "done" : "stopped";
      p.chip.className = `chip${v.running ? " on" : v.end === "done" ? " ok" : ""}`;
      p.app.textContent = v.learning ? v.label : v.running ? `Working in ${v.label}` : v.label;
      p.task.textContent = v.title;
      p.pct.textContent = v.percent != null ? `${v.percent}%` : v.tries;
      p.pct.classList.toggle("small", v.percent == null);
      p.think.textContent = v.thought;
      p.think.classList.toggle("typing", v.typing);
      p.prog.className = `prog${v.shimmer ? " shimmer" : ""}`;
      p.prog.firstElementChild.style.width = `${v.fill}%`;
      [...p.pipe.children].forEach((s, i) => { s.className = i < v.stage ? "ok" : i === v.stage && v.running ? "on" : ""; });
    },
  };
}

// ---- what is playing, with the player's own buttons ----
export function musicCard(el) {
  el.innerHTML = `<h3>Now playing <span class="chip" data-p="chip" hidden></span></h3>
    <p class="none" data-p="none">Nothing is playing. Start music in any player, or ask me to play something, and it shows here.</p>
    <div class="music" data-p="body" hidden>
      <div class="deck"><div class="disc"><div class="vinyl" data-p="vinyl"><i></i></div><span class="arm" data-p="arm"></span></div>
        <div class="trk"><b data-p="title"></b><span data-p="artist"></span><div class="eqb" data-p="eq"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div></div></div>
      <div data-p="time"><div class="mp"><i data-p="bar"></i></div><div class="tt"><span data-p="now"></span><span data-p="all"></span></div></div>
      <div class="row2"><button type="button" class="btn" data-media="previous">Prev</button><button type="button" class="btn accent" data-media="toggle" data-p="play">Pause</button><button type="button" class="btn" data-media="next">Next</button></div>
      <p class="none err" data-p="err" hidden></p>
    </div>`;
  const p = Object.fromEntries([...el.querySelectorAll("[data-p]")].map((n) => [n.dataset.p, n]));
  const paint = (media) => {
    const v = musicView(media);
    p.none.hidden = !!v; p.body.hidden = !v; p.chip.hidden = !v;
    if (!v) return;
    p.chip.textContent = `${v.playing ? "playing" : "paused"}${v.app ? ` · ${v.app}` : ""}`;
    p.chip.className = `chip${v.playing ? " on" : ""}`;
    p.title.textContent = v.title;
    p.artist.textContent = v.artist;
    p.vinyl.classList.toggle("stop", !v.playing);
    p.arm.classList.toggle("off", !v.playing);
    p.eq.classList.toggle("stop", !v.playing);
    p.play.textContent = v.playing ? "Pause" : "Play";
    p.time.hidden = v.progress == null;  // the player does not say how long the track is: no made-up bar
    if (v.progress != null) { p.bar.style.width = `${v.progress}%`; p.now.textContent = v.now; p.all.textContent = v.total; }
  };
  el.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-media]");
    if (!b) return;
    b.setAttribute("aria-busy", "true");
    const r = await api.post("/ui/media", { command: b.dataset.media }).catch(() => null);
    b.removeAttribute("aria-busy");
    p.err.hidden = !!(r && r.ok);
    p.err.textContent = r && r.ok ? "" : (r && r.error) || "That didn't reach the player.";
    if (r && "media" in r) paint(r.media);
  });
  return { update(status) { paint(status && status.media); } };
}

// ---- reminders: the month, the clock at the next one, the list ----
const REFRESH_MS = 30_000;

export function remindersCard(el, { limit = 5 } = {}) {
  el.innerHTML = `<h3>Reminders <button type="button" class="btn small" data-p="add">Add a reminder</button></h3>
    <form class="frm" data-p="form" hidden>
      <input class="field" data-p="what" placeholder="What should I remind you of?" maxlength="200" aria-label="What to be reminded of">
      <input class="field" data-p="when" placeholder="When? e.g. tomorrow 9am, in 20 minutes" maxlength="80" aria-label="When">
      <button class="btn accent" type="submit">Add</button>
      <p class="none err" data-p="err" hidden></p>
    </form>
    <div class="calrow"><div><div class="calh" data-p="calh"></div><div class="cal" data-p="cal"></div></div>
      <svg class="clock" viewBox="0 0 60 60" role="img" data-p="clock"><circle cx="30" cy="30" r="27"/><g class="ticks">${TICKS}</g>
        <line class="hh" data-p="hh" x1="30" y1="30" x2="30" y2="16"/><line class="mh" data-p="mh" x1="30" y1="30" x2="30" y2="9"/><circle class="pin" cx="30" cy="30" r="2.4"/></svg>
      <div class="rems" data-p="list"></div></div>`;
  const p = Object.fromEntries([...el.querySelectorAll("[data-p]")].map((n) => [n.dataset.p, n]));
  let tasks = [], loadedAt = 0, loaded = false, firedSeen = "", calKey = "", listHtml = null, alive = true;

  function paint() {
    const now = new Date();
    const next = upcoming(tasks, now, limit);
    const at = next.length ? next[0].at : now;
    // The calendar and the clock follow the next reminder; with none, today and now.
    const grid = monthGrid(at, now), marked = eventDays(tasks, at);
    const key = `${grid.title}|${next.length ? next[0].task_id + next[0].due_date : ""}|${[...marked].join()}|${grid.today}`;
    if (key !== calKey) {  // redrawn (and its date splashes) only when something on it changed
      calKey = key;
      p.calh.textContent = grid.title;
      p.cal.innerHTML = ["M", "T", "W", "T", "F", "S", "S"].map((d) => `<span class="d">${d}</span>`).join("")
        + grid.cells.map((d) => `<span class="${!d ? "" : next.length && d === grid.hit ? "hit" : marked.has(d) ? "ev" : d === grid.today ? "today" : ""}">${d || ""}</span>`).join("");
    }
    const hands = clockAngles(at);
    p.hh.style.transform = `rotate(${hands.hour}deg)`;
    p.mh.style.transform = `rotate(${hands.minute}deg)`;
    p.clock.setAttribute("aria-label", next.length ? `Next reminder ${whenText(at, now)}` : "The time now");
    const html = !loaded ? `<p class="none">Loading…</p>`
      : next.length ? next.map((t) => `<div class="rem" data-id="${t.task_id}"><b>${esc(whenText(t.at, now))}</b><span>${esc(t.title)}</span>
          <button type="button" class="btn small accent" data-rem="done">Done</button><button type="button" class="btn small" data-rem="later">In 10 min</button></div>`).join("")
      : `<p class="none">No reminders yet. Say “remind me…” or add one here.</p>`;
    if (html !== listHtml) { listHtml = html; p.list.innerHTML = html; }  // live updates arrive often; the buttons must stay put
  }

  async function load() {
    loadedAt = Date.now();
    const r = await api.get("/ui/tasks").catch(() => null);
    if (!alive) return;
    if (r && r.ok) { tasks = r.tasks; loaded = true; }
    else if (!loaded) { p.list.innerHTML = `<p class="none err">I couldn't read the reminders.</p>`; return; }
    paint();
  }

  p.add.addEventListener("click", () => { p.form.hidden = !p.form.hidden; if (!p.form.hidden) p.what.focus(); });
  p.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = p.what.value.trim(), when = p.when.value.trim();
    const fail = (msg) => { p.err.hidden = false; p.err.textContent = msg; };
    if (!title) return fail("Say what to remind you of.");
    if (!when) return fail("Say when: a reminder needs a time.");
    const r = await api.post("/ui/tasks", { title, when }).catch(() => null);
    if (!r || !r.ok) return fail((r && r.error) || "I couldn't add that.");
    p.err.hidden = true; p.what.value = ""; p.when.value = ""; p.form.hidden = true;
    calKey = "";  // the new day splashes on the calendar
    load();
  });
  el.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-rem]");
    if (!b) return;
    const id = b.closest("[data-id]").dataset.id;
    b.setAttribute("aria-busy", "true");
    if (b.dataset.rem === "done") await api.post(`/ui/tasks/${id}/done`).catch(() => null);
    else await api.post(`/ui/tasks/${id}/snooze`, { minutes: 10 }).catch(() => null);
    load();
  });

  paint();
  load();
  return {
    update(status) {
      // A reminder that just fired, or a list that may be stale: read it again.
      const fired = ((status && status.fired_reminders) || []).map((r) => r.id).join();
      if (fired !== firedSeen || Date.now() - loadedAt > REFRESH_MS) { firedSeen = fired; load(); }
      else paint();
    },
    destroy() { alive = false; },
  };
}
