# Building the phone app

## What you need

- JDK 17 (Temurin is what the project is built with)
- The Android SDK with platform 35 and build-tools 35.0.0 (`ANDROID_HOME` set, or `android/local.properties` with `sdk.dir=...`)
- Node 20 for the page's tests
- A phone with USB debugging on, to install and look at it

The Gradle wrapper is in the repository: you do not need to install Gradle.

## Everyday commands

```bash
# page tests (no phone)
node --test shell/ui/tests/*.test.mjs

# Android unit tests (no phone)
cd android && ./gradlew :app:testDebugUnitTest

# debug build: android/app/build/outputs/apk/debug/app-debug.apk
cd android && ./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The page (`shell/ui`) is copied into the app at build time, so a change to the page needs a rebuild to reach the phone.

## Looking at the page while it runs on the phone (debug builds)

```bash
adb forward tcp:9222 localabstract:webview_devtools_remote_$(adb shell pidof app.mbuddy.mobile)
node android/tools/regress/cdp.mjs "document.title"
```

`android/tools/regress/regress.ps1` is the on-phone regression suite. It leaves a few test questions in the conversation and answers
every confirmation "no", so nothing is sent or called.

## A release build

```bash
cd android && ./gradlew :app:assembleRelease
```

The release build is shrunk (R8), arm64 only, and about 60 MB. **Signing:** if `android/keystore.properties` exists (it is not
committed) the release is signed with that key; otherwise it falls back to the debug key, which is only fine for installing on your
own phone. To make a key:

```bash
keytool -genkeypair -v -keystore android/mbuddy-release.jks -alias mbuddy -keyalg RSA -keysize 4096 -validity 10000
```

then create `android/keystore.properties`:

```
storeFile=mbuddy-release.jks
storePassword=...
keyAlias=mbuddy
keyPassword=...
```

Keep the `.jks` file and its passwords somewhere safe and **out of the repository**: losing it means you cannot update an app you
published with it.

## Version numbers

`versionCode` is the number of commits on the current branch, so every build is newer than the last; `versionName` is
`0.1.<that number>`.
