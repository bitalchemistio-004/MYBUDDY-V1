# The PC app (Tauri) and the shared page

| Folder | What it is |
|---|---|
| [`src-tauri/`](src-tauri) | The Windows app's shell (Rust, Tauri): the window, the floating buddy, the tray |
| [`ui/`](ui) | **The page.** HTML, CSS and JavaScript used by *both* the PC app and the phone app |

The page is shared on purpose: the Python backend serves it to the PC app, and the Android build copies it into the phone app
(`copyWeb` in `android/app/build.gradle.kts`). A change in `ui/` reaches both, so run the page tests before you commit:

```bash
node --test shell/ui/tests/*.test.mjs
```

The phone app's screens are in `ui/js/papp/`; the PC app's spaces are in `ui/js/views/` and `ui/js/spaces.js`.
Build and run the PC app (needs Node and Rust):

```bash
cd shell
npm install
npm run dev
```
