"""
tts/local_tts.py - on-device speech with Kokoro-82M, plus expression clips.

Kokoro speaks every word (voice `af_heart`, chosen by the user). It cannot
laugh, chuckle, cough or sigh - it reads "Haha" as a word - so those come
from short clips rendered once in the same voice by tools/gen_expressions.py
(models/expressions/<name>_<n>.wav). A reply such as
"*laughs* okay, you got me" becomes a laugh clip followed by spoken words.

Runs on the GPU when onnxruntime-gpu can reach CUDA, else on the CPU. One
engine per process, created on first use.

Env: TTS_VOICE (default af_heart), TTS_DEVICE (default cuda).
"""
from __future__ import annotations

import os
import random
import re
import threading
import wave
from pathlib import Path

import numpy as np

import utils.logger as log
from stt.cuda_libs import register_cuda_dll_dirs

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models" / "kokoro"
EXPRESSION_DIR = ROOT / "models" / "expressions"
KOKORO_SR = 24000
GAP_S = 0.08  # breath between a clip and the words around it

# Stage directions MBUDDY may write, mapped to clip names. "*laughs*",
# "*chuckling*", "[laugh]" and the like all match, by word stem.
_EXPRESSION = re.compile(
    r"\*\s*(laugh|chuckl|cough|sigh)[a-z]*\s*\*|\[\s*(laugh|chuckl|cough|sigh)[a-z]*\s*\]",
    re.IGNORECASE,
)
_STEM_TO_CLIP = {"laugh": "laugh", "chuckl": "chuckle", "cough": "cough", "sigh": "sigh"}

# Laughs written as words ("Haha.", "hehehe", "Heh,"). Kokoro reads these
# flatly as words, so they are played as clips too. A lone "Ha" or "Aha" is
# left alone: those are usually speech, not laughter.
_WRITTEN_LAUGH = re.compile(r"\b(?:(?:ha){2,}h?|he(?:he)+|(heh))\b[.!,]*", re.IGNORECASE)

# A bare stage word opening a sentence ("Sighs. How about..."), which the
# model writes without asterisks. Mid-sentence ("he laughs at") stays speech.
_BARE_STAGE = re.compile(r"(?:^|(?<=[.!?]\s))(laughs|chuckles|sighs|coughs)[.!,]+(?=\s|$)", re.IGNORECASE)

_OTHER_STAGE = re.compile(r"\*\s*[a-z][a-z' ]{0,40}\*", re.IGNORECASE)

# Emoji and pictographs. Kokoro reads them out as words ("party popper"), so
# they are removed from anything it is asked to say.
_EMOJI = re.compile(
    "[🀀-🫿☀-➿🤀-🧿"
    "⬀-⯿️‍⃣]+"
)

_engine = None
_engine_lock = threading.Lock()
_synth_lock = threading.Lock()
_clips: dict[str, list[np.ndarray]] | None = None


def _load_engine():
    global _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        model = str(MODEL_DIR / "kokoro-v1.0.onnx")
        voices = str(MODEL_DIR / "voices-v1.0.bin")
        providers = ["CPUExecutionProvider"]
        from stt.local_whisper import _speech_on_cpu
        if (os.getenv("TTS_DEVICE") or ("cpu" if _speech_on_cpu() else "cuda")) == "cuda" and "CUDAExecutionProvider" in ort.get_available_providers():
            register_cuda_dll_dirs()
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        session = ort.InferenceSession(model, providers=providers)
        _engine = Kokoro.from_session(session, voices)
        log.info(f"Local TTS ready: Kokoro on {session.get_providers()[0]}")
        return _engine


_warmed = False


def warm() -> None:
    """Load the voice and run one throwaway synthesis (the first GPU run is
    slow). Called when MBUDDY wakes, so the first reply doesn't pay ~5 s; it
    stays unloaded while MBUDDY sleeps."""
    global _warmed
    if _warmed:
        return
    engine = _load_engine()
    with _synth_lock:
        engine.create("Hi.", voice=os.getenv("TTS_VOICE", "af_heart"), speed=1.0, lang="en-us")
    _warmed = True


def _load_clips() -> dict[str, list[np.ndarray]]:
    global _clips
    if _clips is None:
        clips: dict[str, list[np.ndarray]] = {}
        for path in sorted(EXPRESSION_DIR.glob("*_[0-9]*.wav")):
            name = path.stem.rsplit("_", 1)[0]
            with wave.open(str(path), "rb") as w:
                if w.getframerate() != KOKORO_SR:
                    continue
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            clips.setdefault(name, []).append(pcm.astype(np.float32) / 32768)
        _clips = clips
    return _clips


def _clip_for(match: re.Match) -> str:
    if match.re is _WRITTEN_LAUGH:
        return "chuckle" if match.group(1) else "laugh"
    if match.re is _BARE_STAGE:
        return _STEM_TO_CLIP[match.group(1).lower().rstrip("s").replace("chuckle", "chuckl")]
    return _STEM_TO_CLIP[(match.group(1) or match.group(2)).lower()]


def split_expressions(text: str) -> list[tuple[str, str]]:
    """[("say", words) | ("clip", name)] in reading order; empty text dropped."""
    parts: list[tuple[str, str]] = []
    pos = 0
    # Other stage directions ("*adjusts volume*") have no sound; spoken, they
    # came out as the words "adjusts volume". Drop them.
    text = _OTHER_STAGE.sub(lambda m: m.group(0) if _EXPRESSION.fullmatch(m.group(0)) else " ", text)
    matches = sorted(
        list(_EXPRESSION.finditer(text)) + list(_WRITTEN_LAUGH.finditer(text)) + list(_BARE_STAGE.finditer(text)),
        key=lambda m: m.start(),
    )
    for m in matches:
        if m.start() < pos:
            continue  # overlaps one already taken
        before = text[pos:m.start()].strip()
        if before:
            parts.append(("say", before))
        parts.append(("clip", _clip_for(m)))
        pos = m.end()
    rest = text[pos:].strip()
    if rest:
        parts.append(("say", rest))
    return parts


# Kokoro has no emotion control, but its voices are style vectors that mix.
# A share of a second voice colours af_heart without replacing it: brighter
# for happy, softer and breathier for sad or sleepy, firmer for annoyed.
# (user, S17: "the new TTS has no tone variations"). Shares are a first
# guess, tuned by ear.
EMOTION_BLEND: dict[str, tuple[str, float]] = {
    "Happy": ("af_bella", 0.30), "Glee": ("af_bella", 0.40), "Surprised": ("af_bella", 0.25),
    "Awe": ("af_nicole", 0.20),
    "Sad": ("af_nicole", 0.35), "Sleepy": ("af_nicole", 0.45), "Worried": ("af_nicole", 0.25),
    "Scared": ("af_sky", 0.30),
    "Annoyed": ("af_kore", 0.30), "Frustrated": ("af_kore", 0.30), "Unimpressed": ("af_kore", 0.30),
    "Skeptic": ("af_kore", 0.20), "Suspicious": ("af_kore", 0.20),
    "Angry": ("af_kore", 0.40), "Furious": ("af_kore", 0.50),
}
_blends: dict[str, np.ndarray] = {}


def voice_for_emotion(emotion: str, base: str | None = None, amount: float = 1.0):
    """The voice for this emotion: the base name, or a mixed style vector.

    `amount` scales the blend (the app's "Feeling in the voice": 1.0 is the
    default, 0 is the plain voice).
    """
    base = base or os.getenv("TTS_VOICE", "af_heart")
    blend = EMOTION_BLEND.get(emotion)
    amount = max(0.0, min(1.67, amount))
    if not blend or amount == 0:
        return base
    key = f"{base}|{emotion}|{amount:.2f}"
    if key not in _blends:
        engine = _load_engine()
        other, share = blend
        share = min(0.8, share * amount)
        _blends[key] = ((1 - share) * engine.get_voice_style(base) + share * engine.get_voice_style(other)).astype(np.float32)
    return _blends[key]


def synthesize(text: str, speed: float = 1.0, voice=None, prepare=None) -> np.ndarray:
    """
    Blocking synthesis to float32 mono at 24 kHz; empty array if nothing to say.

    `prepare` cleans each spoken part (e.g. tts.expressions.voice_ready). It
    runs after the expression markers are split out, so it cannot turn
    "*laughs*" into a spoken "Haha" before the clip gets a chance to play.
    """
    engine = _load_engine()
    if voice is None or (isinstance(voice, str) and not voice):  # a blend is an array: no truthiness test
        voice = os.getenv("TTS_VOICE", "af_heart")
    clips = _load_clips()
    gap = np.zeros(int(KOKORO_SR * GAP_S), dtype=np.float32)
    out: list[np.ndarray] = []
    for kind, value in split_expressions(text):
        if kind == "clip":
            from core import ui_settings
            if not ui_settings.get("expression_sounds"):
                continue  # switched off in the app: skip the sound, never read the word
            options = clips.get(value)
            if options:
                out += [random.choice(options), gap]
            # No clip for this sound: leave it out rather than read the word.
            continue
        if prepare is not None:
            value = prepare(value) or ""
        value = _EMOJI.sub("", value).strip()
        if not re.search(r"\w", value):
            continue
        with _synth_lock:
            samples, _ = engine.create(value, voice=voice, speed=speed, lang="en-us")
        out += [samples.astype(np.float32), gap]
    if not out:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(out[:-1])
