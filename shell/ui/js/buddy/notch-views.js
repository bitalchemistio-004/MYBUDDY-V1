// The notch's room shows one thing at a time. Pressing a button does not add to the pill: it shows that thing
// in the same place, with a way back. Each view is built from real state or real APIs:
//   music  the record, the song, the progress, the player controls (the media keys), the real output level
//          and the Windows volume
//   timer  a dial to pick how long (or the clock time to count down to), then the countdown of the real timer
//   task   a month to pick the day, a time, and what to do (typed or spoken: speech to text on this PC)
//   focus  a dial to pick the minutes of a focus sprint, then its countdown
// A view is { title, mount(box, h) -> { update(status), destroy() } }; `h` is what the notch gives it.
import { monthGrid } from "./cards.js";

const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const I = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
const clock = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = String(s % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`; };
const hhmm = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
const pad2 = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

// ---- a dial that picks minutes: drag the handle round the face, or press a number ----
function dial(el, { max, value, onChange }) {
  const C = 80, R = 62;
  el.innerHTML = `<svg viewBox="0 0 160 160" class="dial" role="slider" aria-label="Minutes" aria-valuemin="1" aria-valuemax="${max}" tabindex="0">
    <circle cx="${C}" cy="${C}" r="${R}" class="d-face"/><path class="d-arc" fill="none"/>
    ${Array.from({ length: 12 }, (_, i) => { const a = (i / 12) * 2 * Math.PI; const x1 = C + Math.sin(a) * (R - 2), y1 = C - Math.cos(a) * (R - 2), x2 = C + Math.sin(a) * (R - (i % 3 ? 6 : 10)), y2 = C - Math.cos(a) * (R - (i % 3 ? 6 : 10)); return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" class="d-tick"/>`; }).join("")}
    <text x="${C}" y="${C - R + 22}" class="d-num">60</text><text x="${C + R - 20}" y="${C + 4}" class="d-num">15</text><text x="${C}" y="${C + R - 14}" class="d-num">30</text><text x="${C - R + 20}" y="${C + 4}" class="d-num">45</text>
    <circle class="d-handle" r="7"/></svg>`;
  const svg = el.firstElementChild, arc = svg.querySelector(".d-arc"), handle = svg.querySelector(".d-handle");
  let v = value, dragging = false;
  const draw = () => {
    const frac = (Math.min(v, 60) % 60 || (v >= 60 ? 60 : 0)) / 60, a = frac * 2 * Math.PI;
    const x = C + Math.sin(a) * R, y = C - Math.cos(a) * R;
    arc.setAttribute("d", v >= 60 ? `M ${C} ${C - R} A ${R} ${R} 0 1 1 ${C - 0.01} ${C - R}` : `M ${C} ${C - R} A ${R} ${R} 0 ${frac > 0.5 ? 1 : 0} 1 ${x.toFixed(2)} ${y.toFixed(2)}`);
    handle.setAttribute("cx", x.toFixed(2)); handle.setAttribute("cy", y.toFixed(2));
    svg.setAttribute("aria-valuenow", String(v));
  };
  const at = (e) => {
    const r = svg.getBoundingClientRect(), dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
    let m = Math.round(((Math.atan2(dx, -dy) + 2 * Math.PI) % (2 * Math.PI)) / (2 * Math.PI) * 60);
    if (m === 0) m = v > 30 ? 60 : 1;
    v = Math.max(1, Math.min(max, m)); draw(); onChange(v);
  };
  svg.addEventListener("pointerdown", (e) => { dragging = true; svg.setPointerCapture(e.pointerId); at(e); });
  svg.addEventListener("pointermove", (e) => { if (dragging) at(e); });
  svg.addEventListener("pointerup", () => { dragging = false; });
  svg.addEventListener("keydown", (e) => { const d = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, PageUp: 5, PageDown: -5 }[e.key]; if (d) { e.preventDefault(); v = Math.max(1, Math.min(max, v + d)); draw(); onChange(v); } });
  draw();
  return { set(x) { v = Math.max(1, Math.min(max, x)); draw(); }, get value() { return v; } };
}

// ---- music ----
const music = {
  title: "Music",
  mount(box, h) {
    box.innerHTML = `<div class="mv">
      <div class="mv-disc" id="mv-disc" aria-hidden="true"><svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="58" class="v-body"/>${[50, 43, 36, 29, 22].map((r) => `<circle cx="60" cy="60" r="${r}" class="v-groove"/>`).join("")}<path d="M60 4a56 56 0 0 1 40 17" class="v-shine"/><circle cx="60" cy="60" r="17" class="v-label"/><circle cx="60" cy="60" r="3.5" class="v-hole"/></svg></div>
      <div class="mv-mid"><div class="mq mv-title" id="mv-title"></div><div class="mv-artist" id="mv-artist"></div>
        <div class="mv-prog"><span id="mv-t0">0:00</span><div class="mv-bar"><i id="mv-fill"></i></div><span id="mv-t1">0:00</span></div>
        <div class="mv-ctl"><button class="nbtn icon" data-m="previous" aria-label="Previous song" type="button">${I("M6 5v14M19 5v14L8 12z")}</button>
          <button class="nbtn icon big" data-m="toggle" aria-label="Play or pause" type="button" id="mv-pp">${I("M8 5v14l11-7z")}</button>
          <button class="nbtn icon" data-m="next" aria-label="Next song" type="button">${I("M18 5v14M5 5v14l11-7z")}</button></div></div>
      <div class="mv-side"><label class="mv-vol"><span>Volume</span><input type="range" min="0" max="100" id="mv-vol" aria-label="Volume"></label>
        <div class="mv-level" id="mv-level" role="img" aria-label="Output level">${Array.from({ length: 14 }, (_, i) => `<i style="height:${28 + i * 5}%"></i>`).join("")}</div><small id="mv-app" class="faint"></small></div>
      <p class="mv-empty" id="mv-empty" hidden>Nothing is playing. <button class="nbtn go" type="button" id="mv-play">Play some music</button></p></div>`;
    const $ = (s) => box.querySelector(s);
    let media = null, anchor = { pos: 0, at: 0 }, level = 0, alive = true, hadMedia = false, goneSince = 0, idleSince = 0;
    $(".mv").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-m]");
      if (!b) return;
      const r = await h.api.post("/ui/media", { command: b.dataset.m }).catch(() => null);   // the same buttons as the quick panel: they go to the player that is showing
      if (!r || !r.ok) h.say((r && r.error) || "I couldn't press that.");
    });
    $("#mv-play").addEventListener("click", () => h.chat("play some music"));
    const vol = $("#mv-vol");
    h.api.get("/ui/system-volume").then((r) => { if (r && r.level != null) vol.value = r.level; }).catch(() => {});
    vol.addEventListener("change", () => h.api.post("/ui/system-volume", { level: Number(vol.value) }).catch(() => {}));
    const dots = [...box.querySelectorAll("#mv-level i")];
    // The real output level, about seven times a second while this view is open.
    const poll = setInterval(async () => {
      const r = await h.api.get("/ui/audio-level").catch(() => null);
      level = r && r.level != null ? r.level : 0;
    }, 140);
    const paintLevel = setInterval(() => {
      const shown = Math.round(Math.min(1, Math.sqrt(level) * 1.15) * dots.length);
      dots.forEach((d, i) => d.classList.toggle("on", i < shown));
      if (media && media.duration_s > 0) {
        const pos = Math.min(media.duration_s, anchor.pos + (media.playing ? (Date.now() - anchor.at) / 1000 : 0));
        $("#mv-fill").style.width = `${(100 * pos / media.duration_s).toFixed(1)}%`;
        $("#mv-t0").textContent = clock(pos);
      }
    }, 140);
    return {
      update(status) {
        const m = status && status.media;
        // What was playing is gone (the player was closed, the tab shut): do not sit on an empty player. After a
        // moment of nothing (a track change can show nothing for a second) it says so and goes back to the tools.
        if (m) { hadMedia = true; goneSince = 0; }
        else if (hadMedia) {
          goneSince = goneSince || Date.now();
          if (Date.now() - goneSince > 2500) { h.say("The music stopped: the player was closed."); h.leave(); return; }
        }
        // It opened by itself because something was playing. The music has stopped and a paused player is all that is left (a
        // closed tab can stay "paused" for hours): after a moment it goes back to the tools instead of sitting on an idle player.
        // A player you opened yourself stays until you leave it.
        if (m && !m.playing && h.auto && h.auto()) {
          idleSince = idleSince || Date.now();
          if (Date.now() - idleSince > 6000) { h.leave(); return; }
        } else idleSince = 0;
        if (m && (!media || m.position_s !== media.position_s || m.title !== media.title)) anchor = { pos: m.position_s || 0, at: Date.now() };
        media = m || null;
        const has = !!m;
        $(".mv").classList.toggle("none", !has);
        $("#mv-empty").hidden = has;
        if (!has) return;
        h.setText($("#mv-title"), m.title || "Unknown");
        $("#mv-artist").textContent = m.artist || "";
        $("#mv-app").textContent = m.app || "";
        $("#mv-t1").textContent = m.duration_s > 0 ? clock(m.duration_s) : "";
        $("#mv-disc").classList.toggle("spin", !!m.playing);
        $("#mv-pp").innerHTML = I(m.playing ? "M7 5v14M17 5v14" : "M8 5v14l11-7z");
        $("#mv-pp").setAttribute("aria-label", m.playing ? "Pause" : "Play");
        box.querySelector(".mv-prog").hidden = !(m.duration_s > 0);
      },
      destroy() { alive = false; clearInterval(poll); clearInterval(paintLevel); },
    };
  },
};

// ---- timer ----
const timer = {
  title: "Timer",
  mount(box, h) {
    box.innerHTML = `<div class="tv"><div class="tv-dial" id="tv-dial"></div>
      <div class="tv-side"><div class="tv-read" id="tv-read">10:00</div><div class="tv-sub" id="tv-sub"></div>
        <div class="tv-set" id="tv-set"><div class="nrow" id="tv-chips">${[1, 5, 10, 15, 25, 45].map((m) => `<button class="nbtn" type="button" data-min="${m}">${m} min</button>`).join("")}</div>
          <label class="tv-until">Count down to <input type="time" id="tv-until" aria-label="Clock time to count down to"></label>
          <div class="nrow"><button class="nbtn icon" type="button" data-step="-1" aria-label="One minute less">${I("M5 12h14")}</button><button class="nbtn icon" type="button" data-step="1" aria-label="One minute more">${I("M5 12h14M12 5v14")}</button>
            <button class="nbtn go" type="button" id="tv-start">Start</button></div></div>
        <div class="tv-run" id="tv-run" hidden><button class="nbtn" type="button" id="tv-cancel">Cancel the timer</button></div></div></div>`;
    const $ = (s) => box.querySelector(s);
    let minutes = 10, running = null, anchorAt = 0, alive = true;
    const d = dial($("#tv-dial"), { max: 60, value: minutes, onChange: (m) => { minutes = m; paintSet(); } });
    const paintSet = () => {
      $("#tv-read").textContent = `${minutes}:00`;
      $("#tv-sub").textContent = `Ends at ${hhmm(new Date(Date.now() + minutes * 60000))}`;
      box.querySelectorAll("[data-min]").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.min) === minutes)));
    };
    const setMinutes = (m) => { minutes = Math.max(1, Math.min(24 * 60, Math.round(m))); d.set(Math.min(60, minutes)); paintSet(); if (minutes > 60) { $("#tv-read").textContent = `${Math.floor(minutes / 60)}:${pad2(minutes % 60)} h`; } };
    box.addEventListener("click", async (e) => {
      const c = e.target.closest("[data-min]"), st = e.target.closest("[data-step]");
      if (c) setMinutes(Number(c.dataset.min));
      if (st) setMinutes(minutes + Number(st.dataset.step));
    });
    // "Count down to a clock time": the minutes from now to the next time the clock shows it.
    $("#tv-until").addEventListener("change", (e) => {
      const [hh, mm] = e.target.value.split(":").map(Number);
      if (Number.isNaN(hh)) return;
      const now = new Date(), target = new Date(now); target.setHours(hh, mm, 0, 0);
      if (target <= now) target.setDate(target.getDate() + 1);
      setMinutes((target - now) / 60000);
    });
    $("#tv-start").addEventListener("click", async () => {
      const r = await h.api.post("/ui/timers", { seconds: minutes * 60 }).catch(() => null);
      if (r && r.ok) { h.say(`Timer started: ${minutes} minute${minutes === 1 ? "" : "s"}, until ${hhmm(new Date(Date.now() + minutes * 60000))}.`); await load(); }
      else h.say((r && r.error) || "I couldn't start the timer.");
    });
    $("#tv-cancel").addEventListener("click", async () => {
      if (running) await h.api.del("/ui/timers", { key: running.key }).catch(() => null);
      h.say("Timer cancelled.");
      await load();
    });
    async function load() {
      const o = await h.api.get("/ui/overview").catch(() => null);
      const t = o && o.ok && (o.timers || [])[0];
      running = t ? { key: t.key, label: t.label, remaining: t.remaining_s, total: t.duration_s || t.remaining_s } : null;
      anchorAt = Date.now();
      $("#tv-set").hidden = !!running; $("#tv-run").hidden = !running;
      if (running) paintRun(); else paintSet();
    }
    const paintRun = () => {
      if (!running) return;
      const left = Math.max(0, running.remaining - (Date.now() - anchorAt) / 1000);
      $("#tv-read").textContent = clock(left);
      $("#tv-sub").textContent = `${running.label} · ends at ${hhmm(new Date(Date.now() + left * 1000))}`;
      d.set(Math.max(1, Math.min(60, Math.ceil(left / 60))));
    };
    const tick = setInterval(() => { if (running) paintRun(); }, 500);
    const refresh = setInterval(() => { if (alive) load(); }, 4000);
    paintSet(); load();
    return { update() {}, destroy() { alive = false; clearInterval(tick); clearInterval(refresh); } };
  },
};

// ---- task / reminder ----
const task = {
  title: "Task",
  mount(box, h) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    let shown = new Date(today.getFullYear(), today.getMonth(), 1), picked = new Date(today), noDate = false, time = "09:00", listening = false;
    box.innerHTML = `<div class="kv"><div class="kv-cal" id="kv-cal"></div>
      <div class="kv-form"><div class="kv-say"><input id="kv-text" class="kv-input" autocomplete="off" maxlength="200" placeholder="What do you want to do?" aria-label="What to do"><button class="nbtn icon" type="button" id="kv-mic" aria-label="Say it" title="Say it">${I("M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3")}</button></div>
        <div class="kv-time"><label>At <input type="time" id="kv-time" value="${time}" aria-label="Time"></label>
          ${["09:00", "12:00", "15:00", "18:00"].map((t) => `<button class="nbtn" type="button" data-t="${t}">${t}</button>`).join("")}
          <button class="nbtn" type="button" id="kv-nodate" aria-pressed="false">No date</button></div>
        <div class="kv-sum" id="kv-sum" aria-live="polite"></div>
        <div class="nrow"><button class="nbtn go" type="button" id="kv-add">Add it</button><span class="faint" id="kv-msg" aria-live="polite"></span></div></div></div>`;
    const $ = (s) => box.querySelector(s);
    const summary = () => {
      const d = noDate ? "" : `${picked.toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "long" })}, ${time}`;
      $("#kv-sum").textContent = noDate ? "No date: it goes on your list." : d;
      box.querySelectorAll("[data-t]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.t === time && !noDate)));
      $("#kv-nodate").setAttribute("aria-pressed", String(noDate));
    };
    const drawCal = () => {
      const grid = monthGrid(shown, new Date()), name = shown.toLocaleDateString("en-US", { month: "long", year: "numeric" });
      const cells = grid.cells;
      $("#kv-cal").innerHTML = `<div class="kc-head"><button class="nbtn icon" type="button" data-mo="-1" aria-label="Previous month">${I("M15 6l-6 6 6 6")}</button><b>${esc(name)}</b><button class="nbtn icon" type="button" data-mo="1" aria-label="Next month">${I("M9 6l6 6-6 6")}</button></div>
        <div class="kc-grid"><span>M</span><span>T</span><span>W</span><span>T</span><span>F</span><span>S</span><span>S</span>${cells.map((c) => {
          const n = c && typeof c === "object" ? c.day ?? c.n : c;
          if (!n) return "<i></i>";
          const dt = new Date(shown.getFullYear(), shown.getMonth(), n), past = dt < today, on = !noDate && ymd(dt) === ymd(picked);
          return `<button type="button" class="kc-day" data-d="${ymd(dt)}" ${past ? "disabled" : ""} aria-pressed="${on}" aria-label="${dt.toDateString()}">${n}</button>`;
        }).join("")}</div>`;
    };
    box.addEventListener("click", (e) => {
      const mo = e.target.closest("[data-mo]"), day = e.target.closest("[data-d]"), tt = e.target.closest("[data-t]");
      if (mo) { shown = new Date(shown.getFullYear(), shown.getMonth() + Number(mo.dataset.mo), 1); drawCal(); }
      if (day) { const [y, m, dd] = day.dataset.d.split("-").map(Number); picked = new Date(y, m - 1, dd); noDate = false; drawCal(); summary(); }
      if (tt) { time = tt.dataset.t; $("#kv-time").value = time; noDate = false; drawCal(); summary(); }
    });
    $("#kv-time").addEventListener("change", (e) => { time = e.target.value || time; noDate = false; drawCal(); summary(); });
    $("#kv-nodate").addEventListener("click", () => { noDate = !noDate; drawCal(); summary(); });
    $("#kv-mic").addEventListener("click", async () => {
      if (listening) return;
      listening = true; $("#kv-msg").textContent = "Listening… say it now."; $("#kv-mic").setAttribute("aria-pressed", "true");
      const r = await h.api.post("/ui/dictate", { seconds: 12 }).catch(() => null);
      listening = false; $("#kv-mic").setAttribute("aria-pressed", "false");
      if (r && r.ok) { $("#kv-text").value = r.text; $("#kv-msg").textContent = "Heard you. Add it, or change it first."; }
      else $("#kv-msg").textContent = (r && r.error) || "I couldn't hear that.";
    });
    $("#kv-add").addEventListener("click", async () => {
      const title = $("#kv-text").value.trim();
      if (!title) { $("#kv-msg").textContent = "Write or say what to do first."; return; }
      const body = { title }; if (!noDate) body.when = `${ymd(picked)} ${time}`;
      const r = await h.api.post("/ui/tasks", body).catch(() => null);
      if (r && r.ok) { $("#kv-text").value = ""; $("#kv-msg").textContent = ""; h.say(noDate ? `Added: ${title}.` : `Added: ${title}, ${picked.toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "long" })} at ${time}.`); }
      else $("#kv-msg").textContent = (r && r.error) || "I couldn't add that.";
    });
    $("#kv-text").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#kv-add").click(); });
    drawCal(); summary();
    return { update() {}, destroy() {}, focus() { $("#kv-text").focus(); } };
  },
};

// ---- focus sprint ----
const focus = {
  title: "Focus",
  mount(box, h) {
    box.innerHTML = `<div class="tv"><div class="tv-dial" id="fv-dial"></div>
      <div class="tv-side"><div class="tv-read" id="fv-read">25:00</div><div class="tv-sub" id="fv-sub">A sprint with no distractions: I watch what you are doing and nudge you back.</div>
        <div class="tv-set" id="fv-set"><div class="nrow">${[15, 25, 45, 60].map((m) => `<button class="nbtn" type="button" data-min="${m}">${m} min</button>`).join("")}</div>
          <div class="nrow"><button class="nbtn icon" type="button" data-step="-5" aria-label="Five minutes less">${I("M5 12h14")}</button><button class="nbtn icon" type="button" data-step="5" aria-label="Five minutes more">${I("M5 12h14M12 5v14")}</button><button class="nbtn go" type="button" id="fv-start">Start the sprint</button></div></div>
        <div class="tv-run" id="fv-run" hidden><button class="nbtn" type="button" id="fv-stop">End the sprint</button></div></div></div>`;
    const $ = (s) => box.querySelector(s);
    let minutes = 25, cur = null, anchorAt = 0;
    const d = dial($("#fv-dial"), { max: 60, value: minutes, onChange: (m) => { minutes = m; paintSet(); } });
    const paintSet = () => { $("#fv-read").textContent = `${minutes}:00`; box.querySelectorAll("[data-min]").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.min) === minutes))); };
    box.addEventListener("click", (e) => {
      const c = e.target.closest("[data-min]"), st = e.target.closest("[data-step]");
      if (c) { minutes = Number(c.dataset.min); d.set(minutes); paintSet(); }
      if (st) { minutes = Math.max(5, Math.min(60, minutes + Number(st.dataset.step))); d.set(minutes); paintSet(); }
    });
    $("#fv-start").addEventListener("click", async () => {
      const r = await h.api.post("/ui/focus/start", { minutes }).catch(() => null);
      if (r && r.ok) h.say(`Focus sprint started: ${minutes} minutes.`); else h.say((r && r.error) || "I couldn't start it.");
    });
    $("#fv-stop").addEventListener("click", async () => { await h.api.post("/ui/focus/stop").catch(() => null); h.say("Sprint ended."); });
    const paintRun = () => {
      $("#fv-set").hidden = !!cur; $("#fv-run").hidden = !cur;
      if (!cur) { paintSet(); $("#fv-sub").textContent = "A sprint with no distractions: I watch what you are doing and nudge you back."; return; }
      const left = Math.max(0, cur.remaining_s - (Date.now() - anchorAt) / 1000);
      $("#fv-read").textContent = clock(left);
      $("#fv-sub").textContent = `Sprint of ${cur.minutes} minutes · ends at ${hhmm(new Date(Date.now() + left * 1000))}`;
      d.set(Math.max(1, Math.min(60, Math.ceil(left / 60))));
    };
    const tick = setInterval(paintRun, 500);
    paintSet();
    return {
      update(status) {
        const f = status && status.focus;
        if (f && (!cur || f.id !== cur.id || Math.abs((f.remaining_s) - (cur.remaining_s - (Date.now() - anchorAt) / 1000)) > 3)) { cur = f; anchorAt = Date.now(); }
        if (!f) cur = null;
        paintRun();
      },
      destroy() { clearInterval(tick); },
    };
  },
};

export const VIEWS = { music, timer, task, focus };
