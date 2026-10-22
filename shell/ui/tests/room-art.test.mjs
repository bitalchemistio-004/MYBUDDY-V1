import { test } from "node:test";
import assert from "node:assert/strict";
import { ST, PAL, PLACES, roomSVG, roomBox, roomTier, roomSum, SHOP_ITEMS } from "../js/room/art.js";

const chars = Object.keys(PAL);
const setup = (lv, owned = {}) => {
  for (const c of chars) { ST.levels[c] = Object.fromEntries(PLACES.map((p) => [p, lv])); ST.owned[c] = { ...owned }; ST.layout[c] = {}; }
  ST.preview = 0; ST.at = null; ST.eggs = {}; ST.sprints = 11;
};

test("every character's room draws at every level, with everything bought, without a broken value", () => {
  const all = Object.fromEntries(SHOP_ITEMS.map((i) => [i, true]));
  for (const lv of [1, 2, 3, 4, 5]) {
    setup(lv, all);
    for (const c of chars) {
      const svg = roomSVG(c, null);
      assert.ok(svg.startsWith("<svg") && svg.includes("<rect"), `${c} level ${lv}`);
      assert.ok(!/undefined|NaN|\[object/.test(svg), `${c} level ${lv} has a broken value`);
    }
  }
});

test("the room grows in five tiers with the total of the levels", () => {
  setup(1); assert.equal(roomTier("blink"), 1);
  setup(2); assert.equal(roomSum("blink"), 18); assert.equal(roomTier("blink"), 2);
  setup(3); assert.equal(roomTier("blink"), 3);
  setup(4); assert.equal(roomTier("blink"), 4);
  setup(5); assert.equal(roomTier("blink"), 5);
});

test("every place sits inside the room at every level, and a higher level never draws fewer pixels", () => {
  for (const c of chars) for (const p of PLACES) {
    let last = 0;
    for (const lv of [1, 2, 3, 4, 5]) {
      const b = roomBox(c, p, lv);
      assert.ok(b.x >= 0 && b.x + b.w <= 880, `${c} ${p} ${lv} is outside the width`);
      assert.ok(b.y >= 0 && b.y + b.h <= 500, `${c} ${p} ${lv} is outside the height`);
      const px = b.g.runs().reduce((a, r) => a + r.w, 0);
      assert.ok(px >= last * 0.9, `${c} ${p} level ${lv} lost detail`);
      last = px;
    }
  }
});
