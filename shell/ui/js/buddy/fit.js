// How large the buddy's window must be: exactly what is drawn, plus a small
// pad (the character's shadow and the effects around it). Nothing else: a
// transparent window larger than its content covers other apps for no reason.

export const PAD = 14;  // px; buddy-sprite.css uses the same value as --pad

// The size that holds every box, measured from the corner the character
// stands in (the window's anchor): bottom-right for a buddy on the right,
// bottom-left for one on the left. `boxes` are layout boxes in the current
// viewport; because everything is laid out from that corner, the result does
// not depend on how large the window is at the moment.
export function sizeFor(boxes, side, viewport, pad = PAD) {
  let w = 0, h = 0;
  for (const b of boxes) {
    if (!b || b.width <= 0 || b.height <= 0) continue;
    const across = side === "left" ? b.left + b.width : viewport.width - b.left;
    w = Math.max(w, across);
    h = Math.max(h, viewport.height - b.top);
  }
  return { w: Math.ceil(w + pad), h: Math.ceil(h + pad) };
}

// Layout boxes (offset*, which ignore the extend/retract transforms) of the
// character and of whatever is showing beside it.
// The notch's window is always as large as its largest shape (the room, with room to spare for a question
// with its answers), so opening and closing only animate the shape inside it and the window never moves or
// resizes in the middle of an animation. Clicks pass through everywhere except the shape (buddy_hot).
export const NOTCH_WINDOW = { w: 640 + 2 * PAD, h: 360 };

export function contentSize(root, side, pad = PAD) {
  // The notch hangs from the top edge, centred. Larger than the fixed window only if its content ever is.
  if (side === "top") {
    const n = root.querySelector("#shell"), top = root.querySelector("#notch").offsetTop;
    return { w: Math.max(NOTCH_WINDOW.w, Math.ceil(n.offsetWidth + 2 * pad)), h: Math.max(NOTCH_WINDOW.h, Math.ceil(top + n.offsetHeight + pad)) };
  }
  const stage = root.querySelector("#stage"), aside = root.querySelector("#side");
  const box = (el) => ({ left: el.offsetLeft, top: el.offsetTop, width: el.offsetWidth, height: el.offsetHeight });
  const boxes = [box(stage)];
  if ([...aside.children].some((el) => !el.hidden)) boxes.push(box(aside));
  return sizeFor(boxes, side, { width: root.clientWidth, height: root.clientHeight }, pad);
}
