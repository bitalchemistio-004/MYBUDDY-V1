// Status (installation checks + live state) and the first-run Setup.
import { api } from "../api.js";
import { esc, header } from "./ui.js";
import { characterPicker, wirePicker } from "./prefs.js";
import { mountBrain } from "./brainpick.js";

const I = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
const ICON_OK = I("M5 12.5l4.5 4.5L19 7.5"), ICON_NEED = I("M12 7v6M12 17h.01"), ICON_SOFT = I("M7 12h10");

function checkRow(c) {
  const cls = c.ok ? "ok" : c.level === "required" ? "no" : "soft";
  const ico = c.ok ? ICON_OK : c.level === "required" ? ICON_NEED : ICON_SOFT;
  return `<div class="check ${cls}"><span class="ico">${ico}</span><div class="grow" style="flex:1">
    <div class="row between"><b>${esc(c.title)}</b>${c.level === "required" && !c.ok ? `<span class="chip bad"><span class="dot"></span>needed</span>` : ""}</div>
    <div class="s muted" style="font-size:13px">${esc(c.detail)}${c.fix ? ` ${esc(c.fix)}` : ""}</div>
    ${c.action === "pull" ? `<div class="stack" style="gap:6px;margin-top:8px"><button class="btn accent" type="button" data-pull>Download it now</button><div class="progress" hidden><i style="width:0%"></i></div><small class="faint" data-pull-status></small></div>` : ""}
  </div></div>`;
}

async function runChecks(box, ctx, onDone) {
  box.innerHTML = `<div class="empty">Checking this PC…</div>`;
  const r = await api.get("/setup/checks");
  if (!r.ok) { box.innerHTML = `<div class="empty">Couldn't run the checks.</div>`; return null; }
  const bad = r.checks.filter((c) => !c.ok);
  const summary = bad.length
    ? `<div class="check ${bad.some((c) => c.level === "required") ? "no" : "soft"}"><span class="ico">${ICON_NEED}</span><div><b>${bad.length} of ${r.checks.length} checks need a look</b></div></div>`
    : `<div class="check ok"><span class="ico">${ICON_OK}</span><div><b>All ${r.checks.length} checks passed</b></div></div>`;
  box.innerHTML = summary + r.checks.map(checkRow).join("");
  const pull = box.querySelector("[data-pull]");
  if (pull) pull.addEventListener("click", async () => {
    pull.disabled = true;
    const bar = box.querySelector(".progress"), label = box.querySelector("[data-pull-status]");
    bar.hidden = false;
    await api.post("/setup/pull", { model: ctx.settings.llm_model });
    const t = setInterval(async () => {
      const p = (await api.get("/setup/pull")).pull;
      if (p.total) bar.firstElementChild.style.width = Math.round((p.completed / p.total) * 100) + "%";
      label.textContent = p.state === "error" ? "Failed: " + p.error : p.status;
      if (p.state === "done" || p.state === "error") { clearInterval(t); if (p.state === "done") runChecks(box, ctx, onDone); else pull.disabled = false; }
    }, 1000);
  });
  onDone && onDone(r);
  return r;
}

export const status = {
  async render(el, ctx) {
    el.innerHTML = `${header("Status", "Is everything MBUDDY needs working? Each check looks at the real thing.")}
      <div class="grid2">
        <div class="surface card stack"><div class="row between"><div class="label">This PC</div><button class="btn" type="button" id="st-re">Check again</button></div><div class="list" id="st-checks"></div></div>
        <div class="surface card stack"><div class="label">Right now</div><div class="list" id="st-live"></div>
          <div class="row between" style="margin-top:6px"><span class="muted" style="font-size:13px">Something stuck? Restart the brain (takes a few seconds).</span><button class="btn" type="button" id="st-restart">Restart</button></div></div>
      </div>`;
    const box = el.querySelector("#st-checks");
    el.querySelector("#st-re").onclick = () => runChecks(box, ctx);
    el.querySelector("#st-restart").onclick = async (e) => {
      e.target.disabled = true;
      await api.post("/control/shutdown").catch(() => {});  // the shell starts it again
      ctx.toast("Restarting MBUDDY's brain…", 5000);
      setTimeout(() => { e.target.disabled = false; }, 8000);
    };
    runChecks(box, ctx);
    const live = () => {
      const s = ctx.status || {};
      const rows = [
        ["Backend", ctx.connected ? "running" : "not connected"],
        ["Voice", s.device_conn_state === "online" ? `connected (${esc(s.device_firmware_version)})` : "not connected"],
        ["Awake", s.awake ? "yes" : "asleep (listening for “Hey Buddy”)"],
        ["Feeling", s.emotion || "–"],
        ["Mic level", s.level != null ? Number(s.level).toFixed(3) + (s.level > 0.006 ? " (hearing sound)" : " (quiet)") : "–"],
        ["Last heard", s.last_heard || "–"],
      ];
      el.querySelector("#st-live").innerHTML = rows.map(([k, v]) => `<div class="item"><div class="grow t">${k}</div><div class="s">${esc(v)}</div></div>`).join("");
    };
    live();
    this._off = ctx.on(live);
  },
  destroy() { this._off && this._off(); },
};

const STEPS = ["Welcome", "Where I think", "Check this PC", "Pick your buddy", "Say “Hey Buddy”", "From your phone", "Done"];

export const setup = {
  async render(el, ctx) {
    let step = 0, ready = false, woke = false, baseline = null;
    // Passes only on a new phrase containing "buddy" heard during this step.
    const off = ctx.on(() => {
      const heard = ctx.status && ctx.status.last_heard;
      if (step === 4 && !woke && heard && heard !== baseline && /buddy/i.test(heard)) { woke = true; draw(); }
    });
    this._off = off;
    const draw = () => {
      el.innerHTML = `<div class="setup">
        <div class="steps surface">${STEPS.map((s, i) => `<div class="${i === step ? "on" : i < step ? "done" : ""}"><span>${i < step ? ICON_OK : i + 1}</span>${esc(s)}</div>`).join("")}</div>
        <div class="surface card stack" style="padding:32px;min-height:520px">${body()}
          <div class="row between" style="margin-top:auto"><button class="btn" type="button" id="su-back" ${step === 0 ? "disabled" : ""}>Back</button>
          <button class="btn accent" type="button" id="su-next" ${step === 2 && !ready ? "disabled" : ""}>${step === 0 ? "Let's start" : step === 6 ? "Open MBUDDY" : step === 4 && !woke ? "Skip for now" : step === 5 ? "Skip for now" : "Next"}</button></div></div></div>`;
      el.querySelector("#su-back").onclick = () => { step--; draw(); };
      el.querySelector("#su-next").onclick = async () => {
        if (step === 6) { await ctx.save({ onboarding_done: true }); location.hash = "#/home"; return; }
        if (step === 0) {
          const name = el.querySelector("#su-name").value.trim();
          if (name && name !== ctx.settings.user_name && !(await ctx.save({ user_name: name }))) return;
        }
        step++;
        if (step === 4) baseline = ctx.status ? ctx.status.last_heard : null;
        draw();
      };
      if (step === 1) mountBrain(el.querySelector("#su-brain"), ctx);
      if (step === 2) runChecks(el.querySelector("#su-checks"), ctx, (r) => {
        ready = r.ready;
        const next = el.querySelector("#su-next"); if (next) next.disabled = !ready;
        const note = el.querySelector("#su-note");
        if (note) note.textContent = ready ? "Everything MBUDDY needs is here." : "Fix the items marked “needed”, then check again.";
      });
      if (step === 2) el.querySelector("#su-recheck").onclick = () => draw();
      if (step === 3) wirePicker(el, ctx);
    };
    const body = () => [
      `<h1>Hi, I'm MBUDDY.</h1><p class="sub" style="font-size:17px;line-height:1.55">A companion that lives on this PC. I listen for “Hey Buddy”, talk back, set timers and reminders, and help with your apps. Everything I hear and think stays on this computer.</p>
        <label style="max-width:360px"><span class="label">What should I call you?</span><input class="field" id="su-name" maxlength="40" value="${esc(ctx.settings.user_name)}" style="margin-top:8px"></label>
        <p class="muted">Setup checks this PC, lets you pick your buddy, and tests your microphone. No commands to type.</p>`,
      `<h1>Where should I think?</h1><p class="muted">My thinking can run on this PC, or in the cloud if this laptop is weak or you want it to stay light. You can change this any time in Settings, Models.</p><div class="stack" id="su-brain"></div>`,
      `<div class="row between"><h1>Checking this PC</h1><button class="btn" type="button" id="su-recheck">Check again</button></div><p class="muted" id="su-note">Looking…</p><div class="list" id="su-checks"></div>`,
      `<h1>Pick your buddy</h1><p class="muted">Each one has its own look, moves and theme. You can change it any time in Buddy.</p>${characterPicker(ctx, "su-picker")}`,
      `<h1>Now say “Hey Buddy”.</h1><p class="muted">That's how you wake me. I'll answer so you know I heard you.</p>
        <div class="check ${woke ? "ok" : "soft"}"><span class="ico">${woke ? ICON_OK : ICON_SOFT}</span><div><b>${woke ? "I heard you!" : "Listening for “Hey Buddy”…"}</b><div class="s muted" style="font-size:13px">${woke ? esc(ctx.status?.last_heard || "") : "Speak normally, from where you usually sit."}</div></div></div>`,
      `<h1>Use me from your phone <span class="chip">optional</span></h1><p class="muted">You can skip this and do it later in Away.</p>
        <div class="list">
          <div class="item"><div class="grow"><div class="t">1. Install Tailscale on this PC and on your phone</div><div class="s">It's free (tailscale.com/download). Sign in to the same account on both. It makes a private network between your devices, so nothing is opened to the internet.</div></div></div>
          <div class="item"><div class="grow"><div class="t">2. Run this once on the PC, in a terminal</div><div class="s"><code>tailscale serve --bg 5051</code> gives your phone a private address for MBUDDY.</div></div></div>
          <div class="item"><div class="grow"><div class="t">3. Pair the phone</div><div class="s">Open Away in MBUDDY, press “New pairing code”, then open the address on the phone and type the code.</div></div></div></div>
        <div class="check soft"><span class="ico">${ICON_NEED}</span><div><b>Never use “Tailscale Funnel” for this.</b><div class="s muted" style="font-size:13px">Funnel puts the address on the public internet. “Serve” keeps it private to your own devices. A paired phone can only do a short list of things, and “Pause access” cuts every phone off at once.</div></div></div>`,
      `<h1>All set.</h1><p class="sub" style="font-size:17px;line-height:1.55">I wait quietly in the tray. Windows sometimes hides tray icons under the <b>^</b> arrow: drag mine onto the taskbar to keep me in sight.</p>`,
    ][step];
    draw();
  },
  destroy() { this._off && this._off(); },
};
