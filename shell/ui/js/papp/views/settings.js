// Settings: a short list of groups, each opening its own page, so nothing is shown until it is wanted. Buddy (who, voice, motion),
// Brain (which one, the cloud key, the model on this phone), Calling me (assistant button, "hey buddy"), Work in other apps (screen
// control), While I work (bar or island), Your PC, On this phone.
import { esc } from "../util.js";
import { charPicker } from "./shared.js";
import { CAST_INFO, castFor, stillUrl } from "../../buddy/sprite.js";

const N = () => window.Native || null;
const call = (fn, fallback) => { try { return N() && N()[fn] ? N()[fn]() : fallback; } catch { return fallback; } };
const agent = () => { try { return N() && N().agentStatus ? JSON.parse(N().agentStatus()) : null; } catch { return null; } };
const wake = () => { try { return N() && N().wakeState ? JSON.parse(N().wakeState()) : null; } catch { return null; } };
const CHEV = `<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>`;
const ic = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
const ICONS = {
  smart: ic("M12 3l1.8 4.6L18.5 9l-4.7 1.4L12 15l-1.8-4.6L5.5 9l4.7-1.4zM18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z"),
  permissions: ic("M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.500-7-10V6zM9 12l2 2 4-4"),
  brain: ic("M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 3 4 3 3 0 0 0 5 1V5a3 3 0 0 0-3-1zM15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-3 4 3 3 0 0 1-5 1"),
  calling: ic("M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM5 11a7 7 0 0 0 14 0M12 18v3"),
  control: ic("M8 3h8a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM11 18h2"),
  style: ic("M4 7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3zM9 4.5h6"),
  pc: ic("M3 5h18v11H3zM8 20h8M12 16v4"),
  data: ic("M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"),
};

let timers = [];
const every = (fn, ms) => { fn(); timers.push(setInterval(fn, ms)); };

export default {
  render(el, ctx, sub = []) {
    timers.forEach(clearInterval); timers = [];
    const page = sub[0] || "";
    if (!page) return main(el, ctx);
    const pages = { buddy: buddyPage, brain: brainPage, calling: callingPage, control: controlPage, style: stylePage, pc: pcPage, data: dataPage, permissions: permissionsPage, smart: smartPage };
    const title = { buddy: "Your buddy", brain: "Brain", calling: "Calling me", control: "Work in other apps", style: "Card style", pc: "Your PC", data: "Your data", permissions: "Permissions", smart: "Smart behaviours" }[page];
    if (!pages[page]) return main(el, ctx);
    el.innerHTML = `<header class="pa-subhead"><a class="pa-back" href="#/settings" aria-label="Back to Settings"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg><span>Settings</span></a>
      <h1>${esc(title)}</h1></header><div class="pa-page" id="pg"></div>`;
    pages[page](el.querySelector("#pg"), ctx);
  },
  destroy() { timers.forEach(clearInterval); timers = []; },
};

// ---- smart behaviours: what the buddy does on its own, none of it with the microphone ----
// a long page becomes short: each section folds under its own title, the first one open
function foldSections(el) {
  [...el.querySelectorAll(":scope > .pa-sec")].forEach((sec, i) => {
    const h = sec.querySelector(":scope > h2"); if (!h) return;
    const d = document.createElement("details"); d.className = "pa-fold"; if (i === 0) d.open = true;
    const s = document.createElement("summary"); s.textContent = h.textContent; h.remove();
    sec.replaceWith(d); d.append(s, sec);
  });
}
function smartPage(el) {
  el.innerHTML = `<p class="pa-lead">Ways the buddy reaches you first, or that you reach it without opening the app. Each one is off until you turn it on, and the microphone only opens when you tap the handle (and you can turn even that off).</p>`;
  // privacy mode first: it is the switch for the moments that matter most
  const pv = (() => { try { return N() && N().privacyState ? JSON.parse(N().privacyState()) : null; } catch { return null; } })();
  if (pv) {
    const sec = document.createElement("section"); sec.className = "pa-sec pa-privacy";
    const left = pv.on && pv.until ? Math.max(0, Math.round((pv.until - Date.now()) / 60000)) : 0;
    sec.innerHTML = `<h2>Privacy mode</h2>
      <label class="pa-switch"><span>Privacy mode<small>For when you are doing something private.${pv.on ? (pv.until ? ` On for about ${left} more minute${left === 1 ? "" : "s"}.` : " On until you turn it off.") : ""}</small></span><input type="checkbox" id="s-priv" ${pv.on ? "checked" : ""}><i></i></label>
      <select class="pa-field" id="s-priv-for" aria-label="How long"><option value="0">Until I turn it off</option><option value="15">For 15 minutes</option><option value="30">For 30 minutes</option><option value="60">For an hour</option></select>
      <p class="pa-note" id="s-priv-android"></p>
      <p class="pa-note"><b>While it is on I do not:</b> look at the screen (so no task can run in another app), read other apps' notifications (no message cards), show anything over your apps (no card, no edge buddy) or listen for a request.</p>
      <p class="pa-note"><b>It does not:</b> switch off Android's own screen-control permission (that stays yours, in Android settings), or silence your alarms, timers and notifications. Turn it off from the notification, the Privacy tile in your quick settings, or here. You can also say "privacy mode on".</p>`;
    el.appendChild(sec);
    const apply = () => { try { N().setPrivacy(sec.querySelector("#s-priv").checked, Number(sec.querySelector("#s-priv-for").value)); } catch { /* not in the app */ } };
    sec.querySelector("#s-priv").addEventListener("change", () => { apply(); setTimeout(() => location.reload(), 250); });
    sec.querySelector("#s-priv-for").addEventListener("change", () => { if (sec.querySelector("#s-priv").checked) { apply(); setTimeout(() => location.reload(), 250); } });
    // Android's own screen-control switch is a different setting from this one: say what each is, side by side, so they cannot be mixed up
    const androidLine = () => {
      try {
        const a = JSON.parse(N().agentStatus()), p = JSON.parse(N().privacyState()), line = sec.querySelector("#s-priv-android");
        if (!line) return;
        line.innerHTML = `<b>Android's screen-control switch</b> (Accessibility settings): <b>${a.enabled ? "on" : "off"}</b>. <b>Privacy mode</b>: <b>${p.on ? "on" : "off"}</b>. ` +
          (p.on && a.enabled ? "Android's switch stays on because only you can change it there; while privacy mode is on I do not use it, so nothing can be seen or done."
            : p.on ? "Android's switch is off, so nothing could look at the screen anyway."
            : a.enabled ? "Privacy mode is off, so I can use screen control for tasks you give me." : "Screen control is off in Android, so I cannot look at the screen.");
      } catch { /* not in the app */ }
    };
    androidLine();
    // the mode can be changed from outside this page (the tile, the notification, by voice): the switch follows the real state
    const follow = setInterval(() => {
      if (!sec.isConnected) { clearInterval(follow); return; }
      try {
        const now = JSON.parse(N().privacyState()), box = sec.querySelector("#s-priv");
        if (box && box.checked !== !!now.on) { box.checked = !!now.on; setTimeout(() => location.reload(), 150); }
        androidLine();
      } catch { /* not in the app */ }
    }, 1500);
  }
  const hs = (() => { try { return N() && N().handleState ? JSON.parse(N().handleState()) : null; } catch { return null; } })();
  const sm = (() => { try { return N() && N().smartState ? JSON.parse(N().smartState()) : null; } catch { return null; } })();
  if (hs) {
    const sec = document.createElement("section"); sec.className = "pa-sec";
    sec.innerHTML = `<h2>Edge handle</h2><label class="pa-switch"><span>Keep a small buddy at the edge of the screen</span><input type="checkbox" id="s-handle" ${hs.on ? "checked" : ""}><i></i></label>
      <div class="pa-seg small" id="s-hside" role="radiogroup" aria-label="Edge">${[["right", "Right edge"], ["left", "Left edge"]].map(([v, l]) => `<button type="button" role="radio" data-side="${v}" aria-checked="${hs.side === v}">${l}</button>`).join("")}</div>
      <label class="pa-switch"><span>Listen as soon as the panel opens<small>Tap the buddy and it is already listening; the tiles are still there to tap instead. Off: it waits until you tap Talk.</small></span><input type="checkbox" id="s-plisten" ${hs.panelListen === false ? "" : "checked"}><i></i></label>
      <p class="pa-note">Tap it for what you use most, or to talk. Drag it up or down, or to the other edge. It works over every app. The microphone opens only when you tap it, and only if the switch above is on.${hs.screenControl ? "" : " It needs screen control turned on in Android's Accessibility settings."}</p>`;
    el.appendChild(sec);
    const save = () => N().setHandle(sec.querySelector("#s-handle").checked, (sec.querySelector("[data-side][aria-checked=true]") || { dataset: { side: "right" } }).dataset.side);
    sec.querySelector("#s-handle").addEventListener("change", save);
    sec.querySelector("#s-plisten").addEventListener("change", (e) => { try { N().setPanelListen(e.target.checked); } catch { /* not in the app */ } });
    sec.querySelectorAll("[data-side]").forEach((b) => b.addEventListener("click", () => { sec.querySelectorAll("[data-side]").forEach((x) => x.setAttribute("aria-checked", String(x === b))); save(); }));
  }
  if (sm) {
    const sec = document.createElement("section"); sec.className = "pa-sec";
    sec.innerHTML = `<h2>Heads-up cards</h2>
      <label class="pa-switch"><span>Ten minutes before an event</span><input type="checkbox" data-k="smart_events" ${sm.events ? "checked" : ""}><i></i></label>
      <label class="pa-switch"><span>A task at its time</span><input type="checkbox" data-k="smart_tasks" ${sm.tasks ? "checked" : ""}><i></i></label>
      <label class="pa-switch"><span>A morning briefing at ${String(sm.hour).padStart(2, "0")}:00</span><input type="checkbox" data-k="smart_brief" ${sm.brief ? "checked" : ""}><i></i></label>
      <select class="pa-field" id="s-hour" aria-label="Briefing hour">${[6, 7, 8, 9, 10].map((h) => `<option value="${h}" ${h === sm.hour ? "selected" : ""}>${String(h).padStart(2, "0")}:00</option>`).join("")}</select>
      <p class="pa-note">These come from your own agenda and need no microphone. They arrive as a notification, and as a card over what you are doing when the screen is on and screen control is on.</p>`;
    el.appendChild(sec);
    const apply = (k, on) => N().setSmart(k, on, Number(sec.querySelector("#s-hour").value));
    sec.querySelectorAll("[data-k]").forEach((i) => i.addEventListener("change", () => apply(i.dataset.k, i.checked)));
    sec.querySelector("#s-hour").addEventListener("change", () => apply("smart_brief", sec.querySelector('[data-k="smart_brief"]').checked));
  }
  const tr = (() => { try { return N() && N().triggersState ? JSON.parse(N().triggersState()) : null; } catch { return null; } })();
  if (tr) {
    const sec = document.createElement("section"); sec.className = "pa-sec";
    sec.innerHTML = `<h2>When a message arrives</h2><label class="pa-switch"><span>Offer to reply by voice</span><input type="checkbox" id="s-msg" ${tr.msgCards ? "checked" : ""}><i></i></label>
      <p class="pa-note">A card with the sender and the message when someone writes on WhatsApp or SMS (one person, not groups), with a button to reply by voice. It reads the notification only to make that card: nothing is saved or sent anywhere, and a reply always asks before it is sent.${tr.access ? "" : " It needs notification access, which Android asks you to give yourself."}</p>
      ${tr.access ? "" : `<div class="pa-chips"><button class="pa-btn" id="s-acc" type="button">Give notification access</button></div>`}
      <h2 style="margin-top:14px">In the car</h2><input class="pa-field" id="s-car" placeholder="Your car's Bluetooth name, or part of it" value="${esc(tr.car || "")}" maxlength="40">
      <p class="pa-note">When your phone connects to it, a card offers Maps and music. Leave empty to turn it off.</p>`;
    el.appendChild(sec);
    sec.querySelector("#s-msg").addEventListener("change", (e) => N().setMsgCards(e.target.checked));
    const acc = sec.querySelector("#s-acc"); if (acc) acc.addEventListener("click", () => N().openNotificationAccess());
    sec.querySelector("#s-car").addEventListener("change", (e) => N().setCar(e.target.value));
  }
  if (!el.querySelector(".pa-sec")) el.insertAdjacentHTML("beforeend", `<p class="pa-note">These work in the phone app.</p>`);
  foldSections(el);
}

// ---- permissions: each one, what it does, and whether it is allowed ----
const PERMS = {
  mic: ["Microphone", "Lets me hear you when you tap the mic, call me with the assistant button, or say “hey buddy” if you turned that on. It is never open in the background unless you switch “hey buddy” on."],
  notifications: ["Notifications", "Lets me show reminders, heads-up cards before events, timers, and the “tap to do it” buttons for things I cannot start from the background."],
  contacts: ["Contacts", "Lets me find who “Baba” is when you say call or message. Names stay on this phone."],
  calendar: ["Calendar", "Lets me put an event straight into your calendar when you ask."],
  screen: ["Screen control", "Lets me see the screen and tap and type in other apps while a task you gave me is running. It also draws my card over other apps, the edge handle and the volume-keys shortcut. I never operate banking or password apps, and I always ask before sending, paying, deleting or calling."],
  overlay: ["Display over other apps", "Lets me open another app's screen when I am not in front, for example opening WhatsApp from a voice command."],
  assistant: ["Assistant button", "Makes MBUDDY what opens when you hold the power button, with no microphone open until you do. Optional."],
  notifaccess: ["Notification access", "Only for message cards: reads who wrote and what, to offer a voice reply. Nothing is saved or sent. Optional."],
  bluetooth: ["Bluetooth", "Only for the car card: tells me when your car's Bluetooth connects. Optional."],
};
const smartVal = () => {
  try {
    const hs = N() && N().handleState ? JSON.parse(N().handleState()) : null, sm = N() && N().smartState ? JSON.parse(N().smartState()) : null, tr = N() && N().triggersState ? JSON.parse(N().triggersState()) : null;
    const on = [hs && hs.on, sm && sm.events, sm && sm.tasks, sm && sm.brief, tr && tr.msgCards, tr && tr.car].filter(Boolean).length;
    return on ? `${on} on` : "All off";
  } catch { return ""; }
};
const permList = () => { try { return N() && N().permissionsState ? JSON.parse(N().permissionsState()) : []; } catch { return []; } };
const permVal = () => { const l = permList(); return l.length ? `${l.filter((p) => p.granted).length} of ${l.length} allowed` : ""; };
let permTimer = null;
function permissionsPage(el) {
  clearInterval(permTimer);
  let last = "";
  const draw = () => {
    const list = permList(), priv = (() => { try { return !!(N() && N().privacyState && JSON.parse(N().privacyState()).on); } catch { return false; } })();
    const key = JSON.stringify([list, priv]); if (key === last) return; last = key;
    if (!list.length) { el.innerHTML = `<p class="pa-note">Permissions are managed in the phone app.</p>`; return; }
    el.innerHTML = `<p class="pa-lead">What I am allowed to do, and why. Nothing here is on without you.</p>
      <ul class="pa-perms">${list.map((p) => { const [name, what] = PERMS[p.id] || [p.id, ""];
        return `<li class="pa-perm${p.granted ? " on" : ""}"><div class="pa-perm-h"><b>${esc(name)}</b>${p.optional ? `<span class="pa-opt">optional</span>` : ""}<span class="pa-state-chip">${p.granted ? (priv && (p.id === "screen" || p.id === "overlay" || p.id === "notifaccess") ? "Allowed in Android · paused by privacy mode" : "Allowed") : "Not allowed"}</span></div>
          <p>${esc(what)}</p>
          <div class="pa-chips" style="margin:6px 0 0"><button class="pa-btn${p.granted ? "" : " go"}" type="button" data-perm="${p.id}">${p.granted ? "Manage" : "Allow"}</button></div></li>`; }).join("")}</ul>
      <p class="pa-note">If a button does nothing, Android has stopped asking. Open MBUDDY's page in Android settings and allow it there.</p>
      <div class="pa-chips"><button class="pa-btn" type="button" data-perm="app">Open Android settings for MBUDDY</button></div>`;
    el.querySelectorAll("[data-perm]").forEach((b) => b.addEventListener("click", () => { try { N().requestPermission(b.dataset.perm); } catch { /* not in the app */ } }));
  };
  draw();
  permTimer = setInterval(draw, 1500); timers.push(permTimer);
}

// ---- the list ----
function main(el, ctx) {
  const { store, brain } = ctx;
  const draw = () => {
    const s = store.settings.get(), bs = brain.brainState(), a = agent(), w = wake();
    const name = CAST_INFO[castFor(s.character)][0];
    const brainVal = !(bs.cloud || bs.pc || bs.phone) ? `<span class="warn">No brain yet</span>`
      : `${{ auto: "Auto", cloud: "Cloud", pc: "My PC", phone: "This phone" }[s.planner] || "Auto"} · ${bs.cloud ? "cloud ready" : bs.pc ? "PC" : "phone model"}`;
    const callVal = call("isAssistant", false) ? "Assistant button" : w && w.enabled ? "Hey buddy" : "Off";
    const ctlVal = a ? (a.enabled && a.connected ? "On" : `<span class="warn">Off</span>`) : "In the phone app";
    const styleVal = N() && N().overlayStyle ? `${N().overlayStyle() === "island" ? "Island" : "Bar"}${call("colorfulLight", true) ? " · colourful" : ""}` : "";
    const row = (id, label, val) => `<li><a class="pa-group" href="#/settings/${id}"><span class="gi">${ICONS[id]}</span><span class="gl">${label}</span><span class="gv">${val}</span>${CHEV}</a></li>`;
    el.innerHTML = `<header class="pa-head"><h1>Settings</h1></header>
      <a class="pa-profile" href="#/settings/buddy"><img src="${stillUrl(castFor(s.character))}" alt="" draggable="false">
        <span class="grow"><b>${esc(s.user_name || "You")}</b><span>${esc(name)} · ${s.speak_replies ? "talks out loud" : "quiet"}</span></span>${CHEV}</a>
      <ul class="pa-groups">${row("brain", "Brain", brainVal)}${row("calling", "Calling me", callVal)}${row("control", "Work in other apps", ctlVal)}</ul>
      <ul class="pa-groups">${row("style", "Card style", styleVal)}${row("pc", "Your PC", bs.pc ? "Paired" : "Not paired")}${row("data", "Your data", "Export or delete")}</ul>
      <ul class="pa-groups">${row("smart", "Smart behaviours", smartVal())}${row("permissions", "Permissions", permVal())}</ul>`;
  };
  // redrawn only when what it shows has changed (redrawing replays the entrance and looked like the page refreshing)
  let last = "";
  const state = () => JSON.stringify([store.settings.get(), brain.brainState(), agent(), wake(), call("isAssistant", false), call("overlayStyle", ""), call("colorfulLight", true)]);
  last = state(); draw();
  every(() => { const now = state(); if (now === last) return; last = now; el.querySelectorAll("*").forEach((n) => { n.style.animation = "none"; }); draw(); el.querySelectorAll("*").forEach((n) => { n.style.animation = "none"; }); }, 2000);
}

// ---- pages ----
function voiceSection(el) {
  if (!(N() && N().voicesState)) return () => {};
  const sec = document.createElement("section"); sec.className = "pa-sec"; el.appendChild(sec);
  let tries = 0;
  const draw = () => {
    let st = null; try { st = JSON.parse(N().voicesState()); } catch { return; }
    const list = st.voices || [], who = esc(st.character);
    if (!list.length && tries++ < 10) setTimeout(draw, 700);       // the speech engine lists its voices a moment after it starts
    if (!list.length && tries <= 10) { sec.innerHTML = `<h2>Its voice</h2><div class="pa-skel" aria-label="Looking for voices"><i></i><i></i><i></i></div>`; return; }
    sec.innerHTML = `<h2>Its voice</h2>
      <p class="pa-note">${who} already has its own pitch and pace${list.length > 1 ? " and a voice of its own" : ""}. Listen to the voices on this phone and give ${who} the one you like.</p>
      <div class="pa-voices">
        <div class="pa-voice${st.chosen ? "" : " on"}"><span class="grow"><b>Automatic</b><small>a voice chosen for ${who}</small></span><button class="pa-btn" data-use="" type="button">${st.chosen ? "Use" : "In use"}</button><button class="pa-btn" id="v-try" type="button">Hear</button></div>
        ${list.map((v) => `<div class="pa-voice${v.name === st.chosen ? " on" : ""}"><span class="grow"><b>${esc(v.label)}</b></span><button class="pa-btn" data-use="${esc(v.name)}" type="button">${v.name === st.chosen ? "In use" : "Use"}</button><button class="pa-btn" data-hear="${esc(v.name)}" type="button">Hear</button></div>`).join("")}
      </div>
      <div class="pa-chips"><button class="pa-btn go" id="v-more" type="button">Get more voices</button></div>
      <p class="pa-note">More voices are downloaded by the phone's speech engine (Google, or the one you chose in Android's text-to-speech settings). Come back here afterwards and they will be in the list.</p>`;
    sec.querySelector("#v-try").addEventListener("click", () => N().previewVoice());
    sec.querySelector("#v-more").addEventListener("click", () => N().getMoreVoices());
    sec.querySelectorAll("[data-hear]").forEach((b) => b.addEventListener("click", () => N().auditionVoice(b.dataset.hear)));
    sec.querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", () => { N().setBuddyVoice(b.dataset.use); N().previewVoice(); setTimeout(draw, 150); }));
  };
  setTimeout(draw, 400);     // the speech engine needs a moment to list its voices
  document.addEventListener("visibilitychange", () => { if (!document.hidden) setTimeout(draw, 600); });      // back from downloading voices
  return draw;
}
function buddyPage(el, ctx) {
  const { store } = ctx, s = store.settings.get();
  el.innerHTML = `<section class="pa-sec"><label class="pa-label" for="s-name">What should I call you?</label><input class="pa-field" id="s-name" maxlength="40" placeholder="Your name" value="${esc(s.user_name)}"></section>
    <section class="pa-sec"><h2>Who I am</h2><div id="s-chars"></div></section>
    <section class="pa-sec"><label class="pa-switch"><span>Say my answers out loud</span><input type="checkbox" id="s-speak" ${s.speak_replies ? "checked" : ""}><i></i></label>
      <label class="pa-switch"><span>Calmer motion</span><input type="checkbox" id="s-motion" ${s.reduced_motion ? "checked" : ""}><i></i></label></section>
    <section class="pa-sec" id="s-engine-sec" hidden><h2>How I listen</h2>
      <select class="pa-field" id="s-engine" aria-label="Speech engine"><option value="auto">Automatic</option><option value="online">Google, online (needs the internet)</option><option value="boosted">Google, with my voice made louder (quiet microphones)</option><option value="device">On this phone only</option></select>
      <p class="pa-note">If I keep missing what you say, try the other one: phones differ in which hears better.</p></section>`;
  const $ = (id) => el.querySelector("#" + id);
  $("s-name").addEventListener("change", (e) => store.settings.set({ user_name: e.target.value.trim() }));
  const redrawVoice = voiceSection(el);
  charPicker($("s-chars"), s.character, (c) => { store.settings.set({ character: c }); try { N() && N().setCharacter(c); } catch { /* not in the app */ } setTimeout(redrawVoice, 250); });
  $("s-motion").addEventListener("change", (e) => store.settings.set({ reduced_motion: e.target.checked }));
  try { if (N() && N().voiceEngine) { $("s-engine-sec").hidden = false; $("s-engine").value = N().voiceEngine(); $("s-engine").addEventListener("change", (e) => N().setVoiceEngine(e.target.value)); } } catch { /* not in the app */ }
  $("s-speak").addEventListener("change", (e) => { store.settings.set({ speak_replies: e.target.checked }); try { N() && N().setVoice(e.target.checked); } catch { /* fine */ } });
}

function brainPage(el, ctx) {
  const { store, brain } = ctx, s = store.settings.get(), bs = brain.brainState();
  const inUse = ((brain.brainHealth() || {}).order || [])[0] || (bs.cloud ? "cloud" : bs.pc ? "pc" : bs.phone ? "phone" : "");
  el.innerHTML = `<section class="pa-sec"><p class="pa-lead ${inUse === "cloud" ? "good" : ""}">${{ cloud: "Using the cloud brain. Your key stays on this phone.", pc: "Using your PC's brain. A cloud key lets me think without the PC.", phone: "Using the model on this phone. A cloud key makes me much quicker and smarter at tasks." }[inUse] || "I have no brain yet. Paste your cloud key, or download the model below."}</p></section>
    <section class="pa-sec"><h2>Which brain thinks</h2><div class="pa-seg small" role="radiogroup" aria-label="Which brain">${[["auto", "Auto"], ["cloud", "Cloud"], ["pc", "My PC"], ["phone", "This phone"]].map(([v, l]) => `<button type="button" role="radio" data-pl="${v}" aria-checked="${s.planner === v}" aria-selected="${s.planner === v}">${l}</button>`).join("")}<i class="pa-seg-mark" aria-hidden="true"></i></div>
      <p class="pa-note">Auto: the cloud if you have a key, else your PC if it answers, else this phone. Pick one to make me use only that.</p></section>
    <section class="pa-sec"><h2>How each one is doing</h2><ul class="pa-brains" id="s-health"></ul>
      <button class="pa-btn" id="s-test" type="button">Test my brains</button></section>
    <details class="pa-fold"><summary>Set up the cloud brain</summary><section class="pa-sec"><label class="pa-switch"><span>Share my tasks, events and notes with the cloud brain<small>Off: it only sees what you say, never your lists.</small></span><input type="checkbox" id="s-share" ${s.cloud_share ? "checked" : ""}><i></i></label><h2>Ollama Cloud key</h2><p class="pa-note">Make one at ollama.com/settings/keys. Keys from other providers are refused.</p><input class="pa-field" id="s-key" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${bs.cloud ? "Replace the saved key" : "Paste your Ollama Cloud key"}" aria-label="Ollama Cloud key">
      <div class="pa-two"><button class="pa-btn go" id="s-key-save" type="button">Save key</button><button class="pa-btn" id="s-key-del" type="button" ${bs.cloud ? "" : "disabled"}>Remove key</button></div>
      <label class="pa-label" for="s-model">Cloud model</label><select class="pa-field" id="s-model"><option value="${esc(s.cloud_model)}">${esc(s.cloud_model)}</option></select>
      <p class="pa-note" id="s-key-out"></p></section></details>
    
    <details class="pa-fold"><summary>Set up the model on this phone</summary><section class="pa-sec"><div id="s-lm"></div></section></details>`;
  const $ = (id) => el.querySelector("#" + id);
  $("s-share").addEventListener("change", (e) => { store.settings.set({ cloud_share: e.target.checked }); brain.syncToApp(); });
  const seg = el.querySelector(".pa-seg"), mark = seg.querySelector(".pa-seg-mark");
  const place = () => { const b = seg.querySelector('[aria-checked="true"]') || seg.querySelector("button"); mark.style.transform = `translateX(${b.offsetLeft}px)`; mark.style.width = b.offsetWidth + "px"; };
  requestAnimationFrame(place);
  seg.querySelectorAll("[data-pl]").forEach((b) => b.addEventListener("click", () => {
    store.settings.set({ planner: b.dataset.pl });
    try { N() && N().setPlanner(b.dataset.pl); } catch { /* the sync does it shortly */ }
    health();
    seg.querySelectorAll("[data-pl]").forEach((x) => { x.setAttribute("aria-checked", String(x === b)); x.setAttribute("aria-selected", String(x === b)); });
    place();
  }));
  // each brain: set up or not, how it last did and how fast; the test asks each one a tiny question
  const NAMES = { cloud: ["Cloud", "your key, on ollama.com"], pc: ["Your PC", "MBUDDY on your computer"], phone: ["This phone", "the model downloaded here"] };
  const usage = (u) => {
    if (!u || (!u.total && !u.totalFail)) return "No answers yet";
    return `Today ${u.today} answer${u.today === 1 ? "" : "s"}${u.todayFail ? `, ${u.todayFail} failed` : ""} · ${u.total} in all${u.avgMs ? ` · about ${(u.avgMs / 1000).toFixed(1)} s each` : ""}`;
  };
  const health = (test = null) => {
    const h = brain.brainHealth(), box = $("s-health"); if (!box) return;
    if (!h) { box.innerHTML = `<li class="pa-note">Works in the phone app.</li>`; return; }
    box.innerHTML = ["cloud", "pc", "phone"].map((n) => {
      const x = (test && test[n]) || h[n] || {}, inUse = (h.order || [])[0] === n;
      const st = !x.ready ? ["off", "Not set up"] : x.ok === true ? ["ok", `Ready · ${(x.ms / 1000).toFixed(1)} s`] : x.ok === false ? ["bad", x.why || "Didn't answer"] : ["idle", "Not tried yet"];
      return `<li class="pa-brain" data-st="${st[0]}"><i></i><span class="grow"><b>${NAMES[n][0]}${inUse ? ` <em>in use</em>` : ""}</b><span>${esc(st[1])}</span><span class="pa-usage">${esc(usage(x.usage))}</span></span></li>`;
    }).join("");
  };
  health();
  every(() => health(), 3000);
  $("s-test").addEventListener("click", async () => {
    const b = $("s-test"); b.disabled = true; b.textContent = "Testing…";
    $("s-health").querySelectorAll(".pa-brain").forEach((li) => { if (li.dataset.st !== "off") li.dataset.st = "testing"; });
    let r = null; try { r = await (await fetch("/native/brains-test", { method: "POST", body: "{}" })).json(); } catch { /* below */ }
    b.disabled = false; b.textContent = "Test my brains";
    health(r);
  });
  const out = $("s-key-out");
  $("s-key-save").addEventListener("click", async () => {
    const key = $("s-key").value.trim();
    if (!key) { out.textContent = "Paste the key first."; out.className = "pa-note bad"; return; }
    out.textContent = "Checking it with the cloud…"; out.className = "pa-note";
    let r = null; try { r = await (await fetch("/cloud/key", { method: "POST", body: JSON.stringify({ key }) })).json(); } catch { /* below */ }
    if (r && r.ok) { $("s-key").value = ""; out.textContent = "Saved. I'm ready."; out.className = "pa-note good"; ctx.emit(); setTimeout(() => location.reload(), 700); }
    else { out.textContent = (r && r.error) || "I couldn't check that key."; out.className = "pa-note bad"; }
  });
  $("s-key-del").addEventListener("click", () => { try { N() && N().removeCloudKey(); } catch { /* fine */ } ctx.toast("Key removed."); location.reload(); });
  $("s-model").addEventListener("change", (e) => store.settings.set({ cloud_model: e.target.value }));
  if (bs.cloud) fetch("/cloud/models").then((r) => r.json()).then((j) => {
    const names = ((j && j.models) || []).map((m) => m.name || m.model).filter(Boolean).sort();
    if (!names.length) return;
    const cur = store.settings.get().cloud_model;
    $("s-model").innerHTML = (names.includes(cur) ? names : [cur, ...names]).map((n) => `<option value="${esc(n)}" ${n === cur ? "selected" : ""}>${esc(n)}</option>`).join("");
  }).catch(() => { /* the saved model stays */ });
  lmCard(ctx, $("s-lm"));
}

function callingPage(el, ctx) {
  el.innerHTML = `<section class="pa-sec"><h2>The assistant button</h2><div id="s-assist"></div></section>
    <section class="pa-sec"><h2>“Hey buddy”</h2><div id="s-wake"></div></section>`;
  assistCard(el.querySelector("#s-assist"));
  const keys = el.querySelector("#s-assist").parentElement.parentElement;      // the page, not the section that holds the assistant card
  setTimeout(() => foldSections(el), 0);
  if (N() && N().chordOn) {
    const sec = document.createElement("section"); sec.className = "pa-sec";
    sec.innerHTML = `<h2>Both volume keys</h2><label class="pa-switch"><span>Press volume up and down together to call me</span><input type="checkbox" id="s-chord" ${N().chordOn() ? "checked" : ""}><i></i></label>
      <p class="pa-note">Works with the app closed and the screen on, and no microphone is open until you do it. It needs MBUDDY's screen control turned on in Android's Accessibility settings, which is how the two keys are noticed. The keys are read for nothing else. If MBUDDY is your assistant (above), this opens the same session as holding the power button.</p>`;
    keys.insertBefore(sec, el.querySelector("#s-assist").parentElement.nextSibling);      // after the assistant button section
    sec.querySelector("#s-chord").addEventListener("change", (e) => N().setChord(e.target.checked));
  }
  wakeCard(ctx, el.querySelector("#s-wake"));
}

function controlPage(el, ctx) {
  let lastKey = "";
  every(() => {
    const s = agent();
    if (!s) { el.innerHTML = `<section class="pa-sec"><p class="pa-note">Working inside other apps is available in the phone app.</p></section>`; return; }
    const key = JSON.stringify([s.enabled, s.connected, s.running, s.voice, s.hear]);
    if (key === lastKey) return; lastKey = key;
    const on = s.enabled && s.connected;
    el.innerHTML = `<section class="pa-sec"><p class="pa-lead ${on ? "good" : ""}">${on ? `Screen control is on.${s.running ? " I'm working on something now." : ""}` : "Screen control is off."}</p>
        <p class="pa-note">I open apps, type and tap for you, one step at a time, with my card on top. I read back what I wrote and wait for your yes before I send, pay, delete, post or call, and I never touch banking, payment or password apps.</p>
        ${on ? "" : `<button class="pa-btn go" id="c-on" type="button">Turn it on in Android</button>`}
        ${s.running ? `<button class="pa-btn danger" id="c-stop" type="button">Stop what I'm doing</button>` : ""}</section>
      <section class="pa-sec"><label class="pa-switch"><span>Talk and listen while I work${s.hear ? "" : " (needs the microphone)"}</span><input type="checkbox" id="c-voice" ${s.voice ? "checked" : ""}><i></i></label></section>`;
    const b = el.querySelector("#c-on"); if (b) b.addEventListener("click", () => N().openControlSettings());
    const st = el.querySelector("#c-stop"); if (st) st.addEventListener("click", () => { N().agentStop(); lastKey = ""; });
    el.querySelector("#c-voice").addEventListener("change", (e) => { N().setVoice(e.target.checked); ctx.store.settings.set({ speak_replies: e.target.checked }); lastKey = ""; });
  }, 1500);
}

function stylePage(el, ctx) { el.innerHTML = `<section class="pa-sec" id="s-style"></section>`; styleCard(ctx, el.querySelector("#s-style")); }

function pcPage(el, ctx) {
  const bs = ctx.brain.brainState();
  el.innerHTML = `<section class="pa-sec"><p class="pa-lead">${bs.pc ? "Paired. I can use your PC's brain and sync with it." : "Not paired. The PC is optional."}</p>
    ${bs.pc ? `<button class="pa-btn danger" id="s-unpair" type="button">Unpair this phone</button>`
      : `<p class="pa-note">On the PC, open MBUDDY, then Away, then New pairing code, and enter the address it shows and the six digits here.</p>
        <input class="pa-field" id="s-addr" placeholder="https://your-pc.your-tailnet.ts.net" inputmode="url" autocapitalize="off" aria-label="PC address">
        <input class="pa-field" id="s-code" inputmode="numeric" maxlength="6" placeholder="6-digit code" aria-label="Pairing code">
        <button class="pa-btn go" id="s-pair" type="button">Pair</button><p class="pa-note" id="s-pair-out"></p>`}</section>`;
  const pair = el.querySelector("#s-pair");
  if (pair) pair.addEventListener("click", async () => {
    const po = el.querySelector("#s-pair-out"); po.textContent = "Pairing…"; po.className = "pa-note";
    let r = null;
    try { r = await (await fetch("/native/pair", { method: "POST", body: JSON.stringify({ address: el.querySelector("#s-addr").value.trim(), code: el.querySelector("#s-code").value.trim(), name: "My phone" }) })).json(); } catch { /* below */ }
    if (r && r.ok) location.reload(); else { po.textContent = (r && r.error) || "That didn't work."; po.className = "pa-note bad"; }
  });
  const un = el.querySelector("#s-unpair"); if (un) un.addEventListener("click", () => { try { N() && N().unpair(); } catch { /* fine */ } location.reload(); });
}

function dataPage(el, ctx) {
  el.innerHTML = `<section class="pa-sec"><p class="pa-lead">Your tasks, events, notes and conversation are kept on this phone. Only the cloud brain's answers need your words to travel, and only your words: your lists stay here unless you allowed sharing them.</p>
    <button class="pa-btn" id="s-clear" type="button">Clear the conversation</button></section>
    <section class="pa-sec"><h2>Your data</h2>
      <button class="pa-btn" id="s-export" type="button">Export my data</button>
      <p class="pa-note" id="s-export-out">Saves your tasks, events, notes, level, badges, game levels and settings as one file in Downloads. Keys and tokens are never in it.</p>
      <button class="pa-btn" id="s-import" type="button">Restore from a file</button>
      <p class="pa-note" id="s-import-out">Pick a file you exported before. You choose whether it is added to what is here or replaces it; a lower level in the file never lowers yours.</p>
      <h2 style="margin-top:18px">Problems log</h2>
      <p class="pa-note" id="s-prob-out"></p>
      <div class="pa-two"><button class="pa-btn" id="s-prob-copy" type="button">Copy the log</button><button class="pa-btn" id="s-prob-clear" type="button">Clear it</button></div>
      <h2 style="margin-top:18px">Start over</h2>
      <button class="pa-btn danger" id="s-wipe" type="button">Delete everything</button>
      <p class="pa-note">Removes every task, event, note, the conversation, your level and badges, the cloud key and the PC pairing from this phone. Downloaded models stay. It cannot be undone.</p></section>`;
  el.querySelector("#s-export").addEventListener("click", () => {
    const data = ctx.store.exportAll();
    const name = `mbuddy-export-${new Date().toISOString().slice(0, 10)}.json`, out = el.querySelector("#s-export-out");
    let where = ""; try { where = N() && N().saveToDownloads ? N().saveToDownloads(name, JSON.stringify(data, null, 2)) : ""; } catch { where = ""; }
    out.textContent = where ? `Saved: ${where}` : "I couldn't save the file."; out.className = "pa-note " + (where ? "good" : "bad");
  });
  const probText = () => {
    const l = ctx.store.problems.list(); let crash = ""; try { crash = N() && N().lastCrash ? N().lastCrash() : ""; } catch { crash = ""; }
    const NL = String.fromCharCode(10);
    return [crash ? "Last crash of the app:" + NL + crash : "", ...l.map((p) => `${new Date(p.at).toISOString()}  ${p.text}`)].filter(Boolean).join(NL) || "";
  };
  const probShow = () => { const t = probText(), n = ctx.store.problems.list().length; el.querySelector("#s-prob-out").textContent = t ? `${n} thing${n === 1 ? "" : "s"} noted${/Last crash/.test(t) ? " and one crash of the app" : ""}. It stays on this phone; copy it if you want to send it to someone.` : "Nothing has gone wrong that I noticed."; };
  probShow();
  el.querySelector("#s-prob-copy").addEventListener("click", () => { const t = probText(); if (!t) { ctx.toast("Nothing to copy."); return; } try { navigator.clipboard.writeText(t).then(() => ctx.toast("Copied.")).catch(() => ctx.toast("Could not copy.")); } catch { ctx.toast("Could not copy."); } });
  el.querySelector("#s-prob-clear").addEventListener("click", () => { ctx.store.problems.clear(); try { N() && N().clearCrash && N().clearCrash(); } catch { /* fine */ } probShow(); });
  el.querySelector("#s-import").addEventListener("click", () => {
    const out = el.querySelector("#s-import-out");
    window.__nativePicked = (text) => {
      if (text == null) return;
      let data = null; try { data = JSON.parse(text); } catch { /* below */ }
      const info = ctx.store.inspectImport(data);
      if (!info.ok) { out.textContent = info.why || "I could not read that file."; out.className = "pa-note bad"; return; }
      ctx.sheet((body, close) => {
        body.insertAdjacentHTML("beforeend", `<h2>Restore this file?</h2><p class="pa-note">It has ${info.tasks.length} task${info.tasks.length === 1 ? "" : "s"}, ${info.events.length} event${info.events.length === 1 ? "" : "s"}, ${info.notes.length} note${info.notes.length === 1 ? "" : "s"} and level ${info.level}${info.exported ? `, saved ${esc(info.exported.slice(0, 10))}` : ""}.</p>
          <button class="pa-btn go" id="i-merge" type="button">Add to what is here</button><button class="pa-btn" id="i-replace" type="button">Replace what is here</button><button class="pa-btn" id="i-no" type="button">Cancel</button>`);
        const apply = (mode) => { const r = ctx.store.importAll(data, mode); close(); out.textContent = `Restored ${r.tasks.length} tasks, ${r.events.length} events, ${r.notes.length} notes.`; out.className = "pa-note good"; ctx.toast("Restored."); setTimeout(() => location.reload(), 600); };
        body.querySelector("#i-merge").addEventListener("click", () => apply("merge"));
        body.querySelector("#i-replace").addEventListener("click", () => apply("replace"));
        body.querySelector("#i-no").addEventListener("click", close);
      });
    };
    try { if (N() && N().pickFile) N().pickFile(); else { out.textContent = "File restore works inside the app."; out.className = "pa-note bad"; } } catch { out.textContent = "I could not open the file picker."; out.className = "pa-note bad"; }
  });
  el.querySelector("#s-wipe").addEventListener("click", () => ctx.sheet((body, close) => {
    body.insertAdjacentHTML("beforeend", `<h2>Delete everything?</h2><p class="pa-note">Every task, event, note, the conversation, your level and badges, the cloud key and the PC pairing will be removed from this phone. This cannot be undone.</p>
      <button class="pa-btn danger" id="w-yes" type="button">Yes, delete everything</button><button class="pa-btn" id="w-no" type="button">Keep it</button>`);
    body.querySelector("#w-no").addEventListener("click", close);
    body.querySelector("#w-yes").addEventListener("click", () => {
      try { N() && N().wipeAll && N().wipeAll(); } catch { /* the page data goes anyway */ }
      Object.keys(localStorage).filter((k) => k.startsWith("mbp_") || k.startsWith("mb_")).forEach((k) => localStorage.removeItem(k));
      close(); setTimeout(() => { location.hash = "#/buddy"; location.reload(); }, 300);
    });
  }));
  el.querySelector("#s-clear").addEventListener("click", () => {
    const saved = ctx.store.convo.list(200);
    ctx.store.convo.clear();
    ctx.toast("Conversation cleared.", 4500, { label: "Undo", run: () => saved.forEach((c) => ctx.store.convo.add(c.you, c.me, c.source, c.did)) });
  });
}

// ---- how the buddy shows itself over other apps: a bar at the bottom, or an island grown out of the camera ----
function styleCard(ctx, box) {
  if (!N() || !N().overlayStyle) { box.innerHTML = `<p class="pa-note">Works in the phone app.</p>`; return; }
  const draw = () => {
    const cur = N().overlayStyle();
    const opt = (id, name, about) => `<button type="button" class="pa-choice${cur === id ? " on" : ""}" data-style="${id}" aria-pressed="${cur === id}">
      <span class="t">${name}</span><span class="s">${about}</span></button>`;
    box.innerHTML = `<div class="pa-choices">${opt("bar", "Bar", "A black bar at the bottom, a light that travels round the screen's edge.")}
      ${opt("island", "Island", "I grow out of your camera at the top, and the light unravels from it down both sides.")}</div>
      <label class="pa-switch"><span>Colourful light</span><input type="checkbox" id="st-col" ${N().colorfulLight() ? "checked" : ""}><i></i></label>
      <label class="pa-switch"><span>Show each step as I work<small>Off: only your request, my questions and the result.</small></span><input type="checkbox" id="st-steps" ${N().showSteps() ? "checked" : ""}><i></i></label>
      <button class="pa-btn" id="st-prev" type="button">Preview</button><p class="pa-note" id="st-out"></p>`;
    box.querySelector("#st-col").addEventListener("change", (e) => N().setColorfulLight(e.target.checked));
    box.querySelector("#st-steps").addEventListener("change", (e) => N().setShowSteps(e.target.checked));
    box.querySelectorAll("[data-style]").forEach((b) => b.addEventListener("click", () => { N().setOverlayStyle(b.dataset.style); draw(); }));
    box.querySelector("#st-prev").addEventListener("click", () => { const why = N().previewOverlay(); const o = box.querySelector("#st-out"); o.textContent = why; o.className = "pa-note " + (why ? "bad" : ""); });
  };
  draw();
}

// ---- the assistant button: hold the power button (or the corner swipe) to call the buddy, with no microphone open until then ----
let assistTimer = null;
function assistCard(box) {
  clearInterval(assistTimer);
  if (!N() || !N().isAssistant) { box.innerHTML = `<p class="pa-note">Works in the phone app.</p>`; return; }
  let last = null;
  const draw = () => {
    const on = !!N().isAssistant();
    if (on === last) return; last = on;
    box.innerHTML = on
      ? `<p class="pa-note good">MBUDDY is your assistant. Hold the power button (or swipe up from a bottom corner) and talk. Nothing listens until you do.</p>`
      : `<p class="pa-note">Hold the power button (or swipe up from a bottom corner) to call me from anywhere, the way you call Gemini. Nothing listens until you do, so no microphone stays on. Android lets only you choose the assistant: tap below, choose “Digital assistant app”, then MBUDDY.</p>
         <div class="pa-chips"><button class="pa-btn go" id="as-open" type="button">Make MBUDDY my assistant</button></div>`;
    const b = box.querySelector("#as-open"); if (b) b.addEventListener("click", () => N().openAssistantSettings());
  };
  draw();
  assistTimer = setInterval(draw, 1500); timers.push(assistTimer);
}

// ---- "hey buddy": listen for the name with the app closed ----
let wakeTimer = null;
function wakeCard(ctx, box) {
  clearInterval(wakeTimer);
  if (!N() || !N().wakeState) { box.innerHTML = `<p class="pa-note">“Hey buddy” works in the phone app.</p>`; return; }
  let lastKey = "";
  const draw = () => {
    let w = null; try { w = JSON.parse(N().wakeState()); } catch { /* below */ }
    if (!w) return;
    const key = JSON.stringify(w); if (key === lastKey) return; lastKey = key;
    box.innerHTML = `<p class="pa-note">Say “hey buddy” with the app closed or the screen off. To hear it, the microphone stays on (Android shows its dot and a notification) and it uses some battery: only Google's own assistant gets the phone's low-power listening chip. A small offline model waits for the two words; no sound is kept or sent anywhere. The assistant button above needs none of this.</p>
      ${!w.installed ? (w.phase ? `<p class="pa-note">${w.phase === "downloading" ? `Downloading ${Math.round(w.progress * 100)}% of ${w.sizeMb} MB…` : w.phase === "checking" ? "Checking the file…" : "Unpacking…"}</p>`
          : `${w.error ? `<p class="pa-note bad">${esc(w.error)}</p>` : ""}<div class="pa-chips"><button class="pa-btn go" id="w-dl" type="button">Download the listening model (${w.sizeMb} MB)</button></div>`)
        : `<label class="pa-switch"><span>${w.running ? "Listening for “hey buddy”" : "Listen for “hey buddy”"}</span><input type="checkbox" id="w-on" ${w.enabled ? "checked" : ""}><i></i></label>
          <p class="pa-note" id="w-out"></p>
          <div class="pa-chips">${w.overlay ? "" : `<button type="button" id="w-ov">Let me open apps by voice</button>`}<button type="button" id="w-bat">Keep it running (battery)</button><button class="pa-btn danger" id="w-del" type="button">Remove the model</button></div>
          ${w.overlay ? "" : `<p class="pa-note">“Let me open apps by voice” allows Display over other apps, which Android needs before I can open an app while another is in front or the screen is off.</p>`}`}`;
    const dl = box.querySelector("#w-dl"); if (dl) dl.addEventListener("click", () => { const why = N().wakeModelDownload(); if (why) ctx.toast(why); lastKey = ""; draw(); });
    const on = box.querySelector("#w-on");
    if (on) on.addEventListener("change", (e) => { const why = N().wakeEnable(e.target.checked); const out = box.querySelector("#w-out"); out.textContent = why; out.className = "pa-note " + (why ? "bad" : ""); lastKey = ""; setTimeout(draw, 500); });
    const ov = box.querySelector("#w-ov"); if (ov) ov.addEventListener("click", () => N().openOverlaySettings());
    const bat = box.querySelector("#w-bat"); if (bat) bat.addEventListener("click", () => N().openBatterySettings());
    const del = box.querySelector("#w-del"); if (del) del.addEventListener("click", () => { N().wakeModelDelete(); lastKey = ""; draw(); });
  };
  draw();
  wakeTimer = setInterval(draw, 1000); timers.push(wakeTimer);
}

// ---- the model on the phone: download, use, remove ----
let lmTimer = null;
function lmCard(ctx, box) {
  clearInterval(lmTimer);
  if (!N() || !N().localModelState) { box.innerHTML = `<p class="pa-note">The model on the phone is available in the phone app.</p>`; return; }
  const gb = (b) => (b / 1e9).toFixed(1) + " GB";
  let lastKey = "";
  const draw = () => {
    let st = null; try { st = JSON.parse(N().localModelState()); } catch { /* below */ }
    if (!st) return;
    const key = JSON.stringify(st.models.map((m) => [m.installed, Math.round(m.progress * 200), m.downloading, m.error])) + st.active + st.loaded;
    if (key === lastKey) return; lastKey = key;
    box.innerHTML = `<p class="pa-note">Runs on this phone with no internet and nothing sent anywhere. It is slower and less capable than the cloud, so it is the fallback when the cloud and your PC can't be reached. It needs the download once (use Wi-Fi). Free storage: ${gb(st.freeMb * 1e6)}.${st.loaded ? " It is loaded in memory now." : ""}</p>
      <div class="pa-list list">${st.models.map((m) => `<div class="item" style="align-items:flex-start;flex-direction:column;gap:6px">
        <div class="pa-row" style="width:100%"><div class="grow"><div class="t">${esc(m.label)} <span class="faint">${gb(m.bytes)}</span> ${st.active === m.id ? `<span class="chip">in use</span>` : ""}</div><div class="s">${esc(m.note)}</div></div></div>
        ${m.downloading ? `<div class="progress" style="width:100%;height:8px;border-radius:99px;background:var(--surface-3);overflow:hidden"><i style="display:block;height:100%;width:${Math.round(m.progress * 100)}%;background:var(--accent)"></i></div>
          <div class="pa-row" style="width:100%"><span class="pa-note grow">${m.phase === "checking" ? "Checking the file…" : `Downloading ${Math.round(m.progress * 100)}%`}</span><button class="pa-btn" data-cancel type="button">Stop</button></div>`
        : m.installed ? `<div class="pa-chips" style="margin:0">${st.active === m.id || m.tool ? "" : `<button class="pa-btn go" data-use="${m.id}" type="button">Use this one</button>`}<button class="pa-btn danger" data-del="${m.id}" type="button">Remove</button></div>`
        : `${m.error ? `<p class="pa-note bad">${esc(m.error)}</p>` : ""}<button class="pa-btn go" data-get="${m.id}" type="button">${m.partial ? "Carry on downloading" : "Download"}</button>`}
      </div>`).join("")}</div>
      ${st.active ? `<div class="pa-form" style="margin-top:8px"><button class="pa-btn" id="lm-test" type="button">Try it: ask it a question</button><p class="pa-note" id="lm-out"></p></div>` : ""}`;
    box.querySelectorAll("[data-get]").forEach((b) => b.addEventListener("click", () => { const why = N().localModelDownload(b.dataset.get); if (why) ctx.toast(why); lastKey = ""; draw(); }));
    box.querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", () => { N().localModelUse(b.dataset.use); lastKey = ""; draw(); }));
    box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => { N().localModelDelete(b.dataset.del); lastKey = ""; draw(); }));
    box.querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", () => { N().localModelCancel(); lastKey = ""; setTimeout(draw, 400); }));
    const test = box.querySelector("#lm-test");
    if (test) test.addEventListener("click", async () => {
      const out = box.querySelector("#lm-out"); out.className = "pa-note"; out.textContent = "Thinking on the phone… the first time it has to load the model, which can take half a minute.";
      let j = null;
      try { j = await (await fetch("/native/local-chat", { method: "POST", body: JSON.stringify({ system: "You are a friendly desk buddy. Answer in one short sentence.", user: "Say hello and tell me one fun fact about owls." }) })).json(); } catch { /* below */ }
      if (j && j.ok) { out.className = "pa-note good"; out.textContent = `${j.text.trim()}  (${(j.ms / 1000).toFixed(1)} s)`; } else { out.className = "pa-note bad"; out.textContent = (j && j.error) || "It couldn't answer."; }
    });
  };
  draw();
  lmTimer = setInterval(draw, 1000); timers.push(lmTimer);
}
