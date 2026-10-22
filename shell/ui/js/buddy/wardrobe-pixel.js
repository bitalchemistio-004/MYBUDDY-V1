// Pixel-art versions of the decorations. Each is a small grid of square pixels (3 art units each), drawn with a light from
// the top left. The tones come from one colour (the item's own), and follow the pixel-art rules we adopted
// (the project pixel-art rules): crisp pixels, a small fixed ramp, no pure black and no pure white, shadows pushed toward
// violet and highlights toward warm cream, one tinted outline. k outline, D deep shadow, d shadow, m the colour, l light,
// L bright, w near white; g, G and h are gold (shadow, base, highlight). T twinkles.
// Pure: no document, so the shapes can be read and tested alone (shell/ui/tests/wardrobe-pixel.test.mjs).

export const PX = 3;

export function Grid(w, h) {
  const c = Array.from({ length: h }, () => Array(w).fill(""));
  const g = {
    w, h, c,
    set(x, y, ch) { x = Math.round(x); y = Math.round(y); if (x >= 0 && y >= 0 && x < w && y < h) c[y][x] = ch; return g; },
    get(x, y) { return x >= 0 && y >= 0 && x < w && y < h ? c[y][x] : ""; },
    rect(x, y, rw, rh, ch) { for (let j = 0; j < rh; j++) for (let i = 0; i < rw; i++) g.set(x + i, y + j, ch); return g; },
    hline(x, y, n, ch) { return g.rect(x, y, n, 1, ch); },
    vline(x, y, n, ch) { return g.rect(x, y, 1, n, ch); },
    line(x0, y0, x1, y1, ch) {
      x0 = Math.round(x0); y0 = Math.round(y0); x1 = Math.round(x1); y1 = Math.round(y1);
      const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
      let err = dx + dy;
      for (;;) {
        g.set(x0, y0, ch);
        if (x0 === x1 && y0 === y1) break;
        const e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
      }
      return g;
    },
    disc(cx, cy, r, ch) { for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) if ((i - cx) ** 2 + (j - cy) ** 2 <= r * r + r * 0.6) g.set(i, j, ch); return g; },
    ring(cx, cy, r, ch) { for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) { const d = Math.hypot(i - cx, j - cy); if (d <= r + 0.5 && d > r - 1.0) g.set(i, j, ch); } return g; },
    ellipse(cx, cy, rx, ry, ch) { for (let a = 0; a < 6.3; a += 0.04) g.set(cx + rx * Math.cos(a), cy + ry * Math.sin(a), ch); return g; },
    fillEllipse(cx, cy, rx, ry, ch) { for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) if (((i - cx) / rx) ** 2 + ((j - cy) / ry) ** 2 <= 1) g.set(i, j, ch); return g; },
    // an isosceles triangle: apex at (cx, top), growing one pixel each side every `step` rows, for `rows` rows
    tri(cx, top, rows, step, ch) { for (let y = 0; y < rows; y++) { const hw = Math.floor(y / step) + 1; g.rect(cx - hw, top + y, hw * 2, 1, ch); } return g; },
    // a checkerboard of two tones over a rectangle: the pixel-art way to a soft gradient
    dither(x, y, rw, rh, a, b, phase = 0) { for (let j = 0; j < rh; j++) for (let i = 0; i < rw; i++) g.set(x + i, y + j, (i + j + phase) % 2 ? a : b); return g; },
    // copy the left half over the right half, mirrored, so a symmetrical item is drawn once
    mirror() { const half = Math.floor(w / 2); for (let j = 0; j < h; j++) for (let i = 0; i < half; i++) if (c[j][i]) c[j][w - 1 - i] = c[j][i]; return g; },
    clearBelow(y) { for (let j = y; j < h; j++) for (let i = 0; i < w; i++) c[j][i] = ""; return g; },
    stamp(x, y, rows, map = {}) { rows.forEach((row, j) => [...row].forEach((k, i) => { if (k !== ".") g.set(x + i, y + j, map[k] || k); })); return g; },
    // Light from the top left: an edge of the colour that faces the light gets the light tone, the opposite edge the shadow
    // tone. Only thick parts are shaded (three pixels or more), so a thin line stays one clean colour.
    shade() {
      const src = c.map((r) => r.slice()), at = (x, y) => (y >= 0 && y < h && x >= 0 && x < w ? src[y][x] : "");
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        if (src[y][x] !== "m") continue;
        const lit = (!at(x, y - 1) && at(x, y + 1) && at(x, y + 2)) || (!at(x - 1, y) && at(x + 1, y) && at(x + 2, y));
        const dark = (!at(x, y + 1) && at(x, y - 1) && at(x, y - 2)) || (!at(x + 1, y) && at(x - 1, y) && at(x - 2, y));
        const top = !at(x, y - 1) && at(x, y + 1) && at(x, y + 2), left = !at(x - 1, y) && at(x + 1, y) && at(x + 2, y);
        const bot = !at(x, y + 1) && at(x, y - 1) && at(x, y - 2), right = !at(x + 1, y) && at(x - 1, y) && at(x - 2, y);
        if (lit && !dark) c[y][x] = top && left ? "L" : "l";
        else if (dark && !lit) c[y][x] = bot && right ? "D" : "d";
      }
      return g;
    },
    outline(ch) {
      const add = [];
      for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) if (!c[j][i] && (g.get(i - 1, j) || g.get(i + 1, j) || g.get(i, j - 1) || g.get(i, j + 1))) add.push([i, j]);
      add.forEach(([i, j]) => g.set(i, j, ch));
      return g;
    },
    // horizontal runs of one tone: [{x, y, w, ch}], so a picture is a few dozen rectangles, not hundreds
    runs() {
      const out = [];
      for (let j = 0; j < h; j++) {
        let i = 0;
        while (i < w) {
          const ch = c[j][i];
          if (!ch) { i++; continue; }
          let n = 1;
          while (i + n < w && c[j][i + n] === ch) n++;
          out.push({ x: i, y: j, w: n, ch });
          i += n;
        }
      }
      return out;
    },
    // the smallest rectangle that holds every drawn pixel: {x0, y0, x1, y1} in pixels (x1, y1 exclusive), or null when empty
    bounds() {
      let x0 = w, y0 = h, x1 = -1, y1 = -1;
      for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) if (c[j][i]) { x0 = Math.min(x0, i); y0 = Math.min(y0, j); x1 = Math.max(x1, i + 1); y1 = Math.max(y1, j + 1); }
      return x1 < 0 ? null : { x0, y0, x1, y1 };
    },
  };
  return g;
}

const HEART = [".mm.mm.", "mmmmmmm", "mmmmmmm", ".mmmmm.", "..mmm..", "...m..."];
const PLUS = [".w.", "www", ".w."];
const STAR4 = ["..w..", "..w..", "wwWww", "..w..", "..w.."];
const NOTE = ["..mmm", "..mmm", "..m.m", "..m..", "..m..", "mmm..", "mmm.."];

// key = "<slot>-<item>", as in core/wardrobe.py. w, h in pixels; (ox, oy) is the socket inside the grid.
export const PIXEL_ITEMS = {
  // ---- crown ----
  "crown-crown": { w: 30, h: 20, ox: 15, oy: 20, draw(g) {
    g.rect(1, 14, 28, 6, "m");
    [[4, 3], [10, 5], [15, 0], [20, 5], [26, 3]].forEach(([x, top]) => g.tri(x, top, 12 - top, 2.6, "m"));
    g.rect(1, 14, 28, 6, "m").hline(1, 14, 28, "l");
    g.set(15, 0, "w").set(4, 3, "w").set(26, 3, "w").set(10, 5, "l").set(20, 5, "l");
    g.rect(6, 16, 3, 3, "w").rect(14, 16, 3, 3, "w").rect(22, 16, 3, 3, "w").set(7, 17, "l").set(15, 17, "l").set(23, 17, "l");
    g.hline(2, 19, 26, "D").shade();
  } },
  "crown-beanie": { w: 28, h: 20, ox: 14, oy: 20, draw(g) {
    g.disc(14, 13, 12.5, "m").clearBelow(13).rect(1, 13, 26, 6, "m").hline(1, 13, 26, "l");
    g.vline(7, 14, 5, "D").vline(11, 14, 5, "D").vline(15, 14, 5, "D").vline(19, 14, 5, "D").vline(23, 14, 5, "D");
    g.vline(8, 4, 8, "d").vline(14, 2, 10, "d").vline(20, 4, 8, "d").disc(14, 2.4, 2.4, "w").set(13, 1, "w");
    g.shade().set(13, 1, "w");
  } },
  "crown-party": { w: 20, h: 26, ox: 10, oy: 26, draw(g) {
    g.tri(10, 0, 22, 2.4, "m");
    for (let y = 3; y < 22; y += 5) g.hline(10 - Math.floor(y / 2.4) - 1, y, Math.floor(y / 2.4) * 2 + 2, "w");
    g.rect(2, 22, 16, 3, "d").hline(2, 22, 16, "l").rect(9, 0, 2, 2, "w").set(7, 8, "T").set(13, 14, "T");
    g.shade();
  } },
  "crown-wizard": { w: 34, h: 32, ox: 17, oy: 32, draw(g) {
    g.fillEllipse(17, 28, 16.5, 3.4, "m").tri(17, 0, 26, 2.2, "m");
    g.rect(10, 22, 14, 3, "d").rect(15, 22, 4, 3, "g").set(16, 23, "h");
    [[13, 17], [20, 11], [15, 6], [18, 20]].forEach(([x, y]) => g.set(x, y, "w"));
    g.set(17, 3, "h").set(8, 29, "T").shade();
  } },
  "crown-halo": { w: 34, h: 10, ox: 17, oy: 10, draw(g) {
    g.ellipse(17, 5, 15, 3.6, "h").ellipse(17, 5, 14, 3, "g").ellipse(17, 5, 13, 2.4, "g").set(6, 3, "w").set(7, 2, "w").set(27, 7, "T").set(10, 8, "T");
  } },
  "crown-sprout": { w: 18, h: 18, ox: 9, oy: 18, draw(g) {
    g.rect(3, 15, 12, 3, "m").vline(8, 6, 10, "d").vline(9, 6, 10, "m");
    g.fillEllipse(4, 5, 4.2, 2.6, "m").fillEllipse(14, 3, 4.2, 2.6, "m").line(1, 6, 7, 6, "d").line(11, 4, 16, 3, "d");
    g.set(5, 3, "w").set(13, 1, "w").shade();
  } },
  "crown-antenna": { w: 14, h: 24, ox: 7, oy: 24, draw(g) {
    g.vline(6, 7, 17, "d").vline(7, 7, 17, "m").rect(4, 21, 6, 3, "m").disc(6.5, 4, 3.4, "m").set(5, 3, "w").set(5, 2, "w").set(11, 1, "T").set(2, 2, "T");
    g.shade().rect(5, 13, 4, 1, "L");
  } },
  "crown-horns": { w: 34, h: 20, ox: 17, oy: 20, draw(g) {
    const path = [[11, 19, 5], [10, 17, 5], [9, 15, 4], [8, 13, 4], [7, 11, 4], [6, 9, 3], [5, 7, 3], [5, 5, 3], [5, 3, 2], [6, 2, 2], [7, 1, 1], [8, 0, 1]];
    path.forEach(([x, y, th]) => g.rect(x - Math.floor(th / 2), y, th, 2, "m"));
    g.hline(5, 14, 6, "d").hline(5, 10, 4, "d").hline(6, 6, 3, "d").set(8, 0, "w").set(7, 1, "L");
    g.mirror();
    g.shade();
  } },

  // ---- eyes ----
  "eyes-glasses": { w: 44, h: 16, ox: 22, oy: 8, on: true, draw(g) {
    g.disc(10, 8, 5.4, "c").disc(34, 8, 5.4, "c").ring(10, 8, 6.4, "m").ring(34, 8, 6.4, "m").hline(16, 6, 12, "m");
    g.hline(0, 4, 4, "m").hline(40, 4, 4, "m").set(7, 5, "w").set(8, 4, "w").set(7, 4, "w").set(31, 5, "w").set(32, 4, "w").set(31, 4, "w");
    g.set(12, 12, "l").set(36, 12, "l");
  } },
  "eyes-shades": { w: 48, h: 16, ox: 24, oy: 8, on: true, draw(g) {
    g.rect(1, 2, 46, 2, "m").rect(2, 4, 19, 9, "z").rect(27, 4, 19, 9, "z").rect(20, 3, 8, 2, "m");
    g.set(4, 7, "w").set(5, 8, "w").set(6, 9, "w").set(29, 7, "w").set(30, 8, "w").set(31, 9, "w");
    g.hline(2, 2, 46, "l");
  } },
  "eyes-monocle": { w: 30, h: 21, ox: 8, oy: 9, on: true, draw(g) {
    g.ring(18, 9, 7.6, "g").ring(18, 9, 6.6, "g").set(14, 5, "h").set(15, 4, "h").set(16, 4, "h").set(13, 6, "w").set(22, 15, "G").set(23, 14, "G");
    g.set(25, 16, "w").set(26, 17, "w").rect(25, 18, 3, 2, "g");
  } },
  "eyes-blush": { w: 52, h: 10, ox: 26, oy: 5, draw(g) {
    g.fillEllipse(8, 5, 6, 3, "m").fillEllipse(44, 5, 6, 3, "m").dither(3, 2, 10, 6, "m", "").dither(39, 2, 10, 6, "m", "", 1);
    g.fillEllipse(8, 5, 4.4, 2, "l").fillEllipse(44, 5, 4.4, 2, "l").set(7, 4, "L").set(43, 4, "L");
  } },
  "eyes-freckles": { w: 44, h: 12, ox: 22, oy: 5, draw(g) {
    [[3, 7, "m"], [6, 9, "d"], [9, 6, "m"], [12, 9, "d"], [32, 9, "d"], [35, 6, "m"], [38, 9, "d"], [41, 7, "m"]].forEach(([x, y, ch]) => g.set(x, y, ch).set(x + 1, y, ch));
  } },
  "eyes-scan": { w: 50, h: 14, ox: 25, oy: 7, on: true, draw(g) {
    g.vline(0, 2, 11, "m").vline(49, 2, 11, "m").hline(0, 2, 3, "m").hline(47, 2, 3, "m").hline(0, 12, 3, "m").hline(47, 12, 3, "m");
    g.vline(24, 3, 9, "l").vline(25, 3, 9, "m").set(25, 7, "T");      // a scan line between the eyes, brackets at the edges: nothing across them
  } },
  "eyes-brows": { w: 46, h: 12, ox: 23, oy: 6, draw(g) {
    g.line(3, 9, 15, 4, "m").line(3, 8, 15, 3, "m").line(4, 10, 14, 5, "d").line(4, 9, 3, 9, "m");
    g.mirror();
    g.set(8, 5, "l").set(37, 5, "l");
  } },

  // ---- neck ----
  "neck-bowtie": { w: 28, h: 18, ox: 14, oy: 9, draw(g) {
    for (let x = 0; x < 11; x++) { const hh = Math.floor(x * 0.7) + 2; g.rect(x, 9 - hh, 1, hh * 2, "m"); }
    g.mirror();
    g.rect(11, 6, 6, 6, "m").rect(12, 7, 4, 4, "d").hline(11, 6, 6, "l").set(12, 8, "l");
    g.hline(2, 8, 8, "d").hline(18, 8, 8, "d").shade().set(13, 7, "w");
  } },
  "neck-scarf": { w: 40, h: 22, ox: 20, oy: 4, draw(g) {
    g.rect(0, 0, 40, 7, "m").rect(2, 7, 36, 2, "m");
    for (let x = 4; x < 38; x += 7) g.rect(x, 1, 3, 7, "l");
    g.rect(28, 7, 9, 14, "m").hline(28, 11, 9, "l").hline(28, 16, 9, "l");
    for (let x = 28; x < 37; x += 2) g.set(x, 21, "m");
    g.shade();
  } },
  "neck-medal": { w: 20, h: 30, ox: 10, oy: 3, draw(g) {
    g.line(1, 0, 10, 12, "m").line(2, 0, 11, 12, "m").line(18, 0, 9, 12, "l").line(17, 0, 8, 12, "l");
    g.disc(10, 20, 7, "g").ring(10, 20, 7, "G").disc(10, 20, 5, "g");
    g.stamp(8, 18, [".w.", "www", ".w."], {}).set(7, 16, "h").set(8, 15, "h").set(13, 24, "G");
    g.rect(8, 11, 4, 3, "g").set(9, 11, "h");
  } },
  "neck-bell": { w: 34, h: 26, ox: 17, oy: 3, draw(g) {
    g.line(1, 0, 11, 7, "d").line(11, 7, 23, 7, "d").line(23, 7, 33, 0, "d").line(2, 0, 12, 7, "m").line(32, 0, 22, 7, "m");
    g.disc(17, 16, 5.4, "g").rect(11, 17, 13, 4, "g").hline(11, 21, 13, "G").set(17, 22, "G").set(16, 22, "G");
    g.set(14, 13, "h").set(15, 12, "h").set(14, 14, "w").set(20, 19, "G").rect(15, 9, 5, 2, "G").set(17, 14, "h");
  } },
  "neck-bandana": { w: 34, h: 20, ox: 17, oy: 3, draw(g) {
    for (let y = 0; y < 17; y++) { const hw = 16 - y; g.rect(17 - hw, y, hw * 2, 1, "m"); }
    g.hline(0, 0, 34, "l").hline(1, 1, 32, "m").set(10, 4, "w").set(17, 6, "w").set(23, 4, "w").set(14, 9, "w").set(20, 9, "w").set(17, 12, "w");
    g.rect(15, 0, 4, 3, "d").shade();
  } },

  // ---- back ----
  "back-cape": { w: 42, h: 58, ox: 21, oy: 20, draw(g) {
    for (let y = 0; y < 56; y++) { const hw = Math.min(19, 8 + Math.floor(y * 0.42)); g.rect(21 - hw, y, hw * 2, 1, "m"); }
    g.dither(5, 36, 32, 4, "d", "m").dither(3, 40, 36, 4, "D", "d").rect(3, 44, 36, 12, "D");
    g.vline(13, 6, 40, "d").vline(29, 6, 40, "d").vline(21, 4, 44, "l").rect(17, 0, 8, 3, "g").set(21, 1, "h");
    for (let x = 3; x < 39; x += 4) g.set(x, 56, "D");
    g.shade();
  } },
  "back-wings": { w: 66, h: 42, ox: 33, oy: 16, draw(g) {
    g.fillEllipse(16, 14, 15, 12, "m").rect(24, 12, 9, 5, "m");
    g.fillEllipse(14, 12, 10, 7, "l").dither(2, 18, 22, 5, "m", "d").line(3, 14, 18, 6, "d").line(2, 18, 20, 10, "d");
    g.disc(6, 29, 3.4, "m").disc(13, 31, 3.4, "m").disc(20, 30, 3.4, "m").disc(26, 26, 3, "m").set(11, 7, "w").set(12, 6, "w");
    g.mirror().shade();
  } },
  "back-pack": { w: 30, h: 36, ox: 15, oy: 12, draw(g) {
    g.rect(2, 3, 26, 31, "m").rect(5, 1, 20, 4, "m").rect(6, 20, 18, 11, "m").hline(6, 20, 18, "l").set(15, 24, "g").set(15, 25, "g").set(14, 24, "h");
    g.vline(8, 0, 14, "d").vline(22, 0, 14, "d").hline(6, 26, 18, "d").dither(4, 8, 22, 3, "m", "l");
    g.shade();
  } },
  "back-jet": { w: 52, h: 48, ox: 26, oy: 16, draw(g) {
    g.rect(2, 0, 10, 30, "m").rect(40, 0, 10, 30, "m").rect(3, 30, 8, 3, "d").rect(41, 30, 8, 3, "d");
    g.vline(4, 2, 26, "l").vline(42, 2, 26, "l").hline(2, 8, 10, "d").hline(40, 8, 10, "d").hline(2, 20, 10, "d").hline(40, 20, 10, "d");
    g.rect(4, 33, 6, 5, "g").rect(42, 33, 6, 5, "g").rect(5, 38, 4, 4, "h").rect(43, 38, 4, 4, "h").set(6, 43, "T").set(44, 43, "T");
    g.shade();
  } },

  // ---- hand ----
  "hand-coffee": { w: 18, h: 22, ox: 8, oy: 16, draw(g) {
    g.rect(1, 7, 11, 10, "w").rect(12, 9, 4, 6, "w").set(13, 10, "").set(13, 11, "").set(13, 12, "").set(13, 13, "");
    g.rect(2, 8, 9, 2, "d").hline(0, 17, 14, "d").hline(1, 18, 12, "D").set(3, 11, "L").set(3, 12, "L");
    g.set(4, 0, "T").set(7, 1, "T").set(4, 3, "T").set(7, 4, "T").rect(2, 14, 9, 3, "l");
  } },
  "hand-balloon": { w: 24, h: 40, ox: 12, oy: 39, draw(g) {
    g.disc(12, 11, 9.4, "m").set(7, 5, "w").set(7, 6, "w").set(8, 4, "w").set(6, 7, "L").rect(10, 20, 5, 2, "d").set(12, 22, "D");
    g.dither(14, 14, 7, 6, "d", "m", 1).rect(15, 18, 4, 3, "d");
    g.vline(12, 23, 7, "w").vline(13, 29, 4, "w").vline(12, 33, 6, "w").shade();
  } },
  "hand-trophy": { w: 26, h: 26, ox: 13, oy: 26, draw(g) {
    g.rect(6, 0, 14, 9, "g").rect(7, 9, 12, 2, "g").rect(9, 11, 8, 2, "g").rect(11, 13, 4, 5, "G").rect(7, 18, 12, 3, "G").rect(5, 21, 16, 4, "G");
    g.ring(4, 5, 3.2, "g").ring(22, 5, 3.2, "g").vline(7, 1, 8, "h").vline(8, 1, 6, "h").set(8, 7, "w").stamp(11, 3, [".w.", "www", ".w."], {}).vline(18, 2, 8, "G");
    g.hline(5, 21, 16, "g");
  } },
  "hand-wand": { w: 28, h: 30, ox: 5, oy: 29, draw(g) {
    g.line(1, 28, 16, 10, "m").line(2, 28, 17, 10, "l").line(1, 27, 16, 9, "d");
    g.line(4, 24, 6, 26, "w").line(9, 18, 11, 20, "w");
    g.stamp(15, 1, [".h.", "hgh", ".h."], {}).stamp(13, 3, ["..h", ".hg", "h.."], {}).set(16, 2, "w").stamp(20, 7, PLUS, {}).set(21, 8, "T");
  } },
  "hand-book": { w: 22, h: 20, ox: 11, oy: 18, draw(g) {
    g.rect(0, 0, 20, 17, "m").rect(1, 1, 18, 15, "m").rect(0, 16, 20, 2, "w").rect(1, 17, 19, 1, "l").vline(9, 0, 17, "D").vline(10, 0, 17, "d");
    g.rect(12, 4, 6, 2, "g").hline(12, 7, 6, "G").rect(3, 4, 4, 1, "l").rect(3, 6, 4, 1, "l").rect(3, 8, 3, 1, "l").set(14, 5, "h").shade();
  } },
  "hand-flag": { w: 28, h: 42, ox: 3, oy: 40, draw(g) {
    g.vline(2, 2, 40, "w").vline(3, 2, 40, "l").disc(2.5, 1.5, 1.6, "g").set(2, 1, "h");
    g.rect(4, 3, 22, 13, "m").dither(4, 3, 22, 2, "l", "m").rect(14, 7, 8, 3, "d").rect(4, 14, 22, 2, "D").set(25, 5, "").set(25, 6, "").set(25, 14, "");
    g.shade();
  } },

  // ---- aura ----
  "aura-sparkles": { w: 66, h: 46, ox: 33, oy: 23, draw(g) {
    g.stamp(3, 6, STAR4, {}).stamp(54, 2, ["w", ""], {}).stamp(54, 2, ["..w..", "..w..", "wwWww", "..w..", "..w.."], {});
    g.stamp(58, 30, PLUS, {}).stamp(9, 34, PLUS, {}).stamp(28, 0, PLUS, {}).set(5, 8, "T").set(56, 4, "T").set(59, 31, "T").set(32, 44, "T");
    g.set(2, 13, "w").set(63, 20, "w").set(18, 12, "w");
  } },
  "aura-hearts": { w: 60, h: 38, ox: 30, oy: 19, draw(g) {
    g.stamp(2, 14, HEART, {}).stamp(48, 4, HEART, {}).stamp(42, 26, HEART, {});
    g.set(3, 15, "L").set(4, 15, "L").set(49, 5, "L").set(50, 5, "L").set(43, 27, "L").set(44, 27, "L").shade();
    g.set(3, 15, "L").set(49, 5, "L").set(43, 27, "L");
  } },
  "aura-bubbles": { w: 66, h: 46, ox: 33, oy: 23, draw(g) {
    g.ring(7, 15, 4.6, "w").ring(54, 9, 3.6, "w").ring(58, 33, 2.6, "w").ring(13, 39, 2.6, "w").ring(32, 3, 2, "w");
    g.set(5, 13, "T").set(6, 12, "w").set(53, 7, "T").set(55, 8, "L").set(57, 32, "L").set(12, 38, "L");
  } },
  "aura-orbit": { w: 84, h: 32, ox: 42, oy: 16, draw(g) {
    g.ellipse(42, 16, 38, 10, "m").ellipse(42, 16, 37, 9.3, "m").ellipse(42, 16, 36, 8.6, "d");
    g.disc(77, 12, 2.6, "g").set(76, 11, "h").set(77, 12, "T").disc(8, 19, 1.6, "w");
  } },
  "aura-embers": { w: 66, h: 46, ox: 33, oy: 23, draw(g) {
    [[4, 32, "g"], [58, 28, "m"], [14, 9, "m"], [49, 7, "g"], [31, 40, "m"], [24, 22, "h"], [41, 18, "g"]].forEach(([x, y, ch]) => g.set(x, y, ch).set(x + 1, y, ch).set(x, y + 1, ch === "g" ? "G" : "d"));
    g.set(20, 30, "T").set(40, 12, "T").set(8, 20, "T").set(55, 36, "T");
  } },
  "aura-notes": { w: 56, h: 42, ox: 28, oy: 21, draw(g) {
    g.stamp(3, 14, NOTE, {}).stamp(42, 4, NOTE, {}).stamp(26, 24, ["..mm", "..m.", "mmm.", "mm.."], {});
    g.set(4, 15, "L").set(43, 5, "L").set(26, 27, "T").set(48, 28, "T").shade();
  } },
};
// ---- Void, Glitch, Cinder and Oracle: decorations drawn for their own looks (same rules as above) ----
{
// Same rules as the Blink set (wardrobe-pixel.js): light from the top left, a ramp from one colour, thick parts shaded by the
// engine, a tinted outline. Strokes are two pixels thick unless they are meant to be hairlines.
const STAR = ['..w..', '.www.', 'wwWww', '.www.', '..w..'];
const tl = (g, x0, y0, x1, y1, ch) => g.line(x0, y0, x1, y1, ch).line(x0, y0 + 1, x1, y1 + 1, ch);
const FLAME = [1, 1, 2, 2, 3, 3, 4, 4, 4, 4, 3, 3];
const flame = (g, cx, top, ch, scale = 1) => { FLAME.forEach((w, i) => { const ww = Math.max(1, Math.round(w * scale)); g.rect(cx - ww, top + i, ww * 2, 1, ch); }); return g; };
const cloud = (g, list, ch) => { list.forEach(([x, y, r]) => g.disc(x, y, r, ch)); return g; };

Object.assign(PIXEL_ITEMS, {
  // VOID: gravity, rings, satellites
  'crown-accretion': { w: 36, h: 14, ox: 18, oy: 14, draw(g) {
    g.fillEllipse(18, 8, 17, 5, 'm').fillEllipse(18, 8, 12.5, 3, 'D').fillEllipse(18, 8, 14, 3.6, 'd').fillEllipse(18, 8, 11, 2.4, 'D');
    g.ellipse(18, 7, 16, 4.4, 'l').disc(18, 8, 2.4, 'w').set(17, 7, 'L').set(4, 8, 'T').set(32, 8, 'T');
  } },
  'crown-comet': { w: 42, h: 22, ox: 21, oy: 22, draw(g) {
    tl(g, 30, 8, 2, 16, 'm'); tl(g, 30, 11, 6, 19, 'd'); tl(g, 29, 5, 10, 9, 'l');
    g.dither(8, 14, 10, 4, 'd', '').disc(32, 8, 4.6, 'm').disc(32, 8, 3.4, 'l').disc(31, 7, 2, 'w').set(35, 5, 'T');
  } },
  'crown-dish': { w: 24, h: 26, ox: 12, oy: 26, draw(g) {
    g.rect(11, 12, 2, 14, 'm').rect(7, 24, 10, 2, 'd');
    for (let y = 0; y < 9; y++) { const hw = 3 + y; if (hw <= 11) g.rect(12 - hw, y + 4, hw * 2, 1, y < 2 ? 'l' : 'm'); }
    g.dither(6, 9, 12, 3, 'd', 'm').rect(3, 12, 18, 1, 'd').line(12, 12, 19, 2, 'l').disc(19.5, 1.5, 1.8, 'w').set(21, 0, 'T');
  } },
  'eyes-lens': { w: 50, h: 24, ox: 25, oy: 12, on: true, draw(g) {
    g.fillEllipse(25, 12, 20, 8.6, 'c').ellipse(25, 12, 23, 11, 'm').ellipse(25, 12, 22, 10, 'm').ellipse(25, 12, 20, 8.6, 'd');
    g.hline(8, 3, 10, 'l').hline(10, 2, 7, 'L').set(40, 20, 'l').set(41, 19, 'l').set(6, 18, 'T');
  } },
  'eyes-starry': { w: 50, h: 16, ox: 25, oy: 8, on: true, draw(g) {
    g.stamp(5, 3, STAR, {}).stamp(40, 3, STAR, {}).stamp(7, 5, ['L'], {}).set(7, 5, 'L').set(42, 5, 'L').set(14, 1, 'T').set(36, 14, 'T').set(2, 12, 'T');
  } },
  'neck-orbitring': { w: 44, h: 14, ox: 22, oy: 7, draw(g) {
    g.ellipse(22, 7, 20, 5, 'm').ellipse(22, 7, 20, 4.2, 'm').ellipse(22, 7, 19, 3.4, 'd'); g.hline(10, 2, 12, 'l');
    g.disc(41, 7, 2.6, 'w').set(40, 6, 'L').set(4, 8, 'T');
  } },
  'neck-pendant': { w: 28, h: 28, ox: 14, oy: 3, draw(g) {
    tl(g, 1, 0, 13, 11, 'd'); tl(g, 27, 0, 15, 11, 'd');
    g.disc(14, 18, 6, 'm').ellipse(14, 18, 9, 2.6, 'l').dither(9, 20, 9, 3, 'd', 'm').set(11, 15, 'w').set(12, 14, 'w').set(10, 16, 'L');
  } },
  'back-nebula': { w: 76, h: 58, ox: 38, oy: 26, draw(g) {
    cloud(g, [[16, 32, 12], [50, 22, 14], [34, 46, 11], [62, 42, 9]], 'D');
    cloud(g, [[16, 31, 9], [50, 21, 11], [34, 45, 8], [62, 41, 6]], 'd');
    cloud(g, [[15, 29, 5], [49, 19, 7], [33, 43, 4]], 'm');
    g.dither(24, 28, 14, 8, 'd', 'D'); [[8, 8], [66, 50], [32, 6], [70, 12], [26, 54]].forEach(([x, y]) => g.set(x, y, 'w')); g.set(40, 16, 'T').set(12, 44, 'T');
  } },
  'back-solar': { w: 88, h: 26, ox: 44, oy: 11, draw(g) {
    g.rect(0, 0, 28, 16, 'D'); for (let y = 0; y < 3; y++) for (let x = 0; x < 4; x++) g.rect(1 + x * 7, 1 + y * 5, 6, 4, 'm');
    g.vline(7, 1, 14, 'l').vline(14, 1, 14, 'l').rect(28, 7, 8, 2, 'd').rect(34, 5, 4, 6, 'd'); g.mirror();
  } },
  'hand-planet': { w: 28, h: 28, ox: 14, oy: 26, draw(g) {
    g.rect(13, 18, 2, 8, 'd').rect(9, 25, 10, 2, 'd').disc(14, 11, 7, 'm').dither(8, 10, 12, 4, 'd', 'm').dither(8, 14, 10, 3, 'D', 'd');
    g.ellipse(14, 11, 13, 3.4, 'w').ellipse(14, 11, 12, 2.8, 'l').set(10, 7, 'L').set(11, 6, 'L').disc(24, 3, 1.6, 'l');
  } },
  'hand-scope': { w: 34, h: 32, ox: 10, oy: 30, draw(g) {
    tl(g, 3, 20, 24, 5, 'm'); tl(g, 3, 22, 24, 7, 'd'); g.line(5, 19, 22, 6, 'l');
    g.disc(25, 4, 3.2, 'w').disc(25, 4, 2, 'm').rect(1, 21, 4, 4, 'D');
    g.line(10, 22, 5, 31, 'd').line(11, 22, 6, 31, 'd').line(11, 22, 17, 31, 'd').line(12, 22, 18, 31, 'd');
  } },
  'aura-constellation': { w: 76, h: 46, ox: 38, oy: 23, draw(g) {
    const A = [[4, 10], [14, 3], [26, 8], [30, 20]], B = [[58, 4], [70, 14], [62, 26], [50, 34]];
    [A, B].forEach((p) => p.forEach(([x, y], i) => { if (i) g.line(p[i - 1][0], p[i - 1][1], x, y, 'd'); }));
    [...A, ...B].forEach(([x, y]) => g.rect(x - 1, y - 1, 3, 3, 'm').set(x, y, 'w')); g.set(14, 3, 'T').set(70, 14, 'T').set(50, 34, 'T');
  } },
  'aura-moons': { w: 76, h: 40, ox: 38, oy: 20, draw(g) {
    g.disc(66, 12, 5, 'm').set(64, 11, 'd').set(67, 14, 'd').set(66, 9, 'd').set(63, 9, 'L').set(64, 8, 'L');
    g.disc(9, 28, 3.6, 'l').set(8, 27, 'w').set(10, 30, 'd').set(36, 2, 'T').set(48, 36, 'T');
  } },
  'aura-meteors': { w: 76, h: 46, ox: 38, oy: 23, draw(g) {
    [[50, 3, 'm'], [4, 8, 'l'], [58, 26, 'm']].forEach(([x, y, c]) => { tl(g, x, y, x + 10, y + 10, c); g.disc(x + 11, y + 11, 1.8, 'w'); g.dither(x - 5, y - 5, 5, 5, c, ''); });
    g.set(30, 40, 'T');
  } },

  // GLITCH: signal, pixels, scanlines
  'crown-bars': { w: 26, h: 20, ox: 13, oy: 20, draw(g) {
    [[1, 12, 5], [8, 8, 9], [15, 4, 13], [22, 0, 17]].forEach(([x, y, h], i) => { g.rect(x, y + 3, 4, h, i < 3 ? 'm' : 'd'); });
    g.rect(22, 3, 4, 14, 'd').rect(22, 3, 4, 2, 'l');
  } },
  'crown-pixelcrown': { w: 32, h: 16, ox: 16, oy: 16, draw(g) {
    g.rect(1, 9, 30, 7, 'm').rect(2, 3, 5, 7, 'm').rect(10, 0, 5, 10, 'm').rect(17, 0, 5, 10, 'm').rect(25, 3, 5, 7, 'm');
    g.rect(5, 11, 4, 3, 'w').rect(14, 11, 4, 3, 'w').rect(23, 11, 4, 3, 'w').set(4, 5, 'w').set(12, 2, 'w').set(19, 2, 'w').set(27, 5, 'w').hline(1, 15, 30, 'D');
  } },
  // A headset sits ON the head: a flat band along the top edge, down the two sides to the ear cups at ear height. Nothing rises above the head.
  'crown-headset': { w: 58, h: 34, ox: 29, oy: 3, rest: 4, draw(g) {
    g.rect(6, 0, 46, 3, 'm').hline(6, 0, 46, 'l').hline(6, 3, 46, 'D');
    g.rect(4, 1, 3, 3, 'm').rect(51, 1, 3, 3, 'm').rect(3, 4, 3, 10, 'm').rect(52, 4, 3, 10, 'm').rect(3, 4, 1, 10, 'l').rect(52, 4, 1, 10, 'l');
    g.rect(0, 13, 8, 14, 'm').rect(50, 13, 8, 14, 'm').rect(1, 14, 2, 11, 'l').rect(51, 14, 2, 11, 'l').rect(5, 15, 3, 11, 'd').rect(50, 15, 3, 11, 'd');
  } },
  'eyes-pixshades': { w: 52, h: 16, ox: 26, oy: 8, on: true, draw(g) {
    g.rect(0, 2, 52, 4, 'm').rect(2, 6, 20, 8, 'z').rect(30, 6, 20, 8, 'z').rect(22, 3, 8, 3, 'm').hline(0, 2, 52, 'l');
    g.rect(4, 7, 3, 1, 'w').rect(4, 8, 2, 1, 'w').rect(32, 7, 3, 1, 'w').rect(32, 8, 2, 1, 'w');
  } },
  'eyes-xeyes': { w: 50, h: 18, ox: 25, oy: 9, on: true, draw(g) {
    [11, 39].forEach((cx) => { g.line(cx - 5, 3, cx + 5, 13, 'm').line(cx + 5, 3, cx - 5, 13, 'm'); g.set(cx, 8, 'w'); });
  } },
  'eyes-visor': { w: 54, h: 16, ox: 27, oy: 8, on: true, draw(g) {
    g.rect(0, 4, 54, 8, 'm').rect(2, 6, 50, 4, 'z').hline(0, 4, 54, 'l');
    g.rect(26, 6, 2, 4, 'w').set(27, 7, 'T').rect(0, 6, 2, 4, 'd').rect(52, 6, 2, 4, 'd');
  } },
  'neck-cable': { w: 40, h: 22, ox: 20, oy: 3, draw(g) {
    tl(g, 1, 0, 10, 9, 'm'); tl(g, 10, 9, 28, 9, 'm'); tl(g, 28, 9, 36, 3, 'm');
    g.rect(31, 1, 8, 6, 'd').rect(32, 2, 6, 4, 'm').rect(39, 3, 1, 2, 'l').rect(33, 3, 1, 2, 'w');
  } },
  'neck-qr': { w: 22, h: 30, ox: 11, oy: 3, draw(g) {
    tl(g, 1, 0, 11, 10, 'd'); tl(g, 21, 0, 11, 10, 'd'); g.rect(2, 11, 18, 18, 'w');
    [[3, 12], [14, 12], [3, 23]].forEach(([x, y]) => { g.rect(x, y, 5, 5, 'k').rect(x + 1, y + 1, 3, 3, 'w').set(x + 2, y + 2, 'k'); });
    [[11, 12], [11, 15], [13, 17], [9, 20], [15, 22], [12, 24], [17, 20], [10, 27], [16, 26]].forEach(([x, y]) => g.set(x, y, 'k').set(x + 1, y, 'k'));
  } },
  'back-wifi': { w: 64, h: 34, ox: 32, oy: 22, draw(g) {
    [[30, 'm'], [22, 'm'], [14, 'l']].forEach(([r, c]) => { g.ring(32, 32, r, c).ring(32, 32, r - 1, c); });
    g.clearBelow(26); g.disc(32, 24, 2.4, 'w').set(31, 23, 'L');
  } },
  'back-circuit': { w: 84, h: 42, ox: 42, oy: 16, draw(g) {
    g.rect(0, 5, 18, 2, 'm').rect(16, 5, 2, 12, 'm').rect(16, 16, 14, 2, 'm').rect(6, 5, 2, 24, 'd').rect(6, 28, 12, 2, 'd').rect(30, 16, 2, 8, 'd');
    g.rect(0, 3, 4, 6, 'l').rect(14, 18, 4, 4, 'l').rect(15, 27, 4, 4, 'l').rect(28, 24, 6, 6, 'D').set(30, 26, 'w').dither(2, 12, 4, 4, 'd', ''); g.mirror();
  } },
  'hand-cursor': { w: 18, h: 26, ox: 3, oy: 24, draw(g) {
    const rows = ['w', 'ww', 'www', 'wwww', 'wwwww', 'wwwwww', 'wwwwwww', 'wwwwwwww', 'wwwww', 'wwwww', 'ww.ww', 'w..www', '....www'];
    rows.forEach((r, j) => [...r].forEach((k, i) => { if (k !== '.') g.set(2 + i, 1 + j * 1.5 | 0, 'w').set(2 + i, 2 + j * 1.5 | 0, 'w'); }));
    g.line(3, 5, 3, 14, 'l'); g.set(12, 22, 'T');
  } },
  'hand-usb': { w: 18, h: 28, ox: 9, oy: 26, draw(g) {
    g.rect(3, 12, 12, 14, 'm').rect(4, 13, 3, 11, 'l').rect(5, 2, 8, 10, 'D').rect(6, 3, 6, 8, 'l').set(7, 5, 'k').set(10, 5, 'k').rect(5, 17, 8, 3, 'd').rect(6, 18, 4, 1, 'w');
  } },
  'hand-floppy': { w: 22, h: 22, ox: 11, oy: 20, draw(g) {
    g.rect(0, 0, 22, 22, 'm').rect(5, 0, 12, 7, 'l').rect(12, 1, 3, 5, 'D').rect(3, 12, 16, 10, 'w').rect(5, 14, 12, 1, 'd').rect(5, 17, 10, 1, 'd').rect(0, 0, 3, 3, 'D');
  } },
  'aura-bitrain': { w: 76, h: 50, ox: 38, oy: 25, draw(g) {
    [[4, 2], [18, 12], [58, 6], [68, 22], [10, 30], [64, 38], [34, 42]].forEach(([x, y], i) => { g.rect(x, y, 3, 3, i % 2 ? 'm' : 'l'); g.rect(x, y - 4, 3, 2, 'd'); g.rect(x, y - 7, 3, 1, 'D'); });
    g.set(5, 3, 'T').set(59, 7, 'T').set(35, 43, 'T');
  } },
  'aura-static': { w: 76, h: 46, ox: 38, oy: 23, draw(g) {
    [[2, 8, 14], [8, 13, 10], [56, 3, 16], [62, 8, 11], [4, 36, 14], [54, 32, 16], [60, 38, 10]].forEach(([x, y, n]) => { g.rect(x, y, n, 2, 'm'); g.rect(x + 3, y + 2, n - 6, 1, 'd'); });
    g.set(20, 8, 'T').set(70, 8, 'T').set(30, 36, 'T');
  } },

  // CINDER: ember, ash, heat
  'crown-flamecrown': { w: 34, h: 26, ox: 17, oy: 26, draw(g) {
    flame(g, 6, 8, 'm', 0.9); flame(g, 28, 8, 'm', 0.9); flame(g, 17, 0, 'm', 1.2);
    flame(g, 17, 7, 'g', 0.8); flame(g, 6, 14, 'g', 0.55); flame(g, 28, 14, 'g', 0.55); flame(g, 17, 12, 'h', 0.45);
    g.rect(2, 20, 30, 6, 'D').rect(2, 20, 30, 2, 'd').rect(2, 20, 30, 1, 'l').set(17, 6, 'T');
  } },
  'crown-emberhalo': { w: 40, h: 14, ox: 20, oy: 14, draw(g) {
    for (let a = 0; a < 6.3; a += 0.4) { const x = 20 + 18 * Math.cos(a), y = 7 + 4.6 * Math.sin(a); g.rect(Math.round(x), Math.round(y), 2, 2, a % 0.8 < 0.4 ? 'g' : 'm'); }
    g.set(20, 2, 'T').set(6, 7, 'T').set(34, 7, 'T').set(20, 11, 'h');
  } },
  'crown-chimney': { w: 20, h: 32, ox: 10, oy: 26, draw(g) {
    g.rect(3, 12, 14, 16, 'm').rect(1, 12, 18, 3, 'l'); for (let y = 16; y < 28; y += 4) g.hline(3, y, 14, 'd'); g.vline(10, 15, 13, 'd').vline(6, 19, 4, 'd').vline(14, 23, 4, 'd');
    g.disc(10, 7, 3, 'D').disc(12, 3, 2.4, 'd').disc(9, 0.6, 1.8, 'd').set(10, 7, 'l');
  } },
  'eyes-goggles': { w: 58, h: 22, ox: 29, oy: 11, on: true, draw(g) {
    [13, 45].forEach((cx) => { g.disc(cx, 11, 9.4, 'm').disc(cx, 11, 7, 'z'); g.set(cx - 7, 6, 'h').set(cx + 7, 6, 'h').set(cx - 7, 16, 'h').set(cx + 7, 16, 'h'); g.set(cx - 3, 7, 'w').set(cx - 2, 6, 'w').set(cx - 4, 8, 'L'); });
    g.rect(22, 10, 14, 3, 'm').rect(0, 9, 4, 4, 'D').rect(54, 9, 4, 4, 'D');
  } },
  'eyes-soot': { w: 50, h: 14, ox: 25, oy: 6, draw(g) {
    g.dither(2, 8, 9, 3, 'd', ''); g.dither(39, 8, 9, 3, 'd', '', 1); g.dither(18, 0, 14, 2, 'd', '');
    g.rect(4, 11, 2, 2, 'D').rect(44, 11, 2, 2, 'D').rect(24, 2, 3, 1, 'D');
  } },
  'neck-ashscarf': { w: 44, h: 26, ox: 22, oy: 4, draw(g) {
    g.rect(0, 0, 44, 8, 'm').rect(2, 8, 40, 2, 'm').rect(30, 8, 10, 15, 'm');
    for (let x = 4; x < 42; x += 8) g.rect(x, 1, 3, 8, 'l'); g.hline(30, 14, 10, 'l').hline(30, 19, 10, 'l');
    g.set(31, 24, 'm').set(34, 25, 'm').set(37, 24, 'm').set(39, 25, 'm').dither(0, 6, 44, 2, 'd', 'm');
  } },
  'neck-emberchain': { w: 38, h: 28, ox: 19, oy: 3, draw(g) {
    for (let x = 1; x < 37; x += 4) { const y = 1 + Math.round(8 * Math.sin((x / 38) * Math.PI)); g.rect(x, y, 3, 2, (x / 4) % 2 ? 'm' : 'd'); g.set(x + 1, y + 2, 'D'); }
    flame(g, 19, 12, 'g', 0.6); flame(g, 19, 15, 'h', 0.35); g.set(19, 11, 'T');
  } },
  'back-phoenix': { w: 74, h: 50, ox: 37, oy: 18, draw(g) {
    g.fillEllipse(17, 14, 16, 12, 'm').rect(26, 12, 11, 5, 'm').fillEllipse(15, 12, 11, 8, 'g').fillEllipse(13, 10, 6, 4, 'h');
    for (let i = 0; i < 4; i++) { flame(g, 5 + i * 7, 28 - (i === 1 || i === 2 ? 2 : 0), 'm', 0.9); flame(g, 5 + i * 7, 31 - (i === 1 || i === 2 ? 2 : 0), 'g', 0.5); }
    g.dither(2, 22, 28, 3, 'd', 'm'); g.mirror();
  } },
  'back-smoke': { w: 72, h: 54, ox: 36, oy: 18, draw(g) {
    cloud(g, [[10, 44, 6], [16, 33, 5.4], [9, 22, 5], [16, 12, 4.4], [10, 3, 3.4]], 'D'); cloud(g, [[9, 43, 4], [15, 32, 3.6], [8, 21, 3], [15, 11, 2.6]], 'd');
    g.dither(4, 38, 10, 4, 'd', 'D').set(7, 40, 'l').set(13, 29, 'l'); g.mirror();
  } },
  'hand-marsh': { w: 30, h: 34, ox: 5, oy: 32, draw(g) {
    tl(g, 2, 31, 19, 8, 'l'); tl(g, 3, 31, 20, 8, 'd');
    g.rect(14, 0, 12, 10, 'w').rect(15, 6, 10, 4, 'm').rect(17, 8, 6, 2, 'd').rect(15, 1, 3, 6, 'L').set(23, 3, 'l');
  } },
  'hand-torch': { w: 22, h: 36, ox: 11, oy: 34, draw(g) {
    g.rect(8, 16, 6, 18, 'm').rect(9, 17, 2, 15, 'l').rect(5, 14, 12, 4, 'D').rect(6, 14, 10, 1, 'd'); g.hline(8, 22, 6, 'd').hline(8, 28, 6, 'd');
    flame(g, 11, 0, 'm', 1.0); flame(g, 11, 3, 'g', 0.8); flame(g, 11, 6, 'h', 0.45); g.set(18, 4, 'T').set(4, 8, 'T');
  } },
  'hand-kettle': { w: 30, h: 26, ox: 12, oy: 24, draw(g) {
    g.fillEllipse(11, 14, 10, 8, 'm').rect(2, 17, 18, 7, 'm').rect(20, 11, 7, 2, 'd').rect(25, 9, 2, 4, 'd').rect(8, 4, 6, 3, 'D').rect(9, 3, 4, 1, 'd');
    g.set(6, 11, 'L').set(7, 10, 'L').set(5, 12, 'l').hline(2, 22, 18, 'D').set(10, 0, 'T').set(12, 1, 'T');
  } },
  'aura-sparks': { w: 76, h: 46, ox: 38, oy: 23, draw(g) {
    [[6, 30, 'g'], [12, 20, 'h'], [60, 24, 'g'], [66, 12, 'h'], [24, 6, 'g'], [52, 4, 'h'], [38, 40, 'g']].forEach(([x, y, c]) => { g.rect(x, y, 2, 3, c); g.set(x, y + 3, 'm'); });
    g.set(13, 21, 'T').set(61, 25, 'T').set(53, 5, 'T');
  } },
  'aura-heat': { w: 76, h: 46, ox: 38, oy: 23, draw(g) {
    [6, 66].forEach((x) => { for (let y = 2; y < 42; y += 4) g.rect(x + (((y / 4) | 0) % 2 ? 2 : 0), y, 2, 3, 'm'); g.dither(x - 1, 8, 5, 28, 'd', ''); });
    g.set(8, 10, 'T').set(68, 22, 'T');
  } },

  // ORACLE: eye, sight, signs
  'crown-thirdeye': { w: 32, h: 26, ox: 16, oy: 26, draw(g) {
    g.fillEllipse(16, 16, 13, 6, 'w').fillEllipse(16, 16, 13, 6, 'w').disc(16, 16, 4.6, 'm').disc(16, 16, 2.4, 'k').set(14, 14, 'w').set(15, 14, 'w');
    g.hline(5, 11, 22, 'g').hline(7, 10, 18, 'G').hline(5, 21, 22, 'g');
    [[16, 0], [8, 3], [24, 3], [3, 7], [29, 7]].forEach(([x, y]) => g.rect(x, y, 2, 4, 'h'));
    g.set(16, 4, 'T');
  } },
  'crown-gemband': { w: 36, h: 18, ox: 18, oy: 18, draw(g) {
    g.rect(1, 12, 34, 5, 'g').hline(1, 12, 34, 'h').hline(2, 16, 32, 'G');
    g.stamp(13, 1, ['....hh....', '...hmmh...', '..hmLmmh..', '..mmLmmm..', '..dmmmmd..', '...ddDd...'], {}); g.rect(16, 4, 3, 3, 'L');
    tl(g, 24, 11, 32, 3, 'l'); tl(g, 12, 11, 4, 3, 'l'); g.set(32, 2, 'T');
  } },
  'crown-laurel': { w: 40, h: 22, ox: 20, oy: 22, draw(g) {
    [[3, 16], [2, 12], [4, 8], [7, 5], [11, 2], [16, 1]].forEach(([x, y], i) => { g.rect(x, y, 4, 3, i % 2 ? 'm' : 'l'); g.set(x + 1, y + 3, 'D'); });
    g.rect(3, 19, 4, 3, 'd'); g.mirror(); g.set(20, 0, 'h');
  } },
  'eyes-lashes': { w: 58, h: 20, ox: 29, oy: 14, draw(g) {
    [[3, 10, -3], [8, 6, -2], [14, 3, -1], [20, 1, 0]].forEach(([x, y, d]) => { tl(g, x + 6, y + 6, x + 3 + d, y, 'm'); });
    g.mirror();
  } },
  'eyes-lids': { w: 54, h: 14, ox: 27, oy: 8, draw(g) {
    g.fillEllipse(12, 10, 11, 7, 'm').clearBelow(8); g.rect(1, 6, 24, 3, 'm').hline(1, 8, 24, 'D').hline(2, 9, 22, 'D').dither(2, 4, 20, 2, 'd', 'm').rect(1, 5, 24, 1, 'l'); g.mirror();
  } },
  'eyes-runeye': { w: 58, h: 22, ox: 29, oy: 4, draw(g) {
    [8, 46].forEach((x) => { g.stamp(x, 5, ['..m..', '.mmm.', 'm.m.m', '..m..', '..m..', '.mmm.'], {}); g.set(x + 2, 6, 'L'); }); g.set(10, 6, 'T').set(48, 6, 'T');
  } },
  'neck-amulet': { w: 30, h: 34, ox: 15, oy: 3, draw(g) {
    tl(g, 1, 0, 14, 12, 'd'); tl(g, 29, 0, 16, 12, 'd');
    g.stamp(8, 13, ['...mm...', '..mmmm..', '.mmLmmm.', 'mmLmmmmm', 'mmmmmmdm', '.mmmmdd.', '..mddd..', '...dd...'], {}); g.set(11, 17, 'w').set(10, 18, 'w').rect(11, 11, 3, 3, 'g');
  } },
  'neck-runechain': { w: 38, h: 28, ox: 19, oy: 3, draw(g) {
    for (let x = 2; x < 36; x += 5) { const y = 1 + Math.round(8 * Math.sin((x / 38) * Math.PI)); g.rect(x, y, 4, 4, 'd').rect(x, y, 4, 1, 'l').set(x + 2, y + 2, 'h'); }
    g.rect(16, 14, 7, 9, 'm').stamp(18, 16, ['.m.', 'mmm', '.m.', '.m.'], { m: 'k' }).rect(16, 14, 7, 1, 'l');
  } },
  'back-cloak': { w: 50, h: 60, ox: 25, oy: 22, draw(g) {
    g.fillEllipse(25, 14, 17, 14, 'm'); for (let y = 14; y < 58; y++) { const hw = Math.min(23, 15 + Math.floor((y - 14) * 0.2)); g.rect(25 - hw, y, hw * 2, 1, 'm'); }
    g.fillEllipse(25, 14, 11, 9, 'D'); g.dither(4, 40, 42, 4, 'd', 'm').rect(3, 44, 44, 14, 'd').dither(3, 44, 44, 2, 'D', 'd');
    g.vline(14, 20, 34, 'd').vline(36, 20, 34, 'd').vline(25, 24, 30, 'l'); for (let x = 3; x < 47; x += 4) g.set(x, 59, 'D');
  } },
  'back-rings': { w: 88, h: 44, ox: 44, oy: 22, draw(g) {
    g.ellipse(44, 22, 42, 11, 'm').ellipse(44, 22, 41, 10.4, 'm').ellipse(44, 22, 36, 8, 'l').ellipse(44, 22, 35, 7.4, 'd');
    g.disc(80, 14, 3, 'w').set(79, 13, 'L').disc(10, 30, 2.4, 'h').set(44, 2, 'T').set(44, 42, 'T');
  } },
  'hand-ball': { w: 28, h: 30, ox: 14, oy: 28, draw(g) {
    g.rect(6, 22, 16, 5, 'D').rect(4, 25, 20, 3, 'd').rect(8, 20, 12, 3, 'd'); g.disc(14, 11, 10, 'l').disc(14, 11, 8, 'm').dither(7, 12, 14, 6, 'd', 'm').dither(9, 8, 8, 4, 'l', 'm');
    g.disc(10, 7, 2, 'L').set(9, 6, 'w').set(10, 6, 'w').set(18, 15, 'l').set(14, 11, 'T');
  } },
  'hand-scroll': { w: 28, h: 18, ox: 14, oy: 16, draw(g) {
    g.rect(3, 0, 22, 16, 'w').rect(0, 0, 5, 16, 'm').rect(23, 0, 5, 16, 'm').rect(1, 1, 2, 14, 'l').rect(24, 1, 2, 14, 'l');
    [3, 6, 9, 12].forEach((y, i) => g.hline(7, y, 14 - (i % 2) * 4, 'd')); g.rect(0, 15, 5, 1, 'D').rect(23, 15, 5, 1, 'D');
  } },
  'hand-candle': { w: 16, h: 34, ox: 8, oy: 32, draw(g) {
    g.rect(3, 14, 10, 18, 'w').rect(4, 14, 2, 17, 'L').rect(1, 30, 14, 3, 'd').rect(3, 20, 2, 5, 'd').rect(10, 24, 2, 4, 'd').rect(7, 11, 2, 4, 'k');
    flame(g, 8, 0, 'm', 0.7); flame(g, 8, 2, 'g', 0.55); flame(g, 8, 5, 'h', 0.3); g.set(13, 3, 'T');
  } },
  'aura-runes': { w: 76, h: 46, ox: 38, oy: 23, draw(g) {
    g.stamp(4, 8, ['..m..', '.mmm.', 'm.m.m', '..m..', '..m..', '.mmm.'], {}).stamp(66, 3, ['m...m', '.m.m.', '..m..', '.m.m.', 'm...m'], {}).stamp(56, 30, ['mmmmm', '..m..', 'mmmmm', '..m..', 'mmmmm'], {});
    g.set(6, 9, 'T').set(68, 4, 'T').set(58, 31, 'T').set(20, 38, 'T'); g.set(6, 9, 'L');
  } },
  'aura-eyesfloat': { w: 76, h: 46, ox: 38, oy: 23, draw(g) {
    [[9, 15, 7], [62, 8, 6], [56, 34, 5]].forEach(([x, y, r]) => { g.fillEllipse(x, y, r, r / 2 + 1, 'w').disc(x, y, r / 3 + 0.8, 'm').set(x, y, 'k'); g.hline(x - r, y - 1 - (r / 2 | 0), r * 2, 'g'); });
    g.set(9, 15, 'T').set(62, 8, 'T');
  } },
});
}

export const PIXEL_IDS = Object.keys(PIXEL_ITEMS);

const cache = new Map();
/** The drawn rectangles of one item: {w, h, ox, oy, runs: [{x, y, w, ch}], bounds}. Auras float free; everything else
 *  gets one outline in the dark tint of its own colour. */
export function pixelRects(key, items = PIXEL_ITEMS) {
  if (cache.has(key) && items === PIXEL_ITEMS) return cache.get(key);
  const it = items[key];
  if (!it) return null;
  const g = Grid(it.w, it.h);
  it.draw(g);
  g.shade();                                           // the same light on everything (a thick part of the base tone)
  if (!key.startsWith("aura-")) g.outline("k");
  const out = { w: it.w, h: it.h, ox: it.ox, oy: it.oy, runs: g.runs(), bounds: g.bounds() };
  if (items === PIXEL_ITEMS) cache.set(key, out);
  return out;
}

// Each tone is a fill written on the rectangle itself (not a class rule): an item is drawn through <use>, and style rules that
// depend on where the original lives are not reliable there. The item's colour arrives as the inherited --wd-ink. Shadows
// lean toward violet, highlights toward warm cream, the outline is the colour's own dark tint (never pure black).
export const TONES = {
  c: "rgba(190,220,255,.13)",                              // clear glass (the buddy shows through; not counted as covering it)
  k: "color-mix(in srgb,var(--wd-ink,#fff) 16%,#0b0716)",
  D: "color-mix(in srgb,var(--wd-ink,#fff) 40%,#1b1048)",
  d: "color-mix(in srgb,var(--wd-ink,#fff) 68%,#3a2a82)",
  m: "var(--wd-ink,#fff)",
  l: "color-mix(in srgb,var(--wd-ink,#fff) 66%,#fff1c4)",
  L: "color-mix(in srgb,var(--wd-ink,#fff) 36%,#fff8dc)",
  w: "#fffdf2", g: "#ffc933", G: "#a8690a", h: "#fff0a6", T: "#fffdf2",
  z: "rgba(10,8,22,.52)",                                  // smoked glass: the buddy shows through (the audit counts it as not covering)
};

/** SVG markup for one item as a <g id="wdp-<key>">: rectangles around the socket (origin 0,0). */
export function pixelGroup(key, items = PIXEL_ITEMS) {
  const r = pixelRects(key, items);
  if (!r) return "";
  const rects = r.runs.map((p) => `<rect style="fill:${TONES[p.ch]}${p.ch === "T" ? ";animation:wd-tw 1.6s steps(2) infinite" : ""}" x="${(p.x - r.ox) * PX}" y="${(p.y - r.oy) * PX}" width="${p.w * PX}" height="${PX}"/>`).join("");
  return `<g id="wdp-${key}">${rects}</g>`;
}

/** Where an item's drawn pixels lie around its socket, in art units: {left, top, right, bottom} (negative = left/above).
 *  `rest` is the row an item rests on when part of it hangs down the sides (a headset's ear cups): it sits on the head by that row. */
export function pixelExtent(key, items = PIXEL_ITEMS) {
  const r = pixelRects(key, items);
  if (!r || !r.bounds) return null;
  const def = items[key], ex = { left: (r.bounds.x0 - r.ox) * PX, top: (r.bounds.y0 - r.oy) * PX, right: (r.bounds.x1 - r.ox) * PX, bottom: (r.bounds.y1 - r.oy) * PX };
  if (def && def.rest !== undefined) ex.rest = (def.rest - r.oy) * PX;
  return ex;
}
