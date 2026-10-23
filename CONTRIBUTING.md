# Contributing to MBUDDY

Thank you for wanting to help. MBUDDY is an assistant with three bodies: a PC app (a Python brain and a Tauri window), an
Android app, and a small ESP32 desk device. You don't need to know all of it: pick one part.

## Set up in one command (PC, Windows)

```bat
git clone https://github.com/bitalchemistio-004/MYBUDDY-V1.git
cd MYBUDDY-V1
setup.bat
```

`setup.bat` makes the Python environment, installs the packages, creates `.env`, downloads the voice files, pulls the local
AI model when [Ollama](https://ollama.com) is installed, and checks the result. It is safe to run again. Options:
`-Cpu` (no NVIDIA GPU), `-SkipModel` (skip the 2.5 GB model), `-WithApp` (also the desktop window; needs Node and Rust).
Then run `run.bat` and set your name in the app's Settings. Optional: open a vault in [Obsidian](https://obsidian.md) so MBUDDY
can learn (see [Learning memory](docs/GETTING_STARTED.md#learning-memory-obsidian-optional)).

Sizes, requirements and manual steps for every part are in [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md). Working only
on the pages? You need nothing but Node: `node --test shell/ui/tests/*.test.mjs`.

## Your first pull request

1. **Find something small:** [good first issues](docs/GOOD_FIRST_ISSUES.md), or an issue labelled `good first issue`.
   Say in the issue that you are taking it.
2. **Fork** the repository on GitHub (the Fork button), clone your fork, and make a branch: `git checkout -b fix-timer-label`.
3. **Change it, and test it:**
   ```bash
   python -m pytest -q                              # the PC brain and server
   node --test shell/ui/tests/*.test.mjs            # the pages
   cd android && ./gradlew :app:testDebugUnitTest   # the phone app's safety rules
   ```
4. **Push** your branch and open a **pull request**. Say what you changed, why, and what you checked by running it.
5. A maintainer reviews it; CI runs the page and Android tests on every pull request.

## Where things are

| You want to change | Look in |
|---|---|
| What the PC brain decides and does | `brain/agent.py` (tools), `brain/llm_router.py` (replies), `brain/connectors/` (abilities) |
| Memory, facts, preferences | `memory/`, `brain/memory_context.py` |
| Working inside apps (Blender, Office...) | `brain/connectors/app_driver.py`, `brain/connectors/office.py`, `brain/verification.py` |
| Speech in and out | `stt/`, `tts/` |
| The server and its routes | `api/`, `main.py` |
| The desktop window and the buddy | `shell/ui/` (pages), `shell/src-tauri/` (Rust shell) |
| The phone app | `android/` and its pages in `shell/ui/js/papp/` |
| What the phone may and may not operate | `Risk` in `Screen.kt` (tests in `RulesTest.kt`) |
| The desk device | `firmware/`, `hardware/`, [docs/HARDWARE.md](docs/HARDWARE.md) |

[docs/README.md](docs/README.md) lists the architecture and API docs.

## The ground rules

1. **Only real features.** Every control is wired to real state, or it is not there. No mock-ups, no fake data.
2. **Ask before anything that can't be undone.** Sending, paying, deleting, closing and calling need a yes, every time.
3. **Private by default.** Nothing leaves the device unless the user turned it on; secrets are never logged.
4. **Say what you couldn't check.** "Done" means you saw it work, not that it compiled.
5. **No user's name in the code.** Prompts say `USER_NAME`; the app fills in the name from Settings.
6. **Tests never touch real data.** `tests/conftest.py` gives every test its own memory, notes and tasks; keep it that way.
7. **No emojis in the interface.**

## Making a change

- Keep it small, and say *why* in a comment, not only what.
- Add a test for logic you add. Anything that protects the user (what counts as a yes, what is refused) gets a test.
- Never commit `.env`, keys, or your own memory and notes (they are in `.gitignore`).

## Reporting a problem

Open an issue with what you did, what you expected and what happened. For a security problem, **don't open a public issue**:
see [SECURITY.md](SECURITY.md).

## Licence of your contribution

MBUDDY is licensed under the [GNU GPL v3.0](LICENSE). By sending a contribution you agree it is distributed under the same
licence, and you keep your copyright. Only send work you wrote or have the right to share under GPL-3.0.

## Behaviour

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). How decisions are made is in
[GOVERNANCE.md](GOVERNANCE.md). Questions: [SUPPORT.md](SUPPORT.md).
