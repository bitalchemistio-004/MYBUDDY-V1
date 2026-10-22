// The frame every quick game is played in: your level and record at the top, the buddy's line as you play, the game, and at the end
// a result with Next level (after a win) or Try again (after a loss). It is what turns a game from a one-shot into a climb.
import { play, GAMES, params } from "./games.js";
import { coach } from "./coach.js";
import { stillUrl } from "../buddy/sprite.js";
import { esc } from "./util.js";
import { buzz } from "./haptics.js";

/** xp for winning at a level: more the higher you are, never more than 120. */
export const winXp = (level) => Math.min(120, 25 + 10 * level);

/** What the level asks for, in a few words (shown under the level so the climb is visible). */
export function levelNote(id, level) {
  const p = params(id, level);
  switch (id) {
    case "tap": return `${p.targets} squares in ${p.limit.toFixed(1)} s`;
    case "simon": return `${p.rounds} rounds`;
    case "mind": return `I may read you ${p.max} times`;
    case "stars": return `${p.need} stars`;
    case "hot": return `${p.size} by ${p.size}, ${p.taps} taps`;
    case "lights": return `a board of ${p.presses} presses`;
    case "words": return `words of ${p.minLen}+ letters`;
    case "snake": return `${p.need} apples`;
    case "brick": return `${p.rows} rows of bricks`;
    case "ttt": return p.mistake > 0.4 ? "I'm easy to fool" : p.mistake > 0.2 ? "I rarely slip" : "I almost never slip";
    default: return "";
  }
}

/**
 * Play game [id] in [box]. [env] = { store, character, speak(text), onClose(), daily?: { level } }.
 * With [env.daily] it is today's game: a fixed level, no change to the game's own level, and a bonus (once) when won.
 * Every win is recorded (level up, streak, xp); a loss keeps the level.
 */
export function runGame(id, box, env) {
  const g = GAMES.find((x) => x.id === id) || { name: id };
  const { store } = env;
  const draw = (fromWin = false) => {
    const rec = store.games.get(id);
    if (env.daily) rec.level = env.daily.level;
    box.innerHTML = `<div class="gs">
      <div class="gs-top"><b>${env.daily ? "Daily game · level " : "Level "}${rec.level}</b><span>${esc(levelNote(id, rec.level))}</span><em>${rec.wins} win${rec.wins === 1 ? "" : "s"}${rec.streak > 1 ? ` · ${rec.streak} in a row` : ""}</em></div>
      <div class="gs-coach" aria-live="polite"><img alt="" src="${stillUrl(env.character)}"><p id="gs-line">${esc(coach(env.character, fromWin ? "level" : "start"))}</p></div>
      <div class="gs-play" id="gs-play"></div>
      <div class="gs-end" id="gs-end" hidden></div></div>`;
    const line = box.querySelector("#gs-line"), area = box.querySelector("#gs-play"), end = box.querySelector("#gs-end");
    let ended = false, lastSay = 0;
    const say = (event, hint = "", speak = false) => {
      if (!line.isConnected) return;
      const t = coach(env.character, event, hint);
      line.textContent = t; line.classList.remove("pop"); void line.offsetWidth; line.classList.add("pop");
      if (speak && env.speak) env.speak(t);
    };
    const o = {
      level: rec.level,
      say: (event, hint) => { const now = Date.now(); if (now - lastSay < 1400) return; lastSay = now; say(event, hint); },
      end: (won, { score = 0, text = "" } = {}) => {
        if (ended) return; ended = true;
        buzz(won ? "win" : "loss");
        if (env.daily) {
          // today's game: the bonus once, and the game's own level is not touched
          const first = won && !store.daily.today().done;
          if (first) { store.rewards.add(60, "Daily game", "game_on"); store.quest.addStars(2); store.daily.complete(); }
          say(won ? "win" : "lose", "", true);
          end.hidden = false;
          end.innerHTML = `<h3>${won ? "Daily game done" : "Not this time"}</h3><p>${esc(text)}</p>
            <p class="gs-meta">${won ? (first ? "+60 xp and 2 quest stars. Back tomorrow for the next one." : "Already done today. Back tomorrow.") : "You can try today's game again."}</p>
            <div class="gs-btns">${won ? "" : `<button class="pa-btn go" id="gs-again" type="button">Try again</button>`}<button class="pa-btn" id="gs-done" type="button">Done</button></div>`;
          const again = end.querySelector("#gs-again"); if (again) again.addEventListener("click", () => draw(false));
          end.querySelector("#gs-done").addEventListener("click", () => env.onClose && env.onClose());
          return;
        }
        const before = store.games.get(id);
        const after = store.games.record(id, { won, score });
        if (won) store.rewards.gameWon(`${g.name}, level ${before.level}`, winXp(before.level));
        say(won ? (after.streak > 1 ? "streak" : "win") : "lose", "", true);
        end.hidden = false;
        end.innerHTML = `<h3>${won ? "You won" : "Not this time"}</h3><p>${esc(text)}</p>
          <p class="gs-meta">${won ? `+${winXp(before.level)} xp · next is level ${after.level}` : `Still level ${after.level}. ${after.streak ? "" : "Streak reset."}`}</p>
          <div class="gs-btns"><button class="pa-btn go" id="gs-again" type="button">${won ? "Next level" : "Try again"}</button><button class="pa-btn" id="gs-done" type="button">Done</button></div>`;
        end.querySelector("#gs-again").addEventListener("click", () => draw(won));
        end.querySelector("#gs-done").addEventListener("click", () => env.onClose && env.onClose());
      },
    };
    play(id, area, o);
  };
  draw(false);
}
