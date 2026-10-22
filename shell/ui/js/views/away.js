// Away: reach MBUDDY from your phone, safely. On the PC this is where phones are paired, paused and
// removed (/ui/remote, local only). On a phone it shows this phone's own status and the switch for
// where MBUDDY is working. Everything shown comes from the backend; nothing here is decorative.
import { api, REMOTE, setToken, token } from "../api.js";
import { esc } from "./ui.js";

const ago = (t) => {
  if (!t) return "never";
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
};

export default {
  async render(el, ctx) {
    if (REMOTE) return renderPhone(el, ctx);
    el.innerHTML = `<div class="top"><div><p class="sub">Use MBUDDY from your phone when you're away. The phone connects over a private tunnel, only after you pair it with a code from this screen, and a short list of things is all it can do.</p></div></div>
      <div class="cards" id="aw-cards"><section class="lcard c12"><p class="none">Loading…</p></section></div>`;
    const root = el.querySelector("#aw-cards");
    let timer = null;
    const load = async () => {
      const s = await api.get("/ui/remote").catch(() => null);
      if (!s || !s.ok) { root.innerHTML = `<section class="lcard c12"><p class="none err">I couldn't read the phone settings.</p></section>`; return; }
      draw(s);
    };
    const act = async (body, message) => {
      const r = await api.post("/ui/remote", body).catch(() => null);
      if (r && r.ok) { if (message) ctx.toast(message); draw(r); } else ctx.toast((r && r.error) || "That didn't work.");
    };

    const draw = (s) => {
      const ts = s.tailscale, g = s.gateway;
      const url = ts.installed && ts.running && ts.name ? `https://${ts.name}/` : "";
      const code = s.code;
      root.innerHTML = `
        <section class="lcard c7"><h3>Phone access <span class="chip ${s.paused ? "" : "ok"}"><span class="dot"></span>${s.paused ? "paused" : g.listening ? "ready" : "not running"}</span></h3>
          ${s.devices.length ? `<div class="list">${s.devices.map((d) => `<div class="item"><div class="grow"><div class="t">${esc(d.name)}</div><div class="s">paired · last seen ${ago(d.last_seen)}</div></div>
              <button class="btn small danger" type="button" data-revoke="${esc(d.id)}" aria-label="Remove ${esc(d.name)}">Remove</button></div>`).join("")}</div>`
            : `<div class="empty"><b>No phone paired yet</b><span>Press New pairing code, then open the address on your phone and type the code.</span></div>`}
          <div class="row" style="flex-wrap:wrap">
            <button class="btn accent" type="button" id="aw-code">New pairing code</button>
            <button class="btn ${s.paused ? "accent" : "danger"}" type="button" id="aw-pause" aria-label="${s.paused ? "Resume phone access" : "Pause phone access"}">${s.paused ? "Resume access" : "Pause access"}</button></div>
          ${code ? `<div class="paircode"><div class="label">Type this on your phone</div><div class="big" id="aw-digits" aria-label="Pairing code ${code.code.split("").join(" ")}">${esc(code.code)}</div><div class="muted" id="aw-left"></div></div>` : ""}
          <p class="none">Pausing cuts every phone off at once. They stay paired and come back when you resume.</p></section>
        <section class="lcard c5"><h3>Where I'm working</h3>
          <div class="seg" role="group" aria-label="Where I'm working">${[["pc", "On my PC"], ["phone", "On my phone"]].map(([v, l]) => `<button type="button" data-work="${v}" aria-pressed="${s.work_on === v}">${l}</button>`).join("")}</div>
          <p class="none">${s.work_on === "phone" ? "On your phone: this PC stays quiet. Replies, reminders and questions that need your yes go to the phone." : "On your PC: I speak and listen here. The phone still works as a remote."}</p></section>
        <section class="lcard c7"><h3>How to connect</h3>
          ${url ? `<p>This PC's private address is <code>${esc(url)}</code>. On your phone, install Tailscale, sign in to the same account, and open that address.</p>`
            : ts.installed ? `<p>Tailscale is installed but isn't running or isn't signed in. Start it, then come back here.</p>`
            : `<p>Install <b>Tailscale</b> (free) on this PC and on your phone, and sign in to the same account on both. It makes a private network between them, so nothing is opened to the internet.</p>`}
          <p class="muted">Then, once, run this on the PC so the private address leads to MBUDDY's phone page:</p>
          <p><code id="aw-cmd">tailscale serve --bg ${g.port || 5051}</code> <button class="btn small" type="button" id="aw-copy">Copy</button></p>
          <div class="setting"><div><div class="t">Also allow my home Wi-Fi</div><div class="s">Not encrypted, so only on a network you trust. ${s.lan_ips.length ? `This PC: ${esc(s.lan_ips.map((i) => `http://${i}:${g.port || 5051}/`).join(", "))}` : ""} Takes effect after MBUDDY restarts.</div></div>
            <button class="switch" type="button" role="switch" aria-checked="${s.lan}" aria-label="Also allow my home Wi-Fi" id="aw-lan"></button></div></section>
        <section class="lcard c5"><h3>What a phone can do</h3>
          <div class="list"><div class="item"><div class="grow">See what I'm doing, reminders and tasks</div></div>
            <div class="item"><div class="grow">Ask me things (never spoken on the PC)</div></div>
            <div class="item"><div class="grow">Say yes or no when I ask before acting</div></div>
            <div class="item"><div class="grow">Add or finish a task, start a focus sprint, run a routine</div></div>
            <div class="item"><div class="grow">Switch where I'm working</div></div></div>
          <p class="none">It can't change settings, read your notes, or touch files. Anything from this PC sent to the cloud still asks you first.</p></section>
        <section class="lcard c12"><h3>What a phone did</h3>
          ${s.actions.length ? `<div class="list">${s.actions.map((a) => `<div class="item"><div class="grow"><div class="t">${esc(a.what)}</div><div class="s">${esc(a.device)}</div></div><span class="faint">${ago(a.t)}</span></div>`).join("")}</div>`
            : `<p class="none">Nothing yet.</p>`}</section>`;
      root.querySelectorAll("[data-revoke]").forEach((b) => b.addEventListener("click", () => act({ action: "revoke", id: b.dataset.revoke }, "Removed. That phone can't connect any more.")));
      root.querySelector("#aw-code").addEventListener("click", () => act({ action: "new_code" }));
      root.querySelector("#aw-pause").addEventListener("click", () => act({ action: s.paused ? "resume" : "pause" }, s.paused ? "Phone access is back." : "Phone access is paused. Every phone is cut off."));
      root.querySelector("#aw-lan").addEventListener("click", () => act({ action: "lan", value: !s.lan }, "Saved. Restart MBUDDY for it to take effect."));
      root.querySelector("#aw-copy").addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(root.querySelector("#aw-cmd").textContent); ctx.toast("Copied."); } catch { ctx.toast("Select the command and copy it."); }
      });
      root.querySelectorAll("[data-work]").forEach((b) => b.addEventListener("click", async () => {
        const r = await api.post("/ui/models", { work_on: b.dataset.work }).catch(() => null);
        if (r && r.ok) { ctx.settings.work_on = b.dataset.work; ctx.toast(b.dataset.work === "phone" ? "Working on your phone. The PC stays quiet." : "Working on your PC."); load(); }
      }));
      clearInterval(timer);
      if (code) {
        const tick = () => {
          const left = Math.max(0, Math.round(code.expires - Date.now() / 1000)), n = root.querySelector("#aw-left");
          if (n) n.textContent = left ? `Works for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} more, once.` : "Expired. Press New pairing code.";
          if (!left) clearInterval(timer);
        };
        tick(); timer = setInterval(tick, 1000);
      }
    };
    await load();
    el._away = { stop() { clearInterval(timer); } };
    this._timer = () => clearInterval(timer);
  },
  destroy() { this._timer && this._timer(); },
};

async function renderPhone(el, ctx) {
  const on = ctx.settings.work_on === "phone";
  el.innerHTML = `<div class="top"><div><p class="sub">This phone is connected to your PC. Everything here goes through the private connection.</p></div></div>
    <div class="cards"><section class="lcard c6"><h3>This phone <span class="chip ok"><span class="dot"></span>${ctx.connected ? "connected" : "reconnecting"}</span></h3>
        <p>Working on: <b>${on ? "your phone" : "your PC"}</b></p>
        <p class="none">${on ? "The PC stays quiet. Replies, reminders and questions that need your yes come here." : "The PC speaks and listens. This phone works as a remote."}</p>
        <p class="none">Use the switch at the top of any screen to change it.</p></section>
      <section class="lcard c12"><h3>Phone tools <span class="chip ok"><span class="dot"></span>works without the PC</span></h3>
        <p>Set alarms and timers, add calendar events, start calls and messages, get directions, keep tasks that sync to the PC later, and see which AI model to get for this phone.</p>
        <div class="row"><a class="btn accent" href="phone" id="aw-tools">Open phone tools</a></div>
        <p class="none">It is kept on this phone, so it opens even when the PC is off.</p></section>
      <section class="lcard c12"><h3>A model on this phone <span class="chip">experimental</span></h3>
        <p>Google's <b>AI Edge Gallery</b> app runs a small model (Gemma 4) on the phone itself, with no internet needed for the thinking. It can use MBUDDY's tools on the PC through an address like the one below: the phone decides, the PC does it.</p>
        <p class="muted">In the Gallery: download a Gemma 4 model, then add this as an MCP server. It can read what I'm doing, list and add tasks, and ask me things (answers come as text). It cannot say yes to anything for you.</p>
        <div class="row" style="flex-wrap:wrap"><button class="btn" type="button" id="aw-mcp">Copy the address</button><span class="muted" id="aw-mcp-r"></span></div>
        <p class="none">Keep this address private: it works like a key for this phone. Pausing or removing the phone on the PC stops it.</p></section>
      <section class="lcard c6"><h3>Sign out</h3><p class="none">This forgets the pairing on this phone only. To remove the phone completely, press Remove in Away on the PC.</p>
        <button class="btn danger" type="button" id="aw-out">Sign out of this phone</button></section></div>`;
  el.querySelector("#aw-out").addEventListener("click", () => { setToken(""); location.reload(); });
  el.querySelector("#aw-mcp").addEventListener("click", async () => {
    const url = `${location.origin}/mcp/${token()}`;
    try { await navigator.clipboard.writeText(url); el.querySelector("#aw-mcp-r").textContent = "Copied."; }
    catch { el.querySelector("#aw-mcp-r").textContent = "Couldn't copy here: press and hold this address."; el.querySelector("#aw-mcp").insertAdjacentHTML("afterend", `<code>${esc(url)}</code>`); }
  });
}
