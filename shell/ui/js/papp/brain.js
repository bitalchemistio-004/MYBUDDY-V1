// The phone's brain. What you say goes to a model that answers in a small JSON: what to say back, and the tools to use (a timer, an
// alarm, a task, a note, a call, a message, directions, or "do this inside another app", which the screen control carries out).
// The model can be the cloud (the app holds your own key), your PC's brain (when paired), and later one on the phone itself. With
// none of them, simple commands still work from the rules in phone-actions.js.
import * as store from "./store.js";
import { api, chatTurn } from "../api.js";
import { parseQuick } from "../phone-actions.js";
import { CAST_INFO, castFor } from "../buddy/sprite.js";

const N = () => window.Native || null;
let ctx = null;
export function init(c) { ctx = c; }

const z = (n) => String(n).padStart(2, "0");
const localStamp = (d) => `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;

export const hasCloudKey = () => { try { return !!(N() && N().cloudKeyPresent && N().cloudKeyPresent()); } catch { return false; } };
export const pairedPc = () => { try { return !!(N() && N().paired && N().paired()); } catch { return false; } };
export const phoneModelReady = () => { try { const s = N() && N().localModelState ? JSON.parse(N().localModelState()) : null; return !!(s && s.active); } catch { return false; } };
export const screenControl = () => { try { const s = N() && N().agentStatus ? JSON.parse(N().agentStatus()) : null; return !!(s && s.enabled && s.connected); } catch { return false; } };

// ---- the tools ----
// privacy mode (set in Settings, the tile or the notification): the buddy does not open or use other apps. It says so, instead of promising.
const privacyOn = () => { try { return !!(N() && N().privacyState && JSON.parse(N().privacyState()).on); } catch { return false; } };
export const PRIVACY_SAY = "Privacy mode is on, so I will not open or use other apps. Turn it off to use me for that.";
const PRIVACY_APP = /^(please\s+)?(send|message|text|whatsapp|open|launch|start|call|ring|dial|reply|post|share|forward|email|play|navigate|directions?|take me|show me on)\b/i;
const PRIVATE_KINDS = new Set(["call", "sms", "whatsapp", "navigate", "search", "open_gallery"]);
const APP_TOOLS = new Set(["call", "sms", "message", "whatsapp", "navigate", "directions", "search", "open_app", "open", "email", "map", "phone", "contact", "open_gallery", "music", "play"]);
let privacyHit = false;

function act(kind, params) {
  try { const r = N() && N().act ? JSON.parse(N().act(kind, JSON.stringify(params))) : null; return !!(r && r.ok); } catch { return false; }
}
const mins = (s) => { const m = Math.floor(s / 60), r = s % 60; return `${m ? m + " min" : ""}${m && r ? " " : ""}${r ? r + " s" : ""}`; };

/** Do one tool the model asked for. Returns a short line for "what I did", or "" when nothing happened. */
// The app the owner named for a message wins over the model's pick: "on WhatsApp" is WhatsApp, "a text" or "SMS" is the messages app.
export function namedMessageApp(said) {
  const s = String(said || "").toLowerCase();
  if (/\bwhats\s?app\b/.test(s)) return "whatsapp";
  if (/\b(sms|text message|a text|messages app)\b/.test(s)) return "sms";
  return "";
}

export function runTool(t, said = "") {
  const named = namedMessageApp(said);
  const k = String(t.tool || "").toLowerCase();
  if (APP_TOOLS.has(k) && privacyOn()) { privacyHit = true; return ""; }
  if (k === "timer") {
    const sec = Math.round(Number(t.seconds) || (Number(t.minutes) || 0) * 60);
    if (!(sec >= 1 && sec <= 86400)) return "";
    act("timer", { seconds: sec, label: t.label || "" }); store.timers.add(t.label, sec);
    return `Timer ${mins(sec)}`;
  }
  if (k === "alarm") {
    const h = Number(t.hour), m = Number(t.minute || 0);
    if (!(h >= 0 && h <= 23 && m >= 0 && m <= 59)) return "";
    act("alarm", { hour: h, minute: m, label: t.label || "" });
    return `Alarm at ${z(h)}:${z(m)}`;
  }
  if (k === "task") {
    const when = t.when ? String(t.when).slice(0, 16) : "";
    const task = store.tasks.add({ title: t.title, when });
    return task ? `Task: ${task.title}${when ? " (" + when.replace("T", " ") + ")" : ""}` : "";
  }
  if (k === "event") {
    const start = new Date(String(t.start || "").length === 16 ? t.start : t.start);
    if (isNaN(start)) return "";
    const ev = store.events.add({ title: t.title, start: localStamp(start), place: t.place || "" });
    act("calendar", { title: ev.title, start: start.toISOString(), place: ev.place });
    return `Event: ${ev.title}`;
  }
  if (k === "note") { const n = store.notes.add(t.text); return n ? "Saved a note" : ""; }
  if (k === "call" && t.number) { act("call", { number: String(t.number).replace(/[^\d+]/g, "") }); return `Call screen for ${t.number}`; }
  if (k === "sms" && t.number && named === "whatsapp" && N() && N().messageContact) {
    return N().messageContact("whatsapp", String(t.number).replace(/[^\d+]/g, ""), String(t.body || "")) ? "" : `WhatsApp message to ${t.number}`;
  }
  if (k === "sms" && t.number) { act("sms", { number: String(t.number).replace(/[^\d+]/g, ""), body: t.body || "" }); return `Message ready for ${t.number}`; }
  if (k === "navigate" && t.place) { act("navigate", { place: String(t.place) }); return `Directions to ${t.place}`; }
  if (k === "search" && t.query) { act("search", { query: String(t.query) }); return `Search: ${t.query}`; }
  if (k === "message" && t.to && t.text) {
    const why = N() && N().messageContact ? N().messageContact(String(named || t.app || "whatsapp"), String(t.to), String(t.text)) : "Messaging by name works in the phone app.";
    return why ? "" : `Message to ${t.to}`;
  }
  if (k === "open_app" && t.app) { return act("open_app", { app: String(t.app) }) ? `Open ${t.app}` : ""; }
  if (k === "phone" && t.goal) {
    const why = N() && N().agentStart ? N().agentStart(String(t.goal)) : "Screen control isn't available.";
    return why ? "" : `Working on it: ${String(t.goal).slice(0, 60)}`;
  }
  return "";
}

// ---- the prompt ----
function persona(forApp = false, forCloud = false) {
  const s = store.settings.get();
  const share = !forCloud || s.cloud_share;          // the cloud brain gets the owner's tasks, events and notes only when they allowed it
  const name = CAST_INFO[castFor(s.character)][0];
  const d = new Date();
  const open = !share ? "not shared" : store.tasks.list().filter((t) => !t.done).slice(0, 8).map((t) => `- ${t.title}${t.when ? " (" + t.when.replace("T", " ") + ")" : ""}`).join("\n") || "none";
  const evs = !share ? "not shared" : store.events.list().filter((e) => e.start >= localStamp(d)).slice(0, 5).map((e) => `- ${e.title} at ${e.start.replace("T", " ")}`).join("\n") || "none";
  const notes = !share ? "not shared" : store.notes.list().slice(0, 6).map((n) => `- ${n.text.slice(0, 100)}`).join("\n") || "none";
  return `You are ${name}, the owner's desk buddy, living on their phone (the app is MBUDDY). Reply in short, warm, direct sentences (one to three). No emoji.
${s.user_name ? `The owner is ${s.user_name}.` : ""} Now: ${forApp ? "{{NOW}}" : `${d.toString().slice(0, 24)} (local time, ${localStamp(d)})`}.
Their open tasks:\n${open}\nTheir next events:\n${evs}\nTheir notes:\n${notes}

Answer with ONE JSON object and nothing else: {"do":[ ...tools... ],"say":"what you say to them"}. Decide the tools FIRST; "do" may be empty. Tools:
{"tool":"timer","seconds":600,"label":""}  {"tool":"alarm","hour":7,"minute":30,"label":""}
{"tool":"task","title":"...","when":"YYYY-MM-DDTHH:mm or empty"}  {"tool":"event","title":"...","start":"YYYY-MM-DDTHH:mm","place":""}
{"tool":"note","text":"..."}  {"tool":"call","number":"+961..."}  {"tool":"sms","number":"+961...","body":"..."}
{"tool":"navigate","place":"..."}  {"tool":"search","query":"..."}   open a web search in the browser, only when the owner asks to see results
{"tool":"web","query":"..."}   look it up on the internet and answer aloud from what is found. Use it for any question about facts, people, places, news, weather, sport, prices, times or anything that may have changed or that you are not sure of. Your own memory is old: do not answer such questions from it.
{"tool":"open_app","app":"TikTok"}   ONLY to open an app and nothing more ("open TikTok")
{"tool":"message","app":"whatsapp","to":"Ben","text":"..."}   send a message to a person BY NAME (app is "whatsapp" or "sms"); the phone finds them in the contacts, types it and asks the owner before it is sent. Use this, not "phone", for any "tell/send/message <person> ..." request.
{"tool":"phone","goal":"..."}   do something INSIDE an app on this phone, in several steps: search in YouTube or TikTok, play or pick a video, find something in an app, read what an app shows. Put the owner's whole request in "goal". Use it for any request that asks for more than opening an app; the owner is asked before anything is sent.
Examples:
Owner: remind me to water the plants on friday at 6pm  ->  {"do":[{"tool":"task","title":"water the plants","when":"YYYY-MM-DDT18:00 (that friday)"}],"say":"Okay, I'll remind you on Friday at 6."}
Owner: tell Ben on WhatsApp that dinner is ready  ->  {"do":[{"tool":"message","app":"whatsapp","to":"Ben","text":"dinner is ready"}],"say":"Okay, I'll show you the message before it goes."}
Owner: open YouTube and search for cooking videos then play the first one  ->  {"do":[{"tool":"phone","goal":"open YouTube, search for cooking videos, play the first one"}],"say":"On it."}
Owner: open Spotify  ->  {"do":[{"tool":"open_app","app":"Spotify"}],"say":"Opening Spotify."}
Owner: who won the last Champions League final  ->  {"do":[{"tool":"web","query":"last Champions League final winner"}],"say":""}
Owner: what is 12 times 12  ->  {"do":[],"say":"144."}
Owner: mumble banana laptop  ->  {"do":[],"say":"Sorry, I didn't catch that. What would you like?"}
Only use a tool when the owner clearly asked for it, with the names and numbers they actually said. If the words do not make sense, or you are not sure what they want, do NOT guess: use an empty "do" and ask. The examples above are only examples; never copy their values.
Never say you did something (set, sent, added, saved, opened, called) unless the matching tool is in "do". If you cannot do it, say so.
Rules: never invent a phone number or a contact; if the request needs one you were not given, ask in "say" and leave "do" empty. Use the owner's own words for message text. If they only chat or ask a question, answer in "say" with an empty "do".`;
}

function firstJson(text) {
  const s = String(text || ""); const a = s.indexOf("{");
  if (a < 0) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = a; i < s.length; i++) {
    const c = s[i];
    if (inStr) { if (esc) esc = false; else if (c === "\\") esc = true; else if (c === '"') inStr = false; }
    else if (c === '"') inStr = true;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) { try { return JSON.parse(s.slice(a, i + 1)); } catch { return null; } }
  }
  return null;
}

async function cloudChat(messages) {
  const model = store.settings.get().cloud_model;
  const body = (format) => JSON.stringify({ model, messages, stream: false, ...(format ? { format: "json" } : {}), options: { temperature: 0.3, num_predict: 1500 } });
  for (const format of [true, false]) {
    let r;
    try { r = await fetch("/cloud/chat", { method: "POST", body: body(format) }); } catch { throw new Error("I can't reach the cloud right now."); }
    let j = {}; try { j = await r.json(); } catch { /* not json */ }
    if (r.status === 401) throw new Error("ollama.com refused my key (HTTP 401). Save a fresh Ollama key in Settings, Brain.");
    if (r.status === 403) throw new Error("ollama.com answered 403 for every cloud model I tried. Check that this Ollama account has cloud access.");
    if (r.status === 402) throw new Error(`${model} isn't on your cloud plan. Pick another model in Settings.`);
    if (r.status === 429) throw new Error("The cloud is busy. Try again in a moment.");
    if (r.status === 503 || r.status === 0) throw new Error("I can't reach the cloud right now.");
    const text = j && j.message && j.message.content;
    if (r.ok && text && text.trim()) return text;
  }
  throw new Error("The model gave no answer. Try again.");
}

/** The simple commands (timer, alarm, task, call, message, directions, search), from the rules in phone-actions.js: { say, did, source } or null. */
function byRules(t) {
  let found = parseQuick(t);
  if (found.some((f) => f.kind === "task")) found = found.filter((f) => f.kind !== "calendar");     // "remind me ..." is one task
  if (found.length) {
    const did = [];
    for (const f of found) {
      if (f.kind === "task") { const x = runTool({ tool: "task", title: f.params.title, when: f.params.when ? localStamp(new Date(f.params.when)) : "" }); if (x) did.push(x); }
      else if (f.kind === "calendar") { const x = runTool({ tool: "event", title: f.params.title, start: f.params.start, place: f.params.place }); if (x) did.push(x); }
      else if (["timer", "alarm", "call", "sms", "whatsapp", "navigate", "search", "open_gallery"].includes(f.kind)) {
        if (f.kind === "timer") { const x = runTool({ tool: "timer", seconds: f.params.seconds }); if (x) did.push(x); }
        else if (f.kind === "alarm") { const x = runTool({ tool: "alarm", hour: f.params.hour, minute: f.params.minute, label: f.params.label }); if (x) did.push(x); }
        else if (f.kind === "call") { const x = runTool({ tool: "call", number: f.params.number }); if (x) did.push(x); }
        else if (f.kind === "sms") { const x = runTool({ tool: "sms", number: f.params.number, body: f.params.body }); if (x) did.push(x); }
        else if (f.kind === "navigate") { const x = runTool({ tool: "navigate", place: f.params.place }); if (x) did.push(x); }
        else if (f.kind === "search") { const x = runTool({ tool: "search", query: f.params.query }); if (x) did.push(x); }
        else { act(f.kind, f.params); did.push(f.kind); }
      }
    }
    if (did.length) return { say: did.join(". ") + ".", did, source: "rules" };
  }
  return null;
}

// ---- the whole turn ----
/** What you said, to what happens: { say, did: [...], source }. Never throws. */
export async function ask(text) {
  const t = String(text || "").trim();
  if (!t) return { say: "", did: [], source: "" };
  ctx && ctx.setStatus({ thinking: true, last_heard: t });
  let result; lastSources = [];
  try {
    result = await think(t);
  } catch (e) {
    result = { say: String(e && e.message || "I couldn't answer that."), did: [], source: "error" };
  }
  store.convo.add(t, result.say, result.source, result.did, lastSources);
  for (const d of result.did) store.log.add(d);
  ctx && ctx.setStatus({ thinking: false, last_reply: result.say, emotion: result.source === "error" ? "worried" : result.did.length ? "happy" : "" });
  if (result.say && store.settings.get().speak_replies && N() && N().speak) N().speak(result.say);
  return result;
}

// What the model said, as { say, did }: its JSON when it gave one (and the tools in it are run), else its words.
const NEEDS_APP = /^(send|message|text|tell|ask|open|call|whatsapp|reply|write|post|share|forward|email|play|find|check|read|look|go|turn|book|order)\b/i;
const ACTION_WORDS = /\b(sending|setting|opening|adding|saving|calling|starting|creating|scheduling|writing|messaging|texting|booking|noting|putting|on it|will send|will set|will open|i'll send|i'll set|i'll open|sent|set|added|saved|created|scheduled|opened|called|messaged|started|written|noted)\b/i;
// an answer that only says it does not know: for a question, the web is asked instead
const DONT_KNOW = /\b(i (do not|don't|dont) (have|know)|i'?m not sure|i am not sure|i (cannot|can't|can not) (access|browse|check|look)|no (real-time|current|up-to-date) (information|data)|my (knowledge|training))\b/i;
const IS_QUESTION = /^(what|who|why|when|where|how|which|is|are|do|does|can|could|would|will|did)\b|\?\s*$/i;
const CLAIMS = /\b(i(?:'ve| have)?\s+(?:just\s+)?(?:set|sent|added|saved|created|scheduled|opened|called|messaged|started|put|made|booked|written|noted)|(?:has|have) been (?:set|sent|added|saved|created|scheduled|started))\b/i;
// A question about the world, answered from the web by the app (WebSearch.kt): { say, sources } or null when the web cannot be reached.
let lastSources = [];       // the pages the last web answer was built from, kept with the answer so it can be opened
async function webAnswer(query, question) {
  try {
    const r = await fetch("/native/web", { method: "POST", body: JSON.stringify({ query, question }) });
    const j = await r.json();
    if (j && j.ok) lastSources = (j.sources || []).slice(0, 4).map((s) => ({ title: String(s.title || s.host || "").slice(0, 120), url: s.url, host: s.host || "" }));
    return j && j.ok ? j : null;
  } catch { return null; }
}

async function fromModel(raw, source, userText = "") {
  const j = firstJson(raw);
  if (!j) {
    // never read raw JSON aloud: its "say" if it can be found, else a plain apology
    const t = String(raw).trim();
    const said = /^\{|"do"/.test(t) ? ((t.match(/"say"\s*[:,]\s*"([^"]{2,400})"/) || [])[1] || "Sorry, my words came out jumbled. Could you say that again?") : t.slice(0, 600);
    return { say: said, did: [], source };
  }
  let tools = (Array.isArray(j.do) ? j.do : []).filter((t) => t && typeof t === "object");
  // a task for screen control opens the app itself
  if (tools.some((t) => t.tool === "phone")) tools = tools.filter((t) => t.tool !== "open_app");
  const did = []; let webSay = null;
  for (const t of tools) {
    if (t.tool === "web") {
      const a = await webAnswer(String(t.query || userText), userText || String(t.query || ""));
      webSay = a ? a.say : "I couldn't reach the web just now.";
      if (a) did.push(`Looked up: ${String(t.query || userText).slice(0, 60)}${a.sources && a.sources[0] ? " (" + a.sources[0].host + ")" : ""}`);
      continue;
    }
    const x = runTool(t, userText); if (x) did.push(x);
  }
  // a question the model left unanswered (the small model sometimes answers with nothing at all): look it up rather than stay silent
  if (webSay === null && !did.length && (!String(j.say || "").trim() || DONT_KNOW.test(String(j.say))) && IS_QUESTION.test(userText.trim())) {
    const a = await webAnswer(userText, userText);
    if (a) { webSay = a.say; did.push(`Looked up: ${userText.slice(0, 60)}${a.sources && a.sources[0] ? " (" + a.sources[0].host + ")" : ""}`); }
  }
  let say = (webSay !== null ? webSay : String(j.say || (did.length ? "Done." : "Sorry, I didn't get that. Could you say it again?"))).trim();
  if (privacyHit && !did.length) return { say: PRIVACY_SAY, did: [], source };
  // A small model sometimes says it did something without asking for the tool. Nothing was done, so do not say it was.
  if (!did.length && (CLAIMS.test(say) || (ACTION_WORDS.test(say) && !IS_QUESTION.test(userText.trim())))) {
    const ruled = userText ? byRules(userText) : null;
    if (ruled) return { ...ruled, source: source + "+rules" };
    // A request that needs another app goes to screen control with the owner's own words (it asks before anything is sent).
    if (userText && NEEDS_APP.test(userText) && screenControl()) {
      const x = runTool({ tool: "phone", goal: userText });
      if (x) return { say: "On it. I'll show you before anything is sent.", did: [x], source: source + "+rules" };
    }
    say = "I couldn't turn that into an action just now, so nothing was done. Try saying it as a command, like “remind me to call Sara tomorrow at 9”, or use the Do section.";
  }
  return { say, did, source };
}

// The shape the model on the phone must answer in (the runtime enforces it; a small model needs that).
const CHAT_SCHEMA = {
  type: "object",
  properties: {
    do: { type: "array", items: { type: "object", properties: {
      tool: { type: "string", enum: ["timer", "alarm", "task", "event", "note", "call", "sms", "navigate", "search", "web", "open_app", "message", "phone"] },
      seconds: { type: "integer" }, hour: { type: "integer" }, minute: { type: "integer" }, label: { type: "string" },
      title: { type: "string" }, when: { type: "string" }, start: { type: "string" }, place: { type: "string" }, text: { type: "string" },
      number: { type: "string" }, body: { type: "string" }, query: { type: "string" }, goal: { type: "string" }, app: { type: "string" }, to: { type: "string" },
    }, required: ["tool"] } },
    say: { type: "string" },
  },
  required: ["do", "say"],
};

async function viaCloud(t) {
  const history = store.convo.list(8).flatMap((c) => [{ role: "user", content: c.you }, { role: "assistant", content: c.me }]).filter((m) => m.content);
  return fromModel(await cloudChat([{ role: "system", content: persona(false, true) }, ...history, { role: "user", content: t }]), "cloud", t);
}
async function viaPc(t) {
  const r = await chatTurn(t, false);
  if (r && r.ok && r.reply) return { say: String(r.reply), did: [], source: "pc" };
  throw new Error("I can't reach your PC: MBUDDY isn't running there, or the PC is off. Start it, or choose Auto in Settings, Brain.");
}
async function viaPhone(t) {
  const history = store.convo.list(6).map((c) => `Owner: ${c.you}\nYou: ${c.me}`).join("\n");
  let r;
  try { r = await fetch("/native/local-chat", { method: "POST", body: JSON.stringify({ system: persona(), user: (history ? history + "\n" : "") + "Owner: " + t, schema: CHAT_SCHEMA }) }); } catch { throw new Error("The model on the phone isn't available here."); }
  const j = await r.json().catch(() => ({}));
  if (!j.ok) throw new Error(j.error || "The model on the phone couldn't answer.");
  return fromModel(j.text, "phone", t);
}

// A question about the world (facts, people, places, news, weather, prices, sport) is looked up on the web and answered from what is
// found, the way Gemini does (the small model on the phone otherwise answers some from its old memory, or not at all). Questions about the
// buddy, about the owner's own things, and sums are left to the brain. The same rule as the voice path (NativeBrain.worldQuestion).
const PERSONAL = /\b(you|your|yourself|my|mine|me|i|we|our)\b/i;
const OWN_THINGS = /\b(task|tasks|note|notes|remind|reminder|calendar|event|timer|alarm|schedule|agenda|time is it|what time|what day|the date|date today)\b/i;
const SUM = /^\W*(what('?s| is)\s+)?[-+*/x×÷().,%\d\s]+\??\s*$|\d+(\.\d+)?\s*(times|plus|minus|divided by|multiplied by|percent of|% of)\s*\d+/i;
export const worldQuestion = (t) => { t = String(t || "").trim(); return IS_QUESTION.test(t) && !PERSONAL.test(t) && !OWN_THINGS.test(t) && !SUM.test(t) && t.split(/\s+/).length >= 3; };

async function think(t) {
  privacyHit = false;
  // "privacy mode on" / "turn privacy mode off": worked out and applied by the app, so it can be turned off the way it was turned on
  try { const r = N() && N().privacyCommand ? N().privacyCommand(t) : ""; if (r) return { say: r, did: [], source: "rules" }; } catch { /* the rest decides */ }
  if (privacyOn() && (PRIVACY_APP.test(t.trim()) || parseQuick(t).some((f) => PRIVATE_KINDS.has(f.kind)))) return { say: PRIVACY_SAY, did: [], source: "rules" };
  try {
    if (N() && N().routeAppTask && N().routeAppTask(t)) return { say: "On it.", did: [`Working on it: ${t.slice(0, 60)}`], source: "rules" };
  } catch { /* the brain decides below */ }
  try {            // the tiny Mobile Actions model, for the phone's own functions (flashlight, contact, email, map, Wi-Fi, calendar)
    const d = N() && N().deviceAction ? N().deviceAction(t) : "";
    if (d) { const j = JSON.parse(d); return { say: j.say, did: j.did, source: "actions" }; }
  } catch { /* the brain decides below */ }
  if (worldQuestion(t) && N()) {
    const a = await webAnswer(t, t);
    if (a) return { say: a.say, did: [`Looked up: ${t.slice(0, 60)}${a.sources && a.sources[0] ? " (" + a.sources[0].host + ")" : ""}`], source: "web" };
  }
  const pref = store.settings.get().planner;
  const ready = { cloud: hasCloudKey(), pc: pairedPc(), phone: phoneModelReady() };
  // the app decides the order (Brains.kt): the owner's choice, or auto without a brain that just failed
  let order = null;
  try { order = N() && N().brainsOrder ? JSON.parse(N().brainsOrder()) : null; } catch { order = null; }
  if (!Array.isArray(order)) order = pref === "cloud" || pref === "pc" || pref === "phone" ? [pref] : ["cloud", "pc", "phone"];
  order = order.filter((w) => ready[w]);
  let firstError = null;
  if (!order.length && (pref === "cloud" || pref === "pc" || pref === "phone"))
    firstError = new Error({ cloud: "You chose the cloud brain, but no cloud key is saved. Add it in Settings, Brain, or choose Auto.", pc: "You chose your PC's brain, but this phone isn't paired with a PC. Pair it in Settings, or choose Auto.", phone: "You chose the model on this phone, but none is downloaded. Download one in Settings, Brain, or choose Auto." }[pref]);
  for (const where of order) {
    const t0 = Date.now();
    try {
      const r = await (where === "cloud" ? viaCloud(t) : where === "pc" ? viaPc(t) : viaPhone(t));
      try { N() && N().noteBrain && N().noteBrain(where, true, Date.now() - t0, ""); } catch { /* fine */ }
      return r;
    } catch (e) {                                                    // that brain could not answer: the next one tries
      try { N() && N().noteBrain && N().noteBrain(where, false, Date.now() - t0, String(e && e.message || "")); } catch { /* fine */ }
      firstError = firstError || e;
    }
  }
  if (firstError && !parseQuick(t).length) throw firstError;
  // No brain: the simple commands still work from the rules.
  const ruled = byRules(t);
  if (ruled) return ruled;
  return { say: "I need a brain to answer that. Add your cloud key in Settings, or pair with your PC. Simple commands like “10 minute timer” work without one.", did: [], source: "error" };
}

/** Whether a brain is ready (for the settings screen and the first-run hints). */
/** How each brain is doing (set up, last answer, how fast), from the app: { choice, order, cloud:{ready,ok,ms,ago,why}, pc:…, phone:… }. */
export function brainHealth() { try { return N() && N().brainsState ? JSON.parse(N().brainsState()) : null; } catch { return null; } }

export function brainState() {
  return { cloud: hasCloudKey(), pc: pairedPc(), phone: phoneModelReady(), model: store.settings.get().cloud_model };
}

/** Tell the app which brain and cloud model to use when the page is not open (screen control and the voice wake-up run without it). */
// for the checks that measure how the model on the phone reads requests (tools/bench on the device)
export const promptFor = persona;
export const chatSchema = () => CHAT_SCHEMA;

export function syncToApp() {
  const s = store.settings.get();
  try {
    if (!N()) return;
    N().setPlanner && N().setPlanner(s.planner); N().setCloudModel && N().setCloudModel(s.cloud_model);
    N().setSystemPrompt && N().setSystemPrompt(persona(true));
    N().setSystemPromptCloud && N().setSystemPromptCloud(persona(true, true));
    N().setRecent && N().setRecent(JSON.stringify(store.convo.list(6).map((c) => ({ you: c.you, me: c.me }))));
    // what you use most (the handle's menu) and what is coming up (the smart cards)
    if (N().setFrequent) {
      const seen = new Map();
      for (const c of store.convo.list(200)) { const t = String(c.you || "").trim(); if (t.length < 2 || t.length > 60) continue; const k = t.toLowerCase(); const o = seen.get(k) || { t, n: 0 }; o.n++; seen.set(k, o); }
      N().setFrequent(JSON.stringify([...seen.values()].filter((o) => o.n >= 2).sort((a, b) => b.n - a.n).slice(0, 3).map((o) => o.t)));
    }
    if (N().setAgenda) {
      const now = Date.now(), until = now + 36 * 3600000, at = (s) => (s ? new Date(s).getTime() : 0);
      const items = [
        ...store.events.list().map((e) => ({ kind: "event", id: e.id, title: e.title, place: e.place || "", at: at(e.start) })),
        ...store.tasks.list().filter((t) => !t.done && t.when).map((t) => ({ kind: "task", id: t.id, title: t.title, place: "", at: at(t.when) })),
      ].filter((i) => i.at > now - 3600000 && i.at < until).sort((a, b) => a.at - b.at).slice(0, 38);
      N().setAgenda(JSON.stringify(items));
    }
    // what the home-screen widgets show: open tasks, today's progress, level and streak, the quest floor, whether today's game is waiting
    if (N().setWidget) {
      const r = store.rewards.state(), all = store.tasks.list(), dk = store.dayKey(), d = store.daily.today();
      N().setWidget(JSON.stringify({
        tasks: all.filter((t) => !t.done).slice(0, 8).map((t) => ({ id: t.id, title: t.title, at: t.when ? new Date(t.when).getTime() : 0 })),
        doneToday: all.filter((t) => t.done && store.dayKey(new Date(t.updated)) === dk).length,
        level: r.level, pct: r.pct, streak: r.streak, floor: store.games.get("quest").best || 0, dailyReady: !d.done,
      }));
    }
  } catch { /* not in the app */ }
}

/** Take in what the app did while the screens were closed (a task or note asked for by voice, what was said). */
export function importInbox() {
  let items = [];
  try { items = N() && N().drainInbox ? JSON.parse(N().drainInbox()) : []; } catch { items = []; }
  for (const it of items) {
    if (it.type === "task") store.tasks.add({ title: it.title, when: it.when ? String(it.when).slice(0, 16) : "" });
    else if (it.type === "note") store.notes.add(it.text);
    else if (it.type === "event") store.events.add({ title: it.title, start: String(it.start).slice(0, 16), place: it.place || "" });
    else if (it.type === "convo") store.convo.add(it.you, it.me, it.source || "voice", it.did || []);
    else if (it.type === "log") store.log.add(it.text);
    // taps made on a home-screen widget or a notification while the app was closed
    else if (it.type === "done") { const t = store.tasks.list().find((x) => x.id === it.id); if (t && !t.done) store.tasks.done(it.id, true); }
    else if (it.type === "snooze") { if (store.tasks.list().some((x) => x.id === it.id)) store.tasks.snooze(it.id, Number(it.mins) || 10); }
    else if (it.type === "focus") { store.rewards.add(20 + Math.min(30, Number(it.minutes) || 0), `Focus sprint, ${it.minutes} min`, "zone"); store.log.add(`Focus sprint, ${it.minutes} min`); }
    else if (it.type === "go") { try { window.dispatchEvent(new CustomEvent("mbuddy-go", { detail: String(it.to || "") })); } catch { /* no window */ } }
  }
  return items.length;
}
