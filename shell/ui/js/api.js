// Talking to the MBUDDY backend. In the app (and a browser on this PC) that is the local server; on a phone it
// is the phone gateway on the PC (api/remote_gateway.py), reached through a private tunnel, with the token
// this phone got when it was paired. The gateway serves the page itself and sets window.__MBUDDY_REMOTE__.
export const REMOTE = !!window.__MBUDDY_REMOTE__;
export const BASE = REMOTE ? location.origin + "/remote" : "http://127.0.0.1:5000";
const TOKEN_KEY = "mbuddy_phone_token";
export function token() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } }
export function setToken(t) { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } }
// Pair this phone with the code shown on the PC; the token is kept here, never shown.
export async function pairPhone(code, name) {
  const res = await fetch(location.origin + "/pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code, name }) });
  const data = await res.json().catch(() => ({ ok: false, error: "No answer." }));
  if (data.ok) setToken(data.token);
  return data;
}

// Text to show for a reply: no emotion tags, and no stage directions like
// "*yawns*" (those are sounds MBUDDY plays, not words). The user's own bracket text ("[S1] send the deck",
// "[URGENT]") stays: only the tags in brain/personality.py anywhere, and an invented one at the start, are removed.
const EMOTION_TAGS = /\[(NORMAL|ANGRY|GLEE|HAPPY|SAD|WORRIED|FOCUSED|ANNOYED|SURPRISED|SKEPTIC|FRUSTRATED|UNIMPRESSED|SLEEPY|SUSPICIOUS|SQUINT|FURIOUS|SCARED|AWE|THINKING|WINK|BORED|HYPER|LAUGHING|RELIEVED|مسرور|راض)\]/gi;
export function displayText(t) {
  return String(t || "").replace(/^\s*\[[A-Z]{3,20}\]/, "").replace(EMOTION_TAGS, "")
    .replace(/\*(?!\s)[^*\n]{1,40}?(?<!\s)\*/g, "").replace(/[ \t]{2,}/g, " ").trim();   // "* item" is a bullet, kept
}

async function call(method, path, body) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (REMOTE) headers.Authorization = "Bearer " + token();
  const res = await fetch(BASE + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (REMOTE && (res.status === 401 || res.status === 423)) window.dispatchEvent(new CustomEvent("mbuddy-access", { detail: res.status }));
  let data = null;
  try { data = await res.json(); } catch { data = { ok: false, error: await res.text().catch(() => "") }; }
  if (!res.ok && data && data.ok !== false) data.ok = false;
  if (!res.ok && data && !data.error) data.error = `HTTP ${res.status}`;
  return data;
}

export const api = {
  get: (p) => call("GET", p),
  post: (p, b) => call("POST", p, b || {}),
  del: (p, b) => call("DELETE", p, b),
};

// One typed turn. When the backend is still working after its wait (202 "pending": a long tool, a busy cloud), this
// keeps asking for THIS turn's reply instead of telling the user to ask again (the field test: the answer arrived
// later and was never shown). onPending runs once, so the caller can say "still working on it".
export async function chatTurn(text, speak, onPending) {
  const first = await api.post("/control/chat", { text, speak }).catch(() => null);
  if (!first || !first.pending) return first;
  if (onPending) onPending();
  const q = `/control/chat/result?text=${encodeURIComponent(text)}&after=${first.after}&started=${first.started}`;
  const until = Date.now() + 6 * 60 * 1000;
  while (Date.now() < until) {
    await new Promise((res) => setTimeout(res, 1500));
    const r = await api.get(q).catch(() => null);
    if (r && r.ok) return r;
    if (r && r.error && !r.pending) return r;
  }
  return { ok: false, error: "too_slow" };
}

// Live state: small updates (only the fields the app shows), only when one
// changes (/ui/live). Reconnects by itself (the backend may be restarting).
export function statusStream(onStatus, onConnection) {
  let ws = null, stopped = false, timer = null, heardAt = Date.now();
  const open = () => {
    ws = new WebSocket(BASE.replace("http", "ws") + "/ui/live" + (REMOTE ? "?t=" + encodeURIComponent(token()) : ""));
    ws.onopen = () => { heardAt = Date.now(); onConnection && onConnection(true); };
    ws.onmessage = (e) => { heardAt = Date.now(); try { onStatus(JSON.parse(e.data)); } catch { /* ignore */ } };
    ws.onclose = () => {
      onConnection && onConnection(false);
      if (!stopped) timer = setTimeout(open, 2000);
    };
  };
  open();
  // The backend repeats the snapshot every few seconds. A feed that says nothing for much longer is dead (a crashed backend can
  // leave the socket looking open): close it so it reconnects and the window never keeps showing a state that is long gone.
  const watchdog = setInterval(() => { if (!stopped && ws && ws.readyState === 1 && Date.now() - heardAt > 15000) { try { ws.close(); } catch { /* it closes anyway */ } } }, 3000);
  return () => { stopped = true; clearTimeout(timer); clearInterval(watchdog); ws && ws.close(); };
}

// The backend's "listening" flag follows its voice detector, which also
// fires on room noise; only treat it as listening when there is real sound.
// A typed chat keeps the buddy looking awake (the backend sets chat_until); the voice itself stays asleep.
const chatting = (s) => !!(s && s.chat_until && s.chat_until > Date.now() / 1000);
// With the microphone switched off nothing can wake the voice, so "asleep" would be wrong: the buddy stays up and shows it.
const micOff = (s) => !!(s && s.mic_on === false);
const hearing = (s) => !!(s && s.listening && (s.level == null || s.level > 0.006));

// What the buddy should be doing, from the live status (docs/PHASE3_DESIGN.md 4a).
export function reactionFor(status) {
  if (!status) return "idle";
  if (!status.awake && !chatting(status) && !micOff(status)) return "sleep";
  if (hearing(status)) return "listen";
  if (status.thinking) return "think";
  const emotion = String(status.emotion || "").toLowerCase();
  if (status.speaking) {
    if (["happy", "glee"].includes(emotion)) return "happy";
    if (emotion === "sad") return "sad";
    if (emotion === "worried" || emotion === "scared") return "worried";
    if (emotion === "surprised" || emotion === "awe") return "surprised";
    return "wave";
  }
  return "idle";
}

export function stateLabel(status, connected) {
  if (!connected) return ["Starting…", "warn"];
  if (!status || status.device_conn_state !== "online") return ["Voice not connected", "warn"];
  if (micOff(status)) return ["Microphone off", ""];
  if (!status.awake && !chatting(status)) return ["Asleep · say “Hey Buddy”", ""];
  if (hearing(status)) return ["Listening", "listen"];
  if (status.thinking) return ["Thinking", "listen"];
  if (status.speaking) return ["Speaking", "ok"];
  return ["Awake", "ok"];
}
