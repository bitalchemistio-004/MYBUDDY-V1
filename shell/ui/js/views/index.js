// Screen registry and sidebar icons.
import home from "./home.js";
import { tasks, memory, activity } from "./lists.js";
import { extensions, decorations, voice, settings } from "./prefs.js";
import { status, setup } from "./setup.js";

export const views = { home, tasks, memory, activity, extensions, decorations, voice, settings, status, setup };

// Sections that live in their own file. One that is not there yet is simply
// not in the app: no sidebar entry, no empty page. Each file exports its
// view ({ render(el, ctx), destroy? }) as default or under its own name.
export const OPTIONAL = ["today", "learned", "calendar", "music", "room", "focus", "routines", "models", "usage", "notify", "away"];
for (const id of OPTIONAL) {
  try {
    const module = await import(`./${id}.js`);
    const view = module.default || module[id];
    if (view && typeof view.render === "function") views[id] = view;
  } catch (e) {
    // A missing file fails to fetch and is fine; say so for anything else.
    if (!(e instanceof TypeError)) console.error(`the ${id} section did not load`, e);
  }
}

const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
export const ICONS = {
  home: svg("M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"),
  buddy: svg("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"),
  tasks: svg("M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"),
  memory: svg("M6 3h11a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6zM6 3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2M9 8h6M9 12h6"),
  activity: svg("M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"),
  extensions: svg("M10 3h4v4h3a2 2 0 0 1 2 2v3h-4v4h4v3a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-3h4v-4H5V9a2 2 0 0 1 2-2h3z"),
  settings: svg("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4"),
  status: svg("M12 14l3.5-3.5M20.5 17a9 9 0 1 0-17 0M12 14a1 1 0 1 0 0-.01"),
  today: svg("M4 20V10M10 20V4M16 20v-7M22 20H2"),
  learned: svg("M12 3a6 6 0 0 0-6 6c0 2.2 1.2 3.6 2 4.5V17h8v-3.5c.8-.9 2-2.3 2-4.5a6 6 0 0 0-6-6zM9 21h6"),
  calendar: svg("M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"),
  music: svg("M9 18V5l11-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"),
  routines: svg("M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5"),
  room: svg("M3 11l9-7 9 7v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1zM9 21v-6h6v6"),
  focus: svg("M6 3h12M6 21h12M7 3c0 5 5 6 5 9s-5 4-5 9M17 3c0 5-5 6-5 9s5 4 5 9"),
};
