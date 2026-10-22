"""
core/broadcast.py — Real-time status broadcaster for dashboards.

Replaces 1.2 s HTTP polling with push-based WebSocket updates. Any
RuntimeStatus.update() call marks the hub dirty; a debounced background
task pushes the latest snapshot to all connected dashboard clients.

This gives < 50 ms end-to-end latency for emotion/energy/action changes
instead of waiting for the next poll.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Set

import utils.logger as log


class StatusBroadcaster:
    """Fan-out hub for real-time status snapshots to dashboard clients."""

    def __init__(self) -> None:
        self.clients: Set[Any] = set()
        self._snapshot_provider: Callable[[], dict] | None = None
        self._dirty: bool = False
        self._task: asyncio.Task | None = None
        # Coalesce bursts of updates. This is a status view read by a person,
        # not a control loop: 0.04s allowed 25 broadcasts a second, which at
        # ~11 KB each meant a quarter of a megabyte per second of JSON for the
        # desktop to parse. Four a second still feels instant.
        self._debounce_s: float = 0.25
        # Last payload sent, so an unchanged snapshot is not re-sent at all.
        self._last_payload: str | None = None

    def set_snapshot_provider(self, provider: Callable[[], dict]) -> None:
        self._snapshot_provider = provider

    def add_client(self, ws: Any) -> None:
        self.clients.add(ws)

    def remove_client(self, ws: Any) -> None:
        self.clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self.clients)

    def mark_dirty(self) -> None:
        """Schedule a broadcast if one is not already pending (debounced)."""
        if self._dirty:
            return
        self._dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop yet (early boot) — a later update will trigger.
            self._dirty = False
            return
        self._task = loop.create_task(self._broadcast_soon())

    async def _broadcast_soon(self) -> None:
        await asyncio.sleep(self._debounce_s)
        self._dirty = False
        if not self.clients or self._snapshot_provider is None:
            return
        try:
            data = self._snapshot_provider()
        except Exception as exc:
            log.warn(f"Broadcast snapshot failed: {exc}")
            return
        if not self.clients:
            return
        payload = json.dumps(data, default=str)

        # Most ticks change nothing a viewer would notice. Skipping identical
        # payloads removes the bulk of the traffic on an idle system.
        if payload == self._last_payload:
            return
        self._last_payload = payload

        for client in list(self.clients):
            try:
                await client.send_str(payload)
            except Exception:
                self.clients.discard(client)

    async def broadcast_now(self, data: dict) -> None:
        """Push an immediate snapshot to all clients (used on connect)."""
        if not self.clients:
            return
        payload = json.dumps(data, default=str)
        self._last_payload = payload
        for client in list(self.clients):
            try:
                await client.send_str(payload)
            except Exception:
                self.clients.discard(client)

    async def close_all(self) -> None:
        for client in list(self.clients):
            try:
                await client.close()
            except Exception:
                pass
        self.clients.clear()


# Global hub shared across the process.
broadcaster = StatusBroadcaster()
