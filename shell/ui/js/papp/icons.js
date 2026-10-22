// Line icons (24 x 24, drawn with a stroke) used across the phone app, and the doodle wallpapers made from them. No emoji anywhere.
export const PATHS = {
  star: "M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.5 6.6 19.5l1.2-6L3.3 9.3l6.1-.7z",
  heart: "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.6-7 10-7 10z",
  chat: "M4 5h16v11H9l-5 4z",
  clock: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM12 8v4l3 2",
  cal: "M5 6h14v13H5zM5 10h14M9 4v3M15 4v3",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2",
  moon: "M20 14a8 8 0 1 1-9-10 6 6 0 0 0 9 10z",
  check: "M4 12l5 5L20 6",
  list: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
  pencil: "M4 20l1-4L16 5l3 3L8 19zM14 7l3 3",
  bulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10c1 1 1 2 1 3h6c0-1 0-2 1-3a6 6 0 0 0-4-10z",
  paper: "M6 3h9l4 4v14H6zM15 3v4h4",
  pin: "M12 21s-6-6-6-11a6 6 0 0 1 12 0c0 5-6 11-6 11zM12 7v6",
  gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1",
  bolt: "M13 2L5 14h6l-1 8 8-12h-6z",
  cloud: "M7 18h10a4 4 0 0 0 0-8 6 6 0 0 0-11-1 4 4 0 0 0 1 9z",
  smile: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM8.5 14q3.5 3 7 0M9 10h.01M15 10h.01",
  dice: "M5 5h14v14H5zM9 9h.01M15 9h.01M12 12h.01M9 15h.01M15 15h.01",
  pad: "M9 4h6v5h5v6h-5v5H9v-5H4V9h5zM12 12h.01",
  trophy: "M8 4h8v5a4 4 0 0 1-8 0zM8 6H4v2a3 3 0 0 0 4 3M16 6h4v2a3 3 0 0 1-4 3M12 13v4M8 20h8",
  timer: "M10 3h4M12 7a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM12 10v4l2 1",
  target: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 11.5h.01",
  flame: "M12 3c1 4 5 5 5 10a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3-1-5 1-9z",
  phone: "M8 3h8a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM11 18h2",
  music: "M9 18V6l10-2v12M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0zM19 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
  plus: "M12 5v14M5 12h14",
};
export const icon = (name, cls = "") => `<svg class="pa-i ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${PATHS[name] || ""}"/></svg>`;

// each place has its own mood colour and its own doodles: small line drawings of what the place is for
export const MOODS = {
  buddy:    { color: "#ffcc33", doodles: ["chat", "star", "heart", "smile", "bolt", "cloud", "chat", "star"] },
  today:    { color: "#60a5fa", doodles: ["clock", "cal", "sun", "moon", "cloud", "star", "clock", "pin"] },
  do:       { color: "#4ade80", doodles: ["check", "list", "timer", "target", "bolt", "pencil", "check", "flame"] },
  remember: { color: "#a78bfa", doodles: ["paper", "pencil", "bulb", "heart", "pin", "star", "paper", "smile"] },
  play:     { color: "#fb7185", doodles: ["pad", "dice", "trophy", "star", "bolt", "flame", "pad", "heart"] },
  settings: { color: "#9ca3af", doodles: ["gear", "bolt", "cloud", "gear", "star", "phone", "gear", "bolt"] },
};
const SPOTS = [[10, 10, -12], [78, 6, 10], [140, 22, -6], [34, 60, 14], [104, 66, -10], [164, 78, 8], [8, 116, -14], [70, 124, 12], [132, 128, -8], [180, 138, 16], [40, 170, 6], [100, 172, -12], [156, 180, 10], [12, 64, -4]];
const cache = {};
/** The wallpaper tile of a place, as a CSS url(): a 200 px square of doodles that repeats. */
export function wallpaper(place) {
  const m = MOODS[place] || MOODS.buddy;
  return cache[place] || (cache[place] = (() => {
    const g = SPOTS.map(([x, y, r], i) => `<g transform="translate(${x} ${y}) rotate(${r} 12 12) scale(.8)"><path d="${PATHS[m.doodles[i % m.doodles.length]]}"/></g>`).join("");
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200" fill="none" stroke="${m.color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${g}</svg>`;
    return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
  })());
}
