// What a phone's browser can ask Android to do, as links, plus a small parser for what a person types, and the advice
// on which on-device model to get. Pure functions: no page, no network, so they can be tested in Node.
//
// How it works, honestly: a web page cannot set an alarm or add a calendar event by itself. Chrome on Android lets a
// link open an app with details filled in (an "intent"): the alarm clock opens with the time set, the calendar opens
// with the event written. It needs one tap by the person on that link; nothing happens silently.

export const GALLERY_PACKAGE = "com.google.ai.edge.gallery";
export const GALLERY_PLAY = `https://play.google.com/store/apps/details?id=${GALLERY_PACKAGE}`;

export const isAndroid = (ua = "") => /android/i.test(ua);

const enc = encodeURIComponent;

// An Android intent link. extras: [["S"|"i"|"l"|"B", name, value], ...] (S text, i number, l long number, B true/false; a lowercase b is a byte).
export function intent({ action = "", host = "", scheme = "", pkg = "", extras = [], fallback = "" } = {}) {
  const parts = [];
  if (scheme) parts.push(`scheme=${scheme}`);
  if (action) parts.push(`action=${action}`);
  if (pkg) parts.push(`package=${pkg}`);
  for (const [type, name, value] of extras) parts.push(`${type}.${name}=${type === "S" ? enc(String(value)) : String(value)}`);
  if (fallback) parts.push(`S.browser_fallback_url=${enc(fallback)}`);
  return `intent:${host ? `//${host}` : ""}#Intent;${parts.join(";")};end`;
}

const pad = (n) => String(n).padStart(2, "0");
const localStamp = (d) => `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`;
const utcStamp = (d) => `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}00Z`;

// A calendar file (.ics) for one event: opening it in Android adds it to the calendar app.
export function icsFor({ title = "Event", start, end, place = "", notes = "" }) {
  const s = new Date(start), e = end ? new Date(end) : new Date(s.getTime() + 3600_000);
  const esc = (t) => String(t).replace(/[\\;,]/g, (c) => `\\${c}`).replace(/\n/g, "\\n");
  return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MBUDDY//EN", "BEGIN:VEVENT", `UID:${Date.now()}-${Math.round(s.getTime() / 1000)}@mbuddy`,
    `DTSTAMP:${utcStamp(new Date())}`, `DTSTART:${utcStamp(s)}`, `DTEND:${utcStamp(e)}`, `SUMMARY:${esc(title)}`,
    place ? `LOCATION:${esc(place)}` : "", notes ? `DESCRIPTION:${esc(notes)}` : "", "END:VEVENT", "END:VCALENDAR"].filter(Boolean).join("\r\n");
}

// One action as what the page shows: a label, the link to tap, and others to try if the first is not what the phone has.
// What the Android app can do itself (Actions.kt): inside the app each of these is done by the app, not by a link the page opens.
const APP_KINDS = new Set(["alarm", "timer", "calendar", "call", "sms", "whatsapp", "navigate", "search", "email", "open_gallery"]);

export function buildAction(kind, p = {}) {
  const a = buildLink(kind, p);
  if (a && APP_KINDS.has(kind)) a.native = { kind, params: p };
  return a;
}

function buildLink(kind, p = {}) {
  switch (kind) {
    case "alarm":
      return { label: `Alarm at ${pad(p.hour)}:${pad(p.minute)}${p.label ? ` · ${p.label}` : ""}`, android: true,
        href: intent({ action: "android.intent.action.SET_ALARM", extras: [["i", "android.intent.extra.alarm.HOUR", p.hour], ["i", "android.intent.extra.alarm.MINUTES", p.minute],
          ["S", "android.intent.extra.alarm.MESSAGE", p.label || "MBUDDY"], ["B", "android.intent.extra.alarm.SKIP_UI", "true"]] }) };
    case "timer": {
      const m = Math.floor(p.seconds / 60), s = p.seconds % 60;
      return { label: `Timer ${m ? `${m} min` : ""}${m && s ? " " : ""}${s ? `${s} s` : ""}${p.label ? ` · ${p.label}` : ""}`, android: true,
        href: intent({ action: "android.intent.action.SET_TIMER", extras: [["i", "android.intent.extra.alarm.LENGTH", p.seconds], ["S", "android.intent.extra.alarm.MESSAGE", p.label || "MBUDDY"], ["B", "android.intent.extra.alarm.SKIP_UI", "true"]] }) };
    }
    case "calendar": {
      const s = new Date(p.start), e = p.end ? new Date(p.end) : new Date(s.getTime() + 3600_000);
      const web = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${enc(p.title || "Event")}&dates=${localStamp(s)}/${localStamp(e)}&details=${enc(p.notes || "")}&location=${enc(p.place || "")}`;
      return { label: `Calendar: ${p.title || "Event"} · ${s.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`, android: true,
        href: intent({ host: "com.android.calendar/events", scheme: "content", action: "android.intent.action.INSERT",
          extras: [["S", "title", p.title || "Event"], ["S", "eventLocation", p.place || ""], ["S", "description", p.notes || ""], ["l", "beginTime", s.getTime()], ["l", "endTime", e.getTime()]] }),
        alt: [{ label: "Google Calendar", href: web }, { label: "Calendar file (.ics)", file: { name: "event.ics", type: "text/calendar", text: icsFor({ ...p, start: s, end: e }) } }] };
    }
    case "call": return { label: `Call ${p.number}`, href: `tel:${p.number}` };
    case "sms": return { label: `Text ${p.number}`, href: `sms:${p.number}${p.body ? `?body=${enc(p.body)}` : ""}` };
    case "whatsapp": return { label: `WhatsApp ${p.number}`, href: `https://wa.me/${String(p.number).replace(/\D/g, "")}${p.body ? `?text=${enc(p.body)}` : ""}` };
    case "navigate": return { label: `Directions to ${p.place}`, href: `https://www.google.com/maps/dir/?api=1&destination=${enc(p.place)}` };
    case "search": return { label: `Search: ${p.query}`, href: `https://www.google.com/search?q=${enc(p.query)}` };
    case "email": return { label: `Email${p.to ? ` ${p.to}` : ""}`, href: `mailto:${p.to || ""}?subject=${enc(p.subject || "")}&body=${enc(p.body || "")}` };
    case "open_gallery": return { label: "Open AI Edge Gallery", android: true, href: intent({ pkg: GALLERY_PACKAGE, fallback: GALLERY_PLAY }) };
    default: return null;
  }
}

// ---- understanding a typed or spoken line, on the phone, with no PC and no model ----
const DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];

export function parseClock(text) {
  const m = /\b(?:at|for|by)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b/i.exec(text);
  if (!m) return null;
  let h = Number(m[1]), min = Number(m[2] || 0);
  const ap = (m[3] || "").toLowerCase().replace(/\./g, "");
  if (ap === "pm" && h < 12) h += 12;
  if (ap === "am" && h === 12) h = 0;
  if (!ap && !m[2] && !/\bat\b/i.test(m[0])) return null;      // a bare number is not a time
  return h > 23 || min > 59 ? null : { h, min };
}

// "tomorrow at 9", "friday 15:30", "in 2 hours", "tonight" -> a Date, or null.
export function parseWhen(text, now = new Date()) {
  const t = String(text).toLowerCase();
  const rel = /\bin\s+(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|hr|day)s?\b/.exec(t);
  if (rel) return new Date(now.getTime() + Number(rel[1]) * { second: 1e3, sec: 1e3, minute: 6e4, min: 6e4, hour: 36e5, hr: 36e5, day: 864e5 }[rel[2]]);
  const d = new Date(now);
  let dayGiven = false;
  if (/\btomorrow\b/.test(t)) { d.setDate(d.getDate() + 1); dayGiven = true; }
  else if (/\b(today|tonight)\b/.test(t)) dayGiven = true;
  else {
    const wd = DAYS.findIndex((n) => new RegExp(`\\b${n}\\b`).test(t));
    if (wd >= 0) { d.setDate(d.getDate() + ((wd - d.getDay() + 7) % 7 || 7)); dayGiven = true; }
  }
  const c = parseClock(t.replace(/\b(today|tonight|tomorrow)\b/g, ""));
  if (c) d.setHours(c.h, c.min, 0, 0);
  else if (dayGiven) d.setHours(/\btonight\b/.test(t) ? 20 : 9, 0, 0, 0);
  else return null;
  if (!dayGiven && d <= now) d.setDate(d.getDate() + 1);
  return d;
}

const UNIT_S = { second: 1, sec: 1, minute: 60, min: 60, hour: 3600, hr: 3600 };
const NUM = /([+]?\d[\d\s-]{2,}\d)/;

// What a line means, as a list of { kind, params } (a task has kind "task"). [] when nothing is understood.
export function parseQuick(line, now = new Date()) {
  const text = String(line || "").trim();
  if (!text) return [];
  let m;
  if ((m = /(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|hr)s?\s*timer|timer\s*(?:for\s*)?(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|hr)s?/i.exec(text))) {
    const n = Number(m[1] || m[3]), u = (m[2] || m[4]).toLowerCase();
    const seconds = Math.round(n * UNIT_S[u]);
    if (seconds >= 1 && seconds <= 86400) return [{ kind: "timer", params: { seconds } }];
  }
  if (/\b(alarm|wake me)\b/i.test(text)) {
    const c = parseClock(text);
    if (c) return [{ kind: "alarm", params: { hour: c.h, minute: c.min, label: /\bfor\s+([a-z ]{3,30})$/i.exec(text)?.[1] || "" } }];
  }
  if ((m = /^remind me (?:to )?(.+)$/i.exec(text)) || (m = /^(?:add (?:a )?)?(?:task|todo)[: ]\s*(.+)$/i.exec(text))) {
    const rest = m[1].trim();
    // "in 20 minutes to stretch", "tomorrow at 9 to call Sara": the time comes first, then "to" and the thing.
    const lead = /^((?:in|at|on|tomorrow|today|tonight)\b.*?)\s+to\s+(.+)$/i.exec(rest);
    const cut = lead ? null : /\s+(?:at|on|tomorrow|today|tonight|in)\s+/i.exec(rest);
    const title = lead ? lead[2].trim() : (cut ? rest.slice(0, cut.index) : rest).trim() || rest;
    const when = parseWhen(lead ? lead[1] : rest.slice(cut ? cut.index : 0), now);
    const out = [{ kind: "task", params: { title, when: when ? when.toISOString() : "" } }];
    if (when) out.push({ kind: "calendar", params: { title, start: when.toISOString(), end: "", place: "", notes: "" } });
    return out;
  }
  // "add meeting with Ali on friday at 3pm to my calendar", "schedule dentist tomorrow at 10"
  if ((m = /^(?:add|put|schedule|create)\s+(.+?)(?:\s+(?:to|in|on)\s+(?:my\s+)?calendar)?$/i.exec(text)) && (/calendar$/i.test(text) || /^schedule\b/i.test(text))) {
    const rest = m[1].trim(), cut = /\s+(?:at|on|tomorrow|today|tonight|in)\s+/i.exec(rest);
    const when = parseWhen(cut ? rest.slice(cut.index) : rest, now);
    if (when) return [{ kind: "calendar", params: { title: (cut ? rest.slice(0, cut.index) : rest).trim(), start: when.toISOString(), end: "", place: "", notes: "" } }];
  }
  if ((m = /^(?:call|ring|dial)\s+(.+)$/i.exec(text))) { const n = NUM.exec(m[1]); if (n) return [{ kind: "call", params: { number: n[1].replace(/[\s-]/g, "") } }]; }
  if ((m = /^(?:whatsapp|wa)\s+([+\d][\d\s-]{2,})\s*(.*)$/i.exec(text))) return [{ kind: "whatsapp", params: { number: m[1].replace(/[\s-]/g, ""), body: m[2] } }];
  if ((m = /^(?:text|sms|message)\s+([+\d][\d\s-]{2,})\s*(.*)$/i.exec(text))) return [{ kind: "sms", params: { number: m[1].replace(/[\s-]/g, ""), body: m[2] } }];
  if ((m = /(?:navigate|directions?|take me|drive|route)\s+to\s+(.+)$/i.exec(text))) return [{ kind: "navigate", params: { place: m[1].trim() } }];
  if ((m = /^(?:search|google|look up|find)(?: for)?\s+(.+)$/i.exec(text))) return [{ kind: "search", params: { query: m[1].trim() } }];
  if (/^open (?:the )?(?:ai )?(?:edge )?gallery$/i.test(text)) return [{ kind: "open_gallery", params: {} }];
  // a bare "thing + when": "dentist friday 3pm", "pay rent tomorrow", "football match saturday at 5". Not a question, short, with a day or a clock time.
  if (!/\?/.test(text) && text.split(/\s+/).length <= 9 && !/^(what|when|where|who|why|how|which|is|are|do|does|can|could|would|will|did|tell|show|open|play|set|turn|i|my|it|the)\b/i.test(text)) {
    const cut = /\s+(?:(?:at|on|by|this)\s+)?(?:tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2})\b/i.exec(text);
    if (cut && cut.index >= 2) {
      const title = text.slice(0, cut.index).trim(), when = parseWhen(text.slice(cut.index), now);
      if (when && title.length >= 2 && title.length <= 80) {
        const isEvent = /\b(meeting|appointment|dentist|doctor|match|game|class|lunch|dinner|breakfast|party|flight|interview|concert|birthday|exam|date|training|gym|visit)\b/i.test(title);
        return [isEvent ? { kind: "calendar", params: { title, start: when.toISOString(), end: "", place: "", notes: "" } } : { kind: "task", params: { title, when: when.toISOString() } }];
      }
    }
  }
  return [];
}

// ---- which on-device model to get in Google AI Edge Gallery ----
export const MODELS = {
  e2b: { name: "Gemma 4 E2B", size: "2.58 GB" },
  e4b: { name: "Gemma 4 E4B", size: "3.65 GB" },
};

// profile: { android, ram (GB, as the browser reports it: at most 8), cores, webgpu }
export function recommend(profile = {}) {
  const { android, ram, cores } = profile;
  const caution = "Small models make mistakes with tools (one test: about 7 calls in 10 were valid). When your PC or the internet is reachable, MBUDDY's own brain is more reliable.";
  if (!android) return { model: null, why: "AI Edge Gallery runs models on Android (and iOS, with fewer tools). This device doesn't look like an Android phone, so there is nothing to download here.", caution };
  if (!ram) return { model: MODELS.e2b, alt: null, why: "I can't read this phone's memory from a web page, so start with the smaller model: it fits almost every phone from the last few years.", caution };
  if (ram >= 8) return { model: MODELS.e4b, alt: MODELS.e2b, why: "This phone reports 8 GB of memory or more: the larger model gives better answers and still fits. Pick E2B instead if you care more about speed and battery.", caution };
  if (ram >= 6) return { model: MODELS.e2b, alt: MODELS.e4b, why: "With about 6 GB of memory the smaller model is smooth. E4B can work but may be slow, and the phone may close other apps to make room.", caution };
  if (ram >= 4) return { model: MODELS.e2b, alt: null, why: "With about 4 GB of memory only the smaller model is realistic, and other apps may be closed while it runs.", caution };
  return { model: null, why: "This phone reports 3 GB of memory or less: a model on the phone would be slow and unstable. Use MBUDDY through your PC, or the cloud brain, instead.", caution };
}
