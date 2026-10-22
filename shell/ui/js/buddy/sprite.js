// The thin browser side of the drawn buddy: loads the character's clips,
// draws the player's current frame on canvases, and runs one
// requestAnimationFrame loop only while the window is visible. All timing
// and state logic lives in clip-player.js and state.js, which are pure and
// tested in Node.
import { ClipPlayer, parseClips } from "./clip-player.js";
import { BuddyState } from "./state.js";

const BASE_URL = new URL("../../assets/buddy/chars/", import.meta.url);

// The drawn cast (docs/design/buddy_art/chars). Every character has the same
// clips, so the state machine never cares which one is on screen.
export const CAST = ["blink", "void", "glitch", "cinder", "oracle"];
export const CAST_INFO = {
  blink: ["Blink", "the original"], void: ["Void", "a pocket black hole"], glitch: ["Glitch", "a living screen error"],
  cinder: ["Cinder", "lava rock, flame crown"], oracle: ["Oracle", "the all-seeing eye"],
};

// The character setting, mapped onto the cast. A name from an older cast
// (a setting saved before this one) shows the default character.
export function castFor(character) {
  return CAST.includes(character) ? character : CAST[0];
}

export const stillUrl = (character) => new URL(`${castFor(character)}/still.webp`, BASE_URL).href;
export const clipsUrl = (character) => new URL(`${castFor(character)}/clips.json`, BASE_URL).href;
export const thumbsUrl = (character) => new URL(`${castFor(character)}/thumbs.webp`, BASE_URL).href;

export async function loadClips(character) {
  const name = castFor(character);
  const manifest = await fetch(new URL(`${name}/clips.json`, BASE_URL)).then((r) => {
    if (!r.ok) throw new Error(`${name}/clips.json: ${r.status}`);
    return r.json();
  });
  return { sheet: parseClips(manifest), name };
}

// One image per clip, loaded when the clip is first wanted. A whole
// character in one sheet decoded to ~160 MB; a clip is 5 to 15 MB, and only
// the last few stay in memory.
export class ClipImages {
  constructor(name, { keep = 4 } = {}) {
    this.name = name;
    this.keep = keep;
    this.loaded = new Map();   // clip -> image, oldest use first
    this.loading = new Map();  // clip -> promise
    this.needs = new Map();    // who is playing from these images -> the clips it needs now
  }

  get(clip) { return this.loaded.get(clip) || null; }

  load(clip) {
    if (this.loaded.has(clip)) return Promise.resolve(this.touch(clip));
    if (!this.loading.has(clip)) {
      const img = new Image();
      img.src = new URL(`${this.name}/${clip}.webp`, BASE_URL).href;
      // decode() resolves once the pixels are ready, so the first frame of a
      // clip never waits for a decode in the middle of a draw.
      const ready = img.decode().then(() => {
        this.loaded.set(clip, img);
        return img;
      }).finally(() => this.loading.delete(clip));
      this.loading.set(clip, ready);
    }
    return this.loading.get(clip);
  }

  touch(clip) {
    const img = this.loaded.get(clip);
    this.loaded.delete(clip);
    this.loaded.set(clip, img);
    return img;
  }

  // Drop the clips used longest ago, never one that any player still needs.
  trim(needed, owner = null) {
    this.needs.set(owner, needed);
    needed = [...this.needs.values()].flat();
    for (const clip of [...this.loaded.keys()]) {
      if (this.loaded.size <= this.keep) break;
      if (needed.includes(clip)) continue;
      this.loaded.get(clip).src = "";
      this.loaded.delete(clip);
    }
  }

  clear() {
    for (const img of this.loaded.values()) img.src = "";
    this.loaded.clear();
  }
}

// The art is drawn at 320x240 and shown at about half that, so it stays
// sharp on a 2x display; sizes land on whole CSS pixels.
export function fitSize(frame, scale) {
  return { w: Math.round(frame.w * scale), h: Math.round(frame.h * scale) };
}

export function reducedMotionWanted(settings) {
  if (settings && settings.reduced_motion) return true;
  return !!(globalThis.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
}

// Draws one frame per change onto every canvas given (the stage and, for
// any small copy of it).
export class SpriteRenderer {
  constructor(images, sheet) {
    this.images = images;
    this.sheet = sheet;
    this.canvases = [];
    this.drawn = -1;
  }

  attach(canvas) {
    canvas.width = this.sheet.width;
    canvas.height = this.sheet.height;
    this.canvases.push(canvas);
    this.drawn = -1;  // the new canvas needs a first draw
  }

  draw(player) {
    const index = player.frameIndex;
    if (index === this.drawn || index < 0) return;
    const f = this.sheet.frames[index];
    const image = this.images.get(f.clip);
    if (!image) return;  // not loaded: the frame on screen stays until it is
    this.drawn = index;
    for (const cv of this.canvases) {
      const ctx = cv.getContext("2d");
      ctx.imageSmoothingEnabled = true;  // smooth drawn art, not pixel art
      ctx.clearRect(0, 0, cv.width, cv.height);
      ctx.drawImage(image, f.x, f.y, f.w, f.h, 0, 0, cv.width, cv.height);
    }
  }
}

// One loop for the whole buddy. It stops when the document is hidden and
// resumes without fast-forwarding through the time nobody saw.
export function runLoop(step, onResume = null) {
  let id = 0;
  const frame = () => { step(); id = requestAnimationFrame(frame); };
  const start = () => { if (!id && document.visibilityState === "visible") id = requestAnimationFrame(frame); };
  const stop = () => { cancelAnimationFrame(id); id = 0; };
  const seen = () => {
    if (document.visibilityState !== "visible") return stop();
    if (onResume) onResume();
    start();
  };
  document.addEventListener("visibilitychange", seen);
  start();
  // dispose: for a buddy that is replaced (another character), so its loop
  // cannot come back on the next visibility change.
  return { start, stop, dispose: () => { stop(); document.removeEventListener("visibilitychange", seen); }, get running() { return id !== 0; } };
}

// The images of a character, shared by every player of it in this window.
const shared = new Map();  // character -> { images, users }
function borrowImages(name) {
  let entry = shared.get(name);
  if (!entry) shared.set(name, entry = { images: new ClipImages(name), users: 0 });
  entry.users += 1;
  return entry.images;
}
function returnImages(name, owner) {
  const entry = shared.get(name);
  if (!entry) return;
  entry.images.needs.delete(owner);
  entry.users -= 1;
  if (entry.users <= 0) { entry.images.clear(); shared.delete(name); }
}

// Player, state machine, renderer and loop wired together. Returns the
// handle the window uses: fire(event), setReducedMotion(), attach(canvas).
export async function createSpriteBuddy({ character = CAST[0], reducedMotion = false, onClip = null } = {}) {
  const { sheet, name } = await loadClips(character);
  const images = borrowImages(name), owner = {};
  try { await images.load("idle"); }
  catch (e) { returnImages(name, owner); throw e; }
  const renderer = new SpriteRenderer(images, sheet);
  let state = null, shown = "idle";
  // Tell the window when another clip comes on screen (it pops the character
  // on the cut), whether an event started it or a one-shot ended.
  const note = () => {
    if (player.name === shown) return;
    shown = player.name;
    if (onClip) onClip(shown);
  };
  const player = new ClipPlayer(sheet, {
    now: () => performance.now(),
    reducedMotion,
    maxStep: 250,
    onEnd: (ended) => state && state.clipEnded(ended),
  });
  // The state machine names the clip; it starts once its image is here. The
  // clip on screen keeps playing until then (tens of milliseconds).
  const show = (clip, s) => {
    const base = player.has(s.base) ? s.base : "idle";
    if (clip !== s.base && !player.has(clip)) return s.clipEnded(clip);  // no art for it: skip it
    const wanted = clip === s.base ? [base] : [base, clip];
    Promise.all(wanted.map((c) => images.load(c))).then(() => {
      if (s.clip !== clip) return;  // something newer took over while it loaded
      player.play(base);  // the loop to return to
      if (clip !== s.base) player.play(clip);
      images.trim(["idle", base, player.name], owner);
      renderer.draw(player);
      note();
    }).catch((e) => {
      console.error(`clip ${clip} did not load`, e);
      if (clip !== s.base) s.clipEnded(clip);
    });
  };
  state = new BuddyState({ now: () => performance.now(), onChange: show });
  state.setClips([...sheet.tags.keys()], sheet.loops);
  const loop = runLoop(() => {
    state.tick();
    if (player.update()) { renderer.draw(player); note(); }
  }, () => player.resync());
  return {
    player, state, loop, sheet, character: name,
    clips: [...sheet.tags.keys()],
    fire: (event) => state.fire(event),
    attach: (canvas) => { renderer.attach(canvas); renderer.draw(player); },
    setReducedMotion: (on) => { player.setReducedMotion(on); renderer.drawn = -1; renderer.draw(player); },
    destroy: () => { loop.dispose(); returnImages(name, owner); },
  };
}
