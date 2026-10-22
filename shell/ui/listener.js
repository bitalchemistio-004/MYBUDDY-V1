// MBUDDY voice bridge. See listener.html for why this exists.
"use strict";

const params = new URLSearchParams(location.search);
const BACKEND = params.get("backend") || "ws://127.0.0.1:5000/ws";
// The shell injects the token before this script runs (never in the URL).
const TOKEN = window.__MBUDDY_TOKEN || params.get("token") || "mbuddy-device-token"; // backend's local-only default
const MIC_HINT = params.get("mic") || "Microphone Array";
const CAPTURE_RATE = 16000;   // what the backend's VAD and Whisper expect
const FRAME_SAMPLES = 256;    // core/config.py CHUNK_SAMPLES (16 ms)
const PLAYBACK_RATE = 22050;  // tts/speaker.py TARGET_SR
// The app's hidden window owns the mic; a browser tab (for testing) gives
// way to it (api/websocket_server.py claim_device).
const FIRMWARE = window.__MBUDDY_HOST === "app" ? "shell-app-0.2" : "shell-web-0.2";
let superseded = false;

const stateEl = document.getElementById("state");
const logEl = document.getElementById("log");
function log(msg) {
  const line = `${new Date().toLocaleTimeString()}  ${msg}\n`;
  logEl.textContent = (line + logEl.textContent).slice(0, 20000);
  console.log(msg);
}
function setState(s) { stateEl.textContent = s; }

// Only the conversation is shown. The backend also sends device signals
// ([HEARD] ready, [QUIET], [ENERGY:80], [STATE:...]) meant for the ESP32's
// screen; "[HEARD] ready" means "listening again", not something you said.
function showConversation(msg) {
  const heard = msg.match(/^\[HEARD\]\s*(.*)$/);
  if (heard) {
    if (heard[1] && heard[1] !== "ready") log(`You: ${heard[1]}`);
    return;
  }
  const spoken = msg.match(/^\[SPEAKING\](?:\s*\[[^\]]*\])*\s*(.*)$/);
  if (spoken && spoken[1]) log(`MBUDDY: ${spoken[1]}`);
  if (msg.startsWith("[THINKING]")) setState("Thinking…");
}

// ── Capture: a worklet collects 16 kHz float samples into 256-sample frames.
const WORKLET = `
class Framer extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(${FRAME_SAMPLES}); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice()); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("framer", Framer);`;

let ws = null, captureCtx = null, captureStream = null, playCtx = null;
let playHead = 0, pendingSources = 0, replyActive = false;
// The mic is not sent while MBUDDY speaks, nor for a short tail after, so it
// cannot hear its own voice (it once "heard" its own "You'll know." and
// answered it). Interrupting MBUDDY mid-reply is not possible meanwhile.
const ECHO_TAIL_MS = 600;
let mutedUntil = 0;
function micMuted() { return pendingSources > 0 || performance.now() < mutedUntil; }

function toInt16(frame) {
  const out = new Int16Array(frame.length);
  for (let i = 0; i < frame.length; i++) out[i] = Math.max(-1, Math.min(1, frame[i])) * 32767;
  return out.buffer;
}

const HTTP = BACKEND.replace(/^ws/, "http").replace(/\/ws$/, "");
let chosenMic = null;  // the app's Microphone setting ("" = automatic)
let micOn = null;      // the app's microphone on/off switch (null until the backend answers)
let micLabels = [];

// Called once the backend answers: report the microphones, learn the setting.
async function syncMicSetting() {
  fetch(HTTP + "/ui/audio-devices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ devices: micLabels }) }).catch(() => {});
  const now = await settingsMic();
  if (now === null) return;
  if (chosenMic === null) chosenMic = now;
  else if (now !== chosenMic) { chosenMic = now; if (micOn !== false) restartCapture(); }   // the setting changed while we were offline
}

async function settingsMic() {
  try {
    const r = await (await fetch(HTTP + "/ui/settings")).json();
    // Switching the mic on or off starts or stops capture right here. The connection and the playback are left alone, so a reply
    // being spoken is not cut and the session does not drop (it used to reload this page: typed chat got "No active session"
    // and the sentence stopped mid-word).
    const on = r.settings.microphone_on !== false;
    const changed = (micOn !== null && on !== micOn) || (micOn === null && !on && captureCtx);
    micOn = on;
    if (changed) applyMic(on);
    return r.settings.microphone || "";
  }
  catch { return null; }
}

async function pickMic() {
  // Labels are only visible after one permission grant.
  const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
  probe.getTracks().forEach(t => t.stop());
  const inputs = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audioinput");
  // Tell the app which microphones exist (for its Microphone setting).
  micLabels = [...new Set(inputs.map(d => d.label).filter(l => l && !/^(default|communications)\b/i.test(l)))];
  chosenMic = await settingsMic();  // null while the backend is still starting
  const wanted = chosenMic || MIC_HINT;
  const match = inputs.find(d => d.label === wanted) || inputs.find(d => d.label.includes(wanted));
  log(`microphone: ${match ? match.label : "default (no match for '" + wanted + "')"}`);
  return match ? { exact: match.deviceId } : undefined;
}

// Switch microphones when the setting changes (reload = clean restart of capture).
setInterval(async () => {
  const now = await settingsMic();
  if (chosenMic !== null && now !== null && now !== chosenMic) { chosenMic = now; if (micOn !== false) restartCapture(); }
}, 5000);

async function startCapture() {
  const deviceId = await pickMic();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: {
    deviceId, channelCount: 1,
    echoCancellation: true, noiseSuppression: true, autoGainControl: true,
  }});
  captureStream = stream;
  const s = stream.getAudioTracks()[0].getSettings();
  log(`capture: AGC=${s.autoGainControl} NS=${s.noiseSuppression} AEC=${s.echoCancellation}`);
  captureCtx = new AudioContext({ sampleRate: CAPTURE_RATE });
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  await captureCtx.audioWorklet.addModule(url);
  const src = captureCtx.createMediaStreamSource(stream);
  const framer = new AudioWorkletNode(captureCtx, "framer");
  framer.port.onmessage = e => {
    if (ws && ws.readyState === WebSocket.OPEN && !micMuted()) ws.send(toInt16(e.data));
  };
  src.connect(framer);
  if (captureCtx.state === "suspended") {
    // Autoplay policy: audio stays paused until the page is clicked. The
    // shell's WebView2 is started with this policy relaxed.
    throw new Error("audio context suspended until a click");
  }
}

// Release the microphone (Windows shows it unused) without touching the connection or what is being played.
function stopCapture() {
  try { if (captureStream) captureStream.getTracks().forEach(t => t.stop()); } catch { /* already gone */ }
  captureStream = null;
  try { if (captureCtx) captureCtx.close(); } catch { /* already closed */ }
  captureCtx = null;
}

async function restartCapture() {
  stopCapture();
  try { await startCapture(); setState("Listening"); log("capture restarted for the new microphone"); }
  catch (err) { setState("Click to start the microphone"); log(`capture not restarted: ${err}`); }
}

async function applyMic(on) {
  if (!on) {
    stopCapture();
    setState("Microphone off");
    log("microphone switched off in the app: capture released, connection kept");
    return;
  }
  try { await startCapture(); setState("Listening"); log("microphone switched on: capture started"); }
  catch (err) {
    setState("Click to start the microphone");
    log(`capture not started: ${err}`);
    const b = document.getElementById("start");
    if (b) { b.hidden = false; b.onclick = async () => { b.hidden = true; await startCapture(); setState("Listening"); }; }
  }
}

// ── Playback: queue each PCM chunk right after the previous one.
function playPcm(buffer) {
  if (!playCtx) playCtx = new AudioContext({ sampleRate: PLAYBACK_RATE });
  const pcm = new Int16Array(buffer);
  if (!pcm.length) return;
  const audio = playCtx.createBuffer(1, pcm.length, PLAYBACK_RATE);
  const ch = audio.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const node = playCtx.createBufferSource();
  node.buffer = audio;
  node.connect(playCtx.destination);
  playHead = Math.max(playHead, playCtx.currentTime + 0.03);
  node.start(playHead);
  playHead += audio.duration;
  pendingSources++;
  replyActive = true;
  node.onended = () => {
    pendingSources--;
    if (pendingSources === 0) mutedUntil = performance.now() + ECHO_TAIL_MS;
    if (pendingSources === 0 && replyActive) {
      // The backend waits for this before it treats the reply as heard.
      replyActive = false;
      if (ws && ws.readyState === WebSocket.OPEN) ws.send("[PLAYBACK_DONE]");
      setState("Listening");
    }
  };
  setState("Speaking");
}

function connect() {
  ws = new WebSocket(BACKEND, ["arduino"]);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    ws.send(JSON.stringify({
      version: 1, type: "hello",
      device_id: "pc-shell", session_id: crypto.randomUUID(),
      firmware_version: FIRMWARE, auth_token: TOKEN,
      capabilities: { mic: true, speaker: true, display: false },
    }));
    log(`connected to ${BACKEND}`);
    syncMicSetting();
  };
  ws.onmessage = e => {
    if (typeof e.data !== "string") { playPcm(e.data); return; }
    if (e.data.startsWith("{")) {
      const m = JSON.parse(e.data);
      if (m.type === "hello_ack") setState("Listening");
      if (m.type === "hello_reject") {
        if (m.reason === "superseded") superseded = true;
        setState(superseded ? "The MBUDDY app has the microphone; this tab is idle" : `Rejected: ${m.reason}`);
        log(`HELLO rejected: ${m.reason}`);
      }
      return;
    }
    if (e.data === "[STOP]") {
      // Barge-in or cancel: drop anything still queued.
      if (playCtx) { playCtx.close(); playCtx = null; playHead = 0; pendingSources = 0; replyActive = false; }
    }
    showConversation(e.data);
  };
  ws.onclose = e => {
    if (superseded || e.code === 4001) {
      // Another bridge took over (the app, or a newer tab): stop here
      // instead of taking it back every 2 s.
      superseded = true;
      setState("Another MBUDDY bridge has the microphone; this one is idle");
      log(`replaced by another bridge (${e.code}); not reconnecting`);
      return;
    }
    setState("Backend offline, retrying…");
    log(`disconnected (${e.code}); retrying in 2 s`);
    setTimeout(connect, 2000);
  };
}

(async () => {
  try {
    await settingsMic();  // learn the on/off switch (null if the backend isn't up yet: start as on)
    if (micOn === false) {
      // Mic off: never open it (Windows shows no mic in use); replies still play.
      setState("Microphone off");
      log("microphone switched off in the app; not capturing");
      connect();
      return;
    }
    await startCapture();
    connect();
  } catch (err) {
    // Chrome needs a click before audio can start in some cases.
    setState("Click to start the microphone");
    log(`capture not started: ${err}`);
    const b = document.getElementById("start");
    b.hidden = false;
    b.onclick = async () => { b.hidden = true; await startCapture(); connect(); };
  }
})();
