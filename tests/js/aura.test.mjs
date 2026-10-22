// The aura around the buddy: which effect a clip gets (run: node --test tests/js/).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { AURA, auraFor, auraColour, HOT_COLOUR } from "../../shell/ui/js/buddy/aura.js";

test("work, listening, music, anger, needing you and finishing each have their effect", () => {
  for (const clip of ["think", "search", "write"]) assert.equal(auraFor(clip), "work", clip);
  assert.equal(auraFor("listen"), "wave");
  assert.equal(auraFor("music"), "music");
  assert.equal(auraFor("furious"), "mad");
  assert.equal(auraFor("bell"), "alert");
  assert.equal(auraFor("done"), "burst");
});

test("a calm clip, and a clip the art adds later, have no effect", () => {
  for (const clip of ["idle", "sleep", "bored", "juggle", undefined]) assert.equal(auraFor(clip), "");
});

test("every clip with an effect is one the art has", () => {
  const drawn = JSON.parse(readFileSync(new URL("../../shell/ui/assets/buddy/chars/blink/clips.json", import.meta.url))).clips;
  for (const clip of Object.keys(AURA)) assert.ok(drawn[clip], clip);
});

test("anger and heat glow red; everything else takes the character's colour", () => {
  assert.equal(auraColour("furious", "#19e6ff"), HOT_COLOUR);
  assert.equal(auraColour("hot", "#19e6ff"), HOT_COLOUR);
  assert.equal(auraColour("think", "#19e6ff"), "#19e6ff");
});
