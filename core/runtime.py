"""
core/runtime.py — the Companion Runtime: the part of MBUDDY that is MBUDDY.

Until now the agent lived inside `MBUDDYSession`, which is created per device
WebSocket connection. That made the ESP32 not a body but a life-support machine:
with no device attached, `/control/chat` returned 503 ("MBUDDY needs a connected
device before it can hold a conversation"), and so did state, energy, sfx and
power. MBUDDY could not think, remember or speak without hardware — which is the
opposite of the intended relationship, and left the Windows-only experience dead
whenever the body was charging.

The Runtime holds what must survive every interface: the memory and the
emotional state. Sessions borrow them. A device connecting or leaving changes
which *transport* is attached, not who MBUDDY is.

Nothing here may import a Windows, device or provider module — the Runtime has
to run anywhere, including on a host with no PC and no body attached.
"""
from __future__ import annotations

from typing import Any

import utils.logger as log


class NullTransport:
    """
    Stands in for a device WebSocket when no body is attached.

    Reports itself as closed, deliberately. Every send path already checks
    `ws.closed` before writing, so with this in place those writes correctly
    become no-ops rather than silently pretending to reach a device that is not
    there — the same honesty rule the rest of the system now follows.
    """

    #: Always true: there is genuinely nothing to send to.
    closed = True

    async def send_str(self, payload: str) -> bool:
        return False

    async def send_bytes(self, payload: bytes) -> bool:
        return False

    async def close(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def ping(self, *args: Any, **kwargs: Any) -> None:
        return None


class CompanionRuntime:
    """
    The persistent agent. One per process, outliving every connection.

    Owns the two things that define continuity — what MBUDDY remembers and how
    it currently feels — so that a device attaching mid-thought joins something
    already running rather than starting a new one.
    """

    def __init__(self) -> None:
        self._memory: dict | None = None
        self._state: Any | None = None
        self._headless_session: Any | None = None
        self._headless_tasks: list = []

    # ── Memory ────────────────────────────────────────────────────────
    @property
    def memory(self) -> dict:
        """The shared memory dict, loaded once."""
        if self._memory is None:
            from memory.long_term import load_memory

            self._memory = load_memory()
        return self._memory

    def set_memory(self, memory: dict) -> None:
        """
        Adopt an already-loaded memory dict.

        Used at startup so the Runtime and whatever loaded memory first are the
        same object rather than two copies that drift apart.
        """
        self._memory = memory

    # ── State ─────────────────────────────────────────────────────────
    @property
    def state(self):
        """
        The shared emotional state.

        Sessions must use this rather than constructing their own, otherwise
        mood and energy fork the moment a second session exists — the device
        would be feeling one thing and the desktop another, with both correct
        about their own copy.
        """
        if self._state is None:
            from core.state_manager import MBUDDYState

            self._state = MBUDDYState(memory=self.memory)
        return self._state

    # ── Headless session ──────────────────────────────────────────────
    def headless_session(self):
        """
        A session with no body attached, created on first use.

        This is what answers a typed message when the ESP32 is unplugged. It is
        a full session — same routing, same tools, same memory writes — holding
        a `NullTransport`, so everything that would have been sent to a device
        is simply not sent.

        Also starts the same background loops a real device connection starts
        (`_start_inner`, the awake-idle timeout, expression decay). Without
        this the headless session was created inert: no autonomous thought, no
        settling back to a resting mood, no sleep cycle — MBUDDY was only ever
        as alive as the last `/control/chat` call, which is the opposite of
        "an independent being that exists on the laptop." A device connecting
        or leaving should change which body is available, not whether MBUDDY
        is thinking at all.
        """
        if self._headless_session is None:
            import asyncio

            from api.websocket_server import MBUDDYSession

            session = MBUDDYSession(NullTransport(), self.memory)
            self._headless_session = session

            session._start_inner()
            self._headless_tasks = [
                asyncio.ensure_future(session.handle_awake_timeout()),
                asyncio.ensure_future(session.handle_expression_decay()),
            ]
            log.info(
                "Runtime: headless session ready (no body attached) — "
                "inner life, idle timeout and expression decay all running."
            )
        return self._headless_session

    def has_body(self) -> bool:
        """Whether a real device transport is currently attached."""
        from api.device_control import device_session

        return device_session() is not None


_runtime: CompanionRuntime | None = None


def get_runtime() -> CompanionRuntime:
    """The process-wide Companion Runtime."""
    global _runtime
    if _runtime is None:
        _runtime = CompanionRuntime()
    return _runtime
