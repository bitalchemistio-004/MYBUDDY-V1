// One pulse language for the whole app, so the same thing always feels the same: a light tick for a touch that registered, a double
// for something done, a rising pulse for a win or a level, a heavy single for a loss, a short triple for a warning.
// Respects "reduce motion" (no pulses) and does nothing where the phone has no vibrator.
const PATTERNS = {
  tick: [10],
  ok: [14, 30, 14],
  win: [20, 40, 30],
  level: [20, 40, 20, 40, 60],
  loss: [70],
  warn: [24, 40, 24, 40, 24],
};

/** The pattern for [kind] (a list of ms), or null. */
export const patternOf = (kind) => PATTERNS[kind] || null;

let enabled = true;
try { enabled = !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch { /* keep it on */ }
export function setHaptics(on) { enabled = !!on; }

export function buzz(kind = "tick") {
  const p = patternOf(kind);
  if (!enabled || !p) return;
  try { if (typeof navigator !== "undefined" && navigator.vibrate) navigator.vibrate(p); } catch { /* not everywhere */ }
}
