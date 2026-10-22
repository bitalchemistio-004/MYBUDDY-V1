// The three little games the console in the room plays, and the month view the wall calendar opens. They open over the room, are never
// shown in the page behind, and pay no points: a game only keeps its best score and a trophy (core/room.py), so nothing here can be farmed.
// The month view shows your real tasks and reminders with a due date (/ui/tasks), nothing made up.
import { pixelGroup } from "../buddy/wardrobe-pixel.js";
import { PAL } from "./art.js";

export const GAMES = [
  { id: "catch", name: "Star catcher", level: 3, how: "Move with the mouse or the arrow keys. Catch stars, dodge bombs. 30 seconds." },
  { id: "pong", name: "Pong with the buddy", level: 5, how: "Mouse or up and down. First to five." },
  { id: "memory", name: "Memory", level: 7, how: "Find the eight pairs in as few moves as you can." },
];
export const TROPHY = { catch: "Star cup", pong: "Pong cup", memory: "Memory cup" };
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const BODY = { blink: [63, 65, 194, 94], void: [66, 38, 199, 149], glitch: [95, 59, 131, 107], cinder: [83, 7, 154, 170], oracle: [69, 44, 182, 136] };   // where each body is in the 320 x 240 frame
const SHEET = new URL("../../assets/buddy/chars/", import.meta.url);

/** One open layer at a time. host: { layer, character, name, best, toast, record(id, score, lower), trophy(id) }; returns close(). */
export function openLayer(host, title, html, onKey) {
  const L = host.layer;
  L.hidden = false;
  L.innerHTML = `<div class="gwin"><div class="gtop"><b>${esc(title)}</b><button class="btn small" type="button" data-g="close">Close</button></div><div class="gbody">${html}</div></div>`;
  let raf = 0, closed = false;
  const state = { raf: (v) => { raf = v; }, keys: {} };
  const close = () => {
    if (closed) return; closed = true;
    cancelAnimationFrame(raf); document.removeEventListener("keydown", kd); document.removeEventListener("keyup", ku);
    L.innerHTML = ""; L.hidden = true; if (host.onClose) host.onClose();
  };
  const kd = (e) => { if (e.key === "Escape") { close(); return; } state.keys[e.key] = true; if (onKey) onKey(e); if (e.key.startsWith("Arrow")) e.preventDefault(); };
  const ku = (e) => { state.keys[e.key] = false; };
  document.addEventListener("keydown", kd); document.addEventListener("keyup", ku);
  L.querySelector('[data-g="close"]').onclick = close;
  return { close, state, body: L.querySelector(".gbody") };
}

function over(g, host, id, score, text, lower) {
  const best = host.best(id), isBest = best === undefined || (lower ? score < best : score > best);
  host.record(id, score, lower);
  const o = document.createElement("div"); o.className = "gover";
  o.innerHTML = `<b>${esc(text)}</b><span>${lower ? score + " moves" : "Score " + score}. Best ${isBest ? score : best}${lower ? " moves" : ""}.</span><button class="btn accent" type="button" data-g="again">Again</button>`;
  g.body.appendChild(o);
  o.querySelector('[data-g="again"]').onclick = () => { g.close(); openGame(id, host); };
}

export function openGame(id, host) {
  const spec = GAMES.find((x) => x.id === id);
  if (!spec) return null;
  if (id === "memory") return memory(spec, host);
  const g = openLayer(host, spec.name, `<canvas id="gcv" width="640" height="380"></canvas><div class="gnote">${esc(spec.how)}</div>`);
  const cv = g.body.querySelector("#gcv"), ctx = cv.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  const img = new Image(); img.src = new URL(`${host.character}/idle.webp`, SHEET).href;
  const mouse = { x: 320, y: 190 }, col = PAL[host.character], keys = g.state.keys;
  cv.addEventListener("pointermove", (e) => { const r = cv.getBoundingClientRect(); mouse.x = (e.clientX - r.left) * 640 / r.width; mouse.y = (e.clientY - r.top) * 380 / r.height; });
  const px = (x, y, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(Math.round(x), Math.round(y), w, h); };
  // the character's body from the first frame of its sheet, scaled to fit `size` and standing with its feet at (cx, bottom)
  const face = (cx, bottom, size) => {
    if (!img.complete || !img.naturalWidth) return;
    const [sx, sy, sw, sh] = BODY[host.character], k = size / Math.max(sw, sh);
    ctx.drawImage(img, sx, sy, sw, sh, cx - sw * k / 2, bottom - sh * k, sw * k, sh * k);
  };
  const run = (loop) => { let last = performance.now(); const tick = (now) => { const dt = Math.min(0.05, (now - last) / 1000); last = now; if (loop(dt) !== false) g.state.raf(requestAnimationFrame(tick)); }; g.state.raf(requestAnimationFrame(tick)); };
  if (id === "catch") {
    let x = 320, score = 0, lives = 3, t = 30, drops = [], spawn = 0;
    run((dt) => {
      t -= dt; spawn -= dt;
      if (keys.ArrowLeft) mouse.x -= 380 * dt; if (keys.ArrowRight) mouse.x += 380 * dt;
      x += (Math.max(30, Math.min(610, mouse.x)) - x) * 0.4;
      if (spawn <= 0) { spawn = Math.max(0.25, 0.55 - score * 0.01); drops.push({ x: 20 + Math.random() * 600, y: -10, bomb: Math.random() < 0.26, v: 130 + score * 4 + Math.random() * 60 }); }
      drops.forEach((d) => { d.y += d.v * dt; });
      const keep = [];
      for (const d of drops) { if (Math.abs(d.x - x) < 34 && d.y > 310 && d.y < 350) { if (d.bomb) lives--; else score++; } else if (d.y < 390) keep.push(d); }
      drops = keep;
      px(0, 0, 640, 380, col.wall); for (let i = 0; i < 40; i++) px((i * 83) % 640, (i * 47) % 300, 2, 2, "rgba(255,255,255,.25)"); px(0, 352, 640, 28, col.floor);
      drops.forEach((d) => { if (d.bomb) { px(d.x - 8, d.y - 8, 16, 16, "#111"); px(d.x - 6, d.y - 6, 4, 4, "#555"); px(d.x + 2, d.y - 12, 4, 4, "#ff5a4d"); } else { px(d.x - 3, d.y - 9, 6, 18, "#ffd83d"); px(d.x - 9, d.y - 3, 18, 6, "#ffd83d"); px(d.x - 2, d.y - 2, 4, 4, "#fff"); } });
      face(x, 372, 72);
      ctx.fillStyle = "#fff"; ctx.font = "700 18px Doto, monospace"; ctx.fillText(`Stars ${score}   Lives ${lives}   Time ${Math.ceil(Math.max(0, t))}`, 14, 26);
      if (t <= 0 || lives <= 0) { over(g, host, id, score, lives <= 0 ? "Boom" : "Time's up", false); if (score >= 15) host.trophy(id); return false; }
    });
  } else {
    let py = 190, by = 190, ball = { x: 320, y: 190, vx: 230, vy: 120 }, a = 0, b = 0;
    const reset = (dir) => { ball = { x: 320, y: 190, vx: 230 * dir, vy: (Math.random() - 0.5) * 220 }; };
    run((dt) => {
      if (keys.ArrowUp) mouse.y -= 340 * dt; if (keys.ArrowDown) mouse.y += 340 * dt;
      py += (Math.max(30, Math.min(350, mouse.y)) - py) * 0.5;
      by += Math.max(-175 * dt, Math.min(175 * dt, ball.y - by)); ball.x += ball.vx * dt; ball.y += ball.vy * dt;
      if (ball.y < 6 || ball.y > 374) ball.vy *= -1;
      if (ball.x < 30 && Math.abs(ball.y - py) < 34 && ball.vx < 0) { ball.vx = Math.min(520, -ball.vx * 1.06); ball.vy += (ball.y - py) * 5; }
      if (ball.x > 610 && Math.abs(ball.y - by) < 34 && ball.vx > 0) { ball.vx = -Math.min(520, ball.vx * 1.04); ball.vy += (ball.y - by) * 4; }
      if (ball.x < -10) { b++; reset(1); } if (ball.x > 650) { a++; reset(-1); }
      px(0, 0, 640, 380, col.wall); for (let y = 6; y < 380; y += 24) px(318, y, 4, 12, "rgba(255,255,255,.2)");
      px(16, py - 32, 8, 64, col.main); px(616, by - 32, 8, 64, "#fff"); px(ball.x - 5, ball.y - 5, 10, 10, "#ffd83d");
      face(590, 58, 46); ctx.fillStyle = "#fff"; ctx.font = "700 28px Doto, monospace"; ctx.fillText(a, 270, 36); ctx.fillText(b, 350, 36);
      if (a >= 5 || b >= 5) { over(g, host, id, a, a >= 5 ? "You win" : `${host.name} wins`, false); if (a >= 5) host.trophy(id); return false; }
    });
  }
  return g;
}

// Memory: the cards are the character's own decorations drawn in pixel art
const FACES = ["crown-crown", "hand-trophy", "hand-coffee", "eyes-glasses", "neck-bell", "aura-hearts", "crown-beanie", "hand-balloon"];
function memory(spec, host) {
  const deck = [...FACES, ...FACES].map((k, i) => [k, (i * 7919) % 31]).sort((a, b) => a[1] - b[1]).map((x) => x[0]);
  const card = (k, i) => `<button class="mcard" type="button" data-i="${i}" aria-label="Card ${i + 1}"><span class="mf"><svg viewBox="-48 -60 96 96" style="--wd-ink:${PAL[host.character].main}"><g transform="scale(.9)">${pixelGroup(k)}</g></svg></span><span class="mb"></span></button>`;
  const g = openLayer(host, spec.name, `<div class="mgrid">${deck.map(card).join("")}</div><div class="gnote" id="mmoves">Moves 0</div>`);
  let open = [], done = 0, moves = 0, lock = false;
  const els = [...g.body.querySelectorAll(".mcard")];
  els.forEach((el, i) => el.addEventListener("click", () => {
    if (lock || el.classList.contains("up") || el.classList.contains("ok")) return;
    el.classList.add("up"); open.push(i);
    if (open.length < 2) return;
    moves++; g.body.querySelector("#mmoves").textContent = `Moves ${moves}`;
    const [a, b] = open;
    if (deck[a] === deck[b]) {
      els[a].classList.add("ok"); els[b].classList.add("ok"); open = []; done++;
      if (done === 8) { over(g, host, "memory", moves, "All pairs found", true); if (moves <= 16) host.trophy("memory"); }
    } else { lock = true; setTimeout(() => { els[a].classList.remove("up"); els[b].classList.remove("up"); open = []; lock = false; }, 800); }
  }));
  return g;
}

/** The wall calendar: this month with a dot on every day that has something due, and what is coming up (all from your real tasks). */
export function openCalendar(host, tasks) {
  const now = new Date(), first = new Date(now.getFullYear(), now.getMonth(), 1), days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const by = {};
  for (const t of tasks || []) { if (!t.due_date) continue; const d = new Date(t.due_date); if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) (by[d.getDate()] ||= []).push(t); }
  let cells = ""; for (let i = 0; i < (first.getDay() + 6) % 7; i++) cells += "<i></i>";
  for (let n = 1; n <= days; n++) cells += `<b class="${n === now.getDate() ? "today" : ""}" title="${esc((by[n] || []).map((t) => t.title).join(", "))}">${n}${by[n] ? "<u></u>" : ""}</b>`;
  const soon = (tasks || []).filter((t) => t.due_date && !t.completed && new Date(t.due_date) >= new Date(now.getFullYear(), now.getMonth(), now.getDate())).sort((a, b) => a.due_date.localeCompare(b.due_date)).slice(0, 5);
  const when = (iso) => new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  return openLayer(host, now.toLocaleString([], { month: "long", year: "numeric" }), `<div class="cal"><div class="calh">${["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((x) => `<span>${x}</span>`).join("")}</div><div class="calg">${cells}</div></div>
    <div class="calside"><h4>Coming up</h4>${soon.length ? soon.map((t) => `<p><b>${esc(when(t.due_date))}</b> ${esc(t.title)}</p>`).join("") : `<p class="note">Nothing with a date is waiting. Add a reminder in Plan and it shows here.</p>`}</div>`);
}
