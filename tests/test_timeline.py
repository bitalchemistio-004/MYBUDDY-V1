"""One timeline per turn with every stage (M2 S6c)."""
import asyncio
import time

from core import timeline


def test_spans_and_marks_are_listed_in_order():
    tl = timeline.Timeline(start=time.monotonic())
    tl.marks = [("routed", 0.01), ("decide step 1>", 0.02), ("decide step 1<", 1.79), ("prompt built", 1.9),
                ("first token", 4.8), ("tool run>", 2.0)]
    line = tl.line()
    assert line.index("routed 0.01") < line.index("decide step 1 0.02-1.79") < line.index("prompt built 1.90")
    assert "tool run 2.00-?" in line and "first token 4.80" in line


def test_marks_follow_the_turn_into_worker_threads():
    async def turn():
        tl = timeline.begin()
        timeline.mark("routed")
        await asyncio.to_thread(timeline.mark, "recall>")
        await asyncio.to_thread(timeline.mark, "recall<")
        return tl
    tl = asyncio.run(turn())
    assert [n for n, _ in tl.marks] == ["routed", "recall>", "recall<"]


def test_without_a_turn_marks_do_nothing():
    async def elsewhere():
        timeline.mark("stray")   # e.g. a background job: no timeline, no error
        return timeline.current()
    assert asyncio.run(elsewhere()) is None
