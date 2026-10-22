# MBUDDY for Android

The phone half of MBUDDY, standalone: its own screens (`shell/ui/phoneapp.html`, bundled into the app), its own data and its own
brain. The PC is optional. What it does natively:

- **Brains**: Ollama Cloud with your key, your PC's brain over Tailscale, or a model on the phone (Gemma 4 E2B or E4B with LiteRT-LM),
  chosen in one place (`Brains.kt`); Auto skips a brain that just failed.
- **Voice**: "hey buddy" (an offline Vosk wake word), the assistant button (`AssistService`), speech understood by the model on the phone.
- **Screen control** (`ControlService`, `Agent.kt`, `Planner.kt`): opens apps, types and taps, waits for the screen to settle, reads a
  message back and asks before anything is sent, paid, deleted, posted or called. Banking, payment and security apps are never operated.
- **Web answers** (`WebSearch.kt`): questions about the world are looked up and answered aloud.
- **The overlay** (`Overlay.kt`, `EdgeGlow.kt`, `shell/ui/agent.html`): a bar, or an island grown out of the camera cutout, with a light
  around the screen's edge.

## Build
Needs Android Studio (it brings the SDK and a JDK 17 or 21); or the SDK command-line tools and JDK 17.
1. Open the `android/` folder in Android Studio, let it sync, press Run (a phone with USB debugging, or an emulator).
2. Or from a terminal: `gradle :app:assembleDebug` (Gradle 8.9 or newer, `ANDROID_HOME` set), then install
   `app/build/outputs/apk/debug/app-debug.apk` on the phone.

The web files are copied from `../shell/ui` into the app at build time (see `copyWeb` in `app/build.gradle.kts`).

## Pair
On the PC: Away, New pairing code. In the app: the PC's private address (`https://<pc>.<tailnet>.ts.net`, from `tailscale serve --bg 5051`)
and the six-digit code. Or skip it and use the phone tools alone.

## Building and testing from the command line (no Android Studio)
Set up on this PC: JDK 17 (Temurin) in `<your-user-folder>\Android\jdk17`, the Android SDK (command-line tools, platform 35, build-tools 35.0.0,
platform-tools) in `<your-user-folder>\AppData\Local\Android\Sdk`; `JAVA_HOME`, `ANDROID_HOME`, `ANDROID_SDK_ROOT` and `PATH` are set for the user,
and `android/local.properties` (not committed) points at the SDK.

- Build: `gradle :app:assembleDebug` in `android/` gives `app/build/outputs/apk/debug/app-debug.apk`.
- Install on a phone with USB debugging on: `adb install -r app/build/outputs/apk/debug/app-debug.apk`, then open it with
  `adb shell am start -n app.mbuddy.mobile/.SetupActivity` (MainActivity is not exported on purpose).
- Test against the PC without Tailscale: start MBUDDY on the PC, get a code (Away, New pairing code, or POST `/ui/remote` `{"action":"new_code"}`),
  run `adb reverse tcp:5051 tcp:5051`, and pair with the address `http://127.0.0.1:5051`. Remove the tunnel (`adb reverse --remove-all`) afterwards;
  a connection the app already has open survives removing it, so restart the app to see a real outage.
- Debug builds let the page be inspected: `adb forward tcp:9222 localabstract:webview_devtools_remote_<pid of app.mbuddy.mobile>`, then
  talk to `http://127.0.0.1:9222/json` (DevTools protocol) to run script in the page.
- Android intent links: a true/false extra is `B.`, a byte is `b.` (a lowercase b made alarms and timers fail).
