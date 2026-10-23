# Getting started

What you need to download, how much space it takes, and the steps, for each part of MBUDDY. You only need
the part you want to work on.

## What it takes

| Part | You need | Download | On disk |
|---|---|---|---|
| The code (`git clone`) | Git | ~85 MB | ~0.3 MB working files + history |
| PC brain (Python) | Windows 10/11, Python 3.10, ideally an NVIDIA GPU (see below) | ~3 GB of Python packages | ~4 GB (`.venv`, 2.4 GB of it CUDA) |
| Local AI model | [Ollama](https://ollama.com) + `qwen3:4b-instruct` | ~2.5 GB | ~2.5 GB (in Ollama's folder) |
| Speech in (Whisper small) | nothing: it downloads itself the first time MBUDDY listens | ~460 MB | ~460 MB |
| Voice out (Kokoro) | two files, see step 4 | ~330 MB | ~330 MB (`models/kokoro`) |
| PC app (Tauri window) | Node 18+, Rust (stable) | Rust ~1 GB, crates ~0.5 GB | ~2.4 GB build folder |
| Phone app | JDK 17, Android SDK | SDK 3-5 GB | ~0.7 GB build folder |
| Desk device (ESP32) | PlatformIO | ~2.2 GB toolchains | ~2.6 GB |
| Page tests only | Node 18+ | none | none |

**On macOS or Linux?** The PC brain and window are built for Windows (they drive Windows apps and windows). You can
work on the pages (Node only), the phone app (JDK and Android SDK) and the desk device (PlatformIO) on any system. A
`setup.sh` for the brain is a welcome first contribution ([good first issue 19](GOOD_FIRST_ISSUES.md)).

A full PC setup (brain, models, app) is about **10 GB** on disk. The Ollama Cloud key (Settings, Brain) lets
MBUDDY use a large model online; the local model still runs the decisions.

**GPU:** `requirements.txt` installs the CUDA builds (`onnxruntime-gpu`, `nvidia-*`) so speech runs on an NVIDIA
GPU. Without one, set `STT_DEVICE=cpu` and `TTS_DEVICE=cpu` in `.env`: it works, more slowly. A lighter CPU-only
install (without the 2.4 GB of CUDA packages) isn't set up yet; help with that is welcome.

## Steps (PC)

**One command:** `setup.bat` does steps 2 to 5 below and checks the result (options: `-Cpu`, `-SkipModel`,
`-WithApp`). Close MBUDDY before running it. The steps are listed here so you know what it does, or can do them by hand.

1. **Clone**
   ```bash
   git clone https://github.com/bitalchemistio-004/MYBUDDY-V1.git
   cd MYBUDDY-V1
   ```
2. **Python brain**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   ```
   Every value in `.env` is optional; keys can also be entered in the app (Settings, Brain).
3. **Local model**: install Ollama, then `ollama pull qwen3:4b-instruct` (or press Download on the app's Setup page,
   which checks every step below and says what is missing).
4. **Voice**: download `kokoro-v1.0.onnx` and `voices-v1.0.bin` from the
   [kokoro-onnx model release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0) into
   `models/kokoro/`.
5. **Run the brain**: `python main.py` (the server is on http://127.0.0.1:5000). `python check_system.py` says
   what is still missing, without changing anything.
6. **The window** (optional): `cd shell`, `npm install`, `npm run dev`.
7. **Your name**: type it in the app (Settings). Nobody's name is built in; until you set one, MBUDDY says "the user".
8. **Learning memory** (optional): in the app, Settings, General, Learning memory; see [Learning memory](#learning-memory-obsidian-optional) below.

## Learning memory (Obsidian, optional)

MBUDDY keeps what it **learns** (lessons from mistakes, skills that worked, notes about apps and topics) as Markdown notes
in an [Obsidian](https://obsidian.md) vault. Your facts, reminders and conversations are stored separately, in MBUDDY's own
database, and don't need a vault.

- **Optional, but recommended.** Without a vault everything else works (chat, voice, reminders, focus, music, apps),
  but MBUDDY doesn't learn and the Brain page's "What I learned" stays empty.
- **Connecting it:** in the app, **Settings, General, Learning memory** (or **Brain, Learned** while nothing is
  linked). Choose **Use my Obsidian vault** (the vault Obsidian has open, found by itself), **Create a new vault**
  (a folder called "MBUDDY Vault" in Documents), or type the path of any folder. MBUDDY writes its notes in an `MBUDDY`
  folder inside it. **Unlink** stops learning and leaves the notes where they are.
- **Obsidian itself is optional:** a vault is just a folder of Markdown files. Install Obsidian (free) to read, edit or
  delete what MBUDDY learned; it respects your edits.
- **Privacy:** the vault stays on your PC and outside git. Tests never write to it.

## Before you open a pull request

```bash
python -m pytest -q                              # the PC brain and server
node --test shell/ui/tests/*.test.mjs            # the pages
cd android && ./gradlew :app:testDebugUnitTest   # the phone app's safety rules
```
Tests never touch your own memory, notes, tasks or settings: `tests/conftest.py` gives each test its own
temporary copies. A few tests need the GPU speech models; they skip or fail without them, and CI runs the page
and Android tests only.

Your data (memory, notes, settings, keys) lives in `memory/`, `~/.buddy/` and your Obsidian vault, all outside
git (see `.gitignore`). Never commit `.env`.

## Freeing space

Build folders grow; they rebuild themselves when deleted: `shell/src-tauri/target` (`cargo clean`),
`android/app/build`, `firmware/**/.pio`.
