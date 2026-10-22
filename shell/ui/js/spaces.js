// The app's structure: seven spaces, each with tabs that show the screens that
// already exist. A screen not built (or whose file is missing) simply has no
// tab; a space with no tab left is not in the rail. Old links (#/calendar,
// #/extensions ...) still work: LEGACY maps them to a space and a tab.

export const SPACES = [
  { id: "now", label: "Now", sub: "What I'm doing, and what I did.",
    tabs: [["live", "Live", "home"], ["history", "History", "activity"]] },
  { id: "plan", label: "Plan", sub: "Calendar, tasks, focus, routines and how your day went.",
    tabs: [["calendar", "Calendar", "calendar"], ["tasks", "Tasks", "tasks"], ["focus", "Focus", "focus"], ["routines", "Routines", "routines"], ["day", "Day", "today"]] },
  { id: "studio", label: "Studio", sub: "Things I play and make for you.",
    tabs: [["music", "Music", "music"], ["room", "Room", "room"]] },
  { id: "decorations", label: "Decorations", sub: "How your buddy, the notch and this window look, and what each wears.",
    tabs: [["look", "Look", "decorations"]] },
  { id: "brain", label: "Brain", sub: "What I know, how I learned it, and the apps I can use.",
    tabs: [["learned", "Learned", "learned"], ["apps", "Apps", "extensions"], ["notes", "Notes", "memory"]] },
  { id: "away", label: "Away", sub: "Reach me from your phone, safely.",
    tabs: [["phone", "Phone", "away"]] },
  { id: "settings", label: "Settings", sub: "Models, voice and this PC.",
    tabs: [["models", "Models", "models"], ["usage", "Usage", "usage"], ["messages", "Messages", "notify"], ["voice", "Voice", "voice"], ["general", "General", "settings"], ["system", "System", "status"], ["setup", "Setup guide", "setup"]] },
];

// Old section ids to "space/tab".
export const LEGACY = {
  home: "now/live", activity: "now/history", today: "plan/day", calendar: "plan/calendar", tasks: "plan/tasks",
  focus: "plan/focus", routines: "plan/routines", music: "studio/music", learned: "brain/learned", memory: "brain/notes",
  extensions: "brain/apps", buddy: "decorations/look", settings: "settings/general", status: "settings/system", setup: "settings/setup",
};

// The tabs of a space that really exist, given the loaded views. The calendar
// replaces the plain task list when it is there (as the old sidebar did).
// On a phone (window.__MBUDDY_REMOTE__) only the screens the phone gateway can serve are offered
// (api/remote_gateway.py ALLOW): the rest would only answer "the phone can't do that".
const REMOTE_VIEWS = new Set(["home", "activity", "calendar", "tasks", "focus", "routines", "away", "models"]);
export function tabsOf(space, views) {
  const remote = !!window.__MBUDDY_REMOTE__;
  return space.tabs.filter(([, , view]) => views[view] && !(view === "tasks" && views.calendar) && (!remote || REMOTE_VIEWS.has(view)));
}

export function spacesWithTabs(views) {
  return SPACES.filter((s) => tabsOf(s, views).length);
}

// "#/plan/focus", "#/focus" or "#/" to { space, tab, view } (or { setup: true }).
export function resolve(hash, views) {
  let path = (hash || "").replace(/^#\/?/, "").split("?")[0];
  if (path === "setup") return { setup: true };
  if (!path) path = "now";
  if (LEGACY[path]) path = LEGACY[path];
  const [spaceId, tabId] = path.split("/");
  const space = SPACES.find((s) => s.id === spaceId && tabsOf(s, views).length) || SPACES[0];
  const tabs = tabsOf(space, views);
  const tab = tabs.find(([id]) => id === tabId) || tabs[0];
  return { space, tabs, tab: tab[0], view: tab[2] };
}

const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
export const SPACE_ICONS = {
  now: svg("M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 12h.01"),
  plan: svg("M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"),
  studio: svg("M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"),
  brain: svg("M12 3a6 6 0 0 0-6 6c0 2.2 1.2 3.6 2 4.5V17h8v-3.5c.8-.9 2-2.3 2-4.5a6 6 0 0 0-6-6zM9 21h6"),
  away: svg("M7 2h10a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1zM11 18h2"),
  decorations: svg("M12 3a9 9 0 1 0 0 18c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.4-.3-.4-.5-.8-.5-1.3 0-1.1.9-2 2-2H17a4 4 0 0 0 4-4c0-4.4-4-8-9-8zM7.5 12h.01M9 7.5h.01M14.5 7.5h.01"),
  settings: svg("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4"),
};
