/*
 * ============================================================
 *  MBUDDY — ESP32 Production Sketch
 *  Library: https://github.com/playfultechnology/esp32-eyes
 *  ✅ NO LIBRARY EDITS REQUIRED — used exactly as published
 *
 *  REQUIRED LIBRARIES (Arduino Library Manager):
 *    - U8g2          (by olikraus)
 *    - WebSockets    (by Markus Sattler, v2.3.5+)
 *    - WiFiManager   (by tzapu, v2.0.16+)
 *
 *  HARDWARE:
 *    - ESP32-S3 Dev Module (GPIO26-32 reserved for flash/PSRAM on this
 *      chip — every pin below was deliberately chosen clear of that range)
 *    - SH1106 VCOMH0 OLED 128x64 (I2C: SDA=8, SCL=9, Addr=0x3C/0x3D)
 *    - 2x INMP441 I2S Mic, stereo pair sharing one bus (WS=17, SD=18,
 *      SCK=16): left mic's L/R pin tied to GND (left slot), right mic's
 *      L/R pin tied to 3.3V (right slot)
 *    - MAX98357A I2S amp (LRC=4, BCLK=5, DIN=6) — SD pin must be tied
 *      high (>1.4V, e.g. to VIN) to select left-channel-only output,
 *      matching this firmware's mono I2S format; floating/default wiring
 *      mixes in the undefined right slot and corrupts the audio.
 *
 *  ARCHITECTURE:
 *    👁️ Autonomic: face->Update() handles idle blink/look + render
 *    ⚡ Reflex: disabled for now while speech path is being tuned
 *    🧠 Brain: WebSocket → emotion tags from Python AI
 *
 *  EMOTION PROTOCOL (Python → ESP32):
 *    [STATE:Normal] [STATE:Angry] [STATE:Glee] [STATE:Happy]
 *    [STATE:Sad] [STATE:Worried] [STATE:Focused] [STATE:Annoyed]
 *    [STATE:Surprised] [STATE:Skeptic] [STATE:Frustrated]
 *    [STATE:Unimpressed] [STATE:Sleepy] [STATE:Suspicious]
 *    [STATE:Squint] [STATE:Furious] [STATE:Scared] [STATE:Awe]
 *    [HEARD] [QUIET]
 *
 *  LICENSE: MIT | Author: BITALCHEMIST | Project: MBUDDY
 * ============================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <WiFiManager.h>
#include <WiFiUdp.h>
#include <Preferences.h>
#include <driver/i2s.h>
#include <freertos/ringbuf.h>
#include <freertos/semphr.h>
#include <Wire.h>
#include <ArduinoOTA.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// esp32-eyes library files (copy to sketch folder, DO NOT EDIT)
#include "Face.h"
#include "FaceEmotions.hpp"

// ── HARDWARE PINS ─────────────────────────────────────────
// ESP32-S3: GPIO21/22 aren't the classic ESP32's I2C default here, and
// GPIO26-32 are reserved for the S3's own SPI flash/PSRAM (using them for
// anything else corrupts flash access, not just "doesn't work") — so the
// OLED and speaker pins moved off both. Mic pins (16/17/18) were already
// clear of every S3-reserved range and are unchanged.
// OLED I2C: SDA=8, SCL=9 (matches the ESP32-S3 Arduino core's own I2C default)
#define OLED_SDA 8
#define OLED_SCL 9
#define I2S_WS 17
#define I2S_SD 18
#define I2S_SCK 16
#define I2S_PORT_MIC I2S_NUM_1
#define I2S_PORT_SPK I2S_NUM_0
#define DEBUG_LED 2
#define I2S_SPK_LRC 4
#define I2S_SPK_BCLK 5
#define I2S_SPK_DIN 6

// ── MBUDDY CORE ENDPOINT (pick ONE) ─────────────────────
// The firmware only needs to know the Core's address, not how traffic gets
// there — a tunnel is just whatever transport reaches it today. Phase 1C
// is what makes this endpoint runtime-configurable (WiFiManager + NVS,
// no reflash); until then it's still compiled in, same as before.
// LOCAL: Python running on this PC (run.bat) — set your PC's WiFi IPv4 below.
// REMOTE: any reachable Core endpoint (currently a Cloudflare tunnel for
// testing, but nothing below is Cloudflare-specific) → set
// USE_LOCAL_BRAIN false and paste the host.
#define USE_LOCAL_BRAIN true
#if USE_LOCAL_BRAIN
  #define MBUDDY_CORE_HOST "10.67.69.172" // Active PC IPv4 address
  #define MBUDDY_CORE_PORT 5000
#else
  #define MBUDDY_CORE_HOST "scripts-returned-judges-prior.trycloudflare.com"  // from: cloudflared tunnel --url http://127.0.0.1:5000
  #define MBUDDY_CORE_PORT 443
#endif
#define WS_PATH "/ws"
#define WS_RECONNECT_MS 10000

// ── Phase 1B: HELLO handshake / device auth ─────────────
// Shared secret must match core/config.py's MBUDDY_DEVICE_TOKEN on the
// backend exactly (same convention as MBUDDY_OTA_PASSWORD below: one value
// kept identical on both sides). Rotate both together before ever exposing
// /ws outside a trusted LAN.
#ifndef MBUDDY_DEVICE_TOKEN
// Phase 1D: rotated off the shipped default before exposing /ws over a real
// Cloudflare tunnel — must match .env's MBUDDY_DEVICE_TOKEN on the backend
// exactly. Rotate both together again before/after this test.
#define MBUDDY_DEVICE_TOKEN "dXJv3OaOMzKoKL_wOYw8cZtJfYFlUHTH"
#endif
#define MBUDDY_FIRMWARE_VERSION "1.0.0"

// ── Backend discovery ───────────────────────────────────────────────
// The compiled-in MBUDDY_CORE_HOST is only a fallback now. The backend answers a UDP
// probe on this port with its current address, so a changed DHCP lease no
// longer strands the device.
#define DISCOVERY_PORT 18830
#define DISCOVERY_PROBE "MBUDDY-DISCOVER?"
#define DISCOVERY_REPLY_PREFIX "MBUDDY-HERE"
#define DISCOVERY_TIMEOUT_MS 1200
#define DISCOVERY_ATTEMPTS 3

// How long the link may stay down before the address itself is suspected.
// Comfortably longer than a normal reconnect, so an ordinary blip does not
// trigger discovery traffic.
#define WS_RERESOLVE_AFTER_MS 25000

// Where the last working address is kept, so a reboot reconnects immediately
// instead of re-discovering.
#define HOST_STORE_NAMESPACE "mbuddy"
#define HOST_STORE_KEY_HOST "wsHost"
#define HOST_STORE_KEY_PORT "wsPort"
// Phase 1C: an explicitly user-configured Core endpoint, e.g.
// "ws://192.168.1.50:5000/ws" or "wss://core.example.com/ws" — set through
// the existing WiFiManager portal (see the "coreurl" parameter in setup()),
// stored in the same hostStore namespace as the two keys above. When
// present this always wins over the remembered/discovered/compiled-in
// chain below — it is a deliberate user choice, not a guess.
#define HOST_STORE_KEY_URL "coreUrl"

#define MIC_SAMPLE_RATE 16000
#define MIC_DMA_COUNT 8
#define MIC_DMA_LEN 256
#define MIC_I2S_WORDS (MIC_DMA_LEN * 2)

#define IDLE_BLINK_MS 3500  // Natural blink interval
#define IDLE_LOOK_MS 2500   // Natural look interval

#define SCREEN_W 128
#define SCREEN_H 64
#define EYE_SIZE 24
#define OLED_PROBE_ONLY 0

// ── LOCKOUT & BARGE-IN ──────────────────────────────────
#define SPEAKER_LOCKOUT_MS       60    // After last PCM packet (Python sends [QUIET] once per reply)
#define POST_TTS_GRACE_MS        35    // Brief grace before mic resumes
#define MIN_BARGE_THRESHOLD      5200  // int16 peak — normal speech/echo stays below this during playback
#define BARGE_IN_MULTIPLIER      3.2f
#define BARGE_SELF_REJECT_MULT   6.5f  // Applied whenever speaker lockout OR playback (not only audioPlaying)
#define BARGE_CONSECUTIVE_FRAMES 6     // ~96ms sustained loud energy before interrupt
#define NOISE_FLOOR_ADAPT        0.02f // Slower adaptation to prevent "learning out" the user voice

// ── GLOBALS ──────────────────────────────────────────────
WebSocketsClient wsClient;
WiFiManager wm;
Face* face = nullptr;
static int32_t rawMicBuffer[MIC_I2S_WORDS];
static int16_t pcmMicBuffer[MIC_DMA_LEN];

struct TxAudioPacket {
  uint16_t bytes;
  int16_t samples[MIC_DMA_LEN];
};

struct TxTextMessage {
  char text[32];
};

struct Sys {
  volatile bool wsConnected = false;
  volatile bool wsStarted = false;
  // Set once an attempt has failed, so the next attempt asks the network for
  // the backend's address rather than reusing a remembered one that is now
  // wrong — which is precisely the case a changed DHCP lease produces.
  volatile bool wsFailedOnce = false;
  // When the link went down, so a sustained outage can trigger a fresh
  // address lookup rather than retrying a host that may have moved.
  volatile uint32_t wsDownSinceMs = 0;
  volatile bool listening = false;
  volatile uint32_t lastKeepalive = 0;
  volatile uint32_t lastActivity = 0;
  eEmotions current = eEmotions::Normal;
  eEmotions backendState = eEmotions::Normal;  // Persistent state from AI

  // Speaker Sync & Barge-in
  volatile bool speakerLockout = false;
  volatile uint32_t speakerLockoutUntil = 0;
  volatile uint32_t postSpeechGraceUntil = 0;
  float ambientNoiseFloor = 500.0f; // Tracked Peak RMS
  volatile int bargeFramesCount = 0;

  // Stereo mic direction: -1.0 = fully left mic dominant, +1.0 = fully
  // right, 0.0 = centered/no clear source. Smoothed across buffers so a
  // single loud sample doesn't snap the face to look somewhere and back.
  // Written by micTask, read by micTask itself when applying a look — kept
  // in Sys and guarded by sysMux anyway, matching every other cross-task
  // field here, in case anything else ever wants to read it (diagnostics,
  // a future telemetry frame to the backend).
  volatile float micDirection = 0.0f;
  // Louder of the two slots' peaks from the most recent buffer — lets
  // micTask gate the directional look on a real margin above
  // ambientNoiseFloor instead of guessing from direction alone (a
  // side-skewed room noise floor can look "directional" without anyone
  // actually talking). Single-writer/single-reader within micTask, same as
  // ambientNoiseFloor above.
  volatile int32_t lastMicPeak = 0;

  // Audio Processing (DC Blocker persistent state)
  int16_t dc_lastX = 0;
  float   dc_lastY = 0.0f;

  // Energy & Metabolism
  float energyLevel = 85.0f;
  char activeAction[24] = "";
  volatile uint32_t actionStartedMs = 0;
  volatile uint32_t actionUntilMs = 0;
  uint8_t clockHour = 12;
  uint8_t clockMinute = 0;
  uint8_t clockSecond = 0;
  volatile uint32_t clockSyncedMs = 0;

} sys;

portMUX_TYPE sysMux = portMUX_INITIALIZER_UNLOCKED;
SemaphoreHandle_t faceMutex = NULL;

// True while an over-the-air update is arriving: the face stops redrawing (the update
// screen owns the OLED) and the microphone stops streaming so Wi-Fi is left to the download.
static volatile bool otaActive = false;

// How long a mood change will wait for the face mutex. faceTask holds it for
// the duration of a frame send (~90 ms at this bus clock), so anything below
// that silently loses to a redraw already in progress.
static const uint32_t FACE_APPLY_TIMEOUT_MS = 250;

bool takeFace(uint32_t timeoutMs = 20) {
  return faceMutex == NULL || xSemaphoreTakeRecursive(faceMutex, pdMS_TO_TICKS(timeoutMs)) == pdTRUE;
}

void giveFace() {
  if (faceMutex != NULL) xSemaphoreGiveRecursive(faceMutex);
}

QueueHandle_t audioTxQueue = NULL;
QueueHandle_t textTxQueue = NULL;

// ── AUDIO RING BUFFER ───────────────────────────────
// Mono PCM @ 22050 Hz ≈ 44.1 KB/s. Larger ring = fewer underruns on long TTS.
// SSL/tunnel needs contiguous heap for mbedtls — use smaller ring when WSS.
#if USE_LOCAL_BRAIN
  static const size_t AUDIO_RING_TRY[] = {48 * 1024, 40 * 1024, 32 * 1024, 24 * 1024};
#else
  static const size_t AUDIO_RING_TRY[] = {32 * 1024, 24 * 1024, 16 * 1024};
#endif
static const size_t AUDIO_RING_TRY_COUNT = sizeof(AUDIO_RING_TRY) / sizeof(AUDIO_RING_TRY[0]);
static size_t audioRingBytes = 0;
static uint32_t audioRingDrops = 0;

RingbufHandle_t audioRingBuf = NULL;
TaskHandle_t audioTaskHandle = NULL;
TaskHandle_t micTaskHandle = NULL;
TaskHandle_t faceTaskHandle = NULL;
static volatile bool audioPlaying = false;
static bool audioSubsystemStarted = false;
static bool faceSubsystemStarted = false;
static uint32_t lastFaceFrameMs = 0;

static const uint8_t ROBOT_IDEA_BULB_16[] PROGMEM = {
  0xc0, 0x03, 0xe0, 0x07, 0x30, 0x0c, 0x18, 0x18,
  0x18, 0x18, 0x30, 0x0c, 0xe0, 0x07, 0xc0, 0x03,
  0x80, 0x01, 0xc0, 0x03, 0xc0, 0x03, 0x80, 0x01,
  0xe0, 0x07, 0x20, 0x04, 0xe0, 0x07, 0x00, 0x00
};

// ── FORWARD DECLARATIONS ───────────────────────────
void setupI2S();
void setupI2SSpeaker();
void audioTask(void* pvParameters);
void micTask(void* pvParameters);
int readMicPcm(int16_t* dest, int maxSamples);
eEmotions tagToEmotion(const String& msg);
void applyMood(eEmotions mood);
void applyMicDirectionLook(float direction);
void updateStatusLed();
void restoreIdleRhythm();
bool canTransmitMic(int16_t* pcm, int samples);
void flushAudioBuffer(); // Instant silence helper
void onWebSocket(WStype_t type, uint8_t* payload, size_t len);
void ensureWebSocket();
bool discoverBackend(String& outHost, uint16_t& outPort);
bool parseCoreUrl(const String& url, String& outHost, uint16_t& outPort, bool& outSecure, String& outPath);
void rememberHost(const String& host, uint16_t port);
void resolveBackendAddress(bool forceDiscovery);  // Connect only after WiFi is up
void logHeap(const char* tag);
bool pushAudioRing(const uint8_t* data, size_t len);
bool enqueueTextMessage(const char* text);
String buildHelloJson();
bool enqueueAudioPacket(const int16_t* pcm, int samples);
void drainWebSocketTxQueues();
void resetWebSocketTxQueues();
eEmotions parseStateTagOnly(const String& msg);
void initAudioRing();
void initAudioSubsystem();
void initFaceSubsystem();
void faceTask(void* pvParameters);
void updateFaceIfDue();
void startAction(const String& name, uint32_t durationMs);
void parseActionTag(const String& msg);
void parseTimeTag(const String& msg);
void pollSerialProtocol();
void processSerialProtocolLine(String msg);
void drawCompanionAction(uint32_t now);
uint32_t currentActionElapsed(uint32_t now);
uint32_t currentActionDuration();
uint8_t currentActionStep(uint32_t now, uint16_t frameMs, uint8_t modulo);
uint8_t currentActionFrame(uint32_t now);
uint8_t prng8(uint16_t seed);
bool isPixelInEyeBounds(uint8_t x, uint8_t y);
bool actionHidesEnergy();
int triWave(uint16_t step, int lo, int hi);
void drawTinyLeftEyes();
void drawJoystickAt(int cx, int cy, bool rightLean);
void drawStickman(int x, int footY, bool runFrame, bool guitar);
void drawDottedHLine(uint8_t y);
void drawTinyStickman(uint8_t x, uint8_t floorY, bool runFrame, bool attackRight);
void drawDragonHead(int x, int y, bool interlace);
void drawCrossStar(uint8_t x, uint8_t y, bool bright);
void drawIconFrame();
void drawReferenceEyes(uint8_t cx, uint8_t cy);
void drawReferenceStickman(uint8_t x, uint8_t y, bool armUp);
void drawReferenceRobot(uint8_t x, uint8_t y);
void drawReferenceHeart(uint8_t x, uint8_t y);
void drawRobotHead(int x, int y, uint8_t mood);
void drawBitmap16(int x, int y, const uint8_t* sprite);
void drawJoystickAction(uint32_t now);
void drawInteractiveArenaAction(uint32_t now);
void drawDragonFireAction(uint32_t now);
void drawDataWeaverAction(uint32_t now);
void drawPixelCascadeAction(uint32_t now);
void drawHeartbeatAction(uint32_t now);
void drawThoughtBubbleAction(uint32_t now);
void drawBlockBreakAction(uint32_t now);
void drawStarlightAction(uint32_t now);
void drawClockTimerAction(uint32_t now);
void drawWifiSearchAction(uint32_t now);
void drawBatteryChargeAction(uint32_t now);
void drawErrorGlitchAction(uint32_t now);
void drawScanningTargetAction(uint32_t now);
void drawDreamingAction(uint32_t now);
void drawCelebrationAction(uint32_t now);
void drawListeningAction(uint32_t now);



// ============================================================
//  AUDIO TASK: Background playback from RingBuffer (Core 1)
// ============================================================
void audioTask(void* pvParameters) {
  while (audioRingBuf == NULL) vTaskDelay(pdMS_TO_TICKS(100));

  static uint8_t emptyReads = 0;
  const uint8_t EMPTY_READS_TO_END = 75;  // ~1500ms bridge gaps between separately synthesized TTS chunks

  while (true) {
    size_t itemSize = 0;
    uint8_t* data = (uint8_t*)xRingbufferReceive(audioRingBuf, &itemSize, pdMS_TO_TICKS(20));

    if (data != NULL) {
      emptyReads = 0;
      portENTER_CRITICAL(&sysMux);
      audioPlaying = true;
      portEXIT_CRITICAL(&sysMux);
      size_t bytesWritten = 0;
      i2s_write(I2S_PORT_SPK, data, itemSize, &bytesWritten, portMAX_DELAY);
      vRingbufferReturnItem(audioRingBuf, data);
      continue;
    }

    if (!audioPlaying) {
      emptyReads = 0;
      continue;
    }

    // Brief ring underrun mid-stream — wait, do NOT click-stop
    emptyReads++;
    if (emptyReads < EMPTY_READS_TO_END) {
      continue;
    }

    // True end-of-stream: pad silence before DMA clear (avoids mid-stream click)
    int16_t silence_buf[256];
    memset(silence_buf, 0, sizeof(silence_buf));
    size_t bytesWritten = 0;
    for (int step = 0; step < 6; step++) {
      i2s_write(I2S_PORT_SPK, (uint8_t*)silence_buf, sizeof(silence_buf), &bytesWritten, portMAX_DELAY);
    }

    uint32_t now = millis();
    portENTER_CRITICAL(&sysMux);
    audioPlaying = false;
    sys.speakerLockoutUntil = now + SPEAKER_LOCKOUT_MS;
    sys.postSpeechGraceUntil = now + POST_TTS_GRACE_MS;
    portEXIT_CRITICAL(&sysMux);
    emptyReads = 0;
    // Bug 4 fix: only report PLAYBACK_DONE when WS is live.
    // Queueing while disconnected fills the queue with stale events
    // that burst out on reconnect and trigger premature mic-open.
    if (sys.wsConnected) {
      enqueueTextMessage("[PLAYBACK_DONE]");
    }
    i2s_zero_dma_buffer(I2S_PORT_SPK);
  }
}

void logHeap(const char* tag) {
  Serial.printf(
    "📊 Heap [%s]: free=%u min=%u maxBlock=%u\n",
    tag,
    ESP.getFreeHeap(),
    ESP.getMinFreeHeap(),
    ESP.getMaxAllocHeap()
  );
}

bool pushAudioRing(const uint8_t* data, size_t len) {
  if (audioRingBuf == NULL) {
    initAudioRing();
  }
  if (audioRingBuf == NULL || data == NULL || len == 0) return false;
  TickType_t timeout = audioPlaying ? pdMS_TO_TICKS(200) : pdMS_TO_TICKS(100);
  if (xRingbufferSend(audioRingBuf, (void*)data, len, timeout) == pdTRUE) {
    return true;
  }
  // Soft flush legacy items if ring buffer becomes full during fast stream
  size_t drainSize = 0;
  void* item = xRingbufferReceive(audioRingBuf, &drainSize, 0);
  if (item != NULL) {
    vRingbufferReturnItem(audioRingBuf, item);
  }
  return (xRingbufferSend(audioRingBuf, (void*)data, len, pdMS_TO_TICKS(50)) == pdTRUE);
}

bool enqueueTextMessage(const char* text) {
  if (textTxQueue == NULL || text == NULL) return false;
  TxTextMessage msg = {};
  strlcpy(msg.text, text, sizeof(msg.text));
  return xQueueSend(textTxQueue, &msg, 0) == pdTRUE;
}

// Phase 1B HELLO handshake payload. Built fresh per connection (session_id
// changes every time) and sent directly via wsClient.sendTXT() rather than
// through the 32-byte TxTextMessage queue used by enqueueTextMessage() —
// this JSON is comfortably longer than that buffer, and HELLO only needs
// to be sent once per connection, so a queue isn't needed for it anyway.
String buildHelloJson() {
  String mac = WiFi.macAddress();
  mac.replace(":", "");
  String deviceId = "mbuddy-" + mac;
  String sessionId = String(millis(), HEX) + String((uint32_t)random(0, 0x7FFFFFFF), HEX);

  String json = "{";
  json += "\"version\":1,";
  json += "\"type\":\"hello\",";
  json += "\"device_id\":\"" + deviceId + "\",";
  json += "\"session_id\":\"" + sessionId + "\",";
  json += "\"firmware_version\":\"" MBUDDY_FIRMWARE_VERSION "\",";
  json += "\"auth_token\":\"" MBUDDY_DEVICE_TOKEN "\",";
  json += "\"capabilities\":{\"mic\":true,\"speaker\":true,\"display\":true,\"ota\":true}";
  json += "}";
  return json;
}

bool enqueueAudioPacket(const int16_t* pcm, int samples) {
  if (audioTxQueue == NULL || pcm == NULL || samples <= 0) return false;
  TxAudioPacket packet = {};
  if (samples > MIC_DMA_LEN) samples = MIC_DMA_LEN;
  packet.bytes = samples * sizeof(int16_t);
  memcpy(packet.samples, pcm, packet.bytes);
  return xQueueSend(audioTxQueue, &packet, 0) == pdTRUE;
}

void resetWebSocketTxQueues() {
  if (audioTxQueue != NULL) xQueueReset(audioTxQueue);
  if (textTxQueue != NULL) xQueueReset(textTxQueue);
}

void drainWebSocketTxQueues() {
  if (!sys.wsConnected || WiFi.status() != WL_CONNECTED) return;
  if (audioTxQueue == NULL || textTxQueue == NULL) return;

  TxTextMessage msg;
  while (xQueueReceive(textTxQueue, &msg, 0) == pdTRUE) {
    wsClient.sendTXT(msg.text);
  }

  TxAudioPacket packet;
  uint8_t sentPackets = 0;
  while (sentPackets < 8 && xQueueReceive(audioTxQueue, &packet, 0) == pdTRUE) {
    wsClient.sendBIN((uint8_t*)packet.samples, packet.bytes);
    sentPackets++;
  }
}

// ── Backend address resolution ──────────────────────────────────────
static Preferences hostStore;

// The address currently being used, and where it came from — reported on
// connect so a wrong address is visible rather than silent.
static String resolvedHost = MBUDDY_CORE_HOST;
static uint16_t resolvedPort = MBUDDY_CORE_PORT;
static String resolvedPath = WS_PATH;
// Phase 1C: which transport to use for resolvedHost/resolvedPort, decided
// at runtime now instead of only by the USE_LOCAL_BRAIN compile flag. The
// remembered/discovered/compiled-in paths below still default to
// !USE_LOCAL_BRAIN exactly as before Phase 1C (unchanged behavior); only an
// explicitly configured Core URL (HOST_STORE_KEY_URL) can set this to
// something else, decided by the "ws://" vs "wss://" scheme the user typed.
static bool resolvedSecure = !USE_LOCAL_BRAIN;
static const char* resolvedVia = "compiled-in";

/**
 * Parse a Phase 1C Core URL ("ws://host[:port][/path]" or
 * "wss://host[:port][/path]") into its parts. Returns false — leaving the
 * output parameters untouched — for anything that isn't a well-formed
 * ws(s):// URL, so a malformed configured value can never silently seed a
 * broken connection attempt; the caller falls through to the existing
 * remembered/discovered/compiled-in chain instead.
 */
bool parseCoreUrl(const String& url, String& outHost, uint16_t& outPort, bool& outSecure, String& outPath) {
  String rest;
  if (url.startsWith("wss://")) {
    outSecure = true;
    rest = url.substring(6);
  } else if (url.startsWith("ws://")) {
    outSecure = false;
    rest = url.substring(5);
  } else {
    return false;
  }
  if (rest.length() == 0) return false;

  int pathIdx = rest.indexOf('/');
  String hostPort = (pathIdx >= 0) ? rest.substring(0, pathIdx) : rest;
  outPath = (pathIdx >= 0) ? rest.substring(pathIdx) : String(WS_PATH);
  if (outPath.length() == 0) outPath = String(WS_PATH);

  int colonIdx = hostPort.indexOf(':');
  if (colonIdx >= 0) {
    outHost = hostPort.substring(0, colonIdx);
    long p = hostPort.substring(colonIdx + 1).toInt();
    if (p <= 0 || p > 65535) return false;
    outPort = (uint16_t)p;
  } else {
    outHost = hostPort;
    outPort = outSecure ? 443 : 80;
  }
  if (outHost.length() == 0) return false;
  return true;
}

/**
 * Ask the network where the backend is.
 *
 * Broadcasts a probe and waits briefly for the beacon's reply, which carries
 * the backend's current address. Returns true when an address was learned.
 */
bool discoverBackend(String& outHost, uint16_t& outPort) {
  WiFiUDP udp;
  if (!udp.begin(0)) {
    return false;
  }

  for (uint8_t attempt = 0; attempt < DISCOVERY_ATTEMPTS; attempt++) {
    udp.beginPacket(IPAddress(255, 255, 255, 255), DISCOVERY_PORT);
    udp.write((const uint8_t*)DISCOVERY_PROBE, strlen(DISCOVERY_PROBE));
    udp.endPacket();

    uint32_t deadline = millis() + DISCOVERY_TIMEOUT_MS;
    while ((int32_t)(millis() - deadline) < 0) {
      int size = udp.parsePacket();
      if (size <= 0) {
        delay(10);
        continue;
      }

      char buf[128] = {0};
      int len = udp.read(buf, sizeof(buf) - 1);
      if (len <= 0) continue;
      buf[len] = '\0';

      // Expected: "MBUDDY-HERE <ip> <port> <name>"
      if (strncmp(buf, DISCOVERY_REPLY_PREFIX, strlen(DISCOVERY_REPLY_PREFIX)) != 0) {
        continue;
      }
      char ip[64] = {0};
      unsigned int port = 0;
      if (sscanf(buf + strlen(DISCOVERY_REPLY_PREFIX), "%63s %u", ip, &port) == 2 && port > 0) {
        outHost = String(ip);
        outPort = (uint16_t)port;
        udp.stop();
        Serial.printf("Discovery: backend at %s:%u\n", ip, port);
        return true;
      }
    }
  }

  udp.stop();
  return false;
}

/**
 * Remember an address that actually worked.
 *
 * Called only once a session opens, so a failed guess never overwrites a
 * known-good address.
 */
void rememberHost(const String& host, uint16_t port) {
  if (host.length() == 0) return;
  hostStore.begin(HOST_STORE_NAMESPACE, false);
  if (hostStore.getString(HOST_STORE_KEY_HOST, "") != host ||
      hostStore.getUShort(HOST_STORE_KEY_PORT, 0) != port) {
    hostStore.putString(HOST_STORE_KEY_HOST, host);
    hostStore.putUShort(HOST_STORE_KEY_PORT, port);
    Serial.printf("Discovery: remembered %s:%u\n", host.c_str(), port);
  }
  hostStore.end();
}

/**
 * Decide which address to try, cheapest option first.
 *
 * The stored address covers the ordinary case with no network traffic; probing
 * only happens when that is absent or has already failed this boot.
 */
void resolveBackendAddress(bool forceDiscovery) {
  // Phase 1C: an explicitly configured Core URL always wins — it is the
  // user's deliberate choice made through the WiFiManager portal, not a
  // guess, so it is checked before (and regardless of) forceDiscovery.
  hostStore.begin(HOST_STORE_NAMESPACE, true);
  String customUrl = hostStore.getString(HOST_STORE_KEY_URL, "");
  hostStore.end();
  if (customUrl.length() > 0) {
    String host, path;
    uint16_t port;
    bool secure;
    if (parseCoreUrl(customUrl, host, port, secure, path)) {
      resolvedHost = host;
      resolvedPort = port;
      resolvedSecure = secure;
      resolvedPath = path;
      resolvedVia = "configured";
      return;
    }
    Serial.printf("Configured Core URL invalid, ignoring: %s\n", customUrl.c_str());
  }

  // Every path below is unchanged from before Phase 1C and always uses the
  // compile-time transport default, exactly as it always has.
  resolvedSecure = !USE_LOCAL_BRAIN;
  resolvedPath = WS_PATH;

  if (!forceDiscovery) {
    hostStore.begin(HOST_STORE_NAMESPACE, true);
    String stored = hostStore.getString(HOST_STORE_KEY_HOST, "");
    uint16_t storedPort = hostStore.getUShort(HOST_STORE_KEY_PORT, 0);
    hostStore.end();
    if (stored.length() > 0 && storedPort > 0) {
      resolvedHost = stored;
      resolvedPort = storedPort;
      resolvedVia = "remembered";
      return;
    }
  }

  String host;
  uint16_t port = 0;
  if (discoverBackend(host, port)) {
    resolvedHost = host;
    resolvedPort = port;
    resolvedVia = "discovered";
    return;
  }

  resolvedHost = MBUDDY_CORE_HOST;
  resolvedPort = MBUDDY_CORE_PORT;
  resolvedVia = "compiled-in";
}

void ensureWebSocket() {
  if (WiFi.status() != WL_CONNECTED || sys.wsStarted) {
    return;
  }
  logHeap("ws-before-begin");
  sys.wsStarted = true;

  // After a failed attempt, ignore the remembered address and ask the network
  // again — that is exactly the case where the address has changed.
  resolveBackendAddress(sys.wsFailedOnce);

  // Phase 1C: the ws/wss choice is now resolved at runtime (resolvedSecure)
  // instead of only by the USE_LOCAL_BRAIN compile flag — a configured Core
  // URL can point at either transport without a reflash. The compile flag
  // still supplies the default when nothing has been configured.
  if (resolvedSecure) {
    // beginSSL()'s 4th parameter is a TLS fingerprint, NOT the protocol
    // (unlike begin(), where it is) — this call previously passed "arduino"
    // as the fingerprint, which could never match any real certificate and
    // would fail every TLS handshake (start_ssl_client: -1). This bug
    // predates Phase 1C (the same mistake was in the original #else branch
    // of USE_LOCAL_BRAIN) and was never caught because wss:// had apparently
    // never actually been exercised until Phase 1D's real tunnel test.
    // Passing "" leaves fingerprint/CA unset, so the library falls back to
    // setInsecure() (no certificate validation) — acceptable here since
    // HELLO's shared-secret auth is what actually authenticates the link,
    // not the TLS certificate.
    wsClient.beginSSL(resolvedHost.c_str(), resolvedPort, resolvedPath.c_str(), "", "arduino");
    Serial.printf("WS -> wss://%s:%u%s (%s)\n",
                  resolvedHost.c_str(), resolvedPort, resolvedPath.c_str(), resolvedVia);
  } else {
    wsClient.begin(resolvedHost.c_str(), resolvedPort, resolvedPath.c_str(), "arduino");
    Serial.printf("WS -> ws://%s:%u%s (%s)\n",
                  resolvedHost.c_str(), resolvedPort, resolvedPath.c_str(), resolvedVia);
  }
  wsClient.onEvent(onWebSocket);
  wsClient.setReconnectInterval(WS_RECONNECT_MS);
  // Heartbeat every 10s, timeout 3s — must be shorter than WS_RECONNECT_MS
  // so a dead TCP connection is detected before the reconnect timer fires.
  wsClient.enableHeartbeat(10000, 3000, 2);
}

// Helper to instantly kill any pending playback
void flushAudioBuffer() {
  if (audioRingBuf == NULL) return;
  
  // Drain RingBuffer items
  size_t itemSize;
  void* item;
  while ((item = xRingbufferReceive(audioRingBuf, &itemSize, 0)) != NULL) {
    vRingbufferReturnItem(audioRingBuf, item);
  }
  
  portENTER_CRITICAL(&sysMux);
  audioPlaying = false;
  portEXIT_CRITICAL(&sysMux);
  i2s_zero_dma_buffer(I2S_PORT_SPK);
}

// ============================================================
//  EMOTION PARSER: tag string → eEmotions enum (18 values)
// ============================================================
eEmotions tagToEmotion(const String& msg) {
  String m = msg;
  m.toLowerCase();

  // Python Backend States (Priority)
  if (m.indexOf("happy") >= 0) return eEmotions::Happy;
  if (m.indexOf("sleepy") >= 0) return eEmotions::Sleepy;
  if (m.indexOf("tired") >= 0) return eEmotions::Sleepy;
  if (m.indexOf("thinking") >= 0 || m.indexOf("focused") >= 0) return eEmotions::Focused;
  if (m.indexOf("bored") >= 0) return eEmotions::Skeptic;
  if (m.indexOf("hyper") >= 0 || m.indexOf("laughing") >= 0 || m.indexOf("glee") >= 0) return eEmotions::Glee;
  if (m.indexOf("sad") >= 0) return eEmotions::Sad;
  if (m.indexOf("surprised") >= 0) return eEmotions::Surprised;
  if (m.indexOf("neutral") >= 0 || m.indexOf("normal") >= 0) return eEmotions::Normal;

  // Legacy/Detailed Tags
  if (m.indexOf("angry") >= 0) return eEmotions::Angry;
  if (m.indexOf("worried") >= 0) return eEmotions::Worried;
  if (m.indexOf("annoyed") >= 0) return eEmotions::Annoyed;
  if (m.indexOf("skeptic") >= 0) return eEmotions::Skeptic;
  if (m.indexOf("frustrated") >= 0) return eEmotions::Frustrated;
  if (m.indexOf("unimpressed") >= 0) return eEmotions::Unimpressed;
  if (m.indexOf("suspicious") >= 0) return eEmotions::Suspicious;
  if (m.indexOf("squint") >= 0) return eEmotions::Squint;
  if (m.indexOf("furious") >= 0) return eEmotions::Furious;
  if (m.indexOf("scared") >= 0) return eEmotions::Scared;
  if (m.indexOf("awe") >= 0) return eEmotions::Awe;

  return eEmotions::Normal;  // Unknown should not keep latching old backend mood
}

eEmotions parseStateTagOnly(const String& msg) {
  int stateIdx = msg.indexOf("[STATE:");
  if (stateIdx < 0) return eEmotions::Normal;

  int endIdx = msg.indexOf("]", stateIdx);
  if (endIdx <= stateIdx) return eEmotions::Normal;

  String stateTag = msg.substring(stateIdx, endIdx + 1);
  return tagToEmotion(stateTag);
}

// ============================================================
//  Apply Mood: wrapper for library's individual GoTo_XXX() methods
//  ✅ Uses exact API from esp32-eyes — no modifications needed
// ============================================================
void applyMood(eEmotions mood) {
  if (!face) return;
  // Must exceed one full frame. faceTask holds this mutex across
  // u8g2.sendBuffer(), which at 100 kHz is ~90 ms for a 128x64 frame, so the
  // old 20 ms timeout lost the race most of the time and dropped the mood
  // change without a word.
  if (!takeFace(FACE_APPLY_TIMEOUT_MS)) {
    // Leave sys.backendState alone so the retry in loop() picks this up, and
    // make the miss visible instead of failing silently.
    Serial.printf("Face Apply BLOCKED: %d (mutex busy)\n", (int)mood);
    return;
  }
  bool alreadyInState = (sys.current == mood);

  // Call the exact method name from FaceExpression.h
  switch (mood) {
    case eEmotions::Normal: face->Expression.GoTo_Normal(); break;
    case eEmotions::Happy: face->Expression.GoTo_Happy(); break;
    case eEmotions::Sad: face->Expression.GoTo_Sad(); break;
    case eEmotions::Angry: face->Expression.GoTo_Angry(); break;
    case eEmotions::Glee: face->Expression.GoTo_Glee(); break;
    case eEmotions::Focused: face->Expression.GoTo_Focused(); break;
    case eEmotions::Worried: face->Expression.GoTo_Worried(); break;
    case eEmotions::Annoyed: face->Expression.GoTo_Annoyed(); break;
    case eEmotions::Surprised: face->Expression.GoTo_Surprised(); break;
    case eEmotions::Skeptic: face->Expression.GoTo_Skeptic(); break;
    case eEmotions::Frustrated: face->Expression.GoTo_Frustrated(); break;
    case eEmotions::Unimpressed: face->Expression.GoTo_Unimpressed(); break;
    case eEmotions::Sleepy: face->Expression.GoTo_Sleepy(); break;
    case eEmotions::Suspicious: face->Expression.GoTo_Suspicious(); break;
    case eEmotions::Squint: face->Expression.GoTo_Squint(); break;
    case eEmotions::Furious: face->Expression.GoTo_Furious(); break;
    case eEmotions::Scared: face->Expression.GoTo_Scared(); break;
    case eEmotions::Awe: face->Expression.GoTo_Awe(); break;
  }

  sys.current = mood;

  // Optional: emotion-specific timing tweaks using library API
  switch (mood) {
    case eEmotions::Sleepy:
      face->Blink.Timer.SetIntervalMillis(1800);
      face->Look.Timer.SetIntervalMillis(8000);
      break;
    case eEmotions::Glee:
      face->DoBlink();
      face->Look.Timer.SetIntervalMillis(700);
      break;
    case eEmotions::Surprised:
      face->Look.LookAt(0.0f, -0.4f);
      face->Blink.Timer.SetIntervalMillis(5000);
      break;
    case eEmotions::Angry:
    case eEmotions::Furious:
    case eEmotions::Frustrated:
      face->Look.LookAt(0.0f, 0.2f);
      face->Blink.Timer.SetIntervalMillis(4000);
      break;
    case eEmotions::Worried:
    case eEmotions::Scared:
      face->Look.LookAt(0.2f, -0.2f);
      face->Blink.Timer.SetIntervalMillis(2000);
      break;
    case eEmotions::Focused:
    case eEmotions::Skeptic:
    case eEmotions::Suspicious:
      face->Look.LookAt(0.0f, 0.0f);
      face->Blink.Timer.SetIntervalMillis(5000);
      break;
    default:
      restoreIdleRhythm();
      break;
  }

  sys.current = mood;

  // React to mood re-confirmation or change
  if (alreadyInState) {
    face->DoBlink();  // Eyes feel "active" even if mood is same
  }
  giveFace();

  // Report what is actually being drawn, whatever transport asked for it.
  // "Serial State Sync" only covers commands that arrived over USB, so it
  // cannot confirm a face set over WiFi. This can, and it reads sys.current
  // rather than the requested mood, so it reports the applied state.
  Serial.printf("Face Applied: %d\n", (int)sys.current);
}

// ============================================================
//  Stereo mic direction -> look toward whoever is talking
// ============================================================
// Scales sys.micDirection (-1..+1) into the same x range applyMood's fixed
// offsets already use (LookAt's own idle wander uses -0.5..+0.5) — a full
// turn toward the loudest slot is a strong but not extreme glance, not the
// eyes snapping to the very edge of their travel.
static const float MIC_LOOK_SCALE = 0.5f;

void applyMicDirectionLook(float direction) {
  if (!face) return;
  if (!takeFace()) return;  // Best-effort — a busy frame just skips this glance, not worth blocking micTask over.
  face->Look.LookAt(constrain(direction, -1.0f, 1.0f) * MIC_LOOK_SCALE, 0.0f);
  // Restart the idle look-around countdown so this glance actually holds
  // for a moment instead of being immediately overwritten by the next
  // random idle look, which runs on its own independent timer.
  face->Look.Timer.Reset();
  giveFace();
}

void restoreIdleRhythm() {
  if (!face) return;
  if (!takeFace()) return;
  face->Blink.Timer.SetIntervalMillis(IDLE_BLINK_MS);
  face->Look.Timer.SetIntervalMillis(IDLE_LOOK_MS);
  face->Look.LookAt(0.0f, 0.0f);
  giveFace();
}

void initFaceSubsystem() {
  if (faceSubsystemStarted) return;
  faceSubsystemStarted = true;

  if (!takeFace(100)) return;
  logHeap("display-init-start");
  face = new Face(SCREEN_W, SCREEN_H, EYE_SIZE);
  if (!face) {
    Serial.println("Face allocation failed - continuing headless");
    logHeap("display-init-failed");
    giveFace();
    return;
  }

  face->EyeInterDistance = 10;
  face->RandomBehavior = false;
  face->RandomBlink = true;
  face->RandomLook = true;
  restoreIdleRhythm();
  applyMood(eEmotions::Normal);
  face->Update();
  logHeap("display-init-done");
  giveFace();
}

// ============================================================
//  WebSocket Handler: Brain Interface
// ============================================================
void onWebSocket(WStype_t type, uint8_t* payload, size_t len) {
  switch (type) {
    case WStype_CONNECTED:
      sys.wsConnected = true;
      sys.wsFailedOnce = false;
      // Only now is this address known to work, so this is the safe point to
      // persist it. Storing it earlier would let a bad guess replace a good
      // address.
      rememberHost(resolvedHost, resolvedPort);
      resetWebSocketTxQueues();
      // Phase 1B: HELLO must be the very first message the backend sees on
      // this socket, or it rejects the connection — sent directly, before
      // anything else (including the queue drain, which resetWebSocketTxQueues()
      // just cleared anyway) can get in front of it.
      {
        String hello = buildHelloJson();  // sendTXT() takes String& (no const/rvalue overload), needs a named lvalue
        wsClient.sendTXT(hello);
      }
      Serial.println("Brain connected — HELLO sent");
      initAudioSubsystem();
      applyMood(eEmotions::Normal);
      if (takeFace()) {
        if (face) face->DoBlink();
        giveFace();
      }
      break;

    case WStype_DISCONNECTED:
      sys.wsConnected = false;
      sys.wsFailedOnce = true;
      resetWebSocketTxQueues();
      Serial.println("Brain disconnected");
      Serial.printf("   [Debug] Free Heap: %d | Max Block: %d\n", ESP.getFreeHeap(), ESP.getMaxAllocHeap());
      if (WiFi.status() == WL_CONNECTED) {
        applyMood(eEmotions::Worried);  // "Online but brain missing"
      } else {
        applyMood(eEmotions::Normal);   // "No WiFi, chilling offline"
      }
      break;

    case WStype_TEXT:
      {
        String msg;
        msg.reserve(len + 1);
        for (size_t i = 0; i < len; i++) msg += (char)payload[i];
        Serial.printf("MSG %s\n", msg.c_str());
        sys.lastActivity = millis();

        // Phase 1B: the backend's reply to HELLO (hello_ack/hello_reject) is
        // plain JSON, never a bracket tag — none of the tag checks below can
        // match it, but short-circuit anyway rather than running them for no
        // reason. A reject is logged only: the backend closes the socket
        // right after sending it, so WStype_DISCONNECTED (and the existing
        // reconnect timer) handles what happens next without any extra code here.
        if (msg.startsWith("{")) {
          if (msg.indexOf("\"hello_reject\"") >= 0) {
            Serial.printf("HELLO rejected by backend: %s\n", msg.c_str());
          } else if (msg.indexOf("\"hello_ack\"") >= 0) {
            Serial.println("HELLO accepted");
          }
          break;
        }

        parseTimeTag(msg);

        // ── PROTOCOL PARSING ───────────────────────────────
        bool isSpeaking = (msg.indexOf("[SPEAKING]") >= 0);
        bool isQuiet    = (msg.indexOf("[QUIET]") >= 0);
        bool isBarge    = (msg.indexOf("[BARGE]") >= 0);
        bool isHeard    = msg.startsWith("[HEARD]");
        bool isThinking = (msg.indexOf("[THINKING]") >= 0);
        bool isInner    = (msg.indexOf("[INNER]") >= 0);
        bool isAction   = (msg.indexOf("[ACT:") >= 0);
        bool isSfx      = (msg.indexOf("[SFX:") >= 0);

        if (isAction) {
          parseActionTag(msg);
        }
        if (isSfx) {
          if (msg.indexOf("[SFX:laser_pew]") >= 0 || msg.indexOf("[SFX:chiptune_loop]") >= 0) {
            startAction("joystick", 1800);
          } else if (msg.indexOf("[SFX:dragon_breath]") >= 0) {
            startAction("dragon_fire", 2000);
          } else if (msg.indexOf("[SFX:data_blip]") >= 0 || msg.indexOf("[SFX:data_drone]") >= 0) {
            startAction("data_weaver", 1600);
          } else if (msg.indexOf("[SFX:cascade_arp]") >= 0) {
            startAction("pixel_cascade", 1800);
          } else if (msg.indexOf("[SFX:ecg_beep]") >= 0) {
            startAction("heartbeat", 1600);
          } else if (msg.indexOf("[SFX:bubble_pop]") >= 0 || msg.indexOf("[SFX:bounce_click]") >= 0) {
            startAction("thought_bubble", 1600);
          } else if (msg.indexOf("[SFX:paddle_bounce]") >= 0 || msg.indexOf("[SFX:brick_hit]") >= 0) {
            startAction("block_break", 1800);
          } else if (msg.indexOf("[SFX:star_tinkle]") >= 0) {
            startAction("starlight", 1800);
          } else if (msg.indexOf("[SFX:clock_tick]") >= 0) {
            startAction("clock_timer", 1600);
          } else if (msg.indexOf("[SFX:sonar_ping]") >= 0) {
            startAction("wifi_search", 1700);
          } else if (msg.indexOf("[SFX:charge_hum]") >= 0) {
            startAction("battery_charge", 1800);
          } else if (msg.indexOf("[SFX:glitch_screech]") >= 0) {
            startAction("error_glitch", 1400);
          } else if (msg.indexOf("[SFX:sleep_dream]") >= 0) {
            startAction("dreaming", 2600);
          } else if (msg.indexOf("[SFX:success_chime]") >= 0) {
            startAction("celebration", 1800);
          } else if (msg.indexOf("[SFX:listen_ping]") >= 0) {
            startAction("listening_animation", 1800);
          }
        }

        // Update persistent backend state if [STATE:xxx] is present
        int stateIdx = msg.indexOf("[STATE:");
        if (stateIdx >= 0) {
          int endIdx = msg.indexOf("]", stateIdx);
          if (endIdx > stateIdx) {
            String stateTag = msg.substring(stateIdx, endIdx + 1);
            sys.backendState = tagToEmotion(stateTag);
            Serial.printf("State Sync: %d\n", (int)sys.backendState);
          }
        }

        // [STOP] — Backend forcing instant silence (Barge-in / Cancel)
        if (msg.indexOf("[STOP]") >= 0) {
          flushAudioBuffer();
          portENTER_CRITICAL(&sysMux);
          sys.speakerLockout = false;
          sys.speakerLockoutUntil = 0;
          sys.postSpeechGraceUntil = 0;
          sys.bargeFramesCount = 0;
          portEXIT_CRITICAL(&sysMux);
          return;
        }

        // ── LOGIC TRANSITIONS ──────────────────────────────
        if (isSpeaking) {
          portENTER_CRITICAL(&sysMux);
          sys.speakerLockout = true;
          sys.listening = false;
          sys.bargeFramesCount = 0;
          portEXIT_CRITICAL(&sysMux);
        }

        if (isBarge) {
          flushAudioBuffer();
          portENTER_CRITICAL(&sysMux);
          sys.speakerLockout = false;
          sys.speakerLockoutUntil = 0;
          sys.postSpeechGraceUntil = 0;
          sys.bargeFramesCount = 0;
          portEXIT_CRITICAL(&sysMux);
        }

        if (isQuiet) {
          uint32_t now = millis();
          portENTER_CRITICAL(&sysMux);
          sys.speakerLockout = false;
          sys.speakerLockoutUntil = now + SPEAKER_LOCKOUT_MS;
          sys.postSpeechGraceUntil = now + POST_TTS_GRACE_MS;
          sys.listening = false;
          sys.bargeFramesCount = 0;
          portEXIT_CRITICAL(&sysMux);
        }

        // [ENERGY:XX] — Sync energy level for OLED corner display
        int energyIdx = msg.indexOf("[ENERGY:");
        if (energyIdx >= 0) {
          int endIdx = msg.indexOf("]", energyIdx);
          if (endIdx > energyIdx) {
            String eVal = msg.substring(energyIdx + 8, endIdx);
            float ev = eVal.toFloat();
            if (ev > 0) sys.energyLevel = ev;
            Serial.printf("Energy Sync: %.1f\n", sys.energyLevel);
          }
        }

        if (isHeard) {
          sys.listening = true;
          if (takeFace()) {
            if (face) {
              face->Look.LookAt(-0.3f, 0.0f);
              face->DoBlink();
            }
            giveFace();
          }
        }

        if (isThinking) {
          sys.listening = true;
          if (takeFace()) {
            if (face) face->Look.LookAt(0.3f, 0.3f);
            giveFace();
          }
        }

        if (isInner) {
          if (takeFace()) {
            if (face) {
              face->Look.LookAt(random(-5, 5) / 10.0f, random(-3, 3) / 10.0f);
              face->DoBlink();
            }
            giveFace();
          }
        }

        // ── MOOD APPLICATION ───────────────────────────────
        // Determine the target emotion for this message. Only [STATE:...] is authoritative;
        // spoken text must not change the eyes just because it contains emotion words.
        eEmotions targetMood = sys.backendState;
        if (isHeard || isThinking) {
          targetMood = eEmotions::Focused;
        } else if (msg.indexOf("[STATE:") >= 0) {
          targetMood = parseStateTagOnly(msg);
        }

        // Apply emotion — single authoritative call, no double-apply
        if (isSpeaking || isQuiet || isBarge) {
          sys.listening = false;
        } else if (isHeard || isThinking) {
          sys.listening = true;
        }
        if (targetMood != sys.current) {
          applyMood(targetMood);
        }
        if (!sys.listening) {
          if (takeFace()) {
            if (face) face->Look.LookAt(0.0f, 0.0f);  // Return gaze to center
            giveFace();
          }
        }
        break;
      }

    case WStype_BIN:
      {
        if (pushAudioRing(payload, len)) {
          uint32_t now = millis();
          portENTER_CRITICAL(&sysMux);
          sys.speakerLockoutUntil = now + SPEAKER_LOCKOUT_MS;
          sys.postSpeechGraceUntil = 0;
          portEXIT_CRITICAL(&sysMux);
        }
        break;
      }

    case WStype_ERROR:
      Serial.println("WebSocket error");
      sys.wsConnected = false;
      resetWebSocketTxQueues();
      break;
  }
}

// ============================================================
//  I2S Microphone Setup (INMP441)
// ============================================================
void setupI2S() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = MIC_SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S, // Philips Standard for INMP441
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = MIC_DMA_COUNT,
    .dma_buf_len = MIC_DMA_LEN,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pins = {
    .bck_io_num = I2S_SCK,
    .ws_io_num = I2S_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = I2S_SD
  };
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(4, 4, 0)
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
#endif

  if (i2s_driver_install(I2S_PORT_MIC, &cfg, 0, nullptr) != ESP_OK) {
    Serial.println("I2S mic install failed");
    return;
  }
  if (i2s_set_pin(I2S_PORT_MIC, &pins) != ESP_OK) {
    Serial.println("I2S mic pin setup failed");
    return;
  }
  i2s_zero_dma_buffer(I2S_PORT_MIC);
  Serial.println("I2S mic initialized");
}

void setupI2SSpeaker() {
  i2s_config_t cfg_spk = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = 22050,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT, // Mono — matches Python Mono PCM stream
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = 0,
    .dma_buf_count = 4,
    .dma_buf_len = 512,
    .use_apll = false,      // MUST BE FALSE at 22050Hz — APLL breaks WiFi coexistence
    .tx_desc_auto_clear = true,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pins_spk = {
    .bck_io_num = I2S_SPK_BCLK,
    .ws_io_num = I2S_SPK_LRC,
    .data_out_num = I2S_SPK_DIN,
    .data_in_num = I2S_PIN_NO_CHANGE
  };
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(4, 4, 0)
  pins_spk.mck_io_num = I2S_PIN_NO_CHANGE;
#endif
  
  if (i2s_driver_install(I2S_PORT_SPK, &cfg_spk, 0, nullptr) != ESP_OK) {
    Serial.println("I2S speaker install failed");
    return;
  }
  i2s_set_pin(I2S_PORT_SPK, &pins_spk);
  i2s_zero_dma_buffer(I2S_PORT_SPK);
  Serial.println("I2S speaker initialized on BCLK=26, LRC=25, DIN=27");
}


int readMicPcm(int16_t* dest, int maxSamples) {
  if (!dest || maxSamples <= 0) return 0;

  size_t bytesRead = 0;
  int requestedWords = maxSamples * 2;
  if (requestedWords > MIC_I2S_WORDS) requestedWords = MIC_I2S_WORDS;
  esp_err_t result = i2s_read(
    I2S_PORT_MIC,
    rawMicBuffer,
    requestedWords * sizeof(int32_t),
    &bytesRead,
    portMAX_DELAY
  );
  static uint32_t lastMicDiagMs = 0;
  if (result != ESP_OK || bytesRead == 0) {
    uint32_t now = millis();
    if (now - lastMicDiagMs > 1000) {
      Serial.printf("MIC read failed: err=%d bytes=%u\n", (int)result, (unsigned)bytesRead);
      lastMicDiagMs = now;
    }
    return 0;
  }

  int words = bytesRead / sizeof(int32_t);
  int samples = words / 2;
  if (samples > maxSamples) samples = maxSamples;

  int32_t rawPeak = 0;
  int32_t pcmPeak = 0;
  int32_t slot0Peak = 0;
  int32_t slot1Peak = 0;

  for (int i = 0; i < samples; i++) {
    int32_t raw0 = rawMicBuffer[i * 2];
    int32_t raw1 = rawMicBuffer[(i * 2) + 1];

    // 1. Convert 32-bit I2S slot → 16-bit
    // INMP441 is 24-bit left-justified in a 32-bit slot (bits 31..8).
    // Shifting by 16 correctly moves the most significant 16 bits (including sign)
    // into the bottom 16 bits, perfectly matching int16_t range.
    int16_t slot0 = (int16_t)(raw0 >> 16);
    int16_t slot1 = (int16_t)(raw1 >> 16);
    int32_t abs0 = abs((int32_t)slot0);
    int32_t abs1 = abs((int32_t)slot1);
    if (abs0 > slot0Peak) slot0Peak = abs0;
    if (abs1 > slot1Peak) slot1Peak = abs1;

    // Two INMP441s, one per stereo slot. Use whichever slot is louder this
    // sample — that alone is a real, safe SNR win (whichever mic is
    // nearer/louder wins) and is also the "which side" signal for
    // direction below.
    //
    // This used to also subtract a fraction of the quieter slot as a
    // differential noise-reduction step, on the assumption that the quiet
    // slot was mostly ambient noise. Real hardware data disproved that:
    // MIC diag logs showed the "weak" slot tracking proportionally with
    // the strong slot's loud spikes (e.g. slot0 110->2818 alongside slot1
    // 47->247 in the same instant) — meaning it carries real, correlated
    // voice content, not independent noise, at both mic positions. Given
    // that, subtracting it was partially cancelling actual speech rather
    // than just background sound, which is a plausible contributor to the
    // hallucination/mishearing this was meant to fix in the first place.
    // Proper noise cancellation on a correlated signal needs phase/delay
    // alignment between the two mics, not a flat per-sample subtraction —
    // worth revisiting with that if two-mic SNR still isn't enough on its
    // own.
    int16_t sample = (abs1 > abs0) ? slot1 : slot0;
    int32_t rawAbs = abs((int32_t)sample);
    if (rawAbs > rawPeak) rawPeak = rawAbs;

    // 2. DC Blocking Filter — state stored in sys struct (not static locals)
    // y[n] = x[n] - x[n-1] + 0.9946 * y[n-1]
    float x = (float)sample;
    float y = x - (float)sys.dc_lastX + 0.9946f * sys.dc_lastY;
    sys.dc_lastX = sample;
    sys.dc_lastY = y;

    // 3. Digital Gain — increased to 12.0x for a robust signal.
    const float MIC_GAIN = 12.0f;
    float amplified = y * MIC_GAIN;
    
    if (amplified > 32767.0f) amplified = 32767.0f;
    if (amplified < -32768.0f) amplified = -32768.0f;
    dest[i] = (int16_t)amplified;
    int32_t pcmAbs = abs((int32_t)dest[i]);
    if (pcmAbs > pcmPeak) pcmPeak = pcmAbs;
  }

  // Smoothed left/right direction from this buffer's peaks: -1.0 fully
  // left, +1.0 fully right, 0.0 centered/unclear. Skipped on a near-silent
  // buffer so room noise/floor hiss doesn't slowly drag a real reading back
  // toward center between utterances. Single-writer (only micTask calls
  // this), so no critical section — matches ambientNoiseFloor just below.
  if (slot0Peak > 50 || slot1Peak > 50) {
    float instant = (float)(slot1Peak - slot0Peak) / (float)(slot1Peak + slot0Peak);
    sys.micDirection = sys.micDirection * 0.8f + instant * 0.2f;
  }
  sys.lastMicPeak = (slot0Peak > slot1Peak) ? slot0Peak : slot1Peak;

  uint32_t now = millis();
  if (now - lastMicDiagMs > 1000) {
    Serial.printf(
      "MIC diag: bytes=%u samples=%d slot0=%ld slot1=%ld raw=%ld pcm=%ld dir=%.2f\n",
      (unsigned)bytesRead,
      samples,
      (long)slot0Peak,
      (long)slot1Peak,
      (long)rawPeak,
      (long)pcmPeak,
      sys.micDirection
    );
    lastMicDiagMs = now;
  }

  return samples;
}

// ============================================================
//  BARGE-IN LOGIC: Detect user interruption during speech
// ============================================================
bool canTransmitMic(int16_t* pcm, int samples) {
  if (samples <= 0) return false;

  // Calculate Peak Amplitude for this chunk
  int32_t peak = 0;
  for (int i = 0; i < samples; i++) {
    int32_t v = abs((int32_t)pcm[i]);
    if (v > peak) peak = v;
  }

  bool playingNow;
  bool lockoutNow;
  uint32_t lockoutUntil;
  uint32_t graceUntil;
  int bargeFrames;
  portENTER_CRITICAL(&sysMux);
  playingNow = audioPlaying;
  lockoutNow = sys.speakerLockout;
  lockoutUntil = sys.speakerLockoutUntil;
  graceUntil = sys.postSpeechGraceUntil;
  bargeFrames = sys.bargeFramesCount;
  portEXIT_CRITICAL(&sysMux);

  bool isOutputting = playingNow || (millis() < lockoutUntil) || lockoutNow;
  bool inPostGrace  = (millis() < graceUntil);

  if (isOutputting || inPostGrace) {
    // During speech/lockout/grace: User MUST exceed the adaptive threshold AND the absolute floor
    float threshold = sys.ambientNoiseFloor * BARGE_IN_MULTIPLIER;
    
    // Speaker lockout OR playback — require much louder mic than normal conversation.
    if (playingNow || lockoutNow) {
      threshold *= BARGE_SELF_REJECT_MULT;
    }

    if (threshold < MIN_BARGE_THRESHOLD) threshold = MIN_BARGE_THRESHOLD;

    if (peak > (int)threshold) {
      portENTER_CRITICAL(&sysMux);
      sys.bargeFramesCount++;
      bargeFrames = sys.bargeFramesCount;
      portEXIT_CRITICAL(&sysMux);
      if (bargeFrames >= BARGE_CONSECUTIVE_FRAMES) {
        Serial.printf("BARGE-IN: peak=%ld thr=%d\n", (long)peak, (int)threshold);
        portENTER_CRITICAL(&sysMux);
        sys.speakerLockout = false;
        sys.speakerLockoutUntil = 0;
        sys.postSpeechGraceUntil = 0;
        sys.bargeFramesCount = 0;
        portEXIT_CRITICAL(&sysMux);
        flushAudioBuffer();
        enqueueTextMessage("[BARGE]");
        return true;
      }
    } else {
      portENTER_CRITICAL(&sysMux);
      sys.bargeFramesCount = 0;
      portEXIT_CRITICAL(&sysMux);
    }
    return false;
  }

  // Not speaking: reset barge count and update ambient noise floor estimate slowly
  portENTER_CRITICAL(&sysMux);
  sys.bargeFramesCount = 0;
  portEXIT_CRITICAL(&sysMux);
  if (peak > 50) { 
    sys.ambientNoiseFloor = (sys.ambientNoiseFloor * (1.0f - NOISE_FLOOR_ADAPT)) + (peak * NOISE_FLOOR_ADAPT);
  }

  return true;
}


// ============================================================
//  SETUP — One-time initialization
// ============================================================
// ── MIC CAPTURE TASK (Dedicated for gapless audio) ───────
void micTask(void* pvParameters) {
  uint32_t lastDirectionLookMs = 0;
  while (true) {
    int samples = readMicPcm(pcmMicBuffer, MIC_DMA_LEN);
    bool canStream = (WiFi.status() == WL_CONNECTED && sys.wsConnected && !otaActive);

    if (canStream && samples > 0) {
      if (canTransmitMic(pcmMicBuffer, samples)) {
        enqueueAudioPacket(pcmMicBuffer, samples);
      }
    }

    // Look toward whoever's talking. Gated on a real margin above the
    // adaptive noise floor (not just "louder than usual") so it reacts to
    // an actual voice, not room hum tipping slightly to one side, and
    // rate-limited so it reads as one glance per utterance rather than a
    // continuous, jittery tracking twitch every ~16ms buffer.
    bool clearVoice = sys.lastMicPeak > (int32_t)(sys.ambientNoiseFloor * 1.5f);
    if (samples > 0 && clearVoice && fabsf(sys.micDirection) > 0.15f) {
      uint32_t now = millis();
      if (now - lastDirectionLookMs > 400) {
        applyMicDirectionLook(sys.micDirection);
        lastDirectionLookMs = now;
      }
    }
  }
}

void setup() {
  // Disable brownout detector to prevent bootloops during power spikes
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  
  Serial.begin(115200);
  while (!Serial) delay(10);
  Serial.println("\nMBUDDY Starting...");
  delay(800);

  // Confirms the PSRAM build flag (board_build.arduino.memory_type =
  // qio_opi) actually took effect on this specific chip, rather than
  // trusting the build config silently worked — a mismatched octal/quad
  // setting fails to initialize PSRAM without necessarily erroring loudly.
  if (psramFound()) {
    Serial.printf("PSRAM: %u bytes total, %u free\n", ESP.getPsramSize(), ESP.getFreePsram());
  } else {
    Serial.println("PSRAM: NOT detected (check board_build.arduino.memory_type in platformio.ini)");
  }

  // Debug LED
  pinMode(DEBUG_LED, OUTPUT);
  digitalWrite(DEBUG_LED, LOW);

  Wire.begin(OLED_SDA, OLED_SCL);  // ESP32-S3: SDA=8, SCL=9
  Wire.setClock(100000);  // Reduced from 400kHz to 100kHz to lower power draw
  faceMutex = xSemaphoreCreateRecursiveMutex();

#if OLED_PROBE_ONLY
  runOledProbeOnly();
#endif

  // Start the face immediately so OLED wiring/driver problems are visible
  // even before WiFiManager or the backend connection is ready.
  initFaceSubsystem();
  xTaskCreatePinnedToCore(faceTask, "faceTask", 4096, NULL, 2, &faceTaskHandle, 1);

  // ── WiFi Manager (Non-blocking mode) ───────────────
  // Phase 1C: one extra field on the SAME existing captive portal — no new
  // portal, no new trigger. Prefilled from NVS so a silent reconnect (the
  // portal not shown at all, the normal case once WiFi is saved) can never
  // overwrite a previously-configured URL: the field just holds what's
  // already stored unless the user actually submits the form. `coreUrlBuf`
  // and `coreUrlParam` must outlive setup() (WiFiManager reads the buffer
  // for as long as the parameter/portal exists), hence `static`.
  static char coreUrlBuf[128] = "";
  hostStore.begin(HOST_STORE_NAMESPACE, true);
  hostStore.getString(HOST_STORE_KEY_URL, "").toCharArray(coreUrlBuf, sizeof(coreUrlBuf));
  hostStore.end();
  static WiFiManagerParameter coreUrlParam(
    "coreurl", "MBUDDY Core URL (blank = auto-discover, e.g. ws://host:5000/ws)",
    coreUrlBuf, sizeof(coreUrlBuf) - 1
  );
  wm.addParameter(&coreUrlParam);
  wm.setSaveParamsCallback([]() {
    String val = String(coreUrlParam.getValue());
    val.trim();
    hostStore.begin(HOST_STORE_NAMESPACE, false);
    String prior = hostStore.getString(HOST_STORE_KEY_URL, "");
    if (val != prior) {
      hostStore.putString(HOST_STORE_KEY_URL, val);
      Serial.printf("Core URL configured via portal: %s\n",
                     val.length() ? val.c_str() : "(cleared - back to auto)");
    }
    hostStore.end();
  });

  wm.setConfigPortalBlocking(false);
  wm.setConfigPortalTimeout(300);  // 5 minutes to configure — 2 was easy to miss
  wm.autoConnect("MBUDDY_SETUP");
  Serial.println("WiFi auto-connect initiated (non-blocking)");
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("WiFi connected: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("WiFi not connected yet - setup portal or saved network pending");
  }

  logHeap("boot-before-ring");

  // Audio hardware, queues, tasks, and the playback ring are deferred until
  // WiFi is connected so the setup portal keeps the largest possible heap.
  logHeap("boot-audio-deferred");
  Serial.println("Audio will start after WebSocket connects");

  // WebSocket starts in loop() after WiFi connects (ensureWebSocket)

  // Re-enable brownout so we are safe during operation
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 1);

  Serial.println("MBUDDY ready - waiting for brain...");
}

// ============================================================
//  STATUS LED — the only thing DEBUG_LED was ever missing: something
//  that actually turns it on. Previously set OUTPUT+LOW once at boot and
//  never touched again, so it always looked "dead" regardless of wiring.
//  Non-blocking (millis()-based), safe to call every loop() iteration.
//
//  Fast blink (4Hz) = no WiFi (setup portal active, or a saved network
//  that isn't answering — check MBUDDY_SETUP before assuming a wiring
//  fault). Slow blink (1Hz) = WiFi is up but the backend isn't. Solid on
//  = fully connected.
// ============================================================
void updateStatusLed() {
  if (sys.wsConnected) {
    digitalWrite(DEBUG_LED, HIGH);
    return;
  }
  static uint32_t lastToggleMs = 0;
  static bool ledOn = false;
  uint32_t interval = (WiFi.status() == WL_CONNECTED) ? 1000 : 250;
  uint32_t now = millis();
  if (now - lastToggleMs >= interval) {
    lastToggleMs = now;
    ledOn = !ledOn;
    digitalWrite(DEBUG_LED, ledOn ? HIGH : LOW);
  }
}

// ============================================================
//  LOOP — Three-Layer Architecture (Non-blocking)
// ============================================================
// ============================================================
//  OTA UPDATES (ArduinoOTA) with a download animation on the OLED
//
//  Update over Wi-Fi with no cable:
//    cd firmware/mbuddy && pio run -e ota -t upload --upload-port <board IP>
//  The password is a shared default (MBUDDY_OTA_PASSWORD here and --auth in
//  platformio.ini's [env:ota]): change both together.
//  While an update arrives the OLED shows an arrow dropping into a tray, falling
//  data dots, the percentage and a progress bar; then DONE, or the error.
// ============================================================
#ifndef MBUDDY_OTA_PASSWORD
#define MBUDDY_OTA_PASSWORD "mbuddy-ota"
#endif
#define OTA_FRAME_MS 120     // a 128x64 frame takes ~90 ms to send at 100 kHz

static uint32_t otaLastDraw = 0;
static uint32_t otaTick = 0;
static bool otaStarted = false;

static void drawOtaFrame(int pct, uint32_t t, bool done) {
  u8g2.clearBuffer();
  u8g2.setMaxClipWindow();
  u8g2.setDrawColor(1);
  u8g2.setFont(u8g2_font_6x12_tr);
  u8g2.drawStr(2, 10, done ? "DONE!" : "UPDATING");
  if (!done) {
    for (uint32_t i = 0; i < (t / 3) % 4; i++) u8g2.drawBox(52 + i * 5, 8, 2, 2);
  }
  char buf[8];
  snprintf(buf, sizeof(buf), "%d%%", pct);
  u8g2.drawStr(126 - u8g2.getStrWidth(buf), 10, buf);

  u8g2.drawLine(46, 36, 46, 44);                 // tray the arrow drops into
  u8g2.drawLine(46, 44, 82, 44);
  u8g2.drawLine(82, 44, 82, 36);

  if (done) {
    u8g2.drawLine(54, 26, 61, 33);               // tick mark
    u8g2.drawLine(61, 33, 76, 16);
    u8g2.drawLine(54, 27, 61, 34);
    u8g2.drawLine(61, 34, 76, 17);
  } else {
    int y = 10 + (int)(t % 14);
    u8g2.setClipWindow(0, 14, 127, 44);          // hidden above the title row, "inside" the tray at the bottom
    u8g2.drawBox(60, y, 8, 10);
    u8g2.drawTriangle(54, y + 10, 74, y + 10, 64, y + 20);
    u8g2.setMaxClipWindow();
    for (int k = 0; k < 3; k++) {
      u8g2.drawBox(28, 16 + (int)((t * 2 + k * 9) % 27), 2, 2);
      u8g2.drawBox(98, 16 + (int)((t * 2 + k * 9 + 13) % 27), 2, 2);
    }
  }
  u8g2.drawFrame(4, 50, 120, 10);
  u8g2.drawBox(6, 52, (116 * pct) / 100, 6);
  u8g2.sendBuffer();
}

static void otaOnStart() {
  otaActive = true;                              // faceTask and the mic stop; the update screen owns the display
  takeFace(500);                                 // wait out any frame already being sent, then hold the display
  otaTick = 0;
  otaLastDraw = 0;
  Serial.println("OTA: update started");
  drawOtaFrame(0, 0, false);
}

static void otaOnProgress(unsigned int done, unsigned int total) {
  int pct = total ? (int)((uint64_t)done * 100 / total) : 0;
  static int lastPrinted = -10;
  if (pct >= lastPrinted + 10 || pct == 100) { Serial.printf("OTA: %d%%\n", pct); lastPrinted = pct; }
  uint32_t now = millis();
  if (otaLastDraw != 0 && now - otaLastDraw < OTA_FRAME_MS && pct < 100) return;   // keep the display from slowing the download
  otaLastDraw = now;
  drawOtaFrame(pct, ++otaTick, false);
}

static void otaOnEnd() {
  Serial.println("OTA: complete, rebooting");
  drawOtaFrame(100, 0, true);
  delay(900);                                    // the board restarts right after this returns
}

static void otaOnError(ota_error_t e) {
  Serial.printf("OTA: failed (error %u), staying on the current firmware\n", (unsigned)e);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  u8g2.setFont(u8g2_font_ncenB10_tr);
  u8g2.drawStr(6, 24, "UPDATE FAILED");
  u8g2.setFont(u8g2_font_6x12_tr);
  char buf[24];
  snprintf(buf, sizeof(buf), "error %u - still", (unsigned)e);
  u8g2.drawStr(6, 42, buf);
  u8g2.drawStr(6, 55, "on the old firmware");
  u8g2.sendBuffer();
  delay(2500);
  giveFace();                                    // hand the display back to the face
  otaActive = false;
}

static void otaLoop() {
  if (!otaStarted && WiFi.status() == WL_CONNECTED) {
    ArduinoOTA.setHostname("mbuddy");
    ArduinoOTA.setPassword(MBUDDY_OTA_PASSWORD);
    ArduinoOTA.onStart(otaOnStart);
    ArduinoOTA.onProgress(otaOnProgress);
    ArduinoOTA.onEnd(otaOnEnd);
    ArduinoOTA.onError(otaOnError);
    ArduinoOTA.begin();
    otaStarted = true;
    Serial.println("OTA ready (hostname mbuddy)");
  }
  if (otaStarted) ArduinoOTA.handle();
}

void loop() {
  otaLoop();
  updateStatusLed();
  wm.process();
  pollSerialProtocol();

  if (WiFi.status() != WL_CONNECTED) {
    if (sys.wsStarted || sys.wsConnected) {
      Serial.println("WiFi lost - closing WS socket");
      // Bug 3 fix: explicitly disconnect so the library releases the TCP socket
      // rather than leaving a stale connection object that can corrupt state.
      wsClient.disconnect();
      resetWebSocketTxQueues();
    }
    sys.wsStarted = false;
    sys.wsConnected = false;
  } else {
    initFaceSubsystem();
    ensureWebSocket();

    // The WebSocket library reconnects on its own, but always to the address
    // it was handed at begin(). If that address has changed, it would retry a
    // dead host indefinitely — which is the failure the discovery beacon
    // exists to prevent. After a sustained outage, tear the client down so
    // ensureWebSocket() runs again and asks the network where the backend is.
    if (sys.wsConnected) {
      sys.wsDownSinceMs = 0;
    } else if (sys.wsStarted) {
      uint32_t now = millis();
      if (sys.wsDownSinceMs == 0) {
        sys.wsDownSinceMs = now;
      } else if (now - sys.wsDownSinceMs > WS_RERESOLVE_AFTER_MS) {
        Serial.println("WS down too long - re-resolving backend address");
        wsClient.disconnect();
        resetWebSocketTxQueues();
        sys.wsStarted = false;
        sys.wsDownSinceMs = 0;
      }
    }
  }

  // Drain WS faster while TTS is playing to keep the ring fed
  if (sys.wsStarted) {
    if (audioPlaying) {
      for (int i = 0; i < 12; i++) {
        wsClient.loop();
      }
    } else {
      wsClient.loop();
    }
  }
  drainWebSocketTxQueues();

  // ── BRAIN LAYER: Audio streaming to Python ────────────
  // Mic capture is now handled in a dedicated micTask on Core 1 (High Priority).
  // This ensures OLED rendering (~40ms) doesn't cause audio gaps.

  if (WiFi.status() == WL_CONNECTED && sys.wsConnected && millis() - sys.lastKeepalive > 30000) {
    enqueueTextMessage("keep-alive");
    sys.lastKeepalive = millis();
  }

  // ── RECOVERY & COMPANION LIFE ─────────────────────────
 if (!sys.listening && millis() - sys.lastActivity > 2000) {
  if (sys.current != sys.backendState) {
    applyMood(sys.backendState);
  }
}

  // Listening timeout (Focused)
  if (sys.listening && (millis() - sys.lastActivity > 15000)) {
    Serial.println("Listening timeout");
    sys.listening = false;
    applyMood(sys.backendState);
  }

  // ── AUTONOMIC LAYER: Face animation + display render ─
  if (audioPlaying) {
    delay(0);
  } else if (sys.current == eEmotions::Sleepy) {
    delay(10);
  } else {
    delay(1);
  }
}

// ============================================================
//  Init Audio Ring: Defer until WiFi is up to save heap
// ============================================================
void initAudioRing() {
  if (audioRingBuf != NULL) return;

  logHeap("pre-ring-alloc");
  for (size_t i = 0; i < AUDIO_RING_TRY_COUNT; i++) {
    size_t want = AUDIO_RING_TRY[i];
    audioRingBuf = xRingbufferCreate(want, RINGBUF_TYPE_BYTEBUF);
    if (audioRingBuf != NULL) {
      audioRingBytes = want;
      float sec = (float)want / 44100.0f;
      Serial.printf("Audio ring: %u B (~%.1fs mono @ 22.05kHz)\n", (unsigned)want, sec);
      break;
    }
    Serial.printf("Ring %u B failed (maxBlock=%u)\n", (unsigned)want, ESP.getMaxAllocHeap());
  }
  if (!audioRingBuf) {
    Serial.println("Critical: no audio ring buffer - voice playback will fail");
  }
  logHeap("post-ring-alloc");
}

void initAudioSubsystem() {
  if (audioSubsystemStarted) return;
  if (WiFi.status() != WL_CONNECTED) return;

  audioSubsystemStarted = true;
  logHeap("audio-init-start");

  initAudioRing();

  setupI2S();
  setupI2SSpeaker();
  logHeap("audio-after-i2s");

  audioTxQueue = xQueueCreate(16, sizeof(TxAudioPacket));
  textTxQueue = xQueueCreate(8, sizeof(TxTextMessage));
  if (audioTxQueue == NULL || textTxQueue == NULL) {
    Serial.println("TX queue allocation failed");
    logHeap("audio-queue-failed");
    return;
  }

  xTaskCreatePinnedToCore(audioTask, "audioTask", 4096, NULL, 4, &audioTaskHandle, 0);
  xTaskCreatePinnedToCore(micTask, "micTask", 4096, NULL, 5, &micTaskHandle, 1);
  logHeap("audio-init-done");
}

void faceTask(void* pvParameters) {
  while (true) {
    updateFaceIfDue();
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

void updateFaceIfDue() {
  if (otaActive) return;
  if (!face) return;
  uint32_t now = millis();
  uint32_t frameMs = 33;

  if (audioPlaying) {
    frameMs = 66;
  } else if (sys.current == eEmotions::Sleepy) {
    frameMs = 100;
  }

  if (now - lastFaceFrameMs < frameMs) return;
  lastFaceFrameMs = now;
  if (!takeFace(5)) return;
  face->Update();
  drawCompanionAction(now);

  // ── ENERGY OVERLAY (Subtle corner indicator) ───────
  // Lower brightness / discrete display as requested.
  // We draw this AFTER face->Update() so we must sendBuffer again.
  // On ESP32 @ 400kHz, this second pass is <2ms.
  if (!actionHidesEnergy()) {
  u8g2.setDrawColor(1);
  u8g2.setFont(u8g2_font_u8glib_4_hf); // Tiny 4px font
  
  // Draw a 10px wide energy bar in the bottom right corner
  int barW = map((int)sys.energyLevel, 0, 100, 0, 12);
  u8g2.drawFrame(114, 60, 14, 4);      // Border
  u8g2.drawBox(115, 61, barW, 2);       // Fill
  }
  
  u8g2.sendBuffer();
  giveFace();
}

void startAction(const String& name, uint32_t durationMs) {
  String clean = name;
  clean.trim();
  clean.toLowerCase();
  if (clean.length() == 0) return;
  clean.toCharArray(sys.activeAction, sizeof(sys.activeAction));
  sys.activeAction[sizeof(sys.activeAction) - 1] = '\0';
  uint32_t now = millis();
  sys.actionStartedMs = now;
  sys.actionUntilMs = now + max(durationMs, (uint32_t)250);
  sys.lastActivity = now;
  if (takeFace()) {
    if (face) face->DoBlink();
    giveFace();
  }
}

void parseActionTag(const String& msg) {
  int start = msg.indexOf("[ACT:");
  if (start < 0) return;
  int nameStart = start + 5;
  int colon = msg.indexOf(":", nameStart);
  int end = msg.indexOf("]", nameStart);
  if (end < 0) return;
  int nameEnd = (colon > nameStart && colon < end) ? colon : end;
  String name = msg.substring(nameStart, nameEnd);
  uint32_t duration = 1500;
  if (colon > nameStart && colon < end) {
    duration = (uint32_t)msg.substring(colon + 1, end).toInt();
  }
  name.trim();
  name.toLowerCase();
  if (name == "clear" || name == "idle" || name == "none" || name == "off") {
    sys.activeAction[0] = '\0';
    sys.actionStartedMs = 0;
    sys.actionUntilMs = 0;
    return;
  }
  startAction(name, duration);
}

void parseTimeTag(const String& msg) {
  int start = msg.indexOf("[TIME:");
  if (start < 0) return;
  int end = msg.indexOf("]", start);
  if (end < 0) return;
  String body = msg.substring(start + 6, end);
  int p1 = body.indexOf(":");
  int p2 = body.indexOf(":", p1 + 1);
  if (p1 < 0 || p2 < 0) return;
  int hh = body.substring(0, p1).toInt();
  int mm = body.substring(p1 + 1, p2).toInt();
  int ss = body.substring(p2 + 1).toInt();
  if (hh < 0 || hh > 23 || mm < 0 || mm > 59 || ss < 0 || ss > 59) return;
  sys.clockHour = (uint8_t)hh;
  sys.clockMinute = (uint8_t)mm;
  sys.clockSecond = (uint8_t)ss;
  sys.clockSyncedMs = millis();
}

void pollSerialProtocol() {
  static String line = "";
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (line.length() > 0) {
        processSerialProtocolLine(line);
        line = "";
      }
      continue;
    }
    if (line.length() < 160) {
      line += c;
    } else {
      line = "";
    }
  }
}

void processSerialProtocolLine(String msg) {
  msg.trim();
  if (msg.length() == 0) return;
  Serial.printf("SERIAL CMD %s\n", msg.c_str());
  sys.lastActivity = millis();
  parseTimeTag(msg);

  bool isSpeaking = (msg.indexOf("[SPEAKING]") >= 0);
  bool isQuiet    = (msg.indexOf("[QUIET]") >= 0);
  bool isBarge    = (msg.indexOf("[BARGE]") >= 0);
  bool isHeard    = msg.startsWith("[HEARD]");
  bool isThinking = (msg.indexOf("[THINKING]") >= 0);
  bool isInner    = (msg.indexOf("[INNER]") >= 0);

  if (msg.indexOf("[ACT:") >= 0) parseActionTag(msg);

  if (msg.indexOf("[SFX:") >= 0) {
    if (msg.indexOf("[SFX:laser_pew]") >= 0 || msg.indexOf("[SFX:chiptune_loop]") >= 0) startAction("joystick", 1800);
    else if (msg.indexOf("[SFX:data_blip]") >= 0 || msg.indexOf("[SFX:data_drone]") >= 0) startAction("data_weaver", 1600);
    else if (msg.indexOf("[SFX:cascade_arp]") >= 0) startAction("pixel_cascade", 1800);
    else if (msg.indexOf("[SFX:ecg_beep]") >= 0) startAction("heartbeat", 1600);
    else if (msg.indexOf("[SFX:bubble_pop]") >= 0 || msg.indexOf("[SFX:bounce_click]") >= 0) startAction("thought_bubble", 1600);
    else if (msg.indexOf("[SFX:paddle_bounce]") >= 0 || msg.indexOf("[SFX:brick_hit]") >= 0) startAction("block_break", 1800);
    else if (msg.indexOf("[SFX:star_tinkle]") >= 0) startAction("starlight", 1800);
    else if (msg.indexOf("[SFX:clock_tick]") >= 0) startAction("clock_timer", 1600);
    else if (msg.indexOf("[SFX:sonar_ping]") >= 0) startAction("wifi_search", 1700);
    else if (msg.indexOf("[SFX:charge_hum]") >= 0) startAction("battery_charge", 1800);
    else if (msg.indexOf("[SFX:glitch_screech]") >= 0) startAction("error_glitch", 1400);
    else if (msg.indexOf("[SFX:dragon_breath]") >= 0) startAction("dragon_fire", 2000);
    else if (msg.indexOf("[SFX:sleep_dream]") >= 0) startAction("dreaming", 2600);
    else if (msg.indexOf("[SFX:success_chime]") >= 0) startAction("celebration", 1800);
    else if (msg.indexOf("[SFX:listen_ping]") >= 0) startAction("listening_animation", 1800);
  }

  int stateIdx = msg.indexOf("[STATE:");
  if (stateIdx >= 0) {
    int endIdx = msg.indexOf("]", stateIdx);
    if (endIdx > stateIdx) {
      String stateTag = msg.substring(stateIdx, endIdx + 1);
      sys.backendState = tagToEmotion(stateTag);
      Serial.printf("Serial State Sync: %d\n", (int)sys.backendState);
    }
  }

  int energyIdx = msg.indexOf("[ENERGY:");
  if (energyIdx >= 0) {
    int endIdx = msg.indexOf("]", energyIdx);
    if (endIdx > energyIdx) {
      String eVal = msg.substring(energyIdx + 8, endIdx);
      float ev = eVal.toFloat();
      if (ev >= 0) sys.energyLevel = ev;
      Serial.printf("Serial Energy Sync: %.1f\n", sys.energyLevel);
    }
  }

  if (msg.indexOf("[STOP]") >= 0 || isBarge) {
    flushAudioBuffer();
    portENTER_CRITICAL(&sysMux);
    sys.speakerLockout = false;
    sys.speakerLockoutUntil = 0;
    sys.postSpeechGraceUntil = 0;
    sys.bargeFramesCount = 0;
    portEXIT_CRITICAL(&sysMux);
  }

  if (isSpeaking) {
    portENTER_CRITICAL(&sysMux);
    sys.speakerLockout = true;
    sys.listening = false;
    sys.bargeFramesCount = 0;
    portEXIT_CRITICAL(&sysMux);
  }

  if (isQuiet) {
    uint32_t now = millis();
    portENTER_CRITICAL(&sysMux);
    sys.speakerLockout = false;
    sys.speakerLockoutUntil = now + SPEAKER_LOCKOUT_MS;
    sys.postSpeechGraceUntil = now + POST_TTS_GRACE_MS;
    sys.listening = false;
    sys.bargeFramesCount = 0;
    portEXIT_CRITICAL(&sysMux);
  }

  if (isHeard) {
    sys.listening = true;
    if (takeFace()) {
      if (face) {
        face->Look.LookAt(-0.3f, 0.0f);
        face->DoBlink();
      }
      giveFace();
    }
  }
  if (isThinking) {
    sys.listening = true;
    if (takeFace()) {
      if (face) face->Look.LookAt(0.3f, 0.3f);
      giveFace();
    }
  }
  if (isInner) {
    if (takeFace()) {
      if (face) {
        face->Look.LookAt(random(-5, 5) / 10.0f, random(-3, 3) / 10.0f);
        face->DoBlink();
      }
      giveFace();
    }
  }

  eEmotions targetMood = sys.backendState;
  if (isHeard || isThinking) targetMood = eEmotions::Focused;
  else if (msg.indexOf("[STATE:") >= 0) targetMood = parseStateTagOnly(msg);
  if (isSpeaking || isQuiet || isBarge) sys.listening = false;
  else if (isHeard || isThinking) sys.listening = true;
  if (targetMood != sys.current) applyMood(targetMood);
  if (!sys.listening) {
    if (takeFace()) {
      if (face) face->Look.LookAt(0.0f, 0.0f);
      giveFace();
    }
  }
}

void drawCompanionAction(uint32_t now) {
  if (sys.activeAction[0] == '\0') return;
  if ((int32_t)(now - sys.actionUntilMs) >= 0) {
    sys.activeAction[0] = '\0';
    sys.actionStartedMs = 0;
    sys.actionUntilMs = 0;
    return;
  }

  String action = String(sys.activeAction);
  if (action == "joystick") {
    drawJoystickAction(now);
  } else if (action == "interactive_arena") {
    drawInteractiveArenaAction(now);
  } else if (action == "dragon_fire") {
    drawDragonFireAction(now);
  } else if (action == "data_weaver") {
    drawDataWeaverAction(now);
  } else if (action == "pixel_cascade") {
    drawPixelCascadeAction(now);
  } else if (action == "heartbeat") {
    drawHeartbeatAction(now);
  } else if (action == "thought_bubble") {
    drawThoughtBubbleAction(now);
  } else if (action == "block_break") {
    drawBlockBreakAction(now);
  } else if (action == "starlight") {
    drawStarlightAction(now);
  } else if (action == "clock_timer") {
    drawClockTimerAction(now);
  } else if (action == "wifi_search") {
    drawWifiSearchAction(now);
  } else if (action == "battery_charge") {
    drawBatteryChargeAction(now);
  } else if (action == "error_glitch") {
    drawErrorGlitchAction(now);
  } else if (action == "scanning_targeting") {
    drawScanningTargetAction(now);
  } else if (action == "dreaming") {
    drawDreamingAction(now);
  } else if (action == "celebration") {
    drawCelebrationAction(now);
  } else if (action == "listening_animation") {
    drawListeningAction(now);
  }
}

uint32_t currentActionElapsed(uint32_t now) {
  return now - sys.actionStartedMs;
}

uint32_t currentActionDuration() {
  return max((uint32_t)250, sys.actionUntilMs - sys.actionStartedMs);
}

uint8_t currentActionStep(uint32_t now, uint16_t frameMs, uint8_t modulo) {
  if (modulo == 0) return 0;
  return (uint8_t)((currentActionElapsed(now) / max((uint16_t)1, frameMs)) % modulo);
}

uint8_t currentActionFrame(uint32_t now) {
  return (uint8_t)((currentActionElapsed(now) / 33) % 60) + 1;
}

uint8_t prng8(uint16_t seed) {
  seed ^= seed << 7;
  seed ^= seed >> 9;
  seed ^= seed << 8;
  return (uint8_t)(seed & 0xFF);
}

bool isPixelInEyeBounds(uint8_t x, uint8_t y) {
  return y <= 31 && x >= 24 && x <= 104;
}

bool actionHidesEnergy() {
  return sys.activeAction[0] != '\0';
}

int triWave(uint16_t step, int lo, int hi) {
  int span = hi - lo;
  if (span <= 0) return lo;
  int p = step % (span * 2);
  if (p > span) p = (span * 2) - p;
  return lo + p;
}

void drawTinyLeftEyes() {
  u8g2.setDrawColor(1);
  u8g2.drawFrame(5, 43, 12, 8);
  u8g2.drawFrame(21, 43, 12, 8);
  u8g2.drawBox(10, 46, 2, 2);
  u8g2.drawBox(26, 46, 2, 2);
}

void drawJoystickAt(int cx, int cy, bool rightLean) {
  int dx = rightLean ? 3 : -3;
  u8g2.drawFrame(cx - 2, cy - 1, 5, 2);
  u8g2.drawLine(cx, cy - 1, cx + dx, cy - 6);
  u8g2.drawDisc(cx + dx, cy - 7, 2);
}

void drawStickman(int x, int footY, bool runFrame, bool guitar) {
  int torsoY = footY - 10;
  u8g2.drawCircle(x, torsoY - 6, 2);
  u8g2.drawLine(x, torsoY - 4, x, torsoY + 3);
  u8g2.drawLine(x, torsoY - 1, x - 4, torsoY + 2);
  u8g2.drawLine(x, torsoY - 1, x + 4, torsoY + 2);
  if (runFrame) {
    u8g2.drawLine(x, torsoY + 3, x - 2, footY);
    u8g2.drawLine(x, torsoY + 3, x + 2, footY);
  } else {
    u8g2.drawLine(x, torsoY + 3, x - 1, footY);
    u8g2.drawLine(x, torsoY + 3, x + 1, footY);
  }
  if (guitar) {
    u8g2.drawLine(x - 3, torsoY, x + 2, torsoY + 4);
    u8g2.drawPixel(x + 3, torsoY + 4);
    u8g2.drawPixel(x + 2, torsoY + 5);
  }
}

void drawDottedHLine(uint8_t y) {
  for (uint8_t x = 0; x < 128; x += 2) {
    if (!isPixelInEyeBounds(x, y)) u8g2.drawPixel(x, y);
  }
}

void drawTinyStickman(uint8_t x, uint8_t floorY, bool runFrame, bool attackRight) {
  u8g2.drawBox(x, floorY - 7, 2, 2);
  u8g2.drawLine(x, floorY - 5, x, floorY - 3);
  if (attackRight) u8g2.drawLine(x - 1, floorY - 4, x + 4, floorY - 4);
  else u8g2.drawLine(x - 2, floorY - 4, x + 2, floorY - 4);
  if (runFrame) {
    u8g2.drawLine(x, floorY - 2, x - 2, floorY - 1);
    u8g2.drawLine(x, floorY - 2, x + 2, floorY - 1);
  } else {
    u8g2.drawLine(x - 1, floorY - 2, x - 1, floorY - 1);
    u8g2.drawLine(x + 1, floorY - 2, x + 1, floorY - 1);
  }
}

void drawDragonHead(int x, int y, bool interlace) {
  for (int i = 0; i < 24; i++) {
    if (interlace && (i & 1)) continue;
    int px = x + i;
    if (px < 0 || px > 127) continue;
    if (i < 12) u8g2.drawPixel(px, y - 10 + (i >> 1));
    else u8g2.drawPixel(px, y - 4 - ((i - 12) >> 1));
  }
  if (!interlace) {
    u8g2.drawLine(x + 3, y - 9, x + 20, y - 3);
    u8g2.drawLine(x + 5, y + 7, x + 22, y + 2);
    u8g2.drawLine(x + 8, y - 2, x + 23, y + 2);
    u8g2.drawPixel(x + 14, y - 5);
    u8g2.drawPixel(x + 19, y + 4);
  }
}

void drawCrossStar(uint8_t x, uint8_t y, bool bright) {
  u8g2.drawPixel(x, y);
  if (!bright) return;
  if (x > 0) u8g2.drawPixel(x - 1, y);
  if (x < 127) u8g2.drawPixel(x + 1, y);
  if (y > 0) u8g2.drawPixel(x, y - 1);
  if (y < 63) u8g2.drawPixel(x, y + 1);
}

void drawIconFrame() {
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  u8g2.drawFrame(0, 0, 128, 64);
  u8g2.drawFrame(2, 2, 124, 60);
}

void drawReferenceEyes(uint8_t cx, uint8_t cy) {
  u8g2.drawBox(cx - 17, cy - 5, 12, 10);
  u8g2.drawBox(cx + 5, cy - 5, 12, 10);
  u8g2.drawPixel(cx - 6, cy - 3);
  u8g2.drawPixel(cx + 4, cy - 3);
}

void drawReferenceStickman(uint8_t x, uint8_t y, bool armUp) {
  u8g2.drawDisc(x, y, 4);
  u8g2.drawLine(x, y + 4, x, y + 19);
  u8g2.drawLine(x, y + 8, x - 9, y + 17);
  if (armUp) u8g2.drawLine(x, y + 8, x + 9, y + 2);
  else u8g2.drawLine(x, y + 8, x + 9, y + 17);
  u8g2.drawLine(x, y + 19, x - 8, y + 31);
  u8g2.drawLine(x, y + 19, x + 8, y + 31);
}

void drawReferenceRobot(uint8_t x, uint8_t y) {
  u8g2.drawFrame(x, y, 11, 9);
  u8g2.drawPixel(x + 3, y + 3);
  u8g2.drawPixel(x + 7, y + 3);
  u8g2.drawHLine(x + 3, y + 6, 5);
  u8g2.drawLine(x + 2, y + 9, x - 2, y + 16);
  u8g2.drawLine(x + 8, y + 9, x + 13, y + 16);
  u8g2.drawLine(x + 1, y + 12, x - 5, y + 12);
  u8g2.drawLine(x + 10, y + 12, x + 16, y + 12);
}

void drawReferenceHeart(uint8_t x, uint8_t y) {
  u8g2.drawBox(x + 2, y, 4, 3);
  u8g2.drawBox(x + 8, y, 4, 3);
  u8g2.drawBox(x, y + 3, 14, 4);
  u8g2.drawBox(x + 2, y + 7, 10, 3);
  u8g2.drawBox(x + 4, y + 10, 6, 3);
  u8g2.drawBox(x + 6, y + 13, 2, 2);
}

void drawBitmap16(int x, int y, const uint8_t* sprite) {
  u8g2.drawXBMP(x, y, 16, 16, sprite);
}

void drawRobotHead(int x, int y, uint8_t mood) {
  u8g2.drawVLine(x + 8, y, 3);
  u8g2.drawPixel(x + 7, y);
  u8g2.drawPixel(x + 9, y);
  u8g2.drawBox(x, y + 8, 2, 5);
  u8g2.drawBox(x + 14, y + 8, 2, 5);
  u8g2.drawRFrame(x + 2, y + 3, 12, 12, 2);
  if (mood == 1) {
    u8g2.drawHLine(x + 4, y + 8, 3);
    u8g2.drawHLine(x + 9, y + 8, 3);
    u8g2.drawPixel(x + 5, y + 12);
    u8g2.drawPixel(x + 10, y + 12);
    return;
  }
  if (mood == 2) {
    drawCrossStar(x + 5, y + 8, true);
    drawCrossStar(x + 10, y + 8, true);
    u8g2.drawHLine(x + 5, y + 12, 6);
    return;
  }
  u8g2.drawBox(x + 4, y + 7, 3, 3);
  u8g2.drawBox(x + 9, y + 7, 3, 3);
  u8g2.drawHLine(x + 5, y + 12, 6);
}

void drawJoystickAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  drawIconFrame();
  drawReferenceStickman(30, 19, (f & 1) == 0);
  drawReferenceStickman(96, 19, (f & 1) != 0);
  int flame = 2 + (f % 4);
  u8g2.drawDisc(64, 43, 8);
  u8g2.setDrawColor(0);
  u8g2.drawTriangle(56, 45, 64, 26 + flame, 72, 45);
  u8g2.setDrawColor(1);
  u8g2.drawLine(58, 49, 64, 31 + flame);
  u8g2.drawLine(70, 49, 64, 31 + flame);
  for (uint8_t i = 0; i < 8; i++) {
    uint8_t sx = 48 + i * 4;
    uint8_t sy = 28 + ((f + i) & 0x07);
    u8g2.drawPixel(sx, sy);
  }
}

void drawInteractiveArenaAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  drawIconFrame();
  drawJoystickAt(17, 58, (f & 1) != 0);
  for (uint8_t x = 37; x < 119; x += 14) u8g2.drawVLine(x, 8, 45);
  for (uint8_t y = 12; y < 55; y += 12) u8g2.drawHLine(34, y, 84);
  u8g2.setDrawColor(0);
  u8g2.drawBox(38, 20, 22, 10);
  u8g2.drawBox(64, 44, 30, 9);
  u8g2.setDrawColor(1);
  int pathX = 40 + triWave(f * 3, 0, 58);
  int pathY = 24 + ((f / 12) % 2) * 18;
  drawRobotHead(pathX, pathY, 0);
  u8g2.drawFrame(106, 47, 9, 9);
  u8g2.drawPixel(110, 51);
  u8g2.drawLine(pathX + 18, pathY + 8, pathX + 24, pathY + 8);
  u8g2.drawLine(pathX + 24, pathY + 8, pathX + 21, pathY + 5);
  u8g2.drawLine(pathX + 24, pathY + 8, pathX + 21, pathY + 11);
}

void drawDragonFireAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  if (f <= 15) {
    int x = 127 - (((int)f * 57) / 15);
    drawDragonHead(x, 32, false);
    return;
  }
  if (f <= 40) {
    int jitter = (f & 1) ? 1 : -1;
    drawDragonHead(70, 32 + jitter, false);
    int waveX = 70 - ((int)(f - 16) * 5);
    for (int x = waveX; x < waveX + 64; x++) {
      if (x < 0 || x > 127) continue;
      for (int y = 24; y < 40; y++) {
        if (((x + y + f) & 1) == 0) u8g2.drawPixel(x, y);
      }
    }
    return;
  }
  if (f <= 55) {
    int waveX = -55 - ((int)(f - 41) * 5);
    for (int x = waveX; x < waveX + 64; x++) {
      if (x < 0 || x > 127) continue;
      for (int y = 24; y < 40; y++) {
        if (((x + y + f) & 1) == 0) u8g2.drawPixel(x, y);
      }
    }
    drawDragonHead(70 + ((int)(f - 41) * 9), 32, true);
    return;
  }
}

void drawDataWeaverAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  u8g2.drawCircle(64, 30, 17);
  u8g2.drawCircle(64, 30, 10);
  u8g2.drawBox(58, 26, 12, 8);
  u8g2.setDrawColor(0);
  u8g2.drawBox(61, 28, 6, 4);
  u8g2.setDrawColor(1);
  const int8_t ends[][2] = {{7, 8}, {22, 48}, {41, 9}, {93, 10}, {112, 48}, {124, 23}};
  for (uint8_t i = 0; i < 6; i++) {
    int ex = ends[i][0];
    int ey = ends[i][1];
    uint8_t grow = min((uint8_t)100, (uint8_t)((f * 3 + i * 11) % 100));
    int mx = 64 + (((ex - 64) * grow) / 100);
    int my = 30 + (((ey - 30) * grow) / 100);
    u8g2.drawLine(64, 30, mx, my);
    if (grow > 70) u8g2.drawFrame(ex - 2, ey - 2, 5, 5);
    int px = 64 + (((ex - 64) * ((f * 5 + i * 17) % 100)) / 100);
    int py = 30 + (((ey - 30) * ((f * 5 + i * 17) % 100)) / 100);
    if (!isPixelInEyeBounds(px, py)) u8g2.drawBox(px, py, 2, 2);
  }
}

void drawPixelCascadeAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  const uint8_t cols[] = {12, 27, 39, 54, 68, 83, 101, 118};
  for (uint8_t i = 0; i < 8; i++) {
    int y = ((int)f * 3 + i * 9) % 72 - 8;
    if (y >= 0 && y < 64) u8g2.drawVLine(cols[i], y, min(3, 64 - y));
    for (uint8_t t = 1; t <= 6; t++) {
      int ty = y - (t * 4);
      if (ty < 0 || ty > 63) continue;
      if (((0x94 >> ((t + f + i) & 7)) & 1) != 0) u8g2.drawPixel(cols[i], ty);
    }
    if (y >= 62) u8g2.drawHLine(cols[i] - 1, 62, 3);
  }
  if ((f & 7) < 3) drawRobotHead(56, 24, 0);
}

int ecgY(uint8_t p) {
  if (p == 11 || p == 12) return 51;
  if (p == 13) return 58;
  if (p == 15) return 34;
  if (p == 17) return 62;
  if (p >= 20 && p <= 24) return (p == 21 || p == 22) ? 50 : 54;
  return 54;
}

void drawHeartbeatAction(uint32_t now) {
  uint8_t shift = currentActionStep(now, 33, 60) * 2;
  uint8_t pulse = ((currentActionFrame(now) % 30) < 5) ? 1 : 0;
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  drawRobotHead(53, 5, 0);
  u8g2.drawFrame(45, 21, 38, 20);
  drawReferenceHeart(60, 24 - pulse);
  if (pulse) u8g2.drawCircle(67, 31, 16);
  u8g2.setDrawColor(1);
  u8g2.drawHLine(0, 54, 128);
  for (uint8_t x = 8; x < 119; x++) {
    uint8_t p0 = (x + shift) % 60;
    uint8_t p1 = (x + 1 + shift) % 60;
    u8g2.drawLine(x, ecgY(p0), x + 1, ecgY(p1));
  }
}

void drawThoughtBubbleAction(uint32_t now) {
  uint16_t step = currentActionElapsed(now) / 33;
  uint8_t f = currentActionFrame(now);
  int tx = triWave(step * 2, 52, 124);
  int ty = triWave(step + 11, 4, 48);
  u8g2.clearBuffer();
  drawRobotHead(6, 43, 0);
  if (f >= 1) u8g2.drawPixel(38, 42);
  if (f >= 5) u8g2.drawPixel(42, 34);
  if (f >= 9) u8g2.drawPixel(46, 26);
  if (f >= 12) {
    u8g2.drawRFrame(50, 2, 76, 48, 4);
    if (f > 22) drawBitmap16(81, 20, ROBOT_IDEA_BULB_16);
    for (uint8_t i = 0; i < 5; i++) {
      uint8_t sx = 58 + i * 8;
      uint8_t sy = 15 + ((f + i) & 3);
      drawCrossStar(sx, sy, ((f + i * 5) & 7) < 2);
    }
  }
  if (f >= 13) u8g2.drawBox(tx, ty, 2, 2);
}

void drawBlockBreakAction(uint32_t now) {
  uint16_t step = currentActionElapsed(now) / 33;
  uint8_t destroyed = min((uint8_t)32, (uint8_t)(step / 8));
  int ballX = triWave(step * 2, 1, 126);
  int ballY = triWave(step * 2, 10, 59);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  uint8_t idx = 0;
  for (uint8_t row = 0; row < 2; row++) {
    for (uint8_t col = 0; col < 16; col++) {
      if (idx++ < destroyed) continue;
      u8g2.drawBox(4 + col * 8, 3 + row * 5, 6, 2);
    }
  }
  int paddleX = constrain(ballX - 5, 0, 118);
  drawRobotHead(paddleX - 3, 44, 0);
  u8g2.drawBox(paddleX, 60, 10, 2);
  u8g2.drawPixel(ballX, ballY);
  u8g2.setFont(u8g2_font_u8glib_4_hf);
  u8g2.drawStr(96, 18, "SCORE");
  char score[4];
  snprintf(score, sizeof(score), "%02u", destroyed);
  u8g2.drawStr(108, 24, score);
}

void drawStarlightAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  const uint8_t xs[] = {13, 31, 49, 72, 95, 113, 40, 86};
  const uint8_t ys[] = {12, 45, 18, 50, 15, 40, 30, 28};
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  drawRobotHead(56, 42, 1);
  u8g2.drawCircle(20, 12, 6);
  u8g2.setDrawColor(0);
  u8g2.drawCircle(24, 10, 6);
  u8g2.setDrawColor(1);
  for (uint8_t i = 0; i < 8; i++) {
    bool bright = ((f + i * 7) % 45) >= 16 && ((f + i * 7) % 45) <= 20;
    drawCrossStar(xs[i], ys[i], bright);
  }
  if (f > 18 && f < 31) u8g2.drawLine(88 + f, 9 + (f >> 1), 78 + f, 14 + (f >> 1));
}

void drawClockTimerAction(uint32_t now) {
  static const int16_t lookup_cos[60] = {0, 27, 53, 79, 104, 128, 150, 171, 190, 207, 222, 234, 243, 250, 255, 256, 255, 250, 243, 234, 222, 207, 190, 171, 150, 128, 104, 79, 53, 27, 0, -27, -53, -79, -104, -128, -150, -171, -190, -207, -222, -234, -243, -250, -255, -256, -255, -250, -243, -234, -222, -207, -190, -171, -150, -128, -104, -79, -53, -27};
  static const int16_t lookup_sin[60] = {-256, -255, -250, -243, -234, -222, -207, -190, -171, -150, -128, -104, -79, -53, -27, 0, 27, 53, 79, 104, 128, 150, 171, 190, 207, 222, 234, 243, 250, 255, 256, 255, 250, 243, 234, 222, 207, 190, 171, 150, 128, 104, 79, 53, 27, 0, -27, -53, -79, -104, -128, -150, -171, -190, -207, -222, -234, -243, -250, -255};
  uint32_t elapsed = (sys.clockSyncedMs > 0) ? ((now - sys.clockSyncedMs) / 1000) : (now / 1000);
  uint32_t total = (uint32_t)sys.clockHour * 3600UL + (uint32_t)sys.clockMinute * 60UL + sys.clockSecond + elapsed;
  uint8_t hour24 = (total / 3600UL) % 24;
  uint8_t minute = (total / 60UL) % 60;
  uint8_t second = total % 60;
  uint8_t hour = ((hour24 % 12) * 5 + (minute / 12)) % 60;
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  drawRobotHead(8, 8, (hour24 >= 20 || hour24 < 6) ? 1 : 0);
  if (hour24 >= 20 || hour24 < 6) {
    u8g2.drawCircle(112, 11, 5);
    u8g2.setDrawColor(0);
    u8g2.drawCircle(115, 9, 5);
    u8g2.setDrawColor(1);
  } else {
    u8g2.drawCircle(112, 11, 5);
    u8g2.drawLine(112, 3, 112, 0);
    u8g2.drawLine(112, 22, 112, 19);
    u8g2.drawLine(104, 11, 101, 11);
    u8g2.drawLine(124, 11, 121, 11);
  }
  u8g2.drawCircle(64, 34, 20);
  u8g2.drawPixel(64, 13);
  u8g2.drawPixel(85, 34);
  u8g2.drawPixel(64, 55);
  u8g2.drawPixel(43, 34);
  int mx = 64 + ((14 * lookup_cos[minute]) >> 8);
  int my = 34 + ((14 * lookup_sin[minute]) >> 8);
  int hx = 64 + ((8 * lookup_cos[hour]) >> 8);
  int hy = 34 + ((8 * lookup_sin[hour]) >> 8);
  int sx = 64 + ((17 * lookup_cos[second]) >> 8);
  int sy = 34 + ((17 * lookup_sin[second]) >> 8);
  u8g2.drawLine(64, 34, hx, hy);
  u8g2.drawLine(64, 34, mx, my);
  u8g2.drawPixel(sx, sy);
  u8g2.drawDisc(64, 34, 1);
  u8g2.setFont(u8g2_font_5x8_tf);
  char timeText[9];
  snprintf(timeText, sizeof(timeText), "%02u:%02u", hour24, minute);
  u8g2.drawStr(52, 63, timeText);
}

void drawWifiSearchAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  drawRobotHead(8, 40, 0);
  u8g2.drawCircle(24, 37, 4);
  u8g2.drawCircle(32, 37, 4);
  u8g2.drawLine(28, 37, 43, 30);
  u8g2.drawDisc(64, 54, 3);
  if (f >= 8) u8g2.drawCircle(64, 54, 10, U8G2_DRAW_UPPER_LEFT | U8G2_DRAW_UPPER_RIGHT);
  if (f >= 18) u8g2.drawCircle(64, 54, 20, U8G2_DRAW_UPPER_LEFT | U8G2_DRAW_UPPER_RIGHT);
  if (f >= 30) u8g2.drawCircle(64, 54, 31, U8G2_DRAW_UPPER_LEFT | U8G2_DRAW_UPPER_RIGHT);
  const uint8_t nx[] = {87, 105, 118};
  const uint8_t ny[] = {28, 14, 42};
  for (uint8_t i = 0; i < 3; i++) {
    bool locked = f > 38 || ((f + i * 8) & 15) < 8;
    if (locked) u8g2.drawFrame(nx[i], ny[i], 5, 5);
  }
  if (f > 42) drawCrossStar(104, 54, true);
}

void drawBatteryChargeAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  uint8_t active = 0;
  if (f >= 15) active = 1;
  if (f >= 25) active = 2;
  if (f >= 35) active = 3;
  if (f >= 45) active = 4;
  bool flash = f > 50 && ((f / 5) % 2 == 0);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  drawRobotHead(6, 38 + ((f > 45 && (f & 3) == 0) ? -2 : 0), (f > 45) ? 2 : 0);
  u8g2.drawFrame(42, 22, 52, 22);
  u8g2.drawBox(94, 29, 4, 8);
  if (flash) active = 0;
  for (uint8_t b = 0; b < active; b++) u8g2.drawBox(46 + (b * 10), 26, 7, 16);
  u8g2.drawLine(22, 48, 38, 38);
  u8g2.drawLine(38, 38, 42, 38);
  u8g2.drawBox(36, 35, 4, 5);
  for (uint8_t i = 0; i < active + 1; i++) {
    uint8_t ex = 35 + ((f * 2 + i * 13) % 52);
    uint8_t ey = 16 + (prng8(f + i * 19) % 11);
    u8g2.drawPixel(ex, ey);
  }
  if (active >= 4 && !flash) drawCrossStar(112, 21, true);
}

void drawErrorGlitchAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  for (uint8_t x = 7; x < 122; x += 9) {
    u8g2.drawVLine(x, 5, 54);
    u8g2.drawPixel(x + 3, 12 + ((x + f) & 15));
  }
  for (uint8_t y = 8; y < 58; y += 10) {
    int tear = ((f & 3) == 0 && (y == 18 || y == 38)) ? ((y == 18) ? -12 : 14) : 0;
    u8g2.drawHLine(max(0, 8 + tear), y, 112 - abs(tear));
  }
  drawRobotHead(8 + ((f & 4) ? 2 : 0), 43, 0);
  u8g2.drawLine(24, 50, 48, 33);
  u8g2.drawLine(23, 53, 54, 46);
  if ((f & 4) == 0) {
    u8g2.drawLine(64, 12, 44, 52);
    u8g2.drawLine(64, 12, 84, 52);
    u8g2.drawLine(44, 52, 84, 52);
    u8g2.drawBox(63, 26, 3, 16);
    u8g2.drawBox(63, 46, 3, 3);
  }
  if (f > 34) {
    uint8_t w = max((int)1, 128 - ((int)(f - 34) * 7));
    u8g2.clearBuffer();
    u8g2.drawHLine((128 - w) / 2, 32, w);
  }
}

void drawScanningTargetAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  u8g2.drawCircle(64, 32, 23);
  u8g2.drawCircle(64, 32, 10);
  u8g2.drawBox(54, 27, 20, 10);
  u8g2.setDrawColor(0);
  u8g2.drawBox(60, 30, 8, 4);
  u8g2.setDrawColor(1);
  uint8_t scanY = 8 + ((f * 2) % 49);
  u8g2.drawHLine(23, scanY, 82);
  u8g2.drawHLine(40, 32, 48);
  u8g2.drawVLine(64, 8, 48);
  if (f > 32) {
    u8g2.drawFrame(41, 16, 46, 32);
    drawCrossStar(91, 14, true);
  }
}

void drawDreamingAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  drawRobotHead(13, 42, 1);
  u8g2.drawCircle(91, 11, 8);
  u8g2.setDrawColor(0);
  u8g2.drawCircle(96, 8, 8);
  u8g2.setDrawColor(1);
  for (uint8_t i = 0; i < 7; i++) {
    uint8_t x = 41 + ((i * 17 + f) % 80);
    uint8_t y = 12 + ((i * 9 + (f >> 1)) % 35);
    drawCrossStar(x, y, ((f + i * 6) & 15) < 5);
  }
  u8g2.drawCircle(37, 38, 2);
  u8g2.drawCircle(44, 31, 3);
  u8g2.drawRFrame(52, 15, 36, 18, 6);
  u8g2.setFont(u8g2_font_5x8_tf);
  u8g2.drawStr(62, 27, "Z");
  if ((f & 15) > 8) u8g2.drawStr(72, 24, "z");
}

void drawCelebrationAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  int jump = (f < 22) ? -triWave(f, 0, 8) : 0;
  drawRobotHead(56, 36 + jump, 2);
  for (uint8_t i = 0; i < 30; i++) {
    uint8_t x = prng8(i * 13 + f * 7) & 0x7F;
    uint8_t y = (prng8(i * 31 + f * 5) + f * 2) & 0x3F;
    if ((i & 1) == 0) u8g2.drawPixel(x, y);
    else u8g2.drawHLine(x, y, 2);
  }
  u8g2.drawHLine(40, 61, 48);
  if (f > 36) {
    u8g2.setFont(u8g2_font_5x8_tf);
    u8g2.drawStr(47, 16, "DONE!");
  }
}

void drawListeningAction(uint32_t now) {
  uint8_t f = currentActionFrame(now);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);
  u8g2.drawBox(43, 20, 14, 12);
  u8g2.drawBox(71, 20, 14, 12);
  u8g2.drawPixel(50 + ((f >> 2) & 1), 25);
  u8g2.drawPixel(78 - ((f >> 2) & 1), 25);
  for (uint8_t r = 5; r <= 25; r += 6) {
    if (((f + r) & 7) < 5) {
      u8g2.drawCircle(28, 32, r, U8G2_DRAW_UPPER_RIGHT | U8G2_DRAW_LOWER_RIGHT);
      u8g2.drawCircle(100, 32, r, U8G2_DRAW_UPPER_LEFT | U8G2_DRAW_LOWER_LEFT);
    }
  }
  u8g2.drawVLine(64, 42, 11);
  u8g2.drawFrame(56, 52, 17, 4);
  u8g2.drawHLine(52, 57, 25);
}

/*
 * ============================================================
 *  DEPLOYMENT CHECKLIST
 * ============================================================
 *  1. Install libraries via Arduino Library Manager:
 *     • U8g2 (by olikraus)
 *     • WebSockets (by Markus Sattler, v2.3.5+)
 *     • WiFiManager (by tzapu, v2.0.16+)
 *
 *  2. Copy esp32-eyes files to sketch folder (DO NOT EDIT):
 *     Face.h, Face.cpp, FaceEmotions.hpp
 *     Eye.h, Eye.cpp, EyeDrawer.h, EyeDrawer.cpp
 *     Common.h, Animations.h, EyePresets.h
 *     FaceExpression.h, FaceExpression.cpp
 *     FaceBehavior.h, FaceBehavior.cpp
 *     LookAssistant.h, LookAssistant.cpp
 *     BlinkAssistant.h, BlinkAssistant.cpp
 *
 *  3. Wire hardware (ESP32-S3 — GPIO26-32 avoided everywhere, reserved
 *     for the S3's own flash/PSRAM):
 *     OLED (SH1106 VCOMH0):
 *       VCC → 3.3V, GND → GND, SDA → GPIO 8, SCL → GPIO 9
 *     Mic (INMP441 x2, stereo pair sharing one bus):
 *       VDD → 3.3V, GND → GND, SCK → 16, WS → 17, SD → 18
 *       Left mic:  L/R → GND
 *       Right mic: L/R → 3.3V
 *     Speaker (MAX98357A):
 *       VIN → 5V, GND → GND, LRC → GPIO 4, BCLK → GPIO 5, DIN → GPIO 6
 *       SD   → 3.3V directly (not a GPIO — this is a static hardware
 *              setting, not something firmware drives). >1.4V selects
 *              left-channel-only output, matching this firmware's mono
 *              I2S format; floating/left-default wiring instead outputs
 *              (L+R)/2 and mixes in the undefined, never-written right
 *              slot, corrupting the audio.
 *       GAIN → left unconnected/floating = 9dB (default, safe starting
 *              point). Only add a resistor if it's too quiet/loud once
 *              running: 100K to GND=15dB, direct to GND=12dB, direct to
 *              VIN=6dB, 100K to VIN=3dB.
 *       Speaker terminals → the two leads of a small 4-8Ω speaker (this
 *              amp is rated ~3W into 4Ω) — no polarity requirement for a
 *              single speaker, but keep +/- consistent if you ever add a
 *              second one, or the two will play out of phase.
 *
 *  4. Arduino IDE settings:
 *     Board: ESP32S3 Dev Module
 *     Upload Speed: 921600
 *     CPU Frequency: 240MHz
 *     Flash Size: (match your module — check before flashing)
 *     Partition Scheme: Default
 *
 *  5. Upload and monitor Serial (115200 baud):
 *     Expected: "✅ MBUDDY ready" → eyes animate on OLED
 *
 *  6. Test:
 *     • Disconnect WiFi → Worried but eyes keep animating (autonomic)
 *     • Send [STATE:happy] from Python → instant Happy eyes (brain)
 *
 *  TROUBLESHOOTING:
 *  • Blank screen? → Check OLED wiring to GPIO 21/22, verify I2C address 0x3C/0x3D
 *  • No voice replies? → Confirm WebSocket connection and inspect [HEARD] logs
 *  • WebSocket fails? → Verify SSL host/port, check Railway status
 *  • Emotions not changing? → Verify tag format [STATE:xxx] from Python
 *
 *  MEMORY USAGE (typical):
 *  • Heap: ~45KB free after init (ESP32 has ~290KB total)
 *  • All operations non-blocking — safe for long-term use
 * ============================================================
 */
