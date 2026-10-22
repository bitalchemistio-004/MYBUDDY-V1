"""
api/status_stream.py — WebSocket endpoint for real-time dashboard status.

Replaces 1.2s HTTP polling with push-based updates (<50ms latency).
"""
from aiohttp import web
import utils.logger as log
from core.status import runtime_status
from core.broadcast import broadcaster


async def status_stream_handler(request: web.Request) -> web.StreamResponse:
    """
    WebSocket endpoint for dashboard clients.
    Pushes a status snapshot immediately on connect, then streams updates.
    """
    # What MBUDDY last heard and said is private: local only, like /control.
    from api.device_control import _require_local
    _require_local(request)
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    log.info(f"Dashboard client connected: {request.remote}")
    broadcaster.add_client(ws)

    # Send initial snapshot immediately
    try:
        snapshot = runtime_status.snapshot()
        await broadcaster.broadcast_now(snapshot)
    except Exception as exc:
        log.warn(f"Initial broadcast failed: {exc}")

    try:
        # Keep connection alive (actual data pushed via broadcaster)
        async for msg in ws:
            # Client shouldn't send anything, but handle pings
            if msg.type == web.WSMsgType.PING:
                await ws.pong()
    except Exception as exc:
        log.info(f"Dashboard client disconnected: {exc}")
    finally:
        broadcaster.remove_client(ws)
        log.info(f"Dashboard client removed: {request.remote}")

    return ws
