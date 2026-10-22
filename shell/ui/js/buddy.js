// The floating buddy: the chosen character, alone on the desktop, reacting
// to what MBUDDY is really doing. What it says and what it is working on
// extend out beside it and retract. The window is kept exactly as large as
// what is drawn (fit()), anchored at the character's own corner, so the
// character never moves when a bubble comes or goes.
import { api, BASE, statusStream, reactionFor, displayText } from "./api.js";
import { freshReward } from "./buddy/notch-state.js";
import { createSpriteBuddy, fitSize, castFor, reducedMotionWanted } from "./buddy/sprite.js";
import { applyReaction, applyJob } from "./buddy/state.js";
import { StatusBubble, bubbleView, bubbleVisible, stageOf } from "./buddy/bubble.js";
import { Aura } from "./buddy/aura.js";
import { attachDrag } from "./buddy/motion.js";
import { contentSize, PAD } from "./buddy/fit.js";
import { createNotch } from "./buddy/notch.js";
import * as wardrobe from "./buddy/wardrobe.js";

// ?embed: this page is the live preview inside Settings: the real notch, with no window of its own to place or hide
const EMBED = new URLSearchParams(location.search).has("embed");
const tauri = EMBED ? null : window.__TAURI__;
const invoke = (cmd, args) => (tauri ? tauri.core.invoke(cmd, args) : Promise.resolve(null));
const win = tauri && tauri.window ? tauri.window.getCurrentWindow() : null;
const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);

// How large the 320x240 art is shown, per size setting.
const SPRITE_SCALE = { small: 0.4, medium: 0.5, large: 0.625 };
const SPRITE_FRAME = { w: 320, h: 240 };  // until the clips have loaded
let sprite = null, lastReaction = null, greeted = false, hadAsk = false, aura = null;
// Effects around the buddy: on unless switched off, and never with reduced motion.
const effectsWanted = () => !!settings && settings.buddy_effects !== false && !reducedMotionWanted(settings) && !notch.active;  // the notch has its own glow

let settings = null, status = null, placed = "", lastKey = "";
let mainOpen = null, lastShould = null;      // the app-open rule (watchMain)
// The notch hangs from the top edge and is centred ("top"); the other styles stand in a corner of the screen.
const geoSide = () => (settings && settings.popup_style === "notch" ? "top" : settings.buddy_side);
let notch = null;
let bubbleUntil = 0, bubbleSince = 0, bubble = { who: "", text: "" }, ringUntil = 0;
// While the status bubble is up, the words make way sooner (live, one reply
// sat there for a whole job).
const SPEECH_WHILE_WORKING_MS = 6000, SPOKEN_WHILE_WORKING_MS = 20000;
const seen = new Set();
let first = true;
// The status bubble: the background job, from /ui/live.
const work = new StatusBubble($("#bub"), { iconBase: BASE });
const jobSeen = new Map();   // job id -> "running" | "ended", so an ending plays once
let jobStage = 0, jobEndedAt = null, jobShown = null;
let debugJob;  // __buddyDebug.job({...}): look at the bubble without a real job (preview only)
const currentJob = () => (debugJob !== undefined ? debugJob : status && status.job) || null;

function applySettings(s) {
  settings = s;
  document.documentElement.dataset.theme = s.theme === "character" ? s.character : "clay";
  document.documentElement.dataset.motion = s.reduced_motion ? "reduced" : "full";
  document.body.dataset.c = castFor(s.character);  // the bubbles wear the character's colours
  const b = document.body.classList;
  b.remove("corner", "edge", "dock", "left", "right", "small", "medium", "large");
  b.add(s.popup_style === "notch" ? "dock" : s.popup_style, s.buddy_side, s.buddy_size);   // "notch" is the shape's own class, so the body says "dock"
  notch.mount(s.popup_style === "notch");
  notch.configure(s.notch);
  sizeSprite();
  if (sprite) sprite.setReducedMotion(reducedMotionWanted(s));
  if (aura) { aura.enable(effectsWanted()); if (sprite) aura.show(sprite.player.name); }
  // A different character: load its clips (the state carries over).
  if (sprite && sprite.character !== castFor(s.character)) mountSprite().catch((e) => console.error("character did not load", e));
  const where = s.show_buddy ? `${s.popup_style}:${s.buddy_side}` : "hidden";
  if (where !== placed) {
    placed = where;
    if (!s.show_buddy) { if (win) win.hide(); }
    else {
      // Where it was last dragged to, when that was in this style and on this side.
      const at = s.buddy_position && s.buddy_position.for === where ? s.buddy_position : null;
      const size = contentSize(document.body, geoSide());
      fitted = size;
      invoke("buddy_place", { style: s.popup_style, side: geoSide(), w: size.w, h: size.h, ax: at ? at.x : null, ay: at ? at.y : null })
        .then(() => { updateHot(); lastShould = null; mainOpen = null; }).catch(() => {});      // placing it shows it: the app-open rule is applied again
    }
  }
  lastKey = "";
  render();
  fit();
}

// ---------- the window is exactly what is drawn ----------

let fitted = { w: 0, h: 0 }, shrinkTimer = 0;

// Growing happens at once (the bubble then extends inside the larger window);
// shrinking waits until the bubble has retracted. Several changes in a row
// make one resize.
function fit() {
  if (!settings || !settings.show_buddy) return;
  const size = contentSize(document.body, geoSide());
  if (size.w === fitted.w && size.h === fitted.h) return;
  const grows = size.w > fitted.w || size.h > fitted.h;
  clearTimeout(shrinkTimer);
  const apply = () => {
    const now = contentSize(document.body, geoSide());
    if (now.w === fitted.w && now.h === fitted.h) return;
    fitted = now;
    invoke("buddy_fit", { side: geoSide(), w: now.w, h: now.h }).then(updateHot).catch(() => {});
  };
  if (grows) apply(); else shrinkTimer = setTimeout(apply, 60);
}

// Show or hide one of the things beside the character. Shown: it takes its
// place first (the window grows), then extends. Hidden: it retracts, then
// gives its place back (the window shrinks).
const RETRACT_MS = 340;
function reveal(el, on) {
  if (on === el.classList.contains("show") && on === !el.hidden) return;
  clearTimeout(el._hide);
  if (on) {
    el.hidden = false;
    fit();
    void el.offsetWidth;  // so the transition starts from the retracted state
    el.classList.add("show");
  } else {
    el.classList.remove("show");
    el._hide = setTimeout(() => { el.hidden = true; tail(); fit(); }, reducedMotionWanted(settings) ? 0 : RETRACT_MS);
  }
  tail();
}

// The tail points at the character from the lowest bubble showing.
const BUBBLES = () => [$("#bubble"), $("#act"), $("#bub")];
function tail() {
  const shown = BUBBLES().filter((el) => !el.hidden);
  for (const el of BUBBLES()) el.classList.toggle("tail", el === shown[shown.length - 1]);
}

// Where clicks should land; everywhere else passes through to the desktop.
function updateHot() {
  const els = notch.active ? notch.hotEls() : [$("#stage"), ...BUBBLES().filter((el) => !el.hidden)];
  const rects = els.map((e) => e.getBoundingClientRect()).filter((r) => r.width > 0);
  if (!rects.length) return;
  const x = Math.max(0, Math.min(...rects.map((r) => r.left)));
  const y = Math.max(0, Math.min(...rects.map((r) => r.top)));
  const w = Math.min(innerWidth, Math.max(...rects.map((r) => r.right))) - x;
  const h = Math.min(innerHeight, Math.max(...rects.map((r) => r.bottom))) - y;
  invoke("buddy_hot", { x, y, w, h }).catch(() => {});
}

// ---------- what it says ----------

function say(who, text, ms, ring = false) {
  text = displayText(text);
  if (!text) return;
  ms = Math.min(25000, Math.max(ms, 7000 + 60 * text.length));   // long enough to be read, whatever the caller asked
  bubble = { who, text };
  bubbleSince = Date.now();
  bubbleUntil = bubbleSince + ms;
  if (ring) ringUntil = bubbleSince + 2600;
  render();
}

function render() {
  if (!settings || !settings.show_buddy) return;
  const now = Date.now();
  const reaction = reactionFor(status);
  const speaking = !!(status && status.speaking);
  const busy = work.visible;
  const talking = busy ? (now < Math.min(bubbleUntil, bubbleSince + SPEECH_WHILE_WORKING_MS) || (speaking && now < bubbleSince + SPOKEN_WHILE_WORKING_MS))
    : (speaking && !!bubble.text) || now < bubbleUntil;
  const listening = reaction === "listen" && !talking && !busy;
  const thinking = reaction === "think" && !talking && !busy;
  const key = [reaction, talking, bubble.who, bubble.text, listening, thinking, now < ringUntil].join("|");
  if (key === lastKey) return;  // nothing visible changed
  lastKey = key;
  const b = document.body.classList;
  b.toggle("asleep", reaction === "sleep");
  b.toggle("listening", listening);
  b.toggle("thinking", thinking);
  $("#bubble-who").textContent = bubble.who;
  $("#bubble-text").textContent = bubble.text;
  $("#bubble").classList.toggle("ring", now < ringUntil);
  reveal($("#bubble"), talking);
  reveal($("#cap"), listening || thinking);
  fit();  // the words may have changed the bubble's size
  setTimeout(updateHot, RETRACT_MS + 40);
}

function onStatus(s) {
  const prev = status;
  status = s;
  if (settings && wardrobe.apply(s.wardrobe)) sizeSprite();   // a crown widens the notch's crop of the character
  if (s.wardrobe && s.wardrobe.notch) { notch.decorate(s.wardrobe.notch); notch.setPreview(!!s.wardrobe.preview); }
  // The sound bars follow the voice level; a CSS variable only, no redraw.
  const lvl = s && s.listening ? Math.min(1, Math.max(0, ((s.level || 0) - 0.004) / 0.05)) : 0;
  document.body.style.setProperty("--lvl", lvl.toFixed(2));
  if (s.last_reply && (!prev || s.last_reply !== prev.last_reply)) say("MBUDDY", s.last_reply, 9000);
  spriteFollow();
  // A thought from the inner life: a quiet bubble, never spoken while you're away.
  const t = s.inner_thought;
  if (t && t.at && (!prev || !prev.inner_thought || t.at !== prev.inner_thought.at)
      && Date.now() / 1000 - t.at < 600) say("A thought", t.text, 12000);
  for (const r of s.fired_reminders || []) {
    if (seen.has(r.id)) continue;
    seen.add(r.id);
    if (first) continue;  // announced before this window opened
    // A background job's own announcement: its ending already played (applyJob), so only the words.
    if (r.source === "learned") { say("MBUDDY", r.title, 10000); continue; }
    if (sprite) sprite.fire(r.source === "timer" ? "finished" : "reminder");  // a reminder rings, then waits for you
    // A task's reminder can be answered right here; a timer or any other reminder stays on the notch, ringing now and
    // then, until you answer it (it is never a flash that is gone before you look up).
    const task = /^task:(\d+)$/.exec(String(r.id));
    if (task) ask({ key: `reminder:${r.id}:${r.title}`, who: "Reminder", text: r.title, ms: 0, kind: "reminder", buttons: [
      { label: "Done", run: async () => { await api.post(`/ui/tasks/${task[1]}/done`); if (sprite) sprite.fire("finished"); } },
      { label: "In 10 min", run: () => api.post(`/ui/tasks/${task[1]}/snooze`, { minutes: 10 }) }] });
    else if (r.source === "timer") ask({ key: `timer:${r.id}`, who: "Timer", text: r.title, ms: 0, kind: "timer", buttons: [
      { label: "OK", run: async () => {} },
      { label: "5 more min", run: () => api.post("/control/chat", { text: "start a 5 minute timer", speak: false }) }] });
    else ask({ key: `reminder:${r.id}:${r.title}`, who: "Reminder", text: r.title, ms: 0, kind: "reminder", buttons: [{ label: "OK", run: async () => {} }] });
  }
  first = false;
  followJob();
  followAsks();
  render();
  notch.onStatus();
  celebrateProgress(s);
}

// ---------- progress ----------
// A task finished, a focus sprint done, a habit kept: the newest award comes in the live status once. The character plays its own
// "proud" clip (real art, not a stand-in) and the progress shows in the notch, or as a short bubble in the corner style.
let rewardSeen = null;
function celebrateProgress(s) {
  if (notch.active) {
    const got = notch.celebrate(s);
    if (got && sprite) sprite.fire(got.kind === "focus_sprint" ? "goal" : "proud");
    return;
  }
  const fr = freshReward(s, rewardSeen);
  if (!fr) return;
  rewardSeen = fr.id;
  if (fr.first) return;
  if (sprite) sprite.fire(fr.kind === "focus_sprint" ? "goal" : "proud");
  say("Progress", `+${fr.xp} xp · ${fr.label || "Done"}`, 4000);
}

// ---------- what it asks ----------

// A question with its answers, beside the character: a reminder whose time
// has come (Done / In 10 min), a check-in (Working / On a break / Busted), a
// nudge during a focus sprint. One at a time; `key` says which, so the same
// one is not shown twice. It goes when answered or when its time is up.
let asking = null;
const askedKeys = new Set(), askQueue = [];
const ALERT_REPEAT_MS = 7000, MESSAGE_REPEAT_MS = 30_000, ALERT_GIVE_UP_MS = 15 * 60_000;   // a timer or reminder rings again every few seconds, for up to 15 minutes

function ask(item) {
  const { key } = item;
  if (askedKeys.has(key)) return;
  askedKeys.add(key);
  if (asking && asking.key !== key) { askQueue.push(item); return; }      // one at a time; the next waits its turn
  showAsk(item);
}

function showAsk({ key, who, text, buttons, ms = 0, kind = "", onShow }) {
  if (onShow) onShow();
  asking = { key, kind, who, text, since: Date.now(), poked: Date.now(), until: ms ? Date.now() + ms : 0 };
  $("#act-who").textContent = who;
  $("#act-text").textContent = text;
  const row = $("#act-buttons");
  row.replaceChildren(...buttons.map((b, i) => {
    const el = document.createElement("button");
    el.type = "button";
    el.textContent = b.label;
    if (i === 0) el.className = "go";
    el.addEventListener("click", async () => {
      row.querySelectorAll("button").forEach((x) => { x.disabled = true; });
      try { await b.run(); } catch (e) { console.error("the answer did not go through", e); }
      endAsk(key);
    });
    return el;
  }));
  reveal($("#act"), true);
}

function endAsk(key) {
  if (!asking || asking.key !== key) return;
  asking = null;
  const next = askQueue.shift();
  if (next) { setTimeout(() => showAsk(next), reducedMotionWanted(settings) ? 0 : RETRACT_MS + 60); }
  reveal($("#act"), false);
}

// A timer or a reminder that is waiting for you keeps ringing: the character moves again every few seconds, and
// after fifteen minutes it gives up quietly.
function keepRinging() {
  if (!asking || !asking.kind) return;
  const now = Date.now();
  if (now - asking.since > ALERT_GIVE_UP_MS) { endAsk(asking.key); return; }
  if (now - asking.poked >= (asking.kind === "message" ? MESSAGE_REPEAT_MS : ALERT_REPEAT_MS)) { asking.poked = now; if (sprite) sprite.fire(asking.kind === "timer" ? "finished" : "reminder"); }
}

// From the live state: a check-in waiting for an answer, a nudge, a sprint
// that was just completed.
let nudgeSeen = null, sprintCheered = null;
function followAsks() {
  const c = status && status.checkin;
  if (c && c.id != null) {
    const answer = (value, event) => async () => {
      const r = await api.post("/ui/activity/answer", { id: c.id, answer: value });
      if (r && r.ok && event && sprite) sprite.fire(event);
    };
    ask({ key: `checkin:${c.id}`, who: "Check-in", text: c.question || `${c.minutes} minutes on ${c.app}. Working, or wandering?`,
      buttons: [{ label: "Working", run: answer("work", "ack") }, { label: "On a break", run: answer("break", null) }, { label: "Busted", run: answer("off", "furious") }] });
  } else if (asking && asking.key.startsWith("checkin:")) endAsk(asking.key);  // answered somewhere else, or withdrawn

  const n = status && status.focus_nudge;
  const nudgeKey = n ? `nudge:${n.key}` : null;
  if (n && nudgeKey !== nudgeSeen) {
    askedKeys.delete(nudgeKey);  // the same program may earn another nudge later
    ask({ key: nudgeKey, who: "Focus", text: `${n.app} is off work. Back to it?`, buttons: [{ label: "OK", run: async () => {} }], ms: 12000 });
  }
  nudgeSeen = nudgeKey;

  // A music request found no app chosen: offer the choice (it opens Apps I can use).
  const need = status && status.needs_choice;
  if (need && need.role) {
    ask({ key: `choice:${need.role}`, who: need.label || "Choose an app", text: `Which app should I use for ${String(need.label || need.role).toLowerCase()}? You choose, I won't guess.`,
      buttons: [{ label: "Choose an app", run: async () => { await invoke("open_main"); const t = window.__TAURI__; if (t && t.event) await t.event.emit("mbuddy-goto", { hash: "#/extensions" }); } },
                { label: "Later", run: async () => {} }] });
  } else if (asking && asking.key.startsWith("choice:")) endAsk(asking.key);  // an app was chosen

  // A message that has waited long enough (core/notif_watch.py): who wrote, in which app, with what to do about it.
  // It stays until you answer it; it is counted as shown once, so the next reminder comes after the repeat time.
  const ma = status && status.message_alert;
  if (ma) {
    const id = ma.id, say = (action) => () => api.post("/ui/notifications", { action, id });
    ask({ key: `msg:${id}:${ma.alerts}`, who: `${ma.app}${ma.important ? " · important" : ""}`, text: `${ma.who}: ${ma.text}`.slice(0, 160), ms: 0, kind: "message",
      onShow: () => { api.post("/ui/notifications", { action: "shown", id }).catch(() => {}); if (sprite) sprite.fire("reminder"); },
      buttons: [{ label: `Open ${ma.app}`, run: say("open") }, { label: "In 10 min", run: say("snooze") }, { label: "I saw it", run: say("seen") }, { label: "Mute chat", run: say("mute") }] });
  }

  // A PC action waiting for a yes, in the notch: a question with its two answers (the quick panel does this in the other styles).
  const pa = status && status.pending_action && status.pending_action.text;
  if (notch.active && pa) {
    ask({ key: `pending:${pa}`, who: "Needs your OK", text: `${pa.charAt(0).toUpperCase()}${pa.slice(1)}?`, buttons: [
      { label: "Yes, do it", run: () => api.post("/ui/pending-action", { decision: "yes" }) }, { label: "No", run: () => api.post("/ui/pending-action", { decision: "no" }) }] });
  } else if (asking && asking.key.startsWith("pending:")) endAsk(asking.key);

  const done = status && status.focus_done;
  if (done && sprite && sprintCheered !== (done.id ?? done.at)) { sprintCheered = done.id ?? done.at; sprite.fire("goal"); }
  if (asking && asking.until && Date.now() > asking.until) endAsk(asking.key);
}

// ---------- what it is working on ----------

// The job beside the character: shown while it runs, its ending held for a
// few seconds, then gone. Nothing is shown for a job that had already ended
// when this window opened.
function followJob() {
  const job = currentJob();
  if (!job) { jobShown = null; jobEndedAt = null; return showWork(null); }
  if (jobShown !== job.id) { jobShown = job.id; jobStage = 0; jobEndedAt = null; }
  if (job.running) jobStage = stageOf(job.phase) ?? jobStage;
  else if (jobEndedAt == null && jobSeen.get(job.id) === "running") jobEndedAt = Date.now();
  if (sprite) applyJob(sprite.state, job, jobSeen);
  else jobSeen.set(job.id, job.running ? "running" : "ended");
  showWork(bubbleView(job, jobStage));
}

function showWork(view) {
  const on = bubbleVisible(view, jobEndedAt, Date.now());
  if (on) work.fill(view);
  reveal($("#bub"), on);
  if (on) fit();  // its text may have changed its height
}

// Click: the quick panel next to the buddy; the full app is one click away inside it.
const openPanel = () => notch.active ? notch.toggleRoom() : settings && invoke("open_panel", { side: settings.buddy_side }).catch(() => invoke("open_main").catch(() => {}));
$("#bub").addEventListener("click", openPanel);
$("#bubble").addEventListener("click", openPanel);
setInterval(() => { spriteFollow(); render(); followJob(); followAsks(); keepRinging(); notch.tick(); }, 1000);  // lets the words and an ended job's bubble expire (cheap: skips when unchanged)

// ---------- the drawn character ----------

function sizeSprite() {
  const frame = sprite ? { w: sprite.sheet.width, h: sprite.sheet.height } : SPRITE_FRAME;
  // In the notch the character is cropped to its face (buddy/notch.js); elsewhere it is shown whole at the chosen size.
  const size = notch.active ? notch.stageSize(castFor(settings.character)) : fitSize(frame, SPRITE_SCALE[settings.buddy_size] || 0.5);
  document.body.style.setProperty("--sprite-w", `${size.w}px`);
  document.body.style.setProperty("--sprite-h", `${size.h}px`);
  if (aura) aura.resize(size, PAD);
}

// The live status sets the base loop; a change into a real moment (a timer
// done, a reminder) plays its one-shot once.
function spriteFollow() {
  if (!sprite) return;
  const r = reactionFor(status);
  applyReaction(sprite.state, r, lastReaction, currentJob());
  lastReaction = r;
  // Something is really playing on the PC (any player): it moves to it.
  sprite.state.setAmbient("music", !!(status && status.media && status.media.playing));
  // The PC is straining (the backend's own measure): it feels the heat.
  sprite.state.setAmbient("hot", !!(status && status.pc && status.pc.strain));
  // A check-in waiting for an answer: the look. A focus sprint: the hourglass.
  sprite.state.setAmbient("judge", !!(status && status.checkin));
  sprite.state.setAmbient("focus", !!(status && status.focus));
  // A PC action waiting for a yes: it needs you, once per question.
  const ask = !!(status && status.pending_action && status.pending_action.text);
  if (ask && !hadAsk) sprite.fire("confirmation");
  hadAsk = ask;
}

// A cut to another clip: a small pop, so the change of pose reads as a move
// and not as a jump. The class is restarted by forcing a reflow.
function popOnCut(clip) {
  document.body.dataset.clip = clip;
  if (aura) aura.show(clip);
  const cv = $("#stage canvas");
  if (!cv || reducedMotionWanted(settings)) return;
  cv.classList.remove("pop");
  void cv.offsetWidth;
  cv.classList.add("pop");
}

// Dropped after a drag: the window is pulled fully back onto its screen and
// the character's place is remembered for the next start.
async function keepPlace() {
  if (!win || !settings) return;
  try {
    const anchor = await invoke("buddy_fit", { side: geoSide(), w: fitted.w, h: fitted.h });
    if (!anchor) return;
    const at = { x: anchor[0], y: anchor[1], for: `${settings.popup_style}:${settings.buddy_side}` };
    settings.buddy_position = at;  // so the settings that come back are not seen as a change of place
    await api.post("/ui/settings", { changes: { buddy_position: at } });
  } catch (e) { console.error("the buddy's place was not saved", e); }
}

let spriteWired = false;
async function mountSprite() {
  const next = await createSpriteBuddy({ character: settings.character, reducedMotion: reducedMotionWanted(settings), onClip: popOnCut });
  if (sprite) sprite.destroy();  // a character change: the old loop ends and its images go
  sprite = next;
  lastReaction = null;
  $("#stage").innerHTML = '<div class="sprite-wrap"><div class="aura" aria-hidden="true"></div><canvas class="sprite" aria-label="MBUDDY"></canvas></div>';
  wardrobe.setCharacter(castFor(settings.character));
  wardrobe.attach($("#stage .sprite-wrap"));
  aura = new Aura($("#stage .aura"));
  aura.enable(effectsWanted());
  sprite.attach($("#stage canvas"));
  sizeSprite();
  if (!spriteWired) {  // listeners belong to the window, not to one character
    spriteWired = true;
    matchMedia("(prefers-reduced-motion: reduce)")
      .addEventListener("change", () => { sprite.setReducedMotion(reducedMotionWanted(settings)); aura.enable(effectsWanted()); });
    attachDrag($("#stage"), {
      win,
      // The first click of a session is a hello; after that a click is a pat
      // (and a burst of them is poking, which it does not take kindly).
      onClick: () => { sprite.fire(greeted ? "click" : "greet"); greeted = true; openPanel(); },
      onDrop: () => { updateHot(); keepPlace(); },
      reduced: () => reducedMotionWanted(settings),
    });
  }
  // Debug and screenshots: __buddyDebug.fire("finished") or ?event=finished.
  window.__buddyDebug = {
    fire: (name) => sprite.fire(name), get clip() { return sprite.state.clip; }, get playing() { return sprite.player.name; },
    get clips() { return sprite.clips; }, get sprite() { return sprite; },
    job: (j) => { debugJob = j; spriteFollow(); followJob(); },
    say: (text, who = "MBUDDY") => say(who, text, 9000),
    ask: (text, labels = ["Done", "In 10 min"], kind = "") => ask({ key: `debug:${Date.now()}`, who: kind === "timer" ? "Timer" : "Reminder", text, kind, buttons: labels.map((label) => ({ label, run: async () => {} })) }),
    // The window's size against what is drawn: { window, content, pad }.
    get bounds() { return { window: { ...fitted }, content: contentSize(document.body, geoSide(), 0), pad: PAD }; },
    get notch() { return notch; },
  };
  document.body.dataset.clip = sprite.player.name;
  aura.show(sprite.player.name);
  spriteFollow();
  if (params.get("event")) sprite.fire(params.get("event"));

  fit();
  setTimeout(updateHot, 60);
}

// Browser preview without the backend (?dev): default settings, so the
// window can be opened and screenshotted on its own. ?outline draws the
// window's own rectangle.
const DEV_SETTINGS = { show_buddy: true, popup_style: "corner", buddy_side: "right", buddy_size: "medium",
  theme: "clay", character: params.get("character") || "blink", reduced_motion: false, microphone_on: true };
if (params.has("outline")) document.body.classList.add("outline");

// ?dev&style=corner&size=large&side=left&character=void: look at one layout
// without changing the saved settings (the preview only, never the app).
const DEV_KEYS = { style: "popup_style", size: "buddy_size", side: "buddy_side", character: "character" };
function devView(s) {
  if (EMBED) return { ...s, popup_style: "notch", show_buddy: true };
  if (!params.has("dev")) return s;
  const view = { ...s };
  for (const [param, key] of Object.entries(DEV_KEYS)) if (params.get(param)) view[key] = params.get(param);
  return view;
}

notch = createNotch({
  $, invoke, win, fit, applySettings, display: displayText,
  settings: () => settings, status: () => status, reaction: () => reactionFor(status),
  reduced: () => reducedMotionWanted(settings),
  attention: () => (asking && asking.kind ? { who: asking.who, text: asking.text, kind: asking.kind } : null), resizeStage: () => sizeSprite(), hot: () => updateHot(), fire: (name) => { if (sprite) sprite.fire(name); },
  say: (text) => say("MBUDDY", text, 9000)
});

// The buddy is for when the app is closed: it stands in its corner while the app's window is hidden or minimised (a quick way in),
// and steps aside while the app is open. "Always" in its settings keeps it up.
function watchMain() {
  const W = tauri && tauri.webviewWindow && tauri.webviewWindow.WebviewWindow;
  if (!W || !win) return;
  setInterval(async () => {
    let open = false;
    try {
      const main = await W.getByLabel("main");
      open = !!main && (await main.isVisible()) && !(await main.isMinimized());
    } catch { open = false; }
    // the notch hangs from the top of the screen, clear of the app, so it stays (you see what you change); the corner buddy steps aside
    const should = !!settings && settings.show_buddy && (settings.buddy_when === "always" || settings.popup_style === "notch" || !open);
    if (open === mainOpen && lastShould === should) return;
    mainOpen = open; lastShould = should;
    if (!settings || !settings.show_buddy) return;
    try { should ? await win.show() : await win.hide(); } catch { /* the window is going away */ }
  }, 700);
}
async function boot() {
  for (;;) {
    const r = await api.get("/ui/settings").catch(() => null);
    if (r && r.ok) { applySettings(devView(r.settings)); break; }
    if (params.has("dev")) { applySettings(devView(DEV_SETTINGS)); break; }
    await new Promise((res) => setTimeout(res, 2000));
  }
  watchMain();
  // ?room opens the notch's room, ?say=words makes it speak: to look at them without the backend's events (preview only).
  if ((params.has("dev") || EMBED) && params.has("room")) notch.toggleRoom();
  if (params.has("dev") && params.get("say")) say("MBUDDY", params.get("say"), 600000);
  // The character is its drawn art. If the art does not load (the backend's
  // files are still starting), it is tried again; nothing else stands in for it.
  for (;;) {
    try { await mountSprite(); break; }
    catch (e) { console.error("the character's art did not load; trying again", e); await new Promise((res) => setTimeout(res, 3000)); }
  }
  // Follow changes made in the app or the quick panel (character, theme,
  // style, size, side, on/off, mic): /ui/live carries settings_rev and a new
  // one refetches, instead of polling (S81).
  let rev = null, ticket = 0;
  statusStream((s) => {
    if (s.settings_rev !== undefined && s.settings_rev !== rev) {
      rev = s.settings_rev;
      const mine = ++ticket;
      api.get("/ui/settings?only=settings").catch(() => null).then((r) => {
        if (mine !== ticket) return;
        if (!r || !r.ok) { rev = null; return; }  // try again on the next update
        if (JSON.stringify(devView(r.settings)) !== JSON.stringify(settings)) applySettings(devView(r.settings));
      });
    }
    onStatus(s);
  }, (c) => {
    if (c) { rev = null; return; }  // after a reconnect, refetch once
    // The feed is gone (the backend stopped or restarted): what it last said is no longer true. Nothing is playing, working or
    // waiting as far as this window knows, so the character and the notch stop showing it until the feed is back.
    if (status) onStatus({ ...status, media: null, job: null, pc: null, focus: null, checkin: null, pending_action: {}, listening: false, thinking: false, speaking: false });
  });
}
boot();
