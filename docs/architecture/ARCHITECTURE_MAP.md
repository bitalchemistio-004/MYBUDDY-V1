# MBUDDY Architecture Map

## Runtime Topology

```
ESP32 firmware
  <-> WebSocket /ws
  <-> optional USB serial bridge
Backend aiohttp app on :5000
  -> dashboard/control routes
  -> MBUDDYSession
  -> VAD/STT -> cognitive decision -> LLM router -> TTS/SFX -> ESP32 protocol
  -> memory JSON/SQLite/vector store
  -> runtime_status snapshot
```

## Backend Services

| Component | Entry point | Purpose | Risk |
| --- | --- | --- | --- |
| `main.py` | `run_mbuddy()` | aiohttp application bootstrap | MEDIUM |
| `api/websocket_server.py` | `/ws` | ESP32 audio, state, TTS, behavior loop | HIGH |
| `api/device_control.py` | `/control/*` | dashboard manual controls | HIGH |
| `api/dashboard.py` | `/dashboard` | browser UI | MEDIUM |
| `api/status.py` | `/status` | status JSON | LOW |
| `api/serial_bridge.py` | serial connect/send | USB fallback transport | HIGH |
| `api/tunnel_manager.py` | tunnel start/stop | cloudflared process lifecycle | CRITICAL |

## AI/Cognitive Systems

| Component | Purpose | Evidence | Risk |
| --- | --- | --- | --- |
| `brain/cognitive_state.py` | structured state and behavior decisions | simulation produced explicit reasons | MEDIUM |
| `brain/emotion_authority.py` | cognitive emotion authority validation | tests pass | LOW |
| `brain/llm_router.py` | search routing, memory injection, provider fallback | code inspected | HIGH |
| `brain/memory_context.py` | LLM system prompt builder | code inspected | MEDIUM |
| `core/state_manager.py` | energy, needs, inner life | logs show unsolicited TTS risk | HIGH |
| `core/status.py` | trace/status ring buffers | tests pass | LOW |

## Hardware/Firmware

| Component | Purpose | Evidence | Risk |
| --- | --- | --- | --- |
| `firmware/mbuddy/src/MBUDDY.ino` | ESP32 runtime, OLED, WiFi, audio | PlatformIO build pass | HIGH |
| `firmware/mbuddy/platformio.ini` | ESP32 build config | build pass | MEDIUM |
| `firmware/diagnostics/oled_diag/MBUDDY_OLED_DIAG.ino` | OLED diagnostic sketch | static only | LOW |
| `firmware/mbuddy/lib/esp32-eyes-main/*` | eye rendering library | compiled as dependency | MEDIUM |

## Routes

| Method | Path | Purpose | Validation result |
| --- | --- | --- | --- |
| GET | `/` | health | PASS |
| GET | `/status` | runtime status | PASS |
| GET | `/ws` | ESP32 WebSocket | PARTIAL, connected |
| GET | `/dashboard` | browser dashboard | PASS |
| GET | `/control/manifest` | actions/SFX/states | PASS |
| POST | `/control/action` | send OLED action | FAIL malformed JSON -> 500 |
| POST | `/control/action/clear` | clear action | PASS invalid target -> 400 |
| POST | `/control/auto_visuals` | toggle auto visuals | NOT FUZZED |
| POST | `/control/sfx` | play SFX | PASS unknown -> 400 |
| POST | `/control/state` | set display state | PASS unknown -> 400 |
| POST | `/control/energy` | set energy | FAIL nonnumeric -> 500 |
| POST | `/control/tag` | raw protocol tag | PASS invalid tag -> 400 |
| POST | `/control/say` | manual TTS | PASS empty text -> 400 |
| GET | `/control/serial/ports` | list serial ports | PASS |
| POST | `/control/serial/connect` | connect USB serial | FAIL empty port -> 500 |
| POST | `/control/serial/disconnect` | disconnect USB serial | NOT FUZZED |
| POST | `/control/tunnel/start` | start cloudflared | NOT EXECUTED, security risk |
| POST | `/control/tunnel/stop` | stop tunnel | NOT EXECUTED |
| POST | `/control/{command}` | protocol command | PASS unknown -> 400 |

## Owned File Inventory Summary

Owned source/config count excluding generated `.pio`, `.pio-core`, `.venv`, `.git`, caches: 102.

| Type | Count |
| --- | ---: |
| Python | 55 |
| Arduino/C++ owned files | 30 |
| JSON | 2 |
| Markdown | 4 |
| Config/batch/text/toml | 11 |

Largest owned files:
- `api/websocket_server.py` - 1569 lines - HIGH risk
- `api/dashboard.py` - 1101 lines - MEDIUM risk
- `api/device_control.py` - 520 lines - HIGH risk
- `brain/llm_router.py` - 430 lines - HIGH risk
- `core/state_manager.py` - 419 lines - HIGH risk
- `brain/cognitive_state.py` - 381 lines - MEDIUM risk

Generated/toolchain directories exist under `firmware/mbuddy/.pio` and `firmware/mbuddy/.pio-core`; these are excluded from owned-code scoring but should be excluded from repo commits if present.
