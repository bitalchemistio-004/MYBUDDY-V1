# MBUDDY API Reference

**Version:** 4.1.0-ENHANCED  
**Base URL:** `http://<server-ip>:5000`  
**Last Updated:** August 13, 2026

---

## 📡 WebSocket Endpoints

### Device Connection (ESP32)
**Endpoint:** `ws://<server-ip>:5000/ws`  
**Protocol:** `arduino` (subprotocol)  
**Purpose:** Primary ESP32 ↔ Server communication

#### Client → Server Messages

**Audio Data (Binary)**
```
Binary frames containing 16-bit PCM audio @ 16kHz
Frame size: 512-1024 bytes typically
```

**Text Commands**
```
[BARGE]           - User interrupt during TTS
[PLAYBACK_DONE]   - Firmware finished playing audio
keep-alive        - Connection health check
```

#### Server → Client Messages

**Text Commands**
```
[THINKING]                                 - Show thinking animation
[SPEAKING:emotion:text]                    - About to speak
[QUIET]                                    - Resume listening
[STOP]                                     - Stop current playback
[STATE:emotion]                            - Set emotional state
[ENERGY:level]                             - Update energy (0-100)
[ACT:name:duration_ms]                     - Play animation
[ACT:clear]                                - Clear animation
[HEARD:text]                               - Show heard text
[INNER:text]                               - Inner monologue
[SFX:name]                                 - Play sound effect
[TIME]                                     - Show clock
[VISUAL:shape:duration:intensity]          - Mouth shape (lip-sync)
```

**Binary Audio (PCM)**
```
16-bit signed PCM @ 16kHz
Streamed in chunks during TTS playback
```

---

### Dashboard Status Stream
**Endpoint:** `ws://<server-ip>:5000/status/stream`  
**Purpose:** Real-time status updates for dashboards

#### Server → Client Messages

**Status Snapshot (JSON)**
```json
{
  "started_at": 1723564856.875,
  "device_connected": true,
  "mode": "normal",
  "awake": true,
  "emotion": "Happy",
  "energy": 65.5,
  "listening": false,
  "speaking": true,
  "thinking": false,
  "last_heard": "what's the weather?",
  "last_reply": "I don't have access to weather data yet.",
  "last_error": null,
  "memory_count": 42,
  "needs": {"social": 75, "novelty": 45},
  "world_model": {...},
  "behavior_decision": {...},
  "emotion_decision": {...}
}
```

**Update Frequency:** Debounced to 40ms (max 25 updates/second)

---

## 🔌 HTTP Endpoints

### Health Check
```http
GET /
```

**Response:**
```
MBUDDY v4 ONLINE - Healthcheck OK
```

---

### Status Snapshot
```http
GET /status
```

**Response:** JSON status object (same as WebSocket stream)

**Example:**
```bash
curl http://localhost:5000/status | jq .
```

---

### Dashboard
```http
GET /dashboard
```

**Response:** HTML dashboard interface

**Features:**
- Live OLED preview canvas
- Real-time status monitoring
- Animation controls
- Hardware integration (serial, tunnel)

---

## 🎮 Control API

### Action Control

#### Trigger Animation
```http
POST /control/action
Content-Type: application/json

{
  "name": "dragon_fire",
  "duration_ms": 2200,
  "target": "auto"
}
```

**Parameters:**
- `name` (string, required): Animation name
- `duration_ms` (int, optional): Duration in milliseconds (default: 2200)
- `target` (string, optional): "auto", "wireless", "serial", or "both"

**Available Animations:**
```
joystick, interactive_arena, dragon_fire, data_weaver,
pixel_cascade, heartbeat, thought_bubble, block_break,
starlight, clock_timer, wifi_search, battery_charge,
error_glitch, scanning_targeting, dreaming, celebration,
listening_animation
```

**Response:**
```json
{
  "ok": true,
  "action": "dragon_fire",
  "thought": "I am showing this as a quiet visual state."
}
```

#### Clear Animation
```http
POST /control/action/clear
Content-Type: application/json

{
  "target": "auto"
}
```

---

### Sound Effects

#### Play SFX
```http
POST /control/sfx
Content-Type: application/json

{
  "name": "laser_pew",
  "target": "auto"
}
```

**Available SFX:**
```
laser_pew, data_blip, dragon_breath, cascade_arp,
ecg_beep, bubble_pop, brick_hit, star_tinkle,
clock_tick, sonar_ping, charge_hum, glitch_screech,
success_chime, listen_ping, sleep_dream
```

---

### State Control

#### Set Emotional State
```http
POST /control/state
Content-Type: application/json

{
  "state": "Happy",
  "target": "auto"
}
```

**Available States:**
```
Normal, Happy, Glee, Sad, Worried, Sleepy, Focused,
Skeptic, Suspicious, Squint, Annoyed, Frustrated,
Unimpressed, Angry, Furious, Scared, Surprised, Awe, Curious
```

#### Set Energy Level
```http
POST /control/energy
Content-Type: application/json

{
  "level": 75,
  "target": "auto"
}
```

**Parameters:**
- `level` (int, required): Energy 0-100

---

### Speech Control

#### Text-to-Speech
```http
POST /control/say
Content-Type: application/json

{
  "text": "Hello, how are you?",
  "state": "Happy",
  "lang": "en",
  "target": "auto"
}
```

**Parameters:**
- `text` (string, required): Text to speak
- `state` (string, optional): Emotional state (default: "Normal")
- `lang` (string, optional): Language code "en" or "ar" (default: "en")
- `target` (string, optional): Delivery target

---

### Protocol Commands

#### Thinking State
```http
POST /control/thinking
Content-Type: application/json

{
  "target": "auto"
}
```

#### Heard Acknowledgment
```http
POST /control/heard
Content-Type: application/json

{
  "target": "auto"
}
```

#### Quiet State
```http
POST /control/quiet
Content-Type: application/json

{
  "target": "auto"
}
```

#### Stop Playback
```http
POST /control/stop
Content-Type: application/json

{
  "target": "auto"
}
```

#### Send Raw Tag
```http
POST /control/tag
Content-Type: application/json

{
  "tag": "[ACT:starlight:3000]",
  "target": "auto"
}
```

---

### Auto Visuals

#### Toggle Auto Visuals
```http
POST /control/auto_visuals
Content-Type: application/json

{
  "enabled": true,
  "target": "auto"
}
```

**Response:**
```json
{
  "ok": true,
  "auto_visuals": true
}
```

---

## 🔧 Hardware Integration

### Serial Bridge

#### List Available Ports
```http
GET /control/serial/ports
```

**Response:**
```json
{
  "ports": [
    {
      "device": "COM5",
      "description": "USB Serial Device (COM5)",
      "hwid": "USB VID:PID=10C4:EA60"
    }
  ],
  "serial": {
    "connected": false,
    "port": null,
    "baud": 115200
  }
}
```

#### Connect Serial
```http
POST /control/serial/connect
Content-Type: application/json

{
  "port": "COM5",
  "baud": 115200
}
```

#### Disconnect Serial
```http
POST /control/serial/disconnect
Content-Type: application/json

{}
```

---

### Tunnel Manager

#### Start Tunnel
```http
POST /control/tunnel/start
Content-Type: application/json

{
  "url": "http://127.0.0.1:5000"
}
```

**Response:**
```json
{
  "ok": true,
  "tunnel": {
    "running": true,
    "url": "https://abc123.trycloudflare.com",
    "logs": ["Tunnel started..."]
  }
}
```

#### Stop Tunnel
```http
POST /control/tunnel/stop
Content-Type: application/json

{}
```

---

### Manifest

#### Get Available Actions/SFX
```http
GET /control/manifest
```

**Response:**
```json
{
  "actions": ["joystick", "dragon_fire", ...],
  "sfx": ["laser_pew", "data_blip", ...],
  "states": ["Normal", "Happy", ...],
  "auto_visuals": true
}
```

---

## 📊 Status Object Schema

### RuntimeStatus
```typescript
interface RuntimeStatus {
  // System
  started_at: number;
  uptime_s: number;
  device_connected: boolean;
  mode: string;              // "normal", "limited", "offline"
  mode_reason: string;
  remote: string | null;     // ESP32 IP
  last_ws_seen_at: number;
  last_ws_age_s: number | null;

  // State
  awake: boolean;
  emotion: string;
  energy: number;            // 0-100
  needs: Record<string, number>;

  // Activity
  listening: boolean;
  speaking: boolean;
  thinking: boolean;
  barge_armed: boolean;

  // Interaction
  last_heard: string;
  last_reply: string;
  last_rms: number;

  // Actions
  current_action: string;
  current_action_thought: string;
  last_sfx: string;

  // Health
  last_error: string | null;
  last_stt_ok: boolean;
  last_llm_ok: boolean;
  last_tts_ok: boolean;

  // Memory
  memory_count: number;
  inner_count: number;
  daily_stats: object;
  active_project_thread: string;
  pending_reminders_count: number;

  // Models
  world_model: object;
  cognitive_state: object;
  behavior_decision: object;
  emotion_decision: object;

  // Audit
  behavior_traces: object[];
  emotion_disagreements: object[];
  recent_actions: string[];
  recent_emotions: string[];
  recent_sfx: string[];
  recent_animations: string[];
}
```

---

## 🔐 Authentication

**Current Version:** No authentication

**Security Recommendations:**
- Use firewall rules to restrict access
- Deploy behind reverse proxy (nginx/Caddy) with Basic Auth
- Never expose directly to internet without authentication

---

## ⚡ Rate Limits

**Internal Limits:**
- WebSocket broadcast: Debounced to 40ms (25 Hz max)
- LLM requests: 25s timeout per request
- Audio chunks: 16kHz × 16-bit = ~32KB/s

**External Limits:**
- Groq API: Free tier rate limits apply
- Edge-TTS: No official rate limit (respectful use recommended)

---

## 🐛 Error Codes

### HTTP Status Codes
- `200 OK` - Success
- `400 Bad Request` - Invalid parameters
- `404 Not Found` - Endpoint doesn't exist
- `500 Internal Server Error` - Server-side error

### WebSocket Close Codes
- `1000` - Normal closure
- `1001` - Going away (server shutdown)
- `1006` - Abnormal closure (connection lost)

---

## 📝 Examples

### Python Client
```python
import asyncio
import aiohttp
import json

async def monitor_status():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('ws://localhost:5000/status/stream') as ws:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    status = json.loads(msg.data)
                    print(f"Emotion: {status['emotion']}, Energy: {status['energy']}")

asyncio.run(monitor_status())
```

### JavaScript Client
```javascript
const ws = new WebSocket('ws://localhost:5000/status/stream');

ws.onmessage = (event) => {
  const status = JSON.parse(event.data);
  console.log(`${status.emotion} @ ${status.energy}%`);
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};
```

### cURL Examples
```bash
# Get status
curl http://localhost:5000/status | jq .

# Trigger animation
curl -X POST http://localhost:5000/control/action \
  -H "Content-Type: application/json" \
  -d '{"name":"dragon_fire","duration_ms":2500}'

# Speak text
curl -X POST http://localhost:5000/control/say \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello world","state":"Happy","lang":"en"}'

# Set energy
curl -X POST http://localhost:5000/control/energy \
  -H "Content-Type: application/json" \
  -d '{"level":80}'
```

---

## 📚 Additional Resources

- **Setup Guide:** `SETUP.md`
- **Implementation Details:** `IMPLEMENTATION_SUMMARY.md`
- **Improvement Plan:** `IMPROVEMENT_PLAN.md`
- **PowerPoint Guide:** `MBUDDY_GUIDE.pptx`

---

**API Version:** 4.1.0  
**Protocol Version:** arduino/v1  
**Last Updated:** August 13, 2026
