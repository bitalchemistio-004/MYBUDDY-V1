# Tests

| Where | What it covers | Run |
|---|---|---|
| `tests/*.py` | The PC backend: brain, server, memory, tools | `python -m pytest -q` |
| `tests/js/` | The PC app's page (states, cards, bubble) | `node --test tests/js` |
| `shell/ui/tests/` | The phone app's data, rewards, games, quick add, restore, and a parse check of every page script | `node --test shell/ui/tests/*.test.mjs` |
| `android/app/src/test/` | The rules that must never quietly change on the phone: refused apps, yes and no, privacy mode, private addresses | `cd android && ./gradlew :app:testDebugUnitTest` |
| `android/tools/regress/` | The on-phone regression suite (real requests, every confirmation answered "no") | see [`android/README.md`](../android/README.md) |

Continuous integration runs the page tests and the Android unit tests on every push (`.github/workflows/ci.yml`).
