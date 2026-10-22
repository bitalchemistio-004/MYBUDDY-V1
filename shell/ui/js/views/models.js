// Settings, Models: the model on this PC, the bigger model in the cloud, what was
// spent, and what always asks first. Everything shown comes from /ui/models
// (core/cloud.py); the key is typed in brainpick.js, kept encrypted by the backend and never shown again.
import { api, REMOTE } from "../api.js";
import { esc } from "./ui.js";
import { mountBrain, modelNames } from "./brainpick.js";

const fmt = (n) => Number(n || 0).toLocaleString();
const dots = (n, of = 12) => `<span class="dotrow" aria-hidden="true">${Array.from({ length: of }, (_, i) => `<i class="${i < n ? "on" : ""}"></i>`).join("")}</span>`;

export default {
  async render(el, ctx) {
    el.innerHTML = `<div class="top"><div><p class="sub">Which brain answers, and what it costs. My own model runs on this PC and handles most things, even offline. A much bigger one in the cloud helps with hard things, only when it's on.</p></div></div>
      <div class="cards" id="mo-cards"><section class="lcard c12"><p class="none">Loading…</p></section></div>`;
    const root = el.querySelector("#mo-cards");
    const names = await modelNames();
    const load = async () => {
      const s = await api.get("/ui/models").catch(() => null);
      if (!s || !s.ok) { root.innerHTML = `<section class="lcard c12"><p class="none err">I couldn't read the model settings.</p></section>`; return; }
      draw(s);
    };

    const draw = (s) => {
      const c = s.cloud, today = s.usage_today, week = s.usage_week;
      const cap = c.daily_token_cap || 0, used = today.tokens_in + today.tokens_out;
      const blocked = Object.entries(c.blocked || {});
      const phone = REMOTE;   // a phone can look, and switch where I work; the rest is changed on the PC
      root.innerHTML = `
        <section class="lcard c12"><h3>Where I think <span class="chip ${s.brain.running === "cloud" ? "ok" : ""}"><span class="dot"></span>${s.brain.running === "cloud" ? "cloud" : "this PC"}</span></h3>
          ${phone ? `<p>Right now I'm thinking ${s.brain.running === "cloud" ? `in the cloud with ${esc(c.brain_model)}` : "on the PC"}.</p><p class="none">Change this on the PC, in Settings, Models.</p>` : `<div id="mo-brain"></div>`}</section>
        <section class="lcard c6"><h3>On this PC <span class="chip ok"><span class="dot"></span>${ctx.connected ? "ready" : "starting"}</span></h3>
          <div class="mid">${esc(s.local.model)}</div>
          <p class="none">Tools, short writing, finding facts in text, and everything when you're offline. Nothing leaves this PC.</p></section>
        <section class="lcard c6"><h3>Bigger model, in the cloud
            <span class="chip ${c.usable ? "ok" : ""}"><span class="dot"></span>${!c.key_present ? "no key" : !c.enabled ? "off" : "on"}</span></h3>
          ${phone ? `<p>${c.usable ? `On: ${esc(c.model)} for hard questions, ${esc(c.vision_model)} for pictures.` : "Not in use."}</p><p class="none">Change this on the PC, in Settings, Models.</p>`
          : c.key_present ? `<div class="setting"><div><div class="t">Use the bigger model</div>
              <div class="s">For exact reasoning, comparisons, long documents and pictures. Your own words go to it when you ask something hard; anything from this PC only after you say yes.</div></div>
              <button class="switch" type="button" role="switch" aria-checked="${c.enabled}" aria-label="Use the bigger model" id="mo-on"></button></div>
            <label class="stack" style="gap:4px"><span class="label">Main model</span>
              <input class="field" id="mo-main" list="mo-list" value="${esc(c.model)}" maxlength="60" aria-label="Main cloud model"></label>
            <label class="stack" style="gap:4px"><span class="label">Model for pictures</span>
              <input class="field" id="mo-vision" list="mo-list" value="${esc(c.vision_model)}" maxlength="60" aria-label="Cloud model for pictures"></label>
            <datalist id="mo-list">${names.map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
            <div class="row"><button class="btn" type="button" id="mo-test">Test it</button><span class="muted" id="mo-test-r" aria-live="polite"></span></div>
            ${blocked.length ? `<p class="none err">${blocked.map(([m, why]) => `${esc(m)}: ${esc(why)}`).join(" ")} I'll try again in a few minutes.</p>` : ""}`
          : `<div class="empty"><b>No cloud key yet</b><span>Paste your Ollama key above, in Where I think. I keep it encrypted and never show it again.</span></div>`}
        </section>
        <section class="lcard c7"><h3>Used</h3>
          <div class="row between"><span>Today</span><b>${fmt(today.calls)} calls · ${fmt(used)} tokens</b></div>
          ${dots(cap ? Math.min(12, Math.round(12 * used / cap)) : Math.min(12, today.calls))}
          <div class="row between"><span>Last 7 days</span><b>${fmt(week.calls)} calls · ${fmt(week.tokens_in + week.tokens_out)} tokens</b></div>
          ${Object.entries(week.by_model).map(([m, v]) => `<div class="row between"><span class="muted">${esc(m)}</span><span class="muted">${fmt(v.calls)} calls</span></div>`).join("") || `<p class="none">Nothing has gone to the cloud yet.</p>`}
          ${phone ? "" : `<label class="stack" style="gap:4px"><span class="label">Daily limit (tokens, 0 = no limit)</span>
            <input class="field" id="mo-cap" inputmode="numeric" value="${cap}" aria-label="Daily token limit"></label>`}
          <p class="none">The free plan's allowance isn't published, so I can only count what I send. <a href="#/settings/usage">See the full Usage page</a>.</p></section>
        ${phone || !c.key_present ? "" : `<section class="lcard c12"><h3>Try a model</h3>
          <p class="none">Pick any model Ollama lists and ask it a short question to see how it answers, how fast, and what it used, before you choose it. Only the words you type here are sent.</p>
          <div class="row" style="flex-wrap:wrap"><input class="field" id="mo-pm" list="mo-list" value="${esc(c.brain_model)}" maxlength="60" aria-label="Model to try" style="flex:1;min-width:180px">
            <input class="field" id="mo-pp" value="Explain in two sentences what you are good at." maxlength="600" aria-label="Question to ask it" style="flex:3;min-width:240px">
            <button class="btn accent" type="button" id="mo-pgo">Try it</button></div>
          <div id="mo-pr" aria-live="polite"></div></section>`}
        <section class="lcard c5"><h3>Always asks first</h3>
          <div class="list"><div class="item"><div class="grow"><div class="t">The clipboard</div></div><span class="chip">asks</span></div>
            <div class="item"><div class="grow"><div class="t">A file or document</div></div><span class="chip">asks</span></div>
            <div class="item"><div class="grow"><div class="t">A screenshot</div></div><span class="chip">asks</span></div></div>
          <p class="none">I tell you exactly what would be sent and to which model, and only your yes sends it. This isn't switchable.</p></section>
        <section class="lcard c12"><h3>Where I'm working</h3>
          <div class="seg" role="group" aria-label="Where I'm working">
            ${[["pc", "On my PC"], ["phone", "On my phone"]].map(([v, l]) => `<button type="button" data-work="${v}" aria-pressed="${s.work_on === v}">${l}</button>`).join("")}</div>
          <p class="none">${s.work_on === "phone" ? "The PC stays quiet; replies and approvals go to your phone." : "I speak and listen here on the PC."} ${phone ? "" : `Pair a phone in <a href="#/away">Away</a>.`}</p></section>`;

      const brainHost = root.querySelector("#mo-brain");
      if (brainHost) mountBrain(brainHost, ctx, { onChange: () => { if (!root.dataset.busy) { root.dataset.busy = "1"; load().finally(() => { delete root.dataset.busy; }); } } });
      const on = root.querySelector("#mo-on");
      if (on) on.addEventListener("click", async () => {
        const next = on.getAttribute("aria-checked") !== "true";
        on.setAttribute("aria-checked", String(next));
        if (await ctx.save({ cloud: { enabled: next } })) { ctx.toast(next ? "The bigger model is on." : "The bigger model is off."); load(); }
        else on.setAttribute("aria-checked", String(!next));
      });
      for (const [id, key] of [["mo-main", "model"], ["mo-vision", "vision_model"]]) {
        const input = root.querySelector("#" + id);
        if (input) input.addEventListener("change", async () => { if (await ctx.save({ cloud: { [key]: input.value.trim() } })) { ctx.toast("Saved."); load(); } else load(); });
      }
      const capEl = root.querySelector("#mo-cap");
      if (capEl) capEl.addEventListener("change", async () => {
        const n = Number(String(capEl.value).replace(/[ ,]/g, ""));
        if (!Number.isInteger(n) || n < 0) { ctx.toast("Enter a whole number of tokens, or 0 for no limit."); load(); return; }
        if (await ctx.save({ cloud: { daily_token_cap: n } })) { ctx.toast(n ? "Daily limit set." : "No daily limit."); load(); } else load();
      });
      const test = root.querySelector("#mo-test");
      if (test) test.addEventListener("click", async () => {
        test.setAttribute("aria-busy", "true"); test.disabled = true;
        const r = await api.post("/ui/models/test").catch(() => null);
        test.removeAttribute("aria-busy"); test.disabled = false;
        const t = r && r.test;
        root.querySelector("#mo-test-r").textContent = !t ? "I couldn't test it." : t.ok ? `Works: ${t.model} answered in ${t.secs} s.` : t.why;
        if (t && t.ok) load();
      });
      const go = root.querySelector("#mo-pgo");
      if (go) go.addEventListener("click", async () => {
        const model = root.querySelector("#mo-pm").value.trim(), box = root.querySelector("#mo-pr");
        go.disabled = true; box.innerHTML = `<p class="muted">Asking ${esc(model)}…</p>`;
        const r = await api.post("/ui/models/preview", { model, prompt: root.querySelector("#mo-pp").value }).catch(() => null);
        go.disabled = false;
        const p = r && r.preview;
        if (!p || !p.ok) { box.innerHTML = `<p class="none err">${esc((p && p.why) || "I couldn't ask it.")}</p>`; return; }
        const used = (week.by_model[p.model] || { calls: 0 });
        box.innerHTML = `<div class="check ok"><span class="ico"></span><div><b>${esc(p.model)}</b> answered in ${p.secs} s · ${fmt(p.tokens_in)} in, ${fmt(p.tokens_out)} out${p.usd != null ? ` · about $${p.usd.toFixed(5)} at list price` : ""}
          <p style="margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere">${esc(p.reply)}</p></div></div>
          <p class="none">Used ${fmt(used.calls)} times in the last 7 days. <button class="btn small" type="button" id="mo-pset">Use it as the cloud brain</button></p>`;
        const set = box.querySelector("#mo-pset");
        set.addEventListener("click", async () => { if (await ctx.save({ cloud: { brain_model: p.model } })) { ctx.toast("Saved. It applies after a restart."); load(); } });
      });
      root.querySelectorAll("[data-work]").forEach((b) => b.addEventListener("click", async () => {
        const r = await api.post("/ui/models", { work_on: b.dataset.work }).catch(() => null);
        if (r && r.ok) { ctx.settings.work_on = b.dataset.work; ctx.toast(b.dataset.work === "phone" ? "Working on your phone. The PC stays quiet." : "Working on your PC."); load(); }
      }));
    };
    await load();
  },
};
