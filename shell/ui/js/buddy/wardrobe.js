// What the character wears, drawn over its sprite. The worn items come from the backend (core/wardrobe.py, carried in the
// live feed as `wardrobe`), already limited to what the user has earned; this module only draws them.
// Every character has a wardrobe of its own (core/wardrobe.py keeps what each wears).
import { DEFS } from "./wardrobe-defs.js";
import { placements, cropFor, COLORS } from "./wardrobe-fit.js";
import { PIXEL_IDS, pixelGroup } from "./wardrobe-pixel.js";

const CHAR_COLOUR = { blink: "#f2f2f0", void: "#a98cff", glitch: "#5fd0e6", cinder: "#ff8a3d", oracle: "#e8c15a" };
let live = { character: "blink", worn: {}, finish: "asis", ink: "char", colors: {}, styles: {}, rev: -1 };
let character = "blink", surface = "app", wrap = null, back = null, front = null;

function ensureDefs() {
  if (document.querySelector(".wd-defs")) return;
  document.body.insertAdjacentHTML("beforeend", DEFS);
  // the pixel drawings are built from their grids once, into the same hidden <defs>
  const defs = document.querySelector(".wd-defs defs");
  if (defs) defs.insertAdjacentHTML("beforeend", PIXEL_IDS.map((k) => pixelGroup(k)).join(""));
}

// The sprite-wrap is rebuilt when the character changes: the layers are put into the new one.
export function attach(spriteWrap) {
  ensureDefs();
  wrap = spriteWrap;
  const layer = (cls) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("class", `wd-lay ${cls}`); s.setAttribute("viewBox", "0 0 320 240"); s.setAttribute("aria-hidden", "true"); return s; };
  back = layer("wd-back"); front = layer("wd-front");
  const canvas = wrap.querySelector("canvas.sprite");
  if (canvas) { wrap.insertBefore(back, canvas); canvas.after(front); } else { wrap.append(back, front); }
  paint();
}

export function setCharacter(c) { if (c !== character) { character = c; paint(); } }
export function setSurface(s) { if (s !== surface) { surface = s; paint(); } }

/** Take what the live feed says. Returns true when something that changes the picture (or the crop) changed. */
export function apply(feed) {
  if (!feed || typeof feed !== "object" || (feed.rev === live.rev && (feed.character || "blink") === live.character)) return false;   // choosing another buddy changes what is worn with the same revision
  live = { character: feed.character || "blink", worn: feed.worn || {}, finish: feed.finish || "asis", ink: feed.ink || "char",
    colors: feed.colors || {}, styles: feed.styles || {}, rev: feed.rev };
  paint();
  return true;
}

/** What is worn on the character now (nothing for a character with no wardrobe yet). */
export function worn(c = character) { return c === live.character ? live.worn : {}; }

/** The notch's crop of the art, grown to take a crown when one is worn: {part:[x,y,w,h], H}. */
export function crop(c, part, H, surface) {
  return cropFor(c, worn(c), part, H, surface);
}

function paint() {
  if (!wrap || !back || !front) return;
  const mine = character === live.character;
  const items = placements(character, worn(), surface, mine ? { colors: live.colors, styles: live.styles, ink: live.ink } : {});
  const colour = (id) => (id === "char" ? CHAR_COLOUR[character] || COLORS.char : COLORS[id] || COLORS.char);
  const html = (layer) => items.filter((p) => p.layer === layer)
    .map((p) => `<g class="itm${p.pixel ? " px" : ""}" style="--wd-ink:${colour(p.color)}" transform="translate(${p.x} ${p.y}) scale(${p.k})"><use href="#${p.pixel ? "wdp" : "wd"}-${p.slot}-${p.id}"/></g>`).join("");
  back.innerHTML = html("back");
  front.innerHTML = html("front");
  wrap.style.setProperty("--wd-ink", colour(live.ink));
  wrap.style.setProperty("--wd-cc", CHAR_COLOUR[character] || "#fff");
  const fin = character === live.character ? live.finish : "asis";
  if (fin && fin !== "asis") wrap.dataset.wdFinish = fin; else delete wrap.dataset.wdFinish;
}
