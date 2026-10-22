import asyncio
import unittest
from unittest import mock

import numpy as np

from test_lab.audio_pipeline import corpus, stt_bench, vad_bench
from test_lab.audio_pipeline.tests.test_server import Harness, wait_for


class CorpusTests(unittest.TestCase):
    def test_wer_basics(self):
        self.assertEqual(corpus.wer("hey buddy what is the time", "hey buddy what is the time"), 0.0)
        self.assertEqual(corpus.wer("hey buddy what is the time", "Hey, buddy! What is the time?"), 0.0, "case and punctuation ignored")
        self.assertAlmostEqual(corpus.wer("one two three four", "one two tree four"), 0.25)            # 1 substitution
        self.assertAlmostEqual(corpus.wer("one two three four", "one two three"), 0.25)                # 1 deletion
        self.assertAlmostEqual(corpus.wer("one two", "one two three four"), 1.0)                      # 2 insertions
        self.assertEqual(corpus.wer("", ""), 0.0)
        self.assertEqual(corpus.wer("hello", ""), 1.0)

    def test_arabic_normalisation(self):
        self.assertEqual(corpus.wer("مرحبا كيف حالك اليوم", "مرحباً كيف حالك اليوم؟"), 0.0, "diacritics and punctuation ignored")
        self.assertEqual(corpus.wer("أين المكتبة", "اين المكتبه"), 0.0, "alef and teh-marbuta variants unified")

    def test_spacing_is_a_character_not_a_word_problem(self):
        self.assertAlmostEqual(corpus.wer("ما هي عاصمة لبنان", "ماهي عاصمة لبنان"), 0.5)
        self.assertEqual(corpus.cer("ما هي عاصمة لبنان", "ماهي عاصمة لبنان"), 0.0)
        self.assertGreater(corpus.cer("hello world", "hallo wurld"), 0.15)

    def test_language_code(self):
        self.assertEqual([corpus.language_code(x) for x in ("English", "Arabic", "en", "ar", "arabic", None)],
                         ["en", "ar", "en", "ar", "ar", None])

    def test_noise_is_mixed_at_the_requested_snr_on_the_speech_part(self):
        rng = np.random.default_rng(0)
        t = np.arange(16000 * 3) / 16000
        speech = (np.sin(2 * np.pi * 300 * t) * np.where((t % 1.0) < 0.5, 8000, 30)).astype(np.int16)   # bursts and pauses
        for snr in (15.0, 5.0):
            noisy = corpus.add_noise(speech, snr, rng)
            noise = noisy.astype(float) - speech.astype(float)
            measured = 20 * np.log10(corpus.speech_rms(speech) / np.sqrt(np.mean(noise ** 2)))
            self.assertAlmostEqual(measured, snr, delta=0.6, msg=f"target {snr}")

    def test_pink_noise_has_more_low_than_high_frequency_energy(self):
        x = corpus.pink_noise(65536, np.random.default_rng(1))
        spec = np.abs(np.fft.rfft(x)) ** 2
        self.assertGreater(spec[10:200].mean(), 20 * spec[20000:30000].mean())

    def test_quiet_condition_is_34_db_down(self):
        a = (np.random.default_rng(2).standard_normal(16000) * 3000).astype(np.int16)
        q = corpus.CONDITIONS["quiet"][1](a, None)
        self.assertAlmostEqual(20 * np.log10(np.std(q) / np.std(a)), -34.0, delta=0.5)


class FakeSttMixin:
    """Replace the two slow networked steps (speech synthesis, Groq) so the bench logic is tested offline."""

    @staticmethod
    def patch_bench(bench, transcript=lambda ref: ref, latency_ms=100):
        refs = {}

        async def clip(lang, idx):
            text = corpus.SENTENCES[lang][idx]
            pcm = (np.sin(2 * np.pi * 250 * np.arange(16000) / 16000) * 6000).astype(np.int16)
            refs[len(refs)] = text
            bench._last_ref = text
            return pcm, "test-voice", text

        async def transcribe(audio):
            hyp = transcript(bench._last_ref)
            return {"text": hyp, "language": "Arabic" if any("؀" <= c <= "ۿ" for c in bench._last_ref) else "English",
                    "elapsed_ms": latency_ms}

        bench._clip, bench._transcribe = clip, transcribe


class SttBenchTests(unittest.IsolatedAsyncioTestCase, FakeSttMixin):
    async def run_bench(self, h, params, transcript=lambda ref: ref):
        from test_lab.audio_pipeline.server import LAB
        bench = h.app[LAB].benches["stt"]
        self.patch_bench(bench, transcript)
        ui = await h.ui()
        with mock.patch.object(stt_bench, "REQUEST_GAP_S", 0.0):
            await ui.send_json({"type": "bench", "name": "stt", "action": "run", "params": params})
            st = (await wait_for(ui, lambda m: m["type"] == "bench" and m["name"] == "stt" and m["state"]["phase"] in ("done", "failed"), timeout=20))["state"]
        await ui.close()
        return st

    async def test_perfect_transcripts_score_zero_error(self):
        async with Harness() as h:
            st = await self.run_bench(h, {"languages": ["en", "ar"], "conditions": ["clean", "noise5"], "per_language": 2})
            self.assertEqual(st["phase"], "done")
            rows = st["tables"][0]["rows"]
            self.assertEqual(len(rows), 4)
            for r in rows:
                self.assertEqual((r[3], r[4], r[5], r[7]), ("0%", "0%", "100%", 0))
            self.assertEqual(st["verdict"]["level"], "ok")
            self.assertEqual(len(st["tables"][1]["rows"]), 8)

    async def test_errors_are_scored_and_flagged(self):
        async with Harness() as h:
            garble = lambda ref: " ".join(ref.split()[:-3]) or "x"        # drops the last three words (over 35% wrong)
            st = await self.run_bench(h, {"languages": ["en"], "conditions": ["clean"], "per_language": 3}, garble)
            row = st["tables"][0]["rows"][0]
            self.assertNotEqual(row[3], "0%")
            self.assertIn([0, 3], st["tables"][0]["bad"])
            self.assertIn(st["verdict"]["level"], ("warn", "bad"))

    async def test_no_text_is_counted_separately_not_scored_as_a_wrong_answer(self):
        async with Harness() as h:
            st = await self.run_bench(h, {"languages": ["en"], "conditions": ["clean"], "per_language": 2}, lambda ref: "")
            row = st["tables"][0]["rows"][0]
            self.assertEqual((row[3], row[7]), ("—", 2))
            self.assertIn("No clip produced any text", st["verdict"]["text"])
            self.assertEqual(st["verdict"]["level"], "bad")

    async def test_wrong_language_detection_is_reported(self):
        async with Harness() as h:
            from test_lab.audio_pipeline.server import LAB
            bench = h.app[LAB].benches["stt"]
            self.patch_bench(bench)

            async def wrong_lang(audio):
                return {"text": bench._last_ref, "language": "Arabic", "elapsed_ms": 50}      # calls English speech Arabic
            bench._transcribe = wrong_lang
            ui = await h.ui()
            with mock.patch.object(stt_bench, "REQUEST_GAP_S", 0.0):
                await ui.send_json({"type": "bench", "name": "stt", "action": "run", "params": {"languages": ["en"], "conditions": ["clean"], "per_language": 2}})
                st = (await wait_for(ui, lambda m: m["type"] == "bench" and m["name"] == "stt" and m["state"]["phase"] == "done", timeout=20))["state"]
            self.assertEqual(st["tables"][0]["rows"][0][5], "0%")
            self.assertIn([0, 5], st["tables"][0]["bad"])
            await ui.close()

    async def test_bad_parameters_are_refused(self):
        async with Harness() as h:
            ui = await h.ui()
            await ui.send_json({"type": "bench", "name": "stt", "action": "run", "params": {"languages": [], "conditions": ["clean"]}})
            self.assertIn("at least one", (await wait_for(ui, lambda m: m["type"] == "notice"))["text"])
            await ui.close()


class VadBenchPieces(unittest.TestCase):
    """These call the real production modules."""

    @classmethod
    def setUpClass(cls):
        cls.prod = vad_bench.load_production()

    def test_noise_generators_hit_their_stated_level(self):
        rng = np.random.default_rng(0)
        for kind in vad_bench.NOISE_KINDS:
            x = vad_bench.noise_clip(kind, 3.0, rng)
            self.assertEqual(len(x), 48000)
            if "Keyboard" in kind:
                continue                                       # transient noise: RMS is not what its label states
            level = 20 * np.log10(np.sqrt(np.mean(x.astype(float) ** 2)) / 32768)
            self.assertAlmostEqual(level, float(kind.split("(")[1].split(" dBFS")[0]), delta=1.0, msg=kind)

    def test_production_text_logic_passes_every_documented_case(self):
        for fn, text, why, expected in vad_bench.text_cases(self.prod["config"]):
            actual = bool(self.prod["wake"](text) if fn == "wake" else self.prod["halluc"](text))
            self.assertEqual(actual, expected, f"{fn}: {text!r} ({why})")

    def test_gate_never_starts_on_digital_silence_or_near_silence(self):
        rng = np.random.default_rng(1)
        for pcm in (np.zeros(16000 * 20, np.int16), vad_bench.noise_clip("Near silence (-70 dBFS)", 20.0, rng)):
            r = vad_bench.run_gate(self.prod, pcm)
            self.assertEqual((r["starts"], r["ended"]), (0, 0))

    def test_gate_restores_the_production_module_after_running(self):
        mod = self.prod["session"]
        before = (mod.asyncio, mod.log)
        vad_bench.run_gate(self.prod, np.zeros(16000, np.int16))
        self.assertEqual((mod.asyncio, mod.log), before)

    def test_gate_finds_an_utterance_once_when_given_real_speech(self):
        wavs = sorted(corpus.CORPUS_DIR.glob("en_0_*_16000.wav")) if corpus.CORPUS_DIR.exists() else []
        if not wavs:
            self.skipTest("no cached generated speech yet (run the speech-to-text bench once)")
        import wave
        with wave.open(str(wavs[0])) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        sig, lead = vad_bench.embed_speech(pcm, np.random.default_rng(3))
        r = vad_bench.run_gate(self.prod, sig)
        self.assertEqual((r["starts"], r["ended"]), (1, 1))
        self.assertLess(r["first_start_s"] - lead, 1.0)

    def test_scoring(self):
        truth = np.array([True, True, False, False])
        rec, fa = vad_bench.score(np.array([True, False, True, False]), truth)
        self.assertEqual((rec, fa), (0.5, 0.5))
        self.assertEqual(vad_bench.score(np.array([True]), np.array([True])), (1.0, None))


class VadBenchRun(unittest.IsolatedAsyncioTestCase):
    async def test_full_run_without_network_reports_every_section(self):
        async with Harness() as h:
            from test_lab.audio_pipeline.server import LAB
            bench = h.app[LAB].benches["vad"]

            async def no_clips(lang, idx):
                raise RuntimeError("offline")
            bench._clip = no_clips
            ui = await h.ui()
            await ui.send_json({"type": "bench", "name": "vad", "action": "run", "params": {}})
            st = (await wait_for(ui, lambda m: m["type"] == "bench" and m["name"] == "vad" and m["state"]["phase"] in ("done", "failed"), timeout=60))["state"]
            self.assertEqual(st["phase"], "done", st["message"])
            titles = [t["title"] for t in st["tables"]]
            self.assertTrue(any("Production gate on 30 s" in t for t in titles))
            self.assertTrue(any("Wake phrase and hallucination" in t for t in titles))
            self.assertTrue(any("could not be generated" in n for n in st["notes"]), "offline speech generation is reported, not hidden")
            text = next(t for t in st["tables"] if "Wake phrase and hallucination" in t["title"])
            self.assertEqual(text["bad"], [], "production passes its own documented cases")
            await ui.close()


if __name__ == "__main__":
    unittest.main()
