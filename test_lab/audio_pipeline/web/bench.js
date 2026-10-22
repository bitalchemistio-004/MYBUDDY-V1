"use strict";
/* Test benches page. Every bench reports a generic state (message, progress, tables, charts, notes,
 * verdict) and this page just draws it; only each bench's controls are described here. */

const $ = (s, root = document) => root.querySelector(s);
function el(tag, props = {}, ...kids) {
  const n = Object.assign(document.createElement(tag), props);
  kids.forEach((k) => n.append(k));
  return n;
}

const BENCHES = {
  speaker: {
    title: "Speaker",
    desc: "Plays known sounds through the robot's speaker and measures them back with its own microphone: is anything audible, how loud, how clean, what frequency response, and how long the loop takes. Needs the mic_lab firmware v4 (speaker module) and a working microphone near the speaker.",
    controls: [
      { type: "button", action: "all", label: "Run full speaker test" },
      { type: "button", action: "probe", label: "Is it audible?", ghost: true },
      { type: "button", action: "levels", label: "Level & distortion", ghost: true },
      { type: "button", action: "sweep", label: "Frequency response", ghost: true },
      { type: "button", action: "cancel", label: "Cancel", ghost: true },
    ],
  },
  echo: {
    title: "Echo & barge-in",
    desc: "Measures how loudly the robot hears its own voice while speaking, then runs production's exact barge-in rule (threshold = noise floor × 3.2 × 6.5, at least 5200, six frames in a row) on that audio to see whether it would wrongly interrupt itself. The interruption test asks you to talk over the robot.",
    controls: [
      { type: "text", id: "volumes", label: "Speaker levels (dB digital)", value: "-24,-18,-12,-6" },
      { type: "number", id: "repeats", label: "Runs per level", value: 2, min: 1, max: 3 },
      { type: "button", action: "scan", label: "Echo scan (no talking)" },
      { type: "number", id: "volume_db", label: "Interrupt test level (dB)", value: -12, min: -40, max: 0 },
      { type: "button", action: "interrupt", label: "Interruption test (you talk)", ghost: true },
      { type: "button", action: "cancel", label: "Cancel", ghost: true },
    ],
  },
  stt: {
    title: "Speech-to-text",
    desc: "Generated English, Arabic and mixed speech (edge-tts voices) through the production STT stage under several conditions. Reports word error rate, language-detection accuracy and latency. Uses your Groq quota: about one request per clip and condition, spaced out to respect rate limits.",
    controls: [
      { type: "checks", id: "languages", label: "Languages", options: ["en", "ar"], value: ["en", "ar"] },
      { type: "checks", id: "conditions", label: "Conditions", options: ["clean", "noise15", "noise5", "quiet"], value: ["clean", "noise15", "noise5"] },
      { type: "number", id: "per_language", label: "Clips per language", value: 4, min: 1, max: 8 },
      { type: "button", action: "run", label: "Run" },
      { type: "button", action: "cancel", label: "Cancel", ghost: true },
    ],
  },
  vad: {
    title: "Voice detection & wake",
    desc: "Runs production's voice-activity detector on speech and on noise-only clips (false alarms), and production's wake-phrase and hallucination filters on transcripts. Optionally adds a speech-to-text wake test (uses Groq).",
    controls: [
      { type: "checks", id: "extras", label: "Also", options: ["stt_wake"], value: [] },
      { type: "button", action: "run", label: "Run" },
      { type: "button", action: "cancel", label: "Cancel", ghost: true },
    ],
  },
  net: {
    title: "Network",
    desc: "Holds each PCM chunk size for a while and measures loss, jitter, the longest gap, the device's own queue drops, Wi-Fi signal and reconnects from the packets that really arrive. Restores the chunk size afterwards.",
    controls: [
      { type: "number", id: "seconds", label: "Seconds per chunk size", value: 20, min: 3, max: 600 },
      { type: "checks", id: "chunks", label: "Chunk sizes", options: [128, 256, 512, 1024], value: [128, 256, 512, 1024] },
      { type: "button", action: "run", label: "Run" },
      { type: "button", action: "cancel", label: "Cancel", ghost: true },
    ],
  },
};

let ws, connected = false, deviceState = null;
const states = {};                                   // bench name -> last state from the server
let current = (location.hash || "#speaker").slice(1);
if (!BENCHES[current]) current = "speaker";

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws/ui`);
  ws.onopen = () => { connected = true; badges(); };
  ws.onclose = () => { connected = false; badges(); setTimeout(connect, 1500); };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "bench") { states[m.name] = m.state; if (m.name === current) renderState(); }
    else if (m.type === "state") { deviceState = m; badges(); }
    else if (m.type === "notice") showBanner(m.text, m.level);
  };
}
const send = (obj) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); };

let bannerTimer;
function showBanner(text, level = "error") {
  const b = $("#banner"); b.textContent = text; b.dataset.level = level; b.hidden = false;
  clearTimeout(bannerTimer); bannerTimer = setTimeout(() => { b.hidden = true; }, 7000);
}

function badges() {
  const s = $("#badge-server"); s.textContent = connected ? "server: connected" : "server: disconnected — retrying"; s.dataset.state = connected ? "ok" : "bad";
  const d = deviceState && deviceState.device, w = deviceState && deviceState.wire, b = $("#badge-device");
  if (!d) { b.textContent = "device: not connected"; b.dataset.state = "bad"; return; }
  b.textContent = `device: ${d.ip || "?"}${d.fw ? " · " + d.fw : ""}${w && w.streaming ? " · streaming" : " · silent"}`;
  b.dataset.state = w && w.streaming ? "ok" : "warn";
}

function buildTabs() {
  const nav = $("#tabs");
  nav.replaceChildren();
  for (const [key, b] of Object.entries(BENCHES)) {
    const t = el("button", { className: "tab", textContent: b.title, role: "tab" });
    t.setAttribute("aria-selected", String(key === current));
    t.onclick = () => { current = key; location.hash = key; buildTabs(); buildPanel(); };
    nav.append(t);
  }
}

// Controls are built once per tab; values are read when a button is pressed.
function buildPanel() {
  const b = BENCHES[current], panel = $("#panel");
  panel.replaceChildren(el("h2", { textContent: b.title }), el("p", { className: "bench-desc", textContent: b.desc }));
  const row = el("div", { className: "controls" });
  for (const c of b.controls) {
    if (c.type === "button") {
      const btn = el("button", { className: "btn" + (c.ghost ? " ghost" : ""), textContent: c.label });
      btn.dataset.action = c.action;
      btn.onclick = () => send({ type: "bench", name: current, action: c.action, params: readParams(b) });
      row.append(btn);
    } else if (c.type === "checks") {
      const wrap = el("span", { className: "checks" }, c.label + ":");
      for (const o of c.options) {
        const cb = el("input", { type: "checkbox", checked: c.value.includes(o) }); cb.dataset.param = c.id; cb.dataset.value = String(o);
        wrap.append(el("label", {}, cb, String(o)));
      }
      row.append(wrap);
    } else {
      const inp = el("input", { type: c.type, value: c.value }); inp.dataset.param = c.id;
      if (c.min !== undefined) { inp.min = c.min; inp.max = c.max; }
      row.append(el("label", { className: "field" }, c.label + " ", inp));
    }
  }
  panel.append(row, el("div", { id: "out" }));
  renderState();
}

function readParams(b) {
  const p = {};
  for (const c of b.controls) {
    if (c.type === "checks") {
      p[c.id] = [...document.querySelectorAll(`[data-param="${c.id}"]`)].filter((x) => x.checked).map((x) => (isNaN(Number(x.dataset.value)) ? x.dataset.value : Number(x.dataset.value)));
    } else if (c.type === "number") p[c.id] = Number(document.querySelector(`[data-param="${c.id}"]`).value);
    else if (c.type === "text") p[c.id] = document.querySelector(`[data-param="${c.id}"]`).value;
  }
  return p;
}

function chartSvg(c) {
  const W = 640, H = 240, L = 52, R = 12, T = 10, B = 34, xs = c.x, ys = c.y;
  if (!xs.length) return el("div");
  const fx = (v) => (c.logx ? Math.log10(v) : v);
  const x0 = fx(Math.min(...xs)), x1 = fx(Math.max(...xs)), y0 = Math.floor(Math.min(...ys) / 5) * 5, y1 = Math.ceil(Math.max(...ys) / 5) * 5 || y0 + 5;
  const px = (v) => L + ((fx(v) - x0) / (x1 - x0 || 1)) * (W - L - R), py = (v) => H - B - ((v - y0) / (y1 - y0 || 1)) * (H - T - B);
  let s = `<rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="none" stroke="var(--radar-line)"/>`;
  for (let v = y0; v <= y1; v += Math.max(5, Math.ceil((y1 - y0) / 5 / 5) * 5)) s += `<line x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}" stroke="var(--radar-line)" stroke-dasharray="3 4"/><text x="${L - 6}" y="${py(v) + 4}" text-anchor="end" font-size="11" fill="var(--muted)">${v}</text>`;
  const ticks = c.logx ? [100, 200, 500, 1000, 2000, 5000, 8000].filter((t) => t >= Math.min(...xs) * 0.9 && t <= Math.max(...xs) * 1.1) : xs.filter((_, i) => i % Math.ceil(xs.length / 8) === 0);
  for (const t of ticks) s += `<text x="${px(t)}" y="${H - B + 16}" text-anchor="middle" font-size="11" fill="var(--muted)">${t >= 1000 && c.logx ? t / 1000 + "k" : t}</text>`;
  s += `<polyline fill="none" stroke="var(--accent)" stroke-width="2.5" points="${xs.map((x, i) => `${px(x).toFixed(1)},${py(ys[i]).toFixed(1)}`).join(" ")}"/>`;
  s += `<text x="${(L + W - R) / 2}" y="${H - 4}" text-anchor="middle" font-size="12" fill="var(--muted)">${c.xlabel}</text><text x="12" y="${(T + H - B) / 2}" font-size="12" fill="var(--muted)" transform="rotate(-90 12 ${(T + H - B) / 2})" text-anchor="middle">${c.ylabel}</text>`;
  const wrap = el("div", { className: "bchart" }, el("h3", { textContent: c.title }));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.innerHTML = s;
  wrap.append(svg);
  return wrap;
}

function renderState() {
  const st = states[current] || { phase: "idle", message: "Idle.", progress: 0, tables: [], charts: [], notes: [], verdict: null, prompt: null };
  const out = $("#out");
  if (!out) return;
  const busy = st.phase === "running";
  document.querySelectorAll(".controls button").forEach((b) => { if (b.dataset.action !== "cancel") b.disabled = busy; else b.disabled = !busy; });
  const parts = [];
  parts.push(el("div", { className: "progress" }, Object.assign(el("div"), { })));
  parts[0].firstChild.style.width = Math.round((st.progress || 0) * 100) + "%";
  const msg = el("p", { className: "bench-msg", textContent: st.message }); msg.dataset.phase = st.phase; parts.push(msg);
  if (st.prompt) { const p = el("div", { className: "prompt", textContent: st.prompt.text }); p.dataset.l = st.prompt.level; parts.push(p); }
  if (st.verdict) { const v = el("div", { className: "verdict", textContent: st.verdict.text }); v.dataset.l = st.verdict.level; parts.push(v); }
  for (const t of st.tables) {
    const bad = new Set(t.bad.map((x) => x.join(",")));
    const tbl = el("table", { className: "btable" }, el("caption", { textContent: t.title }));
    tbl.append(el("thead", {}, el("tr", {}, ...t.columns.map((c) => el("th", { textContent: c })))));
    const body = el("tbody");
    t.rows.forEach((r, i) => body.append(el("tr", {}, ...r.map((v, j) => {
      const td = el("td", { textContent: String(v) });
      if (bad.has(`${i},${j}`)) td.className = "bad";
      if (/[؀-ۿ]/.test(String(v))) td.classList.add("rtl");
      return td;
    }))));
    tbl.append(body);
    parts.push(el("div", { className: "tablewrap" }, tbl));
  }
  for (const c of st.charts) parts.push(chartSvg(c));
  if (st.notes.length) parts.push(el("ul", { className: "bnotes" }, ...st.notes.map((n) => el("li", { textContent: n }))));
  out.replaceChildren(...parts);
}

buildTabs();
buildPanel();
connect();
