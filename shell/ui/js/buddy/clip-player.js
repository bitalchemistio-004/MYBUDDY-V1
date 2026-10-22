// Plays tagged clips: from a character's clips.json (one sheet per clip,
// tools/build_buddy_assets.py) or from an Aseprite sprite-sheet export
// ("json-array" or "json-hash"). Pure: no DOM and no timers. The time source is injected so
// tests can drive it with a fake clock; the renderer calls update() once per
// animation frame and redraws only when frameIndex changes.

// Clips that repeat until something else happens. Every other tag is a
// one-shot: it plays once, then the player goes back to the current loop.
// clips.json says which clips loop; this list is only for a sheet that does
// not say (an Aseprite export).
export const LOOPS = new Set(["idle", "listen", "think", "sleep"]);

// clips.json: {"w", "h", "clips": {name: {"cols", "loop", "durations"}}}.
// Every clip has its own image, so each frame names its clip and where it
// sits in that clip's sheet; the frames of all clips are numbered in one row
// so the player below does not care how the art is stored.
export function parseClips(manifest) {
  const w = manifest.w, h = manifest.h;
  const frames = [], tags = new Map(), loops = new Set();
  for (const [name, c] of Object.entries(manifest.clips || {})) {
    const from = frames.length, cols = Math.max(1, c.cols || 1);
    (c.durations || []).forEach((d, n) => frames.push({
      clip: name, x: (n % cols) * w, y: Math.floor(n / cols) * h, w, h,
      duration: Math.max(1, Number(d) || 100),
    }));
    if (frames.length === from) continue;  // a clip with no frames cannot be played
    tags.set(name, { from, to: frames.length - 1, direction: "forward" });
    if (c.loop) loops.add(name);
  }
  return { frames, tags, loops, width: w, height: h };
}

// Frame rects and hold times plus tag ranges, in a shape that is cheap to
// read on every tick.
export function parseSheet(json) {
  const raw = Array.isArray(json.frames) ? json.frames : Object.values(json.frames || {});
  const frames = raw.map((f) => ({
    x: f.frame.x, y: f.frame.y, w: f.frame.w, h: f.frame.h,
    duration: Math.max(1, Number(f.duration) || 100),  // a zero hold would spin forever
  }));
  const tags = new Map();
  for (const t of (json.meta && json.meta.frameTags) || []) {
    tags.set(t.name, { from: t.from, to: t.to, direction: t.direction || "forward" });
  }
  const first = frames[0] || { w: 0, h: 0 };
  return { frames, tags, width: first.w, height: first.h };
}

// The frame order a tag plays in. A pingpong loop does not repeat its end
// frames (Aseprite behaviour); a pingpong played once ends where it began.
export function sequence(tag, once = false) {
  const fwd = [];
  for (let i = tag.from; i <= tag.to; i++) fwd.push(i);
  const back = fwd.slice().reverse();
  const tail = (list) => (once ? list.slice(1) : list.slice(1, -1));
  switch (tag.direction) {
    case "reverse": return back;
    case "pingpong": return fwd.concat(tail(back));
    case "pingpong_reverse": return back.concat(tail(fwd));
    default: return fwd;
  }
}

export class ClipPlayer {
  constructor(sheet, { now = () => Date.now(), loops = null, reducedMotion = false, onEnd = null, maxStep = Infinity } = {}) {
    this.sheet = sheet.tags instanceof Map ? sheet : parseSheet(sheet);
    this.now = now;
    this.loops = loops || this.sheet.loops || LOOPS;
    this.reduced = !!reducedMotion;
    this.onEnd = onEnd;
    this.maxStep = maxStep;  // the renderer caps long gaps so a stall does not skip a clip
    this.base = this.has("idle") ? "idle" : null;
    this.clip = null;
    this.last = now();
    if (this.base) this.start(this.base);
  }

  has(name) { return this.sheet.tags.has(name); }
  isLoop(name) { return this.loops.has(name); }
  get name() { return this.clip ? this.clip.name : null; }
  get playingOnce() { return !!(this.clip && this.clip.once); }

  // Reduced motion holds the first frame of every clip.
  get frameIndex() {
    if (!this.clip) return -1;
    return this.clip.seq[this.reduced ? 0 : this.clip.pos];
  }

  get frame() { return this.sheet.frames[this.frameIndex] || null; }

  // A loop becomes the base; it starts now unless a one-shot is still
  // playing (that one finishes first and then returns to it). Asking for the
  // loop that is already on screen does not restart it, so repeated status
  // updates do not make it stutter. Unknown names are a soft no-op.
  play(name) {
    if (!this.has(name)) return false;
    if (this.isLoop(name)) {
      this.base = name;
      if (this.playingOnce || this.name === name) return true;
    }
    this.start(name);
    return true;
  }

  start(name) {
    const once = !this.isLoop(name);
    const seq = sequence(this.sheet.tags.get(name), once);
    const total = seq.reduce((sum, i) => sum + this.sheet.frames[i].duration, 0);
    this.clip = { name, seq, once, total, pos: 0, elapsed: 0, played: 0 };
    this.last = this.now();
  }

  setReducedMotion(on) { this.reduced = !!on; }

  // After a pause (hidden window) continue where it stopped instead of
  // fast-forwarding through the time nobody saw.
  resync() { this.last = this.now(); }

  // Advance to the clock's current time. Returns true when the frame to show
  // changed, so the caller knows whether to redraw.
  update() {
    const t = this.now();
    const dt = Math.min(this.maxStep, Math.max(0, t - this.last));
    this.last = t;
    return this.advance(dt);
  }

  advance(dt) {
    const c = this.clip;
    if (!c || c.done) return false;
    const before = this.frameIndex;
    if (this.reduced) {
      // One-shots still take their natural length, so the buddy returns to
      // its loop at the same moment it would with motion on.
      c.played += dt;
      if (c.once && c.played >= c.total) this.finish();
      return this.frameIndex !== before;
    }
    c.elapsed += dt;
    if (!c.once && c.elapsed >= c.total) c.elapsed %= c.total;  // a long gap on a loop
    while (c.elapsed >= this.hold(c)) {
      c.elapsed -= this.hold(c);
      c.pos += 1;
      if (c.pos < c.seq.length) continue;
      if (c.once) { this.finish(); break; }
      c.pos = 0;
    }
    return this.frameIndex !== before;
  }

  hold(c) { return this.sheet.frames[c.seq[c.pos]].duration; }

  finish() {
    const ended = this.clip.name;
    if (this.base) this.start(this.base);
    else Object.assign(this.clip, { pos: this.clip.seq.length - 1, done: true });  // no loop to return to: rest on the last frame
    if (this.onEnd) this.onEnd(ended);
  }
}
