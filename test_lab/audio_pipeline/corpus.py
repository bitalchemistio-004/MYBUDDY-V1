"""
Test corpus for the speech benches: generated speech, controlled noise, and word error rate.

Speech is synthesised with edge-tts (the same engine MBUDDY speaks with) in a
few English and Lebanese-Arabic voices, decoded to 16-bit mono and cached on
disk, so a bench costs no repeat downloads. Because the text of every clip is
known, accuracy can be measured exactly. Generated speech is cleaner than a
real voice through a real microphone — treat the numbers as a regression
baseline, not as a promise about live use.

MBUDDY supports Arabic and English only, so those are the two languages here.
"""
from __future__ import annotations

import re
import unicodedata
import wave
from pathlib import Path

import numpy as np

CORPUS_DIR = Path(__file__).resolve().parent / "stt_corpus"

SENTENCES = {
    "en": [
        "Hey buddy, what's the weather like today?",
        "Set a timer for me please.",
        "What is the capital of France?",
        "Remind me to call my mother tomorrow morning.",
        "Tell me a short joke.",
        "How do I say thank you in Arabic?",
        "Turn the music down a little.",
        "What time does the sun set today?",
    ],
    "ar": [
        "مرحبا كيف حالك اليوم",
        "ما هي عاصمة لبنان",
        "شغل الموسيقى من فضلك",
        "ذكرني بالاجتماع بعد ساعة",
        "كيف الطقس اليوم في بيروت",
        "احكي لي نكتة قصيرة",
        "خفف الصوت قليلا",
        "ما اسمك وماذا تفعل",
    ],
}
VOICES = {"en": ["en-US-AriaNeural", "en-GB-RyanNeural"], "ar": ["ar-LB-LaylaNeural", "ar-LB-RamiNeural"]}


# ── speech synthesis (cached) ───────────────────────────────────────────
async def synth(text: str, voice: str) -> bytes:
    """mp3 bytes from edge-tts."""
    import edge_tts
    data = b""
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            data += chunk["data"]
    if not data:
        raise RuntimeError(f"edge-tts returned no audio for voice {voice}")
    return data


def decode_mp3(mp3: bytes, rate: int) -> np.ndarray:
    import miniaudio
    d = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=rate)
    return np.frombuffer(d.samples, dtype="<i2").copy()


def _cache_path(lang: str, idx: int, voice: str, rate: int) -> Path:
    return CORPUS_DIR / f"{lang}_{idx}_{voice}_{rate}.wav"


async def get_clip(lang: str, idx: int, rate: int = 16000) -> tuple[np.ndarray, str, str]:
    """(int16 samples, voice, reference text) for sentence `idx` of `lang`, generating and caching it if needed."""
    text = SENTENCES[lang][idx % len(SENTENCES[lang])]
    voice = VOICES[lang][idx % len(VOICES[lang])]
    path = _cache_path(lang, idx, voice, rate)
    if path.is_file():
        with wave.open(str(path), "rb") as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy(), voice, text
    pcm = decode_mp3(await synth(text, voice), rate)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    (CORPUS_DIR / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return pcm, voice, text


# ── noise and conditions ────────────────────────────────────────────────
def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """Unit-variance 1/f noise (a fair stand-in for room and fan noise)."""
    spec = np.fft.rfft(rng.standard_normal(n))
    k = np.arange(len(spec), dtype=np.float64)
    k[0] = 1.0
    x = np.fft.irfft(spec / np.sqrt(k), n)
    return x / max(float(np.std(x)), 1e-9)


def active_frames(audio: np.ndarray, frame: int = 320) -> np.ndarray:
    """Per-frame mask of where the speech actually is (frames within ~20 dB of the loudest)."""
    n = len(audio) // frame
    if n == 0:
        return np.zeros(0, dtype=bool)
    rms = np.sqrt(np.mean(audio[: n * frame].astype(np.float64).reshape(n, frame) ** 2, axis=1))
    return rms > 0.1 * rms.max() if rms.max() > 0 else np.zeros(n, dtype=bool)


def speech_rms(audio: np.ndarray, frame: int = 320) -> float:
    mask = active_frames(audio, frame)
    n = len(mask)
    if not mask.any():
        return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    seg = audio[: n * frame].astype(np.float64).reshape(n, frame)[mask]
    return float(np.sqrt(np.mean(seg ** 2)))


def add_noise(audio: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    """Mix pink noise so the SPEECH-ACTIVE part sits `snr_db` above the noise."""
    noise = pink_noise(len(audio), rng) * speech_rms(audio) / (10.0 ** (snr_db / 20.0))
    return np.clip(np.round(audio.astype(np.float64) + noise), -32768, 32767).astype(np.int16)


CONDITIONS = {
    "clean": ("Clean", lambda a, rng: a),
    "noise15": ("Pink noise, 15 dB SNR", lambda a, rng: add_noise(a, 15.0, rng)),
    "noise5": ("Pink noise, 5 dB SNR", lambda a, rng: add_noise(a, 5.0, rng)),
    "quiet": ("Very quiet talker (-34 dB)", lambda a, rng: np.round(a.astype(np.float64) * 0.02).astype(np.int16)),
}


# ── text accuracy ───────────────────────────────────────────────────────
_AR_DIACRITICS = re.compile("[ً-ْٰـ]")
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي",
                         "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"})


def normalize(text: str) -> list[str]:
    """Lower-case words with punctuation, Arabic diacritics and letter variants removed."""
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = _AR_DIACRITICS.sub("", t).translate(_AR_MAP)
    t = re.sub(r"[^\w\s]", " ", t)
    return t.split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate (substitutions + insertions + deletions) / reference words. Can exceed 1.0."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate with spaces ignored, so 'ما هي' vs 'ماهي' (a spacing choice) does not count as a wrong word."""
    ref, hyp = "".join(normalize(reference)), "".join(normalize(hypothesis))
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def language_code(detected: str | None) -> str | None:
    """Whisper reports 'English' / 'Arabic' (or a code); map to en / ar."""
    d = (detected or "").strip().lower()
    if d.startswith(("en", "eng")):
        return "en"
    if d.startswith(("ar", "ara")):
        return "ar"
    return d[:2] or None
