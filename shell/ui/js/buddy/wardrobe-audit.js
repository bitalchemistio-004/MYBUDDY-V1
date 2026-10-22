// The fit rules for decorations, as code. Given a character's geometry (the eyes box, the body box, where the head begins) and
// the sockets, it works out for each pixel item the size and nudge that put it in its own zone, and checks every outfit.
// Pure: used by the table in wardrobe-fit.js (a test keeps the table in step) and by shell/ui/tests/wardrobe-audit.test.mjs.
//
// Zones (art is 320 x 240):
//   crown  rests on the head: its bottom edge sits a little into the head, never wider than 86 % of the body
//   eyes   centred on the face, inside 88 % of the eyes box in both directions
//   neck   hangs from just under the face, never wider than 90 % of the body
//   hand   beside the face (right of the eyes box), never across it
//   back, aura   anywhere, but inside the picture
// Nothing may leave the picture (3 units of margin), and no two of crown, eyes, neck and hand may touch.
//
// A decoration must not hide the buddy. With the buddy's mask (geom.mask, from tests/fixtures/character-masks.json: where its body and its
// eyes are) the rules also measure how much of the body and of the eyes a decoration really covers, counting only opaque pixels
// (smoked glass, tone "z", lets the buddy show through): the eyes items are placed where they cover the least, and an item or an outfit that
// hides too much is reported.
import { pixelExtent, pixelRects, PX, PIXEL_ITEMS } from "./wardrobe-pixel.js";

export const ART = { w: 320, h: 240, margin: 3 };
// the most of the buddy a decoration may hide (fractions of its opaque body pixels and of its bright eye pixels)
export const HIDE = { itemBody: 0.3, itemEyes: 0.25, outfitBody: 0.45, outfitEyes: 0.3 };

/** A mask for the audit from the fixture's runs ([y, x0, x1]): {body, eyes (Uint8Array of 320 x 240), nb, ne}. */
export function makeMask(entry) {
  const mk = (runs) => { const m = new Uint8Array(ART.w * ART.h); for (const [y, x0, x1] of runs) for (let x = x0; x < x1; x++) m[y * ART.w + x] = 1; return m; };
  const body = mk(entry.body), eyes = mk(entry.eyes);
  return { body, eyes, nb: body.reduce((a, b) => a + b, 0), ne: eyes.reduce((a, b) => a + b, 0) };
}

/** The cells (indexes into the 320 x 240 picture) an item covers with opaque pixels once placed. */
export function opaqueCells(key, fit, socks, items) {
  const r = pixelRects(key, items), [sx, sy, k0] = socks[slotOf(key)], s = PX * k0 * fit.s, cells = new Set();
  for (const run of r.runs) {
    if (run.ch === "z" || run.ch === "c") continue;        // smoked and clear glass let the buddy show through
    const x0 = Math.round(sx + fit.dx + (run.x - r.ox) * s), x1 = Math.round(sx + fit.dx + (run.x + run.w - r.ox) * s);
    const y0 = Math.round(sy + fit.dy + (run.y - r.oy) * s), y1 = Math.round(sy + fit.dy + (run.y + 1 - r.oy) * s);
    for (let y = Math.max(0, y0); y < Math.min(ART.h, y1); y++) for (let x = Math.max(0, x0); x < Math.min(ART.w, x1); x++) cells.add(y * ART.w + x);
  }
  return cells;
}
const hidden = (cells, mask) => { let b = 0, e = 0; for (const i of cells) { if (mask.body[i]) b++; if (mask.eyes[i]) e++; } return { body: b / mask.nb, eyes: e / Math.max(1, mask.ne) }; };
/** How much of the buddy this one item hides: {body, eyes} as fractions. */
export function hides(key, fit, socks, items, mask) { return hidden(opaqueCells(key, fit, socks, items), mask); }

export const slotOf = (key) => key.split("-")[0];

/** The size factor and nudge for one item: {s, dx, dy} (the socket's own size and place are the starting point). */
export function solve(geom, socks, key, items) {
  const { eyes: E, body: B, head } = geom, slot = slotOf(key), [sx, sy, k0] = socks[slot], ex = pixelExtent(key, items);
  const { w: W, h: H, margin: M } = ART, w = (ex.right - ex.left) * k0, h = (ex.bottom - ex.top) * k0;
  let s = 1, dx = 0, dy = 0;
  const box = () => ({ l: sx + dx + ex.left * k0 * s, t: sy + dy + ex.top * k0 * s, r: sx + dx + ex.right * k0 * s, b: sy + dy + ex.bottom * k0 * s });
  if (slot === "crown") {
    const sit = head + 6, rest = ex.rest ?? ex.bottom;       // what hangs below `rest` (ear cups) is over the sides of the head, not on it
    s = Math.min(1, (0.86 * B[2]) / w, (sit - M) / h);
    dy = sit - (sy + rest * k0 * s);
  } else if (slot === "eyes") {
    const half = Math.max(-ex.top, ex.bottom) * k0;
    s = Math.min(1, (0.88 * E[2]) / w, (0.44 * E[3]) / half);
    dy = E[1] + E[3] / 2 - sy;
    if (geom.mask) {
      // Search a little around that: a frame is sized to sit around the eyes, a brow goes above them, a blush below, and nothing is left
      // across them. Smaller moves and sizes closest to the nominal one win a tie.
      const s0 = s, dx0 = E[0] + E[2] / 2 - sx, dy0 = dy, on = !!(items || PIXEL_ITEMS)[key]?.on;      // `on`: worn over the eyes, so it stays on them
      let best = null;
      for (let f = 0.8; f <= 1.31; f += 0.04) {
        const sc = s0 * f;
        if (w * sc > E[2] || (ex.bottom - ex.top) * k0 * sc > E[3] * 1.25) continue;
        for (let ddx = -14; ddx <= 14; ddx += 2) for (let ddy = -36; ddy <= 36; ddy += 2) {
          const fit = { s: +sc.toFixed(3), dx: +(dx0 + ddx).toFixed(1), dy: +(dy0 + ddy).toFixed(1) };
          const b = boxOf(socks, key, fit, items);
          if (b.l < E[0] || b.r > E[0] + E[2] - 1 || b.t < head + 7 || b.b > E[1] + E[3] - 1) continue;      // between the crown's seat and the neck's, and left of the hand
          const h2 = hides(key, fit, socks, items, geom.mask);
          const score = 100 * h2.eyes + 15 * h2.body + (on ? 3 : 0.12) * Math.abs(ddy) + (on ? 0.4 : 0.06) * Math.abs(ddx) + (f < 1 ? 60 * (1 - f) : 10 * (f - 1));    // shrinking an item to slip past the eyes is the worst answer
          if (!best || score < best.score - 1e-9) best = { score, fit };
        }
      }
      if (best) { s = best.fit.s; dx = best.fit.dx; dy = best.fit.dy; }
    }
  } else if (slot === "neck") {
    const top = E[1] + E[3];
    s = Math.min(1, (0.9 * B[2]) / w, (H - M - top) / h);
    dy = top - (sy + ex.top * k0 * s);
  } else if (slot === "hand") {
    const left = E[0] + E[2];
    s = Math.min(1, (W - M - left) / w, (B[1] + B[3] - M - 2) / h);
    dx = Math.max(0, left - (sx + ex.left * k0 * s));
    const b = box();
    if (b.t < M) dy += M - b.t;
    if (b.r > W - M) dx -= b.r - (W - M);
  } else {
    s = Math.min(1, (W - 2 * M) / w, (H - 2 * M) / h);
    const b = box();
    if (b.l < M) dx += M - b.l;
    if (b.r > W - M) dx -= b.r - (W - M);
    if (b.t < M) dy += M - b.t;
    if (b.b > H - M) dy -= b.b - (H - M);
  }
  return { s: +s.toFixed(3), dx: +dx.toFixed(1), dy: +dy.toFixed(1) };
}

/** The box an item covers once placed: {l, t, r, b} in art units. */
export function boxOf(socks, key, fit, items) {
  const ex = pixelExtent(key, items), [sx, sy, k0] = socks[slotOf(key)];
  const hang = slotOf(key) === "crown" && ex.rest !== undefined;       // ear cups hang over the sides of the head: only the band counts as "on the head"
  return { l: sx + fit.dx + ex.left * k0 * fit.s, t: sy + fit.dy + ex.top * k0 * fit.s, r: sx + fit.dx + ex.right * k0 * fit.s, b: sy + fit.dy + (hang ? ex.rest : ex.bottom) * k0 * fit.s };
}

/** What is wrong with a table of fits: items that leave the picture or shrink too far, and outfits whose items touch. */
export function audit(geom, socks, keys, table, items) {
  const problems = [], { w: W, h: H, margin: M } = ART, hit = (a, b) => a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b;
  for (const key of keys) {
    const f = table[key], b = boxOf(socks, key, f, items);
    if (b.l < M - 0.5 || b.t < M - 0.5 || b.r > W - M + 0.5 || b.b > H - M + 0.5) problems.push(`${key} leaves the picture`);
    if (f.s < 0.5) problems.push(`${key} shrinks to ${f.s}`);
  }
  const FRONT = new Set(["crown", "eyes", "neck", "hand"]);
  const cells = {};
  if (geom.mask) {
    for (const key of keys) {
      if (!FRONT.has(slotOf(key))) continue;
      cells[key] = opaqueCells(key, table[key], socks, items);
      const h = hidden(cells[key], geom.mask);
      if (h.body > HIDE.itemBody) problems.push(`${key} hides ${Math.round(h.body * 100)} % of the body`);
      if (h.eyes > HIDE.itemEyes) problems.push(`${key} hides ${Math.round(h.eyes * 100)} % of the eyes`);
    }
  }
  const by = (slot) => keys.filter((k) => slotOf(k) === slot);
  let outfits = 0;
  for (const cr of by("crown")) for (const ey of by("eyes")) for (const ne of by("neck")) for (const ha of by("hand")) {
    outfits++;
    const set = [cr, ey, ne, ha], boxes = set.map((k) => boxOf(socks, k, table[k], items));
    for (let i = 0; i < 4; i++) for (let j = i + 1; j < 4; j++) if (hit(boxes[i], boxes[j])) problems.push(`${set[i]} touches ${set[j]}`);
    if (geom.mask) {
      const all = new Set(); for (const k of set) for (const c of cells[k]) all.add(c);
      const h = hidden(all, geom.mask);
      if (h.body > HIDE.outfitBody) problems.push(`${set.join(" + ")} hides ${Math.round(h.body * 100)} % of the body`);
      if (h.eyes > HIDE.outfitEyes) problems.push(`${set.join(" + ")} hides ${Math.round(h.eyes * 100)} % of the eyes`);
    }
  }
  return { problems: [...new Set(problems)], outfits };
}
