// The status bubble beside the buddy: what it is working on right now, in
// which app, what it is doing at this moment and how far it is. It shows
// while the panel is closed, so work in the background is never invisible.
//
// Everything in it comes from the job the backend reports (core/jobs.py via
// /ui/live). Nothing is made up here: no percent unless the job knows its
// progress, and the ending text is the job's own outcome.

// ---- pure part (tested in Node) ----

// Plan / Do / Check: which of the three segments a phase belongs to.
const STAGE = { requested: 0, planning: 0, executing: 1, observing: 2, evaluating: 2 };
export const HOLD_DONE_MS = 12000;    // how long a finished job stays before it fades
export const HOLD_FAILED_MS = 16000; // a failure is read more slowly

export function stageOf(phase) {
  return phase in STAGE ? STAGE[phase] : null;
}

// What the bubble shows for a job. `lastStage` is the segment the job was in
// when it was last seen running, so a failure shows how far it got.
export function bubbleView(job, lastStage = 0) {
  if (!job) return null;
  const learning = job.kind === "learn";
  const running = !!job.running;
  const end = running ? "" : job.ok ? "done" : "fail";
  const stage = running ? (stageOf(job.phase) ?? lastStage) : end === "done" ? 3 : lastStage;
  const known = typeof job.progress === "number";
  const tries = running && job.step > 1 ? `try ${job.step}${job.total ? `/${job.total}` : ""}` : "";
  return {
    id: job.id,
    subject: job.subject,
    // The heading says where things stand, so an ended job never reads as still going.
    // The app's name matters more than a verb (a long heading was cut off
    // mid-name); the ring and the typing dots already say it is working.
    label: learning ? (running ? "Learning" : end === "done" ? "Learned" : "Couldn't learn")
      : running ? job.subject : `${end === "done" ? "Done" : "Stopped"} \u00b7 ${job.subject}`,
    title: learning ? job.subject : job.task,
    letter: learning ? "" : (String(job.subject || "?").trim()[0] || "?").toUpperCase(),
    icon: job.icon || "",
    learning,
    running,
    end,
    // While it runs: what it is doing now. When it ended: how it ended, in its own words.
    thought: running ? (job.doing || "Getting started") : (job.outcome || (end === "done" ? "Done." : "It didn't work.")),
    typing: running,
    percent: known ? (end === "done" ? 100 : Math.round(job.progress * 100)) : null,
    // No known progress: the bar shimmers instead of showing an invented number.
    shimmer: running && !known,
    fill: end ? 100 : known ? Math.round(job.progress * 100) : 0,
    tries,
    stage,
  };
}

// Whether the bubble is on screen. A job that ended stays for a moment and
// then fades; `endedSeenAt` is when this window first saw it ended.
export function bubbleVisible(view, endedSeenAt, now) {
  if (!view) return false;
  if (view.running) return true;
  if (endedSeenAt == null) return false;  // it ended before this window opened: old news
  return now - endedSeenAt < (view.end === "done" ? HOLD_DONE_MS : HOLD_FAILED_MS);
}

// A steady colour for an app's letter tile, from its name.
export function tileColour(name) {
  let h = 0;
  for (const ch of String(name || "")) h = (h * 31 + ch.codePointAt(0)) % 360;
  return `hsl(${h} 32% 42%)`;
}

// ---- browser part ----

const BOOK = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 5a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2zM4 20a2 2 0 0 0 2 2h13M9 8h6"/></svg>';

// The app's real icon in a tile; a letter on a coloured tile until it loads,
// and for good when there is none. Learning shows a book. Shared by the
// bubble and the panel's work card.
export function paintAppIcon(tile, view, iconBase = "") {
  const key = view.learning ? "learn" : `${view.icon}|${view.letter}|${view.subject}`;
  if (tile.dataset.icon === key) return;
  tile.dataset.icon = key;
  tile.classList.remove("real");
  tile.style.background = "";
  if (view.learning) { tile.innerHTML = BOOK; return; }
  tile.textContent = view.letter;
  tile.style.background = tileColour(view.subject);
  if (!view.icon) return;
  const img = new Image();
  img.alt = "";
  img.draggable = false;
  img.onload = () => {
    if (tile.dataset.icon !== key) return;  // another job took over while it loaded
    tile.textContent = "";
    tile.style.background = "transparent";
    tile.classList.add("real");
    tile.append(img);
  };
  img.src = iconBase + view.icon;
}

export class StatusBubble {
  constructor(el, { iconBase = "" } = {}) {
    this.el = el;
    this.iconBase = iconBase;
    el.innerHTML = `<div class="bh"><span class="bi"></span><div class="bt"><small></small><b></b></div><span class="bp"></span></div>
<div class="bth"></div><div class="bb"><i></i></div><div class="bs"><span></span><span></span><span></span></div>`;
    this.parts = {
      icon: el.querySelector(".bi"), label: el.querySelector(".bt small"), title: el.querySelector(".bt b"),
      side: el.querySelector(".bp"), thought: el.querySelector(".bth"), bar: el.querySelector(".bb i"),
      steps: [...el.querySelectorAll(".bs span")],
    };
  }

  // Whether it is on screen (the window shows and hides it; see buddy.js reveal()).
  get visible() { return !this.el.hidden && this.el.classList.contains("show"); }

  // Put a job's view into the bubble. Showing and hiding is the caller's.
  fill(view) {
    const p = this.parts;
    this.setIcon(view);
    p.icon.classList.toggle("run", view.running);
    p.label.textContent = view.label;
    p.title.textContent = view.title;
    p.side.textContent = view.percent != null ? `${view.percent}%` : view.tries;
    p.side.classList.toggle("small", view.percent == null);
    p.thought.textContent = view.thought;
    p.thought.classList.toggle("typing", view.typing);
    p.bar.style.width = `${view.fill}%`;
    p.bar.parentElement.classList.toggle("shimmer", view.shimmer);
    p.steps.forEach((s, i) => { s.className = i < view.stage ? "ok" : i === view.stage && view.running ? "on" : ""; });
    this.el.classList.toggle("done", view.end === "done");
    this.el.classList.toggle("fail", view.end === "fail");
  }

  setIcon(view) { paintAppIcon(this.parts.icon, view, this.iconBase); }
}
