// Touch on rows: drag a row sideways to act on it (right to finish, left to snooze or remove), or press and hold for its menu.
// The row follows the finger, a coloured edge says what will happen, and letting go past the threshold does it. A short tap still opens
// the row as before (a drag or a hold never counts as a tap).
import { buzz } from "./haptics.js";

export const SWIPE_AT = 88;      // px of drag that commits
export const HOLD_MS = 480;

/** What a drag of [dx] px means: "right", "left", or "" (not far enough). */
export const swipeOf = (dx) => (dx >= SWIPE_AT ? "right" : dx <= -SWIPE_AT ? "left" : "");

/**
 * Wire every [data-open] row under [root]. [onSwipe](li, "right"|"left") returns true when it handled it (the row then goes);
 * [onHold](li) opens the menu.
 */
export function rowGestures(root, { onSwipe, onHold }) {
  root.querySelectorAll("[data-open]").forEach((li) => {
    if (li.dataset.gest) return; li.dataset.gest = "1";
    let sx = 0, sy = 0, dx = 0, down = false, moved = false, held = false, timer = 0, pid = 0;
    const reset = () => { li.style.transition = "transform .22s cubic-bezier(.3,1.3,.4,1)"; li.style.transform = ""; li.removeAttribute("data-swipe"); li.style.removeProperty("--sw"); setTimeout(() => { li.style.transition = ""; }, 240); };
    const cancel = () => { clearTimeout(timer); down = false; };
    li.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button, a, input, textarea")) return;
      down = true; moved = false; held = false; sx = e.clientX; sy = e.clientY; dx = 0; pid = e.pointerId;
      timer = setTimeout(() => { if (down && !moved) { held = true; buzz("tick"); onHold && onHold(li); } }, HOLD_MS);
    });
    li.addEventListener("pointermove", (e) => {
      if (!down) return;
      dx = e.clientX - sx; const dy = e.clientY - sy;
      if (!moved && Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) { cancel(); return; }       // scrolling, not swiping
      if (Math.abs(dx) > 12) {
        if (!moved) { try { li.setPointerCapture(pid); } catch { /* fine */ } }
        moved = true; clearTimeout(timer);
        const eased = Math.sign(dx) * Math.min(150, Math.abs(dx) * 0.9);
        li.style.transform = `translateX(${eased}px)`;
        li.dataset.swipe = dx > 0 ? "right" : "left";
        li.style.setProperty("--sw", String(Math.min(1, Math.abs(dx) / SWIPE_AT)));
      }
    });
    const end = () => {
      clearTimeout(timer);
      if (!down) return; down = false;
      if (moved) {
        const dir = swipeOf(dx);
        if (dir && onSwipe && onSwipe(li, dir)) { buzz("ok"); li.style.transition = "transform .2s, opacity .2s"; li.style.transform = `translateX(${dir === "right" ? 110 : -110}%)`; li.style.opacity = "0"; return; }
        reset();
      }
    };
    li.addEventListener("pointerup", end); li.addEventListener("pointercancel", () => { cancel(); reset(); });
    // a drag or a hold is not a tap: stop the click that follows it
    li.addEventListener("click", (e) => { if (moved || held) { e.stopImmediatePropagation(); e.preventDefault(); moved = false; held = false; } }, true);
    li.addEventListener("contextmenu", (e) => e.preventDefault());
  });
}
