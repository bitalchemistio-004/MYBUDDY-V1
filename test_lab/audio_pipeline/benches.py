"""Registry of the extra test benches. Add a bench here and it appears on the /benches page."""
from __future__ import annotations

from .barge_bench import EchoBench
from .net_bench import NetBench
from .speaker_bench import SpeakerBench
from .stt_bench import SttBench
from .vad_bench import VadBench


def build_benches(lab) -> dict:
    benches = [SpeakerBench(lab), EchoBench(lab), SttBench(lab), VadBench(lab), NetBench(lab)]
    return {b.name: b for b in benches}
