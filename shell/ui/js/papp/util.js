// Small helpers shared by the phone screens.
export const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const z2 = (n) => String(n).padStart(2, "0");

/** A countdown like 4:05 or 1:02:09 from seconds. */
export function clock(secs) {
  const s = Math.max(0, Math.round(secs)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${z2(m)}:${z2(r)}` : `${m}:${z2(r)}`;
}

/** "Today", "Tomorrow" or "Mon 12 Oct" for a YYYY-MM-DD key. */
export function dayLabel(key) {
  const d = new Date(key + "T00:00"), t = new Date(); t.setHours(0, 0, 0, 0);
  const diff = Math.round((d - t) / 86400000);
  if (diff === 0) return "Today"; if (diff === 1) return "Tomorrow"; if (diff === -1) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

/** "14:05" or "2:05 PM" (the phone's own style) from "YYYY-MM-DDTHH:mm". */
export function timeOf(stamp) {
  if (!stamp || stamp.length < 16) return "";
  return new Date(stamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The first card/screen heading. */
export const head = (title, sub = "") => `<div class="pa-head"><h1>${esc(title)}</h1>${sub ? `<p>${esc(sub)}</p>` : ""}</div>`;
