import { test } from "node:test";
import assert from "node:assert/strict";
import { Grid, PIXEL_ITEMS, PIXEL_IDS, pixelRects, pixelGroup, PX } from "../js/buddy/wardrobe-pixel.js";

test("every pixel item draws something inside its own grid, around its socket", () => {
  for (const id of PIXEL_IDS) {
    const r = pixelRects(id);
    assert.ok(r.runs.length > 3, id);
    for (const run of r.runs) {
      assert.ok(run.x >= 0 && run.x + run.w <= r.w && run.y >= 0 && run.y < r.h, `${id} leaves its grid`);
      assert.match(run.ch, /^[kDdmlLwgGhTzc]$/, `${id} uses an unknown tone ${run.ch}`);
    }
    assert.ok(r.ox >= 0 && r.ox <= r.w && r.oy >= 0 && r.oy <= r.h, `${id} socket outside the grid`);
  }
});

test("an item is outlined in k, except the auras that float free", () => {
  assert.ok(pixelRects("crown-crown").runs.some((r) => r.ch === "k"));
  assert.ok(!pixelRects("aura-hearts").runs.some((r) => r.ch === "k"));
});

test("a group is plain rectangles of one pixel height, placed around the socket", () => {
  const svg = pixelGroup("crown-crown");
  assert.match(svg, /^<g id="wdp-crown-crown">/);
  assert.ok(svg.includes("var(--wd-ink"));                       // the item's colour is the one it is given
  assert.equal((svg.match(/height="3"/g) || []).length, pixelRects("crown-crown").runs.length);
  assert.ok(/x="-\d+"/.test(svg) && /y="-\d+"/.test(svg));     // some pixels are left of and above the socket
  assert.equal(PX, 3);
});

test("the grid draws lines, discs and outlines as expected", () => {
  const g = Grid(8, 8).line(0, 0, 7, 7, "m").disc(4, 4, 1, "d");
  assert.equal(g.get(3, 3), "m"); assert.equal(g.get(4, 4), "d"); assert.equal(g.get(7, 0), "");
  g.outline("k");
  assert.equal(g.get(1, 0), "k");
  assert.equal(g.runs().every((r) => r.w >= 1), true);
});

test("the item list is the whole wardrobe: 94 items (Blink 36, the other four 58), six places", () => {
  assert.equal(PIXEL_IDS.length, Object.keys(PIXEL_ITEMS).length);
  assert.equal(PIXEL_IDS.length, 94);
  for (const slot of ["crown", "eyes", "neck", "back", "hand", "aura"]) assert.ok(PIXEL_IDS.some((k) => k.startsWith(slot + "-")), slot);
  assert.ok(PIXEL_IDS.includes("hand-coffee") && PIXEL_IDS.includes("back-wings"));
});

test("a mirrored item is symmetrical", () => {
  const g = Grid(10, 4).rect(0, 0, 2, 4, "m").mirror();
  for (let y = 0; y < 4; y++) assert.equal(g.get(0, y), g.get(9, y));
  assert.equal(g.get(4, 0), "");
});
