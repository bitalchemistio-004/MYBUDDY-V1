// The buddy window is exactly what is drawn (run: node --test tests/js/).
import { test } from "node:test";
import assert from "node:assert/strict";
import { sizeFor, PAD } from "../../shell/ui/js/buddy/fit.js";

// Boxes as the page lays them out from the character's corner: the stage
// PAD from the right and bottom, a bubble 10 px further in.
function right(view, stage, bubble = null) {
  const s = { left: view.width - PAD - stage.w, top: view.height - PAD - stage.h, width: stage.w, height: stage.h };
  const b = bubble && { left: s.left - 10 - bubble.w, top: view.height - PAD - bubble.h, width: bubble.w, height: bubble.h };
  return [s, b];
}

test("alone, the window is the character plus the pad on every side", () => {
  const view = { width: 188, height: 148 };
  assert.deepEqual(sizeFor(right(view, { w: 160, h: 120 }), "right", view), { w: 160 + 2 * PAD, h: 120 + 2 * PAD });
});

test("the size does not depend on how large the window is at the moment", () => {
  for (const view of [{ width: 188, height: 148 }, { width: 900, height: 700 }, { width: 60, height: 40 }]) {
    assert.deepEqual(sizeFor(right(view, { w: 160, h: 120 }, { w: 250, h: 110 }), "right", view),
      { w: PAD + 160 + 10 + 250 + PAD, h: 120 + 2 * PAD }, JSON.stringify(view));
  }
});

test("a bubble taller than the character makes the window taller, not the character lower", () => {
  const view = { width: 448, height: 148 };
  assert.deepEqual(sizeFor(right(view, { w: 160, h: 120 }, { w: 250, h: 190 }), "right", view), { w: 448, h: 190 + 2 * PAD });
});

test("on the left the window grows to the right", () => {
  const view = { width: 188, height: 148 };
  const stage = { left: PAD, top: view.height - PAD - 120, width: 160, height: 120 };
  const bubble = { left: PAD + 160 + 10, top: view.height - PAD - 100, width: 250, height: 100 };
  assert.deepEqual(sizeFor([stage], "left", view), { w: 188, h: 148 });
  assert.deepEqual(sizeFor([stage, bubble], "left", view), { w: PAD + 160 + 10 + 250 + PAD, h: 148 });
});

test("a box that is not shown takes no room", () => {
  const view = { width: 188, height: 148 };
  const [stage] = right(view, { w: 160, h: 120 });
  assert.deepEqual(sizeFor([stage, null, { left: 0, top: 0, width: 0, height: 0 }], "right", view), { w: 188, h: 148 });
});

test("the pad in the script is the pad in the stylesheet", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../shell/ui/css/buddy-sprite.css", import.meta.url), "utf8");
  assert.match(css, new RegExp(`--pad: ${PAD}px`));
});
