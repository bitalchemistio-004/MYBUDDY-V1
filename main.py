# [DEPLOY_FORCE] May 20, 2026 - 11:32 AM
"""
main.py — MBUDDY application entry point.
Highly hardened for Railway/Production.
"""
import os
import sys
import asyncio

# Stay off the internet for models that are already on this PC: Whisper and
# the memory embedder otherwise ask the Hugging Face Hub on every start.
# Only when both are cached, so a first run can still download them.
def _hf_models_cached() -> bool:
    hub = os.path.join(os.getenv("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface"), "hub")
    stt = os.getenv("STT_MODEL", "small")
    vec = os.getenv("MBUDDY_VECTOR_MODEL", "all-MiniLM-L6-v2")
    repos = [f"models--Systran--faster-whisper-{stt}", "models--sentence-transformers--" + vec.split("/")[-1]]
    return all(os.path.isdir(os.path.join(hub, r, "snapshots")) and os.listdir(os.path.join(hub, r, "snapshots")) for r in repos)


if "HF_HUB_OFFLINE" not in os.environ and _hf_models_cached():
    os.environ["HF_HUB_OFFLINE"] = "1"
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# 1. Force Unix-style unbuffered logging immediately if possible
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass
print("------------------------------------------", flush=True)
print("MBUDDY SYSTEM VERSION: 4.1.0-OVERHAUL", flush=True)
print("------------------------------------------", flush=True)
print("DEBUG: Pre-init: sys.path injection start...", flush=True)

# 2. Add root to path so subpackages (core, api, brain, etc.) are always found
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# 3. Virtual Environment Safety Check
VENV_PATH = os.path.join(ROOT_DIR, ".venv")
IS_VENV = sys.prefix != sys.base_prefix or "VIRTUAL_ENV" in os.environ
if not IS_VENV and os.path.exists(VENV_PATH):
    print("WARNING: You are running MBUDDY without an active virtual environment!", flush=True)
    print("Please use: .venv\\Scripts\\python.exe main.py  OR  run.bat", flush=True)
    print("------------------------------------------", flush=True)

from aiohttp import web
from dotenv import load_dotenv

print("DEBUG: Root-level main.py starting...", flush=True)

# 3. Environment setup
load_dotenv()
from core.config import prefer_ipv4_loopback  # noqa: E402
prefer_ipv4_loopback()  # ~2 s per model call otherwise (see core/config.py)

# The cloud brain (core/cloud_brain.py): when the user chose it, a cloud model stands in for the small local
# one. Decided here, once, before anything thinks; changing the choice in Settings asks for a restart.
from core import cloud_brain  # noqa: E402
cloud_brain.activate()

# We import sub-modules AFTER sys.path injection
from brain.brain_adapters import get_available_providers, get_configured_providers

# Determine port from Railway env var
ASSIGNED_PORT = int(os.environ.get("PORT", 5000))
print(f"DEBUG: Assigned Port: {ASSIGNED_PORT}", flush=True)

# Which brain will answer. Never prints any part of a key.
_ready = get_available_providers()
if _ready:
    print(f"DEBUG: Brain provider: {_ready[0]} (configured: {', '.join(get_configured_providers())})", flush=True)
else:
    print(f"CRITICAL: none of the configured brain providers ({', '.join(get_configured_providers())}) is ready.", flush=True)


# 4. Handlers
async def simple_health(request: web.Request) -> web.Response:
    """Minimal local health handler."""
    return web.Response(text="MBUDDY v4 ONLINE - Healthcheck OK")


def run_mbuddy():
    # Import handlers inside the function to keep global scope clean
    from api.auth import token_create_handler, token_list_handler, token_revoke_handler
    from api.dashboard import register_dashboard_routes
    from api.device_control import register_control_routes
    from api.status import status_handler
    from api.status_stream import status_stream_handler
    from api.websocket_server import websocket_handler
    from core.status import runtime_status
    from api.animation_editor import register_animation_editor_routes
    from core.broadcast import broadcaster

    from api.ui_api import origin_guard, register_ui_routes
    app = web.Application(middlewares=[origin_guard])

    # The discovery beacon the firmware actually speaks. An earlier mDNS
    # broadcaster (core/network_discovery.py) advertised an A record under a
    # service name, which is not a resolvable mDNS service — nothing could
    # ever have found the backend that way, so it ran at every startup for no
    # effect and was removed. This beacon answers a plain UDP probe instead,
    # confirmed working end to end.
    from core.discovery_beacon import get_beacon, local_ip_for_lan

    beacon = get_beacon(service_port=ASSIGNED_PORT)
    beacon.start()
    lan_ip = local_ip_for_lan()
    print(f"Discovery beacon: {lan_ip}:{ASSIGNED_PORT}", flush=True)
    print(f"WebSocket URL: ws://{lan_ip}:{ASSIGNED_PORT}/ws", flush=True)

    # Wire broadcaster to runtime_status
    broadcaster.set_snapshot_provider(lambda: runtime_status.snapshot())

    # Device connection-state sweep (Phase 1A). Runs for the life of the
    # process, independent of any single connection: a per-connection task
    # would die with its socket and could never notice a device that
    # disconnects and never comes back, leaving it stuck at RECONNECTING
    # forever instead of eventually reaching OFFLINE.
    async def _device_sweep_loop() -> None:
        while True:
            await asyncio.sleep(5)
            try:
                runtime_status.device_sweep()
            except Exception as exc:
                print(f"DEBUG: device_sweep failed: {exc}", flush=True)

    async def _start_device_sweep(app: web.Application) -> None:
        app["device_sweep_task"] = asyncio.create_task(_device_sweep_loop())

    async def _stop_device_sweep(app: web.Application) -> None:
        task = app.get("device_sweep_task")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.on_startup.append(_start_device_sweep)
    app.on_cleanup.append(_stop_device_sweep)

    # Due reminders. This used to live only in the Tkinter desktop app, so a
    # reminder never fired unless that window was open. The backend now
    # decides what is due and publishes it in the status stream; surfaces
    # only display it.
    async def _reminder_loop() -> None:
        from brain.task_tracker import TaskTracker, reminder_announcement
        from api.device_control import active_session
        tracker = TaskTracker()
        while True:
            try:
                fired = await asyncio.to_thread(tracker.fire_due_reminders)
                runtime_status.record_fired_reminders(fired)
                # Say it out loud too: a notification alone is easy to miss,
                # and MBUDDY is a voice companion. Only when something can
                # actually play audio (a device or the laptop voice bridge).
                session = active_session()
                speech = reminder_announcement(fired)
                from core import ui_settings
                if ui_settings.get("do_not_disturb") or ui_settings.get("work_on") == "phone":
                    speech = ""  # the notification still shows; nothing is said (and the PC stays quiet while working on the phone)
                if speech and session is not None and getattr(session, "ws", None) is not None:
                    await session._send_checked(speech, "Normal", lang="en")
            except Exception as exc:
                print(f"DEBUG: reminder check failed: {exc}", flush=True)
            await asyncio.sleep(30)

    # Finished timers. They used to expire silently; nothing told the user.
    # Checked every second (in memory, cheap) so "your pasta timer is done"
    # is on time; announced like reminders: notification + spoken.
    async def _timer_loop() -> None:
        from brain.tools import _timer_manager, timer_announcement, timer_name
        from api.device_control import active_session
        while True:
            try:
                done = _timer_manager.pop_finished()
                if done:
                    runtime_status.record_fired_reminders([
                        {"id": f"timer:{t.label}:{int(t.end_time)}", "title": f"Your {timer_name(t)} timer is done",
                         "due": "", "source": "timer"}
                        for t in done
                    ])
                    session = active_session()
                    speech = timer_announcement(done)
                    from core import ui_settings
                    if not ui_settings.get("do_not_disturb") and ui_settings.get("work_on") != "phone" and session is not None and getattr(session, "ws", None) is not None:
                        await session._send_checked(speech, "Normal", lang="en")
            except Exception as exc:
                print(f"DEBUG: timer check failed: {exc}", flush=True)
            await asyncio.sleep(1)

    async def _start_reminders(app: web.Application) -> None:
        app["reminder_task"] = asyncio.create_task(_reminder_loop())
        app["timer_task"] = asyncio.create_task(_timer_loop())

    async def _stop_reminders(app: web.Application) -> None:
        for key in ("reminder_task", "timer_task"):
            task = app.get(key)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app.on_startup.append(_start_reminders)

    # Load the brain model into the GPU as soon as the backend starts. Cold,
    # the first question timed out (30 s) while Ollama was still loading it,
    # and the user got "my brain lagged".
    async def _warm_brain(app: web.Application) -> None:
        async def warm() -> None:
            # After a reboot the tray may not start Ollama's server: start it
            # (only if nothing is listening), then load the model and read
            # the decision prompt before the first spoken turn (M2 S4).
            # Voice, memory search, hearing and brain, one after another
            # (G1: loading lazily all at once, the first turn timed out).
            from core.boot_warm import warm_everything
            await asyncio.to_thread(warm_everything)
            # Memory over time, for a leak check (U1b: 1.4 GB fresh, 2.3 GB after ~25 min).
            import psutil
            me = psutil.Process()
            while True:
                print(f"[Mem] backend RSS {me.memory_info().rss / 2**20:.0f} MB", flush=True)
                await asyncio.sleep(600)
        app["warm_task"] = asyncio.create_task(warm())

    app.on_startup.append(_warm_brain)
    app.on_cleanup.append(_stop_reminders)

    async def _free_the_model(_app):
        from brain import ollama_service
        await asyncio.to_thread(ollama_service.shutdown)       # closing MBUDDY must not leave the local model running
    app.on_cleanup.append(_free_the_model)

    # The phone gateway (api/remote_gateway.py): a second, small server for paired phones. Its own port,
    # its own token check and an allowlist; port 5000 is not exposed to anything but this PC.
    from core import notif_watch
    notif_watch.start()          # reads Windows' notification list only when the user has switched Messages on
    from core import loop_watchdog
    app.on_startup.append(loop_watchdog.start)  # if the loop ever freezes, the stacks are written to logs/freeze-*.txt
    from api import remote_gateway
    app.on_startup.append(remote_gateway.start)
    app.on_cleanup.append(remote_gateway.stop)

    # Register routes
    app.router.add_get("/",   simple_health)
    app.router.add_get("/status", status_handler)
    app.router.add_get("/status/stream", status_stream_handler)
    app.router.add_get("/ws", websocket_handler)
    register_ui_routes(app)  # the app's API: settings, overview, tasks, memory, setup checks
    from api.brain_api import register_brain_routes
    register_brain_routes(app)  # the brain: the Obsidian vault it checks every turn and learns into

    # Auth management routes (localhost only)
    app.router.add_post("/auth/token", token_create_handler)
    app.router.add_get("/auth/tokens", token_list_handler)
    app.router.add_post("/auth/revoke", token_revoke_handler)

    register_dashboard_routes(app)
    register_control_routes(app)
    register_animation_editor_routes(app)  # NEW: Animation editor

    print(f"DEBUG: Attempting to bind 0.0.0.0:{ASSIGNED_PORT} via web.run_app...", flush=True)
    print(f"DEBUG: WebSocket endpoint: ws://0.0.0.0:{ASSIGNED_PORT}/ws", flush=True)
    print(f"DEBUG: Dashboard stream: ws://0.0.0.0:{ASSIGNED_PORT}/status/stream", flush=True)
    print(f"DEBUG: Animation editor: http://0.0.0.0:{ASSIGNED_PORT}/animations", flush=True)
    print(f"DEBUG: Auth token creation: http://localhost:{ASSIGNED_PORT}/auth/token", flush=True)
    print("DEBUG: ESP32 must use the same host (PC LAN IP) and port - not an old cloudflare URL.", flush=True)

    # web.run_app is the standard high-level aiohttp entry point.
    # It handles signal management (SIGTERM) and port binding correctly.
    try:
        web.run_app(app, host="0.0.0.0", port=ASSIGNED_PORT, handle_signals=True)
    finally:
        beacon.stop()
        # Vector writes are batched, so the last few additions may not be on
        # disk yet. Without this, shutting down loses whatever was learned in
        # the final seconds — the one cost batching would otherwise carry.
        try:
            from memory.vector_store import get_vector_store

            get_vector_store().flush()
        except Exception as exc:
            print(f"DEBUG: vector flush on shutdown failed: {exc}", flush=True)


if __name__ == "__main__":
    try:
        run_mbuddy()
    except Exception as e:
        print(f"CRITICAL: App crashed: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
