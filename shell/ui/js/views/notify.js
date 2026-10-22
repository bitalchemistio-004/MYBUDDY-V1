// Settings, Messages: which chat and mail apps MBUDDY watches for messages you have not looked at, who matters,
// when it reminds you, and what is waiting now. Everything comes from /ui/notifications (core/notif_watch.py).
import { api } from "../api.js";
import { esc } from "./ui.js";

const ago = (t) => { const m = Math.max(0, Math.round((Date.now() / 1000 - t) / 60)); return m < 1 ? "just now" : m < 90 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };
const STATUS = { waiting: "waiting", seen: "seen" };

export default {
  async render(el, ctx) {
    el.innerHTML = `<div class="top"><div><p class="sub">MBUDDY can read the notifications Windows shows you, for the chat apps you choose, and remind you about a message you haven't looked at. Everything stays on this PC.</p></div></div>
      <div class="cards" id="nf-cards"><section class="lcard c12"><p class="none">Loading…</p></section></div>`;
    const root = el.querySelector("#nf-cards");
    let timer = null, data = null;

    const save = async (change) => {
      if (await ctx.save({ notify: change })) await load();
    };
    const load = async () => {
      const r = await api.get("/ui/notifications").catch(() => null);
      if (!r || !r.ok) { root.innerHTML = `<section class="lcard c12"><p class="none err">I couldn't read the message settings.</p></section>`; return; }
      data = r; draw();
    };
    const chips = (list, attr) => list.map((x) => `<button class="btn small" type="button" ${attr}="${esc(x)}" aria-label="Remove ${esc(x)}">${esc(x)} ✕</button>`).join("");

    const draw = () => {
      const c = data.settings, off = !c.enabled;
      const senders = [...new Set(data.recent.filter((m) => c.apps.includes(m.app)).map((m) => m.title))].filter((t) => !c.chats.includes(t) && !c.muted.includes(t)).slice(0, 8);
      root.innerHTML = `
        <section class="lcard c12"><h3>Watch for messages <span class="chip ${c.enabled ? "ok" : ""}"><span class="dot"></span>${c.enabled ? "on" : "off"}</span></h3>
          <div class="setting"><div><div class="t">Remind me about messages I haven't looked at</div>
            <div class="s">When a message from an app you chose has waited a while and you haven't opened that app, the buddy reminds you. It reads the same notification list you see in Windows, from a copy, and changes nothing in WhatsApp or anywhere else. Off until you switch it on.</div></div>
            <button class="switch" type="button" role="switch" aria-checked="${c.enabled}" aria-label="Watch for messages" id="nf-on"></button></div>
          <p class="none">Notifications from apps you didn't choose are never kept: only the app's name and a count are noticed, so you can pick it below.</p></section>
        <section class="lcard c6"><h3>Which apps</h3>
          <div class="list">${data.apps.map((a) => `<div class="item"><div class="grow"><div class="t">${esc(a.name)}</div><div class="s">${a.seen ? `${a.seen} notification${a.seen === 1 ? "" : "s"} noticed` : "none noticed yet"}</div></div>
            <button class="switch" type="button" role="switch" aria-checked="${a.watched}" aria-label="Watch ${esc(a.name)}" data-app="${esc(a.name)}"></button></div>`).join("")}</div>
          <p class="none">An app appears here once it has shown a notification. Turn on the ones you want me to watch.</p></section>
        <section class="lcard c6"><h3>Who matters</h3>
          <p class="none">Leave this empty to watch every chat of the apps you chose. Add names to watch only those people or groups: their messages count as important.</p>
          <div class="row" style="flex-wrap:wrap">${chips(c.chats, "data-unchat") || `<span class="muted">Every chat</span>`}</div>
          <div class="row"><input class="field" id="nf-chat" maxlength="60" placeholder="A name, as it shows in the notification" aria-label="Add a person or group" style="flex:1"><button class="btn" type="button" id="nf-chat-add">Add</button></div>
          ${senders.length ? `<div class="label">Seen lately</div><div class="row" style="flex-wrap:wrap">${senders.map((s) => `<button class="btn small" type="button" data-addchat="${esc(s)}">+ ${esc(s)}</button>`).join("")}</div>` : ""}
          ${c.muted.length ? `<div class="label">Left alone</div><div class="row" style="flex-wrap:wrap">${chips(c.muted, "data-unmute")}</div>` : ""}</section>
        <section class="lcard c12"><h3>When to remind me</h3>
          <div class="setting"><div><div class="t">After a message has waited</div><div class="s">A message that asks you something, or that I can't tell is unimportant.</div></div><div class="row"><input class="field" id="nf-after" type="number" min="1" max="240" value="${c.remind_after_min}" style="width:80px" aria-label="Minutes before a reminder"> <span class="muted">min</span></div></div>
          <div class="setting"><div><div class="t">After an important message</div><div class="s">From someone on your list, or with one of your words.</div></div><div class="row"><input class="field" id="nf-imp" type="number" min="1" max="60" value="${c.important_after_min}" style="width:80px" aria-label="Minutes before an important reminder"> <span class="muted">min</span></div></div>
          <div class="setting"><div><div class="t">Remind again every</div><div class="s">At most ${c.max_reminders} times per message.</div></div><div class="row"><input class="field" id="nf-rep" type="number" min="1" max="240" value="${c.repeat_every_min}" style="width:80px" aria-label="Minutes between reminders"> <span class="muted">min</span></div></div>
          <label class="stack" style="gap:4px"><span class="label">Words that make a message important (separated by commas)</span><input class="field" id="nf-kw" maxlength="400" value="${esc(c.keywords.join(", "))}" aria-label="Important words"></label>
          <div class="setting"><div><div class="t">Let the model on this PC judge messages</div><div class="s">For messages my rules can't settle: does it need an answer soon? The text goes only to the model on this PC.</div></div>
            <button class="switch" type="button" role="switch" aria-checked="${c.model_judge}" aria-label="Let the model judge" id="nf-judge"></button></div>
          <div class="setting"><div><div class="t">Allow it with the cloud brain</div><div class="s">If the thinking runs in the cloud, message text leaves this PC when the model reads it. Off: with the cloud brain, I use my rules only and never show message text to it.</div></div>
            <button class="switch" type="button" role="switch" aria-checked="${c.share_with_cloud}" aria-label="Allow message text with the cloud brain" id="nf-cloud"></button></div>
          <p class="none">Do not disturb silences these reminders too.</p></section>
        <section class="lcard c12"><h3>Waiting now <span class="chip">${data.waiting.length}</span></h3>
          ${data.waiting.length ? `<div class="list">${data.waiting.map((m) => `<div class="item"><div class="grow"><div class="t">${esc(m.title)} · ${esc(m.app)}${m.important ? ` <span class="chip bad">important</span>` : ""}</div><div class="s">${esc(m.text)} · ${ago(m.arrived)}${m.why ? ` · ${esc(m.why)}` : ""}</div></div>
            <button class="btn small" type="button" data-open="${esc(m.id)}">Open ${esc(m.app)}</button><button class="btn small" type="button" data-seen="${esc(m.id)}">I saw it</button></div>`).join("")}</div>`
            : `<p class="none">${off ? "Switch on watching above to see messages here." : "Nothing is waiting. You are all caught up."}</p>`}
          ${data.recent.length ? `<details><summary class="muted">Recent (${data.recent.length})</summary><div class="list">${data.recent.map((m) => `<div class="item"><div class="grow"><div class="t">${esc(m.title)} · ${esc(m.app)}</div><div class="s">${esc(m.text)} · ${ago(m.arrived)}</div></div><span class="chip">${STATUS[m.status] || m.status}</span></div>`).join("")}</div></details>
            <div class="row"><button class="btn small danger" type="button" id="nf-clear">Forget all of these</button></div>` : ""}</section>`;

      const sw = (id, key) => { const n = root.querySelector(id); n && n.addEventListener("click", () => save({ [key]: n.getAttribute("aria-checked") !== "true" })); };
      sw("#nf-on", "enabled"); sw("#nf-judge", "model_judge"); sw("#nf-cloud", "share_with_cloud");
      root.querySelectorAll("[data-app]").forEach((b) => b.addEventListener("click", () => {
        const on = b.getAttribute("aria-checked") !== "true", set = new Set(c.apps);
        on ? set.add(b.dataset.app) : set.delete(b.dataset.app);
        save({ apps: [...set] });
      }));
      const addChat = (name) => { name = name.trim(); if (name && !c.chats.includes(name)) save({ chats: [...c.chats, name] }); };
      root.querySelector("#nf-chat-add").addEventListener("click", () => addChat(root.querySelector("#nf-chat").value));
      root.querySelector("#nf-chat").addEventListener("keydown", (e) => { if (e.key === "Enter") addChat(e.target.value); });
      root.querySelectorAll("[data-addchat]").forEach((b) => b.addEventListener("click", () => addChat(b.dataset.addchat)));
      root.querySelectorAll("[data-unchat]").forEach((b) => b.addEventListener("click", () => save({ chats: c.chats.filter((x) => x !== b.dataset.unchat) })));
      root.querySelectorAll("[data-unmute]").forEach((b) => b.addEventListener("click", () => save({ muted: c.muted.filter((x) => x !== b.dataset.unmute) })));
      for (const [id, key] of [["#nf-after", "remind_after_min"], ["#nf-imp", "important_after_min"], ["#nf-rep", "repeat_every_min"]]) {
        const n = root.querySelector(id); n.addEventListener("change", () => save({ [key]: Number(n.value) }));
      }
      root.querySelector("#nf-kw").addEventListener("change", (e) => save({ keywords: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) }));
      root.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", async () => { const r = await api.post("/ui/notifications", { action: "open", id: b.dataset.open }).catch(() => null); if (!r || !r.ok) ctx.toast((r && r.error) || "I couldn't open it."); load(); }));
      root.querySelectorAll("[data-seen]").forEach((b) => b.addEventListener("click", async () => { await api.post("/ui/notifications", { action: "seen", id: b.dataset.seen }).catch(() => null); load(); }));
      const clear = root.querySelector("#nf-clear");
      if (clear) clear.addEventListener("click", async () => { await api.post("/ui/notifications", { action: "clear" }).catch(() => null); load(); });
    };

    await load();
    timer = setInterval(() => { if (!root.querySelector("input:focus")) load(); }, 10000);
    this._timer = () => clearInterval(timer);
  },
  destroy() { this._timer && this._timer(); },
};
