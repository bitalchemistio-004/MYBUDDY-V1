import { PIXEL_IDS, pixelExtent } from "./wardrobe-pixel.js";

// Where each decoration goes on each character's art and how it is adjusted for the place it is shown. Pure: no document, no clock,
// so the rules can be read and tested alone (shell/ui/tests/wardrobe-fit.test.mjs).
//
// The art is 320 x 240. A socket is the point on the body a kind of decoration attaches to, and the size of a decoration
// there. Each of the five characters is measured (its own sockets and geometry).
export const SOCK = {
  blink: { crown: [160, 64, 1.15], eyes: [160, 112, 1.7], neck: [160, 160, 1.1], back: [160, 112, 1.06], hand: [250, 135, 1], aura: [160, 112, 1.35] },
  void: { crown: [165, 38, 1.05], eyes: [165, 112, 1.4], neck: [165, 184, 1.1], back: [165, 112, 1], hand: [245, 140, 1], aura: [165, 112, 1.45] },
  glitch: { crown: [160, 58, 0.8], eyes: [160, 104, 1.15], neck: [160, 164, 0.85], back: [160, 112, 0.77], hand: [218, 136, 0.85], aura: [160, 112, 1.15] },
  cinder: { crown: [160, 12, 0.9], eyes: [165, 119, 1.3], neck: [160, 170, 0.9], back: [160, 100, 0.9], hand: [232, 140, 0.9], aura: [160, 100, 1.35] },
  oracle: { crown: [160, 44, 1.05], eyes: [160, 112, 1.4], neck: [160, 180, 1.05], back: [160, 112, 1], hand: [242, 146, 1], aura: [160, 112, 1.4] },
};
// How the shape of each item is fitted on top of its socket: [size factor, nudge x, nudge y] in art units. Worked out by the rules in
// wardrobe-audit.js against each character's own geometry and kept in step by a test; do not edit by hand (change the rules, or the drawing).
export const GEOM = {
  blink: { eyes: [63, 65, 194, 94], body: [63, 65, 194, 94], head: 65 },
  void: { eyes: [88, 78, 160, 66], body: [66, 38, 199, 149], head: 40 },
  glitch: { eyes: [96, 76, 130, 56], body: [95, 59, 131, 107], head: 60 },
  cinder: { eyes: [92, 88, 146, 62], body: [83, 7, 154, 170], head: 70 },
  oracle: { eyes: [80, 68, 160, 82], body: [69, 44, 182, 136], head: 46 },
};
export const ITEM_FIT = {
  blink: {
    "crown-crown": [0.986, 0, 7],
    "crown-beanie": [0.986, 0, 7],
    "crown-party": [0.758, 0, 7],
    "crown-wizard": [0.616, 0, 7],
    "crown-halo": [1, 0, 7],
    "crown-sprout": [1, 0, 7],
    "crown-antenna": [0.821, 0, 7],
    "crown-horns": [0.986, 0, 7],
    "eyes-glasses": [0.761, -2, 0],
    "eyes-shades": [0.697, 0, 0],
    "eyes-monocle": [0.676, 0, 0],
    "eyes-blush": [0.656, 0, -24],
    "eyes-freckles": [0.797, -4, 8],
    "eyes-scan": [0.669, 0, 0],
    "eyes-brows": [0.797, 0, -22],
    "neck-bowtie": [1, 0, 28.7],
    "neck-scarf": [1, 0, 12.2],
    "neck-medal": [0.815, 0, 7.1],
    "neck-bell": [0.985, 0, 8.8],
    "neck-bandana": [1, 0, 8.9],
    "back-cape": [1, 0, 0],
    "back-wings": [1, 0, 0],
    "back-pack": [1, 0, 0],
    "back-jet": [1, 0, 0],
    "hand-coffee": [1, 31, 0],
    "hand-balloon": [0.952, 35.6, 0],
    "hand-trophy": [0.769, 37, 0],
    "hand-wand": [0.833, 19.5, 0],
    "hand-book": [0.952, 38.4, 0],
    "hand-flag": [0.741, 13.7, 0],
    "aura-sparkles": [1, 0, 0],
    "aura-hearts": [1, 0, 0],
    "aura-bubbles": [1, 0, 0],
    "aura-orbit": [1, -1, 0],
    "aura-embers": [1, 0, 0],
    "aura-notes": [1, 0, 0],
  },
  void: {
    "crown-accretion": [1, 0, 8],
    "crown-comet": [0.718, 0, 8],
    "crown-dish": [0.525, 0, 8],
    "eyes-lens": [0.599, 3, -1],
    "eyes-starry": [0.745, 3, -1],
    "neck-orbitring": [1, 0, -20.2],
    "neck-pendant": [1, 0, -30.1],
    "back-nebula": [1, 0, 0],
    "back-solar": [1, 0, 0],
    "hand-planet": [0.821, 37.5, 0],
    "hand-scope": [0.767, 26, 0],
    "aura-constellation": [1, 0, 0],
    "aura-moons": [1, 0, 0],
    "aura-meteors": [1, 3.3, 0],
  },
  glitch: {
    "crown-bars": [1, 0, 8],
    "crown-pixelcrown": [1, 0, 8],
    "crown-headset": [0.809, 0, 6.1],
    "eyes-pixshades": [0.663, 1, -2],
    "eyes-xeyes": [0.873, 1, 0],
    "eyes-visor": [0.663, 1, -2],
    "neck-cable": [1, 0, -24.3],
    "neck-qr": [1, 0, -24.3],
    "back-wifi": [1, 0, 0],
    "back-circuit": [1, 0, 0],
    "hand-cursor": [1, 13.1, 0],
    "hand-usb": [1, 25.8, 0],
    "hand-floppy": [1, 36.1, 0],
    "aura-bitrain": [1, 0, 0],
    "aura-static": [1, 0, 0],
  },
  cinder: {
    "crown-flamecrown": [1, 0, 64],
    "crown-emberhalo": [1, 0, 64],
    "crown-chimney": [0.932, 0, 56.4],
    "eyes-goggles": [0.523, -6, 0],
    "eyes-soot": [0.686, -2, 0],
    "neck-ashscarf": [1, 0, -9.2],
    "neck-emberchain": [1, 0, -14.6],
    "back-phoenix": [1, 0, 0],
    "back-smoke": [1, 0, 0],
    "hand-marsh": [1, 16.8, 0],
    "hand-torch": [1, 27.6, 0],
    "hand-kettle": [1, 38.4, 0],
    "aura-sparks": [1, 0, 0],
    "aura-heat": [1, 0, 0],
  },
  oracle: {
    "crown-thirdeye": [0.648, 0, 12.1],
    "crown-gemband": [0.864, 0, 8],
    "crown-laurel": [0.707, 0, 8],
    "eyes-lashes": [0.638, 0, -21],
    "eyes-lids": [0.621, 0, -39],
    "eyes-runeye": [0.745, 0, -3],
    "neck-amulet": [1, 0, -20.6],
    "neck-runechain": [1, 0, -23.7],
    "back-cloak": [1, 0, 0],
    "back-rings": [1, 0, 0],
    "hand-ball": [1, 31, 0],
    "hand-scroll": [0.917, 36.5, 0],
    "hand-candle": [1, 22, 0],
    "aura-runes": [1, 0, 0],
    "aura-eyesfloat": [1, 0, 0],
  },
};
// A decoration that does not fit the space it is shown in is adjusted for that surface (size, and a nudge in art units).
// While resting the pill shows only the eyes (and the crown, which widens the crop), so the rest are not drawn there.
export const ADJ = {
  rest: { crown: { s: 0.78, dy: 8 } },
  peek: { crown: { s: 0.86, dy: 5 } },
  room: { crown: { s: 0.92, dy: 0 }, neck: { s: 0.85, dy: -8 }, hand: { s: 0.88, dy: -8 }, aura: { s: 0.9, dy: 0 } },
  app: {},
};
// The colours an item can have (ids are the ones core/wardrobe.py accepts). "char" is the character's own colour.
export const COLORS = { char: "#f2f2f0", white: "#ffffff", honey: "#ffd83d", mint: "#5fcf8f", rose: "#ff7ab6", sky: "#8fd4ff", violet: "#a98cff", ember: "#ff8a3d" };
const PIXEL = new Set(PIXEL_IDS);
const SMALL = new Set(["rest", "peek"]);
const BACK = new Set(["back", "aura"]);
export const SLOTS = ["back", "aura", "neck", "eyes", "crown", "hand"];   // drawing order

/** What to draw: [{slot, id, layer: "back"|"front", x, y, k, pixel, color}] for what is worn on this surface.
 *  `look` is {colors: {slot: colourId}, styles: {slot: "line"|"pixel"}, ink: colourId}: an item's own colour wins
 *  over the general ink, and pixel is used only for an item that has a pixel drawing. */
export function placements(character, worn, surface, look = {}) {
  const so = SOCK[character];
  if (!so || !worn) return [];
  const adj = ADJ[surface] || {};
  const out = [];
  for (const slot of SLOTS) {
    const id = worn[slot];
    if (!id || !so[slot]) continue;
    if (SMALL.has(surface) && slot !== "crown" && slot !== "eyes") continue;
    const [x0, y0, k0] = so[slot], a = adj[slot] || {}, [fs, fx, fy] = (ITEM_FIT[character] || {})[`${slot}-${id}`] || [1, 0, 0];
    const x = x0 + fx, y = y0 + fy, k = k0 * fs;
    const own = (look.colors || {})[slot] || "", general = look.ink || "char";
    out.push({ slot, id, layer: BACK.has(slot) ? "back" : "front", x, y: +(y + (a.dy || 0)).toFixed(1), k: +(k * (a.s || 1)).toFixed(3),
      pixel: (look.styles || {})[slot] === "pixel" && PIXEL.has(`${slot}-${id}`), color: COLORS[own] ? own : (COLORS[general] ? general : "char") });
  }
  return out;
}

/** The crop of the art the pill shows. A crown does not fit the eyes band, so the band grows upward to take it, and the
 *  height it is shown at grows with it (never by more than 14 px, so the pill stays calm) to keep the same scale. */
export function cropFor(character, worn, part, H, surface = "rest") {
  const so = SOCK[character];
  if (!so || !worn || !worn.crown) return { part, H };
  // the box the crown really covers (its drawn pixels, its fit and the surface's own adjustment), so it is never cut off
  const key = `crown-${worn.crown}`, ex = pixelExtent(key), [fs, fx, fy] = (ITEM_FIT[character] || {})[key] || [1, 0, 0], a = (ADJ[surface] || {}).crown || {};
  const [sx, sy, k0] = so.crown, k = k0 * fs * (a.s || 1), cx = sx + fx, cy = sy + fy + (a.dy || 0);
  const box = ex ? { l: cx + ex.left * k, r: cx + ex.right * k, t: cy + ex.top * k } : { l: sx - 46 * k0 * 0.8, r: sx + 46 * k0 * 0.8, t: sy - 58 * k0 * 0.8 + 8 };
  const x0 = Math.min(part[0], box.l - 2), x1 = Math.max(part[0] + part[2], box.r + 2), y0 = Math.min(part[1], box.t - 2), bottom = part[1] + part[3];
  const grown = [Math.round(x0), Math.round(y0), Math.round(x1 - x0), Math.round(bottom - y0)];
  const keep = H * (grown[3] / part[3]);
  return { part: grown, H: Math.round(Math.min(keep, H + 14)) };
}
