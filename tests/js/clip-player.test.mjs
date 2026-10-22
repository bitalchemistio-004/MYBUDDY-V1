// The sprite clip player, driven by a fake clock (run: node --test tests/js/).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ClipPlayer, parseSheet, parseClips, sequence, LOOPS } from "../../shell/ui/js/buddy/clip-player.js";

// A tiny sheet with known hold times, shaped like an Aseprite json-array export.
function sheet() {
  const durations = [100, 200, 300, 50, 50, 10, 20, 30, 10, 20, 30];
  return {
    frames: durations.map((d, i) => ({ frame: { x: i * 4, y: 0, w: 4, h: 3 }, duration: d })),
    meta: { frameTags: [
      { name: "idle", from: 0, to: 2, direction: "forward" },
      { name: "done", from: 3, to: 4, direction: "forward" },
      { name: "listen", from: 5, to: 7, direction: "reverse" },
      { name: "think", from: 8, to: 10, direction: "pingpong" },
      { name: "love", from: 8, to: 10, direction: "pingpong" },
    ] },
  };
}

function clock() {
  let t = 0;
  return { now: () => t, go: (ms) => { t += ms; } };
}

test("a real character's clips.json parses: every clip a tag, loops as the art says", () => {
  const json = JSON.parse(readFileSync(new URL("../../shell/ui/assets/buddy/chars/blink/clips.json", import.meta.url)));
  const s = parseClips(json);
  assert.deepEqual([...s.tags.keys()], Object.keys(json.clips));
  assert.equal(s.frames.length, Object.values(json.clips).reduce((n, c) => n + c.durations.length, 0));
  assert.deepEqual([s.width, s.height], [json.w, json.h]);
  for (const [name, t] of s.tags) {
    assert.equal(s.loops.has(name), json.clips[name].loop, name);
    for (let i = t.from; i <= t.to; i++) assert.equal(s.frames[i].clip, name);
  }
  const p = new ClipPlayer(s);
  assert.equal(p.name, "idle");
  assert.equal(p.isLoop("music"), true, "the loops come from the art, not from a list in the player");
  assert.equal(p.isLoop("hello"), false);
});

test("frames sit in their own clip's sheet, row by row", () => {
  const s = parseClips({ w: 10, h: 6, clips: {
    idle: { cols: 2, loop: true, durations: [100, 100, 100] },
    done: { cols: 8, loop: false, durations: [50, 0] },
    empty: { cols: 1, loop: false, durations: [] },
  } });
  assert.deepEqual(s.frames.map((f) => [f.clip, f.x, f.y]), [["idle", 0, 0], ["idle", 10, 0], ["idle", 0, 6], ["done", 0, 0], ["done", 10, 0]]);
  assert.deepEqual(s.tags.get("done"), { from: 3, to: 4, direction: "forward" });
  assert.equal(s.tags.has("empty"), false, "a clip with no frames cannot be played");
  assert.equal(s.frames[4].duration, 100, "a zero hold would spin forever");
  const c = clock();
  const p = new ClipPlayer(s, { now: c.now });
  p.play("done");
  c.go(150); p.update();
  assert.equal(p.name, "idle", "a one-shot from the manifest returns to the loop");
});

test("per-frame hold times are honoured", () => {
  const c = clock();
  const p = new ClipPlayer(sheet(), { now: c.now });
  assert.equal(p.name, "idle");
  assert.equal(p.frameIndex, 0);
  c.go(99); assert.equal(p.update(), false); assert.equal(p.frameIndex, 0);
  c.go(1); assert.equal(p.update(), true); assert.equal(p.frameIndex, 1);
  c.go(199); p.update(); assert.equal(p.frameIndex, 1);
  c.go(1); p.update(); assert.equal(p.frameIndex, 2);
  c.go(300); p.update(); assert.equal(p.frameIndex, 0, "a loop wraps to its first frame");
});

test("a long gap on a loop lands on the right frame without spinning", () => {
  const c = clock();
  const p = new ClipPlayer(sheet(), { now: c.now });
  c.go(600 * 1000 + 150);  // exactly 1000 loops of 600 ms, then 150 ms into the clip
  p.update();
  assert.equal(p.frameIndex, 1);
});

test("maxStep caps a stall", () => {
  const c = clock();
  const p = new ClipPlayer(sheet(), { now: c.now, maxStep: 50 });
  c.go(5000); p.update();
  assert.equal(p.frameIndex, 0);
});

test("a one-shot plays once, then returns to the current loop and reports the end", () => {
  const c = clock();
  const ended = [];
  const p = new ClipPlayer(sheet(), { now: c.now, onEnd: (n) => ended.push(n) });
  assert.equal(p.play("done"), true);
  assert.equal(p.frameIndex, 3);
  c.go(50); p.update(); assert.equal(p.frameIndex, 4);
  c.go(50); p.update();
  assert.equal(p.name, "idle");
  assert.equal(p.frameIndex, 0);
  assert.deepEqual(ended, ["done"]);
});

test("a loop asked for during a one-shot waits for it, then plays", () => {
  const c = clock();
  const p = new ClipPlayer(sheet(), { now: c.now });
  p.play("done");
  p.play("listen");
  assert.equal(p.name, "done", "the one-shot is not cut off");
  c.go(100); p.update();
  assert.equal(p.name, "listen");
});

test("asking for the loop already on screen does not restart it", () => {
  const c = clock();
  const p = new ClipPlayer(sheet(), { now: c.now });
  c.go(150); p.update();
  assert.equal(p.frameIndex, 1);
  p.play("idle");
  assert.equal(p.frameIndex, 1);
});

test("reverse and pingpong directions", () => {
  assert.deepEqual(sequence({ from: 5, to: 7, direction: "reverse" }), [7, 6, 5]);
  assert.deepEqual(sequence({ from: 8, to: 10, direction: "pingpong" }), [8, 9, 10, 9]);
  assert.deepEqual(sequence({ from: 8, to: 10, direction: "pingpong" }, true), [8, 9, 10, 9, 8]);
  assert.deepEqual(sequence({ from: 8, to: 10, direction: "pingpong_reverse" }), [10, 9, 8, 9]);
  assert.deepEqual(sequence({ from: 2, to: 2, direction: "pingpong" }), [2]);

  const c = clock();
  const p = new ClipPlayer(sheet(), { now: c.now });
  p.play("listen");
  const seen = [p.frameIndex];
  for (const d of [30, 20, 10]) { c.go(d); p.update(); seen.push(p.frameIndex); }
  assert.deepEqual(seen, [7, 6, 5, 7], "reverse loop plays 7,6,5 and wraps");
});

test("a pingpong one-shot ends on its first frame, then returns", () => {
  const c = clock();
  const p = new ClipPlayer(sheet(), { now: c.now });
  p.play("love");
  const seen = [p.frameIndex];
  for (const d of [10, 20, 30, 20]) { c.go(d); p.update(); seen.push(p.frameIndex); }
  assert.deepEqual(seen, [8, 9, 10, 9, 8]);
  c.go(10); p.update();
  assert.equal(p.name, "idle");
});

test("reduced motion holds the first frame; one-shots still end on time", () => {
  const c = clock();
  const ended = [];
  const p = new ClipPlayer(sheet(), { now: c.now, reducedMotion: true, onEnd: (n) => ended.push(n) });
  c.go(250); assert.equal(p.update(), false); assert.equal(p.frameIndex, 0);
  p.play("done");
  assert.equal(p.frameIndex, 3);
  c.go(60); p.update(); assert.equal(p.frameIndex, 3, "no motion inside the clip");
  c.go(40); p.update();
  assert.equal(p.name, "idle");
  assert.deepEqual(ended, ["done"]);
  p.setReducedMotion(false);
  c.go(100); p.update(); assert.equal(p.frameIndex, 1);
});

test("unknown clips are a soft no-op", () => {
  const c = clock();
  const p = new ClipPlayer(sheet(), { now: c.now });
  assert.equal(p.play("dance"), false);
  assert.equal(p.name, "idle");
});

test("without a loop to return to, a one-shot rests on its last frame", () => {
  const c = clock();
  const json = sheet();
  json.meta.frameTags = json.meta.frameTags.filter((t) => t.name === "done");
  const ended = [];
  const p = new ClipPlayer(json, { now: c.now, onEnd: (n) => ended.push(n) });
  assert.equal(p.name, null);
  p.play("done");
  c.go(1000); p.update(); c.go(1000); p.update();
  assert.equal(p.frameIndex, 4);
  assert.deepEqual(ended, ["done"], "the end is reported once");
});

test("the default loops are the four base states", () => {
  assert.deepEqual([...LOOPS].sort(), ["idle", "listen", "sleep", "think"]);
});
