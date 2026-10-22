"""HTTP status endpoint for local dashboards."""
from aiohttp import web

from api.serial_bridge import serial_bridge
from api.tunnel_manager import tunnel_manager
from brain.world_model import build_world_model
from core.status import runtime_status


async def status_handler(request: web.Request) -> web.Response:
    # What MBUDDY last heard and said is private: local only, like /control.
    from api.device_control import _require_local
    _require_local(request)
    world = build_world_model(runtime_status, runtime=runtime_status, event="status")
    runtime_status.update(
        world_model=world.to_dict(),
        capability_registry=world.capability_registry,
        disconnected_features=world.disconnected_features,
    )
    # Pass ?full=1 to include behaviour traces and the cognitive state.
    # They are excluded by default because they are megabytes in size.
    full = request.query.get("full", "").lower() in {"1", "true", "yes"}
    data = runtime_status.snapshot(full=full)
    serial_snap = serial_bridge.snapshot()
    data["serial"] = serial_snap
    data["tunnel"] = tunnel_manager.snapshot()

    # What the panel is actually showing, as reported by the firmware, next to
    # what the backend asked for. Everything else here is intent; these two are
    # the only place a divergence between the two can be seen. Available only
    # while USB is attached, since that is how the device reports it.
    data["device_drawn_emotion"] = serial_snap.get("drawn_face")
    data["device_face_in_sync"] = (
        serial_snap.get("drawn_face") == data.get("emotion")
        if serial_snap.get("drawn_face") is not None
        else None
    )
    return web.json_response(data)
