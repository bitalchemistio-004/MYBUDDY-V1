## What this changes and why

<!-- One or two sentences. Link the issue it closes: "Closes #123". -->

## Which part

- [ ] Phone app (`android/`, `shell/ui`)
- [ ] PC app / backend
- [ ] Desk device (`firmware/`, `hardware/`)
- [ ] Docs / project files

## How I checked it

<!-- Tests you ran, and what you saw on a real device. If you could not try it on a device, say so. -->

- [ ] `node --test shell/ui/tests/*.test.mjs`
- [ ] `cd android && ./gradlew :app:testDebugUnitTest :app:assembleDebug` (if you touched Android)
- [ ] Tried it on a real phone / PC / device (say which)

## Ground rules

- [ ] Nothing I added is a mock-up: it is wired to real state
- [ ] Anything irreversible still asks for a yes first
- [ ] Nothing private leaves the device that did not before; no keys or personal data in the change
- [ ] If I touched what MBUDDY may do (refused apps, yes and no, privacy mode), I added or updated a test in `RulesTest.kt`
- [ ] I updated the docs and the changelog if a person using it would notice

By opening this pull request I agree that my contribution may be distributed under the project's licence (GPL-3.0).
