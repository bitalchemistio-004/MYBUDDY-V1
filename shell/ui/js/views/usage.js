// Settings, Usage: which models were used, for what, how many tokens, and what that would cost.
// Everything comes from /ui/models/usage (core/cloud.py, read from the log every cloud call is written to).
// The model on this PC isn't counted: it costs nothing.
import { api } from "../api.js";
import { esc } from "./ui.js";

const fmt = (n) => Number(n || 0).toLocaleString();
const PURPOSE = {
  brain: "Everyday thinking (cloud brain)", ask: "Hard questions (bigger model)", about: "Questions about your clipboard, files or screen",
  screen: "Looking at the screen", test: "Test button", "key check": "Checking a key", "plan check": "Checking the plan", info: "Model information", other: "Other",
};
const money = (v) => (v == null ? "no price listed" : v < 0.01 ? "under 1 cent" : `$${v.toFixed(2)}`);

export default {
  async render(el, ctx) {
    let days = 7;
    el.innerHTML = `<div class="top"><div><p class="sub">What the cloud models were used for. Questions answered by the model on this PC aren't counted: they cost nothing.</p></div>
      <div class="seg" role="group" aria-label="Period" id="us-range">${[[7, "7 days"], [30, "30 days"]].map(([d, l]) => `<button type="button" data-days="${d}" aria-pressed="${d === days}">${l}</button>`).join("")}</div></div>
      <div class="cards" id="us-cards"><section class="lcard c12"><p class="none">Loading…</p></section></div>`;
    const root = el.querySelector("#us-cards");

    const draw = (u, models) => {
      const t = u.total, today = u.today, max = Math.max(1, ...u.by_day.map((d) => d.tokens_in + d.tokens_out));
      const used = today.tokens_in + today.tokens_out;
      const meter = (n, of) => `<span class="dotrow" aria-hidden="true">${Array.from({ length: 12 }, (_, i) => `<i class="${i < Math.round(12 * Math.min(1, n / of)) ? "on" : ""}"></i>`).join("")}</span>`;
      const route = models.running === "cloud" ? "in the cloud" : "on this PC";
      root.innerHTML = `
        <section class="lcard c4"><h3>Today</h3><div class="big">${fmt(used)}</div><div class="muted">tokens · ${fmt(today.calls)} calls</div>
          ${u.cap ? `${meter(used, u.cap)}<div class="muted">of your limit of ${fmt(u.cap)} a day</div>` : `<div class="muted">No daily limit set. You can set one in Models.</div>`}</section>
        <section class="lcard c4"><h3>Last ${u.days} days</h3><div class="big">${fmt(t.tokens_in + t.tokens_out)}</div>
          <div class="muted">tokens · ${fmt(t.calls)} calls · ${fmt(t.tokens_in)} in, ${fmt(t.tokens_out)} out</div>
          <div class="muted">${t.failed ? `${fmt(t.failed)} failed or refused` : "none failed"}${t.calls ? ` · about ${(t.secs / t.calls).toFixed(1)} s each` : ""}</div></section>
        <section class="lcard c4"><h3>What it would cost</h3><div class="big">${u.unpriced.length && !u.estimate_usd ? "–" : money(u.estimate_usd)}</div>
          <div class="muted">at Ollama's list prices on a paid plan. On a free plan you aren't billed per token; the allowance isn't published.</div>
          ${u.unpriced.length ? `<div class="muted">No price known for ${esc(u.unpriced.join(", "))}, so they aren't in this number.</div>` : ""}</section>
        <section class="lcard c12"><h3>Tokens per day</h3>
          <div class="bars" role="img" aria-label="Tokens used per day over the last ${u.days} days; the busiest day was ${fmt(max)} tokens">
            ${u.by_day.map((d) => { const n = d.tokens_in + d.tokens_out; return `<div class="bar" title="${esc(d.day)}: ${fmt(n)} tokens, ${fmt(d.calls)} calls"><i style="height:${Math.max(n ? 4 : 1, Math.round(100 * n / max))}%"></i></div>`; }).join("")}</div>
          <div class="row between"><span class="muted">${esc(u.by_day[0].day)}</span><span class="muted">busiest day: ${fmt(max === 1 && !t.calls ? 0 : max)} tokens</span><span class="muted">${esc(u.by_day[u.by_day.length - 1].day)}</span></div></section>
        <section class="lcard c6"><h3>By model</h3>
          ${u.by_model.length ? `<div class="list">${u.by_model.map((m) => `<div class="item"><div class="grow"><div class="t">${esc(m.name)}</div><div class="s">${fmt(m.calls)} calls · ${fmt(m.tokens_in)} in · ${fmt(m.tokens_out)} out</div></div><span class="muted">${money(m.usd)}</span></div>`).join("")}</div>`
            : `<p class="none">Nothing has gone to the cloud in this period.</p>`}</section>
        <section class="lcard c6"><h3>By job</h3>
          ${u.by_purpose.length ? `<div class="list">${u.by_purpose.map((m) => `<div class="item"><div class="grow"><div class="t">${esc(PURPOSE[m.name] || m.name)}</div><div class="s">${fmt(m.calls)} calls</div></div><span class="muted">${fmt(m.tokens_in + m.tokens_out)} tokens</span></div>`).join("")}</div>`
            : `<p class="none">No jobs yet.</p>`}</section>
        <section class="lcard c12"><h3>Latest calls</h3>
          ${u.recent.length ? `<div class="list">${u.recent.map((r) => `<div class="item"><div class="grow"><div class="t">${esc(r.model)} · ${esc(PURPOSE[r.purpose || "other"] || r.purpose)}</div>
              <div class="s">${r.ok === false ? `<span class="err">failed${r.why ? `: ${esc(r.why)}` : ""}</span> · ` : ""}${fmt(r.in)} in · ${fmt(r.out)} out · ${esc(String(r.secs))} s</div></div>
              <span class="faint">${new Date(r.t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span></div>`).join("")}</div>`
            : `<p class="none">Nothing yet.</p>`}
          <p class="none">Right now I'm thinking ${route}. ${esc(u.local_note)}</p></section>`;
    };

    const load = async () => {
      const [r, m] = await Promise.all([api.get(`/ui/models/usage?days=${days}`).catch(() => null), api.get("/ui/models").catch(() => null)]);
      if (!r || !r.ok) { root.innerHTML = `<section class="lcard c12"><p class="none err">I couldn't read the usage log.</p></section>`; return; }
      draw(r.usage, (m && m.brain) || { running: "local" });
    };
    el.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => {
      days = Number(b.dataset.days);
      el.querySelectorAll("[data-days]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      load();
    }));
    await load();
  },
};
