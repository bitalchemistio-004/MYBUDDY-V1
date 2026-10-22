// What the panel's cards show, worked out from live state. Pure: no DOM, so
// it is tested in Node. Nothing here invents a value: what the PC or the
// player does not report is left out.

// The PC strip: one tile per number this PC really reports.
export function pcTiles(pc) {
  if (!pc) return [];
  const tiles = [];
  const add = (label, value, unit, bad) => { if (typeof value === "number") tiles.push({ label, text: `${value}${unit}`, bad: !!bad }); };
  add("CPU", pc.cpu, "%", pc.cpu >= 90);
  add("Memory", pc.memory, "%", pc.memory >= 90);
  add(pc.charging ? "Charging" : "Battery", pc.battery, "%", pc.battery <= 15 && !pc.charging);
  add("GPU", pc.gpu_temp, "°", pc.gpu_temp >= 85);
  return tiles;
}

// m:ss, or h:mm:ss for long tracks.
export function clockTime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`;
}

// The music card. `progress` is null when the player does not say how long
// the track is (then no bar and no times are shown).
export function musicView(media) {
  if (!media) return null;
  const known = media.duration_s > 0;
  return {
    title: media.title,
    artist: media.artist || "",
    app: media.app || "",
    playing: !!media.playing,
    progress: known ? Math.min(100, Math.round(media.position_s / media.duration_s * 100)) : null,
    now: known ? clockTime(media.position_s) : "",
    total: known ? clockTime(media.duration_s) : "",
  };
}

// The reminder to show: the next one with a time, soonest first. One whose
// time passed in the last hour still shows (it may be waiting for "Done").
export const OVERDUE_SHOWN_MS = 60 * 60_000;

export function nextReminder(tasks, now = new Date()) {
  return upcoming(tasks, now, 1)[0] || null;
}

// The reminders still to come (and the ones whose time passed within the
// last hour), soonest first.
export function upcoming(tasks, now = new Date(), limit = 5) {
  return (tasks || []).filter((t) => t && t.due_date && !t.completed)
    .map((t) => ({ ...t, at: new Date(t.due_date) }))
    .filter((t) => !Number.isNaN(t.at.getTime()) && t.at.getTime() >= now.getTime() - OVERDUE_SHOWN_MS)
    .sort((a, b) => a.at - b.at)
    .slice(0, limit);
}

// The days of `month`'s month that have a reminder on them.
export function eventDays(tasks, month) {
  const days = new Set();
  for (const t of tasks || []) {
    if (!t || !t.due_date || t.completed) continue;
    const at = new Date(t.due_date);
    if (!Number.isNaN(at.getTime()) && at.getFullYear() === month.getFullYear() && at.getMonth() === month.getMonth()) days.add(at.getDate());
  }
  return days;
}

// "today 3:30 PM", "tomorrow 9:00 AM", "Fri 2 Oct, 9:00 AM".
export function whenText(at, now = new Date()) {
  const time = at.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(at) - day(now)) / 86_400_000);
  if (days === 0) return `today ${time}`;
  if (days === 1) return `tomorrow ${time}`;
  return `${at.toLocaleDateString("en-US", { weekday: "short", day: "numeric", month: "short" })}, ${time}`;
}

// A month as rows of seven, Monday first: null for the blanks, else the day
// number. `hit` is the reminder's day, `today` today's, when in this month.
export function monthGrid(at, now = new Date()) {
  const first = new Date(at.getFullYear(), at.getMonth(), 1);
  const blanks = (first.getDay() + 6) % 7;
  const count = new Date(at.getFullYear(), at.getMonth() + 1, 0).getDate();
  const cells = [...Array(blanks).fill(null), ...Array.from({ length: count }, (_, i) => i + 1)];
  while (cells.length % 7) cells.push(null);
  const sameMonth = now.getFullYear() === at.getFullYear() && now.getMonth() === at.getMonth();
  return {
    title: at.toLocaleDateString("en-US", { month: "long", year: "numeric" }),
    cells,
    hit: at.getDate(),
    today: sameMonth ? now.getDate() : null,
  };
}

// Degrees for the clock's hands at a time.
export function clockAngles(at) {
  const minutes = at.getMinutes();
  return { hour: ((at.getHours() % 12) + minutes / 60) * 30, minute: minutes * 6 };
}
