// The chosen character, shown inside the app's own pages: a resting picture
// (pickers, the sidebar) or the live one that follows what MBUDDY is doing
// (the home page). The same drawn art the floating buddy plays.
import { createSpriteBuddy, castFor, stillUrl, reducedMotionWanted } from "./sprite.js";
import { applyReaction } from "./state.js";

const FILL = "display:block;width:100%;height:100%;object-fit:contain;";
const live = new Map();  // element -> { character, buddy, reaction, wanted }

// A page that was redrawn leaves its old element behind; its character must
// stop playing, or every visit to the page would leave one more loop running.
function sweep() {
  for (const el of [...live.keys()]) if (!el.isConnected) unmount(el);
}

// One resting frame. Cheap: a small image, nothing runs.
export function mountStill(el, character) {
  sweep();
  if (!el) return;
  const name = castFor(character);
  if (el.dataset.buddy === `still:${name}`) return;
  unmount(el);
  el.dataset.buddy = `still:${name}`;
  el.innerHTML = `<img alt="" draggable="false" style="${FILL}" src="${stillUrl(name)}">`;
}

// The animated character, following the status reaction. Calling it again
// with a new reaction keeps the same canvas; a new character replaces it.
export function mountLive(el, character, reaction, settings = null) {
  sweep();
  if (!el) return;
  const name = castFor(character);
  let entry = live.get(el);
  if (!entry || entry.character !== name) {
    unmount(el);
    mountStill(el, name);  // something to look at while the clips load
    entry = { character: name, buddy: null, reaction: null, wanted: reaction };
    live.set(el, entry);
    const mine = entry;
    createSpriteBuddy({ character: name, reducedMotion: reducedMotionWanted(settings) }).then((buddy) => {
      if (live.get(el) !== mine || !el.isConnected) return buddy.destroy();  // replaced or removed meanwhile
      mine.buddy = buddy;
      el.dataset.buddy = `live:${name}`;
      el.innerHTML = `<canvas aria-label="MBUDDY" style="${FILL}"></canvas>`;
      buddy.attach(el.firstElementChild);
      follow(mine, mine.wanted);
    }).catch((e) => console.error("the character did not load", e));
  }
  entry.wanted = reaction;
  if (entry.buddy) follow(entry, reaction);
}

function follow(entry, reaction) {
  applyReaction(entry.buddy.state, reaction, entry.reaction);
  entry.reaction = reaction;
}

export function unmount(el) {
  const entry = live.get(el);
  if (entry && entry.buddy) entry.buddy.destroy();
  live.delete(el);
  delete el.dataset.buddy;
}
