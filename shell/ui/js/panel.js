// The quick panel: what is happening now (what the buddy last said, the PC,
// the job, the music, the reminders), chat, mic, buddy and abilities, next to
// the buddy. Everything shown comes from the backend; changes go to the real
// settings. "Abilities" only shows them; they are switched in Extensions.
import { api, chatTurn, statusStream, stateLabel, displayText } from "./api.js";
import { mountStill } from "./buddy/avatar.js";
import { CAST, CAST_INFO, castFor } from "./buddy/sprite.js";
import { pcStrip, workCard, musicCard, remindersCard } from "./buddy/live-cards.js";

const tauri = window.__TAURI__;
const invoke = (cmd, args) => (tauri ? tauri.core.invoke(cmd, args) : Promise.resolve());
const win = tauri && tauri.window ? tauri.window.getCurrentWindow() : null;
const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let settings = null, status = null, connected = false, tab = "now";
const lines = [];
let nowCards = null;  // the live cards of the Now tab while it is showing

function applySettings(s) {
  settings = s;
  document.documentElement.dataset.theme = s.theme === "character" ? s.character : "clay";
  document.documentElement.dataset.motion = s.reduced_motion ? "reduced" : "full";
  $("#mic").setAttribute("aria-checked", String(s.microphone_on !== false));
  $("#name").textContent = CAST_INFO[castFor(s.character)][0];
}

function push(who, text) {
  text = displayText(text);
  if (!text) return;
  const last = lines[lines.length - 1];
  if (last && last.who === who && (last.text === text || (who === "me" && text.startsWith(last.text)))) { last.text = text; }
  else lines.push({ who, text });
  if (lines.length > 40) lines.shift();
  if (tab === "chat") draw();
  else paintSay();
}

// What it last said; a PC action waiting for a yes comes first.
function paintSay() {
  const el = $("#n-say");
  if (!el) return;
  const asked = status && status.pending_action && status.pending_action.text;
  const said = displayText(status && status.last_reply) || (lines.length && lines[lines.length - 1].who === "me" ? lines[lines.length - 1].text : "");
  el.firstElementChild.textContent = asked ? `${asked.charAt(0).toUpperCase()}${asked.slice(1)}?` : said || "Say \u201cHey Buddy\u201d, or type below.";
  el.classList.toggle("quiet", !asked && !said);
  $("#n-ask").hidden = !asked;
}

function leaveNow() {
  if (!nowCards) return;
  for (const card of nowCards) card.destroy && card.destroy();
  nowCards = null;
}

async function draw() {
  const body = $("#body");
  $("#form").hidden = tab !== "chat" && tab !== "now";
  if (tab !== "now") leaveNow();
  if (tab === "now") {
    if (nowCards) { paintSay(); return; }  // already showing: the cards update themselves
    body.innerHTML = `<div class="now">
      <p class="say" id="n-say" aria-live="polite"><span></span></p>
      <div class="row" id="n-ask" hidden><button class="btn accent" data-ok="yes" type="button">Yes, do it</button><button class="btn" data-ok="no" type="button">No</button></div>
      <div class="pcs" id="n-pc" hidden></div>
      <section class="lcard" id="n-work" aria-label="Right now"></section>
      <section class="lcard" id="n-music" aria-label="Now playing"></section>
      <section class="lcard" id="n-rem" aria-label="Reminders"></section>
    </div>`;
    nowCards = [pcStrip($("#n-pc")), workCard($("#n-work")), musicCard($("#n-music")), remindersCard($("#n-rem"), { limit: 3 })];
    for (const card of nowCards) card.update(status);
    paintSay();
    body.scrollTop = 0;
  } else if (tab === "chat") {
    const ask = status && status.pending_action && status.pending_action.text;
    body.innerHTML = `<div class="msgs">${lines.length ? lines.map((l) => `<div class="m ${l.who}">${esc(l.text)}</div>`).join("")
      : `<div class="faint" style="text-align:center;padding:24px 0">Say “Hey Buddy”, or type below.</div>`}</div>
      ${ask ? `<div class="ask"><div class="label">Waiting for your OK</div><b>${esc(ask.charAt(0).toUpperCase() + ask.slice(1))}?</b>
        <div class="row" style="margin-top:8px"><button class="btn accent" data-ok="yes" type="button">Yes, do it</button><button class="btn" data-ok="no" type="button">No</button></div></div>` : ""}`;
    body.scrollTop = body.scrollHeight;
  } else if (tab === "buddy") {
    body.innerHTML = `<div class="sub" style="margin-top:4px">Your buddy</div>
      <div class="picks">${CAST.map((id) => `<button class="pick" type="button" data-char="${id}" aria-pressed="${castFor(settings.character) === id}"><div class="st" data-stage="${id}"></div>${esc(CAST_INFO[id][0])}</button>`).join("")}</div>
      <div class="sub">Where it lives</div>
      <div class="seg">${[["corner", "Corner"], ["edge", "Edge"], ["notch", "Notch"]].map(([k, n]) => `<button type="button" data-spot="${k}" aria-pressed="${settings.popup_style === k}">${n}</button>`).join("")}</div>
      <div class="sub">When it shows</div>
      <div class="seg">${[["closed", "When the app is closed"], ["always", "Always"]].map(([k, n]) => `<button type="button" data-when="${k}" aria-pressed="${(settings.buddy_when || "closed") === k}">${n}</button>`).join("")}</div>`;
    body.querySelectorAll("[data-stage]").forEach((el) => mountStill(el, el.dataset.stage));
  } else {
    body.innerHTML = `<div class="faint">Loading…</div>`;
    const r = await api.get("/ui/abilities").catch(() => null);
    if (tab !== "tools") return;
    body.innerHTML = r && r.ok ? abilitiesHtml(r) : `<div class="faint">Couldn't load that.</div>`;
  }
}

// One row of "What I can do": what it is, and whether MBUDDY can use it now.
function abilityRow(label, state, sub) {
  const cls = state === "on" ? "on" : state === "asks first" ? "asks" : "";
  const text = String(label || "");
  return `<div class="tool"><span>${esc(text.charAt(0).toUpperCase() + text.slice(1))}${sub
    ? `<small class="faint" style="display:block;font-size:var(--fs-caption);line-height:1.35;margin-top:2px">${esc(sub)}</small>` : ""}</span>
    <em class="state ${cls}" style="font-style:normal">${esc(state)}</em></div>`;
}

// MBUDDY's own tools, then its PC abilities by group as Extensions switches
// them (S93), then what it can't do yet. Every state comes from the backend.
function abilitiesHtml(r) {
  const own = r.abilities.filter((a) => a.state !== "not yet").map((a) => abilityRow(a.label, a.state)).join("");
  const later = r.abilities.filter((a) => a.state === "not yet").map((a) => abilityRow(a.label, a.state)).join("");
  const groups = (r.topics || []).map((t) => abilityRow(t.title, t.state, t.description)).join("");
  return `<div>${own}</div><div class="sub">On this PC</div><div>${groups}${later}</div>
    <p class="faint" style="margin:var(--s3) 0 0;font-size:var(--fs-caption)">Switch these on or off in the app, under Extensions.</p>`;
}

function live() {
  const [label] = stateLabel(status, connected);
  $("#state").textContent = label;
  if (settings) mountStill($("#av"), settings.character);
  if (status && status.last_heard && status.last_heard !== live._heard) { live._heard = status.last_heard; push("you", status.last_heard); }
  if (status && status.last_reply && status.last_reply !== live._reply) { live._reply = status.last_reply; push("me", status.last_reply); }
  if (tab === "chat" && status && (status.pending_action || {}).text !== live._ask) { live._ask = (status.pending_action || {}).text; draw(); }
  if (nowCards) { for (const card of nowCards) card.update(status); paintSay(); }
}

async function save(changes) {
  sync.saving++;
  let r;
  try { r = await api.post("/ui/settings", { changes }); }
  finally {
    sync.saving--;
    if (!sync.saving && sync.again) { sync.again = false; followSettings(sync.rev); }
  }
  if (r.ok) { applySettings(r.settings); draw(); }
  return r.ok;
}

// Follow settings changed in the app or elsewhere (S81: the mic switch here
// contradicted the app's). /ui/live carries settings_rev; a new one refetches.
// A refetch landing while a save is in flight is dropped and redone after it.
const sync = { rev: null, saving: 0, again: false, ticket: 0 };
async function followSettings(rev) {
  sync.rev = rev;
  if (sync.saving) { sync.again = true; return; }
  const ticket = ++sync.ticket;
  const r = await api.get("/ui/settings?only=settings").catch(() => null);
  if (ticket !== sync.ticket) return;
  if (sync.saving) { sync.again = true; return; }
  if (!r || !r.ok) { sync.rev = null; return; }  // try again on the next update
  applySettings(r.settings);
  if (tab !== "chat") draw();  // the buddy picks and the abilities show settings too
}

document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
  tab = b.dataset.tab;
  document.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  draw();
}));
$("#mic").addEventListener("click", () => save({ microphone_on: $("#mic").getAttribute("aria-checked") !== "true" }));
$("#body").addEventListener("click", async (e) => {
  const when = e.target.closest("[data-when]");
  if (when) { save({ buddy_when: when.dataset.when }); return; }
  const ch = e.target.closest("[data-char]"), spot = e.target.closest("[data-spot]"), ok = e.target.closest("[data-ok]");
  if (ch) save({ character: ch.dataset.char, theme: settings.theme === "clay" ? "clay" : "character" });
  if (spot) save({ popup_style: spot.dataset.spot, buddy_position: {} });  // choosing a place puts it there, wherever it was dragged
  if (ok) { await api.post("/ui/pending-action", { decision: ok.dataset.ok }); }
});
$("#form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("#input"), text = input.value.trim();
  if (!text) return;
  input.value = "";
  push("you", text);
  const r = await chatTurn(text, settings ? settings.speak_replies : true, () => push("me", "Still working on it… I'll answer here when it's done."));
  if (r && r.ok) push("me", r.reply);
  else if (r && r.error === "interrupted") push("me", "Something else came in before I could answer that. Ask me again?");
  else if (r && r.error === "too_slow") push("me", "This one is taking far too long, so I stopped waiting. Check Now for what happened.");
  else push("me", "I couldn't answer that just now.");
});
$("#full").addEventListener("click", () => { invoke("open_main").catch(() => {}); if (win) win.close(); });
window.addEventListener("keydown", (e) => { if (e.key === "Escape" && win) win.close(); });

(async () => {
  for (;;) {
    const r = await api.get("/ui/settings").catch(() => null);
    if (r && r.ok) { applySettings(r.settings); break; }
    await new Promise((res) => setTimeout(res, 1000));
  }
  const past = await api.get("/ui/conversation?limit=20").catch(() => null);
  for (const t of (past && past.ok && past.turns) || []) { push("you", t.you); push("me", t.me); }
  statusStream((s) => {
    status = s;
    if (s.settings_rev !== undefined && s.settings_rev !== sync.rev) followSettings(s.settings_rev);
    live();
  }, (c) => { connected = c; if (c) sync.rev = null; live(); });
  draw();
  $("#input").focus();
})();
