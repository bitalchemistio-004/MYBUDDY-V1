// Extensions, Voice & buddy, Settings. Every control saves to the backend,
// which validates it, and each one changes real behaviour
// (docs/PHASE3_BUILD_PLAN.md, "Settings and what each one controls").
import { api, BASE } from "../api.js";
import { mountStill, mountLive } from "../buddy/avatar.js";
import { CAST, CAST_INFO, castFor, clipsUrl, thumbsUrl, createSpriteBuddy, reducedMotionWanted } from "../buddy/sprite.js";
import { esc, header, switchRow, segRow, wireSettings } from "./ui.js";
import { rolesCard } from "./roles-card.js";
import { mountWardrobe } from "./wardrobe-card.js";


// The tool permissions (core/ui_settings.py TOOL_NAMES), as Extensions shows them.
const TOOLS = [
  ["volume", "PC volume", "Turn the volume up, down or mute other apps (keeps MBUDDY's voice)."],
  ["timers", "Timers, reminders & tasks", "Set timers and reminders; add to your list."],
  ["search", "Web search", "Look up live facts and news. Says so when a search fails."],
  ["memory", "Remember facts", "Save what you tell it about yourself (see Memory)."],
  ["windows", "Your apps & windows", "See what's open, battery and load. Bring up, minimize or close a window only after you say yes."],
];
const REMOVE_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>`;
// An app's name as the backend compares it (core/ui_settings.py app_allowed): case and extra spaces don't count.
const appKey = (name) => String(name).trim().split(/\s+/).join(" ").toLowerCase();

// Keep a screen's controls in step with settings changed in another window
// (the quick panel's mic switch, the buddy) while it is open (S81). A field
// the user is in, or has typed into without saving, is left alone.
function follow(view, el, ctx, extra) {
  let seen = ctx.settingsSeq;
  const off = ctx.on(() => {
    if (ctx.settingsSeq === seen) return;
    seen = ctx.settingsSeq;
    syncControls(el, ctx);
    extra && extra();
  });
  if (view.destroy) view.destroy();  // a screen that re-renders itself
  view.destroy = () => { off(); view.destroy = null; };
}

function syncControls(el, ctx) {
  const s = ctx.settings, busy = (f) => f === document.activeElement || f.dataset.dirty === "1";
  el.querySelectorAll("[data-switch]").forEach((b) => { if (b.dataset.switch in s) b.setAttribute("aria-checked", String(s[b.dataset.switch] !== false)); });
  el.querySelectorAll("[data-seg]").forEach((b) => {
    const [key, name] = b.dataset.seg.split(":");
    const value = key === "perm" ? (s.tool_permissions || {})[name] : s[key];
    if (value !== undefined) b.setAttribute("aria-pressed", String(b.dataset.value === value));
  });
  const picks = el.querySelectorAll("[data-char]");
  if (picks.length && [...picks].some((b) => (b.getAttribute("aria-pressed") === "true") !== (b.dataset.char === castFor(s.character)))) {
    picks.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.char === castFor(s.character))));
  }
  el.querySelectorAll("[data-key]").forEach((f) => {
    const v = s[f.dataset.key];
    if (v === undefined || busy(f) || String(f.value) === String(v)) return;
    if (f.tagName === "SELECT" && ![...f.options].some((o) => o.value === String(v))) return;
    f.value = v;
    f.dispatchEvent(new Event("synced"));
  });
}

// A text field saved when it loses focus; marked dirty while it has unsaved typing.
function wireText(f, ctx, done) {
  f.addEventListener("input", () => { f.dataset.dirty = "1"; });
  f.addEventListener("change", async () => {
    const ok = await ctx.save({ [f.dataset.key]: f.value });
    if (ok) { f.dataset.dirty = ""; f.value = ctx.settings[f.dataset.key]; }
    done && done(ok, f);
  });
}

// A group of PC abilities (S93). `extensions` is one nested setting, so these
// switches are wired in wireExtensions, not by wireSettings. The note under
// the description says when another setting keeps a group off.
function groupRow(g, on) {
  return `<div class="setting"><div><div class="t">${esc(g.title)}</div><div class="s">${esc(g.description)}</div>
      <div class="s" data-ext-note="${esc(g.id)}" style="color:var(--warn)" hidden></div></div>
    <button class="switch" type="button" role="switch" aria-checked="${on ? "true" : "false"}" aria-label="${esc(g.title)}" data-ext="${esc(g.id)}"></button></div>`;
}

function appRow(name, on) {
  return `<div class="item"><div class="grow"><div class="t">${esc(name)}</div><div class="s">${on ? "MBUDDY may use it." : "Off: MBUDDY leaves it alone."}</div></div>
    <button class="switch" type="button" role="switch" aria-checked="${on ? "true" : "false"}" aria-label="Let MBUDDY use ${esc(name)}" data-app="${esc(name)}"></button>
    <button class="btn icon danger" type="button" data-app-del="${esc(name)}" aria-label="Remove ${esc(name)} from the list">${REMOVE_ICON}</button></div>`;
}

// The PC-ability groups and the app list (S93). Returns what follow() runs
// when the settings change, here or in another window.
function wireExtensions(el, ctx) {
  const list = el.querySelector("#x-apps"), input = el.querySelector("#x-app-q");
  const results = el.querySelector("#x-app-results"), status = el.querySelector("#x-app-status");
  const nameMax = ctx.options.app_name_max || 60, rulesMax = ctx.options.app_rules_max || 200;
  const toolTitle = Object.fromEntries(TOOLS.map(([id, title]) => [id, title]));
  const rules = () => ctx.settings.app_rules || {};
  const listed = (name) => Object.keys(rules()).find((k) => appKey(k) === appKey(name));
  const saving = (p) => p.catch(() => { ctx.toast("Couldn't reach MBUDDY to save that."); return false; });

  // Groups: a change sends only its own group (the backend merges it in).
  const syncGroups = () => el.querySelectorAll("[data-ext]").forEach((b) =>
    b.setAttribute("aria-checked", String((ctx.settings.extensions || {})[b.dataset.ext] !== false)));
  el.querySelectorAll("[data-ext]").forEach((b) => b.addEventListener("click", async () => {
    const topic = b.dataset.ext, title = b.getAttribute("aria-label"), next = b.getAttribute("aria-checked") !== "true";
    b.setAttribute("aria-checked", String(next));
    if (await saving(ctx.save({ extensions: { [topic]: next } }))) ctx.toast(next ? `${title} on.` : `${title} off. MBUDDY will say it can't.`);
    else syncGroups();
  }));

  // A group switched on can still be off because of another setting (today
  // every PC ability also needs "Your apps & windows"): /ui/abilities says which.
  let noteTicket = 0;
  const refreshNotes = async () => {
    const mine = ++noteTicket;
    const r = await api.get("/ui/abilities").catch(() => null);
    if (mine !== noteTicket || !r || !r.ok || !Array.isArray(r.topics)) return;
    for (const t of r.topics) {
      const note = el.querySelector(`[data-ext-note="${CSS.escape(t.topic)}"]`);
      if (!note) continue;
      const text = t.state === "not available" ? "Not available right now."
        : t.enabled && t.state === "off" ? (toolTitle[t.blocked_by] ? `Not in use while “${toolTitle[t.blocked_by]}” is off above.` : "Not in use right now: another setting keeps it off.")
        : "";
      note.textContent = text;
      note.hidden = !text;
    }
  };

  // Search results for "Add an app"; an app already listed can't be added twice.
  const markResults = () => results.querySelectorAll("[data-pick]").forEach((b) => {
    const taken = !!listed(b.dataset.pick), long = b.dataset.pick.trim().length > nameMax;
    b.disabled = taken || long;
    b.lastElementChild.textContent = taken ? "In the list" : long ? "Name too long to add" : "Add";
  });

  // The app list is saved whole, one save after another, so quick changes
  // never undo each other.
  let queue = Promise.resolve();
  const saveRules = (change) => (queue = queue.then(() => saving(ctx.save({ app_rules: change({ ...rules() }) }))));
  let drawn = null;
  const drawRules = (force) => {
    const current = rules(), sig = JSON.stringify(current);
    if (!force && sig === drawn) return;
    drawn = sig;
    // Keep the keyboard where it was: the same app's control, or its neighbour when that app went.
    const f = document.activeElement, attr = f && list.contains(f) ? (f.hasAttribute("data-app") ? "data-app" : "data-app-del") : null;
    const was = attr && f.getAttribute(attr), at = attr ? [...list.querySelectorAll(`[${attr}]`)].indexOf(f) : -1;
    const names = Object.keys(current).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
    list.innerHTML = names.length ? names.map((n) => appRow(n, current[n] !== false)).join("")
      : `<div class="empty">Every app is allowed until you add it here and switch it off.</div>`;
    if (attr) {
      const same = [...list.querySelectorAll(`[${attr}]`)];
      (same.find((b) => b.getAttribute(attr) === was) || same[Math.min(at, same.length - 1)] || input).focus();
    }
    markResults();
  };
  list.addEventListener("click", async (e) => {
    const sw = e.target.closest("[data-app]"), del = e.target.closest("[data-app-del]");
    if (sw) {
      const name = sw.dataset.app, next = sw.getAttribute("aria-checked") !== "true";
      sw.setAttribute("aria-checked", String(next));
      if (await saveRules((r) => (name in r ? { ...r, [name]: next } : r))) ctx.toast(next ? `MBUDDY may use ${name}.` : `MBUDDY will leave ${name} alone.`);
      drawRules(true);  // what was saved (puts the switch back if saving failed)
    } else if (del) {
      const name = del.dataset.appDel, was = rules()[name];
      const ok = await saveRules((r) => { delete r[name]; return r; });
      drawRules(true);
      if (ok) ctx.toast(`Removed ${name}.`, 5000, { label: "Undo", run: () => saveRules((r) => ({ ...r, [name]: was !== false })).then(() => drawRules(true)) });
    }
  });

  // Add an app: installed apps whose name has the typed text (GET /ui/apps),
  // asked once the typing pauses; only the newest answer is shown.
  let timer = 0, ticket = 0, shownFor = "";
  const setStatus = (text, bad = false) => {
    status.textContent = text;
    status.style.color = bad ? "var(--destructive)" : "";
    if (bad) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
  };
  const showResults = (apps, q) => {
    shownFor = q;
    results.innerHTML = apps.map((name) => `<button class="btn ghost" type="button" data-pick="${esc(name)}" style="width:100%;justify-content:space-between;text-align:left"><span>${esc(name)}</span><small class="faint"></small></button>`).join("");
    markResults();
  };
  const clearSearch = () => {
    clearTimeout(timer); ticket++;
    input.value = "";
    showResults([], "");
    results.removeAttribute("aria-busy");
    setStatus("");
  };
  const search = async (q) => {
    if (!el.isConnected) return;  // the screen was left while the timer ran
    const mine = ++ticket;
    results.setAttribute("aria-busy", "true");
    setStatus("Searching…");
    const r = await api.get("/ui/apps?q=" + encodeURIComponent(q)).catch(() => ({ ok: false, error: "MBUDDY isn't answering." }));
    if (mine !== ticket) return;  // a newer search is on its way
    results.removeAttribute("aria-busy");
    if (!r.ok) { showResults([], q); setStatus(r.error || "Couldn't read the installed apps.", true); return; }
    showResults(r.apps, q);
    setStatus(!r.apps.length ? `No installed app matches “${q}” (${r.installed} installed).`
      : r.total > r.apps.length ? `Showing ${r.apps.length} of ${r.total} matches. Type more to narrow it down.`
      : `${r.apps.length} ${r.apps.length === 1 ? "app matches" : "apps match"}.`);
  };
  input.addEventListener("input", () => {
    const q = input.value.trim();
    if (!q) { clearSearch(); return; }
    clearTimeout(timer);
    timer = setTimeout(() => search(q), 250);
  });
  const add = async (name) => {
    const have = listed(name);
    if (have) { ctx.toast(`${have} is already in the list.`); return; }
    if (Object.keys(rules()).length >= rulesMax) { ctx.toast(`The list holds up to ${rulesMax} apps. Remove one first.`); return; }
    if (!(await saveRules((r) => ({ ...r, [name]: true })))) return;
    clearSearch();
    drawRules(true);
    const saved = listed(name) || name, sw = [...list.querySelectorAll("[data-app]")].find((b) => b.dataset.app === saved);
    if (sw) sw.focus();  // so it can be switched off straight away
    ctx.toast(`Added ${saved}. Switch it off to keep MBUDDY away from it.`);
  };
  const pick = async (b) => {
    if (!b || b.disabled) return;
    b.disabled = true;
    b.setAttribute("aria-busy", "true");
    await add(b.dataset.pick);
    if (b.isConnected) { b.removeAttribute("aria-busy"); markResults(); }
  };
  const picks = () => [...results.querySelectorAll("[data-pick]:not(:disabled)")];
  results.addEventListener("click", (e) => pick(e.target.closest("[data-pick]")));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && picks().length) { e.preventDefault(); picks()[0].focus(); }
    else if (e.key === "Escape" && input.value) { e.preventDefault(); clearSearch(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const q = input.value.trim(), all = picks();
      if (!q || q !== shownFor || !all.length) return;  // nothing shown for this text yet
      const exact = all.find((b) => appKey(b.dataset.pick) === appKey(q));
      if (exact || all.length === 1) pick(exact || all[0]); else all[0].focus();
    }
  });
  results.addEventListener("keydown", (e) => {
    const all = picks(), i = all.indexOf(document.activeElement);
    if (i < 0) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      (all[i + (e.key === "ArrowDown" ? 1 : -1)] || (e.key === "ArrowUp" ? input : all[i])).focus();
    } else if (e.key === "Escape") { e.preventDefault(); input.focus(); }
  });

  drawRules(true);
  refreshNotes();
  return () => { syncGroups(); drawRules(); refreshNotes(); };
}

// The apps the user connected, and how much MBUDDY has really learned about
// each (/ui/learned/apps): skills from its notes and how often they were
// checked. Nothing is shown as learned that has no note.
async function learnedApps(box) {
  const r = await api.get("/ui/learned/apps").catch(() => null);
  if (!r || !r.ok) { box.innerHTML = `<p class="note">Couldn't read what I have learned. Restart MBUDDY if this stays.</p>`; return; }
  box.innerHTML = r.apps.length ? r.apps.map((a) => {
    const checked = a.successes + a.failures;
    return `<div class="lrow"><div class="top2"><img alt="" width="28" height="28" style="border-radius:8px" src="${BASE}/ui/app-icon?name=${encodeURIComponent(a.name)}" onerror="this.style.visibility='hidden'">
        <b>${esc(a.name)}</b><span class="chip">${a.learned ? `${a.skills} skill${a.skills === 1 ? "" : "s"}` : "not learned yet"}</span></div>
      ${a.learned ? `<div class="metas">${checked ? `<span class="chip">checked ${a.successes} of ${checked}</span>` : ""}${a.unverified ? `<span class="chip" style="color:var(--warn)">${a.unverified} not verified</span>` : ""}</div>
        ${checked ? `<div class="ev"><i style="width:${Math.round(100 * a.successes / checked)}%"></i></div>` : `<p class="note">None of its notes have been checked yet.</p>`}`
        : `<p class="note">Ask me to do something in ${esc(a.name)} and I'll learn how, then keep what worked.</p>`}</div>`;
  }).join("") : `<div class="empty">No apps connected yet. Add one below, and ask me to use it.</div>`;
}

export const extensions = {
  async render(el, ctx) {
    const s = ctx.settings, o = ctx.options, perm = s.tool_permissions;
    const groups = o.extensions || [];
    const ready = groups.length > 0 && !!s.extensions && !!s.app_rules;  // an older backend has neither
    el.innerHTML = `${header("Apps I can use", "Which app does which job, and what MBUDDY may use. Off means it won't use it, and it tells you so instead.")}
      <section class="lcard" id="x-roles"></section>
      <section class="surface card stack" id="x-learned"><h3>Apps I have learned <span class="chip">what I can really do in each</span></h3><div id="x-learned-b"><p class="note">Looking…</p></div></section>
      <div class="surface card">
        <div class="label" style="margin-bottom:var(--s3)">Its own tools</div>
        ${TOOLS.map(([id, t, d]) => segRow("perm:" + id, t, d, perm[id], [["act", "Act"], ["off", "Off"]])).join("")}
        ${switchRow("internet_search", "Internet access for search", "Off keeps every answer on this PC (weather still needs the internet).", s.internet_search)}
        ${switchRow("awareness", "Know what I am doing", "MBUDDY is told what is in front of you, your time in apps today, your next event and what is playing, so it can answer from that. These go to the brain with your words (the cloud, when that is the brain). Off: it is told none of it.", s.awareness !== false)}
      </div>
      ${ready ? `<div class="surface card stack">
        <div><div class="label">PC abilities</div>
          <p class="muted" style="margin:4px 0 0">What MBUDDY may do on this PC, by kind. Risky actions always ask you first.</p></div>
        <div>${groups.map((g) => groupRow(g, s.extensions[g.id] !== false)).join("")}</div>
      </div>
      <div class="surface card stack">
        <div><div class="label">Apps</div>
          <p class="muted" style="margin:4px 0 0">Switch an app off and MBUDDY won't open it or act on it.</p></div>
        <div class="list" id="x-apps"></div>
        <div class="stack" style="gap:6px">
          <label class="t" for="x-app-q" style="font-weight:600">Add an app</label>
          <input class="field" id="x-app-q" type="search" autocomplete="off" spellcheck="false" maxlength="${o.app_name_max || 60}" placeholder="Type part of its name, like Spotify" aria-describedby="x-app-status">
          <small class="faint" id="x-app-status" aria-live="polite"></small>
          <div class="stack" id="x-app-results" role="group" aria-label="Installed apps" style="gap:2px;max-height:264px;overflow-y:auto;scrollbar-width:thin"></div>
        </div>
      </div>` : `<div class="surface card stack">
        <div class="label">PC abilities and apps</div>
        <p class="muted" style="margin:0">The MBUDDY backend that's running is older than this window, so it can't switch these yet. Restart MBUDDY to use them.</p>
      </div>`}`;
    el.classList.add("sec");
    rolesCard(el.querySelector("#x-roles"), ctx);
    learnedApps(el.querySelector("#x-learned-b"));
    // tool permissions are one nested setting
    el.querySelectorAll('[data-seg^="perm:"]').forEach((b) => b.addEventListener("click", async () => {
      const name = b.dataset.seg.slice(5), value = b.dataset.value;
      if (await ctx.save({ tool_permissions: { [name]: value } })) {
        el.querySelectorAll(`[data-seg="perm:${name}"]`).forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.value === value)));
        ctx.toast(value === "off" ? "Switched off. MBUDDY will say it can't." : "On.");
      }
    }));
    wireSettings(el, ctx);
    follow(extensions, el, ctx, ready ? wireExtensions(el, ctx) : null);
  },
};

export function characterPicker(ctx, id = "c-picker") {
  return `<div class="cast" id="${id}" role="group" aria-label="Choose a buddy">${CAST.map((c) => `
    <button class="charpick" type="button" data-char="${c}" aria-pressed="${castFor(ctx.settings.character) === c}">
      <div class="stage" data-stage="${c}"></div><b>${CAST_INFO[c][0]}</b><small>${CAST_INFO[c][1]}</small></button>`).join("")}</div>`;
}

// The chosen buddy plays; the others rest and play while the pointer or the
// keyboard is on them (five characters playing at once would hold five sets
// of decoded frames). Choosing one also gives the whole app its look;
// Settings > Theme can go back to Clay.
export function wirePicker(root, ctx, onPick) {
  const stage = (b) => b.querySelector("[data-stage]");
  const paint = () => root.querySelectorAll("[data-char]").forEach((b) => {
    if (b.getAttribute("aria-pressed") === "true" || b.matches(":hover, :focus-visible")) mountLive(stage(b), b.dataset.char, "idle", ctx.settings);
    else mountStill(stage(b), b.dataset.char);
  });
  paint();
  root.querySelectorAll("[data-char]").forEach((b) => {
    for (const ev of ["mouseenter", "mouseleave", "focus", "blur"]) b.addEventListener(ev, paint);
    b.addEventListener("click", async () => {
      if (await ctx.save({ character: b.dataset.char, theme: "character" })) {
        root.querySelectorAll("[data-char]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.char === b.dataset.char)));
        paint();
        onPick && onPick(b.dataset.char);
      }
    });
  });
  return paint;
}

// What each reaction is, and when the buddy really shows it. A clip the art
// has but that nothing triggers yet gets its name only: no made-up occasion.
const REACTIONS = {
  idle: ["Idle", "Nothing is going on: it breathes, blinks and looks around."],
  listen: ["Listening", "It hears you speaking."],
  think: ["Working", "It is thinking, or working on a job."],
  search: ["Searching", "A job is looking something up."],
  write: ["Writing", "A job is writing something."],
  done: ["Done", "A job or a timer finished."],
  oops: ["Oops", "A job did not work."],
  alert: ["Needs you", "It is waiting for your yes or no, or a reminder is waiting."],
  bell: ["Reminder", "A reminder's time has come."],
  love: ["Thanks", "You clicked it, or it is happy with how things went."],
  hello: ["Hello", "It wakes up, or you click it for the first time."],
  furious: ["Furious", "You poked it again and again, or a job failed three times in a row."],
  bored: ["Bored", "Eight quiet minutes with nothing to do."],
  shock: ["Surprised", "Something surprised it."],
  sleep: ["Asleep", "It is asleep until you say \u201cHey Buddy\u201d. It turns see-through."],
  music: ["Music", "Something is playing on your PC."],
  hot: ["Hot", "Your PC is straining."],
  judge: ["Checking in", "It wants to know if you are working or wandering."],
  focus: ["Focus", "A focus sprint is running."],
};
const reactionName = (clip) => (REACTIONS[clip] ? REACTIONS[clip][0] : clip.charAt(0).toUpperCase() + clip.slice(1));

// The gallery: every clip the chosen character's art has, read from its own
// clips.json. A click plays it on the preview.
async function mountGallery(el, ctx) {
  const name = castFor(ctx.settings.character);
  if (el.dataset.cast === name) return;
  el.dataset.cast = name;
  if (el._preview) { el._preview.destroy(); el._preview = null; }
  el.innerHTML = `<p class="none">Loading ${esc(CAST_INFO[name][0])}\u2019s reactions\u2026</p>`;
  let manifest;
  try { manifest = await fetch(clipsUrl(name)).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }); }
  catch { if (el.dataset.cast === name) el.innerHTML = `<p class="none err">I couldn\u2019t read this buddy\u2019s reactions.</p>`; return; }
  if (el.dataset.cast !== name) return;  // another buddy was picked meanwhile
  const thumbs = manifest.thumbs, clips = Object.keys(manifest.clips || {});
  const thumb = (clip) => {
    const n = thumbs ? thumbs.order.indexOf(clip) : -1;
    if (n < 0) return "";
    const rows = Math.ceil(thumbs.order.length / thumbs.cols);
    return `background-image:url('${thumbsUrl(name)}');background-size:${thumbs.cols * 100}% ${rows * 100}%;`
      + `background-position:${thumbs.cols > 1 ? (n % thumbs.cols) / (thumbs.cols - 1) * 100 : 0}% ${rows > 1 ? Math.floor(n / thumbs.cols) / (rows - 1) * 100 : 0}%;`;
  };
  el.innerHTML = `<div class="galrow">
      <div class="galview"><div class="stage" id="gal-stage"></div><b id="gal-name"></b><p id="gal-when"></p></div>
      <div class="gal">${clips.map((c) => `<button type="button" class="galpick" data-clip="${esc(c)}" aria-pressed="false">
        <span class="th" style="${thumb(c)}"></span><small>${esc(reactionName(c))}</small><em>${manifest.clips[c].loop ? "loop" : "once"}</em></button>`).join("")}</div>
    </div>`;
  const stage = el.querySelector("#gal-stage");
  const show = (clip) => {
    el.querySelectorAll("[data-clip]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.clip === clip)));
    el.querySelector("#gal-name").textContent = reactionName(clip);
    el.querySelector("#gal-when").textContent = REACTIONS[clip] ? REACTIONS[clip][1] : "";
    if (el._preview) el._preview.state.show(clip);
  };
  try {
    const preview = await createSpriteBuddy({ character: name, reducedMotion: reducedMotionWanted(ctx.settings) });
    if (el.dataset.cast !== name || !el.isConnected) { preview.destroy(); return; }
    el._preview = preview;
    stage.innerHTML = `<canvas aria-label="Preview of the reaction" style="display:block;width:100%;height:100%"></canvas>`;
    preview.attach(stage.firstElementChild);
  } catch { stage.innerHTML = `<p class="none err">The preview did not load.</p>`; }
  el.addEventListener("click", (e) => { const b = e.target.closest("[data-clip]"); if (b) show(b.dataset.clip); });
  show("idle");
}

const NOTCH_TOOLS = [["timer", "Timer"], ["remind", "Remind me"], ["task", "Add a task"], ["focus", "Focus"], ["music", "Music"], ["mic", "Microphone"], ["quiet", "Do not disturb"],
  ["app", "Open MBUDDY"], ["task2", "Tasks"], ["calendar", "Calendar"], ["learned", "Learned"], ["phone", "PC / phone"]];
const NOTCH_DEFAULTS = { glow: "mood", glow_strength: 60, colour: "", length: "standard", thickness: "thin", corners: "pill", border: "line", face: "eyes", clock: true, word: true,
  idle_dim: false, micro: true, breathing: true, gap: 6, buttons: NOTCH_TOOLS.map(([id]) => id) };
const nSeg = (key, label, hint, value, options) => `<div class="setting"><div><div class="t">${label}</div>${hint ? `<div class="s">${hint}</div>` : ""}</div>
  <div class="seg" role="group" aria-label="${label}">${options.map(([v, l]) => `<button type="button" data-nk="${key}" data-nv="${v}" aria-pressed="${value === v}">${l}</button>`).join("")}</div></div>`;
const nSwitch = (key, label, hint, on) => `<div class="setting"><div><div class="t">${label}</div>${hint ? `<div class="s">${hint}</div>` : ""}</div>
  <button class="switch" type="button" role="switch" aria-checked="${!!on}" aria-label="${label}" data-nsw="${key}"></button></div>`;

// A small preview of the pill with these settings: its length, thickness, corners, outline and glow colour.
function notchPreview(n, accent) {
  const w = { compact: 84, short: 200, standard: 250, long: 330 }[n.length] || 84, h = n.thickness === "regular" ? 38 : 30, r = n.corners === "soft" ? 10 : 99;
  const col = n.glow === "character" && n.colour ? n.colour : accent, k = (n.glow_strength ?? 60) / 60, glow = n.glow === "off" ? "none" : `0 0 ${Math.round(16 * k)}px ${col}${Math.round(Math.min(1, .5 * k) * 255).toString(16).padStart(2, "0")}`;
  return `<div style="display:flex;justify-content:center;padding:14px 0"><div style="width:${w}px;height:${h}px;border-radius:${r}px;background:#050506;border:1px solid ${n.border === "none" ? "transparent" : col + "88"};box-shadow:${glow};display:flex;align-items:center;gap:8px;padding:0 14px;opacity:${n.idle_dim ? .6 : 1}">
    <span style="width:8px;height:8px;border-radius:50%;background:${col};box-shadow:0 0 8px ${col}"></span>${n.word && n.length !== "compact" ? `<b style="font-size:13px">Ready</b>` : ""}${n.clock && n.length !== "compact" ? `<span style="margin-left:auto;font-size:12px;opacity:.7">19:30</span>` : ""}</div></div>`;
}

// The notch card: every control saves one notch setting; the preview is redrawn from what was saved.
function wireNotch(el, ctx, start) {
  const card = el.querySelector("#b-notch");
  if (!card) return;
  let now = { ...start };
  const accent = () => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#ffcc33";
  const prev = () => { const box = card.querySelector("#np-prev"); if (box) box.innerHTML = notchPreview(now, accent()); };
  const save = async (change) => {
    const before = now;
    now = { ...now, ...change };
    prev();
    if (!(await ctx.save({ notch: change }))) { now = before; prev(); }
  };
  prev();
  card.addEventListener("click", (e) => {
    const seg = e.target.closest("[data-nk]"), sw = e.target.closest("[data-nsw]"), nb = e.target.closest("[data-nb]");
    if (seg) { card.querySelectorAll(`[data-nk="${seg.dataset.nk}"]`).forEach((b) => b.setAttribute("aria-pressed", String(b === seg))); save({ [seg.dataset.nk]: seg.dataset.nv }); }
    if (sw) { const on = sw.getAttribute("aria-checked") !== "true"; sw.setAttribute("aria-checked", String(on)); save({ [sw.dataset.nsw]: on }); }
    if (nb) {
      const on = nb.getAttribute("aria-pressed") !== "true", list = new Set(now.buttons || []);
      on ? list.add(nb.dataset.nb) : list.delete(nb.dataset.nb);
      nb.setAttribute("aria-pressed", String(on));
      save({ buttons: [...list] });
    }
  });
  for (const [id, key, out] of [["np-gs", "glow_strength", "np-gs-v"], ["np-gap", "gap", "np-gap-v"]]) {
    const input = card.querySelector("#" + id);
    input.addEventListener("input", () => { card.querySelector("#" + out).textContent = input.value; now = { ...now, [key]: Number(input.value) }; prev(); });
    input.addEventListener("change", () => save({ [key]: Number(input.value) }));
  }
  const col = card.querySelector("#np-col");
  col.addEventListener("change", () => { card.querySelector("#np-col-clear").disabled = false; save({ colour: col.value }); });
  card.querySelector("#np-col-clear").addEventListener("click", (e) => { e.target.disabled = true; save({ colour: "" }); });
  card.querySelector("#np-reset").addEventListener("click", async () => {
    if (await ctx.save({ notch: NOTCH_DEFAULTS })) { ctx.toast("The notch is back to the defaults."); decorations.render(el, ctx); }
  });
}

export const decorations = {
  async render(el, ctx) {
    const s = ctx.settings, o = ctx.options, n = { ...NOTCH_DEFAULTS, ...(s.notch || {}) };
    // Everything about how the buddy, the notch and this window look, in big sections that fold. The live preview of the notch stays beside them.
    let openSecs = {};
    try { openSecs = JSON.parse(localStorage.getItem("mbuddy_dsec") || "null") || { buddy: true }; } catch { openSecs = { buddy: true }; }
    const sec = (id, title, chip, body) => `<details class="dsec lcard" data-k="${id}" ${openSecs[id] ? "open" : ""}><summary><h3>${title}</h3>${chip ? `<span class="chip">${chip}</span>` : ""}</summary><div class="dsec-b">${body}</div></details>`;
    el.innerHTML = `<div class="bgrid">
        <div class="bmain">
          ${sec("buddy", "Your buddy", "", characterPicker(ctx))}
          ${sec("screen", "On screen", "where it lives and how it feels", `
          ${switchRow("show_buddy", "Float on the desktop", "Drag it anywhere; it remembers the spot. Off: MBUDDY still listens and talks.", s.show_buddy)}
          ${segRow("popup_style", "Look and place", "Notch: a small pill at the top that opens into the buddy's room. Corner and edge: the character stands alone. Until you drag it.", s.popup_style, [["notch", "Notch"], ["corner", "Corner"], ["edge", "Screen edge"]])}
          ${switchRow("notch_hover", "Notch opens when I rest the pointer on it", "Off: it opens only when you click it. It closes by itself when you move away.", s.notch_hover !== false)}
          ${segRow("buddy_side", "Side of the screen", "Corner and edge only.", s.buddy_side, [["left", "Left"], ["right", "Right"]])}
          ${segRow("buddy_size", "Size", "", s.buddy_size, [["small", "Small"], ["medium", "Medium"], ["large", "Large"]])}
         
          ${switchRow("buddy_effects", "Reactions around the buddy", "Rings, waves, notes and sparks that show what it is doing.", s.buddy_effects !== false)}
          ${switchRow("expression_sounds", "Laughs, sighs and coughs", "Real sounds in MBUDDY's own voice.", s.expression_sounds)}
          ${switchRow("reduced_motion", "Reduce motion", "Keeps the poses; drops the bounces and the effects, everywhere.", s.reduced_motion)}
        `)}
          ${sec("react", "Reactions", "click to preview", `<div id="b-gal"></div>`)}
          ${sec("dress", "Wardrobe", "earned by what you do", `<div id="b-dress"></div>`)}
          ${sec("notchdeco", "Notch decoration", "earned by what you do", `<div id="b-notchdeco"></div>`)}
          ${sec("notch", "Notch behaviour", "changes apply at once", `<div id="b-notch">
          ${nSeg("glow", "Glow", "Mood: the colour follows what is happening (a deadline warms to red, a job is blue...). Character: always the buddy's colour. Off: none.", n.glow, [["mood", "Mood"], ["character", "Character"], ["off", "Off"]])}
          
          ${nSeg("length", "Resting length", "", n.length, [["compact", "Compact"], ["short", "Short"], ["standard", "Standard"], ["long", "Long"]])}
          
          ${nSeg("face", "Face while resting", "Eyes: just the eyes. Whole: the whole character. Text only: no face, just the line and the field to type in.", n.face, [["eyes", "Eyes"], ["whole", "Whole"], ["none", "Text only"]])}
          
          ${nSwitch("clock", "Show the time", "On the resting pill.", n.clock)}
          ${nSwitch("word", "Show what it is doing", "One word: Ready, Listening, Thinking, or a deadline.", n.word)}
          
          <details class="bmore"><summary>More notch settings</summary>
            <div class="setting"><div><div class="t">Glow strength</div><div class="s">How bright the glow is.</div></div>
            <div class="row" style="min-width:260px"><input type="range" class="slider" id="np-gs" min="0" max="100" value="${n.glow_strength}" aria-label="Glow strength"><b id="np-gs-v" style="width:36px">${n.glow_strength}</b></div></div>
          <div class="setting"><div><div class="t">Glow colour of your own</div><div class="s">Used when the glow is Character. A question for you or a deadline about to pass still turns it red.</div></div>
            <div class="row"><input type="color" id="np-col" value="${n.colour || "#ffcc33"}" aria-label="Glow colour"><button class="btn small" type="button" id="np-col-clear" ${n.colour ? "" : "disabled"}>Use the buddy's</button></div></div>
          
            ${nSeg("thickness", "Thickness", "", n.thickness, [["thin", "Thin"], ["regular", "Regular"]])}
          ${nSeg("corners", "Corners", "", n.corners, [["pill", "Pill"], ["soft", "Soft"]])}
          ${nSeg("border", "Outline", "", n.border, [["line", "Line"], ["none", "None"]])}
          
            <div class="setting"><div><div class="t">Distance from the top</div><div class="s">Pixels of space above the pill.</div></div>
            <div class="row" style="min-width:260px"><input type="range" class="slider" id="np-gap" min="0" max="24" value="${n.gap}" aria-label="Distance from the top"><b id="np-gap-v" style="width:36px">${n.gap}</b></div></div>
          
            ${nSwitch("idle_dim", "Fade when idle", "Dimmer until you point at it.", n.idle_dim)}
          ${nSwitch("breathing", "Slow breathing glow while idle", "", n.breathing)}
          ${nSwitch("micro", "Small movements", "Ripples on buttons, a ping when its state changes, a wobble for a reminder, a light that follows the pointer, a hello when it opens.", n.micro)}
          
            <div class="setting"><div><div class="t">Tools in its room</div><div class="s">Choose which buttons it shows. They are ordered by how often you use them.</div></div></div>
          <div class="row" style="flex-wrap:wrap" id="np-btns">${NOTCH_TOOLS.map(([id, label]) => `<button class="btn small" type="button" data-nb="${id}" aria-pressed="${(n.buttons || []).includes(id)}">${label}</button>`).join("")}</div>
          
          </details>
          <div class="row"><button class="btn small" type="button" id="np-reset">Reset the notch to the defaults</button></div>
        </div>`)}
          ${sec("app", "This window", "earned by what you do", `<div id="b-appdeco"></div>`)}
        </div>
        <aside class="bside"><section class="lcard bprev"><h3>Preview <span class="seg" id="b-pv-seg" role="group" aria-label="Preview size"><button type="button" data-pv="" aria-pressed="false">Closed</button><button type="button" data-pv="1" aria-pressed="true">Open</button></span></h3>
          <div class="bframe"><iframe id="b-pv" title="Preview of the notch" src="buddy.html?embed&room" tabindex="-1"></iframe></div>
          <p class="bnote">This is the real notch with what you have chosen. A decoration you try on shows here too.</p></section></aside>
      </div>`;
    const gallery = el.querySelector("#b-gal");
    const boxes = { dress: el.querySelector("#b-dress"), notch: el.querySelector("#b-notchdeco"), app: el.querySelector("#b-appdeco") };
    const mountAll = () => { for (const [part, box] of Object.entries(boxes)) mountWardrobe(box, ctx, part); };
    mountAll();
    const repaint = wirePicker(el, ctx, () => { if (galleryOn) mountGallery(gallery, ctx); mountAll(); });
    wireSettings(el, ctx);
    // the gallery plays animations: it is only set up once its section is open
    let galleryOn = false;
    const openGallery = () => { if (!galleryOn) { galleryOn = true; mountGallery(gallery, ctx); } };
    if (openSecs.react) openGallery();
    wireNotch(el, ctx, n);
    el.addEventListener("toggle", (e) => {
      const d = e.target; if (!d.classList || !d.classList.contains("dsec")) return;
      openSecs[d.dataset.k] = d.open;
      try { localStorage.setItem("mbuddy_dsec", JSON.stringify(openSecs)); } catch { /* it just opens the first one next time */ }
      if (d.open && d.dataset.k === "react") openGallery();
      if (d.open && boxes[d.dataset.k]) mountWardrobe(boxes[d.dataset.k], ctx, d.dataset.k);
      if (d.open && d.dataset.k === "notchdeco") mountWardrobe(boxes.notch, ctx, "notch");
    }, true);
    const frame = el.querySelector("#b-pv");
    el.querySelector("#b-pv-seg").addEventListener("click", (e) => {
      const bt = e.target.closest("[data-pv]"); if (!bt) return;
      el.querySelectorAll("#b-pv-seg [data-pv]").forEach((x) => x.setAttribute("aria-pressed", String(x === bt)));
      frame.src = `buddy.html?embed${bt.dataset.pv ? "&room" : ""}`;
    });
    // A buddy picked somewhere else (the quick panel): the tiles and the gallery follow.
    follow(decorations, el, ctx, () => { repaint(); if (galleryOn) mountGallery(gallery, ctx); mountAll(); });
    const stop = decorations.destroy;
    decorations.destroy = () => { if (gallery._preview) { gallery._preview.destroy(); gallery._preview = null; } stop && stop(); };
  },
};

// Voice: the microphone, the voice and how it speaks (its own tab, it is not a decoration).
export const voice = {
  async render(el, ctx) {
    const s = ctx.settings, o = ctx.options;
    el.innerHTML = `${header("Voice", "The microphone, the voice and how it speaks.")}
      <div class="cards"><section class="lcard c12">
          <div class="setting"><div><div class="t">Microphone</div><div class="s">Which microphone MBUDDY listens with.</div></div>
            <select class="field" id="b-mic" data-key="microphone" style="max-width:280px"><option value="">Automatic</option>${(o.mics || []).map((m) => `<option ${m === s.microphone ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></div>
          <div class="setting"><div><div class="t">Voice</div><div class="s">Kokoro voices installed on this PC.</div></div>
            <select class="field" id="b-voice" data-key="voice" style="max-width:220px">${o.voices.map((v) => `<option ${v === s.voice ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
          <div class="setting"><div><div class="t">Feeling in the voice</div><div class="s">How much emotion colours the voice: 0 is calm and even.</div></div>
            <div class="row" style="min-width:260px"><input type="range" class="slider" id="b-emo" data-key="emotion_strength" min="0" max="100" value="${s.emotion_strength}" aria-label="Feeling in the voice"><b id="b-emo-v" style="width:36px">${s.emotion_strength}</b></div></div>
          ${switchRow("microphone_on", "Microphone", "Off: the mic is released (Windows shows it unused) and MBUDDY hears nothing. Typing still works.", s.microphone_on !== false)}
          ${switchRow("speak_replies", "Speak replies out loud", "Off: replies are shown here, not spoken.", s.speak_replies)}
        </section></div>`;
    wireSettings(el, ctx);
    el.querySelector("#b-mic").addEventListener("change", (e) => ctx.save({ microphone: e.target.value }).then((ok) => ok && ctx.toast("Switching microphone\u2026")));
    el.querySelector("#b-voice").addEventListener("change", (e) => ctx.save({ voice: e.target.value }).then((ok) => ok && ctx.toast("Voice changed for the next reply.")));
    const emo = el.querySelector("#b-emo");
    emo.addEventListener("input", () => { el.querySelector("#b-emo-v").textContent = emo.value; });
    emo.addEventListener("change", () => ctx.save({ emotion_strength: Number(emo.value) }));
    emo.addEventListener("synced", () => { el.querySelector("#b-emo-v").textContent = emo.value; });
  },
};

// About you (S83): the user's own words, given to the model with every reply
// (brain/user_profile.py). The suggestion is only a placeholder.
const TEXTAREA = "min-height:104px;padding:10px var(--s4);resize:vertical;line-height:1.5";
function aboutCard(s, o) {
  const max = o.profile_max || 800;
  const area = (id, key, title, sub, placeholder) => `
    <div class="stack" style="gap:6px">
      <div><label class="t" for="${id}" style="font-weight:600">${esc(title)}</label><div class="s" style="font-size:var(--fs-small);color:var(--text-2);margin-top:2px">${esc(sub)}</div></div>
      <textarea class="field" id="${id}" data-key="${key}" rows="4" maxlength="${max}" style="${TEXTAREA}" placeholder="${esc(placeholder)}">${esc(s[key])}</textarea>
      <div class="row" style="justify-content:space-between;gap:var(--s3)"><small class="faint" id="${id}-n" aria-live="polite"></small>
        ${key === "companion_style" && o.companion_style_suggestion ? `<button class="btn ghost" type="button" id="s-style-use">Use the suggestion</button>` : ""}</div>
    </div>`;
  return `<div class="surface card stack">
      <div><div class="label">About you</div>
        <p class="muted" style="margin:4px 0 0">MBUDDY reads this before every reply. Your own words; either can stay empty.</p></div>
      ${area("s-about", "about_me", "Who you are", "What you do, what you care about, what you're in the middle of.",
        "For example: I'm a nurse on night shifts, I'm learning Python, and I'd like help keeping my sleep on track.")}
      ${area("s-style", "companion_style", "How MBUDDY should be with you", "How it should talk, act and react with you.", o.companion_style_suggestion || "")}
    </div>`;
}

function wireAbout(el, ctx) {
  const max = ctx.options.profile_max || 800, use = el.querySelector("#s-style-use");
  for (const f of el.querySelectorAll("#s-about, #s-style")) {
    const count = () => {
      el.querySelector(`#${f.id}-n`).textContent = `${f.value.length} / ${max}`;
      if (f.id === "s-style" && use) use.disabled = f.value.trim() !== "";  // only offered while empty
    };
    count();
    f.addEventListener("input", count);
    f.addEventListener("synced", count);
    wireText(f, ctx, (ok) => { count(); if (ok) ctx.toast("Saved. MBUDDY uses it from the next reply."); });
  }
  if (use) use.addEventListener("click", async () => {
    const f = el.querySelector("#s-style");
    f.value = ctx.options.companion_style_suggestion;
    f.dispatchEvent(new Event("input"));
    f.dispatchEvent(new Event("change"));  // saves it; edit it from there
    f.focus();
  });
}

export const settings = {
  async render(el, ctx) {
    const s = ctx.settings, o = ctx.options;
    const models = o.models.length ? o.models : [s.llm_model];
    el.innerHTML = `${header("Settings", "How the app looks and behaves, and what MBUDDY knows about you from your own words.")}
      <div class="surface card">
        <div class="label" style="margin-bottom:var(--s3)">General</div>
        ${segRow("theme", "Theme", "The glow follows your buddy's colour. Plain keeps it white.", s.theme, [["character", "Match my buddy"], ["clay", "Plain"]])}
        <div class="setting"><div><div class="t">Your name</div><div class="s">What MBUDDY calls you.</div></div>
          <input class="field" id="s-name" data-key="user_name" style="max-width:260px" maxlength="40" value="${esc(s.user_name)}"></div>
        ${switchRow("inner_thoughts", "Inner life (optional)", "MBUDDY has its own thoughts between chats. Off by default: it runs the AI model every few minutes in the background, which uses the GPU and can spin up the fans.", ctx.inner === true)}
        ${switchRow("do_not_disturb", "Do not disturb", "No spoken reminders or unprompted thoughts. Notifications still show.", s.do_not_disturb)}
        ${switchRow("notifications", "Notifications", "Windows notifications for reminders and timers.", s.notifications)}
        ${switchRow("start_with_windows", "Start with Windows", "Open MBUDDY quietly in the tray when you sign in.", s.start_with_windows)}
      </div>
      ${aboutCard(s, o)}
      <div class="surface card">
        <div class="setting"><div><div class="t">Brain</div><div class="s">The local model MBUDDY thinks with (installed in Ollama).</div></div>
          <select class="field" id="s-model" data-key="llm_model" style="max-width:260px">${models.map((m) => `<option ${m === s.llm_model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select></div>
        <div class="setting"><div><div class="t">Home location</div><div class="s">Used for weather when you don't name a place.</div></div>
          <input class="field" id="s-home" data-key="home_location" style="max-width:260px" maxlength="120" placeholder="e.g. London" value="${esc(s.home_location)}"></div>
      </div>`;
    wireText(el.querySelector("#s-name"), ctx, (ok, f) => ok && ctx.toast(`I'll call you ${f.value}.`));
    // "Thinks to itself" is the backend's existing companion setting, not an app setting.
    const inner = el.querySelector('[data-switch="inner_thoughts"]');
    inner.removeAttribute("data-switch");
    let innerBusy = false;
    const readInner = () => api.get("/control/companion_settings").then((r) => {
      if (r.ok && !innerBusy) { ctx.inner = !!r.companion.inner_enabled; inner.setAttribute("aria-checked", String(ctx.inner)); }
    }).catch(() => {});
    readInner();
    inner.addEventListener("click", async () => {
      const next = inner.getAttribute("aria-checked") !== "true";
      innerBusy = true;
      const r = await api.post("/control/companion_settings", { inner_enabled: next }).finally(() => { innerBusy = false; });
      if (r.ok) {
        ctx.inner = next; inner.setAttribute("aria-checked", String(next));
        ctx.toast(next ? "Inner life on. It uses the GPU in the background." : "Inner life off. The PC stays quiet until you talk.");
      } else ctx.toast("Couldn't change that.");
    });
    wireSettings(el, ctx, async (key, value) => {
      if (key === "start_with_windows") {
        const tauri = window.__TAURI__;
        try { await tauri.core.invoke("set_autostart", { enabled: value }); ctx.toast(value ? "MBUDDY will start with Windows." : "Won't start with Windows."); }
        catch (e) { ctx.toast("Couldn't change autostart: " + e); await ctx.save({ start_with_windows: !value }); settings.render(el, ctx); }
      }
    });
    el.querySelector("#s-model").addEventListener("change", (e) => ctx.save({ llm_model: e.target.value }).then((ok) => ok && ctx.toast("Brain changed for the next reply.")));
    wireText(el.querySelector("#s-home"), ctx, (ok) => ok && ctx.toast("Saved."));
    wireAbout(el, ctx);
    follow(settings, el, ctx, readInner);  // the inner-life switch lives outside the app settings
  },
};
