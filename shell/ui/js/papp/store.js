// The phone's own data: settings, tasks, events, notes, the conversation, timers, a short "what I did" log. It all lives on the
// phone (local storage of the app), so nothing here needs the PC or the internet. Every record has an id and an updated time and is
// removed by marking it, never by erasing it, so a later sync with the PC can merge without losing anything.

const PFX = "mbp_";
const listeners = new Set();

function read(key, fallback) {
  try { const v = localStorage.getItem(PFX + key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
}
/** Free some room by trimming the least important old lists (the conversation, the log, the play log, old problems). */
function trimToFit() {
  try {
    for (const [k, keep] of [["convo", 40], ["log", 40], ["playlog", 40], ["problems", 10]]) {
      const raw = localStorage.getItem(PFX + k); if (!raw) continue;
      const arr = JSON.parse(raw);
      if (Array.isArray(arr) && arr.length > keep) localStorage.setItem(PFX + k, JSON.stringify(arr.slice(-keep)));
    }
  } catch { /* nothing more can be freed */ }
}
/** Save [value]. If the phone's store is full or blocked it makes room and tries once more; if that fails too the page is told
 *  ("storage_error"), so a change that was not saved is never silent. Returns whether it was saved. */
function write(key, value) {
  const text = JSON.stringify(value);
  try { localStorage.setItem(PFX + key, text); return true; } catch { /* full or blocked: make room, then once more */ }
  trimToFit();
  try { localStorage.setItem(PFX + key, text); return true; } catch { /* below */ }
  emit("storage_error");
  return false;
}

// ---- the shape of what is stored: a number, so a later change can bring older data across instead of breaking it ----
export const SCHEMA_VERSION = 2;
/** Brings stored data up to [SCHEMA_VERSION]. Before it changes anything it keeps one copy of the lists (bak1_*). Returns the version. */
export function migrate() {
  let cur = 1;
  try { cur = Number(localStorage.getItem(PFX + "schema")) || 1; } catch { return 1; }
  if (cur >= SCHEMA_VERSION) return cur;
  try {
    for (const k of ["tasks", "events", "notes", "rewards", "games"]) { const raw = localStorage.getItem(PFX + k); if (raw) localStorage.setItem(PFX + "bak1_" + k, raw); }
    // 1 -> 2: no list changes shape; version 2 adds the games, quest, daily and playlog records, which start empty
    localStorage.setItem(PFX + "schema", String(SCHEMA_VERSION));
  } catch { /* the app still works; it will try again next time */ }
  return SCHEMA_VERSION;
}
migrate();

// ---- a short local record of what went wrong, for the owner to read or copy (never sent anywhere) ----
export const problems = {
  list: () => read("problems", []),
  add(text) { const l = read("problems", []); l.push({ at: Date.now(), text: String(text).slice(0, 400) }); write("problems", l.slice(-20)); },
  clear() { write("problems", []); },
};
function emit(what) { for (const fn of listeners) { try { fn(what); } catch (e) { console.error(e); } } }

/** Run [fn] whenever something stored changes; returns the way to stop. */
export const onChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };

const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
const now = () => Date.now();

// ---- settings ----
export const DEFAULTS = {
  user_name: "", character: "blink", speak_replies: true, reduced_motion: false,
  cloud_model: "gpt-oss:120b", cloud_share: false, onboarding_done: false, tour_done: false, hey_buddy: false, planner: "auto",
};
export const settings = {
  get: () => ({ ...DEFAULTS, ...read("settings", {}) }),
  set(changes) { const s = { ...settings.get(), ...changes }; write("settings", s); emit("settings"); return s; },
};

// ---- tasks ----
const allTasks = () => read("tasks", []);
export const tasks = {
  list: () => allTasks().filter((t) => !t.deleted).sort((a, b) => Number(a.done) - Number(b.done) || (a.when || "9").localeCompare(b.when || "9") || a.created - b.created),
  add({ title, when = "", notes = "" }) {
    const t = { id: newId(), title: String(title).trim().slice(0, 200), when: when || "", notes, done: false, created: now(), updated: now() };
    if (!t.title) return null;
    write("tasks", [...allTasks(), t]); emit("tasks"); return t;
  },
  update(id, patch) {
    write("tasks", allTasks().map((t) => (t.id === id ? { ...t, ...patch, updated: now() } : t))); emit("tasks");
  },
  done(id, on = true) { tasks.update(id, { done: on }); if (on) rewards.taskDone(); },
  remove(id) { tasks.update(id, { deleted: true }); },
  restore(id) { tasks.update(id, { deleted: false }); },
  snooze(id, minutes) {
    const d = new Date(Date.now() + minutes * 60000), z = (n) => String(n).padStart(2, "0");
    tasks.update(id, { when: `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`, done: false });
  },
};

// ---- events (the calendar) ----
const allEvents = () => read("events", []);
export const events = {
  list: () => allEvents().filter((e) => !e.deleted).sort((a, b) => a.start.localeCompare(b.start)),
  add({ title, start, end = "", place = "" }) {
    const e = { id: newId(), title: String(title).trim().slice(0, 200) || "Event", start, end, place, created: now(), updated: now() };
    write("events", [...allEvents(), e]); emit("events"); return e;
  },
  remove(id) { write("events", allEvents().map((e) => (e.id === id ? { ...e, deleted: true, updated: now() } : e))); emit("events"); },
  update(id, patch) { write("events", allEvents().map((e) => (e.id === id ? { ...e, ...patch, updated: now() } : e))); emit("events"); },
  restore(id) { write("events", allEvents().map((e) => (e.id === id ? { ...e, deleted: false, updated: now() } : e))); emit("events"); },
};

// ---- notes ----
const allNotes = () => read("notes", []);
export const notes = {
  list: () => allNotes().filter((n) => !n.deleted).sort((a, b) => b.created - a.created),
  add(text, day = "") {
    const n = { id: newId(), text: String(text).trim().slice(0, 2000), day: /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : dayKey(), created: now(), updated: now() };
    if (!n.text) return null;
    write("notes", [...allNotes(), n]); emit("notes"); rewards.unlock("note_taker"); return n;
  },
  remove(id) { write("notes", allNotes().map((n) => (n.id === id ? { ...n, deleted: true, updated: now() } : n))); emit("notes"); },
  update(id, patch) { write("notes", allNotes().map((n) => (n.id === id ? { ...n, ...patch, updated: now() } : n))); emit("notes"); },
  restore(id) { write("notes", allNotes().map((n) => (n.id === id ? { ...n, deleted: false, updated: now() } : n))); emit("notes"); },
};

// ---- the conversation ----
export const convo = {
  list: (limit = 40) => read("convo", []).slice(-limit),
  add(you, me, source = "", did = [], sources = []) {
    const all = read("convo", []); all.push({ id: newId(), you, me, source, did: (did || []).slice(0, 6), sources: (sources || []).slice(0, 4), at: now() });
    write("convo", all.slice(-200)); emit("convo");
  },
  clear() { write("convo", []); emit("convo"); },
};

// ---- what I did (for the Now screen) ----
export const log = {
  list: (limit = 8) => read("log", []).slice(-limit).reverse(),
  add(text) {
    const all = read("log", []); all.push({ at: now(), text: String(text).slice(0, 160) });
    write("log", all.slice(-60)); emit("log");
  },
};

// ---- timers (shown here; the alarm itself is the phone's Clock app) ----
export const timers = {
  list: () => read("timers", []).filter((t) => t.endsAt > now()),
  add(label, seconds) {
    const t = { id: newId(), label: label || "Timer", endsAt: now() + seconds * 1000 };
    write("timers", [...timers.list(), t]); emit("timers"); return t;
  },
  remove(id) { write("timers", timers.list().filter((t) => t.id !== id)); emit("timers"); },
};

// ---- focus sprint ----
export const focus = {
  state: () => { const f = read("focus", null); return f && f.endsAt > now() ? f : null; },
  start(minutes) { write("focus", { startedAt: now(), endsAt: now() + minutes * 60000, minutes }); emit("focus"); rewards.unlock("zone"); },
  stop() { write("focus", null); emit("focus"); },
};

/** Local date as YYYY-MM-DD, for grouping the agenda by day. */
export const dayKey = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

// ---- rewards: xp, level, the streak and badges. Only what really happened earns anything. ----
const BADGES = [
  { id: "first_tick", name: "First tick", how: "Finish a task", icon: "check" },
  { id: "early_bird", name: "Early bird", how: "Finish a task before 9 am", icon: "sun" },
  { id: "night_owl", name: "Night owl", how: "Finish a task after 11 pm", icon: "moon" },
  { id: "streak3", name: "Three in a row", how: "Finish tasks 3 days running", icon: "flame" },
  { id: "streak7", name: "A whole week", how: "Finish tasks 7 days running", icon: "cal" },
  { id: "note_taker", name: "Note taker", how: "Save a note", icon: "paper" },
  { id: "game_on", name: "Game on", how: "Win a game", icon: "pad" },
  { id: "chatterbox", name: "Chatterbox", how: "Get 50 answers from me", icon: "chat" },
  { id: "zone", name: "In the zone", how: "Start a focus sprint", icon: "timer" },
];
const XP_PER_LEVEL = 400;
const rewardListeners = new Set();
const rread = () => ({ xp: 0, streak: 0, lastDay: "", badges: {}, ...read("rewards", {}) });
export const rewards = {
  BADGES,
  /** Called with { text, xp, badge } when something is earned: the app shows it. */
  listen(fn) { rewardListeners.add(fn); return () => rewardListeners.delete(fn); },
  state() {
    const r = rread();
    const level = 1 + Math.floor(r.xp / XP_PER_LEVEL), into = r.xp % XP_PER_LEVEL;
    const list = BADGES.map((b) => ({ ...b, on: !!r.badges[b.id], when: r.badges[b.id] ? new Date(r.badges[b.id]).toLocaleDateString([], { day: "numeric", month: "short" }) : "" }));
    return { xp: r.xp, level, pct: Math.round(into / XP_PER_LEVEL * 100), toNext: XP_PER_LEVEL - into, streak: r.streak, lastDay: r.lastDay, freezes: r.freezes || 0, saved: r.saved || "", badges: list, earned: list.filter((b) => b.on).length, total: list.length };
  },
  /** Give xp and tell the app. Returns the new badge, if one was just earned. */
  add(xp, text, badgeId = "") {
    const r = rread(); r.xp += xp;
    let badge = null;
    if (badgeId && !r.badges[badgeId]) { r.badges[badgeId] = now(); badge = BADGES.find((b) => b.id === badgeId); }
    write("rewards", r); emit("rewards");
    const level = 1 + Math.floor(r.xp / XP_PER_LEVEL), before = 1 + Math.floor((r.xp - xp) / XP_PER_LEVEL);
    for (const fn of rewardListeners) { try { fn({ text, xp, badge, levelUp: level > before ? level : 0, streak: r.streak }); } catch (e) { console.error(e); } }
    return badge;
  },
  unlock(id) { const r = rread(); if (r.badges[id]) return; r.badges[id] = now(); write("rewards", r); emit("rewards"); const badge = BADGES.find((b) => b.id === id);
    for (const fn of rewardListeners) { try { fn({ text: "Badge: " + badge.name, xp: 0, badge, levelUp: 0, streak: r.streak }); } catch (e) { console.error(e); } } },
  taskDone() {
    const r = rread(), today = dayKey(), yesterday = dayKey(new Date(Date.now() - 86400000));
    if (r.lastDay !== today) {
      const twoAgo = dayKey(new Date(Date.now() - 2 * 86400000));
      if (r.lastDay === yesterday) r.streak += 1;
      else if (r.lastDay === twoAgo && (r.freezes || 0) > 0 && r.streak > 0) { r.freezes -= 1; r.streak += 1; r.saved = today; }       // one missed day, saved by a freeze
      else r.streak = 1;
      r.lastDay = today;
      if (r.streak > 0 && r.streak % 7 === 0 && r.freezeAt !== r.streak) { r.freezes = Math.min(2, (r.freezes || 0) + 1); r.freezeAt = r.streak; }       // a week earns one
      write("rewards", r);
    }
    const h = new Date().getHours();
    const order = [!r.badges.first_tick && "first_tick", h < 9 && !r.badges.early_bird && "early_bird", h >= 23 && !r.badges.night_owl && "night_owl",
      r.streak >= 3 && !r.badges.streak3 && "streak3", r.streak >= 7 && !r.badges.streak7 && "streak7"].filter(Boolean);
    const first = order.shift();
    rewards.add(30, "Task done", first || "");
    order.forEach((id) => setTimeout(() => rewards.unlock(id), 1800));
    if (convo.list(60).length >= 50) rewards.unlock("chatterbox");
  },
  gameWon(label, xp = 40) { rewards.add(xp, label, "game_on"); },
};

// ---- games: how far you have got in each one (its level, wins, plays, best run), so a game is a climb, not a one-off ----
const gread = () => read("games", {});
const blank = () => ({ level: 1, plays: 0, wins: 0, streak: 0, bestStreak: 0, best: 0, seen: false });
export const games = {
  get: (id) => ({ ...blank(), ...(gread()[id] || {}) }),
  all: () => gread(),
  /** Note a finished round. A win moves the level up one; a loss keeps it. [score] is kept as the best if higher. Returns the new record. */
  record(id, { won, score = 0 }) {
    const all = gread(), g = { ...blank(), ...(all[id] || {}) };
    g.plays += 1; g.last = now();
    if (won) { g.wins += 1; g.streak += 1; g.bestStreak = Math.max(g.bestStreak, g.streak); g.level += 1; } else g.streak = 0;
    if (score > g.best) g.best = score;
    all[id] = g; write("games", all);
    const log = read("playlog", []); log.push({ at: now(), id, won: !!won }); write("playlog", log.slice(-300));
    emit("games");
    return g;
  },
  /** Set one field (for example that the quest tutorial was seen). */
  mark(id, patch) { const all = gread(); all[id] = { ...blank(), ...(all[id] || {}), ...patch }; write("games", all); emit("games"); },
};

/** Where something lives, so it is in exactly one place: an event or a task WITH a time is in Today, a task without a time is in Do, a note is in Notes. */
export const placeOf = (kind, item = {}) => (kind === "note" ? "notes" : kind === "event" ? "today" : item.when ? "today" : "do");

// ---- the daily game: one game a day, the same for the whole day, with a bonus; and the quest's stars and shop ----
const DAILY_POOL = ["tap", "simon", "mind", "stars", "hot", "lights", "words", "snake", "brick", "ttt"];
export const daily = {
  /** { day, game, level, done }: today's game (picked by the date, so it is the same all day) at a level that follows yours. */
  today() {
    const day = dayKey(), d = read("daily", {});
    const n = Number(day.replace(/-/g, ""));
    const game = DAILY_POOL[n % DAILY_POOL.length], level = Math.max(1, Math.min(6, 1 + Math.floor((1 + Math.floor(rread().xp / XP_PER_LEVEL)) / 3)));
    return { day, game, level, done: d.day === day && !!d.done };
  },
  complete() { const day = dayKey(); write("daily", { day, done: true }); emit("games"); },
};

const QUEST_SHOP = [
  { id: "skin", name: "Thick skin", text: "Start every run with 10 more health.", cost: 5, max: 3 },
  { id: "stone", name: "Whetstone", text: "Start every run with Strike 2 harder.", cost: 6, max: 3 },
  { id: "spare", name: "Spare charge", text: "Start every run with one more Zap.", cost: 8, max: 2 },
];
export const quest = {
  SHOP: QUEST_SHOP,
  state() { const q = read("quest", {}); return { stars: q.stars || 0, bought: q.bought || {} }; },
  addStars(n) { const q = quest.state(); write("quest", { ...q, stars: q.stars + n }); emit("games"); },
  /** Buy one level of an item. Returns an error in words or "". */
  buy(id) {
    const q = quest.state(), it = QUEST_SHOP.find((x) => x.id === id);
    if (!it) return "Not found.";
    const have = q.bought[id] || 0;
    if (have >= it.max) return "You have all of that.";
    if (q.stars < it.cost) return `That costs ${it.cost} stars; you have ${q.stars}.`;
    write("quest", { stars: q.stars - it.cost, bought: { ...q.bought, [id]: have + 1 } }); emit("games");
    return "";
  },
};

/** The last seven days from what really happened: tasks finished, games won, the streak, the best day. */
export function recap(nowMs = Date.now()) {
  const day = (ms) => dayKey(new Date(ms));
  const days = Array.from({ length: 7 }, (_, i) => day(nowMs - (6 - i) * 86400000));
  const perDay = Object.fromEntries(days.map((d) => [d, 0]));
  for (const t of allTasks()) if (t.done && !t.deleted && perDay[day(t.updated)] !== undefined) perDay[day(t.updated)]++;
  const tasksDone = days.reduce((a, d) => a + perDay[d], 0);
  const best = days.reduce((b, d) => (perDay[d] > perDay[b] ? d : b), days[0]);
  const wins = read("playlog", []).filter((e) => e.won && e.at > nowMs - 7 * 86400000).length;
  const played = read("playlog", []).filter((e) => e.at > nowMs - 7 * 86400000).length;
  const notesAdded = allNotes().filter((n) => !n.deleted && n.created > nowMs - 7 * 86400000).length;
  const r = rread();
  return { days, perDay, tasksDone, bestDay: perDay[best] ? best : "", bestCount: perDay[best], wins, played, notesAdded, streak: r.streak, level: 1 + Math.floor(r.xp / XP_PER_LEVEL) };
}

// ---- a whole copy of your data in one file, and putting it back ----
const MAX_ITEMS = 5000, str = (v, n) => String(v == null ? "" : v).slice(0, n);
const cleanTask = (t) => (t && typeof t === "object" && t.id && str(t.title, 1) ? { id: str(t.id, 40), title: str(t.title, 200), when: str(t.when, 16), notes: str(t.notes, 2000), done: !!t.done, created: Number(t.created) || now(), updated: Number(t.updated) || now(), deleted: !!t.deleted } : null);
const cleanEvent = (e) => (e && typeof e === "object" && e.id && e.start ? { id: str(e.id, 40), title: str(e.title, 200) || "Event", start: str(e.start, 16), end: str(e.end, 16), place: str(e.place, 120), created: Number(e.created) || now(), updated: Number(e.updated) || now(), deleted: !!e.deleted } : null);
const cleanNote = (n) => (n && typeof n === "object" && n.id && str(n.text, 1) ? { id: str(n.id, 40), text: str(n.text, 2000), day: /^\d{4}-\d{2}-\d{2}$/.test(n.day) ? n.day : dayKey(new Date(Number(n.created) || now())), created: Number(n.created) || now(), updated: Number(n.updated) || now(), deleted: !!n.deleted } : null);
const SETTINGS_KEEP = ["user_name", "character", "speak_replies", "reduced_motion", "cloud_model", "cloud_share", "planner", "onboarding_done", "tour_done", "tips_seen"];

/** Everything worth keeping, as one object (no keys or tokens: those never leave the phone's secure storage). */
export function exportAll() {
  const s = settings.get(); const keep = {}; for (const k of SETTINGS_KEEP) if (k in s) keep[k] = s[k];
  return {
    app: "MBUDDY", format: 2, exported: new Date().toISOString(),
    tasks: allTasks().filter((t) => !t.deleted), events: allEvents().filter((e) => !e.deleted), notes: allNotes().filter((n) => !n.deleted),
    rewards: read("rewards", {}), games: read("games", {}), playlog: read("playlog", []), quest: read("quest", {}), daily: read("daily", {}), settings: keep,
    conversation: convo.list(200).map((c) => ({ you: c.you, me: c.me, at: c.at })),
  };
}

/** What a file would add, without changing anything: { ok, why, tasks, events, notes, level, ... }. */
export function inspectImport(data) {
  if (!data || typeof data !== "object" || data.app !== "MBUDDY") return { ok: false, why: "That is not a MBUDDY export file." };
  const arr = (a) => (Array.isArray(a) ? a.slice(0, MAX_ITEMS) : []);
  const tasks = arr(data.tasks).map(cleanTask).filter(Boolean), events = arr(data.events).map(cleanEvent).filter(Boolean), notes = arr(data.notes).map(cleanNote).filter(Boolean);
  const r = data.rewards && typeof data.rewards === "object" ? data.rewards : {};
  const xp = Number(r.xp) || 0;
  return { ok: true, why: "", tasks, events, notes, xp, level: 1 + Math.floor(xp / XP_PER_LEVEL), streak: Number(r.streak) || 0, exported: str(data.exported, 30), format: Number(data.format) || 1 };
}

/** Put an export back. [mode] "merge" keeps what is here and adds what is not (the newer copy of the same item wins); "replace" makes this phone match the file. */
export function importAll(data, mode = "merge") {
  const info = inspectImport(data);
  if (!info.ok) return info;
  const merge = (cur, inc) => {
    if (mode === "replace") return inc;
    const by = new Map(cur.map((x) => [x.id, x]));
    for (const x of inc) { const o = by.get(x.id); if (!o || (x.updated || 0) > (o.updated || 0)) by.set(x.id, x); }
    return [...by.values()];
  };
  write("tasks", merge(allTasks(), info.tasks)); write("events", merge(allEvents(), info.events)); write("notes", merge(allNotes(), info.notes));
  // progress: never lower what is here (a restore must not take a level away)
  const cur = rread(), inc = data.rewards && typeof data.rewards === "object" ? data.rewards : {};
  const badges = { ...(cur.badges || {}) };
  if (inc.badges && typeof inc.badges === "object" && !Array.isArray(inc.badges)) for (const [k, v] of Object.entries(inc.badges)) if (!badges[k]) badges[k] = Number(v) || now();
  else if (Array.isArray(inc.badges)) for (const b of inc.badges) if (b && b.id && b.on && !badges[b.id]) badges[b.id] = now();
  const xp = mode === "replace" ? (Number(inc.xp) || 0) : Math.max(cur.xp || 0, Number(inc.xp) || 0);
  write("rewards", { ...cur, xp, streak: Math.max(cur.streak || 0, Number(inc.streak) || 0), badges, freezes: Math.max(cur.freezes || 0, Number(inc.freezes) || 0), lastDay: cur.lastDay || str(inc.lastDay, 10) });
  if (data.games && typeof data.games === "object") {
    const g = gread();
    for (const [id, rec] of Object.entries(data.games)) if (rec && typeof rec === "object" && /^[a-z0-9_-]{1,20}$/.test(id)) {
      const c = { ...blank(), ...(g[id] || {}) };
      g[id] = { ...blank(), ...rec, level: Math.max(c.level, Number(rec.level) || 1), wins: Math.max(c.wins, Number(rec.wins) || 0), plays: Math.max(c.plays, Number(rec.plays) || 0), best: Math.max(c.best, Number(rec.best) || 0) };
    }
    write("games", g);
  }
  if (data.quest && typeof data.quest === "object") { const q = quest.state(); write("quest", { stars: Math.max(q.stars, Number(data.quest.stars) || 0), bought: { ...q.bought, ...(data.quest.bought && typeof data.quest.bought === "object" ? data.quest.bought : {}) } }); }
  if (data.settings && typeof data.settings === "object") { const keep = {}; for (const k of SETTINGS_KEEP) if (k in data.settings && mode === "replace") keep[k] = data.settings[k]; if (Object.keys(keep).length) settings.set(keep); }
  for (const k of ["tasks", "events", "notes", "rewards", "games", "quest"]) emit(k);
  return { ...info, mode };
}
