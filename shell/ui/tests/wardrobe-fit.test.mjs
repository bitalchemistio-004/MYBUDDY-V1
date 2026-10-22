import { test } from "node:test";
import assert from "node:assert/strict";
import { placements, cropFor, SOCK } from "../js/buddy/wardrobe-fit.js";
import { pixelExtent } from "../js/buddy/wardrobe-pixel.js";

const EYES = [63, 65, 194, 94];

test("nothing is drawn for no outfit or for a character that does not exist", () => {
  assert.deepEqual(placements("nobody", { crown: "halo" }, "room"), []);
  assert.deepEqual(placements("blink", null, "room"), []);
});

test("every character is dressed from its own sockets", () => {
  const p = placements("void", { crown: "accretion", eyes: "lens" }, "room", { styles: { crown: "pixel", eyes: "pixel" } });
  assert.deepEqual(p.map((x) => x.slot), ["eyes", "crown"]);
  assert.ok(p.every((x) => x.pixel), "the other four are pixel art");
  assert.ok(p.find((x) => x.slot === "crown").y < 80, "a crown sits near the top of Void's art, not on Blink's head");
});

test("an item sits on its socket and keeps the order back, then front", () => {
  const p = placements("blink", { crown: "crown", back: "cape", eyes: "glasses" }, "app");
  assert.deepEqual(p.map((x) => x.slot), ["back", "eyes", "crown"]);
  assert.ok(p.find((x) => x.slot === "crown").y >= SOCK.blink.crown[1]);   // on its socket, nudged onto the head
  assert.equal(p.find((x) => x.slot === "back").layer, "back");
});

test("a small surface draws only what its crop can show, and fits the crown to it", () => {
  const p = placements("blink", { crown: "crown", neck: "scarf", hand: "coffee", aura: "orbit", eyes: "shades" }, "rest");
  assert.deepEqual(p.map((x) => x.slot).sort(), ["crown", "eyes"]);
  const c = p.find((x) => x.slot === "crown");
  assert.ok(c.k < SOCK.blink.crown[2]);                     // smaller than in the app
  assert.ok(c.y > SOCK.blink.crown[1]);                     // and lowered
});

test("the room nudges tall items so they do not leave the plate", () => {
  const p = placements("blink", { hand: "balloon", neck: "bell" }, "room");
  assert.ok(p.find((x) => x.slot === "hand").y < SOCK.blink.hand[1]);
  assert.ok(p.find((x) => x.slot === "neck").k < SOCK.blink.neck[2]);
});

test("without a crown the crop is untouched", () => {
  assert.deepEqual(cropFor("blink", { eyes: "glasses" }, EYES, 20), { part: EYES, H: 20 });
});

test("a crown grows the crop upward and the face height by at most 14 px", () => {
  const g = cropFor("blink", { crown: "crown" }, EYES, 20);
  assert.ok(g.part[1] < EYES[1]);                          // starts higher
  assert.equal(g.part[1] + g.part[3], EYES[1] + EYES[3]);  // the bottom stays where it was
  assert.ok(g.H > 20 && g.H <= 34);
  const room = cropFor("blink", { crown: "crown" }, EYES, 46);
  assert.equal(room.H, 60);                                 // capped at +14
});

test("an item's own colour wins over the general ink, and an unknown colour falls back", () => {
  const worn = { crown: "crown", eyes: "glasses" };
  const p = placements("blink", worn, "app", { colors: { crown: "rose" }, ink: "mint" });
  assert.equal(p.find((x) => x.slot === "crown").color, "rose");
  assert.equal(p.find((x) => x.slot === "eyes").color, "mint");          // no colour of its own: the general ink
  const q = placements("blink", worn, "app", { colors: { crown: "plaid" }, ink: "nonsense" });
  assert.equal(q.find((x) => x.slot === "crown").color, "char");
});

test("pixel style applies only to what has a pixel drawing, and every catalog item has one", () => {
  const p = placements("blink", { crown: "beanie", neck: "bell", hand: "ghost" }, "app", { styles: { crown: "pixel", neck: "pixel", hand: "pixel" } });
  assert.equal(p.find((x) => x.slot === "crown").pixel, true);
  assert.equal(p.find((x) => x.slot === "neck").pixel, true);
  assert.equal(p.find((x) => x.slot === "hand").pixel, false);           // an unknown item has none
  const line = placements("blink", { crown: "beanie" }, "app", { styles: { crown: "line" } });
  assert.equal(line[0].pixel, false);
});

test("the crop takes the crown that is really drawn, so no crown is ever cut off", () => {
  for (const id of ["crown", "beanie", "party", "wizard", "halo", "sprout", "antenna", "horns"]) {
    for (const surface of ["rest", "peek", "room"]) {
      const g = cropFor("blink", { crown: id }, EYES, 20, surface);
      const c = placements("blink", { crown: id }, surface)[0];
      const ex = pixelExtent(`crown-${id}`), top = c.y + ex.top * c.k;
      assert.ok(g.part[1] <= top, `${id} on ${surface} starts above the crop (${top.toFixed(1)} vs ${g.part[1]})`);
    }
  }
});
