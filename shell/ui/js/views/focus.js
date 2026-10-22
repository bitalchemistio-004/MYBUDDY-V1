// Focus: sprints that count toward the day. Real timer and history (core/focus.py).
import { api } from "../api.js";
import { esc, header } from "./ui.js";

const CIRC = 565;
const clock = (s) => `${String(Math.floor(Math.max(0, s) / 60)).padStart(2, "0")}:${String(Math.max(0, s) % 60).padStart(2, "0")}`;

export const focus = {
  timer: null,
  destroy() { clearInterval(this.timer); this.timer = null; },

  async render(el, ctx) {
    const self = this;
    el.classList.add("sec");
    el.innerHTML = `<div class="top">${header("Focus", "Start a sprint and I keep quiet, keep time, and count it toward your day.")}</div><div id="fc-body" class="stack"></div>`;
    const body = el.querySelector("#fc-body");
    let st = null, tracking = false, remaining = 0, total = 0, tick = null;

    const load = async () => {
      const [f, a] = await Promise.all([api.get("/ui/focus"), api.get("/ui/activity/settings")]);
      if (!f.ok) { body.innerHTML = `<div class="surface card"><div class="empty">Couldn't load Focus. ${esc(f.error || "")}</div></div>`; return; }
      st = f; tracking = !!(a.ok && a.settings.tracking);
      remaining = st.current ? st.current.remaining_s : 0; total = st.current ? st.current.minutes * 60 : 0;
      draw();
    };

    const ringOffset = () => (total ? CIRC * (1 - Math.max(0, remaining) / total) : 0);

    const draw = () => {
      const cur = st.current, w = st.week, max = Math.max(1, ...w.map((d) => d.minutes));
      const days = w.filter((d) => d.minutes > 0).length;
      body.innerHTML = `
        <div class="surface card stack"><div class="fhero">
          <div class="fring"><svg viewBox="0 0 220 220" width="220" height="220"><circle class="bg" cx="110" cy="110" r="90"/>
            <circle class="fg" id="fc-fg" cx="110" cy="110" r="90" style="stroke-dashoffset:${cur ? ringOffset() : CIRC}"/></svg>
            <div><span id="fc-t">${cur ? clock(remaining) : "––:––"}</span><small>${cur ? "left in this sprint" : "no sprint running"}</small></div></div>
          <div class="stack" style="flex:1;min-width:240px">
            ${cur ? `<p class="think">A ${cur.minutes} minute sprint is running. Stopping early keeps the minutes you really focused.</p>
              <div><button class="btn" id="fc-stop" type="button">Stop sprint</button></div>`
              : `<label class="stack" style="gap:6px"><span class="label">Length</span>
                  <select class="field" id="fc-len" aria-label="Sprint length">${[15, 25, 45, 60, 90].map((m) => `<option value="${m}" ${m === 25 ? "selected" : ""}>${m} minutes</option>`).join("")}</select></label>
                <div><button class="btn accent" id="fc-start" type="button">Start sprint</button></div>`}
            <div class="setting"><div><div class="t">Nudge me if I drift</div>
              <div class="s">${tracking ? "If the program in front is one you told me is not work, I say so once, kindly. I never close or hide anything." : "Needs program tracking. Turn it on in Today first."}</div></div>
              <button class="switch" type="button" role="switch" aria-checked="${!!st.settings.nudge && tracking}" aria-label="Nudge me if I drift" id="fc-nudge" ${tracking ? "" : "disabled"}></button></div>
          </div></div></div>
        <div class="surface card stack"><h3>This week <span class="chip">${days} of 7 days</span></h3>
          ${w.some((d) => d.minutes) ? `<div class="hist" role="img" aria-label="Focused minutes per day">${w.map((d) =>
            `<div title="${esc(d.date)}: ${d.minutes} min in ${d.sprints} sprint${d.sprints === 1 ? "" : "s"}"><i class="${d.minutes ? "" : "zero"}" style="height:${d.minutes ? Math.max(6, Math.round(100 * d.minutes / max)) : 4}%"></i></div>`).join("")}</div>
            <div class="histl">${w.map((d) => `<span>${new Date(d.date + "T00:00").toLocaleDateString([], { weekday: "short" })}</span>`).join("")}</div>`
            : `<div class="empty">No sprints yet. Start one above and it shows up here.</div>`}
        </div>`;
      const fg = body.querySelector("#fc-fg"), tt = body.querySelector("#fc-t");
      body.querySelector("#fc-start")?.addEventListener("click", async () => {
        const minutes = Number(body.querySelector("#fc-len").value);
        const r = await api.post("/ui/focus/start", { minutes });
        if (!r.ok) { ctx.toast(r.error || "Couldn't start."); return; }
        ctx.toast(`Sprint started: ${minutes} minutes.`); await load();
      });
      body.querySelector("#fc-stop")?.addEventListener("click", async () => {
        const r = await api.post("/ui/focus/stop");
        ctx.toast(r.ok && r.ended ? `Stopped. ${Math.round(r.ended.focused_s / 60)} minutes counted.` : "Stopped."); await load();
      });
      body.querySelector("#fc-nudge")?.addEventListener("click", async (e) => {
        const b = e.currentTarget, next = b.getAttribute("aria-checked") !== "true";
        b.setAttribute("aria-checked", String(next));
        const r = await api.post("/ui/focus/settings", { nudge: next });
        if (!r.ok) { b.setAttribute("aria-checked", String(!next)); ctx.toast(r.error || "Couldn't save."); }
      });
      clearInterval(tick);
      if (cur) {
        tick = setInterval(async () => {
          remaining -= 1;
          if (remaining <= 0) { clearInterval(tick); ctx.toast("Sprint finished. Nice work."); await load(); return; }
          tt.textContent = clock(remaining); fg.style.strokeDashoffset = ringOffset();
        }, 1000);
        self.timer = tick;
      }
    };
    await load();
  },
};
