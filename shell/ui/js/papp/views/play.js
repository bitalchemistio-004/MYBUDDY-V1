// Play: your level and streak, the games, and the badges page (#/play/badges). Everything here comes from what you really did
// (store.rewards): nothing is shown as earned that was not.
import { icon } from "../icons.js";
import { GAMES, gameCard } from "../games.js";
import questView from "../quest.js";
import { esc } from "../util.js";

export default {
  render(el, ctx, sub = []) {
    const { store } = ctx;
    const draw = () => { sub[0] === "badges" ? badges() : sub[0] === "quest" ? questView.render(el, ctx) : home(); };
    if (sub[0] === "daily") setTimeout(() => { const d = store.daily.today(); ctx.bubble("game", `daily:${d.game}:${d.level}`); }, 350);

    const dailyCard = () => {
      const d = store.daily.today(), g = GAMES.find((x) => x.id === d.game), q = store.quest.state();
      return `<div class="pa-daily${d.done ? " done" : ""}"><div><small>Daily game</small><b>${g ? g.name : d.game}</b><span>${d.done ? "Done today. Back tomorrow." : `Level ${d.level} · +60 xp and 2 stars`}</span></div>
        ${d.done ? `<i class="pa-daily-tick">${icon("check")}</i>` : `<button class="pa-btn go" id="p-daily" type="button">Play</button>`}</div>
        <div class="pa-statrow"><span><b>${q.stars}</b> quest stars</span><span><b>${r0().freezes}</b> streak freeze${r0().freezes === 1 ? "" : "s"}</span></div>`;
    };
    const r0 = () => store.rewards.state();
    const home = () => {
      const r = store.rewards.state();
      el.innerHTML = `<header class="pa-head"><h1>Play</h1><p>Games, your streak and your badges</p></header>
        <section class="pa-level">
          <div class="pa-ring" style="--p:${r.pct}"><span><b>${r.level}</b><small>level</small></span></div>
          <div class="grow"><div class="pa-streak">${icon("flame")}${r.streak ? `<b>${r.streak}</b> day${r.streak === 1 ? "" : "s"} in a row` : "Finish a task to start a streak"}</div>
            <div class="pa-note">${r.xp} xp. ${r.toNext} more for level ${r.level + 1}.</div>
            <a class="pa-btn" href="#/play/badges">${icon("trophy")} Badges · ${r.earned} of ${r.total}</a></div>
        </section>
        ${dailyCard()}
        <h3 class="pa-day">Games</h3>
        <div class="pa-games">${GAMES.map((g, i) => gameCard(g, i === 0, store.games.get(g.id))).join("")}</div>
        <p class="pa-note" style="margin-top:12px">Games open in the bubble, so you stay where you were. Winning one earns xp.</p>`;
      const pd = el.querySelector("#p-daily"); if (pd) pd.addEventListener("click", () => { const d = store.daily.today(); ctx.bubble("game", `daily:${d.game}:${d.level}`); });
      el.querySelectorAll("[data-g]").forEach((b) => b.addEventListener("click", () => (b.dataset.g === "quest" ? ctx.go("play/quest") : ctx.bubble("game", b.dataset.g))));
    };

    const badges = () => {
      const r = store.rewards.state();
      el.innerHTML = `<header class="pa-subhead"><a class="pa-back" href="#/play" aria-label="Back to Play"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg><span>Play</span></a><h1>Badges</h1></header>
        <p class="pa-note">${r.earned} of ${r.total} earned · level ${r.level} · ${r.streak}-day streak</p>
        <div class="pa-badges">${r.badges.map((b) => `<div class="pa-badge${b.on ? " on" : ""}">${icon(b.icon)}<b>${esc(b.name)}</b><span>${b.on ? "Earned " + esc(b.when) : esc(b.how)}</span></div>`).join("")}</div>`;
    };

    draw();
    const off = store.onChange((what) => { if ((what === "rewards" || what === "games") && sub[0] !== "quest") draw(); });
    this._off = off;
  },
  destroy() { if (this._off) { this._off(); this._off = null; } },
};
