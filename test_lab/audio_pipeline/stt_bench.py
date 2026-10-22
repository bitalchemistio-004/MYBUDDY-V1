"""
Speech-to-text bench: how accurate is production's STT stage on Arabic and English
under noise and low volume?

Generated speech with known text goes through the production DSP -> trim ->
Groq Whisper path (stt_branch), then word error rate, language-detection
accuracy and latency are computed. It uses your Groq quota (one request per clip
and condition), spaced out so a free-tier rate limit is not hit; a rate-limited
or empty reply is counted separately as "no text" rather than being scored as
a wrong transcript.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time

import numpy as np

from . import corpus, stt_branch
from .bench_hub import BenchBase, BenchError, table

REQUEST_GAP_S = 1.2                       # spacing between Groq requests
WER_WARN, WER_BAD = 0.15, 0.35


class SttBench(BenchBase):
    name = "stt"

    async def action(self, action: str, params: dict) -> str | None:
        if action != "run":
            return f"stt: unknown action {action!r}"
        if self.running:
            return "The speech-to-text test is already running."
        # an empty selection means "nothing chosen", not "use the defaults"
        langs = [l for l in params.get("languages", ["en", "ar"]) if l in corpus.SENTENCES]
        conds = [c for c in params.get("conditions", ["clean", "noise15", "noise5"]) if c in corpus.CONDITIONS]
        if not langs or not conds:
            return "Pick at least one language and one condition."
        per = max(1, min(8, int(params.get("per_language", 4))))
        self.start(self._run(langs, conds, per))
        return None

    # split out so tests can replace the two slow, networked steps
    async def _clip(self, lang: str, idx: int):
        return await corpus.get_clip(lang, idx, 16000)

    async def _transcribe(self, audio_f32: np.ndarray) -> dict:
        return await stt_branch.transcribe_array(audio_f32, "generated clip", "auto", True)

    async def _run(self, langs: list[str], conds: list[str], per: int) -> None:
        status = stt_branch.status()
        if status["state"] == "no_key":
            raise BenchError("GROQ_API_KEY is not set, so speech-to-text cannot run.")
        total = len(langs) * per * len(conds)
        await self.begin(f"Preparing {len(langs) * per} clips…")
        rng = np.random.default_rng(7)
        results: list[dict] = []
        done = 0
        for lang in langs:
            for idx in range(per):
                await self.update(message=f"Generating {lang.upper()} clip {idx + 1}/{per}…", progress=done / total)
                pcm, voice, text = await self._clip(lang, idx)
                for cond in conds:
                    audio = corpus.CONDITIONS[cond][1](pcm, rng)
                    t0 = time.perf_counter()
                    try:
                        r = await self._transcribe(audio.astype(np.float32) / 32768.0)
                        err = None
                    except Exception as exc:
                        r, err = {"text": None, "language": None}, f"{type(exc).__name__}: {exc}"
                    hyp = r.get("text") or ""
                    results.append({"lang": lang, "idx": idx, "voice": voice, "cond": cond, "ref": text, "hyp": hyp,
                                    "wer": corpus.wer(text, hyp) if hyp else None,
                                    "cer": corpus.cer(text, hyp) if hyp else None,
                                    "detected": corpus.language_code(r.get("language")) if hyp else None,
                                    "ms": r.get("elapsed_ms") or int((time.perf_counter() - t0) * 1000), "error": err})
                    done += 1
                    outcome = "no text" if not hyp else "WER {:.0%}".format(results[-1]["wer"])
                    await self.update(message=f"{lang.upper()} clip {idx + 1}/{per}, {corpus.CONDITIONS[cond][0]}: {outcome}", progress=done / total)
                    await asyncio.sleep(REQUEST_GAP_S)
        await self.update(**self._report(results, langs, conds))
        self._save(results)

    def _report(self, results: list[dict], langs: list[str], conds: list[str]) -> dict:
        rows, bad = [], []
        for lang in langs:
            for cond in conds:
                rs = [r for r in results if r["lang"] == lang and r["cond"] == cond]
                scored = [r for r in rs if r["wer"] is not None]
                notext = len(rs) - len(scored)
                mean_wer = statistics.mean(r["wer"] for r in scored) if scored else None
                mean_cer = statistics.mean(r["cer"] for r in scored) if scored else None
                lang_acc = statistics.mean(r["detected"] == lang for r in scored) if scored else None
                ms = sorted(r["ms"] for r in scored)
                p50 = ms[len(ms) // 2] if ms else None
                rows.append([lang.upper(), corpus.CONDITIONS[cond][0], len(rs),
                             "—" if mean_wer is None else f"{mean_wer:.0%}",
                             "—" if mean_cer is None else f"{mean_cer:.0%}",
                             "—" if lang_acc is None else f"{lang_acc:.0%}",
                             "—" if p50 is None else f"{p50} ms", notext])
                i = len(rows) - 1
                if mean_wer is None or mean_wer > WER_BAD:
                    bad.append((i, 3))
                if lang_acc is not None and lang_acc < 1.0:
                    bad.append((i, 5))
                if notext:
                    bad.append((i, 7))
        detail = [[r["lang"].upper(), corpus.CONDITIONS[r["cond"]][0], r["ref"], r["hyp"] or f"(no text{': ' + r['error'] if r['error'] else ''})",
                   "—" if r["wer"] is None else f"{r['wer']:.0%}", "—" if r["cer"] is None else f"{r['cer']:.0%}",
                   r["detected"] or "—", f"{r['ms']} ms"] for r in results]
        scored_all = [r for r in results if r["wer"] is not None]
        overall = statistics.mean(r["wer"] for r in scored_all) if scored_all else None
        notext_all = len(results) - len(scored_all)
        if overall is None:
            verdict = {"level": "bad", "text": "No clip produced any text. Check the Groq key, quota and network (see the server console)."}
        else:
            lvl = "ok" if overall <= WER_WARN and not notext_all else "warn" if overall <= WER_BAD else "bad"
            verdict = {"level": lvl, "text": f"Overall word error rate {overall:.0%} over {len(scored_all)} clips" +
                       (f"; {notext_all} returned no text." if notext_all else ".")}
        return {"phase": "done", "message": "Finished.", "progress": 1.0, "verdict": verdict,
                "tables": [table("Accuracy by language and condition",
                                 ["Language", "Condition", "Clips", "Word error rate", "Character error rate", "Language detected correctly", "Median latency", "No text"], rows, bad),
                           table("Every clip", ["Lang", "Condition", "Reference", "Heard", "WER", "CER", "Detected", "Latency"], detail)],
                "notes": ["Word error rate: 0% is perfect; text is compared after removing punctuation, case and Arabic diacritics/letter variants. "
                          "Character error rate ignores spaces, so a spacing choice (Arabic 'ما هي' vs 'ماهي') is not counted as a wrong word.",
                          "Generated speech is cleaner than a real voice through the robot's microphone: use this as a regression baseline "
                          "(did an update make it worse?), not as a promise about live accuracy.",
                          "The 'Very quiet talker' condition shows how much production's automatic gain rescues low volume. "
                          "Whisper's own hallucinations on silence are covered by the Voice detection bench."]}

    def _save(self, results: list[dict]) -> None:
        path = getattr(self.lab, "_calibration_file", None)
        if path is not None:
            path.with_name("stt_bench_last.json").write_text(json.dumps({"at": time.time(), "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
