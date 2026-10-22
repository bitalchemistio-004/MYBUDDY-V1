// Music: plays through the app the user chose, keeps their playlists, and
// learns their taste from what they really play (core/music_store.py). Nothing
// is shown that was not measured; taste learning is opt-in and editable.
import { api } from "../api.js";
import { musicCard } from "../buddy/live-cards.js";
import { esc, header } from "./ui.js";

export const music = {
  off: null,
  destroy() { this.off && this.off(); this.off = null; },

  async render(el, ctx) {
    const self = this;
    el.classList.add("sec");
    el.innerHTML = `<div class="top">${header("Music", "Plays in the app you chose. Learns what you like from what you really play.")}<span class="chip" id="mu-prov"></span></div>
      <div id="mu-choose"></div>
      <div class="grid12">
        <section class="surface card stack lcard c7" id="mu-now"></section>
        <section class="surface card stack c5"><h3>Play something</h3>
          <form class="stack" id="mu-form"><input class="field" id="mu-q" maxlength="200" placeholder="A song, an artist or a kind of music" aria-label="What to play">
            <div class="btnrow"><button class="btn accent" type="submit">Play</button><button class="btn" type="button" id="mu-add">Add what is playing to a playlist</button></div></form>
          <p class="note" id="mu-msg"></p></section>
        <section class="surface card stack c12"><h3>For you <button class="btn small" id="mu-ideas" type="button" style="margin-left:auto">Find ideas</button></h3><div id="mu-sug"><p class="note">I suggest songs from what you finish. Ideas come from my own knowledge and I check each one with your app where it can be checked.</p></div></section>
        <section class="surface card stack c7"><h3>Playlists</h3><div id="mu-pl"></div>
          <form class="row" id="mu-newpl"><input class="field" id="mu-pln" maxlength="60" placeholder="New playlist name" aria-label="New playlist name" style="flex:1"><button class="btn" type="submit">Create</button></form></section>
        <section class="surface card stack c5"><h3>What I know about your taste</h3><div id="mu-taste"></div></section>
      </div>`;
    const $ = (s) => el.querySelector(s);
    let st = null, roles = null;

    // the shared player card, kept live by the status stream
    const card = musicCard($("#mu-now"));
    card.update(ctx.status);
    self.off = ctx.on(() => card.update(ctx.status));

    const say = (t) => { $("#mu-msg").textContent = t || ""; };

    const drawChoose = () => {
      const music = roles && roles.roles.find((r) => r.id === "music");
      const chosen = music && music.options.find((o) => o.id === music.chosen);
      $("#mu-prov").textContent = chosen ? `Player: ${chosen.name}` : "No player chosen";
      $("#mu-prov").className = `chip${chosen ? " on" : ""}`;
      if (!music) { $("#mu-choose").innerHTML = ""; return; }
      const opts = music.options.filter((o) => o.available);
      $("#mu-choose").innerHTML = `<section class="surface card stack"><h3>${chosen ? "Play music with" : "Which app should play your music?"}</h3>
        ${chosen ? "" : `<p class="think">I don't assume one. Pick the app or web player you use, and I'll open songs there.</p>`}
        <div class="seg" role="group" aria-label="Music app">${opts.map((o) => `<button type="button" data-prov="${esc(o.id)}" aria-pressed="${o.id === music.chosen}" title="${esc(tiersText(o))}">${esc(o.name)}</button>`).join("")}</div>
        ${chosen ? `<p class="note">${esc(tiersText(chosen))}</p>` : ""}</section>`;
    };
    const tiersText = (o) => {
      const t = o.tiers, parts = [];
      if (t.control) parts.push("pause and skip");
      if (t.search) parts.push("open a search");
      if (t.play) parts.push("play a specific song");
      return `I can: ${parts.join(", ")}.${t.play ? "" : " I open a search and you pick the song."}`;
    };

    const drawPlaylists = () => {
      const pls = st.playlists;
      $("#mu-pl").innerHTML = pls.length ? pls.map((p) => `
        <div class="lrow" data-pl="${esc(p.id)}"><div class="top2"><b>${esc(p.name)}</b><span class="chip">${p.tracks.length} track${p.tracks.length === 1 ? "" : "s"}</span>
          <button class="btn small" type="button" data-delpl="${esc(p.id)}" data-name="${esc(p.name)}">Delete</button></div>
          ${p.tracks.length ? p.tracks.map((t, i) => `<div class="item"><div class="grow"><div class="t">${esc(t.title)}</div>${t.artist ? `<div class="s">${esc(t.artist)}</div>` : ""}</div>
            <button class="btn small" type="button" data-playtr="${esc(p.id)}:${i}">Play</button>
            <button class="btn small danger" type="button" data-rmtr="${esc(p.id)}:${i}" aria-label="Remove ${esc(t.title)}">Remove</button></div>`).join("")
            : `<p class="note">Empty. Add what is playing, or a suggestion.</p>`}
        </div>`).join("") : `<div class="empty">No playlists yet. Create one below, or say "add this to my playlist".</div>`;
    };

    const drawTaste = () => {
      const p = st.profile;
      const item = (a, ev) => `<div class="item"><div class="grow"><div class="t">${esc(a.name)}</div><div class="s">${ev}</div></div>
        <button class="btn small" type="button" data-forget="${esc(a.id)}">Forget</button></div>`;
      $("#mu-taste").innerHTML = `
        <div class="setting"><div><div class="t">Learn my taste</div><div class="s">${st.learn ? "I note which songs you finish and which you skip, in any player. Only titles and artists, only on this PC." : "Off. Turn it on and I'll note which songs you finish or skip, in any player. Only titles and artists, only on this PC."}</div></div>
          <button class="switch" type="button" role="switch" aria-checked="${st.learn}" aria-label="Learn my taste" id="mu-learn"></button></div>
        ${st.learn ? (p.liked.length || p.disliked.length || p.times.length ? `
          ${p.liked.length ? `<div class="label">You finish</div>${p.liked.map((a) => item(a, `finished ${a.finished} of ${a.listens} plays · e.g. ${esc(a.top_track)}`)).join("")}` : ""}
          ${p.disliked.length ? `<div class="label">You skip</div>${p.disliked.map((a) => item(a, `skipped ${a.skipped} of ${a.listens} plays`)).join("")}` : ""}
          ${p.times.length ? `<div class="label">When</div>${p.times.map((a) => item(a, `${a.finished} finished songs in the ${esc(a.name)}`)).join("")}` : ""}`
          : `<p class="think">Nothing yet. I have seen ${p.listens} listen${p.listens === 1 ? "" : "s"}. I need to see a few songs finished or skipped before I say anything about your taste.</p>`) : ""}
        <button class="btn small ghost" type="button" id="mu-unforget">Bring back forgotten items</button>`;
    };

    const refresh = async () => {
      const [s, r] = await Promise.all([api.get("/ui/music"), api.get("/ui/roles")]);
      if (!s.ok) { $("#mu-pl").innerHTML = `<div class="empty">Couldn't load Music. ${esc(s.error || "")}</div>`; return; }
      st = s; roles = r.ok ? r : null;
      drawChoose(); drawPlaylists(); drawTaste();
    };

    const play = async (query) => {
      say("Working on it…");
      const r = await api.post("/ui/music/play", { query });
      say(r.text || r.error || "");
      if (!r.ok && /NEEDS CHOICE/.test(r.text || "")) say("Pick your music app above first.");
    };

    $("#mu-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const q = $("#mu-q").value.trim();
      if (q) await play(q);
    });
    $("#mu-add").addEventListener("click", async () => {
      const target = st.playlists[0];   // the first playlist, or a new one called "My playlist"
      const r = await api.post("/ui/music/add-current", target ? { playlist: target.id } : { name: "My playlist" });
      if (r.ok) { st = r; drawPlaylists(); say(`Added “${r.added.title}” to ${r.added.playlist}.`); } else say(r.error || "Couldn't add that.");
    });
    $("#mu-newpl").addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = $("#mu-pln").value.trim();
      if (!name) return;
      const r = await api.post("/ui/music/playlists", { name });
      if (!r.ok) { ctx.toast(r.error || "Couldn't create that."); return; }
      $("#mu-pln").value = ""; st = r; drawPlaylists();
    });
    $("#mu-ideas").addEventListener("click", async (e) => {
      const b = e.currentTarget; b.disabled = true; b.textContent = "Thinking…";
      const r = await api.get("/ui/music/suggestions");
      b.disabled = false; b.textContent = "Find ideas";
      $("#mu-sug").innerHTML = r.items && r.items.length ? r.items.map((t, i) => `
        <div class="item"><div class="grow"><div class="t">${esc(t.title)}</div><div class="s">${esc(t.artist)} · ${esc(t.why)}</div></div>
          <span class="chip ${t.checked ? "on" : ""}">${t.checked ? "found in your app" : "idea, not checked"}</span>
          <button class="btn small" type="button" data-sug="${i}">${t.checked ? "Play" : "Search"}</button>
          <button class="btn small" type="button" data-sugadd="${i}" aria-label="Add ${esc(t.title)} to a playlist">+</button></div>`).join("")
        : `<p class="note">${esc(r.why || "I have no ideas I can stand behind yet.")}</p>`;
      self.ideas = r.items || [];
    });

    el.addEventListener("click", async (e) => {
      const t = e.target;
      const prov = t.closest("[data-prov]");
      if (prov) {
        const r = await api.post("/ui/roles", { role: "music", provider: prov.dataset.prov });
        if (r.ok) { roles = r; await refresh(); ctx.toast("Music will play there."); } else ctx.toast(r.error || "Couldn't choose that.");
        return;
      }
      const sw = t.closest("#mu-learn");
      if (sw) {
        const next = sw.getAttribute("aria-checked") !== "true";
        const r = await api.post("/ui/music/learn", { on: next });
        if (r.ok) { st = r; drawTaste(); } else ctx.toast(r.error || "Couldn't save that.");
        return;
      }
      const fg = t.closest("[data-forget]");
      if (fg) { const r = await api.post("/ui/music/forget", { id: fg.dataset.forget }); if (r.ok) { st = r; drawTaste(); ctx.toast("Forgotten. I won't use that."); } return; }
      if (t.closest("#mu-unforget")) { const r = await api.del("/ui/music/forget"); if (r.ok) { st = r; drawTaste(); ctx.toast("Brought back."); } return; }
      const dp = t.closest("[data-delpl]");
      if (dp) {
        if (dp.dataset.confirm !== "1") { dp.dataset.confirm = "1"; dp.textContent = "Sure?"; return; }
        const r = await api.del("/ui/music/playlists", { id: dp.dataset.delpl }); if (r.ok) { st = r; drawPlaylists(); }
        return;
      }
      const pt = t.closest("[data-playtr]");
      if (pt) { const [id, i] = pt.dataset.playtr.split(":"); say("Working on it…"); const r = await api.post(`/ui/music/playlists/${id}/play`, { index: Number(i) }); say(r.text || r.error || ""); return; }
      const rt = t.closest("[data-rmtr]");
      if (rt) { const [id, i] = rt.dataset.rmtr.split(":"); const r = await api.del(`/ui/music/playlists/${id}/tracks`, { index: Number(i) }); if (r.ok) { st = r; drawPlaylists(); } return; }
      const sg = t.closest("[data-sug]");
      if (sg && self.ideas) { const x = self.ideas[Number(sg.dataset.sug)]; await play(`${x.artist} ${x.title}`); return; }
      const sa = t.closest("[data-sugadd]");
      if (sa && self.ideas) {
        const x = self.ideas[Number(sa.dataset.sugadd)], pl = st.playlists[0];
        const made = pl || (await api.post("/ui/music/playlists", { name: "My playlist" }));
        const id = pl ? pl.id : made.created;
        const r = await api.post(`/ui/music/playlists/${id}/tracks`, { title: x.title, artist: x.artist });
        if (r.ok) { st = r; drawPlaylists(); ctx.toast(`Added to ${(st.playlists.find((p) => p.id === id) || {}).name || "your playlist"}.`); }
      }
    });
    await refresh();
  },
};
