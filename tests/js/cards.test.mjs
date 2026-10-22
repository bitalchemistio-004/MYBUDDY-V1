// What the live cards show, from live state (run: node --test tests/js/).
import { test } from "node:test";
import assert from "node:assert/strict";
import { pcTiles, clockTime, musicView, nextReminder, upcoming, eventDays, whenText, monthGrid, clockAngles } from "../../shell/ui/js/buddy/cards.js";

test("the PC strip has a tile only for what this PC reports", () => {
  assert.deepEqual(pcTiles(null), []);
  assert.deepEqual(pcTiles({ cpu: 18, memory: 61, battery: null, charging: null, gpu_temp: null, strain: false }).map((t) => t.label), ["CPU", "Memory"]);
  const all = pcTiles({ cpu: 97, memory: 40, battery: 12, charging: false, gpu_temp: 88 });
  assert.deepEqual(all.map((t) => [t.label, t.text, t.bad]), [["CPU", "97%", true], ["Memory", "40%", false], ["Battery", "12%", true], ["GPU", "88\u00b0", true]]);
  assert.deepEqual(pcTiles({ cpu: 5, memory: 5, battery: 12, charging: true }).map((t) => [t.label, t.bad])[2], ["Charging", false]);
});

test("track times read as m:ss, and as h:mm:ss when long", () => {
  assert.equal(clockTime(0), "0:00");
  assert.equal(clockTime(222), "3:42");
  assert.equal(clockTime(3725), "1:02:05");
  assert.equal(clockTime(undefined), "0:00");
});

test("the player shows a bar only when the player says how long the track is", () => {
  assert.equal(musicView(null), null);
  const unknown = musicView({ title: "A", artist: "", app: "AnyPlayer", playing: true, position_s: 0, duration_s: 0 });
  assert.deepEqual([unknown.progress, unknown.now, unknown.total, unknown.playing], [null, "", "", true]);
  const known = musicView({ title: "A", artist: "B", app: "AnyPlayer", playing: false, position_s: 60, duration_s: 240 });
  assert.deepEqual([known.progress, known.now, known.total, known.playing], [25, "1:00", "4:00", false]);
});

const NOW = new Date(2026, 8, 30, 15, 0, 0);  // Wed 30 Sep 2026, 3:00 PM
const at = (day, h, m = 0) => new Date(2026, 8, day, h, m).toISOString();
const TASKS = [
  { task_id: 1, title: "no time", due_date: null, completed: false },
  { task_id: 2, title: "later today", due_date: at(30, 17, 30), completed: false },
  { task_id: 3, title: "done already", due_date: at(30, 16), completed: true },
  { task_id: 4, title: "just passed", due_date: at(30, 14, 40), completed: false },
  { task_id: 5, title: "long gone", due_date: at(29, 9), completed: false },
  { task_id: 6, title: "next month", due_date: new Date(2026, 9, 2, 9).toISOString(), completed: false },
  { task_id: 7, title: "broken", due_date: "soon", completed: false },
];

test("reminders to come are listed soonest first; one that just passed still shows", () => {
  assert.deepEqual(upcoming(TASKS, NOW).map((t) => t.task_id), [4, 2, 6]);
  assert.equal(nextReminder(TASKS, NOW).task_id, 4);
  assert.equal(upcoming(TASKS, NOW, 2).length, 2);
  assert.equal(nextReminder([], NOW), null);
  assert.equal(nextReminder(null, NOW), null);
});

test("the calendar marks the days that have a reminder", () => {
  assert.deepEqual([...eventDays(TASKS, NOW)].sort((a, b) => a - b), [29, 30]);
  assert.deepEqual([...eventDays(TASKS, new Date(2026, 9, 1))], [2]);
});

test("a reminder's time is said the way people say it", () => {
  assert.equal(whenText(new Date(2026, 8, 30, 17, 30), NOW), "today 5:30 PM");
  assert.equal(whenText(new Date(2026, 9, 1, 9, 0), NOW), "tomorrow 9:00 AM");
  assert.match(whenText(new Date(2026, 9, 2, 9, 0), NOW), /^Fri, Oct 2, 9:00 AM$|^Fri 2 Oct, 9:00 AM$/);
});

test("a month is rows of seven from Monday, with the reminder's day and today", () => {
  const grid = monthGrid(new Date(2026, 8, 30, 17, 30), NOW);
  assert.equal(grid.title, "September 2026");
  assert.equal(grid.cells.length % 7, 0);
  assert.equal(grid.cells.indexOf(1), 1, "1 Sep 2026 is a Tuesday");
  assert.deepEqual([grid.hit, grid.today], [30, 30]);
  assert.equal(grid.cells.filter(Boolean).length, 30);
  assert.equal(monthGrid(new Date(2026, 9, 2, 9), NOW).today, null, "today is not in that month");
});

test("the clock's hands point at the time", () => {
  assert.deepEqual(clockAngles(new Date(2026, 8, 30, 15, 30)), { hour: 105, minute: 180 });
  assert.deepEqual(clockAngles(new Date(2026, 8, 30, 0, 0)), { hour: 0, minute: 0 });
});
