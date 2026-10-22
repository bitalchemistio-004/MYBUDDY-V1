// The status bubble beside the buddy: what it shows for a job (run: node --test tests/js/).
import { test } from "node:test";
import assert from "node:assert/strict";
import { bubbleView, bubbleVisible, stageOf, tileColour, HOLD_DONE_MS, HOLD_FAILED_MS } from "../../shell/ui/js/buddy/bubble.js";

const job = (over = {}) => ({ id: 1, kind: "app", subject: "Sketcher 2", task: "a small house", running: true, phase: "planning",
  act: "write", progress: null, step: 1, total: 6, doing: "writing the script", last: "", ok: null, outcome: "", ...over });

test("no job, no bubble", () => {
  assert.equal(bubbleView(null), null);
  assert.equal(bubbleVisible(null, null, 0), false);
});

test("a job in an app: its name, the task, what it is doing now", () => {
  const v = bubbleView(job({ icon: "/ui/app-icon?name=Sketcher%202" }));
  assert.equal(v.label, "Sketcher 2", "the app's name, not a verb that pushes it out of view");
  assert.equal(v.title, "a small house");
  assert.equal(v.thought, "writing the script");
  assert.equal(v.letter, "S");
  assert.equal(v.icon, "/ui/app-icon?name=Sketcher%202");
  assert.equal(v.typing, true);
  assert.equal(v.stage, 0);
});

test("progress that is not known is never shown as a number", () => {
  const v = bubbleView(job());
  assert.equal(v.percent, null);
  assert.equal(v.shimmer, true);
  assert.equal(v.fill, 0);
  assert.equal(v.tries, "", "the first try needs no label");
  assert.equal(bubbleView(job({ step: 3 })).tries, "try 3/6", "tries are said as tries, not as a percent");
  assert.equal(bubbleView(job({ step: 3, total: 0 })).tries, "try 3");
});

test("progress the job really knows is shown", () => {
  const v = bubbleView(job({ kind: "learn", subject: "origami", task: "learn origami", progress: 0.5, phase: "executing" }));
  assert.equal(v.label, "Learning");
  assert.equal(v.title, "origami");
  assert.equal(v.percent, 50);
  assert.equal(v.shimmer, false);
  assert.equal(v.fill, 50);
  assert.equal(v.stage, 1);
  assert.equal(v.learning, true);
});

test("plan, do, check follow the job's phase", () => {
  assert.deepEqual(["requested", "planning", "executing", "observing", "evaluating"].map(stageOf), [0, 0, 1, 2, 2]);
  assert.equal(stageOf("success"), null);
  assert.equal(bubbleView(job({ phase: "evaluating" })).stage, 2);
  assert.equal(bubbleView(job({ phase: "a phase added later" }), 1).stage, 1, "an unknown phase keeps the last segment");
});

test("the ending is the job's own outcome: done fills the bar, failed keeps how far it got", () => {
  const done = bubbleView(job({ running: false, ok: true, phase: "success", outcome: "I made it in Sketcher 2 on the first try." }), 2);
  assert.equal(done.end, "done");
  assert.equal(done.label, "Done \u00b7 Sketcher 2", "an ended job never reads as still going");
  assert.equal(done.thought, "I made it in Sketcher 2 on the first try.");
  assert.deepEqual([done.fill, done.stage, done.typing, done.shimmer], [100, 3, false, false]);
  assert.equal(done.percent, null, "no percent appears at the end for a job that never knew its progress");
  const failed = bubbleView(job({ running: false, ok: false, phase: "failed", outcome: "the roof was missing" }), 1);
  assert.equal(failed.end, "fail");
  assert.equal(failed.label, "Stopped \u00b7 Sketcher 2");
  assert.equal(bubbleView(job({ kind: "learn", running: false, ok: false })).label, "Couldn't learn");
  assert.equal(failed.thought, "the roof was missing");
  assert.equal(failed.stage, 1);
});

test("an ended job stays a moment and then fades; one that ended before the window opened never shows", () => {
  const running = bubbleView(job());
  assert.equal(bubbleVisible(running, null, 0), true);
  const done = bubbleView(job({ running: false, ok: true }));
  assert.equal(bubbleVisible(done, null, 1000), false);
  assert.equal(bubbleVisible(done, 1000, 1000 + HOLD_DONE_MS - 1), true);
  assert.equal(bubbleVisible(done, 1000, 1000 + HOLD_DONE_MS), false);
  const failed = bubbleView(job({ running: false, ok: false }));
  assert.equal(bubbleVisible(failed, 1000, 1000 + HOLD_DONE_MS), true, "a failure is read more slowly");
  assert.equal(bubbleVisible(failed, 1000, 1000 + HOLD_FAILED_MS), false);
});

test("an app's letter tile keeps one colour", () => {
  assert.equal(tileColour("Sketcher 2"), tileColour("Sketcher 2"));
  assert.match(tileColour("anything"), /^hsl\(\d+ 32% 42%\)$/);
});
