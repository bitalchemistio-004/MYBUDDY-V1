// Buddy Quest: a dungeon your buddy fights through, a floor of five rooms at a time. It has its own full page (#/play/quest).
// Your real level makes the buddy stronger; between rooms you choose a perk that stays for the whole run; each floor is harder than the
// last, so a run is a climb. Every fight is turn by turn: the monster tells you what it is about to do, and you choose Strike, Guard or Zap.
import { stillUrl } from "../buddy/sprite.js";
import { monsterArt } from "./pixart.js";
import { coach } from "./coach.js";
import { esc } from "./util.js";

// ---- the rules (pure, tested) ----
/** The buddy's numbers from the real level and the perks picked this run. */
export const hero = (level, perks = {}) => ({
  hp: 50 + level * 6 + (perks.hp || 0),
  atk: 8 + level * 2 + (perks.atk || 0),
  zap: 20 + level * 3 + (perks.zap || 0),
  guardHeal: 5 + (perks.guard || 0),
});

export const MONSTERS = [
  { name: "Slime", hp: 26, hit: [4, 8], blurb: "Soft and slow." },
  { name: "Bat", hp: 34, hit: [6, 10], blurb: "Quick, and likes to wind up." },
  { name: "Ghost", hp: 46, hit: [7, 12], blurb: "Hits harder than it looks." },
  { name: "Golem", hp: 62, hit: [9, 15], blurb: "Slow and heavy." },
  { name: "Dragon", hp: 90, hit: [11, 18], blurb: "The boss of the floor." },
];
/** How much stronger the monsters are on [floor] (1 is the first). */
export const floorScale = (floor) => ({ hp: 1 + 0.35 * (floor - 1), dmg: 1 + 0.2 * (floor - 1) });
export const monsterHp = (room, floor) => Math.round(MONSTERS[room].hp * floorScale(floor).hp);

/** Damage in [lo, hi] from a 0..1 roll. */
export const roll = (lo, hi, r) => lo + Math.floor(r * (hi - lo + 1));

export const PERKS = [
  { id: "tough", name: "Tough", text: "+12 most health, and heal 12.", apply: (s) => { s.perks.hp += 12; s.max += 12; s.hp = Math.min(s.max, s.hp + 12); } },
  { id: "sharp", name: "Sharp", text: "Strike hits 3 harder.", apply: (s) => { s.perks.atk += 3; } },
  { id: "charged", name: "Charged", text: "One more Zap, and Zap hits 4 harder.", apply: (s) => { s.perks.zap += 4; s.charges += 1; } },
  { id: "mend", name: "Mend", text: "Heal 25 right now.", apply: (s) => { s.hp = Math.min(s.max, s.hp + 25); } },
  { id: "brace", name: "Brace", text: "Guard heals 4 more.", apply: (s) => { s.perks.guard += 4; } },
];
/** Three different perks to pick from. */
export function perkChoices(rand = Math.random) {
  const pool = [...PERKS]; const out = [];
  while (out.length < 3 && pool.length) out.push(pool.splice(Math.floor(rand() * pool.length), 1)[0]);
  return out;
}

/** What the monster is about to do, in words (shown before you choose). */
export function intent(s) {
  const m = MONSTERS[s.room], f = floorScale(s.floor).dmg;
  const lo = Math.round(m.hit[0] * f), hi = Math.round(m.hit[1] * f);
  if (s.windup) return { warn: true, head: `${m.name} is charging a heavy blow`, text: `Its next hit is 60% stronger: ${Math.round(lo * 1.6)} to ${Math.round(hi * 1.6)}. Guard now to take half of it.` };
  return { warn: false, head: `${m.name} will attack`, text: `For ${lo} to ${hi} damage this turn. Sometimes it winds up first.` };
}

/** One round of the fight. [move] is "hit", "guard" or "zap". Pure: returns the new numbers and what happened. */
export function round(state, move, rand = Math.random) {
  const s = { ...state, log: [], perks: { hp: 0, atk: 0, zap: 0, guard: 0, ...(state.perks || {}) } };
  const h = hero(s.level, s.perks), f = floorScale(s.floor || 1).dmg;
  let dealt = 0, taken = 0;
  if (move === "zap") { s.charges -= 1; dealt = roll(h.zap - 4, h.zap + 4, rand()); }
  else if (move === "hit") dealt = roll(h.atk - 3, h.atk + 3, rand());
  if (move === "guard") { s.hp = Math.min(s.max, s.hp + h.guardHeal); s.log.push(`You guard and heal ${h.guardHeal}.`); }
  if (dealt) { s.mhp = Math.max(0, s.mhp - dealt); s.log.push(`You ${move === "zap" ? "zap" : "strike"} for ${dealt}.`); }
  s.wasWind = !!state.windup;
  if (s.mhp > 0) {
    const m = MONSTERS[s.room];
    taken = roll(Math.round(m.hit[0] * f), Math.round(m.hit[1] * f), rand());
    if (state.windup) taken = Math.round(taken * 1.6);
    if (move === "guard") taken = Math.ceil(taken / 2);
    s.hp = Math.max(0, s.hp - taken);
    s.log.push(`${m.name} hits you for ${taken}${state.windup ? " (a charged blow)" : ""}${move === "guard" ? ", halved by your guard" : ""}.`);
    s.windup = rand() < 0.3;
  } else s.windup = false;
  s.dealt = dealt; s.taken = taken;
  return s;
}

/** A new run. [bought] is what was bought in the shop ({ skin, stone, spare }): each level is a small, permanent head start. */
export const newRun = (level, bought = {}) => {
  const perks = { hp: 10 * (bought.skin || 0), atk: 2 * (bought.stone || 0), zap: 0, guard: 0 };
  const h = hero(level, perks);
  return { level, floor: 1, room: 0, hp: h.hp, max: h.hp, mhp: monsterHp(0, 1), mmax: monsterHp(0, 1), charges: 3 + (bought.spare || 0), windup: false, perks, log: [], cleared: 0 };
};

// ---- the page ----
const dots = (n, max, cls = "") => `<span class="pq-dots ${cls}" role="img" aria-label="${n} of ${max}">${Array.from({ length: 12 }, (_, i) => `<i class="${i < Math.ceil((12 * n) / max) ? "on" : ""}"></i>`).join("")}</span>`;

export default {
  render(el, ctx) {
    const { store } = ctx;
    const character = store.settings.get().character;
    const level = store.rewards.state().level || 1;
    let s = newRun(level, store.quest.state().bought), busy = false, said = "";
    const rec = () => store.games.get("quest");
    const speak = (t) => { try { if (store.settings.get().speak_replies && window.Native && window.Native.speak) window.Native.speak(t); } catch { /* silent */ } };
    const head = (sub) => `<header class="pa-subhead"><a class="pa-back" href="#/play" aria-label="Back to Play"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg></a><div><h1>Buddy Quest</h1><p>${sub}</p></div></header>`;
    const rooms = () => `<div class="pq-rooms" role="img" aria-label="Room ${s.room + 1} of 5">${MONSTERS.map((m, i) => `<i class="${i < s.room ? "done" : i === s.room ? "now" : ""}"></i>`).join("")}</div>`;
    const perksChips = () => { const p = s.perks, out = []; if (p.hp) out.push(`+${p.hp} health`); if (p.atk) out.push(`+${p.atk} strike`); if (p.zap) out.push(`+${p.zap} zap`); if (p.guard) out.push(`+${p.guard} guard`); return out.length ? `<div class="pq-perks">${out.map((x) => `<span>${x}</span>`).join("")}</div>` : ""; };

    const tutorial = () => {
      el.innerHTML = `${head("How it works")}
        <ol class="pq-how">
          <li><b>Your level is your strength.</b><span>You are level ${level}. Finishing real tasks raises it, so the buddy hits harder and has more health.</span></li>
          <li><b>Read what it will do.</b><span>Before each turn the monster tells you its plan. When it says it is charging, its next hit is much stronger.</span></li>
          <li><b>Pick your move.</b><span>Strike is a sure hit. Guard halves the damage you take and heals a little. Zap hits much harder but you only have a few.</span></li>
          <li><b>Win a room, pick a perk.</b><span>Five rooms make a floor. Each floor is harder. Your perks stay until the run ends.</span></li>
        </ol>
        <button class="pa-btn go pq-wide" id="q-go" type="button">Start the run</button>`;
      el.querySelector("#q-go").addEventListener("click", () => { store.games.mark("quest", { seen: true }); fight(); });
    };

    // before a run: your best floor and stars, and the shop
    const start = () => {
      const q = store.quest.state(), best = rec().best || 0;
      el.innerHTML = `${head("A dungeon of five rooms a floor")}
        <div class="gs-coach"><img alt="" src="${stillUrl(character)}"><p>${esc(coach(character, "start"))}</p></div>
        <ul class="pq-sum"><li><b>${best || "-"}</b><span>best floor</span></li><li><b>${q.stars}</b><span>stars</span></li><li><b>${level}</b><span>your level</span></li></ul>
        <p class="pa-note">Stars come from clearing floors and from the daily game. They buy permanent head starts in the shop.</p>
        <div class="gs-btns"><button class="pa-btn go" id="q-run" type="button">Start a run</button><button class="pa-btn" id="q-shop" type="button">Shop</button><button class="pa-btn" id="q-how" type="button">How it works</button></div>`;
      el.querySelector("#q-run").addEventListener("click", () => { s = newRun(store.rewards.state().level || 1, store.quest.state().bought); said = coach(character, "start"); fight(); });
      el.querySelector("#q-shop").addEventListener("click", shop);
      el.querySelector("#q-how").addEventListener("click", tutorial);
    };

    const shop = () => {
      const q = store.quest.state();
      el.innerHTML = `${head(`${q.stars} stars to spend`)}
        <div class="pq-perkpick">${store.quest.SHOP.map((it) => { const have = q.bought[it.id] || 0, full = have >= it.max;
          return `<button class="pq-perk" data-buy="${it.id}" type="button" ${full || q.stars < it.cost ? "disabled" : ""}><b>${esc(it.name)} <em>${have} of ${it.max}</em></b><span>${esc(it.text)}</span><span class="pq-cost">${full ? "Owned" : `${it.cost} stars`}</span></button>`; }).join("")}</div>
        <div class="gs-btns"><button class="pa-btn go" id="q-back" type="button">Back</button></div>`;
      el.querySelectorAll("[data-buy]").forEach((b) => b.addEventListener("click", () => { const err = store.quest.buy(b.dataset.buy); if (err) ctx.toast(err); else { ctx.toast("Bought."); shop(); } }));
      el.querySelector("#q-back").addEventListener("click", start);
    };

    const fight = (msg) => {
      const m = MONSTERS[s.room], h = hero(s.level, s.perks), it = intent(s);
      el.innerHTML = `${head(`Floor ${s.floor} · Room ${s.room + 1} of 5 · best floor ${Math.max(1, rec().best || 1)}`)}${rooms()}${perksChips()}
        <div class="pq-arena ${s.windup ? "wind" : ""}">
          <div class="pq-fighter pq-you" id="pq-h"><img alt="" src="${stillUrl(character)}"><span class="pq-pop" id="pq-ph"></span></div>
          <div class="pq-fighter pq-foe ${s.windup ? "wind" : ""}" id="pq-m">${monsterArt(m.name, "pq-mon")}<span class="pq-pop" id="pq-pm"></span></div>
          <div class="pq-floor" aria-hidden="true"></div>
        </div>
        <div class="pq-bars">
          <div><div class="pq-barhead"><b>You</b><span>${s.hp} / ${s.max} health</span></div>${dots(s.hp, s.max)}</div>
          <div><div class="pq-barhead"><b>${m.name}</b><span>${s.mhp} / ${s.mmax}</span></div>${dots(s.mhp, s.mmax, "foe")}</div>
        </div>
        <div class="gs-coach"><img alt="" src="${stillUrl(character)}"><p id="q-line">${esc(msg || said || coach(character, "start"))}</p></div>
        <div class="pq-intent ${it.warn ? "warn" : ""}"><b>${esc(it.head)}</b><span>${esc(it.text)}</span></div>
        <div class="pq-acts">
          <button class="pq-act" id="q-hit" type="button"><b>Strike</b><span>A sure hit for ${h.atk - 3} to ${h.atk + 3}.</span></button>
          <button class="pq-act" id="q-guard" type="button"><b>Guard</b><span>Take half damage and heal ${h.guardHeal}.</span></button>
          <button class="pq-act" id="q-zap" type="button" ${s.charges ? "" : "disabled"}><b>Zap · ${s.charges} left</b><span>A big hit for ${h.zap - 4} to ${h.zap + 4}.</span></button>
        </div>
        ${s.log.length ? `<ul class="pq-log">${s.log.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : ""}`;
      el.querySelector("#q-hit").addEventListener("click", () => act("hit"));
      el.querySelector("#q-guard").addEventListener("click", () => act("guard"));
      el.querySelector("#q-zap").addEventListener("click", () => act("zap"));
    };

    const act = (move) => {
      if (busy || !el.isConnected) return; busy = true;
      const before = s, next = round(s, move);
      const you = el.querySelector("#pq-h"), foe = el.querySelector("#pq-m");
      if (move !== "guard" && you) you.classList.add(move === "zap" ? "zap" : "lunge");
      setTimeout(() => { if (next.dealt && foe) { foe.classList.add("hit"); const p = el.querySelector("#pq-pm"); if (p) { p.textContent = `-${next.dealt}`; p.classList.add("up"); } } }, 160);
      setTimeout(() => { if (next.taken && you) { you.classList.add("hurt"); const p = el.querySelector("#pq-ph"); if (p) { p.textContent = `-${next.taken}`; p.classList.add("up"); } } }, 420);
      setTimeout(() => {
        busy = false; if (!el.isConnected) return;
        s = next;
        // the buddy comments on what really happened this turn
        if (s.hp <= 0) return defeat();
        if (before.windup && move !== "guard" && s.mhp > 0) said = coach(character, "bad", "It was charging. Guard next time.");
        else if (before.windup && move === "guard") said = coach(character, "good", "Perfect guard.");
        else if (s.hp / s.max < 0.3) said = coach(character, "near", "You're low. Guard or zap.");
        else if (s.mhp > 0 && s.mhp / s.mmax < 0.25) said = coach(character, "near", "It's nearly down.");
        else if (next.dealt) said = coach(character, "good");
        if (s.mhp <= 0) return roomCleared();
        fight(said);
      }, 900);
    };

    const roomCleared = () => {
      s.cleared += 1;
      store.rewards.add(8 + 4 * s.floor, `Room ${s.room + 1} cleared`);
      if (s.room >= MONSTERS.length - 1) return floorCleared();
      const opts = perkChoices();
      el.innerHTML = `${head(`Floor ${s.floor} · Room ${s.room + 1} cleared`)}${rooms()}
        <div class="gs-coach"><img alt="" src="${stillUrl(character)}"><p>${esc(coach(character, "win", "Pick a perk."))}</p></div>
        <h3 class="pa-day">Choose a perk</h3>
        <div class="pq-perkpick">${opts.map((p, i) => `<button class="pq-perk" data-i="${i}" type="button"><b>${esc(p.name)}</b><span>${esc(p.text)}</span></button>`).join("")}</div>
        <p class="pa-note">You rest a little too: +12 health and one more Zap charge (up to 3).</p>`;
      el.querySelectorAll("[data-i]").forEach((b) => b.addEventListener("click", () => {
        opts[Number(b.dataset.i)].apply(s);
        s.room += 1; s.mmax = monsterHp(s.room, s.floor); s.mhp = s.mmax; s.windup = false; s.hp = Math.min(s.max, s.hp + 12); s.charges = s.charges < 3 ? s.charges + 1 : s.charges;
        s.log = []; said = coach(character, "start");
        fight();
      }));
    };

    const floorCleared = () => {
      store.games.record("quest", { won: true, score: s.floor });
      store.quest.addStars(s.floor);
      store.rewards.gameWon(`Buddy Quest, floor ${s.floor}`, Math.min(120, 40 + 15 * s.floor));
      const line = coach(character, "win"); speak(line);
      el.innerHTML = `${head(`Floor ${s.floor} cleared`)}
        <div class="pq-arena won"><div class="pq-fighter pq-you"><img alt="" src="${stillUrl(character)}"></div><div class="pq-floor"></div></div>
        <div class="gs-coach"><img alt="" src="${stillUrl(character)}"><p>${esc(line)}</p></div>
        <p class="pa-note">+${s.floor} quest star${s.floor === 1 ? "" : "s"}. Floor ${s.floor} done. The monsters on floor ${s.floor + 1} have ${Math.round((floorScale(s.floor + 1).hp - 1) * 100)}% more health and hit ${Math.round((floorScale(s.floor + 1).dmg - 1) * 100)}% harder. You keep your perks and rest up to half health.</p>
        <div class="gs-btns"><button class="pa-btn go" id="q-next" type="button">Go down to floor ${s.floor + 1}</button><a class="pa-btn" href="#/play">Stop here</a></div>`;
      el.querySelector("#q-next").addEventListener("click", () => {
        s.floor += 1; s.room = 0; s.mmax = monsterHp(0, s.floor); s.mhp = s.mmax; s.windup = false; s.hp = Math.min(s.max, Math.max(s.hp, Math.round(s.max / 2))); s.charges = Math.max(3, s.charges); s.log = [];
        said = coach(character, "level"); fight();
      });
    };

    const defeat = () => {
      store.games.record("quest", { won: false, score: s.floor });
      const line = coach(character, "lose"); speak(line);
      el.innerHTML = `${head("The run is over")}
        <div class="pq-arena over"><div class="pq-fighter pq-foe">${monsterArt(MONSTERS[s.room].name, "pq-mon")}</div><div class="pq-floor"></div></div>
        <div class="gs-coach"><img alt="" src="${stillUrl(character)}"><p>${esc(line)}</p></div>
        <ul class="pq-sum"><li><b>${s.floor}</b><span>floor reached</span></li><li><b>${s.cleared}</b><span>rooms cleared</span></li><li><b>${level}</b><span>your level</span></li></ul>
        <p class="pa-note">Finishing real tasks raises your level, and that makes every run easier. Perks do not carry to the next run.</p>
        <div class="gs-btns"><button class="pa-btn go" id="q-again" type="button">Start a new run</button><a class="pa-btn" href="#/play">Back</a></div>`;
      el.querySelector("#q-again").addEventListener("click", () => { s = newRun(store.rewards.state().level || 1, store.quest.state().bought); said = coach(character, "start"); fight(); });
    };

    if (rec().seen) start(); else tutorial();
  },
};
