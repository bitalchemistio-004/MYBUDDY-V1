// The character rooms in pixel art (ported from the design simulator). Every place is a small grid of 6-unit pixels with five levels of
// detail; the room grows in tiers with the total of the levels. Pure drawing: it reads one state object (ST) set by views/room.js from
// /ui/room, the real time and the live feed, and returns SVG text. Nothing here is a mock-up: a place is drawn at the level the user paid for.
import { Grid } from "../buddy/wardrobe-pixel.js";

export const PLACES = ["window", "art", "shelf", "lamp", "seat", "rug", "desk", "plant", "sig"];
export const PAL = {
  blink: { wall: "#13151a", floor: "#0c0e12", main: "#f2f2f0", acc: "#8fd4ff" },
  void: { wall: "#0b0a1d", floor: "#07061a", main: "#a98cff", acc: "#5fd0e6" },
  glitch: { wall: "#06141a", floor: "#040e12", main: "#5fd0e6", acc: "#ff4fd8" },
  cinder: { wall: "#1a0f0a", floor: "#120905", main: "#ff8a3d", acc: "#ffc933" },
  oracle: { wall: "#17130a", floor: "#100d06", main: "#e8c15a", acc: "#a98cff" },
};
const SHOP_ITEMS = ["clock", "lights", "calendar", "radio", "tv", "tank", "pet"];
/** The state the drawing reads. views/room.js keeps it up to date. */
export const ST = {
  levels: {}, owned: {}, layout: {}, preview: 0, at: null, arrange: false, eggs: {}, sprints: 0, radio: false,
  justUp: "", justBought: "", discoUntil: 0, confettiUntil: 0, starUntil: 0, mouseUntil: 0,
};
const PXR = 6;
const mixHex = (a, b, t) => {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [r1, g1, b1] = p(a), [r2, g2, b2] = p(b);
  const h = (v) => Math.round(v).toString(16).padStart(2, '0');
  return `#${h(r1 + (r2 - r1) * t)}${h(g1 + (g2 - g1) * t)}${h(b1 + (b2 - b1) * t)}`;
};
const roomTones = (c) => {
  const p = PAL[c];
  return { k: '#050507', m: p.main, d: mixHex(p.main, '#000000', 0.5), l: mixHex(p.main, '#ffffff', 0.5), a: p.acc, A: mixHex(p.acc, '#000000', 0.5),
    w: '#ffffff', g: '#ffc933', G: '#b8860b', y: mixHex(p.acc, p.wall, 0.72), T: '#ffffff',
    D: mixHex(p.main, '#000000', 0.78), L: mixHex(p.main, '#ffffff', 0.8), h: mixHex(p.acc, '#ffffff', 0.5) };
};
const ell = (g, cx, cy, rx, ry, ch) => { for (let y = 0; y < g.h; y++) for (let x = 0; x < g.w; x++) if (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1) g.set(x, y, ch); return g; };
const gild = (g) => { for (let y = 0; y < g.h; y++) for (let x = 0; x < g.w; x++) { const v = g.get(x, y); if (v === 'm') g.set(x, y, 'g'); else if (v === 'd') g.set(x, y, 'G'); } return g; };

function roomGrid(slot, c, lv) {
  let g;
  switch (slot) {
    case 'window': {
      g = Grid(24, 20);
      if (c === 'void') { g.disc(11.5, 9.5, 9.5, 'm').disc(11.5, 9.5, 7.5, 'y').vline(11, 2, 16, 'm').vline(12, 2, 16, 'm').hline(4, 9, 16, 'm').hline(4, 10, 16, 'm'); }
      else if (c === 'oracle') { g.rect(1, 7, 22, 13, 'm').disc(11.5, 8, 10.5, 'm').rect(3, 8, 18, 11, 'y').disc(11.5, 9, 8.5, 'y').vline(11, 2, 18, 'm').vline(12, 2, 18, 'm').hline(3, 11, 18, 'm'); }
      else if (c === 'glitch') { g.rect(0, 0, 24, 20, 'm').rect(2, 2, 20, 16, 'y'); for (let y = 3; y < 17; y += 3) g.hline(2, y, 20, 'A'); g.vline(11, 2, 16, 'm').vline(12, 2, 16, 'm'); }
      else if (c === 'cinder') { g.rect(0, 0, 24, 20, 'd').rect(2, 2, 20, 16, 'y').vline(7, 2, 16, 'm').vline(16, 2, 16, 'm').hline(2, 9, 20, 'm').hline(2, 10, 20, 'm'); }
      else { g.rect(0, 0, 24, 20, 'm').rect(2, 2, 20, 16, 'y').vline(11, 2, 16, 'm').vline(12, 2, 16, 'm').hline(2, 9, 20, 'm').hline(2, 10, 20, 'm'); }
      if (lv >= 2) { g.hline(0, 19, 24, 'l'); g.rect(2, 2, 3, 15, 'a').rect(19, 2, 3, 15, 'a').vline(3, 2, 15, 'A').vline(20, 2, 15, 'A'); }
      if (lv >= 3) { for (let i = 0; i < 6; i++) g.set(6 + i, 4 + i, 'w').set(7 + i, 4 + i, 'w'); }
      if (lv >= 4) g.set(9, 4, 'T').set(15, 13, 'T').set(7, 14, 'T');
      break;
    }
    case 'art': {
      g = Grid(22, 15);
      g.rect(0, 0, 22, 15, 'm').rect(2, 2, 18, 11, 'd');
      if (c === 'blink') g.rect(4, 3, 4, 4, 'a').rect(9, 4, 4, 4, 'l').rect(14, 3, 4, 5, 'w').hline(5, 10, 12, 'l');
      else if (c === 'void') { g.line(5, 4, 11, 3, 'l').line(11, 3, 16, 7, 'l').line(5, 4, 8, 9, 'l').set(5, 4, 'w').set(11, 3, 'w').set(16, 7, 'w').set(8, 9, 'w').set(14, 10, 'a'); }
      else if (c === 'glitch') g.rect(5, 4, 3, 3, 'a').rect(14, 4, 3, 3, 'a').rect(5, 9, 12, 2, 'a');
      else if (c === 'cinder') g.stamp(8, 3, ['...a...', '..aaa..', '.aaaaa.', 'aaGGGaa', 'aaGGGaa', '.aaaaa.'], {});
      else { ell(g, 10.5, 7, 7.5, 3.6, 'l'); g.disc(10.5, 7, 2, 'a').set(10, 6, 'w'); }
      if (lv >= 2) g.hline(0, 0, 22, 'l').vline(0, 0, 15, 'l');
      if (lv >= 3) g.hline(2, 2, 18, 'l');
      if (lv >= 4) g.set(18, 3, 'T').set(5, 11, 'T');
      break;
    }
    case 'shelf': {
      g = Grid(38, 7);
      g.rect(0, 5, 38, 2, 'd').hline(0, 5, 38, 'l');
      if (c === 'cinder') { g.disc(3, 3, 2, 'm').disc(8, 3, 2, 'd'); if (lv >= 2) g.disc(14, 3, 2, 'm').disc(11, 1, 1.5, 'l'); }
      else if (c === 'void') { g.rect(2, 1, 4, 4, 'y').rect(2, 0, 4, 1, 'm').rect(8, 2, 3, 3, 'y').rect(8, 1, 3, 1, 'm'); if (lv >= 2) g.rect(13, 0, 4, 5, 'y').rect(13, 0, 4, 1, 'm').set(14, 3, 'a'); }
      else { g.rect(2, 1, 2, 4, 'm').rect(4, 2, 2, 3, 'a').rect(6, 0, 3, 5, 'l').rect(10, 1, 2, 4, 'd'); if (lv >= 2) g.rect(13, 2, 2, 3, 'a').rect(15, 1, 3, 4, 'm'); }
      if (lv >= 3) g.rect(22, 2, 4, 3, 'd').set(23, 1, 'a').set(24, 0, 'a').set(25, 1, 'a');
      if (lv >= 4) g.set(31, 2, 'T').set(30, 1, 'T');
      if (lv >= 5) g.rect(30, 1, 4, 4, 'g').rect(29, 2, 1, 2, 'g').rect(34, 2, 1, 2, 'g').rect(31, 5, 2, 1, 'G');
      break;
    }
    case 'lamp': {
      g = Grid(8, 26);
      g.vline(3, 10, 14, 'd').vline(4, 10, 14, 'd').rect(1, 24, 6, 2, 'd');
      if (c === 'blink') g.rect(2, 3, 4, 2, 'm').rect(1, 5, 6, 2, 'm').rect(0, 7, 8, 2, 'm').hline(2, 3, 4, 'l').set(3, 9, 'w').set(4, 9, 'w');
      else if (c === 'void') { g.disc(3.5, 6, 3.6, 'l').hline(0, 6, 8, 'a').set(2, 4, 'w'); }
      else if (c === 'glitch') { g.rect(2, 0, 4, 22, 'a').vline(3, 1, 20, 'w').rect(0, 23, 8, 3, 'd'); }
      else if (c === 'cinder') { g.rect(1, 5, 6, 9, 'm').rect(2, 6, 4, 7, 'y').hline(2, 3, 4, 'd').rect(3, 2, 2, 1, 'd').set(3, 10, 'a').set(4, 9, 'a').set(4, 10, 'a'); }
      else { g.hline(0, 12, 8, 'd'); [0, 3, 6].forEach((x) => { g.rect(x, 7, 2, 5, 'l').set(x, 5, 'a').set(x, 6, 'a'); }); }
      if (lv >= 2) g.set(1, 8, 'w');
      if (lv >= 3) g.set(3, 4, 'w').set(2, 6, 'w').set(5, 7, 'w');
      if (lv >= 4) g.set(3, 1, 'T').set(5, 2, 'T');
      if (lv >= 5) g.rect(1, 24, 6, 2, 'g');
      break;
    }
    case 'seat': {
      g = Grid(20, 12);
      if (c === 'blink') g.rect(2, 0, 16, 8, 'm').rect(0, 5, 20, 5, 'd').rect(3, 6, 14, 3, 'm').rect(1, 10, 2, 2, 'd').rect(17, 10, 2, 2, 'd').hline(2, 0, 16, 'l');
      else if (c === 'void') { g.disc(9.5, 11.5, 9.8, 'm').clearBelow(12).rect(3, 7, 14, 3, 'l').hline(0, 11, 20, 'd'); }
      else if (c === 'glitch') g.rect(5, 0, 10, 7, 'm').rect(2, 7, 16, 2, 'd').rect(9, 9, 2, 2, 'd').hline(3, 11, 14, 'd').hline(5, 0, 10, 'l');
      else if (c === 'cinder') { g.rect(0, 3, 20, 8, 'd').hline(0, 3, 20, 'm').disc(2.5, 7, 3.4, 'l').set(2, 7, 'd'); }
      else { g.rect(1, 6, 18, 5, 'm').rect(3, 2, 14, 5, 'l').set(10, 0, 'a').set(10, 1, 'a'); }
      if (lv >= 2) g.rect(6, 3, 8, 2, 'a');
      if (lv >= 3) g.rect(14, 1, 4, 4, 'A').set(15, 2, 'w');
      if (lv >= 4) g.set(4, 4, 'T');
      if (lv >= 5) gild(g);
      break;
    }
    case 'rug': {
      g = Grid(60, 7);
      ell(g, 29.5, 3, 30, 3.5, 'd');
      if (lv >= 2) ell(g, 29.5, 3, 24, 2.4, 'm');
      if (lv >= 3) { for (let x = 8; x < 52; x += 4) g.set(x, 3, c === 'oracle' || c === 'void' ? 'a' : 'l'); }
      if (lv >= 4) g.set(12, 2, 'T').set(47, 4, 'T');
      if (lv >= 5) { for (let x = 3; x < 58; x += 2) { g.set(x, 6, 'g'); g.set(x, 0, 'g'); } }
      return g.outline('k');
    }
    case 'desk': {
      g = Grid(32, 17);
      g.rect(0, 10, 32, 2, 'm').hline(0, 10, 32, 'l').rect(2, 12, 2, 5, 'd').rect(28, 12, 2, 5, 'd').rect(20, 12, 6, 4, 'd').rect(9, 0, 14, 9, 'd').rect(10, 1, 12, 7, 'y').rect(15, 9, 2, 1, 'd');
      if (lv >= 2) g.hline(11, 3, 8, 'a').hline(11, 5, 6, 'a');
      if (lv >= 3) g.rect(3, 7, 3, 3, 'w').set(6, 8, 'w').set(4, 5, 'T');
      if (lv >= 4) g.vline(28, 3, 7, 'd').rect(27, 2, 3, 1, 'l').set(26, 4, 'T');
      if (lv >= 5) g.rect(24, 5, 3, 3, 'g').rect(25, 8, 1, 2, 'G');
      break;
    }
    case 'plant': {
      g = Grid(8, 14);
      g.rect(1, 10, 6, 4, 'd').hline(1, 10, 6, 'l');
      if (c === 'void') { g.disc(3.5, 5, 3.6, 'y').set(2, 4, 'a').set(4, 6, 'a').set(3, 3, 'w'); if (lv >= 2) g.set(5, 4, 'a').set(2, 7, 'a'); if (lv >= 3) g.set(3, 5, 'l'); }
      else if (c === 'glitch') { g.vline(3, 3, 7, 'a').vline(4, 3, 7, 'a'); if (lv >= 2) g.rect(0, 5, 3, 1, 'a').set(0, 4, 'a'); if (lv >= 3) g.rect(5, 6, 3, 1, 'a').set(7, 5, 'a'); }
      else if (c === 'cinder') { g.vline(3, 4, 6, 'd').vline(4, 5, 5, 'd'); g.set(2, 3, 'a').set(5, 4, 'a'); if (lv >= 2) g.set(1, 5, 'a').set(6, 6, 'a'); if (lv >= 3) g.set(3, 2, 'g').set(4, 3, 'g'); }
      else if (c === 'oracle') { g.vline(4, 4, 6, 'a'); g.set(3, 3, 'l').set(2, 4, 'l').set(2, 5, 'l').set(3, 6, 'l'); if (lv >= 2) g.set(5, 6, 'a').set(6, 5, 'a'); if (lv >= 3) g.set(4, 2, 'l'); }
      else { g.vline(4, 5, 5, 'a'); g.rect(1, 4, 3, 2, 'a'); g.rect(5, 3, 3, 2, 'a'); if (lv >= 2) g.rect(2, 7, 2, 1, 'a').rect(5, 7, 2, 1, 'a'); if (lv >= 3) g.vline(4, 1, 4, 'a'); }
      if (lv >= 4) g.set(4, 0, 'T');
      if (lv >= 5) g.set(2, 8, 'g').set(6, 2, 'g');
      break;
    }
    default: { // sig
      g = Grid(11, 22);
      if (c === 'blink') { g.rect(0, 16, 11, 3, 'm').rect(1, 13, 10, 3, 'a').rect(0, 10, 10, 3, 'l').vline(9, 4, 6, 'w'); if (lv >= 2) g.vline(2, 8, 2, 'a'); if (lv >= 3) g.rect(3, 8, 3, 2, 'T'); }
      else if (c === 'void') { g.line(5, 12, 1, 21, 'd').line(5, 12, 9, 21, 'd').vline(5, 12, 9, 'd').line(2, 10, 9, 3, 'm').line(3, 10, 10, 3, 'm').disc(10, 2, 1.2, 'l'); if (lv >= 2) g.set(1, 9, 'w'); if (lv >= 3) g.ring(10, 2, 3, 'A'); }
      else if (c === 'glitch') { g.rect(1, 0, 9, 19, 'm').rect(2, 2, 7, 7, 'y').rect(5, 12, 1, 2, 'd').set(5, 11, 'a').rect(0, 19, 11, 3, 'd'); if (lv >= 2) g.set(3, 15, 'a').set(7, 15, 'a'); if (lv >= 3) g.hline(1, 0, 9, 'a'); }
      else if (c === 'cinder') { g.rect(0, 6, 11, 16, 'd').rect(2, 12, 7, 8, 'k').rect(7, 0, 3, 6, 'm'); if (lv >= 2) g.rect(3, 14, 5, 5, 'a'); if (lv >= 3) g.rect(4, 15, 3, 3, 'g'); }
      else { g.rect(2, 17, 7, 5, 'd').hline(2, 17, 7, 'l').disc(5, 10, 4.2, 'l').set(3, 8, 'w').set(4, 8, 'w'); if (lv >= 2) g.set(1, 3, 'w').set(9, 5, 'w'); if (lv >= 3) g.ring(5, 10, 6, 'A'); }
      if (lv >= 4) g.set(5, 0, 'T');
      break;
    }
  }
  if (lv >= 5 && slot !== 'rug') gild(g);
  return g.outline('k');
}

const RPOSX = { window: [60, 60], art: [350, 62], shelf: [596, 92], lamp: [24, 332], seat: [84, 332], rug: [370, 332], desk: [596, 332], plant: [808, 332], sig: [500, 332] };

function roomBox(c, slot, lv) {
  const g = roomGrid(slot, c, lv), [x0, y0] = RPOSX[slot], w = g.w * PXR, h = g.h * PXR;
  const x = slot === 'rug' ? 410 - w / 2 : x0, y = slot === 'rug' ? 394 - h / 2 : (['window', 'art', 'shelf'].includes(slot) ? y0 : y0 - h);
  return { g, w, h, x: Math.round(x), y: Math.round(y) };
}

const layoutDx = (c, slot) => ((ST.layout[c] || {})[slot]) || 0;
const roomSum = (c) => (ST.preview ? 9 * ST.preview : PLACES.reduce((a, s) => a + ST.levels[c][s], 0));
const roomTier = (c) => { const n = roomSum(c); return n >= 40 ? 5 : n >= 32 ? 4 : n >= 24 ? 3 : n >= 16 ? 2 : 1; };
const TIERNAME = ['Bare walls', 'Wallpaper', 'Panelled walls', 'Hanging decor', 'Light and dust: a lived-in room'];

// where the shop things stand: wall pieces from their top left, floor pieces from their bottom left
const EXPOS = { tv: ['floor', 216], calendar: ['wall', 236, 62], clock: ['wall', 506, 66], radio: ['wall', 100, 198], tank: ['wall', 700, 28] };
function extraGrid(id, c) {
  let g;
  if (id === 'tv') {
    g = Grid(20, 22); g.rect(0, 15, 20, 7, 'd').hline(0, 15, 20, 'l').rect(2, 17, 7, 4, 'D').rect(11, 17, 7, 4, 'D').set(5, 19, 'g').set(14, 19, 'g');
    g.rect(1, 2, 18, 13, 'm').rect(3, 4, 14, 9, 'D').rect(4, 5, 12, 7, 'y').line(7, 2, 4, 0, 'd').line(12, 2, 15, 0, 'd').set(17, 6, 'l').set(17, 8, 'l');
    g.set(6, 7, 'w').set(7, 7, 'w').set(5, 8, 'w').set(12, 9, 'T').set(9, 6, 'T');
  } else if (id === 'calendar') {
    g = Grid(12, 14); g.rect(0, 1, 12, 13, 'w').rect(0, 1, 12, 4, 'm').set(2, 0, 'k').set(9, 0, 'k');
    for (let r = 0; r < 4; r++) for (let q = 0; q < 5; q++) g.set(1 + q * 2, 6 + r * 2, 'd'); g.rect(5, 10, 2, 2, 'm').set(5, 10, 'L');
  } else if (id === 'clock') {
    g = Grid(9, 9); g.disc(4, 4, 4, 'm').disc(4, 4, 3, 'w'); const d = new Date(), mi = d.getMinutes(), hr = (d.getHours() % 12) + mi / 60;
    g.line(4, 4, 4 + Math.round(3 * Math.sin(mi / 60 * Math.PI * 2)), 4 - Math.round(3 * Math.cos(mi / 60 * Math.PI * 2)), 'k'); g.line(4, 4, 4 + Math.round(2 * Math.sin(hr / 12 * Math.PI * 2)), 4 - Math.round(2 * Math.cos(hr / 12 * Math.PI * 2)), 'D'); g.set(4, 4, 'g');
  } else if (id === 'radio') {
    g = Grid(10, 7); g.rect(0, 1, 10, 6, 'm').rect(1, 2, 4, 4, 'D').dither(1, 2, 4, 4, 'd', 'D').rect(6, 2, 3, 2, 'y').set(7, 5, 'w').set(8, 5, 'w').line(8, 1, 9, 0, 'd');
    if (ST.radio) g.set(2, 0, 'T').set(4, 0, 'T');
  } else if (id === 'tank') {
    g = Grid(14, 9); g.rect(0, 0, 14, 9, 'l').rect(1, 1, 12, 7, 'y').rect(1, 7, 12, 1, 'h').set(3, 6, 'A').set(3, 5, 'A').set(10, 6, 'A').set(10, 5, 'A');
    const fish = 1 + Math.floor(ST.sprints / 5); for (let i = 0; i < Math.min(4, fish); i++) { const x = 3 + i * 2 + (i % 2) * 2; g.set(x, 3 + (i % 2), 'g').set(x + 1, 3 + (i % 2), 'g').set(x - 1, 2 + (i % 2), 'G'); }
    g.set(11, 2, 'T').set(6, 4, 'T');
  }
  return g.outline('k');
}
function extraBox(c, id) {
  const g = extraGrid(id, c), t = EXPOS[id], w = g.w * PXR, h = g.h * PXR, x = t[1], y = t[0] === 'floor' ? 332 - h : t[2];
  return { g, w, h, x, y };
}
function extraSVG(c, id, sel) {
  const b = extraBox(c, id), tn = roomTones(c), dx = Math.max(-b.x, Math.min(880 - b.w - b.x, layoutDx(c, id)));
  const rects = b.g.runs().map((r) => `<rect x="${r.x * PXR}" y="${r.y * PXR}" width="${r.w * PXR + 0.5}" height="${PXR + 0.5}" fill="${tn[r.ch]}"${r.ch === 'T' ? ' class="tw"' : ''}/>`).join('');
  return `<g class="rm${sel ? ' sel' : ''}${ST.justBought === id ? ' just' : ''}" data-a="extra" data-v="${id}" data-x="${b.x + dx}" data-y="${b.y}" data-w="${b.w}" data-x0="${b.x}" transform="translate(${b.x + dx} ${b.y})" shape-rendering="crispEdges">
    <rect x="-6" y="-6" width="${b.w + 12}" height="${b.h + 12}" fill="transparent"/><rect class="arrbox" x="-3" y="-3" width="${b.w + 6}" height="${b.h + 6}" fill="none" stroke="#fff" stroke-width="2" stroke-dasharray="6 6"/>
    <g class="in${ST.justBought === id ? ' build' : ''}">${rects}</g></g>`;
}
function petGrid(c) {
  const g = Grid(11, 9);
  if (c === 'blink') { g.rect(1, 4, 7, 4, 'm').rect(7, 2, 4, 4, 'm').set(7, 1, 'm').set(10, 1, 'm').set(9, 3, 'k').set(10, 3, 'k').line(1, 5, 0, 2, 'm').set(8, 4, 'w'); }
  else if (c === 'void') { g.disc(5, 4, 3.4, 'm').ellipse(5, 4, 5, 1.6, 'l').set(4, 3, 'w').set(8, 1, 'T'); }
  else if (c === 'glitch') { g.rect(2, 3, 7, 4, 'm').rect(0, 5, 2, 1, 'd').rect(9, 5, 2, 1, 'd').set(3, 1, 'l').set(7, 1, 'l').set(3, 4, 'k').set(7, 4, 'k').set(5, 8, 'T'); }
  else if (c === 'cinder') { g.rect(1, 4, 8, 3, 'm').rect(8, 3, 3, 3, 'm').line(1, 5, 0, 7, 'g').set(0, 8, 'g').set(9, 4, 'k').rect(2, 7, 2, 1, 'd').rect(6, 7, 2, 1, 'd'); }
  else { g.fillEllipse(5, 5, 3.6, 3.4, 'm').rect(2, 0, 2, 2, 'm').rect(7, 0, 2, 2, 'm').rect(3, 3, 2, 2, 'w').rect(6, 3, 2, 2, 'w').set(4, 4, 'k').set(7, 4, 'k').set(5, 6, 'g'); }
  return g.outline('k');
}
const PETY = 440;
function petSVG(c, x) {
  const g = petGrid(c), tn = roomTones(c), sleepy = ST.at === 'seat';
  const rects = g.runs().map((r) => `<rect x="${r.x * 5}" y="${r.y * 5}" width="${r.w * 5 + 0.4}" height="5.4" fill="${tn[r.ch]}"${r.ch === 'T' ? ' class="tw"' : ''}/>`).join('');
  return `<g class="rm pet" data-a="extra" data-v="pet" transform="translate(${x} ${PETY})" shape-rendering="crispEdges"><g class="in petbob"><rect x="-4" y="-4" width="64" height="52" fill="transparent"/>${rects}${sleepy ? '<text x="40" y="-6" fill="#fff" font-size="14" font-family="Doto,monospace" class="tw">z</text>' : ''}</g></g>`;
}

// ---- the walls, the floor and what hangs on them: more of it as the room grows ----
function roomBg(c, tier, withProps) {
  const p = PAL[c], m = p.main;
  const wall = {
    blink: `<rect width="40" height="40" fill="${p.wall}"/><rect width="4" height="40" fill="${m}" opacity=".05"/><rect x="22" y="10" width="4" height="4" fill="${m}" opacity=".08"/><rect x="8" y="28" width="4" height="4" fill="${m}" opacity=".06"/>`,
    void: `<rect width="60" height="60" fill="${p.wall}"/><rect x="8" y="10" width="2" height="2" fill="#fff" opacity=".4"/><rect x="40" y="30" width="4" height="4" fill="#fff" opacity=".18"/><rect x="24" y="48" width="2" height="2" fill="#fff" opacity=".3"/><rect x="52" y="6" width="2" height="2" fill="${m}" opacity=".5"/>`,
    glitch: `<rect width="48" height="48" fill="${p.wall}"/><rect width="48" height="2" fill="${m}" opacity=".08"/><rect width="2" height="48" fill="${m}" opacity=".08"/><rect x="20" y="20" width="6" height="6" fill="${m}" opacity=".07"/>`,
    cinder: `<rect width="60" height="24" fill="${p.wall}"/><rect width="60" height="2" fill="#000" opacity=".25"/><rect x="0" y="12" width="2" height="12" fill="#000" opacity=".2"/><rect x="30" y="0" width="2" height="12" fill="#000" opacity=".2"/><rect x="6" y="4" width="14" height="4" fill="${m}" opacity=".04"/>`,
    oracle: `<rect width="48" height="48" fill="${p.wall}"/><polygon points="24,6 40,24 24,42 8,24" fill="${m}" opacity=".06"/><rect x="22" y="22" width="4" height="4" fill="${m}" opacity=".12"/>`,
  }[c];
  const wsz = { blink: [40, 40], void: [60, 60], glitch: [48, 48], cinder: [60, 24], oracle: [48, 48] }[c];
  const floor = {
    blink: `<rect width="120" height="22" fill="${p.floor}"/><rect y="20" width="120" height="2" fill="#000" opacity=".35"/><rect x="60" width="2" height="20" fill="#000" opacity=".25"/><rect x="0" y="6" width="120" height="2" fill="${m}" opacity=".04"/>`,
    void: `<rect width="48" height="48" fill="${p.floor}"/><rect width="48" height="2" fill="${m}" opacity=".12"/><rect width="2" height="48" fill="${m}" opacity=".12"/><rect x="22" y="22" width="4" height="4" fill="${m}" opacity=".2"/>`,
    glitch: `<rect width="54" height="36" fill="${p.floor}"/><rect width="54" height="2" fill="${m}" opacity=".16"/><rect x="26" width="2" height="36" fill="${m}" opacity=".12"/><rect x="26" y="16" width="8" height="2" fill="${m}" opacity=".3"/><rect x="34" y="16" width="2" height="12" fill="${m}" opacity=".3"/>`,
    cinder: `<rect width="72" height="30" fill="${p.floor}"/><rect y="28" width="72" height="2" fill="#000" opacity=".4"/><rect x="36" width="2" height="28" fill="#000" opacity=".3"/><rect x="6" y="6" width="22" height="3" fill="${m}" opacity=".05"/>`,
    oracle: `<rect width="48" height="36" fill="${p.floor}"/><rect width="48" height="2" fill="${m}" opacity=".1"/><rect x="22" y="14" width="4" height="8" fill="${m}" opacity=".12"/><rect x="18" y="16" width="12" height="4" fill="${m}" opacity=".08"/>`,
  }[c];
  const fsz = { blink: [120, 22], void: [48, 48], glitch: [54, 36], cinder: [72, 30], oracle: [48, 36] }[c];
  let out = `<defs><pattern id="wp" width="${wsz[0]}" height="${wsz[1]}" patternUnits="userSpaceOnUse">${wall}</pattern><pattern id="fl" width="${fsz[0]}" height="${fsz[1]}" patternUnits="userSpaceOnUse">${floor}</pattern></defs>`;
  out += `<rect width="880" height="500" fill="${p.wall}"/>`;
  if (tier >= 2) out += '<rect width="880" height="332" fill="url(#wp)"/>';
  out += `<rect y="332" width="880" height="168" fill="${p.floor}"/>`;
  if (tier >= 2) out += '<rect y="332" width="880" height="168" fill="url(#fl)"/>';
  if (tier >= 3) {
    out += `<rect y="250" width="880" height="72" fill="${m}" opacity=".05"/><rect y="246" width="880" height="6" fill="${m}" opacity=".16"/><rect y="322" width="880" height="10" fill="${m}" opacity=".18"/><rect y="322" width="880" height="2" fill="#fff" opacity=".12"/>`;
    for (let x = 40; x < 880; x += 110) out += `<rect x="${x}" y="260" width="4" height="52" fill="#000" opacity=".25"/><rect x="${x + 4}" y="260" width="2" height="52" fill="${m}" opacity=".08"/>`;
  }
  out += `<rect y="330" width="880" height="6" fill="${m}" opacity=".2"/>`;
  if (tier >= 5) {
    out += '<polygon points="76,190 214,190 400,332 210,332" fill="#fff" opacity=".05"/><polygon points="96,190 150,190 280,332 214,332" fill="#fff" opacity=".04"/>';
    for (let i = 0; i < 9; i++) out += `<rect class="mote" x="${120 + i * 70}" y="${120 + (i * 37) % 170}" width="4" height="4" fill="#fff" opacity=".3" style="animation-delay:${i * 0.7}s"/>`;
  }
  if (withProps && tier >= 4) out += roomProps(c);
  return out;
}
// the room's own hanging decor, one kind per character
function roomProps(c) {
  const tn = roomTones(c), draw = (g, x, y, cls) => `<g transform="translate(${x} ${y})" shape-rendering="crispEdges"${cls ? ` class="${cls}"` : ''}>${g.outline('k').runs().map((r) => `<rect x="${r.x * PXR}" y="${r.y * PXR}" width="${r.w * PXR + 0.5}" height="${PXR + 0.5}" fill="${tn[r.ch]}"${r.ch === 'T' ? ' class="tw"' : ''}/>`).join('')}</g>`;
  if (c === 'blink') {
    let s = '<path d="M20 12 Q 400 52 860 12" fill="none" stroke="#fff" stroke-opacity=".3" stroke-width="2"/>';
    for (let i = 0; i < 12; i++) { const x = 30 + i * 70, y = 12 + 38 * Math.sin((x / 880) * Math.PI); s += `<polygon points="${x},${y} ${x + 24},${y} ${x + 12},${y + 26}" fill="${['#ffd83d', '#8fd4ff', '#ff7ab6', '#5fcf8f'][i % 4]}" opacity=".85"/>`; }
    return s;
  }
  if (c === 'void') {
    const g1 = Grid(8, 8).disc(4, 4, 3.4, 'm').set(3, 3, 'w'), g2 = Grid(6, 6).disc(3, 3, 2.6, 'l').set(2, 2, 'w'), g3 = Grid(10, 6).disc(5, 3, 2.6, 'd').ellipse(5, 3, 4.6, 1.2, 'w');
    return `<g class="sway"><line x1="360" y1="0" x2="360" y2="30" stroke="#fff" stroke-opacity=".3"/>${draw(g1, 336, 30)}<line x1="430" y1="0" x2="430" y2="62" stroke="#fff" stroke-opacity=".3"/>${draw(g2, 414, 62)}<line x1="486" y1="0" x2="486" y2="44" stroke="#fff" stroke-opacity=".3"/>${draw(g3, 456, 44)}</g>`;
  }
  if (c === 'glitch') {
    const L = { O: ['.mmm.', 'm...m', 'm...m', 'm...m', '.mmm.'], N: ['m...m', 'mm..m', 'm.m.m', 'm..mm', 'm...m'], L: ['m....', 'm....', 'm....', 'm....', 'mmmmm'], I: ['mmm', '.m.', '.m.', '.m.', 'mmm'], E: ['mmmmm', 'm....', 'mmm..', 'm....', 'mmmmm'] };
    const g = Grid(40, 5); let x = 0; for (const ch of 'ONLINE') { const gl = L[ch]; g.stamp(x, 0, gl); x += gl[0].length + 1; }
    return draw(g, 270, 22, 'flick') + '<path d="M0 60 Q 80 90 160 60 T 320 70" fill="none" stroke="#5fd0e6" stroke-opacity=".35" stroke-width="3"/>';
  }
  if (c === 'cinder') {
    const pan = Grid(10, 10).disc(4, 5, 4, 'D').disc(4, 5, 3, 'd').rect(7, 4, 3, 2, 'D').set(3, 3, 'l'), pot = Grid(8, 7).rect(0, 1, 8, 6, 'm').rect(1, 0, 6, 1, 'D').hline(0, 3, 8, 'd').set(1, 2, 'L');
    return `<rect x="300" y="14" width="230" height="4" fill="#000" opacity=".4"/>${draw(pan, 318, 18)}${draw(pot, 394, 18)}${draw(pan, 452, 18)}`;
  }
  const ch = Grid(18, 12); ch.hline(0, 0, 18, 'g'); [[2, 'm'], [6, 'l'], [9, 'm'], [12, 'l'], [16, 'm']].forEach(([x, k], i) => { ch.vline(x, 1, 3 + (i % 2), 'd'); ch.rect(x - 1, 4 + (i % 2), 3, 3, k); ch.set(x, 5 + (i % 2), 'w'); });
  ch.set(9, 9, 'T').set(3, 8, 'T');
  return draw(ch, 340, 10);
}
const CONF = Array.from({ length: 44 }, (_, i) => [((i * 97) % 880), (i * 0.07) % 1.4, ['#ffd83d', '#8fd4ff', '#ff7ab6', '#5fcf8f', '#a98cff'][i % 5]]);

function roomItemSVG(c, slot, lv, sel) {
  const shown = ST.preview || lv, b = roomBox(c, slot, shown), tn = roomTones(c);
  const dx = Math.max(-b.x, Math.min(880 - b.w - b.x, layoutDx(c, slot)));
  const altArt = slot === 'art' && ST.eggs.poster;
  const g = altArt ? wantedArt(c) : b.g;
  const rects = g.runs().map((r) => `<rect x="${r.x * PXR}" y="${r.y * PXR}" width="${r.w * PXR + 0.5}" height="${PXR + 0.5}" fill="${tn[r.ch]}"${r.ch === 'T' ? ' class="tw"' : ''}/>`).join('');
  const lit = slot === 'lamp' && (ST.at === 'lamp' || ST.discoUntil > Date.now());
  const cls = ['rm', sel ? 'sel' : '', ST.at === slot ? 'use' : '', ST.justUp === slot ? 'just' : ''].join(' ');
  return `<g class="${cls}" data-a="ritem" data-v="${slot}" data-x="${b.x + dx}" data-y="${b.y}" data-w="${b.w}" data-x0="${b.x}" transform="translate(${b.x + dx} ${b.y})" shape-rendering="crispEdges">
    <rect x="-6" y="-6" width="${b.w + 12}" height="${b.h + 12}" fill="transparent"/><rect class="arrbox" x="-3" y="-3" width="${b.w + 6}" height="${b.h + 6}" fill="none" stroke="#fff" stroke-width="2" stroke-dasharray="6 6"/>
    <g class="in${ST.justUp === slot ? ' build' : ''}">${rects}</g>${lit ? `<circle cx="${b.w / 2}" cy="${b.h * 0.2}" r="60" fill="#ffd83d" opacity=".14"/>` : ''}</g>`;
}
function wantedArt(c) {
  const g = Grid(22, 15); g.rect(0, 0, 22, 15, 'w').rect(1, 1, 20, 2, 'k').rect(6, 4, 10, 7, 'm').disc(11, 7, 3, 'l').set(10, 6, 'k').set(12, 6, 'k').rect(9, 9, 4, 1, 'k').rect(3, 12, 16, 1, 'k').set(2, 4, 'g').set(19, 4, 'g');
  return g;
}
function roomSVG(c, sel, only) {
  const p = PAL[c], lv = ST.levels[c], tier = roomTier(c), mini = !!only;
  let items = (only || PLACES).map((sl) => roomItemSVG(c, sl, lv[sl], sel === sl)).join('');
  if (!mini) {
    items += SHOP_ITEMS.filter((id) => ST.owned[c][id] && EXPOS[id]).map((id) => extraSVG(c, id, sel === id)).join('');
    if (ST.owned[c].lights) { let s = '<path d="M12 6 Q 440 70 868 6" fill="none" stroke="#fff" stroke-opacity=".25" stroke-width="2"/>'; for (let i = 0; i < 20; i++) { const x = 24 + i * 42, y = 6 + 64 * Math.sin((x / 880) * Math.PI) * 0.97; s += `<rect class="tw" x="${x}" y="${y + 2}" width="6" height="8" fill="${[p.acc, p.main, '#ffc933', '#fff'][i % 4]}" style="animation-delay:${(i * 0.37) % 2}s;filter:drop-shadow(0 0 4px ${p.acc})"/>`; } items = s + items; }
    if (!ST.eggs.mouse || ST.mouseUntil > Date.now()) items += `<g data-a="egg" data-v="mouse" shape-rendering="crispEdges" class="mousehole"><rect x="18" y="312" width="24" height="18" fill="#000"/><rect x="18" y="312" width="24" height="3" fill="${p.main}" opacity=".5"/>${ST.mouseUntil > Date.now() ? '<rect class="mouseRun" x="22" y="322" width="10" height="6" fill="#bbb"/><rect class="mouseRun" x="30" y="320" width="3" height="3" fill="#fff"/>' : ''}</g>`;
    if (ST.owned[c].pet) items += petSVG(c, Math.max(30, Math.min(820, (ST.at ? slotCenterAny(c, ST.at) : 410) + 110)));
  }
  const dim = ST.at === 'lamp' && !mini ? '<rect width="880" height="500" fill="#02030a" opacity=".5"/>' : '';
  const disco = ST.discoUntil > Date.now() && !mini ? '<rect class="discoa" width="880" height="500"/>' : '';
  const conf = ST.confettiUntil > Date.now() && !mini ? CONF.map(([x, d, col]) => `<rect class="conf" x="${x}" y="-10" width="8" height="8" fill="${col}" style="animation-delay:${d}s"/>`).join('') : '';
  const star = ST.starUntil > Date.now() && !mini ? '<g class="shoot" data-a="egg" data-v="star"><rect width="10" height="10" fill="#fff"/><rect x="-46" y="2" width="44" height="4" fill="#fff" opacity=".6"/><rect x="-90" y="3" width="40" height="2" fill="#fff" opacity=".3"/><circle cx="4" cy="4" r="18" fill="#fff" opacity=".001"/></g>' : '';
  return `<svg class="roomsvg${ST.arrange && !mini ? ' arr' : ''}" viewBox="0 0 880 500" preserveAspectRatio="xMidYMid slice">${roomBg(c, tier, !mini)}${dim}${items}${disco}${star}${conf}</svg>`;
}
const slotCenter = (c, slot) => { const b = roomBox(c, slot, ST.levels[c][slot]); return b.x + layoutDx(c, slot) + b.w / 2; };
const slotCenterAny = (c, id) => (EXPOS[id] ? (() => { const b = extraBox(c, id); return b.x + layoutDx(c, id) + b.w / 2; })() : slotCenter(c, id));

// a small picture of each shop thing and game, drawn from the same pixel grids as the room
function thumbGrid(id, c) {
  if (id === 'lights') {
    const g = Grid(16, 8); g.hline(0, 1, 16, 'd').set(0, 0, 'd').set(15, 0, 'd');
    [1, 5, 9, 13].forEach((x, i) => { g.set(x, 2, 'k').rect(x - 1, 3, 3, 3, ['y', 'g', 'T', 'A'][i]); });
    return g;
  }
  if (id === 'pet') return petGrid(c);
  if (id === 'g-catch') {
    const g = Grid(13, 12); g.rect(5, 1, 3, 9, 'g').rect(2, 4, 9, 3, 'g').rect(3, 3, 7, 5, 'g').set(4, 4, 'L').set(5, 3, 'L').set(8, 7, 'G').set(9, 6, 'G');
    g.disc(10, 10, 1.5, 'k').set(11, 8, 'T');
    return g.outline('k');
  }
  if (id === 'g-pong') {
    const g = Grid(16, 12); g.rect(0, 0, 16, 12, 'D').rect(1, 2, 2, 6, 'm').rect(13, 4, 2, 6, 'l').rect(7, 5, 2, 2, 'w');
    for (let y = 0; y < 12; y += 2) g.set(8, y, 'd');
    return g;
  }
  if (id === 'g-memory') {
    const g = Grid(16, 12); [[0, 0], [8, 0], [0, 7], [8, 7]].forEach(([x, y], i) => { g.rect(x, y, 7, 5, i === 1 ? 'w' : 'm'); if (i === 1) g.set(x + 3, y + 2, 'A').set(x + 2, y + 1, 'A').set(x + 4, y + 1, 'A'); else g.set(x + 3, y + 2, 'l'); });
    return g.outline('k');
  }
  return extraGrid(id, c);
}
function thumbSVG(id, c, lock) {
  const g = thumbGrid(id, c), tn = roomTones(c), k = Math.max(1, Math.floor(50 / Math.max(g.w, g.h))), w = g.w * k, h = g.h * k;
  const rects = g.runs().map((r) => `<rect x="${r.x * k}" y="${r.y * k}" width="${r.w * k + 0.3}" height="${k + 0.3}" fill="${tn[r.ch]}"/>`).join('');
  return `<svg viewBox="${-(56 - w) / 2} ${-(56 - h) / 2} 56 56" width="56" height="56" shape-rendering="crispEdges" style="${lock ? 'opacity:.4;filter:grayscale(.8)' : ''}">${rects}</svg>`;
}

export const PXR_SIZE = PXR;
export { thumbSVG, thumbGrid, roomSVG, roomBox, extraBox, roomTier, roomSum, TIERNAME, layoutDx, roomGrid, roomTones, slotCenter, slotCenterAny, EXPOS, RPOSX, petGrid, extraGrid, SHOP_ITEMS };
