// MBUDDY on the phone: the shell. Three places (Buddy, Today, Settings), one ask bar (on Buddy), the live state that drives the buddy,
// a bottom sheet for adding things, and the toast. Everything it shows comes from the phone's own data (store.js) and brain (brain.js).
import * as store from "./store.js";
import * as brain from "./brain.js";
import { reactionFor } from "../api.js";
import { mountLive } from "../buddy/avatar.js";
import { castFor, CAST_INFO } from "../buddy/sprite.js";
import { esc } from "./util.js";
import buddyView from "./views/buddy.js";
import todayView from "./views/today.js";
import settingsView from "./views/settings.js";
import playView from "./views/play.js";
import searchView from "./views/search.js";
import { MOODS, wallpaper, icon } from "./icons.js";
import { GAMES, gameCard } from "./games.js";
import { runGame } from "./gameshell.js";
import { buzz } from "./haptics.js";

const $ = (s) => document.querySelector(s);
const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;

// Today, Do and Notes are one list each: the agenda, the tasks and the notes, every one with its own job and look
const only = (tab) => ({ render: (el, c, sub) => todayView.render(el, c, sub, tab), destroy: () => todayView.destroy && todayView.destroy() });

export const SECTIONS = [
  { id: "buddy", label: "Buddy", view: buddyView, ask: true, icon: svg("M4 12a8 8 0 1 1 3.2 6.4L4 20l1.1-3.6A7.97 7.97 0 0 1 4 12zM9 11h.01M15 11h.01") },
  { id: "today", label: "Today", view: only("agenda"), icon: svg("M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM8 13h3M8 16h6") },
  { id: "do", label: "Do", view: only("tasks"), icon: svg("M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM8.5 12.5l2.4 2.4 4.6-5") },
  { id: "remember", label: "Notes", view: only("notes"), icon: svg("M6 3h9l4 4v14H6zM15 3v4h4M9 12h6M9 16h4") },
  { id: "play", label: "Play", view: playView, icon: svg("M9 4h6v5h5v6h-5v5H9v-5H4V9h5zM12 12h.01") },
  { id: "settings", label: "Settings", view: settingsView, hidden: true, icon: "" },
  { id: "search", label: "Search", view: searchView, hidden: true, icon: "" },
];
// the old places still open: Now is Buddy, Plan is Today, Notes are in Remember, tasks are in Do
const LEGACY = { now: "buddy", plan: "today", notes: "remember" };

// ---- the shared context the screens use ----
export const ctx = {
  store, brain,
  status: { awake: true, thinking: false, speaking: false, listening: false, mic_on: true, emotion: "", last_reply: "", last_heard: "", pending: "" },
  listeners: new Set(),
  settings: () => store.settings.get(),
  setStatus(patch) { Object.assign(ctx.status, patch); ctx.emit(); },
  emit() { for (const fn of ctx.listeners) { try { fn(); } catch (e) { console.error(e); } } },
  on(fn) { ctx.listeners.add(fn); return () => ctx.listeners.delete(fn); },
  /** A short message at the bottom; [action] = { label, run } adds a button (Undo). */
  toast(msg, ms = 2800, action = null) {
    const t = $("#toast"), b = $("#toast-act");
    $("#toast-text").textContent = msg;
    b.hidden = !action; b.textContent = action ? action.label : ""; b.onclick = action ? () => { action.run(); t.classList.remove("show"); } : null;
    t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), action ? Math.max(ms, 4500) : ms);
  },
  go(path) { location.hash = "#/" + path; },
  /** The bottom sheet: [build] fills it and gets a function that closes it. */
  sheet(build) {
    const sh = $("#pa-sheet"), body = $("#pa-sheet-body");
    const close = () => { sh.classList.remove("open"); setTimeout(() => { sh.hidden = true; body.innerHTML = ""; }, 380); };
    body.innerHTML = `<i class="pa-grab" aria-hidden="true"></i>`;
    build(body, close);
    sh.hidden = false; requestAnimationFrame(() => requestAnimationFrame(() => sh.classList.add("open")));
    sh.querySelector("[data-close]").onclick = close;
    const first = body.querySelector("input, textarea"); if (first) setTimeout(() => first.focus(), 420);
    return close;
  },
};
// whatever goes wrong after start is kept in a short local log (Settings, Your data), and a save that fails is said out loud
window.addEventListener("error", (e) => { try { store.problems.add(`${e.message || "error"} (${String(e.filename || "").split("/").pop()}:${e.lineno || 0})`); } catch { /* nothing more to do */ } });
window.addEventListener("unhandledrejection", (e) => { try { store.problems.add("promise: " + String((e.reason && e.reason.message) || e.reason || "rejected")); } catch { /* nothing more to do */ } });
let lastFull = 0;
store.onChange((what) => { if (what === "storage_error" && Date.now() - lastFull > 20000) { lastFull = Date.now(); ctx.toast("This phone's storage for MBUDDY is full: your last change was not saved. Export your data and clear old items.", 7000); } });
brain.init(ctx);
// a widget, a shortcut or a notification can bring the app up (or to the front) asking for a place; these listeners are in place before
// the first inbox is read, so a "go" that was waiting at a cold start is not lost
window.addEventListener("mbuddy-inbox", () => brain.importInbox());
window.addEventListener("mbuddy-go", (e) => { const to = String(e.detail || "").replace(/^#?\/?/, ""); if (to) ctx.go(to); });
brain.importInbox();
brain.syncToApp();
// keep the app's copy of the buddy's instructions current (a voice command heard while the screens are closed uses it)
let syncTimer = null;
store.onChange(() => { clearTimeout(syncTimer); syncTimer = setTimeout(() => brain.syncToApp(), 800); });
// what was done by voice while the app was closed comes in when it is opened again
document.addEventListener("visibilitychange", () => { if (!document.hidden) brain.importInbox(); });
window.__mbuddy = ctx;            // for the app's own checks

function theme() {
  const s = store.settings.get();
  try { if (window.Native && window.Native.setCharacter) window.Native.setCharacter(castFor(s.character)); } catch { /* not in the app */ }
  document.documentElement.dataset.theme = castFor(s.character);
  document.documentElement.dataset.motion = s.reduced_motion ? "reduced" : "full";
}

// ---- header: the buddy, its name and what it is doing ----
let lastAvatar = "";
let pillReward = null;
function drawTop() {
  const s = store.settings.get(), st = ctx.status;
  const r = reactionFor(st), key = s.character + r;
  if (key !== lastAvatar) { mountLive($("#pa-avatar"), s.character, r, s); lastAvatar = key; }
  $("#pa-name").textContent = CAST_INFO[castFor(s.character)][0];
  const label = st.listening ? "Listening" : st.thinking ? "Thinking" : st.speaking ? "Talking" : "Ready";
  $("#pa-state").textContent = pillReward && pillReward.until > Date.now() ? pillReward.text : label;
  $("#pa-state-wrap").dataset.state = label.toLowerCase();
  // the character stretches into the island while it has something to say: what it is doing, or a reward
  const me = $("#pa-me"), reward = pillReward && pillReward.until > Date.now() ? pillReward : null;
  const text = reward ? reward.text : st.listening ? "Listening" : st.thinking ? "Thinking" : "";
  $("#pa-say").textContent = text;
  me.classList.toggle("on", !!text); me.classList.toggle("ok", !!reward);
  $(".pa-top").classList.remove("pill-on");
  $("#pa-pbar").style.width = reward ? "100%" : st.thinking ? "55%" : "0";
}
ctx.on(drawTop);
store.onChange((what) => { if (what === "settings") { theme(); lastAvatar = ""; drawTop(); } });
$("#pa-gear").addEventListener("click", () => ctx.go("settings"));
$("#pa-search").addEventListener("click", () => ctx.go("search"));


// ---- the bubble: tap the buddy and a bubble opens with what you use most and the games, without leaving the screen ----
let bub = null;
function closeBubble() { if (bub) bub.classList.remove("open"); }
const KINDS = [[/timer|minute|focus|second/i, "timer"], [/\bcall|phone|ring\b/i, "phone"], [/music|play|song|spotify|lofi|youtube/i, "music"], [/\bnote|remember|write down/i, "paper"],
  [/alarm|wake me/i, "clock"], [/remind|task|todo/i, "check"], [/message|send|text|whatsapp|sms/i, "chat"], [/weather|rain|temperature/i, "cloud"], [/today|agenda|calendar|schedule/i, "cal"]];
const short = (t) => { const w = t.replace(/^(please|hey|ok)\s+/i, "").split(/\s+/).slice(0, 3).join(" "); return w.length > 14 ? w.slice(0, 13) + "…" : w; };
/** What you use most: the commands you really repeat (from your own history), filled up to four with starters. */
function frequent() {
  const seen = new Map();
  for (const c of store.convo.list(200)) { const t = String(c.you || "").trim(); if (t.length < 2 || t.length > 60) continue; const k = t.toLowerCase(); const o = seen.get(k) || { t, n: 0 }; o.n++; seen.set(k, o); }
  const real = [...seen.values()].filter((o) => o.n >= 2).sort((a, b) => b.n - a.n).slice(0, 4)
    .map((o) => ({ text: o.t, label: short(o.t), icon: (KINDS.find(([re]) => re.test(o.t)) || [0, "chat"])[1] }));
  const starters = [{ text: "Start a 10 minute timer", label: "Timer", icon: "timer" }, { text: "What's on today?", label: "Today", icon: "cal" },
    { text: "Add a note", label: "Note", icon: "paper" }, { text: "Play some music", label: "Music", icon: "music" }]
    .filter((d) => !real.some((r) => r.text.toLowerCase() === d.text.toLowerCase()));
  return { list: [...real, ...starters].slice(0, 4), real: real.length > 0 };
}
ctx.frequent = () => ({ ...frequent() });
ctx.chipsHtml = (list) => `<div class="pa-chipgrid">${list.map((f) => `<button type="button" class="pa-chipq" data-say="${esc(f.text)}" title="${esc(f.text)}">${icon(f.icon)}<span>${esc(f.label)}</span></button>`).join("")}</div>`;
function bubble(view = "home", arg = "") {
  if (!bub) {
    bub = document.createElement("div"); bub.className = "pa-bubble";
    bub.innerHTML = `<div class="pa-bubble-back" data-close></div><div class="pa-bubble-card" role="dialog" aria-modal="true"></div>`;
    document.body.append(bub); bub.querySelector("[data-close]").addEventListener("click", closeBubble);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeBubble(); });
  }
  const card = bub.querySelector(".pa-bubble-card");
  if (view === "answer") {
    const c = store.convo.list(200).find((x) => x.id === arg);
    if (!c) { closeBubble(); return; }
    card.innerHTML = `<div class="pa-bubble-head"><b>${esc(c.you.length > 60 ? c.you.slice(0, 57) + "…" : c.you)}</b><button class="pa-link" id="b-x" type="button">Close</button></div>
      <p class="pa-answer">${esc(c.me)}</p>
      <h3 class="pa-day">Sources, opened here</h3>
      <div class="pa-freq">${(c.sources || []).map((s, i) => `<button type="button" class="pa-freq-b" data-open-src="${i}">${icon("pin")}<span><b>${esc(s.title || s.host)}</b><br><small>${esc(s.host)}</small></span></button>`).join("")}</div>
      <div class="pa-actions" style="margin-top:12px"><button class="pa-btn" id="b-more" type="button">Tell me more</button><button class="pa-btn" id="b-note" type="button">Save as a note</button></div>`;
    card.querySelector("#b-x").addEventListener("click", closeBubble);
    card.querySelectorAll("[data-open-src]").forEach((b) => b.addEventListener("click", () => {
      const s = c.sources[Number(b.dataset.openSrc)];
      try { if (window.Native && window.Native.openLink) { window.Native.openLink(s.url, s.title || s.host); return; } } catch { /* below */ }
      window.open(s.url, "_blank", "noopener");
    }));
    card.querySelector("#b-more").addEventListener("click", () => { closeBubble(); ctx.say(`Tell me more about: ${c.you}`); });
    card.querySelector("#b-note").addEventListener("click", () => { store.notes.add(`${c.you}: ${c.me}`.slice(0, 1800)); ctx.toast("Saved as a note."); closeBubble(); });
  } else if (view === "game") {
    // "daily:<game>:<level>" is today's game
    const dparts = String(arg).startsWith("daily:") ? String(arg).split(":") : null;
    const gameId = dparts ? dparts[1] : arg;
    const g = GAMES.find((x) => x.id === gameId);
    card.innerHTML = `<div class="pa-bubble-head"><button class="pa-link" id="b-back" type="button">Back</button><b>${g ? g.name : "Game"}</b><button class="pa-link" id="b-x" type="button">Close</button></div><div id="b-game"></div>`;
    card.querySelector("#b-back").addEventListener("click", () => bubble("home"));
    card.querySelector("#b-x").addEventListener("click", closeBubble);
    runGame(gameId, card.querySelector("#b-game"), { daily: dparts ? { level: Number(dparts[2]) || 1 } : null, store, character: store.settings.get().character, speak: (t) => { try { if (store.settings.get().speak_replies && window.Native && window.Native.speak) window.Native.speak(t); } catch { /* silent */ } }, onClose: () => bubble("home") });
  } else {
    const f = frequent();
    card.innerHTML = `<div class="pa-bubble-head"><b>What now?</b><button class="pa-link" id="b-x" type="button">Close</button></div>
      <h3 class="pa-day">${f.real ? "You ask these most" : "Try one"}</h3>
      ${ctx.chipsHtml(f.list)}
      <h3 class="pa-day">Play without leaving</h3>
      <div class="pa-games compact">${GAMES.map((g, i) => gameCard(g, i === 0, store.games.get(g.id))).join("")}</div>`;
    card.querySelector("#b-x").addEventListener("click", closeBubble);
    card.querySelectorAll("[data-say]").forEach((b) => b.addEventListener("click", () => { closeBubble(); ctx.say(b.dataset.say); }));
    card.querySelectorAll("[data-g]").forEach((b) => b.addEventListener("click", () => { if (b.dataset.g === "quest") { closeBubble(); ctx.go("play/quest"); } else bubble("game", b.dataset.g); }));
  }
  requestAnimationFrame(() => bub.classList.add("open"));
}
ctx.bubble = bubble;
$("#pa-me").addEventListener("click", () => (bub && bub.classList.contains("open") ? closeBubble() : bubble("home")));

// ---- a reward moment: the character stretches into the island, the tick stamps in, a ring and confetti burst, xp counts ----
let rewardTimer = 0;
function celebrate(ev) {
  const name = ev.badge ? `Badge: ${ev.badge.name}` : ev.text;
  const extra = [ev.xp ? `+${ev.xp} xp` : "", ev.streak > 1 ? `${ev.streak} days in a row` : ""].filter(Boolean).join(" · ");
  pillReward = { text: ev.levelUp ? `Level ${ev.levelUp}` : `${name}${extra ? " · " + extra : ""}`, until: Date.now() + 3400 };
  const me = $("#pa-me"); me.classList.remove("hop"); void me.offsetWidth; me.classList.add("hop");
  drawTop();
  clearTimeout(rewardTimer); rewardTimer = setTimeout(drawTop, 3500);
  const r = me.getBoundingClientRect(), cx = r.left + 40, cy = r.top + r.height / 2;
  const ring = document.createElement("i"); ring.className = "pa-shock"; ring.style.left = cx + "px"; ring.style.top = cy + "px"; document.body.append(ring); setTimeout(() => ring.remove(), 1000);
  const cols = ["var(--accent)", "#ffcc33", "#4ade80", "#60a5fa", "#fb7185", "#fff"];
  if (document.documentElement.dataset.motion !== "reduced") for (let i = 0; i < 26; i++) {
    const b = document.createElement("i"); b.className = "pa-bit"; b.style.left = cx + "px"; b.style.top = cy + "px"; b.style.background = cols[i % cols.length];
    b.style.setProperty("--x", (Math.random() * 300 - 90) + "px"); b.style.setProperty("--y", (Math.random() * 260 + 10) + "px"); b.style.setProperty("--r", Math.random() * 720 + "deg");
    document.body.append(b); setTimeout(() => b.remove(), 1200);
  }
  buzz(ev.levelUp ? "level" : ev.badge ? "win" : "ok");
}
store.rewards.listen(celebrate);

// ---- asking: what you say appears at once, the buddy thinks, the answer joins the conversation ----
let busy = false;
export async function say(text) {
  if (busy) { ctx.toast("One moment, I'm still answering."); return; }
  busy = true;
  ctx.setStatus({ pending: text, thinking: true });
  if (!location.hash.startsWith("#/buddy")) ctx.go("buddy");
  try {
    const r = await brain.ask(text);
    // speaking: the buddy looks like it is talking for about as long as the words take
    if (r.say && store.settings.get().speak_replies) { ctx.setStatus({ speaking: true }); setTimeout(() => ctx.setStatus({ speaking: false }), Math.min(15000, 900 + r.say.length * 62)); }
  } finally { busy = false; ctx.setStatus({ pending: "", thinking: false }); }
}
ctx.say = say;
$("#pa-ask").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#pa-in"), text = input.value.trim();
  if (!text) return;
  input.value = ""; input.blur(); $("#pa-ask").classList.remove("typed");
  say(text);
});
$("#pa-in").addEventListener("input", (e) => $("#pa-ask").classList.toggle("typed", !!e.target.value.trim()));

// the microphone: listen once, then answer what was said
let micAskedAt = 0;
// the speech engine is really ready: say so on screen as well as with the tone (a muted tablet has no tone)
window.addEventListener("mbuddy-listening", () => { ctx.toast("Listening. Speak now.", 5000); });
window.addEventListener("mbuddy-mic-granted", () => { if (Date.now() - micAskedAt < 60000) { micAskedAt = 0; $("#pa-mic").click(); } });
$("#pa-mic").addEventListener("click", async () => {
  const bar = $("#pa-ask");
  if (bar.classList.contains("listening")) return;
  if (!window.Native) { ctx.toast("The microphone works in the phone app."); return; }
  bar.classList.add("listening"); ctx.setStatus({ listening: true });
  let r = null;
  try { r = await (await fetch("/native/listen", { method: "POST", body: "{}" })).json(); } catch { /* handled below */ }
  bar.classList.remove("listening"); ctx.setStatus({ listening: false });
  if (!r) { ctx.toast("I couldn't listen just now."); return; }
  if (r.error === 9) { micAskedAt = Date.now(); ctx.toast("Allow the microphone and I will start listening."); return; }
  if (r.text) say(r.text);
  else ctx.toast(r.error === 6 || r.error === 7 ? "I didn't catch that. Tap the microphone and try again." : r.error ? `The speech engine said no (code ${r.error}). Try again, or type it.` : "I didn't catch that.");
});

// ---- the places ----
let active = null, activeId = "";
function moveMark(id) {
  const a = $(`#pa-nav a[data-nav="${id}"]`), mark = $("#pa-nav-mark");
  if (!a || !mark) return;
  mark.style.transform = `translateX(${a.offsetLeft}px)`; mark.style.width = a.offsetWidth + "px";
}
function route() {
  let path = location.hash.replace(/^#\/?/, "") || "buddy";
  const first = path.split("/")[0];
  if (LEGACY[first]) { location.replace("#/" + LEGACY[first] + path.slice(first.length)); return; }
  if (first === "today" && /^today\/(tasks|notes)/.test(path)) { location.replace(path.includes("tasks") ? "#/do" : "#/remember"); return; }
  const parts = path.split("/");
  const sec = SECTIONS.find((s) => s.id === parts[0]) || SECTIONS[0];
  $("#pa-nav").querySelectorAll("a").forEach((a) => (a.dataset.nav === sec.id ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  moveMark(sec.id);
  document.body.dataset.place = sec.id;
  document.body.style.setProperty("--wall", wallpaper(sec.id));
  document.body.style.setProperty("--mood", (MOODS[sec.id] || MOODS.buddy).color);
  $("#pa-dock").classList.toggle("with-ask", !!sec.ask);
  const deeper = activeId === sec.id && parts.length > 1, back = activeId === sec.id && parts.length === 1 && active && active._deep;
  if (active && active.destroy) active.destroy();
  const root = $("#pa-content");
  const el = document.createElement("div");
  el.className = "pa-view " + (deeper ? "in-right" : back ? "in-left" : "in-fade");
  el.dataset.view = sec.id;
  root.replaceChildren(el);
  active = sec.view; active._deep = parts.length > 1; activeId = sec.id;
  // The page starts transparent and is shown a frame later. Whatever happens (a screen that throws while it draws, a web view that
  // does not run its frames for a moment) it is shown: the black screen was a page left at opacity 0.
  const show = () => el.classList.add("shown");
  const t0 = performance.now();
  const failed = (e) => { console.error(e); el.innerHTML = `<p class="pa-note bad">This screen didn't load: ${esc(String(e && e.message || e))}</p>`; show(); };
  try { Promise.resolve(sec.view.render(el, ctx, parts.slice(1))).catch(failed); } catch (e) { failed(e); }
  root.scrollTop = 0;
  show();
  window.__lastRenderMs = Math.round(performance.now() - t0);
}

function boot() {
  theme();
  $("#pa-nav").insertAdjacentHTML("beforeend", SECTIONS.filter((s) => !s.hidden).map((s) => `<a href="#/${s.id}" data-nav="${s.id}" aria-label="${s.label}">${s.icon}<span>${s.label}</span></a>`).join(""));
  drawTop();
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => moveMark(activeId));
  route();
}
boot();

// ---- the tour: once, how to reach the buddy without opening the app ----
function tour() {
  const s = store.settings.get();
  if (!s.onboarding_done || s.tour_done) return;
  ctx.sheet((body, close) => {
    const row = (id, t, d, go) => `<button type="button" class="pa-tour-row" data-go="${go}"><b>${t}</b><span>${d}</span></button>`;
    body.insertAdjacentHTML("beforeend", `<h2>Ways to reach me</h2><p class="pa-note">Without opening the app, and without any microphone staying on.</p>
      <div class="pa-tour">${row("a", "Hold the power button", "Make me your assistant once, then call me from anywhere.", "settings/calling")}
      ${row("b", "Both volume keys", "Press volume up and down together.", "settings/calling")}
      ${row("c", "An edge handle", "A small buddy at the edge of the screen. Tap it for what you use most.", "settings/smart")}
      ${row("d", "Heads-up cards", "Before an event, a task at its time, a morning briefing.", "settings/smart")}</div>
      <button class="pa-btn go" id="t-ok" type="button">Got it</button>`);
    const done = () => { store.settings.set({ tour_done: true }); close(); };
    body.querySelector("#t-ok").addEventListener("click", done);
    body.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => { done(); ctx.go(b.dataset.go); }));
  });
}
setTimeout(tour, 1800);
