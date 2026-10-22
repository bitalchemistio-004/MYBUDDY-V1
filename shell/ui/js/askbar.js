// The Ask bar and the "needs you" strip: present in every space (not on Now,
// whose own conversation card does the same job). Both use the backend's real
// state: /control/chat for typing, and the pending PC action in the live
// status, which only a yes or a no (here, on the phone or out loud) resolves.
import { api, chatTurn, displayText } from "./api.js";

const $ = (s) => document.querySelector(s);

export function mountAskBar(ctx) {
  const needs = $("#needs"), form = $("#askbar");
  if (!needs || !form) return;
  const input = $("#ask-in"), reply = $("#ask-reply"), send = $("#ask-send");

  const answer = async (decision) => {
    const r = await api.post("/ui/pending-action", { decision }).catch(() => null);
    ctx.toast((r && (r.result || r.error)) || "Done.");
  };
  $("#needs-yes").addEventListener("click", () => answer("yes"));
  $("#needs-no").addEventListener("click", () => answer("no"));

  const redraw = () => {
    const ask = ctx.status && ctx.status.pending_action && ctx.status.pending_action.text;
    needs.hidden = !ask;
    if (ask) $("#needs-t").textContent = ask.charAt(0).toUpperCase() + ask.slice(1) + "?";
  };
  ctx.on(redraw);
  redraw();

  const show = (you, me, via) => {
    reply.hidden = false;
    reply.innerHTML = "";
    const row = (cls, text) => { const p = document.createElement("p"); p.className = cls; p.textContent = text; reply.appendChild(p); };
    row("you", you);
    row("me", me);
    if (via) row("via", via);
  };
  $("#ask-close").addEventListener("click", () => { reply.hidden = true; });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    send.disabled = true;
    const r = await chatTurn(text, ctx.settings.speak_replies, () => { send.disabled = false; show(text, "Still working on it… I'll answer here when it's done.", ""); });
    send.disabled = false;
    if (r && r.ok) show(text, displayText(r.reply) || "Okay.", r.via || "");
    else if (r && r.error === "too_slow") show(text, "This one is taking far too long, so I stopped waiting. Check Now for what happened.", "");
    else if (r && r.error === "interrupted") show(text, "Something else came in before I could answer. Ask me again?", "");
    else show(text, r && r.error && r.error.includes("No active session") ? "My voice isn't connected yet, so I can't answer. Check System." : "I couldn't answer that just now.", "");
  });
}
