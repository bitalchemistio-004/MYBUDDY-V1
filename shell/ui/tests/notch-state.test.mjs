import { test } from "node:test";
import assert from "node:assert/strict";
import { deadline, moodOf, wordOf, glowMood, freshReward, levelProgress, inText } from "../js/buddy/notch-state.js";

const NOW = Date.parse("2026-10-13T12:00:00Z");
const ctx = (over = {}) => ({ status: {}, reaction: "idle", attention: null, overview: null, doneUntil: 0, now: NOW, ...over });

test("a question waiting for you outranks everything else", () => {
  assert.equal(moodOf(ctx({ status: { pending_action: { text: "close it?" }, job: { running: true }, media: { playing: true } } })), "alert");
  assert.equal(moodOf(ctx({ status: { checkin: { id: 1 } } })), "alert");
});

test("a ringing timer is due, a waiting message is only soon", () => {
  assert.equal(moodOf(ctx({ attention: { kind: "timer", who: "Timer", text: "done" } })), "due");
  assert.equal(moodOf(ctx({ attention: { kind: "message", who: "Sam", text: "lunch?" } })), "soon");
});

test("deadlines warm up as they close in", () => {
  const timer = (s) => ({ overview: { timers: [{ remaining_s: s, label: "Pasta" }] } });
  assert.equal(moodOf(ctx(timer(90))), "due");          // under two minutes
  assert.equal(moodOf(ctx(timer(10 * 60))), "soon");    // within a quarter of an hour
  assert.equal(moodOf(ctx(timer(40 * 60))), "near");    // within the hour
  assert.equal(moodOf(ctx(timer(3 * 3600))), "");       // beyond an hour it is not the notch's business
  assert.deepEqual(deadline({ timers: [{ remaining_s: 600, label: "A" }, { remaining_s: 120, label: "B" }] }, NOW), { m: 2, title: "B" });
});

test("the reminder that passed in the last hour still counts, an older one does not", () => {
  const at = (min) => new Date(NOW + min * 60000).toISOString();
  assert.equal(deadline({ next_reminder: { title: "Call", due_date: at(-30) } }, NOW).title, "Call");
  assert.equal(deadline({ next_reminder: { title: "Old", due_date: at(-90) } }, NOW), null);
  assert.equal(deadline({ next_reminder: { title: "Done", due_date: at(5), completed: true } }, NOW), null);
});

test("then what the buddy is doing, then what is around it", () => {
  assert.equal(moodOf(ctx({ reaction: "listen" })), "listen");
  assert.equal(moodOf(ctx({ reaction: "think" })), "think");
  assert.equal(moodOf(ctx({ status: { job: { running: true } } })), "work");
  assert.equal(moodOf(ctx({ doneUntil: NOW + 5000 })), "done");
  assert.equal(moodOf(ctx({ status: { pc: { strain: true } } })), "hot");
  assert.equal(moodOf(ctx({ status: { focus: { left: 1 } } })), "focus");
  assert.equal(moodOf(ctx({ status: { media: { playing: true } } })), "music");
  assert.equal(moodOf(ctx({ status: { media: { playing: false } } })), "");     // paused is rest
  assert.equal(moodOf(ctx()), "");
});

test("a character-coloured glow keeps only the moments that need you", () => {
  assert.equal(glowMood("music", "character"), "");
  assert.equal(glowMood("due", "character"), "due");
  assert.equal(glowMood("music", "mood"), "music");
});

test("the word on the strip says the one thing that matters", () => {
  assert.deepEqual(wordOf(ctx({ attention: { who: "Timer", text: "Your 1 minute timer is done" } }), "due"), ["Timer", "waiting"]);
  assert.deepEqual(wordOf(ctx({ status: { pending_action: { text: "x" } } }), "alert"), ["Needs you", ""]);
  assert.deepEqual(wordOf(ctx({ overview: { timers: [{ remaining_s: 600, label: "Pasta" }] } }), "soon"), ["Pasta", "in 10 min"]);
  assert.deepEqual(wordOf(ctx({ reaction: "sleep" }), ""), ["Asleep", ""]);
  assert.deepEqual(wordOf(ctx(), "listen"), ["Listening", ""]);
  assert.deepEqual(wordOf(ctx({ status: { speaking: true } }), ""), ["Speaking", ""]);
  assert.deepEqual(wordOf(ctx(), "music", "Yellow"), ["♪ Yellow", ""]);
  assert.deepEqual(wordOf(ctx({ status: { mic_on: false } }), ""), ["Microphone off", ""]);
  assert.deepEqual(wordOf(ctx(), ""), ["Ready", ""]);
  assert.equal(inText(0.4), "under a minute");
});

test("a reward is celebrated once, and the first sight is only remembered", () => {
  const st = (id) => ({ reward: { id, xp: 10, label: "Task done", kind: "task_done" } });
  assert.deepEqual(freshReward(st(4), null), { first: true, id: 4 });
  assert.equal(freshReward(st(4), 4), null);
  assert.deepEqual(freshReward(st(5), 4), { first: false, id: 5, xp: 10, label: "Task done", kind: "task_done" });
  assert.equal(freshReward({}, 3), null);
  assert.equal(levelProgress({ into_level: 75, per_level: 300 }), 0.25);
  assert.equal(levelProgress(null), 0);
});
