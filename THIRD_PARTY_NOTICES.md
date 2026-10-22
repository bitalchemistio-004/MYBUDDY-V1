# Third-party software and assets

MBUDDY is licensed under the [GNU GPL v3.0](LICENSE). It uses the following projects, each under its own licence. This list is
kept by hand and is as accurate as we could make it: **check each project's own licence before you redistribute**, and tell us
if something here is wrong.

## What ships inside the phone app

| Component | Used for | Licence |
|---|---|---|
| AndroidX (core, appcompat, webkit, work, security-crypto) | The Android app's building blocks | Apache-2.0 |
| Kotlin standard library | The Android app | Apache-2.0 |
| LiteRT-LM (Google AI Edge) | Running a model on the phone | Apache-2.0 |
| Vosk (alphacephei) | Offline speech recognition and the wake word | Apache-2.0 |
| JNA | What Vosk's native part needs | Apache-2.0 or LGPL-2.1+ (dual) |
| Fonts: Doto, Space Grotesk, Space Mono, Baloo 2, Cinzel, Nunito | The look of the interface | SIL Open Font License 1.1 |

## What the PC backend uses (from `requirements.txt`)

| Component | Licence (as published) |
|---|---|
| faster-whisper, kokoro-onnx, onnxruntime, python-dotenv, aiohttp, numpy, scipy, Pillow, pycaw, comtypes, pywin32, webrtcvad-wheels, winrt, requests, beautifulsoup4 | MIT, BSD, Apache-2.0 or PSF |
| openwakeword, sentence-transformers, faiss-cpu | Apache-2.0 / MIT |
| edge-tts | LGPL-3.0 |
| NVIDIA CUDA runtime wheels (`nvidia-*`) | NVIDIA's own licence; installed by pip, not redistributed here |
| Optional cloud client libraries (groq, openai, anthropic, google-genai) | Apache-2.0 / MIT |

All of these are compatible with distributing MBUDDY under GPL-3.0 as a combined work.

## Models

MBUDDY does **not** include language, speech or vision model files in this repository. They are downloaded by the user, from
their publishers, under the publisher's terms (for example the Gemma models from Google, Whisper models from OpenAI, and the
Vosk English model). The download is checked against a published checksum before it is used.

## Artwork and media

The buddy characters (Blink, Void, Glitch, Cinder, Oracle), the interface art and the films in `docs/media` were made for this
project and are covered by the repository's licence unless a file says otherwise. If you believe an asset belongs to someone
else, open an issue and it will be removed or credited.

## Cloud services

The cloud brain uses the user's own account and key with Ollama Cloud (`ollama.com`); other providers are optional. No key is
included in this repository.
