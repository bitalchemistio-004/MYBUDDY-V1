// Buddy: the conversation, and nothing else in the way. Only the current conversation is shown (since "New chat", or the last few hours),
// at most a screenful, with "Earlier" to open more; new turns are added on their own instead of redrawing the thread. Empty, it shows the
// buddy, a greeting, what is next today and three things to try. Receipts under an answer open what was done; holding an answer copies it.
import { reactionFor } from "../../api.js";
import { mountLive } from "../../buddy/avatar.js";
import { esc, clock, timeOf, dayLabel } from "../util.js";
import { charPicker } from "./shared.js";

let tick = null, off = null, offStore = null, privTimer = null;

const TRY = [
  "What's the weather in Beirut tomorrow?",
  "Remind me to call Baba at 6pm",
  "Open YouTube and play lofi beats",
  "Who won the last Champions League final?",
  "Add a note: Ali likes green tea",
  "Set a 10 minute timer",
  "Tell me something surprising about octopuses",
  "What's 18% of 2,450?",
];
const PAGE = 10;                     // turns shown at once; "Earlier" opens ten more
const SESSION_GAP = 3 * 3600_000;    // a conversation older than this is "earlier"
const RECEIPT = (d) => /^Looked up/.test(d) ? "search" : /^(Working on it|Open)/.test(d) ? "app" : /^(Timer|Alarm|Focus)/.test(d) ? "clock"
  : /^(Message|WhatsApp|Call)/.test(d) ? "chat" : /^Task/.test(d) ? "task" : /^Event/.test(d) ? "event" : /note/i.test(d) ? "note" : "done";
const GOES = { task: "do", event: "today", note: "remember" };
const ICON = {
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  app: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  chat: "M4 5h16v11H8l-4 4z",
  task: "M5 12l5 5L20 7",
  event: "M7 3v3M17 3v3M4 9h16M5 5h14v15H5z",
  note: "M6 3h9l4 4v14H6zM9 12h6M9 16h6",
  done: "M5 12l5 5L20 7",
};
const icon = (k) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICON[k]}"/></svg>`;
const buzz = (ms = 8) => { try { navigator.vibrate && navigator.vibrate(ms); } catch { /* not everywhere */ } };

import { coach } from "../coach.js";
import { tipHtml, wireTips } from "../tips.js";
export default {
  render(el, ctx) {
    const { store, brain } = ctx;
    let shown = new Set(), extra = 0, lastPending = null;

    const greeting = () => {
      const s = store.settings.get(), h = new Date().getHours();
      return `${h < 5 ? "Up late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"}${s.user_name ? ", " + s.user_name : ""}`;
    };
    const next = () => {
      const now = store.dayKey() + "T" + new Date().toTimeString().slice(0, 5);
      const items = [...store.events.list().map((e) => ({ at: e.start, t: e.title })), ...store.tasks.list().filter((t) => !t.done && t.when).map((t) => ({ at: t.when, t: t.title }))]
        .filter((i) => i.at >= now).sort((a, b) => a.at.localeCompare(b.at));
      return items[0] || null;
    };
    // the current conversation: since "New chat", and not older than a few hours; the rest is "earlier"
    const session = () => {
      const all = store.convo.list(200), since = Math.max(store.settings.get().chat_since || 0, Date.now() - SESSION_GAP);
      const cur = all.filter((c) => (c.at || 0) >= since);
      const older = all.length - cur.length;
      return { all, cur, older };
    };

    // the first thing a new person sees when no brain is set up: three honest ways, with what each costs, and the cloud one done here
    const setupCard = () => `<section class="pa-setup" aria-label="Give me a brain">
      <h2>Give me a brain</h2>
      <p>Timers, tasks, notes and calls already work. To think and answer questions I need one of these. You can change it any time in Settings.</p>
      <div class="pa-way"><b>In the cloud <em>fastest, about 1 to 2 seconds</em></b>
        <p>Your words go to Ollama's cloud to be answered; your lists stay on this phone. Free to start.</p>
        <ol><li>Open the page below and sign in.</li><li>Make a key.</li><li>Copy it, then paste it here.</li></ol>
        <button class="pa-btn" id="su-open" type="button">Open ollama.com/settings/keys</button>
        <div class="pa-keyrow"><input class="pa-field" id="su-key" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Paste the key" aria-label="Key"><button class="pa-btn" id="su-paste" type="button">Paste</button></div>
        <button class="pa-btn go" id="su-save" type="button">Save and start</button><p class="pa-note" id="su-out"></p></div>
      <div class="pa-way"><b>On this phone <em>private, works offline</em></b>
        <p>A download of about 1 GB (use Wi-Fi). Slower: about 8 seconds a step, and the phone works harder while it thinks.</p>
        <button class="pa-btn" id="su-offline" type="button">Set up the offline brain</button></div>
      <div class="pa-way"><b>On your PC <em>private, uses your computer</em></b>
        <p>If MBUDDY runs on your computer, pair this phone with it.</p>
        <button class="pa-btn" id="su-pc" type="button">Pair with my PC</button></div></section>`;

    // screen control, explained before Android's own screen asks: what it is, what it never does, what to expect
    const controlSheet = () => ctx.sheet((body, close) => {
      body.insertAdjacentHTML("beforeend", `<h2>Let me work inside other apps</h2>
        <p class="pa-note">Screen control lets me tap and type for you in other apps, only while a task you gave me is running.</p>
        <h3 class="pa-day">What I do</h3><ul class="pa-points"><li>Open an app and do the steps you asked for, one at a time, showing you each step.</li><li>Read back what I wrote and wait for your yes before sending, paying, deleting or calling.</li></ul>
        <h3 class="pa-day">What I never do</h3><ul class="pa-points"><li>Operate banking, payment or password apps.</li><li>Run by myself, or record the screen.</li></ul>
        <h3 class="pa-day">What Android will show</h3><p class="pa-note">A red warning. Android shows it for any app that can control the screen; it is not specific to me. You can switch this off at any time, and Android switches it off after an update (I will tell you).</p>
        <ol class="pa-points"><li>Tap the button below.</li><li>Open Installed apps or Downloaded apps.</li><li>Choose MBUDDY screen control and switch it on.</li></ol>
        <button class="pa-btn go" id="c-open" type="button">Open Android settings</button><button class="pa-btn" id="c-no" type="button">Not now</button>`);
      body.querySelector("#c-open").addEventListener("click", () => { close(); try { window.Native.openControlSettings(); } catch { /* not in the app */ } });
      body.querySelector("#c-no").addEventListener("click", close);
    });

    // "Your week": the last seven days from what really happened, with the buddy's words about it
    const recapCard = () => {
      const r = store.recap();
      if (!(r.tasksDone || r.wins || r.notesAdded)) return "";
      const max = Math.max(1, ...r.days.map((d) => r.perDay[d]));
      const day = (d) => new Date(d + "T12:00").toLocaleDateString([], { weekday: "long" });
      const parts = [];
      if (r.tasksDone) parts.push(`finished ${r.tasksDone} task${r.tasksDone === 1 ? "" : "s"}${r.bestDay && r.tasksDone > 1 ? `, most on ${day(r.bestDay)} (${r.bestCount})` : ""}`);
      if (r.wins) parts.push(`won ${r.wins} game${r.wins === 1 ? "" : "s"}`);
      if (r.notesAdded) parts.push(`kept ${r.notesAdded} note${r.notesAdded === 1 ? "" : "s"}`);
      const mood = r.tasksDone >= 8 || r.wins >= 5 ? "streak" : r.tasksDone >= 3 || r.wins >= 2 ? "good" : "start";
      const line = coach(store.settings.get().character, mood);
      return `<section class="pa-recap" aria-label="Your week">
        <div class="pa-recap-head"><b>Your week</b>${r.streak ? `<span>${r.streak} day streak</span>` : ""}</div>
        <div class="pa-recap-bars" role="img" aria-label="Tasks finished each day">${r.days.map((d) => `<div><i style="--h:${Math.max(6, Math.round((100 * r.perDay[d]) / max))}%"></i><span>${new Date(d + "T12:00").toLocaleDateString([], { weekday: "narrow" })}</span></div>`).join("")}</div>
        <p>You ${parts.join(", ")}.</p><p class="pa-recap-line">${esc(line)}</p></section>`;
    };

    const draw = () => {
      const s = store.settings.get(), bs = brain.brainState(), { cur } = session();
      const picks = TRY.slice().sort(() => Math.random() - 0.5).slice(0, 3);
      const n = next();
      let ctl = ""; try { const a = window.Native && window.Native.agentStatus ? JSON.parse(window.Native.agentStatus()) : null; if (a && !(a.enabled && a.connected)) ctl = a.wasOn ? `<button class="pa-banner" id="b-ctl" type="button"><b>Screen control is off.</b><span>Android turns it off when the app is updated. Tap to turn it back on.</span></button>`
        : `<button class="pa-banner calm" id="b-ctl" type="button"><b>Let me work inside other apps</b><span>Optional. Tap to see what it does, what it never does, and how to switch it on.</span></button>`; } catch { /* not in the app */ }
      let priv = ""; try { const p = window.Native && window.Native.privacyState ? JSON.parse(window.Native.privacyState()) : null; if (p && p.on) priv = `<button class="pa-banner priv" id="b-priv" type="button"><b>Privacy mode is on.</b><span>I am not looking at the screen or listening. Tap to turn it off.</span></button>`; } catch { /* not in the app */ }
      el.innerHTML = `
        ${priv}
        <div class="pa-running" id="b-run" hidden></div>
        ${s.onboarding_done ? "" : `<section class="pa-hello">
          <h2>Hi, I'm your buddy.</h2><p class="pa-note">What should I call you? Pick who you'd like me to be; you can change it later.</p>
          <input class="pa-field" id="b-name" placeholder="Your name" maxlength="40" value="${esc(s.user_name)}" aria-label="Your name">
          <div id="b-chars"></div><button class="pa-btn go" id="b-go" type="button">Start</button></section>`}
        ${ctl}
        ${bs.cloud || bs.pc || bs.phone ? "" : setupCard()}
        <div class="pa-empty-state${!s.onboarding_done || !(bs.cloud || bs.pc || bs.phone) ? " compact" : ""}" id="b-empty" ${cur.length ? "hidden" : ""}>
          <div class="pa-stage" id="b-stage" aria-hidden="true"></div>
          <h1 class="pa-greet">${esc(greeting())}</h1>
          <p class="pa-sub">${n ? `Next: <b>${esc(n.t)}</b> · ${esc(dayLabel(n.at.slice(0, 10)))} ${esc(timeOf(n.at))}` : "Ask me anything, or tell me what to do."}</p>
          ${recapCard()}
          ${store.rewards.state().earned >= 1 ? tipHtml(store, "widgets", "Press and hold your home screen, choose Widgets, then MBUDDY: tasks you can tick, a bar to ask, and what is next.") : ""}
          <h3 class="pa-day">${ctx.frequent().real ? "You use these most" : "Try one"}</h3>${ctx.chipsHtml(ctx.frequent().list)}
        </div>
        <div class="pa-thread-bar" id="b-bar" hidden><button type="button" class="pa-earlier" id="b-earlier"></button><button type="button" class="pa-newchat" id="b-new">New chat</button></div>
        <div class="pa-thread" id="b-thread" aria-live="polite"></div>`;
      const $ = (id) => el.querySelector("#" + id);
      if (!s.onboarding_done) {
        for (const ms of [0, 250, 700]) setTimeout(() => { const sc = document.getElementById("pa-content"); if (sc && !session().cur.length) sc.scrollTop = 0; }, ms);       // the first screen starts at its top
        let chosen = s.character;
        charPicker($("b-chars"), chosen, (c) => { chosen = c; store.settings.set({ character: c }); });
        $("b-go").addEventListener("click", () => store.settings.set({ user_name: $("b-name").value.trim(), character: chosen, onboarding_done: true }));
      }
      const goPriv = $("b-priv"); if (goPriv) goPriv.addEventListener("click", () => { try { window.Native.setPrivacy(false, 0); } catch { /* not in the app */ } draw(); });
      const goCtl = $("b-ctl"); if (goCtl) goCtl.addEventListener("click", controlSheet);
      wireTips(el, store);
      const su = $("su-save");
      if (su) {
        // the new person starts at the top of the choices (after the layout has settled, because focus and fonts move it)
        for (const ms of [0, 250, 700]) setTimeout(() => { const sc = document.getElementById("pa-content"); if (sc && !session().cur.length) sc.scrollTop = 0; }, ms);
        const out = $("su-out"), say = (t, bad) => { out.textContent = t; out.className = "pa-note " + (bad ? "bad" : "good"); };
        $("su-open").addEventListener("click", () => { const u = "https://ollama.com/settings/keys"; try { if (window.Native && window.Native.openLink) { window.Native.openLink(u, "Ollama keys"); return; } } catch { /* below */ } window.open(u, "_blank", "noopener"); });
        $("su-paste").addEventListener("click", () => { try { navigator.clipboard.readText().then((t) => { $("su-key").value = t.trim(); }).catch(() => say("Press and hold in the box to paste.", true)); } catch { say("Press and hold in the box to paste.", true); } });
        su.addEventListener("click", async () => {
          const key = $("su-key").value.trim(); if (!key) { say("Paste the key first.", true); return; }
          su.disabled = true; say("Checking the key...");
          let r = null; try { r = await (await fetch("/cloud/key", { method: "POST", body: JSON.stringify({ key }) })).json(); } catch { /* below */ }
          su.disabled = false;
          if (r && r.ok) { $("su-key").value = ""; say("Saved. I am ready."); setTimeout(() => location.reload(), 700); } else say((r && r.error) || "I could not check that key.", true);
        });
        $("su-offline").addEventListener("click", () => ctx.go("settings/brain"));
        $("su-pc").addEventListener("click", () => ctx.go("settings/brain"));
      }
      el.querySelectorAll("[data-try]").forEach((b) => b.addEventListener("click", () => { buzz(); b.classList.add("picked"); ctx.say(b.dataset.try); }));
      el.querySelectorAll("[data-say]").forEach((b) => b.addEventListener("click", () => { buzz(); ctx.say(b.dataset.say); }));
      $("b-earlier").addEventListener("click", () => { extra += PAGE; rebuild(); });
      $("b-new").addEventListener("click", () => {
        buzz(12);
        const t = $("b-thread"); t.classList.add("clearing");
        setTimeout(() => { store.settings.set({ chat_since: Date.now() }); }, 260);   // the settings change redraws the screen, empty
      });
      // receipts open what was done; holding an answer copies it
      $("b-thread").addEventListener("click", (e) => { const r = e.target.closest("[data-go]"); if (r) ctx.go(r.dataset.go); const s = e.target.closest("[data-src]"); if (s) ctx.bubble("answer", s.dataset.src); });
      let hold = null;
      $("b-thread").addEventListener("pointerdown", (e) => {
        const p = e.target.closest(".pa-me-say p"); if (!p) return;
        hold = setTimeout(() => { navigator.clipboard && navigator.clipboard.writeText(p.textContent).then(() => { buzz(14); p.classList.add("copied"); setTimeout(() => p.classList.remove("copied"), 700); ctx.toast("Copied."); }).catch(() => {}); }, 550);
      });
      ["pointerup", "pointercancel", "pointerleave"].forEach((ev) => $("b-thread").addEventListener(ev, () => clearTimeout(hold)));
      if ($("b-stage")) mountLive($("b-stage"), s.character, reactionFor(ctx.status), s);
      shown = new Set(); lastPending = null;
      rebuild();
      running();
    };

    // an answer's words come in one after another, quickly (the first eighty; a long answer does not keep you waiting)
    const words = (text, animate) => animate
      ? esc(text).split(/(\s+)/).map((w, i) => (/^\s+$/.test(w) ? w : i < 80 ? `<span class="w" style="--d:${Math.min(i, 80) * 9}ms">${w}</span>` : w)).join("")
      : esc(text);
    const turnHtml = (c, mode) => `<div class="pa-turn ${mode}" data-id="${c.id}">
        <div class="pa-you">${esc(c.you)}</div>
        ${c.me || (c.did && c.did.length) ? `<div class="pa-me-say">${c.me ? `<p>${words(c.me, mode !== "")}</p>` : ""}${c.sources && c.sources.length ? `<button type="button" class="pa-receipt" data-src="${esc(c.id)}">${icon("search")}<span>${c.sources.length} source${c.sources.length === 1 ? "" : "s"}: ${esc(c.sources[0].host || "web")}</span><svg class="go" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg></button>` : ""}${(c.did || []).map((d) => {
          const k = RECEIPT(d); return `<button type="button" class="pa-receipt" data-k="${k}" ${GOES[k] ? `data-go="${GOES[k]}"` : "disabled"}>${icon(k)}<span>${esc(d)}</span>${GOES[k] ? `<svg class="go" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>` : ""}</button>`; }).join("")}</div>` : ""}
      </div>`;

    const scrollDown = (smooth) => { if (el.querySelector("#su-save") && !session().cur.length) return; const sc = document.querySelector("#pa-content"); requestAnimationFrame(() => sc.scrollTo({ top: sc.scrollHeight, behavior: smooth ? "smooth" : "auto" })); };

    // the whole thread, drawn once (on opening, or after "Earlier")
    const rebuild = () => {
      const thread = el.querySelector("#b-thread"); if (!thread) return;
      const { all, cur } = session();
      const list = extra ? all.slice(-(cur.length + extra)) : cur.slice(-PAGE);
      thread.innerHTML = list.map((c) => turnHtml(c, "")).join("");
      shown = new Set(list.map((c) => c.id));
      bar();
      scrollDown(false);
    };
    const bar = () => {
      const { all, cur } = session(), visible = shown.size, hidden = all.length - visible;
      const b = el.querySelector("#b-bar"); if (!b) return;
      b.hidden = !(cur.length || extra);
      const e = el.querySelector("#b-earlier"); e.hidden = hidden <= 0; e.textContent = `Earlier · ${hidden}`;
      const empty = el.querySelector("#b-empty"); if (empty) empty.hidden = !!(cur.length || extra || ctx.status.pending);
    };
    // only what is new is added: a turn that was asked here replaces its "thinking" row; one from elsewhere slides in
    const addNew = () => {
      const thread = el.querySelector("#b-thread"); if (!thread) return;
      const { cur } = session();
      const fresh = cur.filter((c) => !shown.has(c.id));
      if (!fresh.length) return;
      const pend = thread.querySelector(".pa-pending");
      for (const c of fresh) {
        const html = turnHtml(c, pend && c === fresh[fresh.length - 1] ? "arrived" : "fresh");
        if (pend && c === fresh[fresh.length - 1]) pend.outerHTML = html; else thread.insertAdjacentHTML("beforeend", html);
        shown.add(c.id);
      }
      // keep the thread short: the oldest rows go once there are more than a screenful
      const rows = thread.querySelectorAll(".pa-turn:not(.pa-pending)");
      if (!extra && rows.length > PAGE) for (let i = 0; i < rows.length - PAGE; i++) { shown.delete(rows[i].dataset.id); rows[i].remove(); }
      buzz(6);
      bar();
      scrollDown(true);
    };
    // the "thinking" row: only when what is pending changes (not on every change of the buddy's state)
    const pending = () => {
      const st = ctx.status, p = st.pending || "";
      if (p === lastPending) return;
      lastPending = p;
      const thread = el.querySelector("#b-thread"); if (!thread) return;
      const row = thread.querySelector(".pa-pending");
      if (p && !row) {
        thread.insertAdjacentHTML("beforeend", `<div class="pa-turn fresh pa-pending"><div class="pa-you">${esc(p)}</div><div class="pa-me-say"><span class="pa-typing" aria-label="Thinking"><i></i><i></i><i></i></span></div></div>`);
        bar(); scrollDown(true);
      } else if (!p && row) setTimeout(() => { const r = thread.querySelector(".pa-pending"); if (r) r.remove(); bar(); }, 400);
    };

    const running = () => {
      const box = el.querySelector("#b-run"); if (!box) return;
      const timers = store.timers.list(), f = store.focus.state();
      const rows = [...(f ? [{ label: "Focus", left: (f.endsAt - Date.now()) / 1000 }] : []), ...timers.map((t) => ({ label: t.label, left: (t.endsAt - Date.now()) / 1000 }))];
      box.hidden = !rows.length;
      if (rows.length) box.innerHTML = rows.map((r) => `<span><i></i>${esc(r.label)} <b>${clock(r.left)}</b></span>`).join("");
    };

    draw();
    // the privacy banner follows the real mode, whoever changed it (the tile, the notification, Settings, a spoken command)
    let lastPriv = null;
    const privNow = () => { try { return !!(window.Native && window.Native.privacyState && JSON.parse(window.Native.privacyState()).on); } catch { return false; } };
    lastPriv = privNow();
    privTimer = setInterval(() => { if (!el.isConnected) return; const p = privNow(); if (p !== lastPriv) { lastPriv = p; draw(); } }, 1500);
    off = ctx.on(pending);
    offStore = store.onChange((what) => { if (what === "settings") draw(); else if (what === "convo") addNew(); else if (what === "timers" || what === "focus") running(); });
    tick = setInterval(running, 1000);
  },
  destroy() { clearInterval(tick); clearInterval(privTimer); off && off(); offStore && offStore(); },
};
