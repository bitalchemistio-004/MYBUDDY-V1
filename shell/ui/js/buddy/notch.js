// The notch: the buddy's window as a thin pill floating at the top of the screen (popup style "notch").
//   rest  nothing going on: a long, thin strip with the character's face, one word and the time.
//   peek  something is happening (it speaks, asks, works, listens): the same pill, wider, with it inline.
//   room  a short wide pill: the character, what it last said, a question waiting for your yes, the tools
//         you use most (in the order you use them), what is coming up, and a line to type to it.
// The glow colour is the kind of moment: a deadline closing in warms from amber to red, a job is blue, a focus
// sprint indigo, music pink, a question for you red, listening cyan; the character's own colour otherwise.
// Everything shown comes from the live status or from the APIs the app uses; the buttons call real things. The
// character and its clips are the existing ones (sprite.js, state.js): here they are only cropped to the face
// (never stretched) and placed.
import { api, chatTurn } from "../api.js";
import { musicView, whenText } from "./cards.js";
import { VIEWS } from "./notch-views.js";
import { glowMood, moodOf, wordOf, freshReward, levelProgress } from "./notch-state.js";
import { crop as wardrobeCrop, setSurface as wardrobeSurface } from "./wardrobe.js";

const RANK = { rest: 0, peek: 1, room: 2 };
const FACE_H = { rest: 20, peek: 26, room: 46 };      // height of the character's crop in each size, px (the open notch: see PLATE_*)
const PLATE_H = 116, PLATE_W = 150;                   // the open notch's plate: the character is at most this tall and this wide
const HOVER_OPEN_MS = 450, CLOSE_AFTER_MS = 1400, MORPH_MS = 300, OVERVIEW_EVERY_MS = 20000;
// The part of the 320x240 art shown: the eyes band while resting and peeking, the whole body in the room.
// Measured from each character's idle frame. Blink is already a wide pill with its eyes: it is shown whole.
const CROPS = {
  blink: { eyes: [63, 65, 194, 94], body: [63, 65, 194, 94] },
  void: { eyes: [88, 78, 160, 66], body: [66, 38, 199, 149] },
  glitch: { eyes: [96, 76, 130, 56], body: [95, 59, 131, 107] },
  cinder: { eyes: [92, 88, 146, 62], body: [83, 7, 154, 170] },
  oracle: { eyes: [80, 68, 160, 82], body: [69, 44, 182, 136] },
};
const PIN_PATH = {
  star: "M12 2l2.9 6.6 7.1.7-5.3 4.8 1.6 7L12 17.6 5.7 21l1.6-7L2 9.3l7.1-.7z", bolt: "M13 2L4 14h6l-1 8 9-12h-6z",
  leaf: "M5 19C5 9 11 4 20 4c0 9-5 15-15 15zM5 19c3-5 6-8 10-10", flame: "M12 2c1.5 5 7 7 7 13a7 7 0 0 1-14 0c0-3 2-5 3-7 1 2 2 3 3 3 0-3-1-6 1-9z",
  note: "M9 18V5l11-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
};
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const I = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
const ICON = {
  timer: I("M12 8v5l3 2M9 2h6M12 21a8 8 0 1 0 0-16 8 8 0 0 0 0 16z"), remind: I("M18 8a6 6 0 1 0-12 0c0 7-3 8-3 8h18s-3-1-3-8zM10 20a2 2 0 0 0 4 0"),
  task: I("M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"), focus: I("M6 3h12M6 21h12M7 3c0 5 5 6 5 9s-5 4-5 9M17 3c0 5-5 6-5 9s5 4 5 9"),
  music: I("M9 18V5l11-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"), mic: I("M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3"),
  quiet: I("M20 15A8 8 0 1 1 9 4a6 6 0 0 0 11 11z"), app: I("M4 5h16v14H4zM4 9h16"),
  calendar: I("M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"),
  learned: I("M12 3a6 6 0 0 0-6 6c0 2.2 1.2 3.6 2 4.5V17h8v-3.5c.8-.9 2-2.3 2-4.5a6 6 0 0 0-6-6zM9 21h6"),
  phone: I("M7 2h10a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2"), run: I("M7 4l13 8-13 8z"),
  list: I("M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"), more: I("M5 12h.01M12 12h.01M19 12h.01"),
};
// The small status glyphs on the right of the room's bar: each is on only when it is really so.
const CHIPS = [
  ["disc", "Something is playing", "spin", "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"],
  ["glass", "Focus sprint running", "", "M6 3h12M6 21h12M7 3c0 5 5 6 5 9s-5 4-5 9M17 3c0 5-5 6-5 9s5 4 5 9"],
  ["heat", "The PC is working hard", "", "M12 3c1.5 5 7 7 7 13a7 7 0 0 1-14 0c0-3 2-5 3-7 1 2 2 3 3 3 0-3-1-6 1-9z"],
  ["mic", "Hearing you", "", "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3"],
];

export function createNotch(host) {
  const $ = host.$;
  const el = { notch: $("#notch"), shell: $("#shell"), inner: $("#ninner"), scene: $("#scene"), chips: $("#props"), nside: $("#nside"), room: $("#nroom"),
    word: $("#nword"), meta: $("#nmeta"), close: $("#nclose") };
  let mounted = false, state = "rest", built = false, pinned = false, hovering = false, focused = false, roomUntil = 0, hoverTimer = 0;
  let rewards = null, rewardSeen = null, showAllTools = false, autoView = false, overview = null, routines = [], overviewAt = 0, nextText = "", replyText = "", busy = false, transitioning = false, doneUntil = 0, lastW = "", hint = "", cfg = {}, lastMood = "", lastMinute = "", lastGreet = 0, view = "home", viewCtl = null, noHoverUntil = 0, chosenHome = false;

  const settings = () => host.settings();
  const status = () => host.status();
  const uses = () => { try { return JSON.parse(localStorage.getItem("mbuddy_notch_use") || "{}"); } catch { return {}; } };
  const bump = (id) => { try { const u = uses(); u[id] = (u[id] || 0) + 1; localStorage.setItem("mbuddy_notch_use", JSON.stringify(u)); } catch { /* the order just stays as it is */ } };

  // ---- what each button really does ----
  const chat = async (text) => {
    say("Thinking…");
    const r = await chatTurn(text, settings() ? settings().speak_replies !== false : true, () => say("Still working on it… I'll answer here when it's done."));
    const reply = r && r.ok ? r.reply
      : r && r.error === "too_slow" ? "This one is taking far too long, so I stopped waiting. Check Now for what happened."
      : r && r.error === "interrupted" ? "Something else came in before I could answer that. Ask me again?"
      : "I couldn't reach my brain just now.";
    say(reply);
    if (r && r.ok && host.say) host.say(reply);      // also as a bubble, so it is still there when the room closes
    return !!(r && r.ok);
  };
  const prefill = (text) => () => { const i = $("#ninput"); i.value = text; i.focus(); i.setSelectionRange(text.length, text.length); };
  const goto = (hash) => async () => {
    await host.invoke("open_main").catch(() => {});
    const t = window.__TAURI__;
    if (t && t.event) await t.event.emit("mbuddy-goto", { hash });
  };
  const toggle = (key) => async () => { const r = await api.post("/ui/settings", { changes: { [key]: !settings()[key] } }).catch(() => null); if (r && r.ok) host.applySettings(r.settings); paint(); };
  const playing = () => !!(status() && status().media && status().media.playing);
  const ACTIONS = [
    { id: "timer", label: "Timer", run: () => showView("timer") },
    { id: "remind", label: "Remind me", run: () => showView("task", "Remind me") },
    { id: "task", label: "Add a task", run: () => showView("task", "Add a task") },
    { id: "focus", label: "Focus sprint", run: () => showView("focus") },
    { id: "music", label: () => (playing() ? "Now playing" : "Music"), run: () => showView("music") },
    { id: "mic", label: () => (settings().microphone_on === false ? "Microphone is off" : "Microphone is on"), run: toggle("microphone_on"), pressed: () => settings().microphone_on !== false },
    { id: "quiet", label: "Do not disturb", run: toggle("do_not_disturb"), pressed: () => !!settings().do_not_disturb },
    { id: "app", label: "Open MBUDDY", run: async () => { await host.invoke("open_main").catch(() => {}); } },
    { id: "task2", icon: "list", label: "Tasks", run: goto("#/plan/tasks") },
    { id: "calendar", label: "Calendar", run: goto("#/plan/calendar") },
    { id: "learned", label: "What I learned", run: goto("#/brain/learned") },
    { id: "phone", label: () => (settings().work_on === "phone" ? "Working on my phone" : "Working on my PC"), pressed: () => settings().work_on === "phone",
      run: async () => { const r = await api.post("/ui/models", { work_on: settings().work_on === "phone" ? "pc" : "phone" }).catch(() => null); if (r && r.ok) { settings().work_on = r.work_on; say(r.work_on === "phone" ? "Working on your phone. The PC stays quiet." : "Working on your PC."); } paint(); } },
  ];
  const labelOf = (a) => (typeof a.label === "function" ? a.label() : a.label);

  function say(text) { replyText = text; paintSay(); }

  // ---- text that is centred, and that turns round to show all of it when it is too long ----
  const watched = new WeakSet();
  let marqueeRO = null;
  function setText(node, text) {
    if (!node) return;
    node.classList.add("mq");
    if (node._text !== text) {
      node._text = text;
      node._runFor = "";
      node.classList.remove("run");
      node.innerHTML = `<span class="mq-track"><span class="mq-t"></span></span>`;
      node.querySelector(".mq-t").textContent = text;
    }
    if (!watched.has(node) && window.ResizeObserver) {
      watched.add(node);
      marqueeRO = marqueeRO || new ResizeObserver((list) => list.forEach((en) => requestAnimationFrame(() => fitMarquee(en.target))));
      marqueeRO.observe(node);
    }
    requestAnimationFrame(() => fitMarquee(node));
  }
  // Too long for its place: two copies side by side slide round in a loop (after a pause at the start), so every
  // word is shown. Short enough: it stays centred and still. With reduced motion it is cut with an ellipsis.
  function fitMarquee(node) {
    const track = node.querySelector(".mq-track"), t = node.querySelector(".mq-t");
    if (!track || !t || !node.clientWidth) return;
    const text = node._text || "", over = t.scrollWidth > node.clientWidth + 2;
    if (!over || host.reduced()) {
      if (node.classList.contains("run")) { node.classList.remove("run"); track.innerHTML = `<span class="mq-t"></span>`; track.firstChild.textContent = text; }
      node.classList.toggle("cut", over);
      return;
    }
    if (node.classList.contains("run") && node._runFor === `${text}|${node.clientWidth}`) return;
    track.innerHTML = `<span class="mq-t"></span><span class="mq-gap"></span><span class="mq-t"></span>`;
    track.querySelectorAll(".mq-t").forEach((n) => { n.textContent = text; });
    const one = track.firstElementChild.offsetWidth + 36;
    node.style.setProperty("--mq-w", `${one}px`);
    node.style.setProperty("--mq-d", `${Math.max(6, one / 38)}s`);       // about 38 px a second: readable
    node.classList.add("run"); node.classList.remove("cut");
    node._runFor = `${text}|${node.clientWidth}`;
  }

  // ---- what the room shows: the tools, or one thing in full (music, a timer, a task, a focus sprint) ----
  // Changing what is shown morphs the pill (it grows or shrinks to what the thing needs); it never adds a second pill.
  function morphDo(fn) {
    if (document.startViewTransition && !host.reduced() && el.shell.offsetWidth) {
      try {
        const t = document.startViewTransition(fn);
        t.ready.catch(() => {}); t.updateCallbackDone.catch(() => {}); t.finished.catch(() => {});
        return;
      } catch { /* fall through */ }
    }
    fn();
  }
  const viewHost = () => ({ api, say, chat, setText, micro, auto: () => autoView, leave: () => { chosenHome = true; showView("home"); } });
  function showViewNow(name, heading) {
    if (viewCtl) { viewCtl.destroy(); viewCtl = null; }
    view = name;
    const box = $("#nviewbox"), home = $("#nhome");
    if (name === "home" || !VIEWS[name]) { view = "home"; box.hidden = true; home.hidden = false; $("#nview").innerHTML = ""; paintActs(); }
    else {
      home.hidden = true; box.hidden = false;
      $("#nvtitle").textContent = heading || VIEWS[name].title;
      viewCtl = VIEWS[name].mount($("#nview"), viewHost());
      viewCtl.update(status());
      if (viewCtl.focus) setTimeout(() => viewCtl.focus(), 60);
    }
    host.hot();
  }
  function showView(name, heading) { autoView = false; morphDo(() => showViewNow(name, heading)); }
  // What it opens into: what you are most likely there for (the music that is playing, a timer that is running).
  function defaultView() {
    if (playing()) return "music";
    if (overview && (overview.timers || []).length) return "timer";
    return "home";
  }

  // ---- the notch's own settings (Settings, Buddy, Notch) ----
  const RESTW = { compact: 96, short: 240, standard: 300, long: 400 };
  function configure(c) {
    cfg = c || {};
    const n = el.notch, set = (k, v) => n.style.setProperty(k, v);
    n.dataset.length = cfg.length || "compact";
    n.dataset.glow = cfg.glow || "mood";
    n.dataset.border = cfg.border || "line";
    n.dataset.face = cfg.face || "eyes";
    n.dataset.clock = cfg.clock === false ? "0" : "1";
    n.dataset.word = cfg.word === false ? "0" : "1";
    n.dataset.dim = cfg.idle_dim ? "1" : "0";
    n.dataset.breath = cfg.breathing === false || host.reduced() ? "0" : "1";
    n.dataset.micro = cfg.micro === false || host.reduced() ? "0" : "1";
    set("--w-rest", `${RESTW[cfg.length] || 300}px`);
    set("--bar-h", cfg.thickness === "regular" ? "38px" : "30px");
    set("--r-pill", cfg.corners === "soft" ? "10px" : "18px");
    set("--r-room", cfg.corners === "soft" ? "14px" : "26px");
    set("--gk", String(((cfg.glow_strength ?? 60) / 60).toFixed(2)));
    set("--gap", `${cfg.gap ?? 6}px`);
    if (built) { paintActs(); host.resizeStage(); host.hot(); }
  }
  // What the notch wears (core/wardrobe.py, only what is unlocked): the plate, frame, shape, ledge and lamp are attributes, the pins are drawn.
  let decoKey = "";
  function decorate(d) {
    if (!d) return false;
    const key = JSON.stringify(d);
    if (key === decoKey) return false;
    decoKey = key;
    for (const part of ["plate", "frame", "shape", "ledge", "lamp"]) el.notch.dataset[part] = d[part] || "";
    $("#npins").innerHTML = (d.pins || []).filter((p) => PIN_PATH[p]).map((p) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${PIN_PATH[p]}"/></svg>`).join("");
    if (built) { host.resizeStage(); host.fit(); host.hot(); }
    return true;
  }
  // A decoration that is only being tried on: the notch says so under itself.
  function setPreview(on) { el.notch.dataset.try = on ? "1" : "0"; }
  const buttonsOn = () => (Array.isArray(cfg.buttons) ? ACTIONS.filter((a) => cfg.buttons.includes(a.id)) : ACTIONS);
  const micro = () => el.notch.dataset.micro === "1";

  // ---- the character, cropped to its face ----
  // The crop is chosen by size: the eyes band while resting, the whole body in the room. Never stretched.
  function stageSize(character) {
    const c = CROPS[character] || CROPS.blink, big = state === "room" || (cfg.face === "whole" && state !== "peek");
    // A crown does not fit the eyes band: the crop grows upward to take it (wardrobe-fit.js), only when one is worn.
    const g = wardrobeCrop(character, big ? c.body : c.eyes, FACE_H[state] || FACE_H.rest, state), part = g.part;
    let h = g.H;
    // In the open notch the whole character stands on the plate: as tall as fits, never wider than the plate allows.
    if (state === "room") h = Math.round(part[3] * Math.min(PLATE_H / part[3], PLATE_W / part[2]));
    const k = h / part[3];
    const st = document.body.style;
    st.setProperty("--k", k.toFixed(4)); st.setProperty("--cx", part[0]); st.setProperty("--cy", part[1]);
    return { w: Math.round(part[2] * k), h };
  }

  // ---- the shape ----
  function mount(on) {
    if (on === mounted) return;
    mounted = on;
    document.body.classList.toggle("nmode", on);
    el.notch.hidden = !on;
    if (on) { el.scene.append($("#stage")); el.nside.append($("#side")); }
    else { document.body.append($("#stage"), $("#side")); state = "rest"; wardrobeSurface("app"); el.notch.dataset.n = "rest"; document.body.style.removeProperty("--k"); }
    if (on) { build(); apply(state); }
  }

  function build() {
    if (built) return;
    built = true;
    el.chips.insertAdjacentHTML("beforebegin", `<span class="nlevel" id="nlevel" hidden><i class="nring" aria-hidden="true"></i><b id="nlevel-t"></b></span>`);
    el.chips.innerHTML = CHIPS.map(([id, title, cls, d]) => `<span class="chip-prop ${cls}" id="c-${id}" title="${esc(title)}">${I(d)}</span>`).join("");
    // What it last said sits in the bar itself (room only), so the room needs no row of its own for it.
    el.chips.insertAdjacentHTML("beforebegin", `<p class="nsay" id="nsay" aria-live="polite"></p>`);
    el.room.innerHTML = `<div class="nviewbox" id="nviewbox" hidden><div class="nhead"><button class="nbtn" id="nback" type="button" aria-label="Back to the tools">${I("M15 6l-6 6 6 6")}Back</button><b id="nvtitle"></b></div><div class="nview" id="nview"></div></div>
      <div class="nhome" id="nhome"><div class="nask" id="nask" hidden><span id="nask-t"></span><button class="nbtn go" data-ok="yes" type="button">Yes</button><button class="nbtn" data-ok="no" type="button">No</button></div>
      <form class="ncompose" id="nform"><label for="ninput" style="position:absolute;width:1px;height:1px;overflow:hidden">Message MBUDDY</label>
        <input id="ninput" autocomplete="off" maxlength="1000" placeholder="Ask me anything, or type / for tools"><button class="nbtn go" type="submit">Send</button></form>
      <div class="nacts" id="nacts"></div></div>`;
    $("#nback").addEventListener("click", () => { chosenHome = true; showView("home"); });
    el.room.addEventListener("click", async (e) => {
      const ok = e.target.closest("[data-ok]"), act = e.target.closest("[data-act]"), rt = e.target.closest("[data-routine]");
      if (e.target.closest("[data-more]")) { showAllTools = !showAllTools; paintActs(); host.fit(); host.hot(); return; }
      if (ok) await api.post("/ui/pending-action", { decision: ok.dataset.ok }).catch(() => {});
      if (act && !busy) {
        const a = ACTIONS.find((x) => x.id === act.dataset.act); bump(a.id); busy = true;
        if (micro()) ripple(act, e);
        let good = true;
        try { good = (await a.run()) !== false; } catch { good = false; } finally { busy = false; }
        paintActs();
        const again = $(`[data-act="${a.id}"]`);
        if (again && micro()) { again.classList.add(good ? "ok" : "bad"); setTimeout(() => again.classList.remove("ok", "bad"), 800); }
      }
      if (rt) { const r = await api.post("/ui/routines/run", { id: rt.dataset.routine }).catch(() => null); say(r && r.ok ? "Running it." : (r && r.error) || "I couldn't run that."); }
    });
    // Pointing at a button says what it is in the line above.
    el.room.addEventListener("mouseover", (e) => { const b = e.target.closest("[data-act],[data-routine]"); hint = b ? (b.getAttribute("aria-label") || "") : ""; paintSay(); });
    el.room.addEventListener("mouseleave", () => { hint = ""; paintSay(); });
    $("#nform").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = $("#ninput"), text = input.value.trim();
      if (!text) return;
      input.value = "";
      const go = $("#nform .go"); go.classList.add("busy");
      try { await chat(text); } finally { go.classList.remove("busy"); }
    });
    const input = $("#ninput");
    input.addEventListener("input", () => {
      if (input.value.trim() === "/") { input.value = ""; showAllTools = !showAllTools; paintActs(); host.fit(); host.hot(); }
    });
    input.addEventListener("focus", () => { focused = true; });
    input.addEventListener("blur", () => { focused = false; hold(); });
    // The window never takes focus by itself; a click on the line to type does.
    input.addEventListener("mousedown", () => { if (host.win && host.win.setFocus) host.win.setFocus().catch(() => {}); });
    window.addEventListener("keydown", (e) => { if (e.key === "Escape") { pinned = false; roomUntil = 0; evaluate(); } });
    el.close.addEventListener("click", () => { pinned = false; roomUntil = 0; evaluate(); });
    // Rest the pointer on it and it opens; leave it and it closes. A click pins it open.
    el.shell.addEventListener("mouseenter", () => {
      if (transitioning) { hovering = true; return; }
      hovering = true;
      clearTimeout(hoverTimer);
      // Hovering opens the room from rest or a plain reply, never while the strip holds a question or an alert with
      // buttons (you are reaching for them), and not for a moment after you pressed one of them.
      if (settings() && settings().notch_hover !== false && state !== "room" && !asking()) hoverTimer = setTimeout(() => { if (hovering && Date.now() > noHoverUntil) openRoom(); }, HOVER_OPEN_MS);
    });
    // While the shape is morphing the browser draws its own pictures on top, and the page sees the pointer
    // "leave" and "enter" for no reason: those are ignored, and the real state is read when it is done.
    el.shell.addEventListener("click", (e) => { if (e.target.closest("button")) noHoverUntil = Date.now() + 2200; }, true);
    el.shell.addEventListener("mouseleave", () => { if (transitioning) return; hovering = false; clearTimeout(hoverTimer); hold(); });
    let lightQueued = 0, lastEvent = null;
    el.shell.addEventListener("mousemove", (e) => {
      if (!micro()) return;
      lastEvent = e;
      if (lightQueued) return;
      lightQueued = requestAnimationFrame(() => { lightQueued = 0; const r = el.shell.getBoundingClientRect(); el.shell.style.setProperty("--mx", `${lastEvent.clientX - r.left}px`); el.shell.style.setProperty("--my", `${lastEvent.clientY - r.top}px`); });
    });
  }

  function ripple(btn, e) {
    const r = btn.getBoundingClientRect(), d = Math.max(r.width, r.height) * 2.2, dot = document.createElement("span");
    dot.className = "rip"; dot.style.cssText = `left:${e.clientX - r.left}px;top:${e.clientY - r.top}px;width:${d}px;height:${d}px`;
    btn.append(dot); setTimeout(() => dot.remove(), 600);
  }

  const hold = () => { roomUntil = Date.now() + CLOSE_AFTER_MS; setTimeout(evaluate, CLOSE_AFTER_MS + 30); };
  function openRoom() { roomUntil = Date.now() + 4000; evaluate(true); setTimeout(evaluate, 4040); }
  function toggleRoom() { if (state === "room" && pinned) { pinned = false; roomUntil = 0; } else { pinned = true; } evaluate(true); }

  // A question with answers (a timer that is done, a reminder, an approval) is shown in the strip and nothing opens over it: the room
  // would put the question beside itself and the two would draw on top of each other.
  const asking = () => [...$("#side").children].some((n) => !n.hidden && (n.id === "act" || n.id === "bub")) || !!host.attention();
  function wanted() {
    if (asking()) { pinned = false; return "peek"; }
    if (pinned || hovering || focused || Date.now() < roomUntil) return "room";
    const shown = [...$("#side").children].some((n) => !n.hidden);
    return shown ? "peek" : "rest";
  }

  // The compact pill is only its face and a dot; it widens just enough for the words it shows (a question keeps room for its answers).
  function peekWidth() {
    if (asking()) return 460;
    const chars = [...$("#side").children].filter((n) => !n.hidden).reduce((a, n) => a + (n.textContent || "").trim().length, (el.word.textContent || "").length + (el.meta.textContent || "").length);
    return Math.round(Math.min(460, Math.max(190, 100 + chars * 7.2)));
  }
  let evaluating = false;
  function evaluate(force = false) {
    if (!mounted || evaluating || transitioning) return;
    evaluating = true;
    try {
      const next = wanted();
      paintLine();
      el.nside.hidden = ![...$("#side").children].some((n) => !n.hidden && !(state === "room" && n.id === "bubble"));
      el.notch.style.setProperty("--w-peek", `${peekWidth()}px`);       // compact: as wide as what it says needs, and no wider
      el.notch.dataset.stack = asking() && next !== "room" ? "1" : "0";     // a question gets its own lines (notch.css)
      if (next !== state || force) change(next);
    } finally { evaluating = false; }
  }

  // The window is already as large as the room, so only the shape moves. Opening: it draws out sideways first,
  // then drops down, and what is inside rises into place one part after another. Closing is the same in
  // reverse: the parts of the room let go first (they fade up and away), then the shape tucks up and draws in
  // with no bounce, and the face eases to its small size at the same time. The size is animated between the
  // measured old and new sizes, so the switch of layout underneath is never seen.
  const OPEN_MS = 460, CLOSE_MS = 400, LET_GO_MS = 170;
  const radiusOf = (st) => (st === "room" ? (cfg.corners === "soft" ? 14 : 26) : (cfg.corners === "soft" ? 10 : 18));
  const sizeOf = () => ({ w: el.shell.offsetWidth, h: el.shell.offsetHeight });
  const parts = () => [$("#nsay"), el.chips, el.close, el.room].filter(Boolean);
  const run = (node, frames, opts) => { try { return node.animate(frames, opts); } catch { return null; } };
  function shapeTo(from, to, ms, easing, opening, r0, r1) {
    // Two moves: opening goes wider and then taller; closing goes shorter and then narrower.
    const mid = opening ? { w: to.w, h: from.h } : { w: from.w, h: to.h };
    const px = (v) => `${v}px`;
    return run(el.shell, [
      { width: px(from.w), height: px(from.h), borderRadius: px(r0) },
      { offset: opening ? 0.5 : 0.45, width: px(mid.w), height: px(mid.h), borderRadius: px((r0 + r1) / 2) },
      { width: px(to.w), height: px(to.h), borderRadius: px(r1) },
    ], { duration: ms, easing });
  }
  // Diagnostics: how smooth was this change really? Frame times during it, written to logs/notch_perf.jsonl.
  function probe(label, ms) {
    const t0 = performance.now(), stamps = [];
    const tick = (t) => {
      stamps.push(t);
      if (t - t0 < ms + 150) requestAnimationFrame(tick);
      else {
        const d = stamps.slice(1).map((v, i) => v - stamps[i]);
        api.post("/ui/perf", { what: label, frames: stamps.length, ms: Math.round(stamps[stamps.length - 1] - t0), avg: +(d.reduce((a, b) => a + b, 0) / Math.max(1, d.length)).toFixed(1),
          max: +Math.max(0, ...d).toFixed(1), slow: d.filter((v) => v > 24).length, dpr: devicePixelRatio, hidden: document.hidden }).catch(() => {});
      }
    };
    requestAnimationFrame(tick);
  }
  function change(next) {
    if (next === state) { apply(next); return; }          // nothing to animate: it is already this size
    probe(`${state}->${next}`, 600);
    // The browser can morph one state of the pill into the next by itself (View Transitions): it keeps a picture
    // of the old pill and the live new one and animates the group between them on the GPU, with the spring in
    // notch.css. Where that is not available the animation below does the same by hand.
    if (document.startViewTransition && !host.reduced() && el.shell.offsetWidth) {
      const closing = RANK[next] < RANK[state];
      document.documentElement.toggleAttribute("data-nclosing", closing);
      transitioning = true;
      let t;
      try { t = document.startViewTransition(() => { apply(next); }); } catch { t = null; }
      if (t) {
        const end = () => {
          transitioning = false; document.documentElement.removeAttribute("data-nclosing"); host.hot();
          hovering = el.shell.matches(":hover");     // the pointer events that came while the browser was morphing were not reliable: ask again
          evaluate();
        };
        // A morph the browser skips (the page was not being drawn) rejects these promises: that is fine, the change is applied.
        t.ready.catch(() => {}); t.updateCallbackDone.catch(() => {});
        t.finished.then(end, end);
        setTimeout(() => { if (transitioning) end(); }, 900);     // never stuck, even if the page was not drawing
        return;
      }
      transitioning = false;
    }
    changeByHand(next);
  }
  function changeByHand(next) {
    const before = sizeOf(), r0 = radiusOf(state), r1 = radiusOf(next);
    const grows = RANK[next] >= RANK[state];
    if (host.reduced() || !before.w) { apply(next); return; }
    if (grows) {
      apply(next);
      const after = sizeOf();
      shapeTo(before, after, OPEN_MS, "cubic-bezier(.22, 1.1, .36, 1)", true, r0, r1);
      // The parts of the room rise into place one after another once the shape is wide enough for them.
      parts().forEach((node, i) => run(node, [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }],
        { duration: 260, delay: 150 + i * 55, easing: "cubic-bezier(.2, 1, .3, 1)", fill: "backwards" }));
      run($("#nbar"), [{ opacity: 0.4 }, { opacity: 1 }], { duration: 220, easing: "ease-out" });
    } else {
      const old = state;
      el.notch.dataset.n = next; const after = sizeOf(); el.notch.dataset.n = old;      // measure where it is going
      transitioning = true;
      const letGo = parts().map((node, i) => run(node, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-6px)" }],
        { duration: LET_GO_MS, delay: i * 25, easing: "ease-in", fill: "forwards" }));
      const anim = shapeTo(before, after, CLOSE_MS, "cubic-bezier(.45, 0, .2, 1)", false, r0, r1);
      let finished = false;
      const done = () => { if (finished) return; finished = true; transitioning = false; evaluate(); };
      // Part way through, the layout switches to the small one underneath: the shape is already animating on
      // explicit sizes, and the parts it hides have already let go.
      setTimeout(() => { letGo.forEach((a) => a && a.cancel()); apply(next); run($("#nbar"), [{ opacity: 0.5 }, { opacity: 1 }], { duration: 200, easing: "ease-out" }); }, LET_GO_MS + 60);
      if (anim) anim.onfinish = done;
      setTimeout(done, CLOSE_MS + 120);          // never stuck half-closed, even if the page was not drawing (animations pause then)
    }
  }

  function apply(next) {
    state = next;
    wardrobeSurface(next);
    el.notch.dataset.n = next;
    el.room.hidden = next !== "room";
    el.close.hidden = next !== "room";
    host.resizeStage();
    if (next !== "room") { chosenHome = false; if (viewCtl) { viewCtl.destroy(); viewCtl = null; view = "home"; } }
    if (next === "room") {
      if (view === "home" && !chosenHome) { const v = defaultView(); if (v !== "home") { showViewNow(v); autoView = true; } }
      paint(); refreshOverview(true);
      if (micro() && Date.now() - lastGreet > 600000 && host.reaction() !== "sleep") { lastGreet = Date.now(); host.fire("greet"); }   // a small hello when it opens, at most every ten minutes
    }
    host.fit();
    host.hot();
  }

  // ---- what is shown (the rules are in notch-state.js: pure, and tested) ----
  const facts = () => ({ status: status(), reaction: host.reaction(), attention: host.attention(), overview, doneUntil, now: Date.now() });
  const moodNow = () => glowMood(moodOf(facts()), cfg.glow);
  const wordNow = (mood) => { const m = musicView((status() || {}).media); return wordOf(facts(), mood, m && m.title); };

  function paintLine() {
    const mood = moodNow(), [word, extra] = wordNow(mood);
    setText(el.word, word);
    const now = new Date(), clock = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    const meta = extra || clock;
    if (el.meta.textContent !== meta) el.meta.textContent = meta;
    if (el.notch.dataset.m !== mood) {
      el.notch.dataset.m = mood;
      if (micro() && lastMood !== mood) {
        const dot = el.notch.querySelector(".ndot"); dot.classList.remove("ping"); void dot.offsetWidth; dot.classList.add("ping");
        if (["alert", "due", "soon"].includes(mood)) wobble();
      }
      lastMood = mood;
    }
    if (micro() && clock !== lastMinute) { if (lastMinute) { el.meta.classList.remove("tick"); void el.meta.offsetWidth; el.meta.classList.add("tick"); } }
    lastMinute = clock;
    // A colour of your own for the glow (when the glow is "the character's"), except while something needs you.
    if (cfg.glow === "character" && cfg.colour && !mood) el.notch.style.setProperty("--glow", cfg.colour); else el.notch.style.removeProperty("--glow");
    const s = status() || {};
    if (lastW === "work" && !(s.job && s.job.running)) { doneUntil = Date.now() + 9000; el.notch.classList.remove("flash"); void el.notch.offsetWidth; el.notch.classList.add("flash"); }
    lastW = s.job && s.job.running ? "work" : "";
  }

  function wobble() { el.shell.classList.remove("wobble"); void el.shell.offsetWidth; el.shell.classList.add("wobble"); setTimeout(() => el.shell.classList.remove("wobble"), 750); }

  let shownSay = "";
  function paintSay() {
    const n = $("#nsay");
    if (!n) return;
    const s = status() || {}, asked = s.pending_action && s.pending_action.text;
    const said = replyText || host.display(s.last_reply);
    const line = hint || (asked ? `${asked.charAt(0).toUpperCase()}${asked.slice(1)}?` : said || nextText || "Say “Hey Buddy”, or type below.");
    setText(n, line);
    n.classList.toggle("quiet", !hint && !asked && !said);
    if (micro() && line !== shownSay && !hint) { n.classList.remove("in"); void n.offsetWidth; n.classList.add("in"); }
    shownSay = line;
    $("#nask").hidden = !asked;
    if (asked) $("#nask-t").textContent = "Waiting for your OK";
  }

  function paintActs() {
    const g = $("#nacts");
    if (!g) return;
    const u = uses();
    const ranked = [...buttonsOn()].sort((a, b) => (u[b.id] || 0) - (u[a.id] || 0) || ACTIONS.indexOf(a) - ACTIONS.indexOf(b));
    const SHOWN = 7;      // the ones used most; the rest are one tap away
    const list = showAllTools || ranked.length <= SHOWN + 1 ? ranked : ranked.slice(0, SHOWN);
    const hiddenCount = ranked.length - list.length;
    const moreBtn = ranked.length <= SHOWN + 1 ? "" :
      `<button class="nbtn icon" type="button" data-more aria-expanded="${showAllTools}" aria-label="${showAllTools ? "Fewer tools" : `${hiddenCount} more tools`}" title="${showAllTools ? "Fewer tools" : `${hiddenCount} more tools`}">${ICON.more}</button>`;
    g.innerHTML = list.map((a) => {
      const label = labelOf(a), pressed = a.pressed ? ` aria-pressed="${a.pressed()}"` : "";
      return `<button class="nbtn icon" type="button" data-act="${a.id}"${pressed} aria-label="${esc(label)}" title="${esc(label)}">${ICON[a.icon || a.id.replace(/\d$/, "")] || ""}<span>${esc(label)}</span></button>`;
    }).join("") + moreBtn + routines.slice(0, 3).map((r) => `<button class="nbtn" type="button" data-routine="${esc(r.id)}" aria-label="Run ${esc(r.name)}">${ICON.run}${esc(r.name)}</button>`).join("");
  }

  // What is coming up is the line in the bar while nothing was said.
  function paintNext() {
    const s = status() || {}, bits = [];
    const m = musicView(s.media);
    if (m && m.playing) bits.push(`♪ ${m.title}${m.artist ? ` · ${m.artist}` : ""}`.slice(0, 46));
    for (const t of (overview && overview.timers) || []) bits.push(`${t.label || "Timer"} ${Math.floor(t.remaining_s / 60)}:${String(Math.round(t.remaining_s % 60)).padStart(2, "0")}`);
    const r = overview && overview.next_reminder;
    if (r && r.due_date) bits.push(`${String(r.title).slice(0, 30)} ${whenText(new Date(r.due_date))}`);
    if (s.focus) bits.push("Focus sprint running");
    nextText = bits.length ? `Coming up: ${bits.slice(0, 3).join("  ·  ")}` : "";
    paintSay();
  }

  function paintChips() {
    const s = status() || {}, set = settings() || {};
    const on = { disc: !!(s.media && s.media.playing), glass: !!s.focus, heat: !!(s.pc && s.pc.strain), mic: set.microphone_on !== false && host.reaction() === "listen" };
    for (const id of Object.keys(on)) { const c = $(`#c-${id}`); if (c) c.classList.toggle("on", on[id]); }
  }

  // Progress for something that really happened: a small "+10" rises from the face and the level ring catches up.
  // It is seen once per award (the live feed carries the newest award's id) and never on the first status after a start.
  function paintLevel() {
    const n = $("#nlevel");
    if (!n) return;
    if (!rewards || !rewards.xp) { n.hidden = true; return; }
    n.hidden = false;
    n.style.setProperty("--p", String(levelProgress(rewards)));
    $("#nlevel-t").textContent = `Lv ${rewards.level}`;
    n.title = `Level ${rewards.level}: ${rewards.into_level} of ${rewards.per_level} xp` + (rewards.streak ? `, ${rewards.streak}-day streak` : "");
  }
  function celebrate(fr) {
    const f = document.createElement("span");
    f.className = "nreward";
    f.textContent = `+${fr.xp}`;
    f.title = fr.label || "";
    el.shell.append(f);
    setTimeout(() => f.remove(), 1700);
    el.notch.classList.remove("proud"); void el.notch.offsetWidth; el.notch.classList.add("proud");
    setTimeout(() => el.notch.classList.remove("proud"), 1200);
    api.get("/ui/rewards").then((r) => { if (r && r.ok) { rewards = r; paintLevel(); } }).catch(() => {});
    say(`${fr.label || "Done"} · +${fr.xp} xp`);
  }
  function seeReward(status) {
    const fr = freshReward(status, rewardSeen);
    if (!fr) return false;
    rewardSeen = fr.id;
    if (fr.first) return false;
    celebrate(fr);
    return fr;
  }

  function paint() { if (!mounted || state !== "room") return; paintSay(); paintActs(); paintNext(); paintChips(); paintLevel(); }

  async function refreshOverview(force = false) {
    if (!force && Date.now() - overviewAt < OVERVIEW_EVERY_MS) return;
    overviewAt = Date.now();
    const [o, r, w] = await Promise.all([api.get("/ui/overview").catch(() => null), state === "room" ? api.get("/ui/routines").catch(() => null) : null,
      state === "room" ? api.get("/ui/rewards").catch(() => null) : null]);
    if (o && o.ok) overview = o;
    if (r && r.ok) routines = r.routines || [];
    if (w && w.ok) rewards = w;
    paintLine();
    paint();
  }

  return {
    mount, evaluate, toggleRoom, stageSize, configure, decorate, setPreview,
    get active() { return mounted; },
    get state() { return state; },
    celebrate: (status) => (mounted ? seeReward(status) : false),
    onStatus() { if (!mounted) return; paintLine(); if (state === "room") { paintChips(); paintSay(); if (viewCtl) viewCtl.update(status()); } evaluate(); },
    hotEls: () => (mounted ? [el.inner] : []),   // the contents box: always the size it is growing to or closing from
    tick() { if (mounted) { paintLine(); if (state === "room") { paintChips(); paintNext(); } refreshOverview(); evaluate(); } },
  };
}
