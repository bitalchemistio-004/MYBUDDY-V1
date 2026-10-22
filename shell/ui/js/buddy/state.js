// What the drawn buddy should be showing, from GENERIC events. Pure: no DOM,
// no timers; the clock is injected so tests can drive it.
//
// Every clip answers a moment any task can raise (heard you, working,
// searching, finished, needs you, failed, thanks, idle, asleep). Nothing here
// is keyed to an app, a tool or a phrase: a document export and a web search
// end with the same "done".

// Loops. Several can be true at once (music on while work starts); the
// highest one shows. ACTIVITY loops say what the buddy itself is doing, and
// the live status sets exactly one of them. AMBIENT loops say what is going
// on around it and stay until they are switched off.
export const BASE_PRIORITY = { sleep: 0, idle: 1, music: 2, focus: 3, hot: 4, judge: 5, listen: 6, think: 7, search: 8, write: 9 };
export const AMBIENT = new Set(["music", "focus", "hot", "judge"]);

// One-shots play once over the loop. A higher one may cut in (after the
// minimum visible time); an equal or lower one waits its turn.
export const SHOT_PRIORITY = {
  bell: 10, alert: 9, shock: 8, furious: 7, oops: 6, done: 5, proud: 5,
  hello: 4, dizzy: 3, love: 2, wink: 2, bored: 1,
};

// Event names callers may use, mapped to the clip (or clips, in order) they
// mean. Clip names are accepted too, so the debug hook can show any clip.
export const EVENTS = {
  heard: "listen", listening: "listen", listen: "listen",
  working: "think", thinking: "think", think: "think",
  searching: "search", search: "search",
  writing: "write", write: "write",
  idle: "idle", awake: "idle",
  asleep: "sleep", sleep: "sleep",
  finished: "done", done: "done",
  goal: "proud", streak: "proud", proud: "proud",
  needs_you: "alert", confirmation: "alert", alert: "alert",
  reminder: ["bell", "alert"], bell: "bell",  // it rings, then it waits for you
  error: "oops", failed: "oops", oops: "oops",
  thanks: "love", click: "love", love: "love",
  surprise: "shock", sudden: "shock", shock: "shock",
  greet: "hello", returned: "hello", hello: "hello",
  ack: "wink", wink: "wink",
  chaos: "dizzy", dizzy: "dizzy",
  bored: "bored", furious: "furious",
  music_on: "music", music: "music",
  focus_on: "focus", focus: "focus",
  pc_strain: "hot", hot: "hot",
  checkin: "judge", judge: "judge",
};

// Events that switch an ambient loop off again.
export const OFF_EVENTS = { music_off: "music", focus_off: "focus", pc_ok: "hot", checkin_done: "judge" };

const MAX_QUEUE = 3;  // older moments are stale by the time they would play

export class BuddyState {
  constructor({ now = () => Date.now(), minVisible = 900, onChange = null,
    boredAfter = 8 * 60_000,                 // this long with nothing happening: one bored look
    pokes = 4, pokeWindow = 5000,            // this many clicks this fast is poking
    failures = 3, failureWindow = 5 * 60_000 // this many failures in a row is infuriating
  } = {}) {
    this.now = now;
    this.minVisible = minVisible;
    this.onChange = onChange;
    this.limits = { boredAfter, pokes, pokeWindow, failures, failureWindow };
    this.flags = new Set();    // active activity loops other than idle
    this.ambient = new Set();  // active ambient loops
    this.extra = new Map();    // clips the art has that this file does not name: name -> is it a loop
    this.shot = null;          // the one-shot on screen
    this.shotAt = 0;
    this.queue = [];
    this.quietSince = now();
    this.wasBored = false;
    this.pokeTimes = [];
    this.failTimes = [];
  }

  // The clips the loaded art has (from its manifest). One this file has no
  // event for can still be shown by its own name: a loop as an activity, the
  // rest as the lowest one-shot. New art never needs a change here to be seen.
  setClips(names, loops) {
    this.extra.clear();
    for (const n of names) if (!(n in BASE_PRIORITY) && !(n in SHOT_PRIORITY)) this.extra.set(n, loops.has(n));
  }

  basePriority(name) { return name in BASE_PRIORITY ? BASE_PRIORITY[name] : 1.5; }
  shotPriority(name) { return name in SHOT_PRIORITY ? SHOT_PRIORITY[name] : 0; }
  isBase(name) { return name in BASE_PRIORITY || this.extra.get(name) === true; }
  isShot(name) { return name in SHOT_PRIORITY || this.extra.get(name) === false; }

  get base() {
    if (this.flags.has("sleep")) return "sleep";  // asleep is asleep, whatever plays around it
    // Idle is the absence of any other state, so it is the fallback here.
    let best = null;
    for (const f of [...this.flags, ...this.ambient]) if (best === null || this.basePriority(f) > this.basePriority(best)) best = f;
    return best || "idle";
  }

  // The clip that should be on screen now.
  get clip() { return this.shot || this.base; }

  // Returns false for an unknown event, and changes nothing: a new backend
  // event name must never break the buddy.
  fire(event) {
    const name = String(event || "").toLowerCase();
    const before = this.clip;
    if (name in OFF_EVENTS) this.ambient.delete(OFF_EVENTS[name]);
    else {
      const clips = [].concat(EVENTS[name] || (this.extra.has(name) ? name : []));
      if (!clips.length) return false;
      for (const clip of clips) {
        if (this.isShot(clip)) this.addShot(this.escalate(name, clip));
        else if (AMBIENT.has(clip)) this.ambient.add(clip);
        else this.applyBase(clip);
      }
    }
    this.stir();
    this.changed(before);
    return true;
  }

  // Poked again and again, or failing again and again: it loses its temper.
  escalate(event, clip) {
    const t = this.now(), L = this.limits;
    const count = (times, window, limit) => {
      times.push(t);
      while (times.length && t - times[0] > window) times.shift();
      if (times.length < limit) return false;
      times.length = 0;
      return true;
    };
    if (event === "click" && count(this.pokeTimes, L.pokeWindow, L.pokes)) return "furious";
    if (clip === "oops" && count(this.failTimes, L.failureWindow, L.failures)) return "furious";
    if (clip === "done" || clip === "proud") this.failTimes.length = 0;  // a success ends the streak
    return clip;
  }

  // Show exactly this clip and nothing else (a preview): whatever was going
  // on is dropped. A one-shot plays and goes back to idle; a loop stays.
  show(name) {
    const before = this.clip;
    this.flags.clear(); this.ambient.clear(); this.queue.length = 0; this.shot = null;
    const known = this.isShot(name) || this.isBase(name);
    if (known && name !== "idle") {
      if (this.isShot(name)) this.showShot(name);
      else if (AMBIENT.has(name)) this.ambient.add(name);
      else this.flags.add(name);
    }
    this.stir();
    this.changed(before, true);
    return known;
  }

  // The live status knows the whole truth about what the buddy is doing, so
  // it sets exactly one activity. Ambient loops are left as they are.
  setBase(name) {
    if (!this.isBase(name) || AMBIENT.has(name)) return false;
    const before = this.clip;
    const was = this.flags.size ? [...this.flags].join() : "idle";
    this.flags.clear();
    if (name !== "idle") this.flags.add(name);
    if (was !== name) this.stir();
    this.changed(before);
    return true;
  }

  setAmbient(name, on) {
    if (!AMBIENT.has(name)) return false;
    const before = this.clip;
    if (on === this.ambient.has(name)) return true;
    if (on) this.ambient.add(name); else this.ambient.delete(name);
    this.stir();
    this.changed(before);
    return true;
  }

  applyBase(clip) {
    if (clip === "idle") this.flags.clear();
    else if (clip === "sleep") { this.flags.clear(); this.flags.add("sleep"); }
    else { this.flags.delete("sleep"); this.flags.add(clip); }
  }

  addShot(clip) {
    // Finishing or failing ends the work that led to it.
    if (clip === "done" || clip === "oops") for (const f of [...this.flags]) if (f !== "sleep") this.flags.delete(f);
    if (!this.shot) return this.showShot(clip);
    if (clip === this.shot || this.queue.includes(clip)) return;
    this.queue.push(clip);
    this.queue.sort((a, b) => this.shotPriority(b) - this.shotPriority(a));  // stable: equal ones keep arrival order
    this.queue.length = Math.min(this.queue.length, MAX_QUEUE);
    this.tryCutIn();
  }

  showShot(clip) {
    this.shot = clip;
    this.shotAt = this.now();
  }

  // A waiting one-shot that outranks the one on screen replaces it, but only
  // once the current one has been seen for the minimum time.
  tryCutIn() {
    const next = this.queue[0];
    if (!next || !this.shot) return false;
    if (this.shotPriority(next) <= this.shotPriority(this.shot)) return false;
    if (this.now() - this.shotAt < this.minVisible) return false;
    this.showShot(this.queue.shift());
    return true;
  }

  // Something happened: the quiet stretch that leads to boredom starts over.
  stir() {
    this.quietSince = this.now();
    this.wasBored = false;
  }

  // Called every animation frame so a queued cut-in happens on time, and so
  // a long stretch of plain idling gets one bored look (one, not a loop).
  tick() {
    const before = this.clip;
    if (this.tryCutIn()) return this.changed(before);
    if (before === "idle" && !this.wasBored && this.now() - this.quietSince >= this.limits.boredAfter) {
      this.wasBored = true;
      this.showShot("bored");
      this.changed(before);
    }
  }

  // The player finished a one-shot: play the next waiting one, or go back
  // to the loop. An end report for a clip no longer on screen is ignored.
  clipEnded(name) {
    if (name !== this.shot) return;
    const before = this.clip;
    this.shot = null;
    if (this.queue.length) this.showShot(this.queue.shift());
    this.changed(before, true);
  }

  changed(before, force = false) {
    if ((force || this.clip !== before) && this.onChange) this.onChange(this.clip, this);
  }
}

// ---- the backend status of today, mapped onto the generic events ----
// reactionFor(status) yields: sleep, listen, think, idle, happy, sad,
// worried, surprised, wave (the last five while speaking), plus the window's
// own alert and celebrate for reminders and timers.

const REACTION_BASE = { sleep: "sleep", listen: "listen", think: "think" };

// Only real moments become one-shots. A sad or worried tone in a reply is
// not a failed task, so it does not play "oops".
const REACTION_SHOT = { happy: "thanks", surprised: "surprise", celebrate: "finished", alert: "needs_you" };

export function baseForReaction(reaction) {
  return REACTION_BASE[reaction] || "idle";
}

export function shotForReaction(reaction) {
  return REACTION_SHOT[reaction] || null;
}

// A background job (core/jobs.py) that is running is work, even when nobody
// is talking to the buddy and even when it is "asleep": it looks something
// up, writes, or plainly works, as the job says. Hearing the user comes first.
const WORK_ACT = { search: "search", write: "write" };

export function baseForWork(reaction, job) {
  const base = baseForReaction(reaction);
  if (!job || !job.running || base === "listen") return base;
  return WORK_ACT[job.act] || "think";
}

// Apply one status reaction. The one-shot plays only on the change into it,
// not on every repeated status update. Waking up is the user coming back:
// it says hello.
export function applyReaction(state, reaction, previous, job = null) {
  state.setBase(baseForWork(reaction, job));
  if (previous === "sleep" && reaction !== "sleep") state.fire("returned");
  const shot = shotForReaction(reaction);
  if (shot && reaction !== previous) state.fire(shot);
}

// A job's ending, once: done or oops. `seen` is the id -> "running" | "ended"
// memory the caller keeps. A job first seen already ended is old news.
export function applyJob(state, job, seen) {
  if (!job) return;
  const was = seen.get(job.id);
  seen.set(job.id, job.running ? "running" : "ended");
  if (was === "running" && !job.running) state.fire(job.ok ? "finished" : "failed");
}
