import { test } from "node:test";
import fs from "node:fs";
import assert from "node:assert/strict";
import { PIXEL_IDS } from "../js/buddy/wardrobe-pixel.js";
import { solve, audit, hides, makeMask, ART, HIDE } from "../js/buddy/wardrobe-audit.js";
import { GEOM, ITEM_FIT, SOCK, placements } from "../js/buddy/wardrobe-fit.js";

const MASKS = JSON.parse(fs.readFileSync(new URL("./fixtures/character-masks.json", import.meta.url), "utf8"));   // where each buddy's body and eyes are (tools/make_character_masks.py)
const chars = Object.keys(GEOM);
const geomOf = (c) => ({ ...GEOM[c], mask: makeMask(MASKS[c]) });
const keysOf = (c) => Object.keys(ITEM_FIT[c]);
const table = (c) => Object.fromEntries(keysOf(c).map((k) => { const f = ITEM_FIT[c][k]; return [k, { s: f[0], dx: f[1], dy: f[2] }]; }));

test("every item of every character has a fit, and between them they are the whole wardrobe", () => {
  const all = chars.flatMap(keysOf);
  assert.equal(new Set(all).size, all.length, "an item is in two characters' tables");
  assert.deepEqual([...all].sort(), [...PIXEL_IDS].sort());
});

test("the fit table is what the rules produce (it cannot drift from the drawings)", () => {
  for (const c of chars) for (const k of keysOf(c)) {
    const want = solve(geomOf(c), SOCK[c], k);
    assert.deepEqual(ITEM_FIT[c][k], [want.s, want.dx, want.dy], `${c} ${k}: regenerate the table`);
  }
});

test("every item of every character stays inside the picture and no outfit has two items touching", () => {
  let outfits = 0;
  for (const c of chars) {
    const r = audit(geomOf(c), SOCK[c], keysOf(c), table(c));
    assert.deepEqual(r.problems, [], c);
    outfits += r.outfits;
  }
  assert.ok(outfits >= 1848);                                   // Blink's 8 x 7 x 5 x 6 = 1,680, and every outfit of the other four
});

test("a crown sits on the head, eyes are centred on the face, a hand stays beside it", () => {
  const p = placements("blink", { crown: "crown", eyes: "glasses", hand: "coffee" }, "app");
  const crown = p.find((x) => x.slot === "crown"), eyes = p.find((x) => x.slot === "eyes"), hand = p.find((x) => x.slot === "hand");
  assert.ok(crown.y > SOCK.blink.crown[1]);                     // nudged down onto the head
  assert.ok(Math.abs(eyes.y - (65 + 94 / 2)) <= 8);              // on the eyes (the middle of the eyes box, nudged only to keep clear of them)
  assert.ok(hand.x >= GEOM.blink.eyes[0] + GEOM.blink.eyes[2] - 40);   // right of the face
  assert.ok(ART.margin >= 3);
});

test("a broken rule is caught: an oversized crown would shrink or leave the picture", () => {
  const bad = table("blink");
  bad["crown-crown"] = { s: 1, dx: 200, dy: 0 };
  assert.ok(audit(geomOf("blink"), SOCK.blink, keysOf("blink"), bad).problems.some((x) => x.includes("crown-crown")));
});

test("a headset rests its band on the head and lets the ear cups hang down the sides", () => {
  const items = { "crown-headset": { w: 20, h: 14, ox: 10, oy: 2, rest: 3, draw(g) { g.rect(2, 0, 16, 3, "m"); g.rect(0, 6, 4, 8, "m"); g.rect(16, 6, 4, 8, "m"); } } };
  const g = GEOM.blink, f = solve(g, SOCK.blink, "crown-headset", items), plain = solve(g, SOCK.blink, "crown-headset", { "crown-headset": { ...items["crown-headset"], rest: undefined } });
  assert.ok(f.dy > plain.dy, "with cups hanging, the band sits lower (on the head) than when the whole item has to rest on it");
});

test("no decoration hides the buddy: every item and every outfit leaves the body and the eyes in view", () => {
  for (const c of chars) {
    const g = geomOf(c);
    for (const k of keysOf(c)) {
      if (!["crown", "eyes", "neck", "hand"].includes(k.split("-")[0])) continue;
      const f = ITEM_FIT[c][k], h = hides(k, { s: f[0], dx: f[1], dy: f[2] }, SOCK[c], undefined, g.mask);
      assert.ok(h.body <= HIDE.itemBody, `${c} ${k} hides ${Math.round(h.body * 100)} % of the body`);
      assert.ok(h.eyes <= HIDE.itemEyes, `${c} ${k} hides ${Math.round(h.eyes * 100)} % of the eyes`);
    }
  }
});

test("the audit catches a decoration that covers the eyes (opaque shades dropped straight onto them)", () => {
  const g = geomOf("blink"), bad = table("blink");
  bad["eyes-scan"] = { s: 1, dx: 0, dy: 112 - SOCK.blink.eyes[1] };      // a full-size scan bar across the eyes
  const items = { "eyes-scan": { w: 50, h: 14, ox: 25, oy: 7, draw(gr) { gr.rect(0, 0, 50, 14, "m"); } } };
  assert.ok(hides("eyes-scan", bad["eyes-scan"], SOCK.blink, items, g.mask).eyes > HIDE.itemEyes);
});

test("smoked glass lets the buddy show through: a solid lens hides the eyes, a smoked one does not", () => {
  const g = geomOf("blink"), fit = { s: 0.7, dx: 0, dy: 112 - SOCK.blink.eyes[1] };
  const solid = { "eyes-x": { w: 48, h: 16, ox: 24, oy: 8, draw(gr) { gr.rect(0, 0, 48, 16, "m"); } } }, smoked = { "eyes-x": { w: 48, h: 16, ox: 24, oy: 8, draw(gr) { gr.rect(0, 0, 48, 16, "z"); } } };
  assert.ok(hides("eyes-x", fit, SOCK.blink, solid, g.mask).eyes > 0.5);
  assert.ok(hides("eyes-x", fit, SOCK.blink, smoked, g.mask).eyes < 0.1);
});
