// Where the thinking happens, and the Ollama key it may need. Used by Settings, Models and by the first-run
// setup, so there is one place that does it. Everything shown comes from /ui/models (core/cloud.py,
// core/cloud_brain.py); the key is typed here, sent once, stored encrypted by the backend and never shown again.
import { api } from "../api.js";
import { esc } from "./ui.js";

const FALLBACK_MODELS = ["gemma4:31b", "gpt-oss:120b", "gpt-oss:20b", "nemotron-3-super", "nemotron-3-nano:30b"];
let catalogCache = null;

export async function modelNames() {
  if (catalogCache) return catalogCache;
  const r = await api.get("/ui/models/catalog").catch(() => null);
  catalogCache = r && r.ok && r.models.length ? r.models : FALLBACK_MODELS;
  return catalogCache;
}

// The things a new user needs to know before choosing, in plain words.
export const WARNINGS = [
  ["Your words leave this PC.", "With the cloud brain, what you say to me, and the notes I look up to answer, are sent to Ollama's servers to be answered. With the brain on this PC nothing is sent."],
  ["It needs the internet.", "Without a connection the cloud brain can't think. Switch back to this PC's brain (it needs the model downloaded once) for offline use."],
  ["Your key is yours.", "Make it at ollama.com/settings/keys. I keep it encrypted for your Windows account, never show it again and never write it to a log. Anyone with the key can use your plan, so don't share it, and delete it on ollama.com if it leaks."],
  ["The free plan has limits.", "Ollama doesn't publish the free allowance and allows one request at a time, so busy moments can be slower or stop until the limit resets. A paid plan lifts this. The Usage page counts everything I send."],
  ["Some models need a paid plan.", "A model your plan doesn't include answers “not in your plan”. Use Check my plan to see which ones work for you."],
  ["Switching is at once.", "Switching to the cloud stops the model on this PC and frees its memory (an Ollama you started yourself is left running, with its model unloaded). Switching back starts it again, which takes a few seconds."],
];

export async function mountBrain(host, ctx, { onChange } = {}) {
  const names = await modelNames();
  let busy = false;

  const load = async (notify = true) => {
    const s = await api.get("/ui/models").catch(() => null);
    if (!s || !s.ok) { host.innerHTML = `<p class="none err">I couldn't read the brain settings.</p>`; return; }
    draw(s);
    if (notify) onChange && onChange(s);
  };

  const draw = (s) => {
    const c = s.cloud, b = s.brain;
    const cloudBrain = b.saved === "cloud";
    const hasKey = c.key_present;
    host.innerHTML = `
      <div class="seg" role="group" aria-label="Where the thinking happens">
        <button type="button" data-brain="local" aria-pressed="${!cloudBrain}">On this PC</button>
        <button type="button" data-brain="cloud" aria-pressed="${cloudBrain}">In the cloud</button></div>
      <p>${cloudBrain
        ? `<b>In the cloud.</b> ${esc(c.brain_model)} does the thinking, with all my tools and my notes. Light on a weak laptop. Needs the internet and your own Ollama key.`
        : `<b>On this PC.</b> A small model (${esc(s.local.model)}) runs here through Ollama. Private, works offline. Needs about 4 GB of free memory and a one-time download of about 2.5 GB; slower on a laptop without a graphics card.`}</p>
      ${b.restart_needed ? `<div class="check soft"><span class="ico"></span><div><b>Restart needed.</b> I'm thinking ${b.running === "cloud" ? "in the cloud" : "on this PC"} right now and will switch when I restart.
        <div class="row" style="margin-top:8px"><button class="btn accent" type="button" id="bp-restart">Restart MBUDDY now</button><span class="muted" id="bp-restart-r" aria-live="polite"></span></div></div></div>`
        : `<p class="none">Right now I'm thinking ${b.running === "cloud" ? `in the cloud with ${esc(c.brain_model)}` : "on this PC"}.</p>`}
      <div class="label">Your Ollama key</div>
      ${hasKey
        ? `<div class="setting"><div><div class="t">Key saved <span class="chip ok"><span class="dot"></span>${c.key_source === "app" ? "encrypted for your Windows account" : "from the .env file"}</span></div>
            <div class="s">${c.key_source === "env" ? "It comes from the .env file next to MBUDDY. Save a key here to use that instead." : "I can't show it again. Paste a new one to replace it."}</div></div>
            ${c.key_source === "app" ? `<button class="btn small danger" type="button" id="bp-key-remove">Remove</button>` : ""}</div>`
        : `<p class="none">Get a key at <b>ollama.com/settings/keys</b> (free account). Paste it below.</p>`}
      <label class="stack" style="gap:4px"><span class="label">${hasKey ? "Replace the key" : "Paste your key"}</span>
        <div class="row"><input class="field" id="bp-key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste it here" aria-label="Ollama API key" style="flex:1">
          <button class="btn accent" type="button" id="bp-key-save">Check and save</button></div></label>
      <span class="muted" id="bp-key-r" aria-live="polite"></span>
      <label class="stack" style="gap:4px"><span class="label">Model for the cloud brain</span>
        <input class="field" id="bp-model" list="bp-names" value="${esc(c.brain_model)}" maxlength="60" aria-label="Cloud brain model"></label>
      <datalist id="bp-names">${names.map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
      <p class="none">Recommended: <b>gemma4:31b</b> — fast, sees pictures, picks tools reliably (31 of 32 in my own test; it worked on the free plan, and a paid plan lists it at about $0.14 per million tokens in and $0.40 out).</p>
      <div class="row" style="flex-wrap:wrap"><button class="btn" type="button" id="bp-plan" ${hasKey ? "" : "disabled"}>Check my plan</button><span class="muted" id="bp-plan-r" aria-live="polite"></span></div>
      <div class="label">Hearing and voice run on</div>
      <div class="seg" role="group" aria-label="Where hearing and voice run">
        <button type="button" data-speech="auto" aria-pressed="${s.speech_device !== "cpu"}">The graphics card</button>
        <button type="button" data-speech="cpu" aria-pressed="${s.speech_device === "cpu"}">The processor only</button></div>
      <p class="none">${s.speech_device === "cpu" ? "The graphics card stays free, but hearing is much slower on the processor (several seconds for a sentence on a modest PC). " : "Faster, but about 1 GB of the graphics card's memory is used while MBUDDY runs, even with the cloud brain. "}Applies after a restart. <button class="btn small" type="button" id="bp-speech-restart">Restart MBUDDY</button></p>
      <details class="warn"><summary><b>Read before you switch</b></summary>
        <div class="list">${WARNINGS.map(([t, d]) => `<div class="item"><div class="grow"><div class="t">${esc(t)}</div><div class="s">${esc(d)}</div></div></div>`).join("")}</div></details>`;

    const say = (id, text) => { const n = host.querySelector(id); if (n) n.textContent = text; };
    host.querySelectorAll("[data-brain]").forEach((btn) => btn.addEventListener("click", async () => {
      if (busy || btn.getAttribute("aria-pressed") === "true") return;
      busy = true;
      const r = await api.post("/ui/models", { brain_mode: btn.dataset.brain }).catch(() => null);
      busy = false;
      if (r && r.ok) { draw(r); onChange && onChange(r); if (r.note) say("#bp-key-r", r.note); }
      else say("#bp-key-r", (r && r.error) || "That didn't work.");
    }));
    host.querySelectorAll("[data-speech]").forEach((btn) => btn.addEventListener("click", async () => {
      if (btn.getAttribute("aria-pressed") === "true") return;
      if (await ctx.save({ speech_device: btn.dataset.speech })) { await load(); say("#bp-key-r", "Saved. It applies after a restart."); }
    }));
    const speechRestart = host.querySelector("#bp-speech-restart");
    if (speechRestart) speechRestart.addEventListener("click", async () => { speechRestart.disabled = true; await api.post("/control/shutdown").catch(() => {}); say("#bp-key-r", "Restarting… this page reconnects in a few seconds."); });
    const restart = host.querySelector("#bp-restart");
    if (restart) restart.addEventListener("click", async () => {
      restart.disabled = true;
      await api.post("/control/shutdown").catch(() => {});      // the app starts the backend again
      say("#bp-restart-r", "Restarting… this page reconnects in a few seconds.");
    });
    const save = host.querySelector("#bp-key-save"), input = host.querySelector("#bp-key");
    save.addEventListener("click", async () => {
      const key = input.value.trim();
      if (!key) { say("#bp-key-r", "Paste the key first."); return; }
      save.disabled = true; say("#bp-key-r", "Checking the key with Ollama…");
      const r = await api.post("/ui/models/key", { key }).catch(() => null);
      save.disabled = false;
      if (r && r.ok) { input.value = ""; draw(r); onChange && onChange(r); say("#bp-key-r", r.note || "Saved. The key works."); }
      else say("#bp-key-r", (r && r.error) || "I couldn't check the key.");
    });
    const remove = host.querySelector("#bp-key-remove");
    if (remove) remove.addEventListener("click", async () => {
      const r = await api.post("/ui/models/key", { action: "remove" }).catch(() => null);
      if (r && r.ok) { draw(r); onChange && onChange(r); say("#bp-key-r", "Key removed from this PC."); }
    });
    const model = host.querySelector("#bp-model");
    model.addEventListener("change", async () => {
      if (await ctx.save({ cloud: { brain_model: model.value.trim() } })) { await load(); say("#bp-key-r", "Saved. The new model is used from the next message."); }
      else load();
    });
    const plan = host.querySelector("#bp-plan");
    plan.addEventListener("click", async () => {
      plan.disabled = true; say("#bp-plan-r", "Trying each model with a few words…");
      const picks = [...new Set([c.brain_model, c.model, c.vision_model])];
      const r = await api.post("/ui/models/catalog", { models: picks }).catch(() => null);
      plan.disabled = false;
      say("#bp-plan-r", r && r.ok ? Object.entries(r.plan).map(([m, v]) => `${m}: ${v}`).join(" · ") : ((r && r.error) || "I couldn't check."));
    });
  };

  await load(false);
  return { reload: load };
}
