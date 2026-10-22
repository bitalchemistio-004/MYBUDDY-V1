// The phone tools page. It works with no PC: alarms, timers, calendar events, calls, messages, directions and searches are
// links Android opens (js/phone-actions.js); tasks are kept on the phone and sent to the PC when it can be reached;
// and a guide says which AI model to get in Google AI Edge Gallery. When the PC IS reachable, the same page also shows
// what MBUDDY asked the phone to do and can ask MBUDDY's own brain.
import { api, chatTurn, token } from "./api.js";
import { buildAction, parseQuick, isAndroid, recommend, GALLERY_PLAY } from "./phone-actions.js";

const $ = (s, r = document) => r.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const native = window.Native ? (() => { try { return JSON.parse(window.Native.deviceInfo()); } catch { return null; } })() : null;   // set only inside the Android app
const android = isAndroid(navigator.userAgent) || !!native;
const store = (k, v) => { try { if (v === undefined) return JSON.parse(localStorage.getItem(k) || "null"); localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: it still works for this visit */ } return v; };
let online = false, paired = !!token();

// ---- is the PC reachable? ----
async function checkPc() {
  try {
    const r = await fetch("/health", { cache: "no-store", signal: AbortSignal.timeout(3500) });
    const j = await r.json();
    online = !!(r.ok && j.ok && !j.paused) && paired;
    if (online) {
      // The PC answers to anyone; only a paired phone is let in. If it no longer knows this phone (removed on the PC), say so.
      const a = await fetch("/remote/ui/phone-actions", { cache: "no-store", signal: AbortSignal.timeout(3500) });
      if (a.status === 401) {
        paired = false; online = false;
        try { localStorage.removeItem("mbuddy_phone_token"); } catch { /* private mode */ }
        if (window.Native && window.Native.pairingLost) window.Native.pairingLost();
      }
    }
  } catch { online = false; }
  const chip = $("#conn");
  chip.className = `chip ${online ? "ok" : "warn"}`;
  $("#conn-t").textContent = online ? "PC connected" : paired ? "PC not reachable: working on this phone" : "Not paired: working on this phone";
  return online;
}

// ---- a link to tap, for one action ----
function actNode(act, after) {
  const wrap = document.createElement("div");
  wrap.className = "result";
  const mk = (a, first) => {
    if (a.file) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "act"; b.textContent = a.label;
      b.addEventListener("click", () => { const url = URL.createObjectURL(new Blob([a.file.text], { type: a.file.type })); const l = document.createElement("a"); l.href = url; l.download = a.file.name; l.click(); setTimeout(() => URL.revokeObjectURL(url), 4000); after && after(); });
      return b;
    }
    const l = document.createElement("a");
    l.className = `act${first ? " go" : ""}${act.android && !android && first ? " off" : ""}`; l.href = a.href; l.textContent = a.label;
    if (a.href.startsWith("http")) l.target = "_blank";
    l.rel = "noopener";
    l.addEventListener("click", () => after && after());
    return l;
  };
  // Inside the app an event can be written straight into the phone's calendar (no form to fill, no second app).
  if (window.Native && act.native) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "act go"; b.textContent = act.native.kind === "calendar" ? "Save to my calendar" : act.label;
    b.addEventListener("click", () => { try { window.Native.act(act.native.kind, JSON.stringify(act.native.params)); } catch { /* the toast says what happened */ } after && after(); });
    wrap.append(b);
  } else wrap.append(mk(act, true));
  for (const alt of act.alt || []) wrap.append(mk(alt, false));
  if (act.android && !android) { const n = document.createElement("p"); n.className = "note"; n.textContent = "This one works on an Android phone."; wrap.append(n); }
  return wrap;
}

// ---- screen control: the buddy does a task in any app (only inside the Android app) ----
const AGENTY = /^(send|message|text|whatsapp|reply|open|write|post|tell|ask|find|play|order|book|add|remove|delete|check|read|look|go|turn|set|share|forward|email|search in|call)\b/i;
const agentStatus = () => { try { return window.Native && window.Native.agentStatus ? JSON.parse(window.Native.agentStatus()) : null; } catch { return null; } };
const agentOn = () => { const s = agentStatus(); return !!(s && s.enabled && s.connected && s.paired); };
function startAgent(goal, box) {
  const why = window.Native.agentStart(goal);
  if (box) { box.replaceChildren(); const p = document.createElement("p"); p.className = why ? "note bad" : "note good"; p.textContent = why || "On it: watch the buddy at the top of the screen."; box.append(p); }
  renderCtl();
  return !why;
}
function renderCtl() {
  const sec = $("#ctl"), body = $("#ctl-body"), s = agentStatus();
  if (!sec || !body) return;
  sec.hidden = !s;
  if (!s) return;
  const key = JSON.stringify([s.enabled, s.connected, s.running, s.voice, s.paired]);
  if (body.dataset.key === key) return;               // nothing changed: leave the buttons alone so a tap is never lost to a redraw
  body.dataset.key = key;
  if (!s.paired) {
    body.innerHTML = `<p class="note">To do things in other apps, the buddy needs your PC: it plans each step there. Pair this phone first.</p>`;
  } else if (!s.enabled || !s.connected) {
    body.innerHTML = `<p class="note">With screen control the buddy can open an app, type for you and tap, one step at a time, with its own frame on top. It reads back what it wrote and waits for your yes before it sends, pays, deletes, posts or calls, and it never touches banking, payment or password apps. You turn it on yourself: Android Settings, Accessibility, Downloaded apps, MBUDDY screen control.</p>
      <button class="go" type="button" id="ctl-open">Open Accessibility settings</button>`;
    $("#ctl-open").addEventListener("click", () => window.Native.openControlSettings());
  } else {
    body.innerHTML = `<p class="note good">Screen control is on. Say or type what to do (“send Sara on WhatsApp that I'm late”, “open Maps and find a pharmacy”) and press <b>Do it on my phone</b>.</p>
      <label class="row" style="color:inherit"><input type="checkbox" id="ctl-voice" style="width:24px;min-height:24px" ${s.voice ? "checked" : ""}> Talk and listen (otherwise it only shows text and buttons)</label>
      ${s.running ? `<button class="go" type="button" id="ctl-stop">Stop what it is doing</button>` : ""}`;
    $("#ctl-voice").addEventListener("change", (e) => window.Native.setVoice(e.target.checked));
    const stop = $("#ctl-stop"); if (stop) stop.addEventListener("click", () => { window.Native.agentStop(); setTimeout(renderCtl, 400); });
  }
}

// ---- tasks: kept here, sent to the PC when it can be reached ----
const tasks = () => store("mb_tasks") || [];
const saveTasks = (t) => store("mb_tasks", t.slice(-200));
async function syncTasks() {
  if (!(await checkPc())) return 0;
  let sent = 0;
  const list = tasks();
  for (const t of list) {
    if (t.sent) continue;
    const body = { title: t.title }; if (t.when) body.when = t.when.replace("T", " ").slice(0, 16);
    const r = await api.post("/ui/tasks", body).catch(() => null);
    if (r && r.ok) { t.sent = true; sent++; }
  }
  saveTasks(list);
  return sent;
}
// A time as the phone shows it (the same form a date-time field uses), not as UTC: "tomorrow at 9" must stay 9.
function localInput(iso) {
  const d = new Date(iso), z = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;
}
function addTask(title, when) {
  const list = tasks();
  list.push({ id: Date.now().toString(36), title, when: when || "", created: Date.now(), sent: false });
  saveTasks(list);
}

// ---- the page ----
function render() {
  // In the app the phone's real memory is known (a browser only reports a rounded figure of at most 8 GB).
  const ramGb = native ? Math.round(native.ramMb / 1024) : navigator.deviceMemory;
  const rec = recommend({ android, ram: ramGb, cores: navigator.hardwareConcurrency });
  $("#app").innerHTML = `
    <section id="ctl" aria-label="Screen control" hidden><h2>Let the buddy do it</h2><div id="ctl-body"></div></section>
    <section aria-label="Tell me what to do"><h2>Tell me</h2>
      <div class="row"><input id="say" autocomplete="off" placeholder="“set a 10 minute timer”, “remind me to call Sara tomorrow at 9”…" aria-label="What to do"><button class="go" type="button" id="say-go">Go</button>${window.webkitSpeechRecognition || window.SpeechRecognition ? `<button type="button" id="say-mic" aria-label="Speak it" title="Speak it (Chrome sends the audio to Google's speech service)">🎤</button>` : ""}</div>
      <div class="row" style="margin-top:8px">${window.Native && window.Native.agentStart ? `<button type="button" class="go" id="say-agent" style="flex:1">Do it on my phone</button>` : ""}</div>
      <div id="say-out" class="result"></div>
      <p class="note">Understood on this phone, with no PC: timers, alarms, reminders and tasks, calls and messages to a number, directions, searches. ${online ? "Anything else goes to MBUDDY on your PC." : "Anything else needs your PC."}</p></section>
    <section id="from-pc" hidden><h2>From your PC</h2><div id="pc-list" class="list"></div></section>
    <section aria-label="Phone tools"><h2>Phone tools</h2><div class="grid">
      <div class="tile"><b>Alarm</b><input type="time" id="al-time" value="07:00" aria-label="Alarm time"><input id="al-label" placeholder="Label (optional)" maxlength="60" aria-label="Alarm label"><button class="go" type="button" id="al-go">Make the link</button><div id="al-out"></div></div>
      <div class="tile"><b>Timer</b><div class="chips">${[1, 5, 10, 15, 30].map((m) => `<button type="button" data-tm="${m}">${m} min</button>`).join("")}</div><div class="row"><input id="tm-min" type="number" min="1" max="1440" placeholder="Minutes" aria-label="Minutes"><button class="go" type="button" id="tm-go">Start</button></div><div id="tm-out"></div></div>
      <div class="tile"><b>Calendar event</b><input id="ev-title" placeholder="Title" maxlength="100" aria-label="Event title"><input type="datetime-local" id="ev-start" aria-label="Starts"><input id="ev-place" placeholder="Place (optional)" maxlength="100" aria-label="Place"><button class="go" type="button" id="ev-go">Add to calendar</button><div id="ev-out"></div></div>
      <div class="tile"><b>Call or message</b><input id="nm-num" inputmode="tel" placeholder="Number, like +212600111222" aria-label="Phone number"><input id="nm-body" placeholder="Message (optional)" maxlength="300" aria-label="Message"><div class="chips"><button type="button" data-nm="call">Call</button><button type="button" data-nm="sms">Text</button><button type="button" data-nm="whatsapp">WhatsApp</button></div><div id="nm-out"></div><p class="note">A web page can't look up your contacts, so type the number.</p></div>
      <div class="tile"><b>Directions</b><input id="nv-place" placeholder="Where to?" maxlength="120" aria-label="Destination"><button class="go" type="button" id="nv-go">Navigate</button><div id="nv-out"></div></div>
      <div class="tile"><b>Search</b><input id="se-q" placeholder="Search for…" maxlength="120" aria-label="Search"><button class="go" type="button" id="se-go">Search</button><div id="se-out"></div></div></div></section>
    <section aria-label="Tasks"><h2>Tasks <span class="muted" id="tk-sync"></span></h2>
      <div class="row"><input id="tk-title" placeholder="A task…" maxlength="150" aria-label="Task"><input type="datetime-local" id="tk-when" aria-label="When (optional)" style="max-width:210px"><button class="go" type="button" id="tk-add">Add</button></div>
      <div id="tk-list" class="list" style="margin-top:10px"></div>
      <p class="note">Tasks are kept on this phone. When your PC can be reached they are sent to it, so the PC's calendar and reminders have them too.</p></section>
    <section aria-label="AI on this phone"><h2>The AI on this phone</h2>
      <p class="note">Google's <b>AI Edge Gallery</b> app runs a small AI model on the phone itself, with no internet. It is a separate app: MBUDDY can't see inside it, but you can use both.</p>
      <div class="rec">${rec.model ? `<div class="muted">Get this one</div><b class="big">${esc(rec.model.name)}</b> <span class="muted">${esc(rec.model.size)}</span>${rec.alt ? `<div class="muted">or ${esc(rec.alt.name)} (${esc(rec.alt.size)})</div>` : ""}` : `<b>No model on this phone</b>`}<p>${esc(rec.why)}</p></div>
      <p class="note">What I could read from this phone: ${native ? esc(native.model) + `, Android ${esc(native.sdk)}` : esc(navigator.userAgentData?.platform || (android ? "Android" : "not Android"))}${ramGb ? `, about ${ramGb} GB of memory` : ", memory unknown"}${native ? `, ${Math.round(native.freeStorageMb / 1024)} GB free storage` : ""}${navigator.hardwareConcurrency ? `, ${navigator.hardwareConcurrency} processor cores` : ""}${navigator.gpu ? ", graphics acceleration available" : ""}. <span id="dev-model"></span></p>
      <ol class="steps"><li>Install it from Google Play. <div id="gal-open" class="row" style="margin-top:6px"></div><span class="note">If it isn't installed, this opens its Play Store page.</span></li>
        <li>In the app, open a chat with a Gemma 4 model and press Download${rec.model ? ` for <b>${esc(rec.model.name)}</b>` : ""}. The download is large: use Wi-Fi.</li>
        <li>Optional, to let that model use MBUDDY's tools on your PC when it can be reached: in the app, add an MCP server with this address (it works like a key for this phone, so keep it private). <div class="row" style="margin-top:6px"><button type="button" id="mcp-copy"${paired ? "" : " disabled"}>Copy the address</button><button type="button" id="mcp-test"${paired ? "" : " disabled"}>Test it</button></div><div id="mcp-out" class="note"></div>${paired ? "" : `<div class="note">Pair this phone first (open MBUDDY on the phone, Away).</div>`}</li></ol>
      <label class="row" style="color:inherit"><input type="checkbox" id="gal-done" style="width:24px;min-height:24px"> I have it installed</label>
      <p class="note">${esc(rec.caution)}</p></section>
    <p class="note" style="text-align:center"><a href="./" style="color:var(--accent)">Open the whole MBUDDY app</a></p>`;
  wire();
}

function wire() {
  const out = (id, kind, params, after) => {
    const box = $(id); box.replaceChildren();
    try { const a = buildAction(kind, params); if (a) box.append(actNode(a, after)); } catch { box.textContent = "That didn't work."; }
  };
  // Tell me
  const say = async () => {
    const text = $("#say").value.trim(), box = $("#say-out");
    if (!text) return;
    box.replaceChildren();
    // Anything that works inside another app goes to the buddy when screen control is on: it reads back before it sends.
    if (agentOn() && /^(send|message|text|whatsapp|wa|reply)\b/i.test(text) || (agentOn() && AGENTY.test(text) && !parseQuick(text).length)) {
      if (startAgent(text, box)) $("#say").value = "";
      return;
    }
    const found = parseQuick(text);
    if (found.length) {
      for (const f of found) {
        if (f.kind === "task") { addTask(f.params.title, f.params.when ? localInput(f.params.when) : ""); const p = document.createElement("p"); p.className = "note good"; p.textContent = `Added to your tasks: ${f.params.title}${f.params.when ? ` (${new Date(f.params.when).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })})` : ""}.`; box.append(p); renderTasks(); syncTasks().then(renderTasks); continue; }
        const act = buildAction(f.kind, f.params);
        if (act) box.append(actNode(act));
      }
      $("#say").value = "";
      return;
    }
    if (await checkPc()) {
      const p = document.createElement("p"); p.className = "note"; p.textContent = "Asking MBUDDY on your PC…"; box.append(p);
      const r = await chatTurn(text, false, () => { p.textContent = "Still working on it on your PC…"; });
      p.className = r && r.ok ? "" : "note bad"; p.textContent = r && r.ok ? r.reply : "MBUDDY couldn't answer just now.";
      $("#say").value = ""; pcActions();
    } else {
      const p = document.createElement("p"); p.className = "note"; p.textContent = "I didn't understand that without your PC. Try “10 minute timer”, “alarm at 7:30”, “remind me to … tomorrow at 9”, “call +212…”, “directions to …”, or “search …”."; box.append(p);
    }
  };
  $("#say-go").addEventListener("click", say);
  const agentBtn = $("#say-agent");
  if (agentBtn) agentBtn.addEventListener("click", () => {
    const text = $("#say").value.trim(), box = $("#say-out");
    if (!text) { box.textContent = "Type what you want done first."; return; }
    if (startAgent(text, box)) $("#say").value = "";
  });
  $("#say").addEventListener("keydown", (e) => { if (e.key === "Enter") say(); });
  const mic = $("#say-mic");
  if (mic) mic.addEventListener("click", () => {
    const R = window.SpeechRecognition || window.webkitSpeechRecognition, rec = new R();
    rec.lang = navigator.language || "en-US"; rec.interimResults = false;
    rec.onresult = (e) => { $("#say").value = e.results[0][0].transcript; say(); };
    rec.onerror = () => { $("#say-out").textContent = "I couldn't hear that."; };
    rec.start();
  });
  // Tools
  $("#al-go").addEventListener("click", () => { const [h, m] = ($("#al-time").value || "07:00").split(":").map(Number); out("#al-out", "alarm", { hour: h, minute: m, label: $("#al-label").value.trim() }); });
  const timer = (min) => out("#tm-out", "timer", { seconds: Math.round(min * 60), label: "" });
  document.querySelectorAll("[data-tm]").forEach((b) => b.addEventListener("click", () => timer(Number(b.dataset.tm))));
  $("#tm-go").addEventListener("click", () => { const v = Number($("#tm-min").value); if (v > 0) timer(v); });
  $("#ev-go").addEventListener("click", () => { const t = $("#ev-title").value.trim() || "Event", s = $("#ev-start").value; if (!s) { $("#ev-out").textContent = "Pick when it starts."; return; } out("#ev-out", "calendar", { title: t, start: new Date(s).toISOString(), place: $("#ev-place").value.trim() }); });
  document.querySelectorAll("[data-nm]").forEach((b) => b.addEventListener("click", () => { const n = $("#nm-num").value.replace(/[^\d+]/g, ""); if (n.length < 3) { $("#nm-out").textContent = "Type a number first."; return; } out("#nm-out", b.dataset.nm, { number: n, body: $("#nm-body").value.trim() }); }));
  $("#nv-go").addEventListener("click", () => { const p = $("#nv-place").value.trim(); if (p) out("#nv-out", "navigate", { place: p }); });
  $("#se-go").addEventListener("click", () => { const q = $("#se-q").value.trim(); if (q) out("#se-out", "search", { query: q }); });
  // Tasks
  $("#tk-add").addEventListener("click", () => { const t = $("#tk-title").value.trim(); if (!t) return; addTask(t, $("#tk-when").value); $("#tk-title").value = ""; renderTasks(); syncTasks().then(renderTasks); });
  // The AI on the phone
  const gal = buildAction("open_gallery");
  $("#gal-open").append(actNode(gal, () => { store("mb_gallery", true); $("#gal-done").checked = true; }));
  $("#gal-done").checked = native ? !!native.gallery.installed : !!store("mb_gallery");
  if (native && native.gallery.installed) $("#gal-done").insertAdjacentHTML("afterend", `<span class="good">Installed (version ${esc(native.gallery.version || "?")})</span>`);
  $("#gal-done").addEventListener("change", (e) => store("mb_gallery", e.target.checked));
  if (navigator.userAgentData?.getHighEntropyValues) navigator.userAgentData.getHighEntropyValues(["model"]).then((v) => { if (v.model) $("#dev-model").textContent = `Phone: ${v.model}.`; }).catch(() => {});
  const mcp = () => (window.Native ? window.Native.mcpAddress() : `${location.origin}/mcp/${token()}`);
  $("#mcp-copy").addEventListener("click", async () => { try { await navigator.clipboard.writeText(mcp()); $("#mcp-out").textContent = "Copied."; } catch { $("#mcp-out").innerHTML = `Press and hold to copy: <code>${esc(mcp())}</code>`; } });
  $("#mcp-test").addEventListener("click", async () => {
    $("#mcp-out").textContent = "Asking your PC…";
    try {
      const r = await fetch(window.Native ? "/mcp/test" : mcp(), {   // in the app the path goes through the app, which puts the real token in
       method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }) });
      const j = await r.json();
      $("#mcp-out").textContent = r.ok && j.result ? `Working: ${j.result.tools.length} tools are offered (${j.result.tools.map((t) => t.name).join(", ")}).` : r.status === 503 ? "Your PC isn't reachable from here right now." : r.status === 401 || r.status === 404 ? "Your PC doesn't know this phone: pair it again." : `Not working (${r.status}).`;
    } catch { $("#mcp-out").textContent = "Your PC isn't reachable from here right now."; }
  });
  renderTasks();
}

function renderTasks() {
  const list = tasks().slice().reverse(), waiting = list.filter((t) => !t.sent).length;
  const sync = $("#tk-sync"); if (sync) sync.textContent = waiting ? `· ${waiting} waiting to reach the PC` : list.length ? "· all on the PC" : "";
  const box = $("#tk-list"); if (!box) return;
  box.innerHTML = list.length ? list.slice(0, 20).map((t) => `<div class="item"><div>${esc(t.title)}${t.when ? `<small>${esc(t.when.replace("T", " "))}</small>` : ""}</div><div><span class="note">${t.sent ? "on the PC" : "on this phone"}</span> <button type="button" data-rm="${esc(t.id)}" aria-label="Remove ${esc(t.title)}">✕</button></div></div>`).join("") : `<p class="note">No tasks yet.</p>`;
  box.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => { saveTasks(tasks().filter((t) => t.id !== b.dataset.rm)); renderTasks(); }));
}

// What MBUDDY asked this phone to do (its brain, or the PC, put these on a list): each is a link to tap.
async function pcActions() {
  const sec = $("#from-pc");
  if (!online || !sec) return;
  const r = await api.get("/ui/phone-actions").catch(() => null);
  const list = (r && r.ok && r.actions) || [];
  sec.hidden = !list.length;
  const box = $("#pc-list"); box.replaceChildren();
  for (const a of list) {
    const act = buildAction(a.kind, a.params);
    if (!act) continue;
    const row = document.createElement("div"); row.className = "item"; row.style.display = "grid";
    row.append(actNode(act, () => { api.post("/ui/phone-actions", { id: a.id, status: "done" }).catch(() => {}); setTimeout(pcActions, 800); }));
    const x = document.createElement("button"); x.type = "button"; x.textContent = "Not now";
    x.addEventListener("click", () => { api.post("/ui/phone-actions", { id: a.id, status: "dismissed" }).catch(() => {}); row.remove(); });
    row.append(x); box.append(row);
  }
}

async function syncBuddy() {
  // The card the app shows over other apps uses the buddy chosen on the PC.
  if (!window.Native || !window.Native.setCharacter || !online) return;
  const r = await api.get("/ui/settings").catch(() => null);
  const c = r && r.ok && r.settings && r.settings.character;
  if (c) window.Native.setCharacter(String(c));
}
render();
renderCtl();
setInterval(renderCtl, 2500);
checkPc().then(() => { syncTasks().then(renderTasks); pcActions(); syncBuddy(); });
setInterval(async () => { await checkPc(); if (online) { pcActions(); syncTasks().then(renderTasks); } }, 8000);
window.addEventListener("online", () => checkPc().then(() => { syncTasks().then(renderTasks); pcActions(); }));
