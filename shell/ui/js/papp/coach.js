// The buddy as a coach: it comments while you play. What it says depends on what really happened (a good move, a bad one, a close
// call, a win, a loss, a level up) and on which buddy you chose, so each one sounds like itself. Nothing is invented: every line is
// triggered by a real event in the game.

const LINES = {
  blink: {
    start: ["Ready when you are.", "Let's go. I'm watching.", "Okay, show me what you've got."],
    good: ["Nice one!", "Yes! Just like that.", "Smooth."],
    bad: ["Oops. Shake it off.", "Ah, that one stung.", "Not that one. Next!"],
    near: ["So close!", "Almost! One more.", "You're nearly there."],
    streak: ["You're on a roll!", "Again! Don't stop now.", "That's the rhythm."],
    win: ["You did it! I knew you could.", "Won! That felt great.", "Victory. Wow."],
    lose: ["Next time. I'll be here.", "Lost this one. Again?", "It happens. Go again."],
    level: ["Level up! It gets harder now.", "On to the next level.", "New level, new challenge."],
  },
  void: {
    start: ["Begin.", "The board is quiet. Make it loud.", "I'll be watching from the dark."],
    good: ["Acceptable.", "Precise.", "Good. Quietly good."],
    bad: ["A mistake. Even stars collapse.", "That cost you.", "Noted."],
    near: ["The edge is close.", "One step left in the dark.", "Near."],
    streak: ["Momentum is gravity.", "You keep landing it.", "The pull is yours."],
    win: ["Victory. Absorbed.", "You've won. Nothing escapes you.", "Done. Cleanly."],
    lose: ["Defeat. It feeds the next win.", "Gone. Again.", "Lost. Return."],
    level: ["The depth increases.", "Deeper now.", "A harder level opens."],
  },
  glitch: {
    start: ["BOOT OK. ...mostly.", "Starting. No promises.", "Let's break something."],
    good: ["HIT. Beautiful.", "Zzt! Nice.", "Clean. Suspiciously clean."],
    bad: ["ERR. That didn't work.", "Wrong tile. Classic.", "Bzzt. Nope."],
    near: ["Almost compiled.", "99%. Don't crash now.", "So close it hurts."],
    streak: ["Combo!! Don't glitch.", "Chain going.", "Stack overflow of wins."],
    win: ["YOU WIN. Rebooting joy.", "Success. 0 errors. Some.", "Won! Screenshot that."],
    lose: ["GAME OVER. Reboot?", "Crash. Restart.", "Fatal. Try again."],
    level: ["LEVEL UP. Patch applied.", "Next level loaded.", "Harder build unlocked."],
  },
  cinder: {
    start: ["Light it up.", "Let's burn this one down.", "Fire's ready."],
    good: ["Hot! That's it.", "Burning bright.", "Scorched it."],
    bad: ["Ash. Try again.", "That fizzled.", "Cold hit. Heat up."],
    near: ["Feel the heat. Nearly there!", "One spark left.", "It's right there."],
    streak: ["On fire!", "Blazing streak.", "Keep it burning."],
    win: ["Victory burns brightest!", "You torched it!", "Won. Glorious."],
    lose: ["Burned out. Relight.", "Lost. Fan the flame.", "Down. Not out."],
    level: ["Hotter level ahead.", "Level up. It's blazing now.", "The fire grows."],
  },
  oracle: {
    start: ["I see how this will go. Begin.", "Let us see what the pattern holds.", "Focus. I'm watching."],
    good: ["As foreseen.", "Wise move.", "You read it well."],
    bad: ["I saw that one coming.", "A misstep. Learn from it.", "The pattern slipped."],
    near: ["The end is in sight.", "Close. The vision is clear.", "One more truth to find."],
    streak: ["You're in the flow.", "Your pattern is strong.", "Each move proves it."],
    win: ["As written. You won.", "The outcome was always yours.", "Victory. Well read."],
    lose: ["This loss is a lesson.", "Defeated. Next time I'll show you the way.", "It was not yet your hour."],
    level: ["The next level reveals itself.", "A harder vision opens.", "Level up. The path narrows."],
  },
};

let last = "";
/** One line for [event] from [character]; [extra] (a short hint from the game) is added after it. Never the same line twice in a row. */
export function coach(character, event, extra = "") {
  const set = LINES[character] || LINES.blink, pool = set[event] || set.good;
  let line = pool[Math.floor(Math.random() * pool.length)];
  if (pool.length > 1 && line === last) line = pool[(pool.indexOf(line) + 1) % pool.length];
  last = line;
  return extra ? `${line} ${extra}` : line;
}
export const EVENTS = Object.keys(LINES.blink);
export const CHARACTERS = Object.keys(LINES);
