// What the notch should say and how it should feel, as plain functions of the live facts. No DOM, no clock of its own: the
// caller passes `now`, so every rule is tested with a fixed time (shell/ui/tests/notch-state.test.mjs).
//
// The contract: one mood at a time, and each mood has one look (the glow colour in notch.css, data-m) and at most one cue.
//   alert   a question with answers is waiting (an approval, a check-in)          red, rings
//   due     a deadline within two minutes, or a timer/reminder that is ringing     red, pulses
//   soon    a deadline within a quarter of an hour, or a message waiting           amber
//   listen  the buddy hears you                                                    cyan
//   think   the brain is working on your words                                     violet
//   work    a job in an app                                                        blue
//   done    a job just finished (nine seconds)                                     green, one check
//   hot     the PC is straining                                                    orange
//   near    a deadline within the hour                                             warm
//   focus   a focus sprint                                                         indigo
//   music   something is playing                                                   pink
//   ""      rest: nothing matters, so nothing moves
export const MOODS = ["alert", "due", "soon", "listen", "think", "work", "done", "hot", "near", "focus", "music", ""];
export const NEEDS_YOU = new Set(["alert", "due", "soon"]);

export const minutesUntil = (iso, now = Date.now()) => (new Date(iso).getTime() - now) / 60000;
export const inText = (m) => (m <= 0 ? "now" : m < 1 ? "under a minute" : m < 90 ? `in ${Math.round(m)} min` : `in ${Math.round(m / 60)} h`);

/** The nearest deadline: a timer running down, or the next reminder or task due (one that passed in the last hour still counts). */
export function deadline(overview, now = Date.now()) {
  let best = null;
  for (const t of (overview && overview.timers) || []) {
    const m = t.remaining_s / 60;
    if (!best || m < best.m) best = { m, title: t.label || "Timer" };
  }
  const r = overview && overview.next_reminder;
  if (r && r.due_date && !r.completed) {
    const m = minutesUntil(r.due_date, now);
    if (m > -60 && (!best || m < best.m)) best = { m, title: r.title };
  }
  return best && best.m <= 60 ? best : null;
}

/** The one mood for these facts. ctx: { status, reaction, attention, overview, doneUntil, now, glow } */
export function moodOf(ctx) {
  const s = ctx.status || {}, now = ctx.now ?? Date.now();
  const d = deadline(ctx.overview, now);
  if ((s.pending_action && s.pending_action.text) || s.checkin) return "alert";
  if (ctx.attention) return ctx.attention.kind === "message" ? "soon" : "due";   // a timer or reminder waiting for you: red and pulsing; a message: orange
  if (d && d.m <= 2) return "due";
  if (d && d.m <= 15) return "soon";
  if (ctx.reaction === "listen") return "listen";
  if (ctx.reaction === "think") return "think";
  if (s.job && s.job.running) return "work";
  if (now < (ctx.doneUntil || 0)) return "done";
  if (s.pc && s.pc.strain) return "hot";
  if (d) return "near";
  if (s.focus) return "focus";
  if (s.media && s.media.playing) return "music";
  return "";
}

/** "Glow: the character's colour" keeps only the moments that need you; the others stay in the character's colour. */
export function glowMood(mood, glow) {
  return glow === "character" && !["alert", "due"].includes(mood) ? "" : mood;
}

/** [word, extra] for the strip: the one word and what sits beside it (a time left), or "" to show the clock. */
export function wordOf(ctx, mood, musicTitle = "") {
  const s = ctx.status || {}, now = ctx.now ?? Date.now();
  const d = deadline(ctx.overview, now);
  const att = ctx.attention;
  if (att) return [String(att.who).slice(0, 26), "waiting"];      // the words themselves are on the card below, in full
  if (mood === "alert") return [s.checkin ? "Check-in" : "Needs you", ""];
  if (d && ["due", "soon", "near"].includes(mood)) return [String(d.title).slice(0, 26), inText(d.m)];
  if (ctx.reaction === "sleep") return ["Asleep", ""];
  if (mood === "listen") return ["Listening", ""];
  if (mood === "think") return ["Thinking", ""];
  if (mood === "work") return [String((s.job && (s.job.app || s.job.title)) || "Working").slice(0, 24), ""];
  if (s.speaking) return ["Speaking", ""];
  if (mood === "focus") return ["Focus", ""];
  if (mood === "music") return [musicTitle ? `♪ ${String(musicTitle).slice(0, 24)}` : "Playing", ""];
  if (s.mic_on === false) return ["Microphone off", ""];
  return ["Ready", ""];
}

/** A reward to celebrate once: the live feed's newest award when it is new since [seenId]. */
export function freshReward(status, seenId) {
  const r = status && status.reward;
  if (!r || typeof r.id !== "number") return null;
  if (seenId === null || seenId === undefined) return { first: true, id: r.id };   // the first sight is only remembered
  return r.id > seenId ? { first: false, id: r.id, xp: r.xp, label: r.label, kind: r.kind } : null;
}

/** The ring of the level chip: 0..1 progress through the current level. */
export const levelProgress = (r) => (r && r.per_level ? Math.max(0, Math.min(1, r.into_level / r.per_level)) : 0);
