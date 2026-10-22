// Today: where the time went, and whether it moved the user's work forward.
// Real data only (core/activity.py): tracking is opt-in, nothing is shown that
// was not measured, and what counts as "work" is what the user taught.
import { api, BASE } from "../api.js";
import { esc, header } from "./ui.js";

const STATUS = [["work", "Work"], ["off", "Not work"], ["mixed", "Mixed"], ["unknown", "Not sure yet"]];
const fmtDur = (s) => {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${String(m).padStart(2, "0")} m` : `${Math.max(m, s > 0 ? 1 : 0)} m`;
};
const minutes = (hhmm) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3));

export const today = {
  timer: null,
  destroy() { clearInterval(this.timer); this.timer = null; },

  async render(el, ctx) {
    const self = this;
    el.classList.add("sec");
    el.innerHTML = `<div class="top">${header("Today", "Where your time went, and whether it moved your work forward. Everything stays on this PC.")}</div>
      <div id="td-body" class="stack"></div>`;
    const body = el.querySelector("#td-body");
    let data = null;

    const save = async (changes) => {
      const r = await api.post("/ui/activity/settings", changes);
      if (!r.ok) { ctx.toast(r.error || "Couldn't save that."); return false; }
      return true;
    };

    const draw = () => {
      if (!data) return;
      if (!data.enabled) { drawOff(); return; }
      const t = data;
      const known = t.work_s + t.off_s + t.mixed_s;
      const circ = 314, off = t.score == null ? circ : circ * (1 - t.score / 100);
      const max = Math.max(1, ...t.apps.map((a) => a.seconds));
      const streakDots = t.week.map((d) => `<i class="${d.hit ? "on" : ""}" title="${esc(d.date)}: ${fmtDur(d.work_s)} on work"></i>`).join("");
      const ci = t.checkin;
      const first = t.timeline.length ? minutes(t.timeline[0].start) : 0;
      const last = t.timeline.length ? minutes(t.timeline[t.timeline.length - 1].end) : 0;
      const from = Math.floor(Math.min(first, 8 * 60) / 60) * 60, to = Math.ceil(Math.max(last, from + 240) / 60) * 60;
      const pos = (m) => ((m - from) / (to - from)) * 100;
      const hours = []; for (let h = from; h <= to; h += Math.max(60, Math.ceil((to - from) / 6 / 60) * 60)) hours.push(h);
      body.innerHTML = `
        <div class="grid12">
          <div class="surface card stack c5">
            <h3>Focus score <span class="chip">${t.score == null ? "not enough answers yet" : t.score >= 60 ? "on track" : "room to grow"}</span></h3>
            <div class="scorebox">
              <div class="ringwrap"><svg viewBox="0 0 116 116" width="116" height="116"><circle class="bg" cx="58" cy="58" r="50"/><circle class="fg" cx="58" cy="58" r="50" style="stroke-dashoffset:${off}"/></svg>
                <b class="${t.score == null ? "dash" : ""}">${t.score == null ? "–" : t.score}</b></div>
              <div class="stack" style="gap:6px">
                <div class="stat"><b>${fmtDur(t.active_s)}</b> <span class="muted">at the PC</span></div>
                <div class="stat"><b>${fmtDur(t.work_s)}</b> <span class="muted">on work</span></div>
                <div class="dots7" role="img" aria-label="Last 7 days: ${t.week.filter((d) => d.hit).length} reached the goal">${streakDots}</div>
                <div class="note">Goal ${t.goal_min} min of work a day. Streak: ${t.streak_days} day${t.streak_days === 1 ? "" : "s"}.</div>
              </div>
            </div>
            <p class="think">${t.score == null
              ? (t.unknown_s > 0 ? `${fmtDur(t.unknown_s)} is on programs I can't judge yet. Tell me which ones are work below, or answer my check-ins.` : "Nothing counted yet. Keep working and it fills in.")
              : `${fmtDur(known)} of your time is on programs I know. The score is work time (mixed counts half) out of that.`}</p>
          </div>
          <div class="surface card stack c7">
            <h3>Time by program <span class="chip">you decide what counts</span></h3>
            ${t.apps.length ? t.apps.map((a) => `
              <div class="approw"><span class="ic"><img alt="" src="${BASE}/ui/activity/icon?key=${encodeURIComponent(a.key)}" onerror="this.replaceWith(document.createTextNode('${esc((a.name || "?")[0].toUpperCase())}'))"></span>
                <span class="nm" title="${esc(a.name)}">${esc(a.name)}</span>
                <span class="bar" aria-hidden="true"><i class="st-${a.status}" style="width:${Math.max(3, Math.round(100 * a.seconds / max))}%"></i></span>
                <em>${fmtDur(a.seconds)}</em>
                <select class="field" data-key="${esc(a.key)}" aria-label="Does ${esc(a.name)} count as work?">${STATUS.map(([v, l]) => `<option value="${v}" ${v === a.status ? "selected" : ""}>${l}</option>`).join("")}</select>
              </div>`).join("") : `<div class="empty">No programs counted yet. Use your PC for a few minutes and they appear here.</div>`}
            <div class="legend"><span style="--c:var(--accent)">Work</span><span style="--c:var(--bad)">Not work</span><span style="--c:var(--text-3)">Mixed</span><span style="--c:var(--surface-3)">Not sure yet</span></div>
          </div>
          <div class="surface card stack c8">
            <h3>Your day</h3>
            ${t.timeline.length ? `<div class="tl" role="img" aria-label="Your day as a strip">${t.timeline.map((s) =>
              `<i class="st-${s.status}" style="left:${pos(minutes(s.start))}%;width:${Math.max(0.6, pos(minutes(s.end)) - pos(minutes(s.start)))}%" title="${s.start}–${s.end}"></i>`).join("")}</div>
              <div class="tlh">${hours.map((h) => `<span style="left:${pos(h)}%">${String(Math.floor(h / 60) % 24).padStart(2, "0")}:00</span>`).join("")}</div>`
              : `<div class="empty">Nothing on the strip yet.</div>`}
          </div>
          <div class="surface card stack c4">
            <h3>Check-in</h3>
            ${ci ? `<div class="ask-q"><b>${esc(ci.question)}</b><div class="btnrow">
                <button class="btn accent" data-answer="work" type="button">Working</button>
                <button class="btn" data-answer="break" type="button">On a break</button>
                <button class="btn" data-answer="off" type="button">Busted</button></div></div>`
              : `<p class="think">No check-in waiting. I ask about a program after ${t.settings.checkin_minutes} minutes in a row, at most every 45 minutes, and never in a full-screen app.</p>`}
            <p class="note">Each answer teaches me which programs count as work for you.</p>
          </div>
          <div class="surface card stack c12">
            <h3>Tracking</h3>
            <div class="setting"><div><div class="t">Watch which program is in front</div><div class="s">Only the program's name is kept, never window titles. Stays on this PC.</div></div>
              <button class="switch" type="button" role="switch" aria-checked="true" aria-label="Track program usage" data-set="tracking"></button></div>
            <div class="setting"><div><div class="t">Ask me if I am really working</div><div class="s">A short check-in after a long stretch in a program I don't know as work.</div></div>
              <button class="switch" type="button" role="switch" aria-checked="${t.settings.checkins}" aria-label="Check-ins" data-set="checkins"></button></div>
            <div class="setting"><div><div class="t">Check in after</div><div class="s">How long in one program before I ask.</div></div>
              <select class="field" data-num="checkin_minutes" aria-label="Check in after">${[20, 30, 40, 60, 90].map((m) => `<option value="${m}" ${m === t.settings.checkin_minutes ? "selected" : ""}>${m} minutes</option>`).join("")}</select></div>
            <div class="setting"><div><div class="t">Daily work goal</div><div class="s">A day counts toward your streak when work time reaches this.</div></div>
              <select class="field" data-num="work_goal_min" aria-label="Daily work goal">${[30, 60, 90, 120, 180, 240].map((m) => `<option value="${m}" ${m === t.settings.work_goal_min ? "selected" : ""}>${m >= 60 ? `${m / 60} h` : `${m} min`}</option>`).join("")}</select></div>
          </div>
        </div>`;
    };

    const drawOff = () => {
      body.innerHTML = `<div class="surface card stack" style="max-width:640px">
        <h3>Tracking is off</h3>
        <p class="think">When you turn this on, I note which program is in front, a few seconds at a time, and add up your day here: time per program, a focus score, and a streak.</p>
        <ul class="think" style="margin:0;padding-left:1.2em">
          <li>Only the program's name is kept. Window titles and what you type are never read or stored.</li>
          <li>Everything stays on this PC, in a small file per day you can delete.</li>
          <li>I don't decide what is work. I ask you, and remember your answers.</li></ul>
        <div><button class="btn accent" id="td-on" type="button">Turn on tracking</button></div></div>`;
      body.querySelector("#td-on").addEventListener("click", async () => { if (await save({ tracking: true })) { ctx.toast("Tracking is on."); await load(); } });
    };

    const load = async () => {
      const r = await api.get("/ui/activity/today");
      if (!r.ok) { body.innerHTML = `<div class="surface card"><div class="empty">Couldn't load Today. ${esc(r.error || "")}</div></div>`; return; }
      data = r; draw();
    };

    body.addEventListener("click", async (e) => {
      const ans = e.target.closest("[data-answer]");
      if (ans && data && data.checkin) {
        const r = await api.post("/ui/activity/answer", { id: data.checkin.id, answer: ans.dataset.answer });
        ctx.toast(r.ok ? (ans.dataset.answer === "work" ? `Noted: ${r.app} counts as work.` : ans.dataset.answer === "off" ? `Noted: ${r.app} isn't work.` : "Okay, break noted.") : (r.error || "That check-in is gone."));
        await load();
        return;
      }
      const sw = e.target.closest("[data-set]");
      if (sw) {
        const next = sw.getAttribute("aria-checked") !== "true";
        sw.setAttribute("aria-checked", String(next));
        if (await save({ [sw.dataset.set]: next })) await load(); else sw.setAttribute("aria-checked", String(!next));
      }
    });
    body.addEventListener("change", async (e) => {
      const st = e.target.closest("select[data-key]");
      if (st) {
        const r = await api.post("/ui/activity/label", { key: st.dataset.key, status: st.value });
        ctx.toast(r.ok ? "Saved." : (r.error || "Couldn't save that."));
        await load();
        return;
      }
      const num = e.target.closest("select[data-num]");
      if (num && await save({ [num.dataset.num]: Number(num.value) })) await load();
    });

    await load();
    self.timer = setInterval(() => {
      const a = document.activeElement;
      if (document.visibilityState === "visible" && !(a && body.contains(a) && a.tagName === "SELECT")) load();
    }, 30000);
  },
};
