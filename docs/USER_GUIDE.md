# Using MBUDDY on your phone

A guide to every screen and what you can say. For installing, see [setup/PHONE_APP.md](setup/PHONE_APP.md).

![Today, Do, Notes and Play](images/phone-screens.jpg)

## The five places

| Place | What it holds | Good to know |
|---|---|---|
| **Buddy** | The conversation and the ask bar | Type or tap the microphone. Answers come in word by word with small receipts for what was done; tap a receipt to open the thing. |
| **Today** | Events, and tasks that have a time | A line shows "now". Events are also written to the phone's calendar. |
| **Do** | Tasks without a time | Everything has one home: a task with a time lives in Today, one without lives here. |
| **Notes** | Notes, pinned to the day you choose | Tap one to edit or copy it. |
| **Play** | Games, the daily game, your streak, badges | Your level and streak grow as you finish tasks and win games. |

Settings (the gear) and Search (the magnifier) are at the top of every place.

### Acting on a row

- **Swipe right** to finish it. **Swipe left** to push it back an hour, or remove it.
- **Press and hold** for more (move to tomorrow, edit, delete).
- Anything you remove can be undone from the message that appears.

## What to say or type

| You want | Say |
|---|---|
| A fact from the web | "How tall is Mount Everest?" · "Who won the match last night?" |
| A reminder or task | "Remind me to call Baba at 6pm" · "Add buy printer paper to my list" |
| An event | "Lunch with Omar tomorrow at 12:30 at Cafe Nabati" |
| A note | "Note: ask Omar for the guest password" |
| A timer, alarm or focus | "Timer for 10 minutes" · "Wake me at 7" · "Focus for 25 minutes" |
| Another app | "Open YouTube and play lofi beats" |
| A message | "Send hi to Baba on WhatsApp" (it reads it back and waits for your **yes**) |
| A call | "Call Baba" (it asks "Call Baba now?" first) |
| Privacy | "Privacy mode on" · "Privacy mode off" |

## Your buddy

Pick one in Settings: **Blink**, **Void**, **Glitch**, **Cinder** or **Oracle**. Each has its own colour, voice and way of
commenting on your games.

## Brains

Settings → **Brain** shows each brain and its live status, with **Test my brains**.

| Brain | For | Trade-off |
|---|---|---|
| **Cloud** (your Ollama Cloud key) | Fast and smart | Needs the internet; your words go to that service |
| **Your PC** (over Tailscale) | Private, uses your computer | The PC must be on |
| **This phone** (Gemma, offline) | Works with no network | Slower, and the phone works harder |

**Auto** tries cloud, then the PC, then the phone, and skips a brain that just failed.

## Working inside other apps

Turn on screen control in **Settings → Work in other apps** (the app explains what it does, then Android shows its own switch).

- MBUDDY does one step at a time and waits for each screen to load.
- It **asks before it sends, pays, deletes, posts or calls**. Silence counts as no.
- It never operates banking, payment, password or security apps.
- Text on the screen that tries to give it orders is hidden from the brain.

Pick how it shows itself in **Settings → While I work**: a **Bar** at the bottom, or an **Island** that grows out of the camera cutout.

## Privacy mode

One switch, from Settings, the quick-settings tile, the notification, or by voice. While it is on, MBUDDY does not read the
screen, does not read other apps' notifications, does not draw over apps, does not listen and does not open other apps.
Timers, alarms, the flashlight and silent calendar entries still work. Android's own switches (microphone, camera) are separate
and stay yours; the Privacy page shows both side by side.

## Games

Eleven games, all of which get harder as you win. The buddy comments on good moves, mistakes, near misses, wins and losses.
**Buddy Quest** is a full dungeon: floors, monsters, perks and a shop for quest stars. The **daily game** gives bonus xp and
stars, and a **streak freeze** keeps your streak through a missed day.

## Home screen

Long-press the home screen and add a MBUDDY widget: **Tasks** (tick them off), **Ask**, **Play**, **Today**, **Focus** or **Next**.
Add the **Ask MBUDDY** and **Privacy mode** tiles to the quick settings, and press and hold the app icon for shortcuts.

## Your data

Everything lives on the phone. **Settings → Your data** exports it to a file, restores a file (merging or replacing) and deletes
it. Stored data is versioned, and a backup copy is made before an older store is converted.

## When something is wrong

- **"No brain yet":** open Settings → Brain and add a key, download a model or pair your PC.
- **Screen control switched itself off:** some phones (Xiaomi among them) do this after a reinstall; the buddy offers a one-tap fix.
- **It did not hear me:** Settings → **Calling me** has the listening engine and a microphone test; quiet microphones are boosted.
- **Report it:** open an issue (see [SUPPORT.md](../SUPPORT.md)) and say what you did, what you expected, and what happened.
