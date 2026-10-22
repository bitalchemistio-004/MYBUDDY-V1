# Source

`SKILL.md` is copied unchanged from https://github.com/gonzchris/Clawdachi (`.claude/skills/pixel-art-design/SKILL.md`,
fetched 2026-10-16), MIT licence, Copyright (c) the Clawdachi authors.

It was written for a macOS Swift/SpriteKit app, so its code patterns (SKTexture, NSWindow, NSBezierPath, ParticlePool) do
not apply to MBUDDY. What carries over, and what MBUDDY already does with it:

- 6x nearest-neighbour scaling, no anti-aliasing: the room and the pixel decorations use 6- and 3-unit pixels with crisp edges.
- A small fixed palette, one dark outline: every pixel item uses four tones of one colour plus a black outline.
- Pop-in with overshoot (1.1x then settle, 0.15 s + 0.075 s) and 3 s breathing: the room's "build" animation and the idle motion.
- Float-up-and-fade particles drawn from a pool: the auras (embers, sparks, bubbles, notes).
- A quality checklist before any visual element is finished (palette, crisp pixels, outline, shadow offset, timing, caching).
