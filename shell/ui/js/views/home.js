// Home: the buddy and what it last said, how the PC is doing, the job in
// progress, what is playing, the reminders, the conversation (voice and
// typing), and what MBUDDY really did. Everything shown comes from the
// backend; a card with nothing to show says so.
import { api, chatTurn, reactionFor, stateLabel, displayText } from "../api.js";
import { mountLive } from "../buddy/avatar.js";
import { pcStrip, workCard, musicCard, remindersCard } from "../buddy/live-cards.js";
import { esc, fmtSecs, switchRow, wireSettings } from "./ui.js";

let timer = null, off = null, reminders = null;

export default {
  async render(el, ctx) {
    const hour = new Date().getHours();
    const greet = hour < 5 ? "Up late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    const name = (ctx.settings.user_name || "").trim();
    el.innerHTML = `
      <div class="top"><div><h1>${esc(greet)}${name ? `, ${esc(name)}` : ""}</h1>
        <p class="sub">Everything MBUDDY is doing and everything it has for you, in one place.</p></div>
        <span class="chip" id="h-chip"><span class="dot"></span><span id="h-state"></span></span></div>
      <div class="cards">
        <section class="lcard hero c12" aria-label="MBUDDY">
          <div class="hrow"><div class="stage" id="h-stage"></div>
            <div class="hbody">
              <p class="say" id="h-say" aria-live="polite"></p>
              <div class="pcs" id="h-pc" hidden></div>
              <form class="composer" id="h-form">
                <label for="h-input" class="faint" style="position:absolute;width:1px;height:1px;overflow:hidden">Message MBUDDY</label>
                <input id="h-input" autocomplete="off" placeholder="Ask or tell MBUDDY something" maxlength="1000">
                <button class="btn accent icon" type="submit" aria-label="Send"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>
              </form>
            </div></div>
        </section>
        <section class="lcard c12 ask" id="h-ask" hidden role="alertdialog" aria-labelledby="h-ask-t">
          <h3>Waiting for your OK</h3>
          <b id="h-ask-t"></b>
          <div class="row"><button class="btn accent" id="h-yes" type="button">Yes, do it</button><button class="btn" id="h-no" type="button">No</button></div>
        </section>
        <section class="lcard c6" id="h-work" aria-label="Right now"></section>
        <section class="lcard c6" id="h-music" aria-label="Now playing"></section>
        <section class="lcard c12" id="h-rem" aria-label="Reminders"></section>
        <section class="lcard c7">
          <h3>Conversation</h3>
          <div class="chat" id="h-chat" aria-live="polite"></div>
        </section>
        <div class="c5 stack">
          <section class="lcard pc-only">
            <h3>Controls</h3>
            ${switchRow("microphone_on", "Microphone", ctx.settings.microphone_on === false ? "Off: MBUDDY hears nothing. Typing works." : "", ctx.settings.microphone_on !== false)}
            <div class="stack" style="gap:6px">
              <div class="row between"><b>PC volume</b><span id="h-vol-v" class="muted">…</span></div>
              <input type="range" class="slider" id="h-vol" min="0" max="100" aria-label="PC volume">
            </div>
            <div id="h-timers" class="list"></div>
          </section>
          <section class="lcard" id="h-prog" aria-label="Progress" hidden></section>
          <section class="lcard">
            <h3>What I did <a href="#/activity" class="btn small ghost">See all</a></h3>
            <div id="h-actions" class="list"></div>
          </section>
        </div>
      </div>`;
    const $ = (id) => el.querySelector(`#${id}`);

    const cards = [pcStrip($("h-pc")), workCard($("h-work")), musicCard($("h-music"))];
    reminders = remindersCard($("h-rem"));
    cards.push(reminders);

    const chat = $("h-chat");
    // The conversation is the backend's record, so it survives this window
    // being closed while the buddy keeps talking.
    const lines = [];
    const draw = () => {
      chat.innerHTML = lines.length ? lines.map((l) => `<div class="bubble ${l.who}">${esc(l.text)}</div>`).join("")
        : `<div class="empty">Nothing said yet. Say “Hey Buddy”, or type above.</div>`;
      chat.scrollTop = chat.scrollHeight;
    };
    const push = (who, text) => {
      text = displayText(text);
      if (!text) return;
      const last = lines[lines.length - 1];
      if (last && last.who === who && last.text === text) return;
      // A reply arrives in growing pieces while it streams: extend the bubble.
      if (last && last.who === who && who === "me" && text.startsWith(last.text)) { last.text = text; draw(); return; }
      lines.push({ who, text }); if (lines.length > 60) lines.shift();
      draw();
    };
    draw();
    const past = await api.get("/ui/conversation?limit=60").catch(() => null);
    for (const t of (past && past.ok && past.turns) || []) { push("you", t.you); push("me", t.me); }

    // Spoken turns arrive through the live status (heard / replied).
    let lastHeard = ctx.status?.last_heard, lastReply = ctx.status?.last_reply;
    const live = () => {
      const s = ctx.status;
      const [label, cls] = stateLabel(s, ctx.connected);
      $("h-chip").className = "chip " + cls;
      $("h-state").textContent = label;
      mountLive($("h-stage"), ctx.settings.character, reactionFor(s), ctx.settings);
      // What it last said; before anything was said, how to start.
      const said = displayText(s && s.last_reply) || (lines.length && lines[lines.length - 1].who === "me" ? lines[lines.length - 1].text : "");
      $("h-say").textContent = said || "Say “Hey Buddy”, or type below.";
      $("h-say").classList.toggle("quiet", !said);
      for (const card of cards) card.update(s);
      // A PC action MBUDDY asked about: nothing happens until you say yes (here or out loud).
      const ask = s && s.pending_action && s.pending_action.text;
      $("h-ask").hidden = !ask;
      if (ask) $("h-ask-t").textContent = ask.charAt(0).toUpperCase() + ask.slice(1) + "?";
      if (s && s.last_heard && s.last_heard !== lastHeard) { lastHeard = s.last_heard; push("you", s.last_heard); }
      if (s && s.last_reply && s.last_reply !== lastReply) { lastReply = s.last_reply; push("me", s.last_reply); }
    };
    live();
    off = ctx.on(live);

    $("h-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = $("h-input");
      const text = input.value.trim();
      if (!text) return;
      input.value = ""; push("you", text); lastHeard = text;
      const r = await chatTurn(text, ctx.settings.speak_replies, () => push("me", "Still working on it… I'll answer here when it's done."));
      if (r && r.ok) { lastReply = r.reply; push("me", r.reply); }
      else if (r && r.error === "too_slow") push("me", "This one is taking far too long, so I stopped waiting. Check Now for what happened.");
      else if (r && r.error === "interrupted") push("me", "Something else came in before I could answer that. Ask me again?");
      else push("me", r && r.error && r.error.includes("No active session")
        ? "My voice isn't connected yet, so I can't answer. Check Status."
        : "I couldn't answer that just now.");
      live();
    });

    const answer = async (decision) => {
      const r = await api.post("/ui/pending-action", { decision }).catch(() => null);
      ctx.toast((r && (r.result || r.error)) || "Done.");
    };
    $("h-yes").addEventListener("click", () => answer("yes"));
    $("h-no").addEventListener("click", () => answer("no"));
    wireSettings(el, ctx, (key, on) => key === "microphone_on" &&
      ctx.toast(on ? "Microphone on." : "Microphone off. MBUDDY hears nothing until you switch it back on."));

    const vol = $("h-vol"), volV = $("h-vol-v");
    let dragging = false;
    vol.addEventListener("input", () => { dragging = true; volV.textContent = vol.value + "%"; });
    vol.addEventListener("change", async () => {
      const r = await api.post("/ui/system-volume", { level: Number(vol.value) }).catch(() => null);
      dragging = false;
      if (!r || !r.ok) ctx.toast((r && r.error) || "Couldn't change the volume.");
    });

    const refresh = async () => {
      if (document.hidden) return;  // minimized: nothing to show, don't ask Windows for the volume
      const o = await api.get("/ui/overview").catch(() => null);
      if (!o || !o.ok) return;
      if (!dragging) {
        if (o.volume == null) { volV.textContent = "unavailable"; vol.disabled = true; }
        else { vol.disabled = false; vol.value = o.volume; volV.textContent = o.volume + "%"; }
      }
      $("h-timers").innerHTML = o.timers.map((t) =>
        `<div class="item"><div class="grow"><div class="t">${esc(t.label)}</div></div><b>${fmtSecs(t.remaining_s)}</b></div>`).join("");
      $("h-actions").innerHTML = o.recent_actions.length
        ? o.recent_actions.slice().reverse().slice(0, 5).map((a) => `<div class="item"><span class="faint" style="font-size:12px">${esc(a.time)}</span><div class="grow s">${esc(a.result)}</div></div>`).join("")
        : `<div class="empty">Nothing yet. When I set a timer, change the volume or save something, it shows here.</div>`;
    };
    // Progress for what really happened (tasks done, focus sprints, habits kept). Hidden until there is some.
    const drawProgress = (r) => {
      const card = $("h-prog");
      if (!r || !r.ok || !r.xp) { card.hidden = true; return; }
      const earned = r.badges.filter((b) => b.earned), locked = r.badges.filter((b) => !b.earned);
      card.hidden = false;
      card.innerHTML = `<h3>Progress</h3>
        <div class="prog"><div class="prog-ring" style="--p:${(r.into_level / r.per_level).toFixed(3)}" role="img" aria-label="Level ${r.level}, ${r.into_level} of ${r.per_level} xp"><b>${r.level}</b><small>level</small></div>
          <div class="prog-body"><div><b>${r.into_level}</b> <span class="muted">of ${r.per_level} xp to level ${r.level + 1}</span></div>
            <div class="muted">${r.streak ? `${r.streak}-day streak` : "No streak yet"}${r.best_streak > r.streak ? ` · best ${r.best_streak}` : ""}</div>
            <div class="muted">${r.totals.task_done} tasks · ${r.totals.focus_sprint} sprints · ${r.totals.habit} habits</div></div></div>
        <div class="prog-badges">${earned.map((b) => `<span class="chip" title="${esc(b.how)}">${esc(b.name)}</span>`).join("")}${locked.slice(0, 3).map((b) => `<span class="chip off" title="${esc(b.how)}">${esc(b.name)}</span>`).join("")}</div>
        ${r.recent.length ? `<div class="list">${r.recent.slice(0, 3).map((a) => `<div class="item"><div class="grow s">${esc(a.label)}</div><b>+${a.xp}</b></div>`).join("")}</div>` : ""}`;
    };
    let progressTick = 0;
    const refreshProgress = async () => { drawProgress(await api.get("/ui/rewards").catch(() => null)); };
    await refresh();
    refreshProgress();
    timer = setInterval(() => { refresh(); if (++progressTick % 5 === 0) refreshProgress(); }, 3000);
  },
  destroy() { clearInterval(timer); off && off(); reminders && reminders.destroy(); },
};
