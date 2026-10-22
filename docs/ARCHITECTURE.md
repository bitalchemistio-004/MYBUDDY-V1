# How the phone app is put together

MBUDDY on Android is two programs that talk to each other: a **page** (HTML, CSS and JavaScript, drawn in a WebView) and the **app**
(Kotlin). The page is what the owner sees and what holds their data. The app is what can do things the page cannot: act while the
screen is closed, set alarms, talk to the phone's other apps, show cards over other apps, and run the on-device model.

```
 +------------------------------+        Native (94 methods, Bridge.kt)         +--------------------------------+
 |  The page   shell/ui         | <-------------------------------------------> |  The app   android/            |
 |  phoneapp.html, js/papp/*    |        window.__nativeHttp(...), events        |  Kotlin, services, receivers   |
 |  data: localStorage "mbp_*"  |                                                |  secrets: encrypted preferences |
 +------------------------------+                                                +--------------------------------+
            ^                                                                                  |
            |   Inbox: things done while the page was closed (done, snooze, focus, go, task...)  |
            +----------------------------------------------------------------------------------+
```

## Who owns which data

| Data | Owner | Copy elsewhere |
|---|---|---|
| Tasks, events, notes, conversation, rewards, games, quest, settings | The page (`store.js`, localStorage) | A short copy for the app: agenda (`setAgenda`), widgets (`setWidget`), recent talk (`setRecent`) |
| Cloud key, PC pairing token | The app (encrypted `Store`) | Never given to the page |
| Which brain, which model, the buddy | Page settings | Mirrored to the app by `syncToApp()` so voice works with the page closed |

The page writes its copies to the app whenever something changes (debounced, `brain.syncToApp()`); the app never edits the page's
data. When the app needs the page to change something (a widget tick, a notification button, a voice command heard while the page
was closed) it puts an entry in the **Inbox** and the page applies it the next time it is open (`brain.importInbox()`), so the xp
and streak are earned for real.

## The bridge

`Bridge.kt` exposes `Native` to the page. Every method first checks that the call came from the bundled page (`trusted()`), so a
page opened from anywhere else gets nothing. Roughly, by area:

| Area | Examples |
|---|---|
| Brains | `cloudChatAsync`, `saveCloudKeyAsync`, `localChatAsync`, `brainsState`, `setPlanner` |
| Mirrors | `setAgenda`, `setWidget`, `setRecent`, `setFrequent`, `setSystemPrompt` |
| Doing things | `act` (timer, alarm, call, navigate, calendar), `agentStatus`, `agentStop`, `openControlSettings` |
| Voice | `speak`, `voicesState`, `setBuddyVoice`, `previewVoice` |
| Smart behaviours | `handleState`, `setHandle`, `smartState`, `setSmart`, `triggersState`, `setPanelListen` |
| Data | `saveToDownloads`, `pickFile`, `wipeAll`, `drainInbox` |

## The brain, and where it is written twice

A request can arrive two ways, and each has its own implementation:

| Path | When | Page (`shell/ui/js/papp`) | App (`android/.../mobile`) |
|---|---|---|---|
| Typed in the app | The page is open | `brain.js` (`ask`, `think`, `byRules`, `runTool`) | - |
| Spoken, from the edge buddy, the tile, "hey buddy", the assistant button | The page may be closed | - | `Command.kt` then `NativeBrain.kt` and `Planner.kt` |

Both choose a brain in the same order (cloud, then your PC, then the phone's model: `Brains.order` on the app side, `brain.js` on
the page side) and both follow the same persona text (the page builds it and hands it to the app with `setSystemPrompt`). **What is
still written twice:** the quick rules (`phone-actions.js` `parseQuick` and `QuickRules.kt`), and the tool list and their
execution (`runTool` and `NativeBrain.deviceAction`). A change to one must be made in the other. Unifying them behind one shared
description of the tools is on the list (see the three-view audit).

## Things that run without the page

| Thing | Where | Started by |
|---|---|---|
| Heads-up cards, task reminders, morning briefing | `Smart.kt` | the phone's alarm clock |
| Message and car cards | `Triggers.kt` | a notification, a Bluetooth connection |
| Widgets and the quick-settings tile | `NextWidget.kt`, `AskTile.kt` | the launcher |
| Notification and widget taps | `WidgetActionReceiver` in `Widgets.kt` | the notification, the widget |
| The edge buddy, the card, the light around the screen | `EdgeHandle.kt`, `Overlay.kt`, `EdgeGlow.kt` | the accessibility service (`ControlService.kt`) |

## Safety in one place

What the buddy will not operate, what counts as a yes and a no, and which actions need a yes are in `Risk` (`Screen.kt`), and
`RulesTest.kt` pins them. Changing those rules without changing the tests should fail the build.
