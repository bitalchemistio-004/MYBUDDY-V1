# Changelog

All notable changes, newest first. The phone app's version number rises with every build (`0.1.<number of commits>`).

## Unreleased

### PC app
- **Sprint 2 (checked work), from the field test:**
  - Blender results are checked against real sizes (they used to read every part as 1 x 1 x 1 m) and against what you asked ("four legs"), not only the plan. A radius is no longer read as a count; cameras, lights and floors are not "broken geometry"; a chair and a table are each checked on their own legs. A simple chair now passes on the first try.
  - "Render it to my folder" works: a file you ask for is written only into Documents\MBUDDY\<app>, and checked on disk before MBUDDY says it's there.
  - "Now add a table next to it" builds on what was made, and MBUDDY asks before closing the earlier window.
  - A job that gets stuck is stopped for real; nothing appears or is announced after "I've stopped it".
  - An app task can't gain an action you never asked for (closing windows, sending).
  - Your own bracket labels stay ("[S2] send the deck"), in replies, the voice, the app and the saved reminder.
  - "Friday at 3pm" is 15:00, not 09:00.
  - Preferences ("from now on, use bullet points") are kept, followed, and listed on the Memory page with Forget; a new name replaces the old one.
  - "Try again" only looks at this conversation, not one from hours ago.
  - "I'll go from my notes" now starts the work in a connected app instead of doing nothing.
  - "Documents\MBUDDY" is found when you name it; Office lock files aren't listed.
  - Tests no longer write your memory, vault, task list, timers or ledger (a test run had overwritten the memory snapshot; it was restored).
- **Sprint 1 (truth), from the field test:**
  - Asking the time is answered by the clock, also while the laptop charges (it used to invent "ten fifteen").
  - Reminders are once unless you say they repeat; "make it every day", "move it to 7pm" and "mark it done" change the existing item instead of adding another.
  - Weather answers the day you ask about (a real forecast up to 3 days ahead), at your home unless you name a place.
  - It can find what you said before ("what did I ask you on Oct 12?").
  - A slow reply is no longer lost: the app keeps waiting and shows it under the right question; a muted question stays muted.
  - A reply can't contradict what the tool just did (one-time vs every day, a wrong time or file name, a forecast made from the current weather).
  - Translations are delivered instead of "could you ask me that once more?".
  - Lessons from your corrections are written again (none had been since early October), only the notes that relate to an action gain or lose, and a change of mind is not treated as a correction.
- **Try before you earn:** click a locked decoration in Settings, Buddy to see it on your buddy, the notch and the app for a minute. Nothing is saved or unlocked; it ends by itself.
- **Wardrobes for all five buddies:** Void, Glitch, Cinder and Oracle each have their own 14 or 15 pixel decorations, unlocked by what you do, fitted to their own look. What each buddy wears is kept when you switch.
- **Character room:** each buddy has a pixel-art room (Studio, Room): nine places with five levels, a shop with pictures and reasons, three games, a month view of your real tasks, secrets and a pet, all built with reward points (your earned XP minus what you spend; games pay nothing).
- **App look:** skin, backdrop, cards, accent and header scene, unlocked like the rest.
- **Notch decoration:** the open notch is a plate with the character standing on a ledge, tools and the line to type beside it. Plate, frame, shape, ledge, status lamp and up to three pins unlock from what you do and are chosen in Settings, Buddy.
- **Compact notch:** the closed notch is now just the face and a mood dot, and widens only as far as a reply, a question or a job needs. The older Short, Standard and Long lengths are still in Settings, Buddy, Notch.
- **Wardrobe (Blink):** 36 decorations in six places, a look and an ink colour, unlocked by what you really did (level, tasks,
  focus sprints, habits, best streak). Chosen in Settings, Buddy, Wardrobe; drawn over the character in the notch and the app.
  Each item has its own colour and is drawn in pixel art by default (Line is still a choice); every decoration is fitted to the character by rules a test checks, so none leaves the picture or touches another.
  The pill's crop grows to take a crown. The other four characters follow with decorations drawn for their own look.

### Phone app
- **Privacy mode:** one switch (Settings, the quick-settings tile, or "privacy mode on") that stops MBUDDY looking at the screen,
  reading other apps' notifications, drawing over apps, listening, and opening other apps. It follows the real mode from every control.
- **Places:** Today is events and tasks with a time, Do is tasks without a time, Notes is notes. Everything has one home.
  *Remember* is now *Notes*.
- **Listening:** works on quiet microphones (the voice is raised before the speech engine hears it), the cue sounds when the
  engine is really ready, and a visible "Listening" appears when the tablet is muted.
- **First run:** a guided "give me a brain" card (cloud key, on-phone model or PC), screen control explained before Android asks,
  restore from an export file, one-time tips.
- **Games:** eleven games that get harder as you win, the buddy comments on how you play, and Buddy Quest is a full dungeon with
  perks and floors. A daily game, a weekly recap, streak freezes, a quest shop.
- **Widgets:** tasks you can tick, ask, play and streak, today, focus timer, and what is next. Launcher shortcuts and a quick tile.
- **Safety and quality:** failed saves are never silent, stored data is versioned with a backup copy, a local problems log,
  on-screen text that tries to instruct the buddy is hidden from the brain, native unit tests pin the refused-app list and
  what counts as a yes or a no.

### PC app
- **Awareness:** every turn is told the real moment: what is in front, today's time per app, the next event, tasks, timers, what is playing
  and the battery. It is read in the background, so it adds no wait. It can be switched off in Settings ("Know what I am doing").
- **Your day:** plan today around the calendar and tasks, a report of your time per app, one search across tasks, calendar, notes and apps,
  habits with streaks, a distraction list for focus sprints, and reminders that repeat every hour, day or week (and can be stopped).
- **Honest abilities:** what you switched off in Extensions is said as "switched off", not "I can't"; a recall can no longer back a claim
  that something was saved; "play the music" finds what was paused, even after a restart.
- **Speed:** your turn goes first at the cloud (housekeeping and jobs wait), a stuck window reconnects by itself, and boot loads the voice,
  memory search and hearing side by side with the cloud brain.
- **Notch:** idle status glyphs hide, seven tools with a More button, an icon Close, questions stack on their own lines, clicks work during
  the open animation, the music view lets go when the music stops, and a Text only face option.

### Project
- Open-source preparation: GPL-3.0 licence, contributing guide, security policy, code of conduct, governance, issue and pull
  request templates, continuous integration.
