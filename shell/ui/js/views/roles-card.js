// "Which app does which job": for each role (music, browser) the user picks
// the app or web service the PC uses, from what is really available
// (/ui/roles, core/roles.py). Each option says honestly what MBUDDY can do
// with it. Nothing is chosen until the user chooses; "add my own" takes a
// program on this PC or a web address with {q} where the search words go.
import { api } from "../api.js";
import { esc } from "./ui.js";

const TIERS = [["control", "control"], ["search", "search"], ["play", "play a song"]];

export function rolesCard(el, ctx) {
  let roles = null, error = "";

  const chips = (tiers) => TIERS.map(([key, label]) =>
    `<span class="tier${tiers && tiers[key] ? " y" : ""}" title="${tiers && tiers[key] ? "MBUDDY can" : "MBUDDY can't"} ${esc(label)} with this">${esc(label)}</span>`).join("");

  function draw() {
    if (!roles) { el.innerHTML = `<h3>Which app does which job</h3><p class="none${error ? " err" : ""}">${esc(error || "Loading\u2026")}</p>`; return; }
    el.innerHTML = `<h3>Which app does which job</h3>
      <p class="none">Pick what this PC uses for each job. Nothing is used until you choose. For music, the chips say what I can really do with each player: control what is playing, search in it, play a song you name.</p>
      ${roles.map((r) => `
        <div class="role" data-role="${esc(r.id)}">
          <div class="role-head"><b>${esc(r.label)}</b><small>${esc(r.hint || "")}</small>
            <span class="chip${r.chosen ? " on" : ""}">${r.chosen ? esc((r.options.find((o) => o.id === r.chosen) || {}).name || r.chosen) : "not chosen"}</span></div>
          <div class="prov">${r.options.length ? r.options.map((o) => `
            <div class="provopt${o.available ? "" : " off"}">
              <button type="button" class="btn small" data-pick="${esc(o.id)}" aria-pressed="${String(r.chosen === o.id)}" ${o.available ? "" : "disabled"}>${esc(o.name)}</button>
              ${r.id === "music" ? `<div class="tiers">${chips(o.tiers)}</div>` : ""}
              <small>${esc(o.note || (o.kind === "web" ? "opens in your browser" : "app"))}</small>
              ${o.custom ? `<button type="button" class="btn small ghost" data-remove="${esc(o.id)}" aria-label="Remove ${esc(o.name)}">Remove</button>` : ""}
            </div>`).join("") : `<p class="none">Nothing for this job was found on this PC. Add your own below.</p>`}
          </div>
          <div class="row" style="flex-wrap:wrap">
            ${r.chosen ? `<button type="button" class="btn small ghost" data-clear>Use none</button>` : ""}
            <button type="button" class="btn small" data-add>Add my own</button>
          </div>
          <form class="rolefrm" data-form hidden>
            <input class="field" name="name" placeholder="Its name" maxlength="60" aria-label="Name">
            <div class="seg" role="group" aria-label="What it is"><button type="button" data-kind="web" aria-pressed="true">A website</button><button type="button" data-kind="app" aria-pressed="false">A program</button></div>
            <input class="field" name="search" placeholder="Address with {q} where the search words go, like https://site.example/search?q={q}" maxlength="300" aria-label="Web address with {q}">
            <input class="field" name="exe" placeholder="The program's full path, like C:\\Programs\\Player\\player.exe" maxlength="300" aria-label="Program path" hidden>
            <input class="field" name="exe_args" placeholder="What to pass it (optional), with {q} for the search words" maxlength="200" aria-label="Program arguments" hidden>
            <div class="row"><button class="btn small accent" type="submit">Add</button><small class="none err" data-err></small></div>
          </form>
        </div>`).join("")}`;
  }

  const take = (r) => {
    if (r && r.ok && Array.isArray(r.roles)) { roles = r.roles; error = ""; draw(); return true; }
    return false;
  };

  async function load() {
    const r = await api.get("/ui/roles").catch(() => null);
    if (take(r)) return;
    roles = null;
    error = r && r.error === "HTTP 404" ? "This needs a newer MBUDDY backend than the one running. Restart MBUDDY." : "I couldn't read the apps on this PC. Check Status.";
    draw();
  }

  el.addEventListener("click", async (e) => {
    const role = e.target.closest("[data-role]");
    if (!role) return;
    const id = role.dataset.role;
    const pick = e.target.closest("[data-pick]"), kind = e.target.closest("[data-kind]");
    if (pick) {
      const r = await api.post("/ui/roles", { role: id, provider: pick.dataset.pick }).catch(() => null);
      if (!take(r)) ctx.toast((r && r.error) || "Couldn't choose that.");
    } else if (e.target.closest("[data-clear]")) {
      const r = await api.post("/ui/roles", { role: id, provider: null }).catch(() => null);
      if (!take(r)) ctx.toast((r && r.error) || "Couldn't change that.");
    } else if (e.target.closest("[data-remove]")) {
      const r = await api.del("/ui/roles/custom", { id: e.target.closest("[data-remove]").dataset.remove }).catch(() => null);
      if (!take(r)) ctx.toast((r && r.error) || "Couldn't remove that.");
    } else if (e.target.closest("[data-add]")) {
      const form = role.querySelector("[data-form]");
      form.hidden = !form.hidden;
      if (!form.hidden) form.elements.name.focus();
    } else if (kind) {
      const form = kind.closest("form"), web = kind.dataset.kind === "web";
      form.querySelectorAll("[data-kind]").forEach((b) => b.setAttribute("aria-pressed", String(b === kind)));
      form.elements.search.hidden = !web;
      form.elements.exe.hidden = web;
      form.elements.exe_args.hidden = web;
    }
  });

  el.addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-form]");
    if (!form) return;
    e.preventDefault();
    const role = form.closest("[data-role]").dataset.role;
    const kind = form.querySelector('[data-kind][aria-pressed="true"]').dataset.kind;
    const f = form.elements;
    const r = await api.post("/ui/roles/custom", { role, name: f.name.value.trim(), kind,
      search: kind === "web" ? f.search.value.trim() : "", exe: kind === "app" ? f.exe.value.trim() : "", exe_args: kind === "app" ? f.exe_args.value.trim() : "" }).catch(() => null);
    if (!take(r)) form.querySelector("[data-err]").textContent = (r && r.error) || "Couldn't add that.";
    else ctx.toast("Added. Pick it to use it.");
  });

  draw();
  load();
  return { reload: load };
}
