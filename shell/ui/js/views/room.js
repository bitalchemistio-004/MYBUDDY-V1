// Studio, Room: each character has a room of its own. Reward points (the XP you really earned, minus what you spent: core/room.py)
// upgrade its places, five levels each, and buy things for it. The room is drawn in pixel art (js/room/art.js) at the level you paid for,
// and the character stands in it and goes where your real day sends it: a focus sprint takes it to the desk, music to the rug, quiet hours
// to the armchair. Nothing here pays points.
import { api, reactionFor } from "../api.js";
import { mountLive, unmount } from "../buddy/avatar.js";
import { CAST_INFO } from "../buddy/sprite.js";
import { moodOf } from "../buddy/notch-state.js";
import { ST, PAL, PLACES, roomSVG, roomTier, roomSum, TIERNAME, thumbSVG, slotCenterAny, EXPOS } from "../room/art.js";
import { GAMES, TROPHY, openGame, openCalendar } from "../room/games.js";
import { esc } from "./ui.js";

const CHARS = ["blink", "void", "glitch", "cinder", "oracle"];
const BASE = { blink: 159, void: 187, glitch: 166, cinder: 177, oracle: 180 };      // where the art's feet are, in its 320 x 240 picture
const NAMES = {
  blink: { window: "Window", art: "Pinboard", shelf: "Shelf", lamp: "Desk lamp", seat: "Armchair", rug: "Rug", desk: "Desk", plant: "Plant", sig: "Notebook stack" },
  void: { window: "Porthole", art: "Star map", shelf: "Sample shelf", lamp: "Glow orb", seat: "Float pod", rug: "Gravity mat", desk: "Console", plant: "Moss globe", sig: "Telescope" },
  glitch: { window: "Static window", art: "Pixel poster", shelf: "Cable shelf", lamp: "Neon tube", seat: "Gaming chair", rug: "Grid mat", desk: "Rig", plant: "Pixel cactus", sig: "Arcade cabinet" },
  cinder: { window: "Ash window", art: "Forge sign", shelf: "Log rack", lamp: "Lantern", seat: "Log bench", rug: "Hearth rug", desk: "Workbench", plant: "Charcoal bonsai", sig: "Furnace" },
  oracle: { window: "Arch window", art: "Sky chart", shelf: "Book shelf", lamp: "Candelabra", seat: "Cushion", rug: "Eye rug", desk: "Reading desk", plant: "Moon flower", sig: "Crystal stand" },
};
const REAL = {
  window: "Shows the real time of day outside.", art: "Shows the goal you pinned.", shelf: "Holds the badges you really earned.", lamp: "Warms up with your focus minutes.",
  seat: "Rests when quiet hours begin.", rug: "One stitch for each day of your streak.", desk: "Shows your next task on its screen.", plant: "Grows with the tasks you finish.",
  sig: { blink: "Counts your notes.", void: "Scans the sky while you are away.", glitch: "Plays a jingle when a task is done.", cinder: "Burns brighter in a focus sprint.", oracle: "Shows your next calendar event." },
};
const LEVELS = ["Basic", "Detail and colour", "Glow", "Alive: it moves", "Legendary: gold trim"];
const ACT = {
  window: ["looks out of the window", "at dusk and in the daytime (your real clock)"], art: ["studies the pinboard", "when something needs your answer"],
  shelf: ["reads at the shelf", "when you have been quiet for a while"], lamp: ["switches the lamp on", "when the sun goes down; the room gets darker around it"],
  seat: ["sits down and sleeps", "when it is asleep or quiet hours begin"], rug: ["dances on the rug", "while music is playing"],
  desk: ["works at the desk", "during a focus sprint"], plant: ["waters the plant", "when you finish a task"],
  tv: ["plays on the console", "when it is idle"], calendar: ["checks the calendar", "when an event is near"], radio: ["turns the radio on and dances", "when you start music"],
  tank: ["feeds the fish", "after you finish a focus sprint"], clock: ["checks the clock", "on the hour"],
};
const SIG = { blink: ["writes in its notebooks", "when it is thinking"], void: ["scans the sky", "while it works for you"], glitch: ["plays the arcade", "while it works for you"],
  cinder: ["stokes the furnace", "while it works for you"], oracle: ["gazes into the crystal", "while it works for you"] };
const actOf = (c, slot) => (slot === "sig" ? SIG[c] : ACT[slot]);
const WHY = {
  clock: "Know the time at a glance when you look at the room, without opening anything.", lights: "A reward you can see: on a productive day the whole room lights up.",
  calendar: "Your week on the wall. Tap it to see what is coming up.", radio: "Play, pause and skip your music from the room.",
  tv: "Short games for a break between tasks. They never pay points, so they cannot be farmed.", tank: "A living tally of your focus: the more sprints you finish, the fuller it gets.",
  pet: "Company for your buddy. Purely for fun; it reacts to when you rest.",
};
const EGGS = {
  poster: ["The wanted poster", "The pinboard hides something. Keep poking it."], konami: ["The old code", "Some codes never die. Try the arrow keys."],
  plant: ["Plant talk", "Greenery likes attention."], star: ["Make a wish", "Watch the window."], disco: ["Party lamp", "The lamp has a party mode."],
  mouse: ["The mouse hole", "Something small lives at floor level."],
};
const PUNS = ["I am rooting for you.", "Leaf me alone, I am growing.", "You are doing soil good today.", "Photosynthesis is my side hustle."];
const KONAMI = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight", "b", "a"];
const RC = 0.64, RCX = 160 * RC, ROOMH = 500;                  // the character in the room is drawn at 64 % of its art size
const FLOOR = ["seat", "desk", "tv", "sig", "plant", "lamp"], WALL = ["window", "art", "shelf", "calendar", "clock", "radio", "tank"];
const standY = (slot) => (FLOOR.includes(slot) ? 372 : WALL.includes(slot) ? 422 : 462);
const snap6 = (v) => Math.round(v / 6) * 6;
const ago = (t) => { const m = Math.max(0, Math.round((Date.now() / 1000 - t) / 60)); return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };

let timers = [], off = null, mounted = null, konamiOff = null;

export default {
  async render(el, ctx) {
    this.destroy();
    const V = { c: CHARS.includes(ctx.settings.character) ? ctx.settings.character : "blink", R: null, sub: "places", sel: "plant", live: true, at: null, why: "", manualUntil: 0, prevX: 410,
      toast: "", say: "", walking: false, drag: null };
    mounted = V;
    const note = await api.get("/ui/room").catch(() => null);
    if (!note || !note.ok) { el.innerHTML = `<div class="empty">The room could not be read right now.</div>`; return; }
    V.R = note;
    el.innerHTML = `<div class="rm-top"><div class="rm-pills" id="rm-chars"></div><div class="rm-bal"><span>Reward points</span><b id="rm-pts"></b></div></div>
      <div class="rm-grid"><div class="rm-stage"><div class="roomwrap" id="rm-wrap"><div id="rm-svg"></div><i class="rm-shad" id="rm-shad"></i>
        <div class="roomchar" id="rm-char"><div id="rm-cv" style="position:absolute;inset:0"></div><div class="rm-bubble" id="rm-bubble" hidden></div></div>
        <div class="rm-layer" id="rm-layer" hidden></div><div class="rm-toast" id="rm-toast" hidden></div><div class="rm-caption" id="rm-cap"></div></div></div>
        <aside class="rm-side" id="rm-side"></aside></div>`;
    const $ = (id) => el.querySelector(`#${id}`);

    const toast = (text, ms = 3400) => { V.toast = text; $("rm-toast").hidden = !text; $("rm-toast").textContent = text; clearTimeout(V.toastT); if (text) V.toastT = setTimeout(() => toast(""), ms); };
    const say = (text, ms = 3000) => { $("rm-bubble").hidden = !text; $("rm-bubble").textContent = text; clearTimeout(V.sayT); if (text) V.sayT = setTimeout(() => say(""), ms); };

    const sync = () => {
      const R = V.R;
      ST.levels = R.levels; ST.owned = R.owned; ST.layout = R.layout; ST.sprints = R.sprints || 0; ST.at = V.at; ST.eggs = R.eggs || {};
      for (const k of ["justUp", "justBought"]) if (ST[k] === undefined) ST[k] = "";
    };
    const placeX = (slot) => (slot ? Math.max(100, Math.min(780, Math.round(slotCenterAny(V.c, slot)))) : 410);
    const goTo = (slot, why, manual) => {
      if (slot && !PLACES.includes(slot) && !(EXPOS[slot] && V.R.owned[V.c][slot])) return;
      V.at = slot; V.why = why || (slot ? (actOf(V.c, slot) || [, ""])[1] : "");
      if (manual) V.manualUntil = Date.now() + 20000;
      V.walking = true; setTimeout(() => { V.walking = false; paintChar(); }, 1100);
      sync(); paintRoom(); paintChar(); paintCap();
    };

    // Where your real day puts the character (only when "live" is on and you did not just send it somewhere yourself).
    const livePlace = () => {
      const s = ctx.status, now = new Date(), reaction = reactionFor(s), h = now.getHours();
      const mood = moodOf({ status: s, reaction, now: now.getTime() });
      if (reaction === "sleep" || ctx.settings.do_not_disturb) return ["seat", "it is asleep or quiet hours are on"];
      if (mood === "focus") return ["desk", "a focus sprint is running"];
      if (mood === "music") return [V.R.owned[V.c].radio ? "radio" : "rug", "music is playing"];
      if (mood === "think" || mood === "work") return ["sig", "it is working on something for you"];
      if (["alert", "due", "soon"].includes(mood)) return ["art", "something needs your answer"];
      if (h >= 19 || h < 6) return ["lamp", "the sun is down"];
      return ["window", "it is daytime"];
    };
    const follow = () => {
      if (!V.live || Date.now() < V.manualUntil || ST.arrange) return;
      const [slot, why] = livePlace();
      if (slot !== V.at) goTo(slot, why, false);
    };

    // ---- secrets: found by poking the room; they give no points and are kept so they stay found ----
    const clicks = {};
    const hits = (slot, n, within) => { const now = Date.now(); clicks[slot] = (clicks[slot] || []).filter((x) => now - x < within); clicks[slot].push(now); return clicks[slot].length >= n; };
    const findEgg = async (id) => {
      if (V.R.eggs[id]) return false;
      const r = await post({ egg: id });
      if (r && r.ok) { toast(`Secret found: ${EGGS[id][0]}  (${Object.keys(V.R.eggs).length} of ${Object.keys(EGGS).length})`); paintSide(); }
      return true;
    };
    const until = (key, ms) => { ST[key] = Date.now() + ms; paintRoom(); setTimeout(() => { if (mounted === V) paintRoom(); }, ms + 100); };
    const eggClick = async (slot) => {
      if (slot === "art" && hits("art", 5, 4000)) { if (await findEgg("poster")) say("Who put that there?"); paintRoom(); }
      if (slot === "plant") { say(PUNS[(clicks.plant || []).length % PUNS.length]); if (hits("plant", 3, 5000)) findEgg("plant"); }
      if (slot === "lamp" && hits("lamp", 7, 6000)) { until("discoUntil", 6000); findEgg("disco"); say("Party time!"); }
    };
    let kpos = 0;
    const onKey = (e) => {
      if (mounted !== V || !el.isConnected || !$("rm-layer").hidden) return;
      kpos = e.key === KONAMI[kpos] ? kpos + 1 : (e.key === KONAMI[0] ? 1 : 0);
      if (kpos === KONAMI.length) { kpos = 0; until("confettiUntil", 4500); V.walking = true; setTimeout(() => { V.walking = false; paintChar(); }, 1000); paintChar(); findEgg("konami"); say("Up up down down!"); }
    };
    document.addEventListener("keydown", onKey); konamiOff = () => document.removeEventListener("keydown", onKey);
    timers.push(setInterval(() => { if (mounted === V && el.isConnected && $("rm-layer").hidden && Math.random() < 0.7) until("starUntil", 3600); }, 26000));
    // a game or the calendar opens over the room; what a game keeps is its best score and a trophy, never points
    const host = () => ({
      layer: $("rm-layer"), character: V.c, name: CAST_INFO[V.c][0], toast, best: (id) => V.R.best[id],
      record: async (id, score, lower) => { await post({ game: { id, score, lower } }); paintSide(); },
      trophy: async (id) => { if (!V.R.trophies[id]) toast(`New trophy: ${TROPHY[id]}`); await post({ trophy: id }); paintSide(); },
      onClose: () => paintAll(),
    });

    // ---- drawing ----
    const paintChars = () => {
      $("rm-chars").innerHTML = CHARS.map((id) => `<button class="btn small" type="button" data-a="char" data-v="${id}" aria-pressed="${V.c === id}">${esc(CAST_INFO[id][0])}</button>`).join("");
      $("rm-pts").textContent = V.R.points;
    };
    const paintRoom = () => {
      sync();
      $("rm-svg").innerHTML = roomSVG(V.c, V.sel);
    };
    const paintChar = () => {
      const slot = V.at, x = placeX(slot), by = standY(slot), b = $("rm-char");
      const left = (x - RCX) / 880 * 100, top = (by - (FLOOR.includes(slot) ? 2 : 10) - BASE[V.c] * RC) / ROOMH * 100;
      b.style.cssText = `left:${left}%;top:${top}%;width:${320 * RC / 880 * 100}%;height:${240 * RC / ROOMH * 100}%`;
      b.classList.toggle("walking", V.walking);
      const sh = $("rm-shad"); sh.style.cssText = `left:${(x - 40) / 8.8}%;top:${(by - 4) / 5}%;width:${80 / 8.8}%`;
      mountLive($("rm-cv"), V.c, V.at === "seat" ? "sleep" : reactionFor(ctx.status), ctx.settings);
      V.prevX = x;
    };
    const paintCap = () => {
      const name = CAST_INFO[V.c][0];
      $("rm-cap").innerHTML = `<b>${esc(name)}</b> ${V.at ? esc(actOf(V.c, V.at)[0]) : "is resting"}${ST.preview ? ` <em>· preview: every place at level ${ST.preview}, nothing is saved</em>` : ""}${V.at ? `<span>because: ${esc(V.why)}</span>` : ""}`;
    };

    const placesHtml = () => {
      const c = V.c, lv = V.R.levels[c], sel = V.sel, names = NAMES[c], cur = lv[sel] || 1, cost = cur < 5 ? V.R.upgrade_cost[cur] : 0;
      const can = cur < 5 && V.R.points >= cost, real = sel === "sig" ? REAL.sig[c] : REAL[sel];
      const tier = roomTier(c), sum = roomSum(c), next = [16, 24, 32, 40][tier - 1];
      const isPlace = PLACES.includes(sel);
      const card = isPlace ? `<div class="rm-pips">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= cur ? "on" : ""}"></i>`).join("")}</div>
          <div>${esc(LEVELS[cur - 1])}</div><div class="rm-note">${esc(real)}</div>
          ${cur < 5 ? `<div class="rm-note">Next: ${esc(LEVELS[cur])}</div><button class="btn ${can ? "accent" : ""}" type="button" data-a="upgrade" ${can ? "" : 'aria-disabled="true" style="opacity:.5"'}>Upgrade for ${cost} points</button>` : `<div class="rm-note">Fully upgraded.</div>`}`
        : `<div class="rm-note">${esc((V.R.shop.find((x) => x.id === sel) || {}).real || "")}</div>`;
      const label = names[sel] || (V.R.shop.find((x) => x.id === sel) || {}).name || sel;
      const act = actOf(c, sel);
      return `<div class="rm-intro"><b>Tier ${tier}: ${esc(TIERNAME[tier - 1])}</b><span>${tier < 5 ? `${next - sum} more upgrade levels grow the room again. ` : ""}Every upgrade is paid with reward points you earned from real tasks, sprints and habits. Spending them never lowers your level.</span></div>
        <div class="rm-card"><b>${esc(label)}</b>${card}</div>
        <div class="rm-card"><b>Evolution</b><div class="rm-slide"><input type="range" min="0" max="5" step="1" value="${ST.preview}" data-a="preview" aria-label="Preview every place at a level"><span>${ST.preview ? "Level " + ST.preview : "Now"}</span></div>
          <div class="rm-note">Slide to see the whole room at level 1 to 5. At 0 you see it as it really is. Nothing is saved.</div></div>
        <div class="rm-card"><b>Arrange and use</b><div class="rm-sub"><button class="btn small" type="button" data-a="arrange" aria-pressed="${ST.arrange}">Move things around</button>
          <button class="btn small" type="button" data-a="relayout">Put back</button><button class="btn small" type="button" data-a="live" aria-pressed="${V.live}">Let ${esc(CAST_INFO[c][0])} live in it</button></div>
          ${act ? `<div class="rm-note"><b>${esc(CAST_INFO[c][0])} ${esc(act[0])}</b> ${esc(act[1])}.</div><button class="btn small" type="button" data-a="sendto">Send ${esc(CAST_INFO[c][0])} there</button>` : ""}</div>
        <div class="rm-list">${PLACES.map((sl) => `<button class="rm-row${sel === sl ? " on" : ""}" type="button" data-a="rsel" data-v="${sl}"><span>${esc(names[sl])}</span><span class="rm-pips sm">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= lv[sl] ? "on" : ""}"></i>`).join("")}</span></button>`).join("")}</div>
        <div class="rm-card"><b>Log</b>${V.R.log.length ? `<div class="rm-hist">${V.R.log.slice().reverse().slice(0, 5).map((h) => `<div><span>${ago(h.at)}</span>${h.bought ? "Bought " + esc(h.bought) : esc((NAMES[h.character] || {})[h.what] || h.what) + " to level " + h.level}<i>−${h.cost}</i></div>`).join("")}</div>` : `<div class="rm-note">Every upgrade and purchase is logged here with its time.</div>`}</div>`;
    };
    const shopHtml = () => {
      const c = V.c, R = V.R;
      return `<div class="rm-intro"><b>What the shop is for</b><span>Finishing real tasks, focus sprints and habits earns reward points. Spend them here on things that make ${esc(CAST_INFO[c][0])}'s room a place worth looking at. Each one either shows something real from your day (time, events, music, your focus) or is just for fun. Spending points never lowers your level.</span></div>
        <div class="rm-shop">${R.shop.map((s) => {
          const have = !!R.owned[c][s.id], lock = R.level < s.level, poor = R.points < s.cost;
          const action = have ? "<em>In your room</em>" : lock ? `<em class="lk">Reach level ${s.level}</em>` : `<button class="btn ${poor ? "" : "accent"}" type="button" data-a="buy" data-v="${s.id}" ${poor ? 'aria-disabled="true" style="opacity:.5"' : ""}>${poor ? `Need ${s.cost - R.points} more` : `Buy for ${s.cost}`}</button>`;
          return `<div class="rm-item${have ? " have" : ""}"><div class="th">${thumbSVG(s.id, c, lock && !have)}</div><div class="tx"><b>${esc(s.name)}</b><span class="eff">${esc(s.real)}</span><span class="why">${esc(WHY[s.id] || "")}</span>
            <span class="meta">${have ? "Yours" : `${s.cost} points${lock ? ` · unlocks at level ${s.level}` : ""}`}</span></div><div class="act">${action}</div></div>`;
        }).join("")}</div><div class="rm-note">Everything you buy belongs to ${esc(CAST_INFO[c][0])}'s room and can be moved. Points only come from real work.</div>`;
    };
    const playHtml = () => {
      const c = V.c, R = V.R, tv = !!R.owned[c].tv, cal = !!R.owned[c].calendar;
      return `<div class="rm-intro"><b>Games are for breaks</b><span>Put the console in your room, then play when you need ten minutes away. Scores and little trophies are kept; points are never paid, so there is nothing to grind.</span></div>
        <div class="rm-shop">${GAMES.map((g) => {
          const lock = !tv || R.level < g.level, why = !tv ? "Buy the console" : `Reach level ${g.level}`, best = R.best[g.id];
          return `<div class="rm-item${lock ? "" : " have"}"><div class="th">${thumbSVG("g-" + g.id, c, lock)}</div><div class="tx"><b>${esc(g.name)}</b><span class="eff">${esc(g.how)}</span>
            <span class="meta">${best !== undefined ? `Best: ${best}${g.id === "memory" ? " moves" : ""}${R.trophies[g.id] ? " · " + TROPHY[g.id] : ""}` : lock ? "Not played yet" : "No score yet"}</span></div>
            <div class="act">${lock ? `<em class="lk">${why}</em>` : `<button class="btn accent" type="button" data-a="play" data-v="${g.id}">Play</button>`}</div></div>`;
        }).join("")}</div>
        <div class="rm-item${cal ? " have" : ""}"><div class="th">${thumbSVG("calendar", c, !cal)}</div><div class="tx"><b>Month view</b><span class="eff">Your real tasks and reminders with a date, on the wall of ${esc(CAST_INFO[c][0])}'s room.</span></div>
          <div class="act">${cal ? '<button class="btn accent" type="button" data-a="opencal">Open</button>' : '<em class="lk">Buy the calendar</em>'}</div></div>`;
    };
    const secretsHtml = () => {
      const found = Object.keys(V.R.eggs).length;
      return `<div class="rm-intro"><b>Secrets</b><span>${found} of ${Object.keys(EGGS).length} found. Hints appear after your second find. Secrets give no points: they are just for fun.</span></div>
        <div class="rm-shop">${Object.entries(EGGS).map(([id, [name, hint]]) => `<div class="rm-item${V.R.eggs[id] ? " have" : ""}"><div class="tx"><b>${V.R.eggs[id] ? esc(name) : "???"}</b><span class="eff">${V.R.eggs[id] ? "Found." : found >= 2 ? esc(hint) : "Keep looking around the room."}</span></div>${V.R.eggs[id] ? '<div class="act"><em>Found</em></div>' : ""}</div>`).join("")}</div>`;
    };
    const paintSide = () => {
      const tabs = [["places", "Places"], ["shop", "Shop"], ["play", "Play"], ["secrets", `Secrets ${Object.keys(V.R.eggs).length}/${Object.keys(EGGS).length}`]].map(([id, l]) => `<button class="btn small" type="button" data-a="sub" data-v="${id}" aria-pressed="${V.sub === id}">${l}</button>`).join("");
      $("rm-side").innerHTML = `<div class="rm-sub">${tabs}</div>${V.sub === "shop" ? shopHtml() : V.sub === "play" ? playHtml() : V.sub === "secrets" ? secretsHtml() : placesHtml()}`;
    };
    const paintAll = () => { paintChars(); paintRoom(); paintChar(); paintCap(); paintSide(); };

    // ---- what you can do ----
    const post = async (body) => {
      const r = await api.post("/ui/room", body).catch((err) => ({ ok: false, error: String((err && err.message) || "failed") }));
      if (r && r.ok) V.R = r;
      return r;
    };
    el.addEventListener("click", async (e) => {
      const t = e.target.closest("[data-a]"); if (!t) return;
      const a = t.dataset.a, v = t.dataset.v;
      if (t.getAttribute("aria-disabled") === "true") { toast(a === "buy" ? "Not enough points yet. Finish tasks and sprints to earn more." : "Not enough points yet."); return; }
      if (a === "char") { V.c = v; V.sel = "plant"; V.at = null; ST.preview = 0; paintAll(); follow(); }
      else if (a === "sub") { V.sub = v; paintSide(); }
      else if (a === "play") { const g = GAMES.find((x) => x.id === v); if (g && V.R.owned[V.c].tv && V.R.level >= g.level) openGame(v, host()); }
      else if (a === "opencal") { const r = await api.get("/ui/tasks").catch(() => null); openCalendar(host(), r && r.ok ? r.tasks : []); }
      else if (a === "egg") { if (v === "mouse") { until("mouseUntil", 2600); findEgg("mouse"); say("Squeak!"); } else if (v === "star") { findEgg("star"); say("I wished for more focus time."); } }
      else if (a === "rsel" || a === "ritem" || a === "extra") { if (ST.arrange) return; V.sel = v; if (a !== "rsel" || V.sub !== "play") V.sub = "places"; paintRoom(); paintSide(); goTo(v, "", true); if (a === "ritem") eggClick(v); }
      else if (a === "sendto") goTo(V.sel, "", true);
      else if (a === "arrange") { ST.arrange = !ST.arrange; paintRoom(); paintSide(); }
      else if (a === "live") { V.live = !V.live; V.manualUntil = 0; paintSide(); follow(); }
      else if (a === "relayout") { await post({ put_back: V.c }); paintRoom(); paintChar(); toast("Everything is back where it started."); }
      else if (a === "upgrade") {
        const r = await post({ upgrade: { character: V.c, place: V.sel } });
        if (!r.ok) { toast(r.error || "That did not work."); return; }
        ST.justUp = V.sel; setTimeout(() => { ST.justUp = ""; paintRoom(); }, 1300);
        toast(`${NAMES[V.c][V.sel]} is now level ${r.levels[V.c][V.sel]}.`); paintAll();
      } else if (a === "buy") {
        const r = await post({ buy: { character: V.c, item: v } });
        if (!r.ok) { toast(r.error || "That did not work."); return; }
        ST.justBought = v; setTimeout(() => { ST.justBought = ""; paintRoom(); }, 1300);
        toast(`${(r.shop.find((x) => x.id === v) || {}).name} is in the room.`); paintAll();
        if (EXPOS[v]) goTo(v, "you just bought it", true); else if (v === "pet") say("A new friend!");
      }
    });
    el.addEventListener("input", (e) => {
      const t = e.target.closest("[data-a=preview]"); if (!t) return;
      ST.preview = +t.value; paintRoom(); paintCap(); paintSide();
    });
    // drag a place sideways (snapped to the 6-unit pixel grid) while "Move things around" is on
    el.addEventListener("pointerdown", (e) => {
      if (!ST.arrange) return;
      const g = e.target.closest("#rm-svg .rm"); if (!g || g.classList.contains("pet")) return;
      const svg = el.querySelector("#rm-svg svg");
      V.drag = { g, id: g.dataset.v, x0: e.clientX, base: +g.dataset.x, w: +g.dataset.w, orig: +g.dataset.x0, y: +g.dataset.y, k: 880 / svg.getBoundingClientRect().width };
      V.sel = V.drag.id; g.setPointerCapture && g.setPointerCapture(e.pointerId); e.preventDefault();
    });
    el.addEventListener("pointermove", (e) => {
      const d = V.drag; if (!d) return;
      d.nx = Math.max(0, Math.min(880 - d.w, snap6(d.base + (e.clientX - d.x0) * d.k)));
      d.g.setAttribute("transform", `translate(${d.nx} ${d.y})`);
    });
    el.addEventListener("pointerup", async () => {
      const d = V.drag; V.drag = null;
      if (!d || d.nx === undefined) return;
      const r = await post({ move: { character: V.c, thing: d.id, dx: d.nx - d.orig } });
      if (!r.ok) toast(r.error || "That did not move.");
      paintRoom(); paintChar(); paintSide();
    });

    paintAll();
    follow();
    off = ctx.on(() => { if (mounted !== V || !el.isConnected) return; follow(); mountLive($("rm-cv"), V.c, V.at === "seat" ? "sleep" : reactionFor(ctx.status), ctx.settings); });
    timers.push(setInterval(() => { if (mounted === V && el.isConnected) { paintCap(); } }, 30000));
    V.el = el;
  },
  destroy() {
    for (const t of timers) clearInterval(t);
    timers = [];
    if (konamiOff) { konamiOff(); konamiOff = null; }
    if (off) { off(); off = null; }
    if (mounted && mounted.el) { const cv = mounted.el.querySelector("#rm-cv"); if (cv) unmount(cv); clearTimeout(mounted.toastT); clearTimeout(mounted.sayT); }
    ST.arrange = false; ST.preview = 0; ST.at = null;
    mounted = null;
  },
};
