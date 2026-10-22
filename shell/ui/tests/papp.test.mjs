// Tests for the phone app's data and rewards (js/papp/store.js). Run: node --test shell/ui/tests
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

// the store keeps everything in localStorage: a small stand-in for Node
const mem = new Map();
globalThis.localStorage = {
  getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => void mem.set(k, String(v)),
  removeItem: (k) => void mem.delete(k), clear: () => mem.clear(), key: (i) => [...mem.keys()][i], get length() { return mem.size; },
};
const store = await import("../js/papp/store.js");
beforeEach(() => mem.clear());

test("a note is stuck to today unless a day is given", () => {
  const a = store.notes.add("milk"), b = store.notes.add("dentist", "2030-01-02");
  assert.equal(a.day, store.dayKey());
  assert.equal(b.day, "2030-01-02");
  assert.equal(store.notes.add("   "), null, "an empty note is not saved");
});

test("a bad day falls back to today", () => {
  assert.equal(store.notes.add("x", "tomorrow").day, store.dayKey());
});

test("finishing a task earns xp, starts a streak and the first badge", () => {
  const t = store.tasks.add({ title: "write report" });
  const heard = [];
  store.rewards.listen((e) => heard.push(e));
  store.tasks.done(t.id);
  const r = store.rewards.state();
  assert.equal(r.xp, 30);
  assert.equal(r.streak, 1);
  assert.ok(r.badges.find((b) => b.id === "first_tick").on);
  assert.equal(heard[0].badge.id, "first_tick");
});

test("un-doing a task earns nothing", () => {
  const t = store.tasks.add({ title: "x" });
  store.tasks.done(t.id, false);
  assert.equal(store.rewards.state().xp, 0);
});

test("a streak continues on the next day and restarts after a gap", () => {
  const yesterday = store.dayKey(new Date(Date.now() - 86400000)), old = store.dayKey(new Date(Date.now() - 5 * 86400000));
  localStorage.setItem("mbp_rewards", JSON.stringify({ xp: 0, streak: 4, lastDay: yesterday, badges: {} }));
  store.rewards.taskDone();
  assert.equal(store.rewards.state().streak, 5);
  localStorage.setItem("mbp_rewards", JSON.stringify({ xp: 0, streak: 4, lastDay: old, badges: {} }));
  store.rewards.taskDone();
  assert.equal(store.rewards.state().streak, 1);
});

test("400 xp is a level, and a level up is announced", () => {
  const heard = [];
  store.rewards.listen((e) => heard.push(e));
  store.rewards.add(390, "x");
  assert.equal(store.rewards.state().level, 1);
  store.rewards.add(30, "y");
  assert.equal(store.rewards.state().level, 2);
  assert.equal(heard.at(-1).levelUp, 2);
});

test("a badge is only earned once", () => {
  store.rewards.unlock("note_taker");
  const when = store.rewards.state().badges.find((b) => b.id === "note_taker").when;
  store.rewards.unlock("note_taker");
  assert.equal(store.rewards.state().earned, 1);
  assert.ok(when);
});

test("a conversation keeps the sources of an answer, at most four", () => {
  store.convo.add("who", "Someone", "web", [], Array.from({ length: 6 }, (_, i) => ({ title: "t" + i, url: "https://x/" + i, host: "x" })));
  assert.equal(store.convo.list(1)[0].sources.length, 4);
});

import { predict, heat, lightsBoard, shuffle, WORDS, GAMES } from "../js/papp/games.js";

test("the mind reader learns a repeating pattern", () => {
  const h = []; for (let i = 0; i < 12; i++) h.push(i % 2 ? "R" : "L");      // L R L R ...
  h.push("L"); h.push("R");                                                   // last two: L R, which was followed by L every time
  assert.equal(predict(h), "L");
});

test("hot and cold words get warmer as the distance shrinks", () => {
  assert.deepEqual([0, 1, 2, 4, 6, 8].map(heat), ["Found it", "Burning", "Hot", "Warm", "Cool", "Cold"]);
});

test("a lights-out board is solvable: pressing the same squares again clears it", () => {
  const seq = [0.05, 0.3, 0.55, 0.8, 0.95]; let k = 0;
  const { board, press } = lightsBoard(() => seq[k++ % seq.length]);
  assert.ok(board.some(Boolean));
  [0.05, 0.3, 0.55, 0.8, 0.95].forEach((r) => press(Math.floor(r * 16)));
  assert.ok(board.every((v) => !v));
});

test("a shuffled word keeps its letters and changes their order", () => {
  for (const w of WORDS) { const s = shuffle(w); assert.equal([...s].sort().join(""), [...w].sort().join("")); assert.notEqual(s, w); }
});

test("every game in the list has a name, a note and a unique id", () => {
  assert.equal(new Set(GAMES.map((g) => g.id)).size, GAMES.length);
  assert.ok(GAMES.length >= 8 && GAMES.every((g) => g.name && g.note));
});

import { searchAll } from "../js/papp/views/search.js";

test("search finds words across tasks, events, notes and the conversation, in any order", () => {
  const fake = {
    tasks: { list: () => [{ title: "Buy milk and eggs", done: false, when: "2026-10-13T09:00" }, { title: "Call mum", done: true }] },
    events: { list: () => [{ title: "Football match", start: "2026-10-14T17:00", place: "Riverside" }] },
    notes: { list: () => [{ text: "Milk is in the fridge", day: "2026-10-12" }] },
    convo: { list: () => [{ you: "what is the weather", me: "Sunny, 22 degrees" }] },
  };
  assert.deepEqual(searchAll(fake, "milk").map((g) => g.kind), ["tasks", "notes"]);
  assert.deepEqual(searchAll(fake, "eggs buy").map((g) => g.kind), ["tasks"]);
  assert.equal(searchAll(fake, "riverside")[0].rows[0].text, "Football match");
  assert.deepEqual(searchAll(fake, "sunny").map((g) => g.kind), ["chat"]);
  assert.deepEqual(searchAll(fake, "   "), []);
});

import { hero, round, MONSTERS, roll, intent, floorScale, monsterHp, perkChoices, PERKS, newRun } from "../js/papp/quest.js";
import { snakeStep, params, tttBest, tttWinner, tttThreats } from "../js/papp/games.js";
import { coach, CHARACTERS, EVENTS } from "../js/papp/coach.js";
import { winXp, levelNote } from "../js/papp/gameshell.js";

test("the buddy is stronger at a higher level (it comes from what you really did)", () => {
  assert.ok(hero(5).hp > hero(1).hp && hero(5).atk > hero(1).atk && hero(5).zap > hero(1).zap);
});

test("a fight round: striking hurts the monster and the monster hits back; guarding halves it and heals", () => {
  const s0 = { level: 1, hp: 50, max: 56, room: 0, mhp: 26, charges: 3, windup: false, log: [] };
  const hit = round(s0, "hit", () => 0.5);
  assert.ok(hit.mhp < 26 && hit.hp < 50);
  const guard = round({ ...s0, hp: 40 }, "guard", () => 0.5);
  assert.equal(guard.mhp, 26);
  assert.ok(guard.taken < hit.taken && guard.hp >= 40 + 5 - guard.taken);
  const zap = round(s0, "zap", () => 0.5);
  assert.equal(zap.charges, 2); assert.ok(zap.dealt > hit.dealt);
});

test("a winding-up monster hits harder, and a dead monster does not hit back", () => {
  const base = { level: 1, hp: 50, max: 56, room: 1, mhp: 40, charges: 3, log: [] };
  assert.ok(round({ ...base, windup: true }, "hit", () => 0.5).taken > round({ ...base, windup: false }, "hit", () => 0.5).taken);
  const kill = round({ ...base, mhp: 1, windup: false }, "hit", () => 0.5);
  assert.equal(kill.mhp, 0); assert.equal(kill.taken, 0);
});

test("the dungeon gets harder room by room", () => {
  for (let i = 1; i < MONSTERS.length; i++) assert.ok(MONSTERS[i].hp > MONSTERS[i - 1].hp);
  assert.equal(roll(4, 8, 0), 4); assert.equal(roll(4, 8, 0.999), 8);
});

test("snake moves, grows on food, and dies on a wall or on itself", () => {
  let st = { size: 6, body: [[2, 2], [1, 2]], food: [3, 2] };
  st = snakeStep(st, "R", () => 0);
  assert.equal(st.body.length, 3); assert.ok(st.ate); assert.deepEqual(st.body[0], [3, 2]);
  assert.equal(snakeStep({ size: 6, body: [[5, 0], [4, 0]], food: [0, 5] }, "R").dead, true);
  const coil = { size: 6, body: [[2, 2], [2, 3], [1, 3], [1, 2], [1, 1]], food: [5, 5] };
  assert.equal(snakeStep(coil, "L").dead, true);
});

test("every game gets harder as the level rises, and is never impossible", () => {
  assert.ok(params("tap", 6).targets > params("tap", 1).targets && params("tap", 6).limit < params("tap", 1).limit);
  assert.ok(params("simon", 5).rounds > params("simon", 1).rounds && params("simon", 5).speed < params("simon", 1).speed);
  assert.ok(params("snake", 5).need > params("snake", 1).need && params("snake", 5).ms < params("snake", 1).ms);
  assert.ok(params("mind", 4).max < params("mind", 1).max);
  assert.ok(params("ttt", 5).mistake < params("ttt", 1).mistake);
  assert.ok(params("ttt", 99).mistake >= 0.1 && params("mind", 99).max >= 3 && params("tap", 99).limit >= 6 && params("snake", 99).need <= 30);
});

test("a win moves a game up a level; a loss keeps it and ends the streak; the record is kept", () => {
  const a = store.games.record("snake", { won: true, score: 7 });
  assert.equal(a.level, 2); assert.equal(a.streak, 1); assert.equal(a.best, 7);
  const b = store.games.record("snake", { won: true, score: 5 });
  assert.equal(b.level, 3); assert.equal(b.streak, 2); assert.equal(b.best, 7); assert.equal(b.bestStreak, 2);
  const c = store.games.record("snake", { won: false, score: 9 });
  assert.equal(c.level, 3); assert.equal(c.streak, 0); assert.equal(c.best, 9); assert.equal(c.plays, 3); assert.equal(c.wins, 2);
  assert.equal(store.games.get("never-played").level, 1);
});

test("the buddy has a line for every event, in every voice", () => {
  for (const ch of CHARACTERS) for (const ev of EVENTS) { const l = coach(ch, ev); assert.ok(typeof l === "string" && l.length > 3, `${ch}/${ev}`); }
  assert.ok(coach("blink", "bad", "Try the other side.").endsWith("Try the other side."));
});

test("winning pays more at higher levels, with a ceiling", () => {
  assert.ok(winXp(5) > winXp(1)); assert.equal(winXp(500), 120);
  assert.ok(levelNote("tap", 3).length > 0 && levelNote("hot", 3).includes("taps"));
});

test("tic-tac-toe: I block and win when I can, and take a threat for what it is", () => {
  const b = ["X", "X", "", "O", "", "", "", "", ""];
  assert.deepEqual(tttThreats(b, "X"), [2]);
  assert.equal(tttBest(b), 2);                                               // blocks
  assert.equal(tttBest(["O", "O", "", "X", "X", "", "", "", ""]), 2);        // wins
  assert.equal(tttWinner(["X", "X", "X", "", "", "", "", "", ""]), "X");
});

test("quest: each floor is harder, a charge move warns you, perks help and a floor-two monster is tougher", () => {
  assert.ok(floorScale(3).hp > floorScale(2).hp && floorScale(2).dmg > floorScale(1).dmg);
  assert.ok(monsterHp(0, 2) > monsterHp(0, 1));
  const s = { ...newRun(1), room: 1 };
  assert.equal(intent(s).warn, false);
  assert.equal(intent({ ...s, windup: true }).warn, true);
  assert.ok(intent({ ...s, windup: true }).text.includes("Guard"));
  const picks = perkChoices(); assert.equal(picks.length, 3); assert.equal(new Set(picks.map((p) => p.id)).size, 3);
  const st = newRun(1); const hp0 = st.max; PERKS.find((p) => p.id === "tough").apply(st);
  assert.equal(st.max, hp0 + 12); const atk0 = hero(1).atk; PERKS.find((p) => p.id === "sharp").apply(st);
  assert.equal(hero(1, st.perks).atk, atk0 + 3);
});

import { swipeOf, SWIPE_AT } from "../js/papp/gestures.js";
import { patternOf } from "../js/papp/haptics.js";
import { parseQuick } from "../js/phone-actions.js";

test("a drag commits only past the threshold, in the right direction", () => {
  assert.equal(swipeOf(SWIPE_AT), "right"); assert.equal(swipeOf(-SWIPE_AT - 5), "left");
  assert.equal(swipeOf(SWIPE_AT - 1), ""); assert.equal(swipeOf(0), "");
});

test("every haptic kind has a pattern, and an unknown one is silent", () => {
  for (const k of ["tick", "ok", "win", "level", "loss", "warn"]) assert.ok(Array.isArray(patternOf(k)) && patternOf(k).length);
  assert.equal(patternOf("nope"), null);
});

test("quick add: a thing and a time becomes a task or an event; questions and chat do not", () => {
  const one = (s) => { const r = parseQuick(s); return r.length ? [r[0].kind, r[0].params.title] : null; };
  assert.deepEqual(one("dentist friday 3pm"), ["calendar", "dentist"]);
  assert.deepEqual(one("pay rent tomorrow"), ["task", "pay rent"]);
  assert.deepEqual(one("football match saturday at 5"), ["calendar", "football match"]);
  assert.equal(one("what is 5 pm in tokyo"), null);
  assert.equal(one("hello there"), null);
  assert.equal(one("tell me about friday"), null);
});

test("a streak survives one missed day if a freeze was earned, and a week earns a freeze", () => {
  const day = (n) => store.dayKey(new Date(Date.now() - n * 86400000));
  localStorage.setItem("mbp_rewards", JSON.stringify({ xp: 0, streak: 6, lastDay: day(2), badges: {}, freezes: 1 }));
  store.rewards.taskDone();
  let r = store.rewards.state();
  assert.equal(r.streak, 7); assert.equal(r.freezes, 1);          // used one, and day 7 earned one back
  localStorage.setItem("mbp_rewards", JSON.stringify({ xp: 0, streak: 6, lastDay: day(2), badges: {}, freezes: 0 }));
  store.rewards.taskDone();
  r = store.rewards.state();
  assert.equal(r.streak, 1);                                       // no freeze: it starts again
});

test("the daily game is the same all day, finishes once, and the quest shop spends stars", () => {
  const a = store.daily.today(), b = store.daily.today();
  assert.equal(a.game, b.game); assert.equal(a.done, false);
  store.daily.complete();
  assert.equal(store.daily.today().done, true);
  assert.equal(store.quest.buy("skin").length > 0, true);          // no stars yet
  store.quest.addStars(12);
  assert.equal(store.quest.buy("skin"), ""); assert.equal(store.quest.state().stars, 7); assert.equal(store.quest.state().bought.skin, 1);
  assert.equal(store.quest.buy("nope"), "Not found.");
});

test("the recap counts what really happened in the last seven days", () => {
  const t1 = store.tasks.add({ title: "recap a" }), t2 = store.tasks.add({ title: "recap b" });
  store.tasks.done(t1.id, true); store.tasks.done(t2.id, true);
  store.games.record("tap", { won: true, score: 3 });
  const r = store.recap();
  assert.ok(r.tasksDone >= 2 && r.wins >= 1 && r.days.length === 7);
  assert.ok(r.bestCount >= 2 && r.bestDay === store.dayKey());
});

test("an export can be put back: merge keeps both sides, newer wins, progress never goes down; junk is refused", () => {
  localStorage.clear();
  const a = store.tasks.add({ title: "keep me" }); const b = store.tasks.add({ title: "old title" });
  store.rewards.add(900, "seed"); store.games.record("tap", { won: true, score: 4 }); store.games.record("tap", { won: true, score: 4 });
  const file = JSON.parse(JSON.stringify(store.exportAll()));
  assert.equal(file.format, 2); assert.equal(file.tasks.length, 2);
  // a fresh phone gets everything back
  localStorage.clear();
  const info = store.inspectImport(file);
  assert.equal(info.ok, true); assert.equal(info.tasks.length, 2); assert.equal(info.level, 3);
  store.importAll(file, "merge");
  assert.equal(store.tasks.list().length, 2); assert.equal(store.rewards.state().level, 3); assert.equal(store.games.get("tap").level, 3);
  // merge again: no duplicates; a newer edit here is kept
  store.tasks.update(b.id, { title: "edited here" });
  store.importAll(file, "merge");
  assert.equal(store.tasks.list().length, 2); assert.ok(store.tasks.list().some((t) => t.title === "edited here"));
  // a lower level in the file never lowers this phone
  const low = { ...file, rewards: { xp: 10, streak: 0 } };
  store.importAll(low, "merge");
  assert.equal(store.rewards.state().level, 3);
  // junk is refused and nothing changes
  assert.equal(store.inspectImport({ app: "other" }).ok, false);
  assert.equal(store.inspectImport(null).ok, false);
  assert.equal(store.importAll("nope").ok, false);
  assert.equal(store.tasks.list().length, 2);
  // a hostile file cannot smuggle a key through settings
  store.importAll({ app: "MBUDDY", settings: { character: "void", cloud_key: "secret" }, tasks: [] }, "replace");
  assert.equal(store.settings.get().character, "void"); assert.equal(store.settings.get().cloud_key, undefined);
});

test("a full store makes room and tries again; if it still fails, the page is told", () => {
  localStorage.clear();
  for (let i = 0; i < 60; i++) store.convo.add("question " + i, "answer " + i, "test", []);
  const realSet = localStorage.setItem; let calls = 0;
  const heard = []; const off = store.onChange((w) => heard.push(w));
  // full once, until something is trimmed: the second try works
  localStorage.setItem = (k, v) => { calls++; if (k === "mbp_tasks" && calls === 1) throw new Error("QuotaExceededError"); return realSet(k, v); };
  const t = store.tasks.add({ title: "must be saved" });
  assert.ok(t); assert.ok(store.tasks.list().some((x) => x.title === "must be saved"));
  assert.equal(heard.includes("storage_error"), false);
  assert.ok(store.convo.list(200).length <= 40, "old conversation was trimmed to make room");
  // always full: the change is not saved, and it is said out loud
  localStorage.setItem = (k, v) => { if (k === "mbp_tasks") throw new Error("QuotaExceededError"); return realSet(k, v); };
  store.tasks.add({ title: "cannot be saved" });
  assert.equal(heard.includes("storage_error"), true);
  assert.equal(store.tasks.list().some((x) => x.title === "cannot be saved"), false);
  localStorage.setItem = realSet; off();
});

test("the stored data has a version, and an older store gets a backup copy first", () => {
  localStorage.clear();
  localStorage.setItem("mbp_tasks", JSON.stringify([{ id: "a", title: "old", done: false, created: 1, updated: 1 }]));
  assert.equal(store.migrate(), store.SCHEMA_VERSION);
  assert.equal(localStorage.getItem("mbp_schema"), String(store.SCHEMA_VERSION));
  assert.ok(localStorage.getItem("mbp_bak1_tasks"), "the lists were copied before anything changed");
  assert.equal(store.tasks.list().length, 1);
  assert.equal(store.migrate(), store.SCHEMA_VERSION);
});

test("problems are kept (the last twenty) and can be cleared", () => {
  localStorage.clear();
  for (let i = 0; i < 25; i++) store.problems.add("problem " + i);
  assert.equal(store.problems.list().length, 20);
  assert.equal(store.problems.list()[19].text, "problem 24");
  store.problems.clear();
  assert.equal(store.problems.list().length, 0);
});

// Every page file must at least parse: the other tests import only the modules without a screen, so a typo in a view would
// otherwise reach the phone as a blank app (it did once).
import { readdirSync, readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("every page script parses (a typo in a screen would be a blank app)", () => {
  const root = new URL("../js/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
  const dirs = [root, join(root, "papp"), join(root, "papp", "views"), join(root, "buddy")];
  const tmp = mkdtempSync(join(tmpdir(), "mbuddy-syntax-"));
  const bad = [];
  let n = 0;
  for (const d of dirs) {
    for (const f of readdirSync(d).filter((x) => x.endsWith(".js"))) {
      const copy = join(tmp, `${n++}.mjs`);
      writeFileSync(copy, readFileSync(join(d, f), "utf8"));
      try { execFileSync(process.execPath, ["--check", copy], { stdio: "pipe" }); } catch (e) { bad.push(`${join(d, f)}: ${String(e.stderr || e.message).split("\n").slice(0, 3).join(" | ")}`); }
    }
  }
  assert.deepEqual(bad, [], bad.join("\n"));
  assert.ok(n > 10);
});

test("everything has exactly one home: events and timed tasks in Today, untimed tasks in Do, notes in Notes", () => {
  assert.equal(store.placeOf("event", { start: "2026-10-13T10:00" }), "today");
  assert.equal(store.placeOf("task", { when: "2026-10-13T09:00" }), "today");
  assert.equal(store.placeOf("task", { when: "" }), "do");
  assert.equal(store.placeOf("task", {}), "do");
  assert.equal(store.placeOf("note", { day: "2026-10-13" }), "notes");
  // giving a task a time moves it, taking the time away moves it back
  localStorage.clear();
  const t = store.tasks.add({ title: "call Baba" });
  assert.equal(store.placeOf("task", store.tasks.list()[0]), "do");
  store.tasks.update(t.id, { when: "2026-10-13T17:00" });
  assert.equal(store.placeOf("task", store.tasks.list()[0]), "today");
  store.tasks.update(t.id, { when: "" });
  assert.equal(store.placeOf("task", store.tasks.list()[0]), "do");
});
