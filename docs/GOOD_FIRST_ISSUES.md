# Good first issues

Real, small, well-scoped places to start. Each one says what to change, where, and how you will know it works. Pick one, say so
in an issue so two people do not do the same work, and ask anything: someone will help.

The maintainer can paste each of these as an issue labelled `good first issue`.

## Easy (an afternoon)

1. **Add a few more words to Word shuffle.** `WORDS` in `shell/ui/js/papp/games.js`. Keep them 4 to 9 letters. A test already
   checks every word shuffles into a different order.
2. **A new tip.** One-time hints are in `shell/ui/js/papp/tips.js`. Add a hint where a feature is easy to miss, and use `tipHtml`.
3. **A new monster for Buddy Quest.** `MONSTERS` in `shell/ui/js/papp/quest.js` and a 12-by-10 dot picture in `pixart.js`.
   The quest tests check each room is harder than the last.
4. **Fix a typo or unclear sentence** anywhere in `docs/`. Read a guide start to finish as a newcomer and fix what trips you.
5. **Translate the first-run card** (the "Give me a brain" screen in `shell/ui/js/papp/views/buddy.js`) to your language, as a
   proposal in an issue first: it will become the start of a proper strings file.

## Medium (a weekend)

6. **Native tests for the brain order.** `Brains.order` in `android/.../Brains.kt` decides cloud, PC, phone. Make it testable
   without a phone and add the cases to `RulesTest.kt`.
7. **Scheduled automatic export.** Today export is manual (`Settings, Your data`). Add a weekly export to the Downloads folder.
8. **A real TalkBack pass.** Turn on TalkBack and go through the five places and the games. Write down what is not read out or
   not reachable, and fix the first three.
9. **More widget sizes and a preview.** The six widgets in `NextWidget.kt` use fixed layouts; add a compact 2x1 for Ask and Tasks.
10. **A new buddy character.** The cast is in `shell/ui/js/buddy/sprite.js` with art under `shell/ui/assets/buddy/chars/`. Follow
    the existing frame sets; the voice profile is in `Voice.kt` and the colour in `Overlay.kt`.

## Bigger (talk first)

11. **One source for the brain's rules.** The quick rules and tool list exist twice (`phone-actions.js` and `QuickRules.kt`,
    `runTool` and `NativeBrain.deviceAction`). Design a shared description, then migrate one tool at a time.
12. **Split the bridge.** `Bridge.kt` holds about 95 methods. Group them by area, with one place that checks the call came from the
    bundled page.
13. **A first run that needs no key.** A small bundled model or an offline answer set so the very first question works.
14. **The desk device on the new brain.** Bring the firmware's face states in line with the phone's buddy states.

## How to start

```bash
git clone https://github.com/bitalchemistio-004/MYBUDDY-V1.git
cd MYBUDDY-V1
node --test shell/ui/tests/*.test.mjs          # should be all green
```

Then read [CONTRIBUTING.md](../CONTRIBUTING.md), make a branch, and open a pull request. A draft pull request is welcome: it is a
good way to ask for help early.
