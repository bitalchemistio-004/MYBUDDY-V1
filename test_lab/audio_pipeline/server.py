"""
Audio-lab server: receives PCM from the mic_lab firmware, serves the web UI.

    device  --ws /ws/device-->  [ this server ]  --ws /ws/ui-->  browser
                                     |  \\-- WAV recordings, STT branch (REST)

Everything the UI shows about the device comes from the device itself: the
config it ACKed, and the chunk size / channels measured from the packets that
really arrived. The UI's *requested* settings are shown separately, so an
unconfirmed change is never presented as applied.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import WSMsgType, web

from . import analysis, beacon, stt_branch
from .listen import Listener
from .net_bench import NetTracker
from .diag import Assembler as DiagAssembler, DiagError, analyze as analyze_wiring
from .calibration import CalibrationError, Collector, Conditioner, TARGET_DB, measure, save_run
from .protocol import (DEFAULT_CONFIG, SAMPLE_RATE, Frame, ProtocolError, config_command,
                       mic_from_mask, parse_frame, parse_kv, validate_config)
from .recorder import Recorder

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
REC_DIR = HERE / "recordings"
CALIBRATION_FILE = HERE / "calibration.json"
DEFAULT_PORT = 5100
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
TICK_S = 0.05            # level/direction push rate (20 Hz)
STATE_EVERY = 5          # state push every N ticks (4 Hz)


LAB = web.AppKey("lab", "Lab")
ALLOW_LAN_UI = web.AppKey("allow_lan_ui", bool)
LOAD_STT = web.AppKey("load_stt", bool)
BEACON = web.AppKey("beacon", beacon.Beacon)
TICKER = web.AppKey("ticker", asyncio.Task)


class WireStats:
    """What was actually measured on the socket, independent of what anyone asked for."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.packets = 0
        self.dropped = 0            # sequence-number gaps
        self.bad = 0                # packets that failed protocol validation
        self.last_seq: int | None = None
        self.chunk_frames: int | None = None
        self.mask: int | None = None
        self.packet_bytes: int | None = None
        self._recent: deque[tuple[float, int]] = deque()

    def note(self, frame: Frame, nbytes: int, now: float) -> None:
        if self.last_seq is not None:
            gap = (frame.seq - self.last_seq - 1) & 0xFFFF
            if 0 < gap < 1000:      # a huge jump is a device reboot/reset, not loss
                self.dropped += gap
        self.last_seq = frame.seq
        self.packets += 1
        self.chunk_frames, self.mask, self.packet_bytes = frame.frames, frame.mask, nbytes
        self._recent.append((now, nbytes))
        while self._recent and now - self._recent[0][0] > 2.0:
            self._recent.popleft()

    def snapshot(self, now: float) -> dict:
        recent = [r for r in self._recent if now - r[0] <= 2.0]
        span = (recent[-1][0] - recent[0][0]) if len(recent) > 1 else 0
        streaming = bool(recent) and now - recent[-1][0] < 1.0
        return {
            "streaming": streaming,
            "chunk_frames": self.chunk_frames if streaming else None,
            "mic": mic_from_mask(self.mask) if streaming and self.mask else None,
            "packet_bytes": self.packet_bytes if streaming else None,
            "packets_per_s": round((len(recent) - 1) / span, 1) if streaming and span > 0 else None,
            "kbps": round(sum(r[1] for r in recent) * 8 / 1000 / max(span, 0.001), 1) if streaming and span > 0 else None,
            "packet_ms": round(self.chunk_frames * 1000 / SAMPLE_RATE, 1) if streaming and self.chunk_frames else None,
            "packets": self.packets, "dropped": self.dropped, "bad": self.bad,
        }


class Lab:
    def __init__(self, rec_dir: Path = REC_DIR, calibration_file: Path | None = CALIBRATION_FILE,
                 quiet_s: float = 2.0, sound_s: float = 6.0) -> None:
        self.left, self.right = analysis.Ring(4096), analysis.Ring(4096)
        self.recorder = Recorder(rec_dir)
        self.cond = Conditioner.load(calibration_file)
        self._calibration_file = calibration_file
        self.collector = Collector(quiet_s, sound_s)
        self._calibration_task: asyncio.Task | None = None
        self.wire = WireStats()
        self.requested = dict(DEFAULT_CONFIG)
        self.applied: dict | None = None
        self.device: dict | None = None
        self.device_ws: web.WebSocketResponse | None = None
        self.ui: set[web.WebSocketResponse] = set()
        self.listeners: set[Listener] = set()
        self.taps: list = []                         # callables(frame, now) that see the RAW frames (benches use these)
        self.net = NetTracker()
        self.connect_count = 0
        self.benches: dict = {}
        self.spk_done = asyncio.Event()              # set when the device reports a speaker stream has finished playing
        self.spk_last: dict = {}
        self.diag: dict = {"phase": "idle", "message": None, "result": None, "quiet": False, "runs": {}}
        self.diag_timeout_s = 6.0
        self._diag_asm = DiagAssembler()
        self._diag_task: asyncio.Task | None = None
        self._diag_file = calibration_file.with_name("diag_last_capture.npy") if calibration_file else None
        self.error: str | None = None
        self.beacon_error: str | None = None

    # ── device side ───────────────────────────────────────────────────
    def attach_device(self, ws: web.WebSocketResponse, ip: str | None) -> None:
        self.device_ws = ws
        self.connect_count += 1
        self.net.break_link()
        self.device = {"ip": ip, "connected_at": time.time()}
        self.applied = None
        self.wire.reset()
        self.left.clear()
        self.right.clear()
        self.error = None

    def detach_device(self, ws: web.WebSocketResponse) -> None:
        if self.device_ws is ws:                   # ignore a stale socket closing after a reconnect
            self.device_ws, self.device, self.applied = None, None, None
            if self.recorder.active:
                self.stop_recording()

    def on_binary(self, data: bytes) -> None:
        if data[:2] == b"DG":                        # wiring-diagnostic chunk, not audio
            self._on_diag_chunk(data)
            return
        now = time.monotonic()
        try:
            frame = parse_frame(data)
        except ProtocolError as exc:
            self.wire.bad += 1
            self.error = f"bad packet from device: {exc}"
            return
        self.wire.note(frame, len(data), now)
        self.net.note(now)
        for tap in self.taps:
            tap(frame, now)
        self.collector.feed(frame)                 # calibration measures the RAW samples...
        frame = self.cond.apply(frame)             # ...everything downstream hears the conditioned ones
        if frame.left is not None:
            self.left.push(frame.left, now)
        if frame.right is not None:
            self.right.push(frame.right, now)
        self.recorder.write(frame, {**(self.applied or self.requested), "conditioner": self.cond.as_dict()}, self.wire.dropped)
        if self.listeners:
            pcm = frame.interleaved().tobytes()
            for listener in self.listeners:
                listener.push(frame.channels, pcm)

    def on_text(self, text: str) -> None:
        verb = text.split(" ", 1)[0]
        kv = parse_kv(text)
        if verb == "HELLO":
            self.device = {**(self.device or {}), **kv}
        elif verb == "ACK":
            try:
                self.applied = {"chunk": int(kv["chunk"]), "mic": kv["mic"],
                                "gain": float(kv["gain"]), "swap": int(kv["swap"])}
                self.error = None
            except (KeyError, ValueError):
                self.error = f"malformed ACK from device: {text!r}"
        elif verb == "NAK":
            self.error = f"device rejected the config: {text[4:]}"
        elif verb == "STAT" and self.device is not None:
            self.device.update(kv)
        elif verb == "SPKDONE":
            self.spk_last = kv
            self.spk_done.set()

    # ── wiring diagnostic (raw I2S words) ─────────────────────────────
    async def request_diag(self, quiet: bool = False) -> str | None:
        if self.device_ws is None or self.device_ws.closed:
            return "No device connected."
        if self.diag["phase"] == "capturing":
            return "A capture is already running."
        self._diag_asm.reset()
        self.diag = {"phase": "capturing", "message": None, "result": None, "quiet": quiet, "runs": self.diag.get("runs", {})}
        await self.device_ws.send_str("DIAG quiet" if quiet else "DIAG")
        self._diag_task = asyncio.ensure_future(self._diag_expire())
        return None

    async def _diag_expire(self) -> None:
        await asyncio.sleep(self.diag_timeout_s)
        if self.diag["phase"] == "capturing":
            self.diag = {**self.diag, "phase": "failed", "result": None,
                         "message": "The device did not answer. The wiring diagnostic needs the newer test firmware "
                                    "(fw mic_lab/2, and mic_lab/3 for the 'Wi-Fi paused' capture): re-flash firmware/mic_lab and try again."}

    def _on_diag_chunk(self, data: bytes) -> None:
        try:
            words = self._diag_asm.add(data)
            if words is None or self.diag["phase"] != "capturing":
                return
            result = analyze_wiring(words)
        except DiagError as exc:
            self.diag = {**self.diag, "phase": "failed", "result": None, "message": f"Bad diagnostic data from the device: {exc}"}
            return
        if self._diag_task:
            self._diag_task.cancel()
        if self._diag_file is not None:
            import numpy as np
            np.save(self._diag_file, words)          # keep the raw words for offline inspection
        runs = dict(self.diag.get("runs", {}))
        runs["quiet" if self.diag.get("quiet") else "normal"] = {
            "at": time.time(), "slots": [{k: sl[k] for k in ("periodic_pct", "periodic_chance_pct", "noise_dbfs", "big_pct")} for sl in result["slots"]]}
        self.diag = {**self.diag, "phase": "done", "message": None, "result": result, "at": time.time(), "runs": runs}

    async def bench_action(self, name: str, action: str, params: dict) -> str | None:
        bench = self.benches.get(name)
        if bench is None:
            return f"Unknown bench {name!r}."
        if action == "cancel":
            await bench.cancel()
            return None
        return await bench.action(action, params)

    async def push_config(self) -> None:
        if self.device_ws is not None and not self.device_ws.closed:
            await self.device_ws.send_str(config_command(self.requested))

    # ── UI commands ───────────────────────────────────────────────────
    async def set_config(self, raw: dict) -> str | None:
        """Returns an error string, or None if the request was accepted and sent."""
        if self.recorder.active:
            return "Stop the recording before changing the configuration."
        if self.collector.running:
            return "A calibration is running — wait for it to finish before changing the configuration."
        try:
            self.requested = validate_config({**self.requested, **raw})
        except ProtocolError as exc:
            return str(exc)
        await self.push_config()
        return None

    def start_recording(self) -> str | None:
        if self.device_ws is None:
            return "No device connected."
        if not self.wire.snapshot(time.monotonic())["streaming"]:
            return "The device is connected but not streaming audio."
        if self.collector.running:
            return "A calibration is running — wait for it to finish."
        self.recorder.start(self.wire.dropped)
        return None

    # ── calibration ───────────────────────────────────────────────────
    def start_calibration(self, apply: bool) -> str | None:
        snap = self.wire.snapshot(time.monotonic())
        if self.device_ws is None or not snap["streaming"]:
            return "No audio is streaming from the device."
        if snap["mic"] != "both":
            return "Select 'Both' first: calibration compares the two microphones."
        if self.recorder.active:
            return "Stop the recording before calibrating."
        if self.collector.running:
            return "A calibration is already running."
        self.collector.begin(apply, time.monotonic())
        self._calibration_task = asyncio.ensure_future(self._run_calibration())
        return None

    def cancel_calibration(self) -> None:
        if self._calibration_task and not self._calibration_task.done():
            self._calibration_task.cancel()
        self.collector.reset()

    def set_trim(self, left_db, right_db) -> str | None:
        if self.collector.running:
            return "A calibration is running — wait for it to finish."
        try:
            self.cond.set_trims(float(left_db), float(right_db))
        except (CalibrationError, TypeError, ValueError) as exc:
            return str(exc)
        return None

    def set_gate(self, enabled) -> str | None:
        if self.collector.running:
            return "A calibration is running — wait for it to finish."
        self.cond.set_gate(bool(enabled))
        return None

    async def _run_calibration(self) -> None:
        c = self.collector
        try:
            await asyncio.sleep(c.quiet_s)
            c.enter_sound(time.monotonic())
            await asyncio.sleep(c.sound_s)
            c.phase = "processing"
            ql, qr, sl, sr = c.arrays()
            result = await asyncio.to_thread(measure, ql, qr, sl, sr, 0.0, 0.0, self.cond, self.cond.gate_enabled)
            if self._calibration_file is not None:       # keep the raw capture so a result can be checked against real audio
                await asyncio.to_thread(save_run, self._calibration_file.with_name("calibration_last_run.wav"), ql, qr, sl, sr)
            if c.apply:
                t = result["proposed_trim_db"]
                self.cond.set_trims(t["left"], t["right"])
                self.cond.set_noise(result["noise_rms_raw"])
                result["applied"] = True
            c.result, c.phase = result, "done"
        except CalibrationError as exc:
            c.message, c.phase = str(exc), "failed"
        except asyncio.CancelledError:
            raise
        except Exception as exc:                   # never leave the UI stuck in "processing"
            c.message, c.phase = f"calibration failed: {type(exc).__name__}: {exc}", "failed"

    def stop_recording(self) -> dict | None:
        return self.recorder.stop()

    # ── snapshots for the UI ──────────────────────────────────────────
    def state(self) -> dict:
        now = time.monotonic()
        return {
            "type": "state",
            "device": None if self.device is None else {**self.device, "connected": True},
            "requested": self.requested,
            "applied": self.applied,
            "wire": self.wire.snapshot(now),
            "recording": {"active": self.recorder.active,
                          "waiting": self.recorder.waiting_for_audio,
                          "seconds": round(self.recorder.seconds, 1)},
            "calibration": {"settings": self.cond.as_dict(), "phase": self.collector.phase,
                            "apply": self.collector.apply, "message": self.collector.message,
                            "result": self.collector.result, "target_db": TARGET_DB,
                            "remaining_s": self._remaining(now)},
            "diag": self.diag,
            "stt": stt_branch.status(),
            "error": self.error,
            "beacon_error": self.beacon_error,
        }

    def _remaining(self, now: float) -> float | None:
        r = self.collector.remaining(now)
        return None if r is None else round(r, 1)

    def levels(self) -> dict:
        now = time.monotonic()
        return {"type": "levels", "levels": analysis.levels(self.left, self.right, now),
                "direction": analysis.direction(self.left, self.right, now)}

    async def broadcast(self, payload: dict) -> None:
        if not self.ui:
            return
        text = json.dumps(payload)
        await asyncio.gather(*(c.send_str(text) for c in list(self.ui) if not c.closed), return_exceptions=True)


# ── HTTP / WebSocket handlers ─────────────────────────────────────────
@web.middleware
async def guard(request: web.Request, handler):
    """
    Only the device socket is open to the LAN. The UI and REST endpoints expose
    a live microphone, so they answer loopback only (unless --allow-lan-ui) and
    refuse cross-origin requests from other web pages.
    """
    if request.path != "/ws/device":
        if not request.app[ALLOW_LAN_UI] and request.remote not in LOOPBACK:
            raise web.HTTPForbidden(text="the audio lab UI is loopback-only")
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            raise web.HTTPForbidden(text="cross-origin request refused")
    response = await handler(request)
    response.headers["Cache-Control"] = "no-store"
    return response


async def index(_: web.Request) -> web.Response:
    return web.FileResponse(WEB_DIR / "index.html")


async def bench_page(_: web.Request) -> web.Response:
    return web.FileResponse(WEB_DIR / "bench.html")


async def device_socket(request: web.Request) -> web.WebSocketResponse:
    lab: Lab = request.app[LAB]
    ws = web.WebSocketResponse(heartbeat=15, max_msg_size=1 << 20)
    await ws.prepare(request)
    if lab.device_ws is not None and not lab.device_ws.closed:
        await lab.device_ws.close()                # newest connection wins
    lab.attach_device(ws, request.remote)
    await lab.push_config()
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                lab.on_binary(msg.data)
            elif msg.type == WSMsgType.TEXT:
                lab.on_text(msg.data)
    finally:
        lab.detach_device(ws)
    return ws


async def ui_socket(request: web.Request) -> web.WebSocketResponse:
    lab: Lab = request.app[LAB]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    lab.ui.add(ws)
    await ws.send_json(lab.state())
    await ws.send_json({"type": "recordings", "items": lab.recorder.list()})
    for bench in lab.benches.values():
        await ws.send_json({"type": "bench", "name": bench.name, "state": bench.state})
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                cmd = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            err = None
            kind = cmd.get("type")
            if kind == "set_config":
                err = await lab.set_config(cmd.get("config") or {})
            elif kind == "calibrate":
                err = lab.start_calibration(bool(cmd.get("apply", True)))
            elif kind == "calibrate_cancel":
                lab.cancel_calibration()
            elif kind == "bench":
                err = await lab.bench_action(str(cmd.get("name")), str(cmd.get("action")), cmd.get("params") or {})
            elif kind == "diag_capture":
                err = await lab.request_diag(bool(cmd.get("quiet", False)))
            elif kind == "set_gate":
                err = lab.set_gate(cmd.get("enabled", True))
            elif kind == "set_trim":
                err = lab.set_trim(cmd.get("left_db", 0), cmd.get("right_db", 0))
            elif kind == "rec_start":
                err = lab.start_recording()
            elif kind == "rec_stop":
                lab.stop_recording()
                await lab.broadcast({"type": "recordings", "items": lab.recorder.list()})
            if err:
                await ws.send_json({"type": "notice", "level": "error", "text": err})
            await lab.broadcast(lab.state())
    finally:
        lab.ui.discard(ws)
    return ws


LISTEN_FLUSH_S = 0.04        # batch ~40 ms of audio per message


async def listen_socket(request: web.Request) -> web.WebSocketResponse:
    """Live audio for the browser's Listen button (loopback-only like the rest of the UI)."""
    lab: Lab = request.app[LAB]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 16)
    await ws.prepare(request)
    listener = Listener()
    lab.listeners.add(listener)
    await ws.send_json({"type": "hello", "rate": SAMPLE_RATE})

    async def sender() -> None:
        while not ws.closed:
            await asyncio.sleep(LISTEN_FLUSH_S)
            for message in listener.take():
                await ws.send_bytes(message)

    task = asyncio.create_task(sender())
    try:
        async for _ in ws:                          # the browser sends nothing; this just notices a close
            pass
    finally:
        task.cancel()
        lab.listeners.discard(listener)
    return ws


async def recordings_file(request: web.Request) -> web.StreamResponse:
    path = request.app[LAB].recorder.path_for(request.match_info["name"])
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "audio/wav"})


async def transcribe(request: web.Request) -> web.Response:
    lab: Lab = request.app[LAB]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid JSON"}, status=400)
    path = lab.recorder.path_for(str(body.get("name", "")))
    if path is None:
        return web.json_response({"error": "no such recording"}, status=404)
    try:
        result = await stt_branch.transcribe(
            path, str(body.get("source", "auto")), str(body.get("language", "auto")), bool(body.get("dsp", True)))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:                        # surface the real failure to the UI
        return web.json_response({"error": f"{type(exc).__name__}: {exc}"}, status=502)
    return web.json_response(result)


async def ticker(app: web.Application) -> None:
    lab: Lab = app[LAB]
    n = 0
    while True:
        await asyncio.sleep(TICK_S)
        n += 1
        if lab.ui:
            try:
                await lab.broadcast(lab.levels())
                if n % STATE_EVERY == 0:
                    await lab.broadcast(lab.state())
            except Exception as exc:                # one bad payload must not silently freeze every UI
                if n % 100 == 0 or lab.error is None:
                    lab.error = f"UI update failed: {type(exc).__name__}: {exc}"


async def on_startup(app: web.Application) -> None:
    app[TICKER] = asyncio.create_task(ticker(app))
    if app[LOAD_STT]:
        stt_branch.start_loading()


async def on_cleanup(app: web.Application) -> None:
    app[TICKER].cancel()
    app[LAB].cancel_calibration()
    for bench in app[LAB].benches.values():
        await bench.cancel()
    if app[LAB]._diag_task:
        app[LAB]._diag_task.cancel()
    app[BEACON].stop()
    lab: Lab = app[LAB]
    if lab.recorder.active:
        lab.stop_recording()


def make_app(port: int = DEFAULT_PORT, allow_lan_ui: bool = False, rec_dir: Path = REC_DIR,
             start_beacon: bool = True, load_stt: bool = True, calibration_file: Path | None = CALIBRATION_FILE,
             calib_quiet_s: float = 2.0, calib_sound_s: float = 6.0) -> web.Application:
    app = web.Application(middlewares=[guard])
    app[LAB] = lab = Lab(rec_dir, calibration_file, calib_quiet_s, calib_sound_s)
    from .benches import build_benches
    lab.benches = build_benches(lab)
    app[ALLOW_LAN_UI] = allow_lan_ui
    app[LOAD_STT] = load_stt
    app[BEACON] = bc = beacon.Beacon(port)
    if start_beacon:
        bc.start()
        lab.beacon_error = bc.error
    app.add_routes([
        web.get("/", index),
        web.get("/benches", bench_page),
        web.get("/ws/device", device_socket),
        web.get("/ws/ui", ui_socket),
        web.get("/ws/listen", listen_socket),
        web.get("/recordings/{name}", recordings_file),
        web.post("/api/transcribe", transcribe),
        web.static("/static", WEB_DIR),
    ])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="MBUDDY audio-pipeline test bench")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--allow-lan-ui", action="store_true",
                        help="let other machines open the UI (it exposes a live microphone; default is loopback only)")
    args = parser.parse_args()
    app = make_app(args.port, args.allow_lan_ui)
    print(f"\n  MBUDDY audio lab\n  UI:      http://localhost:{args.port}/\n"
          f"  Device:  ws://{beacon.lan_ip()}:{args.port}/ws/device  (discovery UDP :{beacon.BEACON_PORT})\n")
    if app[LAB].beacon_error:
        print(f"  WARNING: {app['lab'].beacon_error}\n")
    web.run_app(app, host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()
