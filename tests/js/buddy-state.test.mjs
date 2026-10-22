// The buddy's event -> clip state machine (run: node --test tests/js/).
import { test } from "node:test";
import assert from "node:assert/strict";
import { BuddyState, EVENTS, OFF_EVENTS, AMBIENT, BASE_PRIORITY, SHOT_PRIORITY, applyReaction, applyJob, baseForWork, baseForReaction, shotForReaction } from "../../shell/ui/js/buddy/state.js";

function make(opts = {}) {
  let t = 0;
  const changes = [];
  const s = new BuddyState({ now: () => t, minVisible: 500, onChange: (clip) => changes.push(clip), ...opts });
  return { s, changes, go: (ms) => { t += ms; } };
}

test("generic events map to the right clips", () => {
  const want = {
    heard: "listen", listening: "listen", working: "think", thinking: "think",
    finished: "done", needs_you: "alert", reminder: "bell", confirmation: "alert",
    error: "oops", failed: "oops", thanks: "love", click: "love", idle: "idle", asleep: "sleep",
    searching: "search", writing: "write", goal: "proud", streak: "proud", surprise: "shock",
    greet: "hello", returned: "hello", ack: "wink", chaos: "dizzy",
    music_on: "music", focus_on: "focus", pc_strain: "hot", checkin: "judge",
  };
  for (const [event, clip] of Object.entries(want)) {
    const { s } = make();
    assert.equal(s.fire(event), true, event);
    assert.equal(s.clip, clip, event);
  }
});

test("every event maps to a clip the art has, as a loop or a one-shot the way the art says", () => {
  const drawn = manifest("blink").clips;
  for (const clip of [...Object.values(EVENTS).flat(), ...Object.values(OFF_EVENTS)]) {
    assert.ok(drawn[clip], clip);
    assert.equal(clip in BASE_PRIORITY, drawn[clip].loop, `${clip}: loop in the art and in the state machine`);
    assert.equal(clip in SHOT_PRIORITY, !drawn[clip].loop, clip);
  }
  for (const clip of Object.keys(drawn)) assert.ok(clip in BASE_PRIORITY || clip in SHOT_PRIORITY, `${clip} has an event`);
});

test("unknown events are a soft no-op", () => {
  const { s, changes } = make();
  s.fire("working");
  assert.equal(s.fire("render_finished_in_some_app"), false);
  assert.equal(s.fire(undefined), false);
  assert.equal(s.clip, "think");
  assert.deepEqual(changes, ["think"]);
});

test("base loops by priority: sleep < idle < listen < think", () => {
  const { s } = make();
  s.fire("asleep"); assert.equal(s.base, "sleep");
  s.fire("heard"); assert.equal(s.base, "listen", "hearing you wakes it");
  s.fire("working"); assert.equal(s.base, "think");
  s.fire("listening"); assert.equal(s.base, "think", "still working outranks listening");
  s.fire("idle"); assert.equal(s.base, "idle");
});

test("setBase sets exactly one base state", () => {
  const { s } = make();
  s.fire("heard"); s.fire("working");
  s.setBase("listen");
  assert.equal(s.base, "listen");
  assert.equal(s.setBase("nonsense"), false);
  assert.equal(s.base, "listen");
});

test("a one-shot returns to the right base, even if the base changed meanwhile", () => {
  const { s } = make();
  s.fire("thanks");
  assert.equal(s.clip, "love");
  s.fire("working");
  assert.equal(s.clip, "love", "the one-shot stays on screen");
  s.clipEnded("love");
  assert.equal(s.clip, "think");
});

test("finishing or failing ends the work state", () => {
  const { s } = make();
  s.fire("working");
  s.fire("finished");
  s.clipEnded("done");
  assert.equal(s.clip, "idle");
  s.fire("working"); s.fire("error"); s.clipEnded("oops");
  assert.equal(s.clip, "idle");
});

test("one-shot priority: alert above oops above done above love", () => {
  const { s, go } = make();
  s.fire("thanks");
  s.fire("finished");
  s.fire("error");
  s.fire("needs_you");
  assert.equal(s.clip, "love", "nothing cuts in before the minimum visible time");
  assert.deepEqual(s.queue, ["alert", "oops", "done"]);
  go(500); s.tick();
  assert.equal(s.clip, "alert", "the highest waiting one cuts in");
  s.clipEnded("alert"); assert.equal(s.clip, "oops");
  s.clipEnded("oops"); assert.equal(s.clip, "done");
  s.clipEnded("done"); assert.equal(s.clip, "idle");
});

test("minimum visible time guards a cut-in", () => {
  const { s, go } = make();
  s.fire("finished");
  go(200);
  s.fire("needs_you");
  assert.equal(s.clip, "done");
  go(299); s.tick(); assert.equal(s.clip, "done");
  go(1); s.tick(); assert.equal(s.clip, "alert");
});

test("a lower or equal one-shot waits for the end instead of cutting in", () => {
  const { s, go } = make();
  s.fire("needs_you");
  s.fire("thanks");
  go(5000); s.tick();
  assert.equal(s.clip, "alert");
  s.clipEnded("alert");
  assert.equal(s.clip, "love");
});

test("duplicates are not queued twice and the queue stays short", () => {
  const { s } = make();
  s.fire("thanks");
  s.fire("finished"); s.fire("finished"); s.fire("thanks");
  assert.deepEqual(s.queue, ["done"]);
  s.fire("error"); s.fire("reminder");
  assert.equal(s.queue.length, 3);
});

test("an end report for a clip that is not on screen is ignored", () => {
  const { s } = make();
  s.fire("finished");
  s.clipEnded("love");
  assert.equal(s.clip, "done");
});

test("replaying the same one-shot after it ended notifies again", () => {
  const { s, changes } = make();
  s.fire("thanks"); s.clipEnded("love"); s.fire("thanks");
  assert.deepEqual(changes, ["love", "idle", "love"]);
});

test("reactionFor values map onto the generic events", () => {
  assert.equal(baseForReaction("sleep"), "sleep");
  assert.equal(baseForReaction("listen"), "listen");
  assert.equal(baseForReaction("think"), "think");
  for (const r of ["idle", "wave", "happy", "sad", "worried", "surprised", "alert", "celebrate", "unknown"]) {
    assert.equal(baseForReaction(r), "idle", r);
  }
  assert.equal(shotForReaction("celebrate"), "finished");
  assert.equal(shotForReaction("alert"), "needs_you");
  assert.equal(shotForReaction("happy"), "thanks");
  assert.equal(shotForReaction("sad"), null, "a sad tone is not a failed task");
  assert.equal(shotForReaction("worried"), null);
});

test("applyReaction fires a one-shot only on the change into it", () => {
  const { s } = make();
  applyReaction(s, "think", "idle");
  assert.equal(s.clip, "think");
  applyReaction(s, "celebrate", "think");
  assert.equal(s.clip, "done");
  s.clipEnded("done");
  applyReaction(s, "celebrate", "celebrate");
  assert.equal(s.clip, "idle", "a repeated status does not replay it");
  applyReaction(s, "sleep", "celebrate");
  assert.equal(s.clip, "sleep");
});

// ---- what the new clips are for ----

test("a reminder rings, then waits for you", () => {
  const { s } = make();
  s.fire("reminder");
  assert.equal(s.clip, "bell");
  s.clipEnded("bell"); assert.equal(s.clip, "alert");
  s.clipEnded("alert"); assert.equal(s.clip, "idle");
});

test("ambient loops show under activity and stay until switched off", () => {
  const { s } = make();
  s.fire("music_on");
  assert.equal(s.clip, "music");
  s.setBase("think"); assert.equal(s.clip, "think", "its own work outranks what plays around it");
  s.setBase("idle"); assert.equal(s.clip, "music", "the live status does not switch music off");
  s.setAmbient("hot", true); assert.equal(s.clip, "hot");
  s.fire("pc_ok"); assert.equal(s.clip, "music");
  s.fire("music_off"); assert.equal(s.clip, "idle");
  assert.equal(s.setAmbient("think", true), false, "think is not ambient");
  for (const a of AMBIENT) assert.ok(a in BASE_PRIORITY, a);
});

test("asleep is asleep, whatever plays around it", () => {
  const { s } = make();
  s.fire("music_on"); s.setBase("sleep");
  assert.equal(s.clip, "sleep");
  s.setBase("idle"); assert.equal(s.clip, "music");
});

test("searching and writing outrank plain thinking, and finishing ends them", () => {
  const { s } = make();
  s.fire("working"); s.fire("searching");
  assert.equal(s.clip, "search");
  s.fire("writing"); assert.equal(s.clip, "write");
  s.fire("finished"); s.clipEnded("done");
  assert.equal(s.clip, "idle");
});

test("a burst of clicks is poking: it loses its temper once, then calms down", () => {
  const { s, go } = make({ pokes: 4, pokeWindow: 5000 });
  for (let i = 0; i < 3; i++) { s.fire("click"); s.clipEnded("love"); go(500); }
  s.fire("click");
  assert.equal(s.clip, "furious");
  s.clipEnded("furious");
  s.fire("click"); assert.equal(s.clip, "love", "the count starts over");
});

test("slow clicks are never poking", () => {
  const { s, go } = make({ pokes: 4, pokeWindow: 5000 });
  for (let i = 0; i < 8; i++) { s.fire("click"); assert.equal(s.clip, "love"); s.clipEnded("love"); go(3000); }
});

test("failing again and again is infuriating; a success ends the streak", () => {
  const { s } = make({ failures: 3 });
  s.fire("failed"); s.clipEnded("oops");
  s.fire("failed"); s.clipEnded("oops");
  s.fire("finished"); s.clipEnded("done");
  s.fire("failed"); s.clipEnded("oops");
  s.fire("failed"); s.clipEnded("oops");
  s.fire("failed");
  assert.equal(s.clip, "furious");
});

test("a long quiet stretch gets one bored look, not a loop of them", () => {
  const { s, go, changes } = make({ boredAfter: 1000 });
  go(999); s.tick(); assert.equal(s.clip, "idle");
  go(1); s.tick(); assert.equal(s.clip, "bored");
  s.clipEnded("bored");
  go(60_000); s.tick();
  assert.equal(s.clip, "idle", "only once per quiet stretch");
  s.fire("working"); s.fire("idle");
  go(1000); s.tick();
  assert.equal(s.clip, "bored", "something happened in between, so it can get bored again");
  assert.equal(changes.filter((c) => c === "bored").length, 2);
});

test("it is never bored while something is going on", () => {
  const { s, go } = make({ boredAfter: 1000 });
  s.setBase("think"); go(5000); s.tick(); assert.equal(s.clip, "think");
  s.setBase("idle"); s.fire("music_on"); go(5000); s.tick(); assert.equal(s.clip, "music");
  s.fire("music_off"); s.setBase("sleep"); go(5000); s.tick(); assert.equal(s.clip, "sleep");
});

test("repeated status updates do not restart the quiet stretch", () => {
  const { s, go } = make({ boredAfter: 1000 });
  for (let i = 0; i < 10; i++) { go(100); s.setBase("idle"); s.tick(); }
  assert.equal(s.clip, "bored");
});

test("waking up says hello", () => {
  const { s } = make();
  applyReaction(s, "sleep", null);
  assert.equal(s.clip, "sleep");
  applyReaction(s, "listen", "sleep");
  assert.equal(s.clip, "hello");
  s.clipEnded("hello");
  assert.equal(s.clip, "listen");
  applyReaction(s, "idle", "listen");
  assert.equal(s.clip, "idle", "no second hello");
});

test("a clip the art has but this file does not name can still be shown by name", () => {
  const { s } = make();
  assert.equal(s.fire("juggle"), false);
  s.setClips(["idle", "juggle", "hum"], new Set(["idle", "hum"]));
  assert.equal(s.fire("juggle"), true);
  assert.equal(s.clip, "juggle");
  s.clipEnded("juggle");
  s.fire("hum"); assert.equal(s.clip, "hum", "a loop the art added");
  s.fire("thanks"); assert.equal(s.clip, "love");
});

test("a preview shows exactly the clip asked for, whatever was going on", () => {
  const { s } = make();
  s.fire("working"); s.fire("music_on"); s.fire("thanks"); s.fire("finished");
  assert.equal(s.show("bell"), true);
  assert.equal(s.clip, "bell");
  s.clipEnded("bell");
  assert.equal(s.clip, "idle", "a one-shot goes back to idle, not to what was going on before");
  s.show("music"); assert.equal(s.clip, "music", "a loop stays");
  s.show("search"); assert.equal(s.clip, "search");
  s.show("idle"); assert.equal(s.clip, "idle");
  assert.equal(s.show("no such clip"), false);
  assert.equal(s.clip, "idle");
});

// ---- background jobs ----

test("a running job is work, even while asleep; hearing the user comes first", () => {
  const job = { id: 1, running: true, act: "" };
  assert.equal(baseForWork("sleep", job), "think");
  assert.equal(baseForWork("idle", { ...job, act: "search" }), "search");
  assert.equal(baseForWork("think", { ...job, act: "write" }), "write");
  assert.equal(baseForWork("listen", job), "listen");
  assert.equal(baseForWork("sleep", { ...job, running: false }), "sleep", "an ended job is not work");
  assert.equal(baseForWork("idle", null), "idle");
  assert.equal(baseForWork("idle", { ...job, act: "something new" }), "think", "an unknown act is plain work");
});

test("a job's ending plays once: done when it worked, oops when it did not", () => {
  const { s, changes } = make();
  const seen = new Map();
  const job = { id: 7, running: true, act: "" };
  applyReaction(s, "idle", "idle", job); applyJob(s, job, seen);
  assert.equal(s.clip, "think");
  const ended = { ...job, running: false, ok: true };
  applyReaction(s, "idle", "idle", ended); applyJob(s, ended, seen);
  assert.equal(s.clip, "done");
  s.clipEnded("done");
  applyReaction(s, "idle", "idle", ended); applyJob(s, ended, seen);
  assert.equal(s.clip, "idle", "the same ended job does not play again");
  assert.equal(changes.filter((c) => c === "done").length, 1);
  const bad = { id: 8, running: true, act: "" };
  applyJob(s, bad, seen); applyJob(s, { ...bad, running: false, ok: false }, seen);
  assert.equal(s.clip, "oops");
});

test("a job that had already ended when the window opened is old news", () => {
  const { s } = make();
  applyJob(s, { id: 3, running: false, ok: true }, new Map());
  assert.equal(s.clip, "idle");
  applyJob(s, null, new Map());
  assert.equal(s.clip, "idle");
});

// ---- the cast ----
import { readFileSync as readCast, existsSync } from "node:fs";
import { CAST, CAST_INFO, castFor } from "../../shell/ui/js/buddy/sprite.js";

const CHARS = new URL("../../shell/ui/assets/buddy/chars/", import.meta.url);
function manifest(name) {
  return JSON.parse(readCast(new URL(`${name}/clips.json`, CHARS)));
}

test("every character of the cast has the same clips, the same loops, and a sheet for each", () => {
  const first = manifest(CAST[0]);
  const names = Object.keys(first.clips).sort();
  assert.ok(names.length >= 22, "the 22 drawn clips");
  for (const name of CAST) {
    const m = manifest(name);
    assert.deepEqual(Object.keys(m.clips).sort(), names, name);
    for (const clip of names) {
      assert.equal(m.clips[clip].loop, first.clips[clip].loop, `${name}/${clip}`);
      assert.ok(m.clips[clip].durations.length > 0, `${name}/${clip}`);
      assert.ok(existsSync(new URL(`${name}/${clip}.webp`, CHARS)), `${name}/${clip}.webp`);
    }
    assert.ok(existsSync(new URL(`${name}/still.webp`, CHARS)), `${name}/still.webp`);
    assert.ok(CAST_INFO[name], name);
  }
});

test("a character name from an older cast shows the default one", () => {
  assert.equal(castFor("void"), "void");
  for (const old of ["mochi", "lens", "puff", "ember", "pip", "", undefined]) assert.equal(castFor(old), "blink");
});
