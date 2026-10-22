// Settings > Buddy > Wardrobe: what the character may wear and what it wears now. Everything here is read from
// the backend (core/wardrobe.py): an item is offered as locked until progress the app really saw unlocks it.
import { api } from "../api.js";
import { esc } from "./ui.js";
import { castFor, CAST_INFO } from "../buddy/sprite.js";
import { COLORS } from "../buddy/wardrobe-fit.js";

const SLOT_NAMES = { crown: "Crown", eyes: "Eyes", neck: "Neck", back: "Back", hand: "Hand", aura: "Aura" };
const INK_NAMES = { char: "Its colour", white: "White", honey: "Honey", mint: "Mint", rose: "Rose", sky: "Sky", violet: "Violet", ember: "Ember" };
const dot = (attrs, id, on, label) => `<button class="btn small" type="button" ${attrs} aria-pressed="${on}" aria-label="${esc(label)}" title="${esc(label)}"
  style="width:26px;min-width:26px;padding:0;border-radius:50%;background:${COLORS[id]};${on ? "outline:2px solid var(--text, #fff);outline-offset:2px" : ""}"></button>`;

const pill = (attrs, label, on, locked, title, trying) =>
  `<button class="btn small" type="button" ${attrs} aria-pressed="${on}" ${locked ? 'aria-disabled="true"' : ""} style="${locked ? "opacity:.55;" : ""}${trying ? "outline:2px dashed var(--accent, #ffcc33);outline-offset:2px;opacity:1;" : ""}" title="${esc(title || "")}">${esc(label)}${trying ? " (trying)" : ""}</button>`;
// what a locked option says when you point at it: how to earn it, and that it can be tried on
const lockTitle = (need) => `Not earned yet: ${need}. Click to try it on for a minute.`;
const tryingOf = (w, kind, part, id) => {
  const p = w.preview || {};
  if (!p.active) return false;
  if (kind === "wear") return (p.worn || {})[part] === id;
  if (kind === "pins") return (p.pins || []).includes(id);
  return ((kind === "notch" ? p.notch : p.app) || {})[part] === id;
};
function previewBanner(w) {
  const p = w.preview;
  if (!p || !p.active) return `<div class="setting"><div><div class="t">Try before you earn</div><div class="s">Locked decorations can be tried on for a minute: they show on your buddy, the notch and this window, nothing is saved and nothing is unlocked. Click a dimmed one.</div></div></div>`;
  return `<div class="setting" style="border:1px dashed var(--accent, #ffcc33);border-radius:8px;padding:10px 12px"><div><div class="t">Trying on (not earned)</div>
    <div class="s">${p.items.map((i) => `${esc(i.what)}: ${esc(i.need)}`).join(" · ")}. Ends in <b id="try-left">${p.left}</b> s.</div></div><button class="btn small" type="button" data-stopprev="1">Stop trying on</button></div>`;
}

const NOTCH_NAMES = { plate: "Plate", frame: "Frame", shape: "Shape", ledge: "Ledge", lamp: "Status lamp", pins: "Pins" };
const NOTCH_HELP = { plate: "Behind the character when the notch is open.", frame: "The outline of the notch.", shape: "Floating, or hanging from the screen's edge.",
  ledge: "What the character stands on when the notch is open.", lamp: "The light that shows what is going on.", pins: "Small badges beside the word. Up to three." };

// The notch's own decoration: each part is unlocked by what you really did, like the wardrobe.
function notchHtml(w) {
  if (!w.notch_parts) return "";
  const rows = Object.keys(NOTCH_NAMES).map((part) => {
    const cur = w.notch[part], list = w.notch_parts[part] || [];
    const on = (id) => (part === "pins" ? (cur || []).includes(id) : cur === id);
    return `<div class="setting" style="align-items:flex-start"><div style="min-width:110px"><div class="t">${NOTCH_NAMES[part]}</div><div class="s">${NOTCH_HELP[part]}</div></div>
      <div class="row" style="flex-wrap:wrap;gap:6px">${list.map((i) => pill(`data-npart="${part}" data-nval="${i.id}"`, i.name, on(i.id), !i.unlocked, i.unlocked ? "" : lockTitle(i.need), tryingOf(w, part === "pins" ? "pins" : "notch", part, i.id))).join("")}</div></div>`;
  }).join("");
  return `<div class="setting"><div><div class="t">The notch</div><div class="s">How the floating notch looks. Everything unlocks from what you do; locked ones show how to earn them when you point at them.</div></div></div>${rows}`;
}

const APP_NAMES = { skin: "Skin", backdrop: "Backdrop", cards: "Cards", accent: "Accent", scene: "Header scene" };
const APP_HELP = { skin: "The surfaces and text of this window.", backdrop: "A quiet pattern behind everything.", cards: "How the panels look.",
  accent: "The one colour that glows.", scene: "What is behind the character at the top of Now." };

// The app window's own decoration: the same unlocks as the notch.
function appHtml(w) {
  if (!w.app_parts) return "";
  const rows = Object.keys(APP_NAMES).map((part) => {
    const cur = w.app[part], list = w.app_parts[part] || [];
    return `<div class="setting" style="align-items:flex-start"><div style="min-width:110px"><div class="t">${APP_NAMES[part]}</div><div class="s">${APP_HELP[part]}</div></div>
      <div class="row" style="flex-wrap:wrap;gap:6px">${list.map((i) => pill(`data-apart="${part}" data-aval="${i.id}"`, i.name, cur === i.id, !i.unlocked, i.unlocked ? "" : lockTitle(i.need), tryingOf(w, "app", part, i.id))).join("")}</div></div>`;
  }).join("");
  return `<div class="setting"><div><div class="t">This window</div><div class="s">How the app looks. It unlocks the same way: from what you do.</div></div></div>${rows}`;
}

// One part of the card at a time (Settings, Buddy has a page for each): "dress" (what the buddy wears, its look and ink), "notch" or "app".
function html(w, part) {
  if (part === "notch") return `${previewBanner(w)}${notchHtml(w)}`;
  if (part === "app") return `${previewBanner(w)}${appHtml(w)}`;
  const rows = Object.keys(SLOT_NAMES).map((slot) => {
    const worn = w.worn[slot] || "";
    const items = w.slots[slot].map((i) => pill(`data-slot="${slot}" data-item="${i.id}"`, i.name, worn === i.id, !i.unlocked, i.unlocked ? "" : lockTitle(i.need), tryingOf(w, "wear", slot, i.id))).join("");
    const cur = w.slots[slot].find((i) => i.id === worn);
    const own = (w.colors || {})[slot] || "";
    const colours = cur ? `<div class="row" style="flex-wrap:wrap;gap:6px;align-items:center"><span class="s" style="min-width:60px">Colour</span>
        ${pill(`data-slot="${slot}" data-color=""`, "Like the rest", !own, false)}
        ${Object.keys(COLORS).map((c) => dot(`data-slot="${slot}" data-color="${c}"`, c, own === c, INK_NAMES[c] || c)).join("")}
        ${cur.pixel && cur.line ? `<span class="s" style="margin-left:10px">Style</span>${pill(`data-slot="${slot}" data-style="line"`, "Line", (w.styles || {})[slot] !== "pixel", false)}${pill(`data-slot="${slot}" data-style="pixel"`, "Pixel", (w.styles || {})[slot] === "pixel", false)}` : ""}</div>` : "";
    return `<div class="setting" style="align-items:flex-start"><div style="min-width:70px"><div class="t">${SLOT_NAMES[slot]}</div></div>
      <div class="stack" style="gap:8px"><div class="row" style="flex-wrap:wrap;gap:6px">${pill(`data-slot="${slot}" data-item=""`, "None", !worn, false)}${items}</div>${colours}</div></div>`;
  }).join("");
  const finishes = w.finishes.map((f) => pill(`data-finish="${f.id}"`, f.name, w.finish === f.id, !f.unlocked, f.unlocked ? "" : f.need)).join("");
  const inks = w.inks.map((i) => pill(`data-ink="${i}"`, INK_NAMES[i] || i, w.ink === i, false)).join("");
  const p = w.progress;
  return `${previewBanner(w)}<div class="setting"><div><div class="t">Earned so far</div><div class="s">${esc((CAST_INFO[w.character] || [""])[0] || "Your buddy")} wears what you choose here. Level ${p.level}, ${p.tasks} tasks, ${p.sprints} focus sprints, ${p.habits} habits, best streak ${p.streak} days. Point at a dimmed item to see how to earn it, click it to try it on.</div></div></div>
    ${rows}
    <div class="setting" style="align-items:flex-start"><div style="min-width:70px"><div class="t">Look</div></div><div class="row" style="flex-wrap:wrap;gap:6px">${finishes}</div></div>
    <div class="setting" style="align-items:flex-start"><div style="min-width:70px"><div class="t">Ink</div><div class="s">The colour of every decoration that has no colour of its own.</div></div><div class="row" style="flex-wrap:wrap;gap:6px">${inks}</div></div>
    <div class="row"><button class="btn small" type="button" data-reset="1">Take everything off</button></div>`;
}

export async function mountWardrobe(el, ctx, part = "dress") {
  const name = (CAST_INFO[castFor(ctx.settings.character)] || [""])[0] || "this buddy";
  const load = async () => api.get("/ui/wardrobe").catch(() => null);
  let tick = 0;
  const paint = (w) => {
    el.innerHTML = w && w.ok ? html(w, part) : `<div class="setting"><div><div class="s">The wardrobe could not be read right now.</div></div></div>`;
    clearInterval(tick);
    if (w && w.ok && w.preview && w.preview.active) {          // count down, and show what is real again when it ends
      let left = w.preview.left;
      tick = setInterval(async () => {
        if (!el.isConnected) { clearInterval(tick); return; }
        left -= 1;
        const n = el.querySelector("#try-left"); if (n) n.textContent = Math.max(0, left);
        if (left <= 0) { clearInterval(tick); paint(await load()); }
      }, 1000);
    }
  };
  paint(await load());
  el.onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.stopprev) { const r = await api.post("/ui/wardrobe", { stop_preview: true }).catch(() => null); if (r && r.ok) paint(r); return; }
    if (b.getAttribute("aria-disabled") === "true") {
      // a locked decoration is tried on; a locked "look" has no preview, it just says what it takes
      const trying = b.dataset.npart ? { kind: "notch", part: b.dataset.npart, value: b.dataset.nval } : b.dataset.apart ? { kind: "app", part: b.dataset.apart, value: b.dataset.aval }
        : b.dataset.slot !== undefined && b.dataset.item ? { kind: "wear", part: b.dataset.slot, value: b.dataset.item } : null;
      if (!trying) { ctx.toast(b.title ? `Not yet: ${b.title}.` : "Not yet."); return; }
      const r = await api.post("/ui/wardrobe", { preview: trying }).catch((err) => ({ ok: false, error: String((err && err.message) || "failed") }));
      if (!r || r.ok === false) { ctx.toast((r && r.error) || "That did not work."); return; }
      ctx.toast("Trying it on for a minute. Nothing is saved or unlocked.");
      paint(r); return;
    }
    let body = null;
    if (b.dataset.npart) body = { notch: { [b.dataset.npart]: b.dataset.nval } };
    else if (b.dataset.apart) body = { app: { [b.dataset.apart]: b.dataset.aval } };
    else if (b.dataset.reset) body = { reset: true };
    else if (b.dataset.finish) body = { finish: b.dataset.finish };
    else if (b.dataset.ink) body = { ink: b.dataset.ink };
    else if (b.dataset.color !== undefined) body = { slot: b.dataset.slot, color: b.dataset.color };
    else if (b.dataset.style) body = { slot: b.dataset.slot, style: b.dataset.style };
    else if (b.dataset.slot !== undefined) body = { slot: b.dataset.slot, item: b.dataset.item || "" };
    if (!body) return;
    const r = await api.post("/ui/wardrobe", body).catch((err) => ({ ok: false, error: String((err && err.message) || "failed") }));
    if (!r || r.ok === false) { ctx.toast((r && r.error) || "That did not work."); return; }
    paint(r);
  };
}
