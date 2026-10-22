// Window-level motion for the drawn buddy: drag the whole window, a small
// lag while it moves, a squash when it lands. These are CSS transforms on the
// container only; the frame art itself is never redrawn or deformed here.

const DRAG_THRESHOLD = 4;  // px of movement before a press becomes a drag
const SETTLE_MS = 160;     // no move events for this long means it was dropped
const MAX_LAG = 6;         // px the sprite trails behind the window

// Press and release without moving is a click; moving past the threshold
// hands the drag to the OS (Tauri startDragging), which moves the window.
export function attachDrag(el, { win, onClick, onDrop, reduced = () => false }) {
  let press = null, dragged = false;
  el.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    press = { x: e.clientX, y: e.clientY };
    dragged = false;
  });
  el.addEventListener("mousemove", (e) => {
    if (!press || dragged) return;
    if (Math.hypot(e.clientX - press.x, e.clientY - press.y) < DRAG_THRESHOLD) return;
    dragged = true;
    press = null;
    if (win) win.startDragging().catch(() => {});
  });
  el.addEventListener("mouseup", () => { press = null; });
  el.addEventListener("click", (e) => {
    if (dragged) { dragged = false; e.stopImmediatePropagation(); return; }  // the end of a drag is not a click
    if (onClick) onClick(e);
  });
  if (win && win.onMoved) followMoves(el, win, { onDrop, reduced });
}

// While the window moves the sprite trails a little behind it and springs
// back (the CSS transition does the spring); when moves stop, it lands with
// a squash.
function followMoves(el, win, { onDrop, reduced }) {
  let prev = null, timer = 0;
  const wrap = el.querySelector(".sprite-wrap") || el;
  win.onMoved(({ payload }) => {
    const scale = globalThis.devicePixelRatio || 1;
    if (prev && !reduced()) {
      const lag = (d) => Math.max(-MAX_LAG, Math.min(MAX_LAG, -d / scale * 0.3)).toFixed(1);
      wrap.style.translate = `${lag(payload.x - prev.x)}px ${lag(payload.y - prev.y)}px`;
    }
    prev = payload;
    clearTimeout(timer);
    timer = setTimeout(() => {
      prev = null;
      wrap.style.translate = "";
      if (!reduced()) squash(wrap);
      if (onDrop) onDrop();
    }, SETTLE_MS);
  });
}

export function squash(wrap) {
  wrap.classList.remove("drop");
  void wrap.offsetWidth;  // restart the animation if it is still running
  wrap.classList.add("drop");
  wrap.addEventListener("animationend", () => wrap.classList.remove("drop"), { once: true });
}
