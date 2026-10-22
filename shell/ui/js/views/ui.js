// Small UI helpers shared by the screens.
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function header(title, sub) {
  return `<div><h1>${esc(title)}</h1>${sub ? `<p class="sub">${esc(sub)}</p>` : ""}</div>`;
}

// A settings row with a switch. `key` is the setting name.
export function switchRow(key, title, sub, on) {
  return `<div class="setting"><div><div class="t">${esc(title)}</div>${sub ? `<div class="s">${esc(sub)}</div>` : ""}</div>
    <button class="switch" type="button" role="switch" aria-checked="${on ? "true" : "false"}" aria-label="${esc(title)}" data-switch="${esc(key)}"></button></div>`;
}

export function segRow(key, title, sub, value, choices) {
  return `<div class="setting"><div><div class="t">${esc(title)}</div>${sub ? `<div class="s">${esc(sub)}</div>` : ""}</div>
    <div class="seg" role="group" aria-label="${esc(title)}">${choices.map(([v, label]) =>
      `<button type="button" data-seg="${esc(key)}" data-value="${esc(v)}" aria-pressed="${v === value ? "true" : "false"}">${esc(label)}</button>`).join("")}</div></div>`;
}

// Wire every switch / segmented button inside `root` to ctx.save.
export function wireSettings(root, ctx, after) {
  root.querySelectorAll("[data-switch]").forEach((b) => b.addEventListener("click", async () => {
    const key = b.dataset.switch, next = b.getAttribute("aria-checked") !== "true";
    b.setAttribute("aria-checked", String(next));
    if (!(await ctx.save({ [key]: next }))) b.setAttribute("aria-checked", String(!next));
    else after && after(key, next);
  }));
  // keys with ":" (e.g. "perm:volume") are handled by their own screen
  root.querySelectorAll("[data-seg]:not([data-seg*=\":\"])").forEach((b) => b.addEventListener("click", async () => {
    const key = b.dataset.seg, value = b.dataset.value;
    if (await ctx.save({ [key]: value })) {
      root.querySelectorAll(`[data-seg="${key}"]`).forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.value === value)));
      after && after(key, value);
    }
  }));
}

export function timeAgo(isoOrHms) {
  return esc(isoOrHms || "");
}

export function fmtDue(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return esc(iso);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? `today ${time}` : `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${time}`;
}

export function fmtSecs(s) {
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), r = s % 60;
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}:${String(r).padStart(2, "0")}`;
}
