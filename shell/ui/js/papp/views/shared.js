// Pieces more than one screen uses.
import { CAST, CAST_INFO, stillUrl } from "../../buddy/sprite.js";
import { esc } from "../util.js";

/** Five buttons, one per buddy, each with its resting picture. [onPick] gets the chosen name. */
export function charPicker(el, current, onPick) {
  el.className = "pa-chars";
  el.innerHTML = CAST.map((c) => `<button type="button" data-c="${c}" aria-pressed="${c === current}" aria-label="${esc(CAST_INFO[c][0])}">
    <img class="pic" alt="" draggable="false" src="${stillUrl(c)}" style="object-fit:contain"><span>${esc(CAST_INFO[c][0])}</span></button>`).join("");
  el.querySelectorAll("[data-c]").forEach((b) => b.addEventListener("click", () => {
    el.querySelectorAll("[data-c]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    onPick(b.dataset.c);
  }));
}

/** Put text in the ask bar and open the keyboard, so a chip can start a sentence for you. */
export function startSentence(text) {
  const input = document.querySelector("#pa-in");
  if (!input) return;
  input.value = text; input.focus();
  try { input.setSelectionRange(text.length, text.length); } catch { /* not all inputs allow it */ }
}
