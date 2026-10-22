"use strict";
/* MBUDDY audio lab — browser side.
 * Shows only what the server relays from the device. "Requested" values are the
 * page's own; "confirmed" values come from the device's ACK; "measured" values
 * come from the headers of the packets that really arrived. */

const FS = 16000, SPEED_OF_SOUND = 343;               // Hz, m/s
const PRESETS = [64, 128, 256, 512, 1024];
const DB_MIN = -80;
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const store = {
  get(k, d) { try { const v = localStorage.getItem("audiolab." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("audiolab." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

// Boxes the user has typed in but not applied yet must not be refreshed from the server state.
const dirty = new Map();                               // input selector -> time of the user's last edit
const DIRTY_MS = 20000;
function syncInput(sel, value) {
  const box = $(sel), t = dirty.get(sel);
  if (document.activeElement === box) return;
  if (t && Date.now() - t < DIRTY_MS && String(box.value) !== String(value)) return;
  dirty.delete(sel);
  box.value = value;
}
let manualGain = null;                                 // last manual gain request, to confirm or explain it

let ws, state = null, connected = false;
let centreLag = store.get("centreLag", 0);
let lastLag = null;
const peakHold = { left: DB_MIN, right: DB_MIN, both: DB_MIN };

// ── connection ───────────────────────────────────────────────────────
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws/ui`);
  ws.onopen = () => { connected = true; renderBadges(); };
  ws.onclose = () => { connected = false; renderBadges(); setTimeout(connect, 1500); };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "state") { state = m; renderState(); }
    else if (m.type === "levels") { renderLevels(m.levels); renderDirection(m.direction); }
    else if (m.type === "recordings") renderRecordings(m.items);
    else if (m.type === "notice") {
      showBanner(m.text, m.level);
      if (manualGain && Date.now() - manualGain.at < 3000) { manualGain.rejected = true; setGainMsg(m.text, "bad"); }
    }
  };
}
const send = (obj) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); };
const setConfig = (config) => send({ type: "set_config", config });

let bannerTimer;
function showBanner(text, level = "error") {
  const b = $("#banner");
  b.textContent = text; b.dataset.level = level; b.hidden = false;
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => { b.hidden = true; }, 6000);
}

// ── header badges ────────────────────────────────────────────────────
function badge(id, text, s) { const el = $(id); el.textContent = text; el.dataset.state = s; }
function renderBadges() {
  badge("#badge-server", connected ? "server: connected" : "server: disconnected — retrying", connected ? "ok" : "bad");
  const d = state && state.device;
  if (!d) badge("#badge-device", "device: not connected", "bad");
  else {
    const w = state.wire;
    badge("#badge-device", `device: ${d.ip || "?"}${d.rssi ? ` · ${d.rssi} dBm` : ""}${w.streaming ? " · streaming" : " · silent"}`, w.streaming ? "ok" : "warn");
  }
  const s = state && state.stt;
  if (s) badge("#badge-stt", `STT: ${s.state === "ready" ? "ready" : s.detail}`, s.state === "ready" ? "ok" : s.state === "loading" ? "warn" : "bad");
}

// ── config / state ───────────────────────────────────────────────────
function renderState() {
  renderBadges();
  const { requested: req, applied: ack, wire, recording: rec } = state;

  // 1. mic choice — selected = what was requested; the pill says whether the hardware confirms it
  $$(".mic-choice").forEach((btn) => {
    const mic = btn.dataset.mic, pill = $('[data-role="confirm"]', btn);
    btn.setAttribute("aria-checked", String(req.mic === mic));
    if (req.mic !== mic) { pill.textContent = ""; pill.dataset.s = ""; return; }
    if (wire.streaming && wire.mic === mic && ack && ack.mic === mic) { pill.textContent = "✓ confirmed by device"; pill.dataset.s = "ok"; }
    else if (!state.device) { pill.textContent = "waiting for device"; pill.dataset.s = "wait"; }
    else { pill.textContent = "applying…"; pill.dataset.s = "wait"; }
  });
  // raw per-slot peaks reported by the firmware once a second (16-bit scale, before swap/gain/DC filter)
  const d = state.device, rawEl = $("#raw-slots"), s0 = d && Number(d.slot0), s1 = d && Number(d.slot1);
  if (d && Number.isFinite(s0) && Number.isFinite(s1) && d.slot0 !== undefined) {
    const f = (v) => `${v} (${v > 0 ? (20 * Math.log10(v / 32768)).toFixed(0) : "−∞"} dBFS)`;
    const gap = s0 > 0 && s1 > 0 ? 20 * Math.log10(s1 / s0) : null;
    rawEl.textContent = `Raw I2S slots, peak over the last second — slot 0: ${f(s0)} · slot 1: ${f(s1)}` +
      (gap !== null ? ` · slot 1 vs slot 0: ${gap > 0 ? "+" : ""}${gap.toFixed(1)} dB` : "");
  } else rawEl.textContent = "Raw I2S slots (measured on the device, before swap / gain / filtering): waiting for device…";
  if (document.activeElement !== $("#swap")) $("#swap").checked = !!req.swap;
  syncInput("#gain", req.gain);

  // 4. chunk
  $$(".preset").forEach((b) => { b.dataset.sel = String(Number(b.dataset.chunk) === req.chunk ? 1 : 0); });
  syncInput("#chunk-custom", req.chunk);
  $("#c-req").textContent = `${req.chunk} samples`;
  const ackEl = $("#c-ack");
  ackEl.textContent = ack ? `${ack.chunk} samples` : (state.device ? "no ACK yet" : "no device");
  ackEl.className = ack ? (ack.chunk === req.chunk ? "ok" : "wait") : "wait";
  const wireEl = $("#c-wire");
  if (wire.streaming) {
    wireEl.textContent = `${wire.chunk_frames} samples per channel`;
    wireEl.className = wire.chunk_frames === req.chunk ? "ok" : "wait";
    $("#c-size").textContent = `${wire.packet_bytes} bytes · ${wire.packet_ms} ms of audio`;
    $("#c-rate").textContent = wire.packets_per_s ? `${wire.packets_per_s} packets/s · ${wire.kbps} kbit/s` : "—";
  } else {
    wireEl.textContent = state.device ? "not streaming" : "no device";
    wireEl.className = "";
    $("#c-size").textContent = "—"; $("#c-rate").textContent = "—";
  }
  const dropEl = $("#c-drop");
  dropEl.textContent = `${wire.dropped} of ${wire.packets + wire.dropped}` + (wire.bad ? ` · ${wire.bad} malformed` : "");
  dropEl.className = wire.dropped || wire.bad ? "bad" : "";

  renderCalibration();
  renderBitHealth();
  renderDiag();

  // 5. recording
  const btn = $("#rec-btn");
  btn.disabled = !rec.active && !wire.streaming;
  btn.dataset.on = rec.active ? "1" : "0";
  btn.textContent = rec.active ? "■ Stop" : "● Record";
  $("#rec-timer").textContent = `${rec.seconds.toFixed(1)} s`;
  $("#rec-hint").textContent = rec.waiting ? "Waiting for the first packet…" :
    rec.active ? "Recording — configuration is locked until you stop." :
    "Records the selected microphone(s) as a 16 kHz 16-bit WAV, exactly as received.";

  if (state.error) showBanner(state.error, "error");
  else if (state.beacon_error) showBanner(state.beacon_error, "info");
}



// ── live listening ───────────────────────────────────────────────────
// The server streams the processed audio in ~40 ms batches; each batch is scheduled a little ahead of
// the audio clock. The "delay" is how much audio is held back to ride out Wi-Fi jitter.
const listen = { on: false, ws: null, ctx: null, gain: null, analyser: null, next: 0, started: false,
                 samples: 0, underruns: 0, dropped: 0, channels: 0, raf: 0, err: null };
const RATE = 16000;

function listenStatus(text, s) { const el = $("#listen-stats"); el.textContent = text; el.dataset.s = s || ""; }

async function startListening() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) { listenStatus("This browser cannot play live audio (no Web Audio).", "bad"); return; }
  if (!listen.ctx) listen.ctx = new Ctx({ latencyHint: "interactive" });
  const ctx = listen.ctx;
  await ctx.resume();
  listen.gain = ctx.createGain();
  listen.gain.gain.value = Number($("#listen-vol").value) / 100;
  listen.analyser = ctx.createAnalyser();
  listen.analyser.fftSize = 1024;
  listen.gain.connect(listen.analyser);
  listen.analyser.connect(ctx.destination);
  Object.assign(listen, { next: 0, started: false, samples: 0, underruns: 0, dropped: 0, channels: 0, err: null, on: true });

  const sock = new WebSocket(`ws://${location.host}/ws/listen`);
  sock.binaryType = "arraybuffer";
  listen.ws = sock;
  sock.onmessage = (e) => { if (typeof e.data !== "string") playChunk(e.data); };
  sock.onclose = () => { if (listen.ws === sock && listen.on) { stopListening(); listenStatus("Listening stopped: the connection to the server closed.", "bad"); } };
  sock.onerror = () => { listen.err = "connection error"; };
  setListenButton(true);
  listenStatus("Connected — waiting for audio from the device…", "");
  drawListen();
}

function playChunk(buf) {
  const ctx = listen.ctx;
  if (!listen.on || !ctx || buf.byteLength < 3) return;
  const ch = new Uint8Array(buf, 0, 1)[0];
  if (ch !== 1 && ch !== 2) return;
  const pcm = new Int16Array(buf.slice(1));                        // copy: the payload starts at an odd byte offset
  const n = Math.floor(pcm.length / ch);
  if (!n) return;
  const target = Number($("#listen-buf").value);
  const now = ctx.currentTime, lead = listen.next - now;
  if (!listen.started || lead < 0.005) {                           // first chunk, or the buffer ran dry: re-buffer
    if (listen.started) listen.underruns++;
    listen.next = now + target;
    listen.started = true;
  } else if (lead > target + 0.12) {                               // running behind real time (bursty Wi-Fi, clock drift,
    listen.dropped++;                                              // or a lower delay just chosen): skip a batch to catch up
    return;
  }
  const ab = ctx.createBuffer(ch, n, RATE);
  for (let c = 0; c < ch; c++) {
    const out = ab.getChannelData(c);
    for (let i = 0; i < n; i++) out[i] = pcm[i * ch + c] / 32768;
  }
  const src = ctx.createBufferSource();
  src.buffer = ab;
  src.connect(listen.gain);
  src.start(listen.next);
  listen.next += ab.duration;
  listen.samples += n;
  listen.channels = ch;
}

function stopListening() {
  listen.on = false;
  if (listen.ws) { const s = listen.ws; listen.ws = null; try { s.close(); } catch { /* already closed */ } }
  if (listen.gain) { try { listen.gain.disconnect(); listen.analyser.disconnect(); } catch { /* not connected */ } }
  cancelAnimationFrame(listen.raf);
  $("#listen-fill").style.width = "0";
  setListenButton(false);
  listenStatus("Not listening.", "");
}

function setListenButton(on) {
  const b = $("#listen-btn");
  b.setAttribute("aria-pressed", String(on));
  b.textContent = on ? "■ Stop listening" : "🎧 Listen live";
}

function drawListen() {
  if (!listen.on) return;
  const a = listen.analyser, buf = new Float32Array(a.fftSize);
  a.getFloatTimeDomainData(buf);
  let sum = 0;
  for (const v of buf) sum += v * v;
  const db = 10 * Math.log10(Math.max(sum / buf.length, 1e-12));
  $("#listen-fill").style.width = Math.max(0, Math.min(1, (db + 80) / 80)) * 100 + "%";
  if (listen.samples > 0) {
    const buffered = Math.max(0, listen.next - listen.ctx.currentTime) * 1000;
    listenStatus(`Listening · ${listen.channels === 2 ? "stereo" : "mono"} · ${buffered.toFixed(0)} ms buffered · ` +
                 `${listen.underruns} gaps · ${listen.dropped} skipped batches`, "ok");
  } else if (state && state.device && !state.wire.streaming) {
    listenStatus("Connected, but the device is not streaming audio.", "bad");
  }
  listen.raf = requestAnimationFrame(drawListen);
}


// ── wiring diagnostic ────────────────────────────────────────────────
function renderBitHealth() {
  const d = state.device, el = $("#bit-health");
  if (!d) { el.textContent = "Raw bit health: waiting for device…"; el.dataset.s = ""; return; }
  if (d.s0big === undefined) { el.textContent = "Raw bit health: needs the mic_lab firmware version 2 (re-flash firmware/mic_lab)."; el.dataset.s = ""; return; }
  const v = ["s0big", "s1big", "s0low", "s1low"].map((k) => Number(d[k]));
  const bad = v[0] > 2 || v[1] > 2 || v[2] > 10 || v[3] > 10;
  el.textContent = `Raw bit health, last second — slot 0: ${v[0]}% full-scale-sized values · ${v[2]}% low bits active  |  ` +
                   `slot 1: ${v[1]}% · ${v[3]}%  (quiet room: all near 0)`;
  el.dataset.s = bad ? "bad" : "";
}

function renderDiagCompare(runs) {
  const tbl = $("#diag-compare");
  tbl.replaceChildren();
  if (!runs.normal && !runs.quiet) return;
  tbl.append(el("tr", {}, el("th", { textContent: "Repeats every I2S buffer (chance ≈ 6%)" }), el("th", { textContent: "Slot 0" }), el("th", { textContent: "Slot 1" })));
  for (const [key, label] of [["normal", "Normal capture (Wi-Fi streaming)"], ["quiet", "Wi-Fi audio paused"]]) {
    const r = runs[key];
    tbl.append(el("tr", {}, el("th", { textContent: label }),
      el("td", { textContent: r ? `${r.slots[0].periodic_pct}%` : "not captured yet", className: r && r.slots[0].periodic_pct >= 15 ? "bad" : "" }),
      el("td", { textContent: r ? `${r.slots[1].periodic_pct}%` : "not captured yet", className: r && r.slots[1].periodic_pct >= 15 ? "bad" : "" })));
  }
}

function renderDiag() {
  const d = state.diag, st = $("#diag-status"), tbl = $("#diag-table"), ver = $("#diag-verdicts");
  $("#diag-btn").disabled = $("#diag-quiet-btn").disabled = d.phase === "capturing" || !state.device;
  st.textContent = d.phase === "capturing" ? "Capturing raw I2S words…" : d.phase === "failed" ? d.message :
                   d.phase === "done" ? "Capture complete." : "Idle.";
  st.dataset.s = d.phase === "failed" ? "bad" : d.phase === "done" ? "ok" : "";
  tbl.replaceChildren(); ver.replaceChildren();
  renderDiagCompare(d.runs || {});
  if (d.phase !== "done") return;
  const [a, b] = d.result.slots;
  const head = el("tr", {}, el("th", { textContent: "" }), el("th", { textContent: "Slot 0 (left mic)" }), el("th", { textContent: "Slot 1 (right mic)" }));
  tbl.append(head);
  const row = (name, fa, fb, badA, badB) => tbl.append(el("tr", {}, el("th", { textContent: name }),
    el("td", { textContent: fa, className: badA ? "bad" : "" }), el("td", { textContent: fb, className: badB ? "bad" : "" })));
  row("Fluctuation (AC level)", `${a.noise_dbfs} dBFS`, `${b.noise_dbfs} dBFS`);
  row("Peak", `${a.peak_dbfs} dBFS`, `${b.peak_dbfs} dBFS`);
  row("DC offset", `${a.dc_dbfs} dBFS`, `${b.dc_dbfs} dBFS`);
  row("Full-scale-sized values (beyond ±12.5%)", `${a.big_pct}%`, `${b.big_pct}%`, a.big_pct > 2, b.big_pct > 2);
  row("Low bits 6..0 active (should be 0%)", `${a.low_active_pct}%`, `${b.low_active_pct}%`, a.low_active_pct > 10, b.low_active_pct > 10);
  row("Repeats every 256-sample I2S buffer", `${a.periodic_pct}%`, `${b.periodic_pct}%`, a.periodic_pct >= 15, b.periodic_pct >= 15);
  row("Distinct values in 0.25 s", String(a.distinct_values), String(b.distinct_values), a.stuck, b.stuck);
  row("Correlation between the slots", String(d.result.slot_correlation), "");
  for (const v of d.result.verdicts) {
    const li = el("li", { textContent: v.text });
    li.dataset.l = v.level;                            // dataset is read-only, so it cannot be passed to el()
    ver.append(li);
  }
}

// ── channel-balance calibration ──────────────────────────────────────
const fmtDb = (v, d = 1) => `${v > 0 ? "+" : ""}${v.toFixed(d)} dB`;
function setGainMsg(text, s) { const m = $("#trim-msg"); m.textContent = text; m.dataset.s = s; }
function applyManualGain() {
  const rawL = $("#trim-left").value.trim(), rawR = $("#trim-right").value.trim();
  const L = Number(rawL), R = Number(rawR);
  if (rawL === "" || rawR === "" || ![L, R].every((v) => Number.isFinite(v) && v >= -20 && v <= 40)) {
    manualGain = null;                                 // don't let an older "Applied" message replace this one
    setGainMsg("Enter a number between −20 and +40 dB in both boxes.", "bad");
    return;
  }
  dirty.delete("#trim-left"); dirty.delete("#trim-right");
  manualGain = { left: L, right: R, at: Date.now(), rejected: false };
  setGainMsg("Applying…", "wait");
  send({ type: "set_trim", left_db: L, right_db: R });
}

function renderCalibration() {
  const c = state.calibration, running = ["quiet", "sound", "processing"].includes(c.phase);
  const s = c.settings, gr = s.gate_reduction_db;
  const gates = ["left", "right"].filter((ch) => gr[ch] > 0).map((ch) => `${ch} −${gr[ch].toFixed(0)} dB when quiet`);
  $("#cal-active").textContent = `Active gain / boost: left ${fmtDb(s.left_db)} · right ${fmtDb(s.right_db)}` +
    (s.left_db === 0 && s.right_db === 0 ? " (uncalibrated)" : "") +
    (gates.length ? ` · gate: ${gates.join(", ")}` : s.gate_enabled ? "" : " · gate off");
  syncInput("#trim-left", s.left_db);
  syncInput("#trim-right", s.right_db);
  if (manualGain && !manualGain.rejected) {
    const ok = Math.abs(s.left_db - manualGain.left) < 0.01 && Math.abs(s.right_db - manualGain.right) < 0.01;
    if (ok) setGainMsg(`Applied: left ${fmtDb(s.left_db)} · right ${fmtDb(s.right_db)}`, "ok");
    else if (Date.now() - manualGain.at > 2500) setGainMsg(`Not applied — the server still has left ${fmtDb(s.left_db)} · right ${fmtDb(s.right_db)}.`, "bad");
  }
  if (document.activeElement !== $("#gate-on")) $("#gate-on").checked = s.gate_enabled;

  const st = $("#cal-status");
  st.dataset.phase = c.phase;
  const left = c.remaining_s !== null ? ` — ${c.remaining_s.toFixed(1)} s` : "";
  st.textContent =
    c.phase === "quiet" ? `1 · STAY SILENT${left}` :
    c.phase === "sound" ? `2 · MAKE SOUND now — keep it steady, centred in front${left}` :
    c.phase === "processing" ? "Measuring…" :
    c.phase === "failed" ? `Not calibrated: ${c.message}` :
    c.phase === "done" ? (c.result.applied ? "Done — settings applied." : "Done — measurement only, settings unchanged.") : "Idle.";

  $("#cal-run").disabled = $("#cal-verify").disabled = running;
  $("#cal-cancel").hidden = !running;

  const tbl = $("#cal-result"), warn = $("#cal-warnings"), r = c.result;
  tbl.replaceChildren(); warn.replaceChildren();
  if (!r || c.phase !== "done") return;
  const row = (k, v, cls) => tbl.append(el("tr", {}, el("th", { textContent: k }), el("td", { textContent: v, className: cls || "" })));
  const verdict = (d) => (Math.abs(d) <= r.target_db ? " ✓" : " ✗");
  const meters = (e, prefix) => {
    row(`${prefix} — at rest`, `L ${e.rest_dbfs.left} · R ${e.rest_dbfs.right} dBFS → ${fmtDb(e.rest_diff_db)}${verdict(e.rest_diff_db)}`, Math.abs(e.rest_diff_db) <= r.target_db ? "ok" : "bad");
    row(`${prefix} — during sound`, `L ${e.sound_dbfs.left} · R ${e.sound_dbfs.right} dBFS → ${fmtDb(e.sound_diff_db)}${verdict(e.sound_diff_db)}`, Math.abs(e.sound_diff_db) <= r.target_db ? "ok" : "bad");
  };
  row("Signal imbalance (speech band, noise removed)", fmtDb(r.raw_diff_db) + (r.weak ? ` · ${r.weak} mic is weaker` : ""));
  if (r.applied) {
    meters(r.before, "Meters before");
    row("New settings (applied)", `gain left ${fmtDb(r.proposed_trim_db.left)} · right ${fmtDb(r.proposed_trim_db.right)}` +
        (r.gate ? ` · gate ${["left", "right"].filter((ch) => r.gate_reduction_db[ch] > 0).map((ch) => `${ch} −${r.gate_reduction_db[ch].toFixed(0)} dB`).join(", ") || "not needed"}` : " · gate off"));
    meters(r.after, "Expected now (same audio)");
    row("Confirm", "Press “Verify only” with a fresh sound (and a quiet moment).");
  } else {
    meters(r.before, "Meters with active settings");
    row("Settings that would balance it", `gain left ${fmtDb(r.proposed_trim_db.left)} · right ${fmtDb(r.proposed_trim_db.right)}`);
  }
  row("Raw noise floor (before any gain)", `L ${(20 * Math.log10(r.noise_rms_raw.left / 32768)).toFixed(1)} · R ${(20 * Math.log10(r.noise_rms_raw.right / 32768)).toFixed(1)} dBFS`);
  row("Weaker mic above its own noise", `${r.weak_channel_snr_db} dB`, r.weak_channel_snr_db < 6 ? "bad" : "");
  row("Sound used / consistency", `${r.active_seconds} s · ±${r.consistency_db} dB`);
  for (const w of r.warnings) warn.append(el("li", { textContent: w }));
}

// ── levels ───────────────────────────────────────────────────────────
function renderLevels(lv) {
  for (const ch of ["left", "right", "both"]) {
    const root = $(`.meter[data-ch="${ch}"]`), v = lv[ch];
    root.dataset.off = v ? "0" : "1";
    const fill = $(".fill", root), peak = $(".peak", root), db = $('[data-role="db"]', root);
    if (!v) { fill.style.width = "0"; peak.style.left = "0"; db.textContent = ch === "both" ? "needs both mics" : "not transmitted"; peakHold[ch] = DB_MIN; continue; }
    const pct = (x) => Math.max(0, Math.min(1, (x - DB_MIN) / -DB_MIN)) * 100;
    peakHold[ch] = Math.max(v.peak, peakHold[ch] - 0.9);      // slow fall
    fill.style.width = pct(v.rms) + "%";
    fill.dataset.hot = v.rms > -6 ? "2" : v.rms > -18 ? "1" : "0";
    peak.style.left = `calc(${pct(peakHold[ch])}% - 3px)`;
    db.textContent = v.rms <= -119 ? "silent" : `${v.rms.toFixed(1)} dBFS`;
  }
  // mic glow in the diagram follows the same real levels
  for (const ch of ["left", "right"]) {
    const g = $(`#glow-${ch}`);
    if (g) g.setAttribute("opacity", lv[ch] ? Math.max(0, Math.min(1, (lv[ch].rms - DB_MIN) / -DB_MIN)).toFixed(2) : "0");
  }
}

// ── direction ────────────────────────────────────────────────────────
const RADAR = { cx: 200, cy: 222, r: 180 };
function buildRadar() {
  const { cx, cy, r } = RADAR, svg = $("#radar");
  const pt = (deg, rad) => [cx + rad * Math.sin(deg * Math.PI / 180), cy - rad * Math.cos(deg * Math.PI / 180)];
  let s = `<path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy} Z" fill="var(--radar-fill)" stroke="var(--radar-line)"/>`;
  for (const rad of [r / 3, 2 * r / 3]) s += `<path d="M ${cx - rad} ${cy} A ${rad} ${rad} 0 0 1 ${cx + rad} ${cy}" fill="none" stroke="var(--radar-line)" stroke-dasharray="3 4"/>`;
  for (const a of [-90, -60, -30, 0, 30, 60, 90]) {
    const [x1, y1] = pt(a, 0), [x2, y2] = pt(a, r), [tx, ty] = pt(a, r + 13);
    s += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--radar-line)" stroke-width="${a === 0 ? 1.5 : 1}"/>`;
    s += `<text x="${tx}" y="${ty + 4}" text-anchor="middle" font-size="11" fill="var(--muted)">${a === 0 ? "0°" : (a > 0 ? "+" : "") + a + "°"}</text>`;
  }
  s += `<text x="${cx - r + 4}" y="${cy - 6}" font-size="12" fill="var(--muted)">LEFT</text><text x="${cx + r - 4}" y="${cy - 6}" text-anchor="end" font-size="12" fill="var(--muted)">RIGHT</text>`;
  s += `<g id="needle" opacity="0"><line id="needle-line" x1="${cx}" y1="${cy}" x2="${cx}" y2="${cy - r}" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/><circle id="needle-dot" cx="${cx}" cy="${cy - r}" r="9" fill="var(--accent)"/></g>`;
  // robot body + the two mics, each with a glow that tracks its real level
  s += `<rect x="${cx - 30}" y="${cy - 2}" width="60" height="24" rx="8" fill="var(--card)" stroke="var(--ink)" stroke-width="1.5"/>`;
  for (const [ch, dx, label] of [["left", -22, "L"], ["right", 22, "R"]]) {
    s += `<circle id="glow-${ch}" cx="${cx + dx}" cy="${cy + 10}" r="17" fill="var(--accent)" opacity="0"/>`;
    s += `<circle cx="${cx + dx}" cy="${cy + 10}" r="7" fill="var(--ink)"/><text x="${cx + dx}" y="${cy + 14}" text-anchor="middle" font-size="9" font-weight="700" fill="var(--bg)">${label}</text>`;
  }
  svg.innerHTML = s;
}

let smoothLag = null, lastDirAt = 0;
function renderDirection(d) {
  const now = performance.now(), dot = $("#balance-dot");
  const spacing = Number($("#spacing").value) / 100;
  if (!d) {
    if (now - lastDirAt > 400) {
      smoothLag = null; lastLag = null;
      $("#needle").setAttribute("opacity", "0");
      $("#r-angle").textContent = $("#r-lag").textContent = $("#r-corr").textContent = "—";
      $("#r-ild").textContent = state && state.wire.mic && state.wire.mic !== "both" ? "one mic only" : "too quiet";
      dot.dataset.live = "0";
    }
    return;
  }
  lastDirAt = now;

  // level balance: raw R−L in dB, drawn ±12 dB across the track (a display range, not a calibration)
  $("#r-ild").textContent = `${d.ild_db > 0 ? "+" : ""}${d.ild_db.toFixed(1)} dB`;
  dot.style.left = `calc(${50 + Math.max(-1, Math.min(1, d.ild_db / 12)) * 50}% - 8px)`;
  dot.dataset.live = "1";

  if (d.lag_samples === null) return;
  lastLag = d.lag_samples;
  smoothLag = smoothLag === null ? d.lag_samples : smoothLag * 0.65 + d.lag_samples * 0.35;
  const lag = smoothLag - centreLag;
  const maxLag = spacing * FS / SPEED_OF_SOUND;          // largest physically possible lag for this spacing
  const ratio = lag / maxLag;
  $("#r-lag").textContent = `${lag > 0 ? "+" : ""}${lag.toFixed(2)} samples (max ±${maxLag.toFixed(1)})`;
  $("#r-corr").textContent = d.corr.toFixed(2);

  if (Math.abs(ratio) > 1.25) {                           // impossible for this spacing: don't invent an angle
    $("#r-angle").textContent = "out of range — check spacing / swap";
    $("#needle").setAttribute("opacity", "0");
    return;
  }
  const deg = -Math.asin(Math.max(-1, Math.min(1, ratio))) * 180 / Math.PI;   // lag>0 ⇒ source on the left ⇒ negative
  $("#r-angle").textContent = `${deg > 0 ? "+" : ""}${deg.toFixed(0)}° ${Math.abs(deg) < 5 ? "(ahead)" : deg < 0 ? "(left)" : "(right)"}`;
  const { cx, cy, r } = RADAR, rad = deg * Math.PI / 180;
  const x = cx + r * Math.sin(rad), y = cy - r * Math.cos(rad);
  $("#needle-line").setAttribute("x2", x); $("#needle-line").setAttribute("y2", y);
  $("#needle-dot").setAttribute("cx", x); $("#needle-dot").setAttribute("cy", y);
  $("#needle").setAttribute("opacity", Math.max(0.25, d.corr).toFixed(2));        // faint when the channels barely correlate
}

// ── recordings ───────────────────────────────────────────────────────
function el(tag, props = {}, ...kids) {
  const n = Object.assign(document.createElement(tag), props);
  kids.forEach((k) => n.append(k));
  return n;
}

function renderRecordings(items) {
  const list = $("#rec-list");
  list.replaceChildren();
  if (!items.length) { list.append(el("p", { className: "empty", textContent: "No recordings yet." })); return; }
  for (const it of items) list.append(recordingRow(it));
}

function recordingRow(it) {
  const stereo = it.channels === 2;
  const meta = [it.mic, stereo ? "stereo" : "mono", `${it.duration_s ?? "?"} s`, it.chunk_frames ? `chunk ${it.chunk_frames}` : null,
                it.gain ? `gain ${it.gain}×` : null, it.dropped_packets ? `${it.dropped_packets} packets lost` : null,
                `${Math.round((it.size_bytes || 0) / 1024)} kB`].filter(Boolean).join(" · ");
  const url = `/recordings/${encodeURIComponent(it.name)}`;

  const source = el("select", { title: "Which channel is sent to speech-to-text" });
  if (stereo) for (const [v, t] of [["mix", "Mix of L+R"], ["left", "Left only"], ["right", "Right only"], ["loudest", "Louder mic per sample (as production)"]]) source.append(el("option", { value: v, textContent: t }));
  else source.append(el("option", { value: "auto", textContent: "Single mic" }));
  const lang = el("select", { title: "Language hint (MBUDDY supports Arabic and English)" });
  for (const [v, t] of [["auto", "Auto-detect"], ["en", "English"], ["ar", "Arabic"]]) lang.append(el("option", { value: v, textContent: t }));
  const dsp = el("input", { type: "checkbox", checked: true });
  const out = el("div");
  const go = el("button", { className: "btn small", textContent: "Transcribe" });
  go.onclick = async () => {
    go.disabled = true; go.textContent = "Transcribing…"; out.replaceChildren();
    try {
      const r = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: it.name, source: source.value, language: lang.value, dsp: dsp.checked }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.statusText);
      out.append(sttResult(j));
    } catch (e) {
      out.append(el("div", { className: "stt-result" }, el("p", { className: "stt-err", textContent: `Transcription failed: ${e.message}` })));
    } finally { go.disabled = false; go.textContent = "Transcribe"; }
  };

  return el("div", { className: "rec-item" },
    el("div", { className: "rec-head" }, el("span", { className: "rec-name", textContent: it.name }), el("a", { href: url, download: it.name, textContent: "Download .wav" })),
    el("div", { className: "rec-meta", textContent: meta }),
    el("audio", { controls: true, preload: "none", src: url }),
    el("div", { className: "stt-row" }, source, lang, el("label", { className: "check" }, dsp, "Production DSP"), go),
    out);
}

function sttResult(j) {
  const box = el("div", { className: "stt-result" });
  if (j.text) box.append(el("p", { className: "stt-text", dir: "auto", textContent: j.text }));
  else box.append(el("p", { className: "stt-err", textContent: "No transcript returned." }));
  const bits = [`language: ${j.language}`, j.avg_logprob !== null ? `avg logprob ${j.avg_logprob}` : null,
                j.no_speech_prob !== null ? `no-speech ${j.no_speech_prob}` : null, `${j.elapsed_ms} ms`,
                `${j.audio.duration_s} s from ${j.audio.source}`, j.audio.production_dsp ? "production DSP on" : "DSP off"].filter(Boolean);
  box.append(el("p", { className: "stt-meta", textContent: bits.join(" · ") }));
  if (j.note) box.append(el("p", { className: "stt-meta", textContent: j.note }));
  return box;
}

// ── wiring ───────────────────────────────────────────────────────────
function init() {
  buildRadar();
  const presets = $("#chunk-presets");
  for (const n of PRESETS) {
    const b = el("button", { className: "preset", textContent: `${n}${n === 256 ? " (production)" : ""}` });
    b.dataset.chunk = n;
    b.onclick = () => setConfig({ chunk: n });
    presets.append(b);
  }
  $$(".mic-choice").forEach((b) => { b.onclick = () => setConfig({ mic: b.dataset.mic }); });
  $("#swap").onchange = (e) => setConfig({ swap: e.target.checked ? 1 : 0 });
  $("#apply-gain").onclick = () => { dirty.delete("#gain"); setConfig({ gain: Number($("#gain").value) }); };
  $("#chunk-apply").onclick = () => { dirty.delete("#chunk-custom"); setConfig({ chunk: Number($("#chunk-custom").value) }); };
  // remember what the user has typed, and let Enter apply it
  for (const [sel, applyBtn] of [["#trim-left", "#trim-apply"], ["#trim-right", "#trim-apply"], ["#gain", "#apply-gain"], ["#chunk-custom", "#chunk-apply"]]) {
    $(sel).addEventListener("input", () => {
      dirty.set(sel, Date.now());
      if (sel.startsWith("#trim")) { manualGain = null; setGainMsg("", ""); }   // a new entry: forget the last result
    });
    $(sel).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $(applyBtn).click(); } });
  }
  $("#cal-run").onclick = () => send({ type: "calibrate", apply: true });
  $("#cal-verify").onclick = () => send({ type: "calibrate", apply: false });
  $("#cal-cancel").onclick = () => send({ type: "calibrate_cancel" });
  $("#trim-apply").onclick = applyManualGain;
  $("#diag-btn").onclick = () => send({ type: "diag_capture", quiet: false });
  $("#diag-quiet-btn").onclick = () => send({ type: "diag_capture", quiet: true });
  $("#listen-btn").onclick = () => (listen.on ? stopListening() : startListening().catch((e) => { stopListening(); listenStatus(`Could not start listening: ${e.message}`, "bad"); }));
  $("#listen-vol").oninput = (e) => { if (listen.gain) listen.gain.gain.value = Number(e.target.value) / 100; };
  $("#gate-on").onchange = (e) => send({ type: "set_gate", enabled: e.target.checked });
  $("#trim-reset").onclick = () => {
    dirty.delete("#trim-left"); dirty.delete("#trim-right");
    manualGain = { left: 0, right: 0, at: Date.now(), rejected: false };
    setGainMsg("Applying…", "wait");
    send({ type: "set_trim", left_db: 0, right_db: 0 });
  };
  $("#rec-btn").onclick = () => send({ type: state && state.recording.active ? "rec_stop" : "rec_start" });

  $("#spacing").value = store.get("spacing", 10);
  $("#spacing").onchange = () => store.set("spacing", Number($("#spacing").value));
  $("#set-centre").onclick = () => {
    if (lastLag === null) return showBanner("No direction reading yet — make a sound in front of the robot first.", "info");
    centreLag = lastLag; smoothLag = null; store.set("centreLag", centreLag);
    showBanner(`Centre set at ${centreLag.toFixed(2)} samples.`, "info");
  };
  $("#clear-centre").onclick = () => { centreLag = 0; smoothLag = null; store.set("centreLag", 0); };
  connect();
}
init();
