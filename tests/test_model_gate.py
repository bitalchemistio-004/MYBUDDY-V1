"""The cloud answers one request at a time: the user's turn must never queue behind housekeeping."""
import asyncio

from core import model_gate as mg


def run(coro):
    return asyncio.run(coro)


def test_priority_names_and_urls():
    assert mg.parse("Background") == "background" and mg.parse("nonsense") == "turn" and mg.parse(None) == "turn"
    assert mg.url_with("http://x/api/chat", "turn") == "http://x/api/chat"
    assert mg.url_with("http://x/api/chat", "background") == "http://x/api/chat?priority=background"
    assert mg.url_with("http://x/api/chat?a=1", "job") == "http://x/api/chat?a=1&priority=job"
    with mg.as_priority("background"):
        assert mg.priority.get() == "background"
    assert mg.priority.get() == "turn"


def test_a_waiting_turn_goes_before_housekeeping_that_arrived_first():
    order = []

    async def call(gate, name, tag, hold=0.05):
        async with gate.slot(name):
            order.append(tag)
            await asyncio.sleep(hold)

    async def go():
        gate = mg.Gate(slots=1, quiet={"job": 0.0, "background": 0.0})
        busy = asyncio.create_task(call(gate, "turn", "first-turn", 0.15))
        await asyncio.sleep(0.02)                                   # the slot is taken
        bg = asyncio.create_task(call(gate, "background", "background"))
        job = asyncio.create_task(call(gate, "job", "job"))
        await asyncio.sleep(0.02)
        turn = asyncio.create_task(call(gate, "turn", "second-turn"))
        await asyncio.gather(busy, bg, job, turn)

    run(go())
    assert order == ["first-turn", "second-turn", "job", "background"]


def test_housekeeping_waits_for_the_gap_between_two_calls_of_one_turn():
    started = {}

    async def go():
        gate = mg.Gate(slots=1, quiet={"job": 0.0, "background": 0.30})

        async def one(name, tag):
            async with gate.slot(name):
                started[tag] = asyncio.get_running_loop().time()
                await asyncio.sleep(0.01)

        await one("turn", "decide")
        bg = asyncio.create_task(one("background", "bg"))
        await asyncio.sleep(0.1)                                    # the reply call of the same turn comes a moment later
        await one("turn", "reply")
        await bg

    run(go())
    assert started["reply"] < started["bg"]                           # housekeeping did not slip in between
    assert started["bg"] - started["reply"] >= 0.25                   # and it waited for the quiet after the turn


def test_nothing_waits_for_ever():
    async def go():
        gate = mg.Gate(slots=1, quiet={"background": 10.0}, max_wait=0.2)
        async with gate.slot("turn"):
            pass
        t = asyncio.get_running_loop().time()
        async with gate.slot("background"):
            return asyncio.get_running_loop().time() - t

    assert run(go()) < 1.0


def test_a_cancelled_waiter_gives_the_slot_back():
    async def go():
        gate = mg.Gate(slots=1, quiet={"job": 0.0, "background": 0.0})

        async def hold(name, secs):
            async with gate.slot(name):
                await asyncio.sleep(secs)

        a = asyncio.create_task(hold("turn", 0.1))
        await asyncio.sleep(0.01)
        b = asyncio.create_task(hold("background", 0.0))
        await asyncio.sleep(0.01)
        b.cancel()
        await a
        async with gate.slot("turn"):                               # still works: nothing is stuck
            return True

    assert run(go()) is True
