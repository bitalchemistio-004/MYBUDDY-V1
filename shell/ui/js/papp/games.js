// The games, played inside the buddy's bubble. Each one is a climb, not a one-off: it has a level (kept in store.games), the level sets
// how hard it is (params), winning moves you up and a loss lets you try the same level again. While you play the buddy comments on what
// really happens (o.say). A game is (box, o) with o = { level, say(event, hint), end(won, { score, text }) }; the shell (gameshell.js)
// provides o, shows the level and the buddy's line, and the result panel with Next level or Try again.
import { gameArt } from "./pixart.js";

export const GAMES = [
  { id: "quest", name: "Buddy Quest", note: "Fight through the dungeon floor by floor.", kind: "Adventure", full: true },
  { id: "snake", name: "Snake", note: "Eat the apples.", kind: "Arcade" },
  { id: "brick", name: "Breakout", note: "Bounce and smash.", kind: "Arcade" },
  { id: "stars", name: "Catch the stars", note: "Twenty seconds.", kind: "Arcade" },
  { id: "tap", name: "Tap fast", note: "Beat the clock.", kind: "Reflex" },
  { id: "simon", name: "Buddy says", note: "Watch, then repeat.", kind: "Memory" },
  { id: "mind", name: "Mind reader", note: "Can you out-think me?", kind: "Mind" },
  { id: "words", name: "Word shuffle", note: "Put the letters back.", kind: "Words" },
  { id: "hot", name: "Hot and cold", note: "Find the hidden square.", kind: "Mind" },
  { id: "lights", name: "Lights out", note: "Switch them all off.", kind: "Puzzle" },
  { id: "ttt", name: "Tic-tac-toe", note: "Against me.", kind: "Classic" },
];

/** One game as a card: its dot picture, what kind it is, its name and a line, and the level you have reached. [rec] is store.games.get(id). */
export const gameCard = (g, wide = false, rec = null) => `<button class="pa-game${wide ? " wide" : ""}" data-g="${g.id}" type="button">
  <span class="pa-game-art">${gameArt(g.id)}</span>
  <span class="pa-game-text"><small>${g.kind}${rec && rec.plays ? ` · Level ${rec.level}` : ""}</small><b>${g.name}</b><span>${g.note}</span></span></button>`;

// ---- how hard each game is at each level (pure, tested) ----
export function params(id, L) {
  const l = Math.max(1, Math.floor(L || 1)), k = l - 1;
  switch (id) {
    case "tap": return { targets: 8 + Math.min(k, 10), limit: Math.max(6, 12 - 0.6 * k) };
    case "simon": return { rounds: Math.min(4 + l, 14), speed: Math.max(0.45, 1 - 0.06 * k) };
    case "mind": return { rounds: 15, max: Math.max(3, 7 - k) };
    case "stars": return { need: 8 + 2 * Math.min(k, 8), fall: 1 + 0.08 * k, time: 20 };
    case "hot": return { size: l < 4 ? 5 : l < 8 ? 6 : 7, taps: Math.max(3, 7 - Math.floor(k / 2)) };
    case "lights": return { presses: Math.min(3 + l, 12) };
    case "words": return { count: 5, minLen: 4 + Math.floor(k / 3) };
    case "snake": return { need: Math.min(6 + 2 * k, 30), ms: Math.max(70, 200 - 12 * k) };
    case "brick": return { rows: Math.min(6, 2 + l), speed: 1 + 0.07 * k };
    case "ttt": return { mistake: Math.max(0.1, 0.55 - 0.1 * k) };
    default: return {};
  }
}

// ---- the small pure parts, kept apart so they can be tested ----
/** What the buddy bets you will press next ("L" or "R"): it looks at what followed your last two presses before, else guesses. */
export function predict(hist) {
  if (hist.length < 3) return Math.random() < 0.5 ? "L" : "R";
  const key = hist.slice(-2).join(""), c = { L: 0, R: 0 };
  for (let i = 0; i + 2 < hist.length; i++) if (hist[i] + hist[i + 1] === key) c[hist[i + 2]]++;
  if (c.L === c.R) return Math.random() < 0.5 ? "L" : "R";
  return c.L > c.R ? "L" : "R";
}
/** How warm a square is, by its distance (in steps) from the hidden one. */
export const heat = (d) => (d === 0 ? "Found it" : d === 1 ? "Burning" : d === 2 ? "Hot" : d <= 4 ? "Warm" : d <= 6 ? "Cool" : "Cold");
/** A lights-out board that can always be solved: start from all off and press [n] distinct random squares. */
export function lightsBoard(rand = Math.random, size = 4, n = 5) {
  const b = Array(size * size).fill(0);
  const press = (i) => {
    const r = Math.floor(i / size), c = i % size;
    for (const [dr, dc] of [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]]) { const rr = r + dr, cc = c + dc; if (rr >= 0 && rr < size && cc >= 0 && cc < size) b[rr * size + cc] ^= 1; }
  };
  const picks = new Set(); while (picks.size < Math.min(n, size * size)) picks.add(Math.floor(rand() * size * size));
  picks.forEach(press);
  return { board: b, press };
}
export const WORDS = ["BUDDY", "ROBOT", "NOTCH", "PIXEL", "ORBIT", "SPARK", "CLOUD", "VOICE", "CHIME", "GLOW", "TASK", "NOTE", "TIMER", "PLAY", "STAR", "MOOD", "SMART", "EMBER", "NIGHT", "WAKE", "DREAM", "TOUCH", "FOCUS", "BRAIN", "SOUND", "LIGHT", "SCREEN", "WIDGET", "ALARM", "CALL", "ANDROID", "BATTERY", "NETWORK", "MESSAGE", "CALENDAR", "REMINDER", "ASSISTANT", "KEYBOARD", "SPEAKER", "NOTIFY"];
/** The letters of a word in a different order. */
export function shuffle(word, rand = Math.random) {
  if (new Set(word).size < 2) return word;
  let a;
  do { a = word.split(""); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } } while (a.join("") === word);
  return a.join("");
}
const LINES3 = [[0, 1, 2], [3, 4, 5], [6, 7, 8], [0, 3, 6], [1, 4, 7], [2, 5, 8], [0, 4, 8], [2, 4, 6]];
/** The winner of a tic-tac-toe board ("X", "O" or ""). */
export const tttWinner = (b) => { for (const l of LINES3) if (b[l[0]] && b[l[0]] === b[l[1]] && b[l[0]] === b[l[2]]) return b[l[0]]; return ""; };
/** The cells where [p] would win next move. */
export const tttThreats = (b, p) => b.map((v, i) => (v ? -1 : (tttWinner(b.map((x, j) => (j === i ? p : x))) === p ? i : -1))).filter((i) => i >= 0);
/** The best move for "O" (minimax; the board is tiny). */
export function tttBest(b) {
  const score = (bd, turn) => {
    const w = tttWinner(bd); if (w === "O") return 1; if (w === "X") return -1; if (bd.every(Boolean)) return 0;
    const sc = bd.map((v, i) => (v ? null : score(bd.map((x, j) => (j === i ? turn : x)), turn === "O" ? "X" : "O"))).filter((x) => x !== null);
    return turn === "O" ? Math.max(...sc) : Math.min(...sc);
  };
  let best = -2, move = -1;
  b.forEach((v, i) => { if (v) return; const s = score(b.map((x, j) => (j === i ? "O" : x)), "X"); if (s > best) { best = s; move = i; } });
  return move;
}
/** Snake: one step. Returns the new state, or { dead: true } on a wall or itself. */
export function snakeStep(st, dir, rand = Math.random) {
  const head = st.body[0], d = { U: [0, -1], D: [0, 1], L: [-1, 0], R: [1, 0] }[dir];
  const nx = head[0] + d[0], ny = head[1] + d[1];
  if (nx < 0 || ny < 0 || nx >= st.size || ny >= st.size) return { ...st, dead: true };
  const eats = nx === st.food[0] && ny === st.food[1];
  const body = [[nx, ny], ...st.body]; if (!eats) body.pop();
  if (body.slice(1).some((p) => p[0] === nx && p[1] === ny)) return { ...st, dead: true };
  let food = st.food;
  if (eats) {
    const free = []; for (let y = 0; y < st.size; y++) for (let x = 0; x < st.size; x++) if (!body.some((p) => p[0] === x && p[1] === y)) free.push([x, y]);
    food = free.length ? free[Math.floor(rand() * free.length)] : st.food;
  }
  return { ...st, body, food, ate: eats, dead: false };
}

const cellsHtml = (n, label) => `<button type="button" aria-label="${label}"></button>`.repeat(n);
const accentOf = () => getComputedStyle(document.documentElement).getPropertyValue("--mood").trim() || "#ffcc33";

// ---- Tic-tac-toe against me; I make mistakes less often as the level rises ----
export function ticTacToe(box, o) {
  const { mistake } = params("ttt", o.level);
  box.innerHTML = `<p class="pa-note" id="g-msg">Your move: you are the cross.</p><div class="pa-ttt" id="g-board">${cellsHtml(9, "Square")}</div>`;
  const cells = [...box.querySelectorAll("#g-board button")], b = Array(9).fill(""), msg = box.querySelector("#g-msg");
  const mark = (i, p) => { b[i] = p; cells[i].textContent = p === "X" ? "✕" : "○"; cells[i].dataset.p = p; };
  let locked = false;
  const over = () => {
    const w = tttWinner(b);
    if (w === "X") { msg.textContent = "Three in a row. You won!"; o.end(true, { score: 1, text: "You beat me at tic-tac-toe." }); return true; }
    if (w === "O") { msg.textContent = "I got three in a row."; o.end(false, { text: "I won this one." }); return true; }
    if (b.every(Boolean)) { msg.textContent = "A draw."; o.end(false, { text: "A draw. Nobody won, so the level stays." }); return true; }
    return false;
  };
  cells.forEach((c, i) => c.addEventListener("click", () => {
    if (locked || b[i]) return;
    const threats = tttThreats(b, "O");
    mark(i, "X");
    if (over()) return;
    if (threats.length && !threats.includes(i)) o.say("bad", "You left my line open.");
    else if (threats.includes(i)) o.say("good", "Good block.");
    locked = true;
    setTimeout(() => {
      const free = b.map((v, k) => (v ? -1 : k)).filter((k) => k >= 0);
      const m = Math.random() < mistake ? free[Math.floor(Math.random() * free.length)] : tttBest(b);
      mark(m, "O"); locked = false;
      if (over()) return;
      if (tttThreats(b, "O").length) o.say("near", "Careful, I have two lined up.");
    }, 340);
  }));
}

// ---- Tap fast: tap the lit square before the time is up ----
export function tapFast(box, o) {
  const { targets, limit } = params("tap", o.level);
  box.innerHTML = `<p class="pa-note" id="g-msg">Tap the lit squares. ${targets} of them in ${limit.toFixed(1)} seconds.</p><div class="pa-tap" id="g-board">${cellsHtml(16, "Square")}</div>`;
  const cells = [...box.querySelectorAll("#g-board button")], msg = box.querySelector("#g-msg");
  let n = 0, lit = -1, t0 = 0, done = false, timer = 0, misses = 0, warned = false;
  const finish = (won) => {
    if (done) return; done = true; clearInterval(timer); cells.forEach((c) => c.classList.remove("lit"));
    const s = ((Date.now() - t0) / 1000).toFixed(1);
    if (won) { msg.textContent = `${targets} in ${s} seconds.`; o.end(true, { score: Math.round(1000 / Math.max(1, +s)), text: `${targets} squares in ${s} s.` }); }
    else { msg.textContent = `Time. ${n} of ${targets}.`; o.end(false, { text: `Time ran out at ${n} of ${targets}.` }); }
  };
  const next = () => {
    cells.forEach((c) => c.classList.remove("lit"));
    if (n >= targets) return finish(true);
    let k; do { k = Math.floor(Math.random() * 16); } while (k === lit);
    lit = k; cells[lit].classList.add("lit");
  };
  cells.forEach((c, i) => c.addEventListener("click", () => {
    if (done) return;
    if (!t0) {
      t0 = Date.now();
      timer = setInterval(() => {
        if (!box.isConnected) return clearInterval(timer);
        const left = limit - (Date.now() - t0) / 1000;
        if (left <= 0) finish(false);
        else if (left < 2.5 && n < targets - 2 && !warned) { warned = true; o.say("near", "Hurry!"); }
      }, 250);
    }
    if (i === lit) { n++; msg.textContent = `${n} of ${targets}`; misses = 0; if (n === Math.ceil(targets / 2)) o.say("good", "Halfway."); next(); }
    else { misses++; if (misses === 2) o.say("bad", "Watch the lit square."); }
  }));
  next();
}

// ---- Buddy says: repeat the lights ----
export function buddySays(box, o) {
  const { rounds, speed } = params("simon", o.level);
  box.innerHTML = `<p class="pa-note" id="g-msg">Watch the lights.</p><div class="pa-simon" id="g-board">${[0, 1, 2, 3].map((i) => `<button type="button" data-i="${i}" aria-label="Pad ${i + 1}">${gameArt("pad" + i)}</button>`).join("")}</div>`;
  const pads = [...box.querySelectorAll("#g-board button")], msg = box.querySelector("#g-msg");
  const seq = []; let at = 0, locked = true, ended = false;
  const flash = (i, ms) => new Promise((r) => { pads[i].classList.add("on"); setTimeout(() => { pads[i].classList.remove("on"); setTimeout(r, 120 * speed); }, ms); });
  const round = async () => {
    locked = true; seq.push(Math.floor(Math.random() * 4)); at = 0; msg.textContent = `Round ${seq.length} of ${rounds}. Watch.`;
    await new Promise((r) => setTimeout(r, 450));
    for (const i of seq) { if (!box.isConnected) return; await flash(i, 340 * speed); }
    locked = false; msg.textContent = "Your turn.";
  };
  pads.forEach((p, i) => p.addEventListener("click", () => {
    if (locked || ended) return;
    flash(i, 170);
    if (i !== seq[at]) { ended = true; locked = true; msg.textContent = `Missed it in round ${seq.length}.`; o.end(false, { text: `You got ${seq.length - 1} of ${rounds} rounds.` }); return; }
    at++;
    if (at === seq.length) {
      if (seq.length >= rounds) { ended = true; locked = true; msg.textContent = "Every round. You win!"; o.end(true, { score: rounds, text: `All ${rounds} rounds.` }); return; }
      o.say(seq.length >= rounds - 1 ? "near" : "good");
      round();
    }
  }));
  round();
}

// ---- Mind reader: out-think me ----
export function mindReader(box, o) {
  const { rounds, max } = params("mind", o.level);
  box.innerHTML = `<p class="pa-note" id="g-msg">I will guess your next press. Be unpredictable. I may read you at most ${max} times in ${rounds}.</p>
    <div class="pa-two"><button class="pa-btn" id="g-l" type="button">Left</button><button class="pa-btn" id="g-r" type="button">Right</button></div><p class="pa-note" id="g-score">I have guessed 0 of 0.</p>`;
  const msg = box.querySelector("#g-msg"), sc = box.querySelector("#g-score"), hist = [];
  let hits = 0, over = false;
  const press = (side) => {
    if (over) return;
    const g = predict(hist); hist.push(side);
    if (g === side) hits++;
    sc.textContent = `I have guessed ${hits} of ${hist.length}.`;
    msg.textContent = g === side ? `I knew it: ${side === "L" ? "left" : "right"}.` : `I said ${g === "L" ? "left" : "right"}. Not this time.`;
    if (hits > max) { over = true; o.end(false, { text: `I read you ${hits} times. The limit was ${max}.` }); return; }
    if (g === side && hist.length > 4 && hits === max) o.say("bad", "One more and I win.");
    else if (g !== side && hist.length % 5 === 0) o.say("good", "Still unreadable.");
    if (hist.length >= rounds) { over = true; o.end(true, { score: rounds - hits, text: `I only read you ${hits} of ${rounds}.` }); }
  };
  box.querySelector("#g-l").addEventListener("click", () => press("L"));
  box.querySelector("#g-r").addEventListener("click", () => press("R"));
}

// ---- Catch the stars ----
export function catchStars(box, o) {
  const { need, fall, time } = params("stars", o.level);
  box.innerHTML = `<p class="pa-note" id="g-msg">Slide your finger to catch the stars. 0 of ${need}</p><canvas class="pa-stars" id="g-cv" width="320" height="360"></canvas>`;
  const cv = box.querySelector("#g-cv"), g = cv.getContext("2d"), msg = box.querySelector("#g-msg"), accent = accentOf();
  let x = 160, stars = [], caught = 0, missed = 0, spawn = 0, done = false, t0 = 0, last = 0, streak = 0;
  const move = (e) => { const r = cv.getBoundingClientRect(); x = Math.max(30, Math.min(290, ((e.clientX - r.left) / r.width) * 320)); };
  cv.addEventListener("pointerdown", move); cv.addEventListener("pointermove", move);
  const frame = (now) => {
    if (done || !box.isConnected) return;
    if (!t0) { t0 = now; last = now; }
    const dt = Math.min(0.05, (now - last) / 1000); last = now; spawn -= dt;
    if (spawn <= 0) { stars.push({ x: 16 + Math.random() * 288, y: -10, v: (120 + Math.random() * 110) * fall }); spawn = (0.45 + Math.random() * 0.35) / Math.sqrt(fall); }
    g.clearRect(0, 0, 320, 360);
    for (const s of stars) { s.y += s.v * dt; g.fillStyle = accent; g.beginPath(); g.arc(s.x, s.y, 8, 0, 7); g.fill(); }
    stars = stars.filter((s) => {
      if (s.y > 326 && s.y < 352 && Math.abs(s.x - x) < 36) {
        caught++; streak++; msg.textContent = `Slide your finger to catch the stars. ${caught} of ${need}`;
        if (streak === 4) o.say("streak"); else if (caught === need - 2) o.say("near");
        return false;
      }
      if (s.y >= 372) { missed++; streak = 0; if (missed % 3 === 0) o.say("bad", "Stay under them."); return false; }
      return true;
    });
    g.fillStyle = "#f5f3ee"; g.fillRect(x - 30, 338, 60, 10);
    const left = time - (now - t0) / 1000;
    g.fillStyle = "#9a9a94"; g.font = "600 14px sans-serif"; g.fillText(`${Math.max(0, Math.ceil(left))} s`, 8, 20);
    if (caught >= need) { done = true; msg.textContent = `${need} stars. You win!`; o.end(true, { score: caught, text: `${need} stars with ${Math.max(0, Math.ceil(left))} s to spare.` }); return; }
    if (left <= 0) { done = true; msg.textContent = `Time. ${caught} of ${need}.`; o.end(false, { text: `You caught ${caught} of ${need}.` }); return; }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

// ---- Hot and cold ----
export function hotCold(box, o) {
  const { size, taps: allowed } = params("hot", o.level), N = size * size;
  box.innerHTML = `<p class="pa-note" id="g-msg">Find the hidden square in ${allowed} taps.</p><div class="pa-hot" id="g-board" style="--n:${size}">${cellsHtml(N, "Square")}</div>`;
  const cells = [...box.querySelectorAll("#g-board button")], msg = box.querySelector("#g-msg");
  const target = Math.floor(Math.random() * N);
  let taps = 0, over = false;
  cells.forEach((c, i) => c.addEventListener("click", () => {
    if (over || c.dataset.s) return;
    taps++;
    const d = Math.abs(Math.floor(i / size) - Math.floor(target / size)) + Math.abs((i % size) - (target % size));
    c.dataset.s = d === 0 ? "found" : d <= 2 ? "hot" : d <= 4 ? "warm" : "cold"; c.textContent = d === 0 ? "" : String(d);
    if (d === 0) {
      over = true; msg.textContent = `Found it in ${taps}!`;
      if (taps <= allowed) o.end(true, { score: allowed - taps + 1, text: `Found in ${taps} of ${allowed} taps.` });
      else o.end(false, { text: `Found it in ${taps}, but ${allowed} was the limit.` });
      return;
    }
    const left = allowed - taps;
    msg.textContent = `${heat(d)}. ${left > 0 ? `${left} tap${left === 1 ? "" : "s"} left.` : "Out of taps."}`;
    if (left <= 0) { over = true; o.end(false, { text: `Not found in ${allowed} taps.` }); return; }
    if (d >= 6) o.say("bad", "That's cold. Try the other side.");
    else if (d <= 1) o.say("near", "Right next to it.");
  }));
}

// ---- Lights out ----
export function lightsOut(box, o) {
  const { presses } = params("lights", o.level);
  const { board, press } = lightsBoard(Math.random, 4, presses);
  box.innerHTML = `<p class="pa-note" id="g-msg">Switch every light off. Moves: 0</p><div class="pa-lights" id="g-board">${cellsHtml(16, "Light")}</div>`;
  const cells = [...box.querySelectorAll("#g-board button")], msg = box.querySelector("#g-msg");
  let moves = 0, over = false;
  const lit = () => board.reduce((a, v) => a + v, 0);
  const draw = () => cells.forEach((c, i) => c.classList.toggle("lit", !!board[i]));
  cells.forEach((c, i) => c.addEventListener("click", () => {
    if (over) return;
    const before = lit(); press(i); moves++; draw();
    const after = lit(); msg.textContent = `Switch every light off. Moves: ${moves}`;
    if (after === 0) { over = true; msg.textContent = `All off in ${moves} moves!`; o.end(true, { score: Math.max(1, 40 - moves), text: `All off in ${moves} moves.` }); return; }
    if (after - before >= 3) o.say("bad", "That lit more than it cleared.");
    else if (after <= 2) o.say("near", "Almost dark.");
    if (moves >= presses * 4) { over = true; o.end(false, { text: "That took too many moves. A fresh board is next." }); }
  }));
  draw();
}

// ---- Word shuffle ----
export function wordShuffle(box, o) {
  const { count, minLen } = params("words", o.level);
  const pool = WORDS.filter((w) => w.length >= minLen), pick = pool.length >= count ? pool : WORDS;
  const words = [...pick].sort(() => Math.random() - 0.5).slice(0, count);
  let n = 0, built = "", tries = 0;
  const show = () => {
    const w = words[n], letters = shuffle(w).split("");
    box.innerHTML = `<p class="pa-note" id="g-msg">Word ${n + 1} of ${count}. Tap the letters in order.</p><div class="pa-built" id="g-built">${w.split("").map((_, i) => `<span>${built[i] || ""}</span>`).join("")}</div>
      <div class="pa-tiles" id="g-tiles">${letters.map((l, i) => `<button type="button" data-i="${i}">${l}</button>`).join("")}</div><button class="pa-btn" id="g-clear" type="button">Clear</button>`;
    box.querySelectorAll("#g-tiles button").forEach((b) => b.addEventListener("click", () => {
      built += b.textContent; b.disabled = true;
      box.querySelectorAll("#g-built span").forEach((s, i) => { s.textContent = built[i] || ""; });
      if (built.length !== w.length) return;
      if (built === w) {
        n++; built = ""; tries = 0;
        if (n >= count) { box.innerHTML = `<p class="pa-note">All ${count} words. You win!</p>`; o.end(true, { score: count, text: `All ${count} words.` }); }
        else { o.say(n === count - 1 ? "near" : "good"); setTimeout(show, 350); }
      } else {
        tries++; box.querySelector("#g-msg").textContent = "Not quite. Clear and try again.";
        o.say("bad", tries > 1 ? "Think about the first letter." : "");
      }
    }));
    box.querySelector("#g-clear").addEventListener("click", () => { built = ""; show(); });
  };
  show();
}

// ---- Snake ----
export function snake(box, o) {
  const { need, ms } = params("snake", o.level), SIZE = 14;
  box.innerHTML = `<p class="pa-note" id="g-msg">Eat ${need} apples. Swipe or use the arrows.</p><canvas class="pa-stars" id="g-cv" width="280" height="280"></canvas>
    <div class="pa-dpad"><button type="button" data-d="U" aria-label="Up">Up</button><button type="button" data-d="L" aria-label="Left">Left</button><button type="button" data-d="D" aria-label="Down">Down</button><button type="button" data-d="R" aria-label="Right">Right</button></div>`;
  const cv = box.querySelector("#g-cv"), g = cv.getContext("2d"), msg = box.querySelector("#g-msg"), cell = 280 / SIZE, accent = accentOf();
  let st = { size: SIZE, body: [[7, 7], [6, 7], [5, 7]], food: [10, 7] }, dir = "R", want = "R", eaten = 0, over = false;
  const opp = { U: "D", D: "U", L: "R", R: "L" };
  const turn = (d) => { if (d !== opp[dir]) want = d; };
  box.querySelectorAll("[data-d]").forEach((b) => b.addEventListener("click", () => turn(b.dataset.d)));
  let sx = 0, sy = 0;
  cv.addEventListener("pointerdown", (e) => { sx = e.clientX; sy = e.clientY; });
  cv.addEventListener("pointerup", (e) => {
    const dx = e.clientX - sx, dy = e.clientY - sy; if (Math.max(Math.abs(dx), Math.abs(dy)) < 18) return;
    turn(Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "R" : "L") : (dy > 0 ? "D" : "U"));
  });
  const paint = () => {
    g.clearRect(0, 0, 280, 280);
    g.fillStyle = "#ef4444"; g.beginPath(); g.arc((st.food[0] + .5) * cell, (st.food[1] + .5) * cell, cell * .36, 0, 7); g.fill();
    st.body.forEach((p, i) => { g.fillStyle = i ? accent : "#f5f3ee"; g.fillRect(p[0] * cell + 1, p[1] * cell + 1, cell - 2, cell - 2); });
  };
  const tick = () => {
    if (over || !box.isConnected) return;
    dir = want; const n = snakeStep(st, dir);
    if (n.dead) { over = true; msg.textContent = `Crashed with ${eaten}.`; o.end(false, { text: `You ate ${eaten} of ${need} before crashing.` }); return; }
    st = n;
    if (n.ate) { eaten++; msg.textContent = `${eaten} of ${need} apples.`; if (eaten === need - 1) o.say("near"); else if (eaten % 4 === 0) o.say("streak"); }
    paint();
    if (eaten >= need) { over = true; msg.textContent = `${need} apples. You win!`; o.end(true, { score: eaten, text: `${need} apples.` }); return; }
    setTimeout(tick, Math.max(60, ms - eaten * 3));
  };
  paint(); setTimeout(tick, 500);
}

// ---- Breakout ----
export function breakout(box, o) {
  const { rows, speed } = params("brick", o.level);
  box.innerHTML = `<p class="pa-note" id="g-msg">Slide to bounce the ball. Clear every brick. Lives: 3</p><canvas class="pa-stars" id="g-cv" width="320" height="360"></canvas>`;
  const cv = box.querySelector("#g-cv"), g = cv.getContext("2d"), msg = box.querySelector("#g-msg"), accent = accentOf();
  const bricks = []; for (let r = 0; r < rows; r++) for (let c = 0; c < 7; c++) bricks.push({ x: 8 + c * 44, y: 40 + r * 22, w: 40, h: 16, on: true, r });
  let px = 160, bx = 160, by = 300, vx = 150 * speed, vy = -210 * speed, lives = 3, done = false, last = 0, serve = 0.8, streak = 0;
  const move = (e) => { const r = cv.getBoundingClientRect(); px = Math.max(30, Math.min(290, ((e.clientX - r.left) / r.width) * 320)); };
  cv.addEventListener("pointerdown", move); cv.addEventListener("pointermove", move);
  const frame = (now) => {
    if (done || !box.isConnected) return;
    if (!last) last = now;
    const dt = Math.min(0.04, (now - last) / 1000); last = now;
    if (serve > 0) serve -= dt; else { bx += vx * dt; by += vy * dt; }
    if (bx < 6 || bx > 314) { vx = -vx; bx = Math.max(6, Math.min(314, bx)); }
    if (by < 6) { vy = Math.abs(vy); by = 6; }
    if (vy > 0 && by > 332 && by < 346 && Math.abs(bx - px) < 38) { vy = -Math.abs(vy); vx = ((bx - px) / 38) * 230 * speed; streak = 0; }
    for (const b of bricks) if (b.on && bx > b.x - 5 && bx < b.x + b.w + 5 && by > b.y - 5 && by < b.y + b.h + 5) {
      b.on = false; vy = -vy; streak++;
      const left = bricks.filter((k) => k.on).length;
      if (streak === 5) o.say("streak"); else if (left === 5) o.say("near");
      break;
    }
    if (by > 372) {
      lives--; streak = 0; msg.textContent = `Slide to bounce the ball. Clear every brick. Lives: ${lives}`;
      if (lives <= 0) { done = true; msg.textContent = "Out of lives."; o.end(false, { text: `${bricks.filter((k) => !k.on).length} of ${bricks.length} bricks cleared.` }); return; }
      o.say("bad", "Keep under the ball.");
      bx = px; by = 300; vx = 150 * speed; vy = -210 * speed; serve = 0.8;
    }
    g.clearRect(0, 0, 320, 360);
    bricks.forEach((b) => { if (b.on) { g.fillStyle = b.r % 3 === 0 ? accent : b.r % 3 === 1 ? "#f5f3ee" : "#6b7280"; g.fillRect(b.x, b.y, b.w, b.h); } });
    g.fillStyle = "#f5f3ee"; g.fillRect(px - 32, 340, 64, 9);
    g.beginPath(); g.arc(bx, by, 6, 0, 7); g.fill();
    if (bricks.every((b) => !b.on)) { done = true; msg.textContent = "Every brick gone. You win!"; o.end(true, { score: lives, text: `Cleared with ${lives} li${lives === 1 ? "fe" : "ves"} left.` }); return; }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

const RUN = { snake, brick: breakout, ttt: ticTacToe, tap: tapFast, simon: buddySays, mind: mindReader, stars: catchStars, hot: hotCold, lights: lightsOut, words: wordShuffle };
/** Start game [id] in [box]. The quest has its own full-screen page (quest.js), so it is not run from here. */
export const play = (id, box, o) => (RUN[id] || tapFast)(box, o);
