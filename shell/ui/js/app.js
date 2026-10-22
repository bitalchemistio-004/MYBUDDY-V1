// MBUDDY app: theme, navigation, live state, window controls.
import { api, statusStream, stateLabel, reactionFor, REMOTE, token, setToken, pairPhone } from "./api.js";
import { mountLive } from "./buddy/avatar.js";
import { CAST_INFO, castFor } from "./buddy/sprite.js";
import { views } from "./views/index.js";
import { SPACES, SPACE_ICONS, resolve, spacesWithTabs } from "./spaces.js";
import { mountAskBar } from "./askbar.js";

const $ = (s) => document.querySelector(s);

export const ctx = {
  settings: null, options: null, status: null, connected: false,
  settingsSeq: 0,  // goes up each time ctx.settings is replaced; screens resync their controls on it
  listeners: new Set(),
  // A short message, optionally with one action ("Undo").
  toast(msg, ms = 2600, action = null) {
    const t = $("#toast");
    t.textContent = msg;
    if (action) {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = action.label;
      b.onclick = () => { t.classList.remove("show"); action.run(); };
      t.appendChild(b);
    }
    t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), ms);
  },
  // Save settings; the backend validates and returns the stored values.
  async save(changes) {
    sync.saving++;
    let r;
    try { r = await api.post("/ui/settings", { changes }); }
    finally {
      sync.saving--;
      if (!sync.saving && sync.again) { sync.again = false; followSettings(sync.rev); }
    }
    if (!r.ok) { ctx.toast(r.error || "Couldn't save that setting."); return false; }
    ctx.settings = r.settings;
    ctx.settingsSeq++;
    applyTheme();
    ctx.emit();
    return true;
  },
  // Batch redraws: at most once per frame, however fast updates arrive.
  emit() {
    if (ctx._queued) return;
    ctx._queued = true;
    requestAnimationFrame(() => { ctx._queued = false; for (const fn of ctx.listeners) { try { fn(); } catch (e) { console.error(e); } } });
  },
  on(fn) { ctx.listeners.add(fn); return () => ctx.listeners.delete(fn); },
};

// Follow settings changed elsewhere (the quick panel, the buddy): /ui/live
// carries settings_rev, and a new one refetches the values (S81). A refetch
// that lands while a save is in flight is dropped and redone after it, so
// it never flips back a switch the user just changed.
const sync = { rev: null, saving: 0, again: false, ticket: 0 };
async function followSettings(rev) {
  sync.rev = rev;
  if (sync.saving) { sync.again = true; return; }
  const ticket = ++sync.ticket;
  const r = await api.get("/ui/settings?only=settings").catch(() => null);
  if (ticket !== sync.ticket) return;  // a newer refetch is on its way
  if (sync.saving) { sync.again = true; return; }
  if (!r || !r.ok) { sync.rev = null; return; }  // try again on the next update
  ctx.settings = r.settings;
  ctx.settingsSeq++;
  applyTheme();
  ctx.emit();
}

// Theme: Clay by default; a character's own theme once one is chosen.
export function applyTheme() {
  const s = ctx.settings;
  if (!s) return;
  document.documentElement.dataset.theme = s.theme === "character" ? s.character : "clay";  // clay: a neutral white glow in this look
  document.documentElement.dataset.motion = s.reduced_motion ? "reduced" : "full";
}

// The app window's decoration (Settings, Buddy, Wardrobe): attributes on <html> that css/decor.css reads. Only what is unlocked arrives.
const LEDGES = { plain: ["#33353d", "none"], neon: ["#5fcf8e", "0 0 12px #5fcf8e"], rainbow: ["linear-gradient(90deg,#ff7ab6,#ffd35a,#5fd0e6,#a98cff)", "none"], wood: ["repeating-linear-gradient(90deg,#6a4a2a 0 10px,#8a6238 10px 20px)", "none"], gold: ["#ffc933", "0 0 10px #ffc933"] };
let decoKey = "";
function tryBar(w) {
  let bar = document.getElementById("try-bar");
  const p = w && w.preview;
  if (!p) { if (bar) bar.hidden = true; return; }
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "try-bar"; bar.className = "try-bar";
    bar.innerHTML = `<span></span><button type="button" class="btn small">Stop</button>`;
    bar.querySelector("button").onclick = async () => { await api.post("/ui/wardrobe", { stop_preview: true }).catch(() => null); };
    document.body.append(bar);
  }
  bar.hidden = false;
  bar.firstElementChild.textContent = `Trying on a decoration you have not earned yet · ends in ${p.left} s`;
}

export function applyDeco(w) {
  tryBar(w);
  if (!w || !w.app) return;
  const key = JSON.stringify([w.app, (w.notch || {}).ledge, !!w.preview]);
  if (key === decoKey) return;
  decoKey = key;
  const h = document.documentElement, a = w.app;
  const set = (name, v, none) => (v && v !== none ? h.setAttribute(`data-${name}`, v) : h.removeAttribute(`data-${name}`));
  set("skin", a.skin, "nocturne"); set("backdrop", a.backdrop, "none"); set("cards", a.cards, "hairline"); set("accent", a.accent, "buddy");
  h.dataset.scene = a.scene || "plate";
  const [bg, sh] = LEDGES[(w.notch || {}).ledge] || LEDGES.plain;       // the ledge is the notch's own, so the character stands on the same one everywhere
  h.style.setProperty("--ledge-bg", bg); h.style.setProperty("--ledge-sh", sh);
}

// The rail: the spaces (spaces.js), in the design's order. A space whose
// screens are not there is not listed: nothing leads to an empty page.
// Built once, so the live character keeps playing while the pages change;
// only the current-page mark moves.
function renderRail() {
  const side = $("#sidebar");
  const here = resolve(location.hash, views);
  if (!side.dataset.built) {
    side.dataset.built = "1";
    const link = (s) => `<a href="#/${s.id}" data-nav="${s.id}" aria-label="${s.label}">${SPACE_ICONS[s.id]}<span>${s.label}</span><span class="pend" hidden></span></a>`;
    side.innerHTML = `
      <div class="me"><div class="avatar" id="side-avatar"></div><small id="side-sub" class="sr">…</small></div>
      <div class="nav">${spacesWithTabs(views).map(link).join("")}</div>
      <div class="gap"></div>
      <div class="live"><span class="chip" id="live-chip"><span class="dot"></span><span id="live-text" class="sr">Starting…</span></span></div>`;
  }
  side.querySelectorAll("[data-nav]").forEach((a) => {
    if (!here.setup && a.dataset.nav === here.space.id) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  updateLive();
}

function updateLive() {
  const [text, cls] = stateLabel(ctx.status, ctx.connected);
  const chip = $("#live-chip");
  if (chip) { chip.className = "chip " + cls; $("#live-text").textContent = text.split(" · ")[0]; chip.title = text; }  // short in the rail
  const av = $("#side-avatar");
  if (av && ctx.settings) mountLive(av, ctx.settings.character, reactionFor(ctx.status), ctx.settings);
  const waiting = !!(ctx.status && ctx.status.pending_action && ctx.status.pending_action.text);
  const pend = document.querySelector('[data-nav="now"] .pend');
  if (pend) { pend.hidden = !waiting; pend.textContent = waiting ? "1" : ""; }
  const sub = $("#side-sub");
  if (sub && ctx.settings) sub.textContent = CAST_INFO[castFor(ctx.settings.character)][0];
}

// "Working on: my PC / my phone". On the phone the PC stays quiet (no voice, no spoken reminders) and the
// replies and approvals are for the phone. It goes through /ui/models, which a paired phone may call.
function whereSwitch() {
  const on = ctx.settings && ctx.settings.work_on === "phone" ? "phone" : "pc";
  const btn = (v, label) => `<button type="button" data-where="${v}" aria-pressed="${on === v}">${label}</button>`;
  return `<div class="where seg" role="group" aria-label="Where I'm working">${btn("pc", "On my PC")}${btn("phone", "On my phone")}</div>`;
}
async function setWorkOn(place, head) {
  const r = await api.post("/ui/models", { work_on: place }).catch(() => null);
  if (!r || !r.ok) { ctx.toast((r && r.error) || "Couldn't switch that."); return; }
  ctx.settings.work_on = place;
  head.querySelectorAll("[data-where]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.where === place)));
  ctx.toast(place === "phone" ? "Working on your phone. The PC stays quiet." : "Working on your PC again.");
  ctx.emit();
}

// A phone that is not paired yet: the code shown on the PC pairs it. Nothing else is shown until it is.
function pairingScreen(note = "") {
  $("#sidebar").hidden = true;
  $("#body").style.gridTemplateColumns = "1fr";
  document.body.classList.add("in-setup");
  $("#content").innerHTML = `<div class="view pairing"><h2 class="pairtitle">Pair this phone</h2>
    <p class="muted">On the PC, open MBUDDY, then Away, and press <b>New pairing code</b>. Type the six digits here.</p>
    ${note ? `<p class="none err" role="alert">${note}</p>` : ""}
    <form id="pair-form" class="stack" style="max-width:360px">
      <label class="stack" style="gap:4px"><span class="label">Code</span><input class="field" id="pair-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" aria-label="Pairing code"></label>
      <label class="stack" style="gap:4px"><span class="label">Name this phone</span><input class="field" id="pair-name" value="My phone" maxlength="40" aria-label="Name for this phone"></label>
      <button class="btn accent" type="submit">Pair</button></form></div>`;
  $("#pair-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await pairPhone($("#pair-code").value.trim(), $("#pair-name").value.trim()).catch(() => ({ ok: false, error: "I couldn't reach the PC." }));
    if (r.ok) location.reload(); else pairingScreen(r.error || "That didn't work.");
  });
}

let active = null;
async function route() {
  if (!ctx.settings) return;
  const here = resolve(location.hash, views);
  if (!ctx.settings.onboarding_done && !here.setup) { location.hash = "#/setup"; return; }
  const id = here.setup ? "setup" : here.view;
  document.body.classList.toggle("in-setup", !!here.setup);
  document.body.dataset.space = here.setup ? "setup" : here.space.id;
  document.body.dataset.tab = here.setup ? "" : here.tab;
  $("#sidebar").hidden = !!here.setup;
  $("#body").style.gridTemplateColumns = here.setup ? "1fr" : "";
  if (!here.setup) renderRail();  // the current-place mark moves at once, not when a slow screen has loaded
  if (active && active.destroy) active.destroy();
  const root = $("#content");
  root.innerHTML = "";
  let host = root;
  if (!here.setup) {
    // the space's title, its tabs, and the screen inside
    const head = document.createElement("header");
    head.className = "ph";
    head.innerHTML = `<div><h2>${here.space.label}</h2><p>${here.space.sub}</p></div>${whereSwitch()}`;
    root.appendChild(head);
    head.querySelectorAll("[data-where]").forEach((b) => b.addEventListener("click", () => setWorkOn(b.dataset.where, head)));
    if (here.tabs.length > 1) {
      const bar = document.createElement("nav");
      bar.className = "tabs"; bar.setAttribute("aria-label", here.space.label);
      bar.innerHTML = here.tabs.map(([tid, label]) => `<a href="#/${here.space.id}/${tid}" ${tid === here.tab ? 'aria-current="page"' : ""}>${label}</a>`).join("");
      root.appendChild(bar);
    }
    host = document.createElement("div");
    host.className = "tabbody";
    root.appendChild(host);
  }
  const el = document.createElement("section");
  el.className = "view";
  el.dataset.view = id;
  host.appendChild(el);
  active = views[id];
  await active.render(el, ctx);
  root.scrollTop = 0;  // every screen starts at its top (Home opened scrolled down)
}

function windowControls() {
  const tauri = window.__TAURI__;
  const w = tauri && tauri.window && tauri.window.getCurrentWindow ? tauri.window.getCurrentWindow() : null;
  $("#wc-min").onclick = () => w && w.minimize();
  $("#wc-max").onclick = () => w && w.toggleMaximize();
  // Close really closes the window (a hidden WebView2 keeps rendering, S50);
  // the buddy, the voice and the tray keep running. Quit is in the tray menu.
  $("#wc-close").onclick = () => w && w.close();
}

async function boot() {
  if (!window.__TAURI__) document.documentElement.dataset.shell = "web";  // a browser or the phone: no custom title bar
  if (REMOTE) {
    document.documentElement.dataset.remote = "1";
    window.addEventListener("mbuddy-access", (e) => {
      if (e.detail === 401) { setToken(""); pairingScreen("This phone isn't paired (any more). Pair it again."); }
      else { $("#content").innerHTML = `<div class="view pairing"><h2 class="pairtitle">Paused</h2><p class="muted">Access from phones is paused on the PC. Resume it in Away on the PC, then reload this page.</p></div>`; }
    });
    if (!token()) { pairingScreen(); return; }
  }
  windowControls();
  // The buddy can send the user to a section ("Choose an app" opens Apps I can use).
  const ev = window.__TAURI__ && window.__TAURI__.event;
  if (ev && ev.listen) ev.listen("mbuddy-goto", (e) => { const h = e && e.payload && e.payload.hash; if (typeof h === "string" && /^#\/[a-z-]+(\/[a-z-]+)?$/.test(h)) location.hash = h; });
  // Wait for the backend (the shell starts it; it may still be loading).
  for (;;) {
    const r = await api.get("/ui/settings").catch(() => null);
    if (r && r.ok) { ctx.settings = r.settings; ctx.options = r.options; break; }
    $("#content").innerHTML = `<div class="offline"><div class="card surface"><h2 style="margin:0 0 6px">Starting MBUDDY…</h2><p class="muted" style="margin:0">Loading the voice and the brain. This takes a few seconds the first time.</p></div></div>`;
    await new Promise((r) => setTimeout(r, 1500));
  }
  applyTheme();
  ctx.on(updateLive);
  statusStream((s) => {
    ctx.status = s;
    applyDeco(s.wardrobe);
    if (s.settings_rev !== undefined && s.settings_rev !== sync.rev) followSettings(s.settings_rev);
    ctx.emit();
  }, (c) => { ctx.connected = c; if (c) sync.rev = null; ctx.emit(); });  // after a reconnect, refetch once
  window.addEventListener("hashchange", route);
  mountAskBar(ctx);
  await route();
}

boot();
