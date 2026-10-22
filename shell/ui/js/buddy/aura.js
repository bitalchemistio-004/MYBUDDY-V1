// The aura: light effects around the character that say, at a glance, what
// kind of moment this is (working, listening, music, angry, needs you, a
// burst when something is finished). It sits behind the drawn frames and
// never takes a click. With reduced motion, or switched off, there is none.
// Every effect stays inside the character's box plus the window's small pad,
// because the window is no larger than that.

// ---- pure part (tested in Node) ----

// Which effect a clip gets. Clips not named here have none.
export const AURA = {
  think: "work", search: "work", write: "work",
  listen: "wave",
  music: "music",
  furious: "mad", hot: "mad",
  alert: "alert", bell: "alert",
  focus: "focus",
  done: "burst", proud: "burst",
};
// Anger and heat glow red whatever the character's own colour is.
const HOT = new Set(["furious", "hot"]);
export const HOT_COLOUR = "#ff4a3a";

export function auraFor(clip) {
  return AURA[clip] || "";
}

export function auraColour(clip, accent) {
  return HOT.has(clip) ? HOT_COLOUR : accent;
}

// ---- browser part ----

const SPARKS = ["#ffcc33", "#34c759", "#ff6a9a", "#6ab0ff", "#ff9a3a"];
const NOTES = ["♪", "♫", "♪", "♬"];

export class Aura {
  constructor(el) {
    this.el = el;
    this.kind = "";
    this.on = true;
    this.box = { w: 160, h: 120 };
    this.pad = 14;
  }

  // The character's shown size and the pad around it: how far effects may reach.
  resize(box, pad) {
    this.box = box;
    this.pad = pad;
    this.el.style.setProperty("--aw", `${box.w}px`);
    this.el.style.setProperty("--ah", `${box.h}px`);
    // A note starts 40% down the box and may rise to the top of the pad.
    this.el.style.setProperty("--rise", `${Math.round(box.h * 0.4 + pad - 22)}px`);
  }

  // Off (the setting, or reduced motion) clears whatever is showing.
  enable(on) {
    this.on = !!on;
    if (!this.on) this.clear();
  }

  clear() {
    this.kind = "";
    this.el.className = "aura";
    this.el.replaceChildren();
  }

  show(clip) {
    if (!this.on) return;
    const kind = auraFor(clip);
    if (kind === this.kind && kind !== "burst") return;  // a burst plays again each time
    this.clear();
    this.kind = kind;
    if (!kind) return;
    const el = this.el;
    el.className = `aura a-${kind}`;
    el.style.setProperty("--aa", auraColour(clip, getComputedStyle(el).getPropertyValue("--bac").trim() || "#ffcc33"));
    const add = (cls, text) => {
      const d = document.createElement("div");
      d.className = cls;
      if (text) d.textContent = text;
      el.append(d);
      return d;
    };
    if (kind === "work") { add("ring"); add("ring r2"); add("dot"); add("dot"); add("dot"); }
    else if (kind === "focus") add("ring");
    else if (kind === "wave" || kind === "alert") { add("wave"); add("wave w2"); add("wave w3"); }
    else if (kind === "mad") add("glow");
    else if (kind === "music") {
      add("wave"); add("wave w2");
      const eq = add("eq");
      for (let i = 0; i < 9; i++) eq.append(document.createElement("i"));
      NOTES.forEach((t, i) => {
        const d = add("note", t);
        d.style.left = `${18 + i * 20}%`;
        d.style.setProperty("--dx", `${i % 2 ? 12 : -12}px`);
        d.style.animationDelay = `${i * 0.55}s`;
      });
    } else if (kind === "burst") {
      // Sparks fly out to the edge of the box plus the pad, no further.
      const rx = this.box.w / 2 + this.pad - 8, up = this.box.h * 0.45 + this.pad - 8, down = this.box.h * 0.55 + this.pad - 8;
      for (let i = 0; i < 26; i++) {
        const d = add("spark");
        const angle = Math.random() * 6.28, far = 0.45 + Math.random() * 0.55;
        const sin = Math.sin(angle);
        d.style.setProperty("--x", `${Math.cos(angle) * rx * far}px`);
        d.style.setProperty("--y", `${sin * (sin < 0 ? up : down) * far}px`);
        d.style.setProperty("--r", `${Math.random() * 720 - 360}deg`);
        d.style.setProperty("--c", SPARKS[i % SPARKS.length]);
        d.style.animationDelay = `${Math.random() * 0.15}s`;
      }
      // The sparks are gone after their flight; nothing is left running.
      setTimeout(() => { if (this.kind === "burst") this.clear(); }, 1500);
    }
  }
}
