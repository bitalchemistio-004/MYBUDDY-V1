// One-time hints: a small card that explains a gesture or a place the first time it matters, and never again once it is dismissed.
// What was seen is kept in the settings (tips_seen), so it survives a restore.
import { esc } from "./util.js";

export const tipSeen = (store, id) => (store.settings.get().tips_seen || []).includes(id);
export const tipMark = (store, id) => store.settings.set({ tips_seen: [...new Set([...(store.settings.get().tips_seen || []), id])].slice(-40) });

/** The card as html, or "" if it was seen. */
export const tipHtml = (store, id, text) => (tipSeen(store, id) ? "" : `<div class="pa-tip" role="note"><span>${esc(text)}</span><button class="pa-link" data-tipx="${esc(id)}" type="button">Got it</button></div>`);

/** Make the "Got it" buttons under [root] work. */
export function wireTips(root, store) {
  root.querySelectorAll("[data-tipx]").forEach((b) => b.addEventListener("click", () => { const c = b.closest(".pa-tip"); if (c) c.remove(); tipMark(store, b.dataset.tipx); }));
}
