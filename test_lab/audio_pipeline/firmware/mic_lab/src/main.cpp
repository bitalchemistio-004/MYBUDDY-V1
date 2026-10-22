/*
 * MBUDDY audio-lab test firmware  (mic_lab)
 * ---------------------------------------------------------------------------
 * Streams the two INMP441 microphones to the audio-lab server
 * (test_lab/audio_pipeline) so the microphones, PCM chunk size and channel
 * selection can be tested from a web page. It replaces the robot firmware
 * while flashed — it has no face, speaker or brain. Re-flash firmware/mbuddy
 * to go back.
 *
 * Wiring is identical to production (MBUDDY.ino): both mics share one I2S bus,
 * WS=17, SD=18, SCK=16; left mic L/R pin -> GND, right mic L/R pin -> 3.3V.
 *
 * Wi-Fi: uses WiFiManager exactly like production, so credentials already
 * stored on the board are reused. If none are stored it opens the
 * "MBUDDY_LAB_SETUP" access point.
 *
 * Server address: found by UDP broadcast (port 18831) so a changed DHCP lease
 * does not matter; LAB_FALLBACK_HOST is only used if nothing answers.
 *
 * Wiring diagnostics (fw 2): the firmware also looks at the RAW 32-bit I2S words,
 * before any shifting, gain or filtering, because that is where a bad word-select
 * (WS), clock (SCK) or data (SD) connection shows up:
 *   - STAT reports, per slot, the % of samples beyond +/-12.5% of full scale
 *     (s0big / s1big) and the % with anything in bits 6..0 (s0low / s1low).
 *     A healthy INMP441 leaves both near 0 in a quiet room.
 *   - "DIAG" (server -> device) captures 4096 raw frames (0.26 s) and sends them
 *     back as 8 binary 'DG' chunks for bit-level analysis on the PC.
 *   - "DIAG quiet" does the same but first stops sending audio (and STAT) over
 *     Wi-Fi for the duration of the capture, so a glitch that disappears in that
 *     mode is being caused by Wi-Fi transmit activity coupling into the mics.
 *
 * OLED (fw 5): lab_oled.cpp drives the robot's 1.3" SH1106 panel (SDA=8, SCL=9, like production):
 * an idle status screen, and an animated download screen while an OTA update arrives.
 *
 * Speaker (fw 4): lab_speaker.cpp plays 22.05 kHz mono PCM streamed by the server
 * through the MAX98357A on I2S port 1, so speaker and echo tests need no other
 * firmware. Over-the-air updates (fw 4): after one USB flash, `pio run -e ota -t upload`
 * updates the board over Wi-Fi (ArduinoOTA, password LAB_OTA_PASSWORD).
 *
 * Protocol: see test_lab/audio_pipeline/protocol.py. Keep the limits below in
 * sync with that file.
 *
 *   server -> device (text)  CFG chunk=<n> mic=<left|right|both> gain=<x> swap=<0|1>
 *   device -> server (text)  HELLO | ACK (config applied) | NAK <why> | STAT
 *   device -> server (bin)   12-byte header + int16 PCM, see protocol.py
 *
 * The device only ever ACKs a configuration AFTER the acquisition task has
 * switched to it, and every packet header carries the chunk size and channel
 * mask actually used — so the web page can show what the hardware did, not
 * just what was requested.
 */
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WiFiManager.h>
#include <WebSocketsClient.h>
#include <driver/i2s.h>
#include <esp_heap_caps.h>
#include <ArduinoOTA.h>
#include "lab_speaker.h"
#include "lab_oled.h"

#ifndef LAB_OTA_PASSWORD
#define LAB_OTA_PASSWORD "mbuddy-lab"
#endif

// ── Hardware (same as production) ───────────────────────────────────────
#define I2S_WS   17
#define I2S_SD   18
#define I2S_SCK  16
#define I2S_PORT I2S_NUM_0

// ── Protocol limits (mirror protocol.py) ────────────────────────────────
#define LAB_SAMPLE_RATE 16000
#define CHUNK_MIN   32
#define CHUNK_MAX   1024
#define CHUNK_STEP  32
#define GAIN_MIN    1.0f
#define GAIN_MAX    32.0f
#define MASK_LEFT   0x01
#define MASK_RIGHT  0x02
#define MASK_BOTH   0x03
#define HEADER_BYTES 12
#define PACKET_MAX  (HEADER_BYTES + CHUNK_MAX * 2 * 2)

// Defaults match production: 256-sample packets, 12x digital gain.
#define DEFAULT_CHUNK 256
#define DEFAULT_GAIN  12.0f

// ── Server discovery ────────────────────────────────────────────────────
#define LAB_DISCOVERY_PORT   18831
#define LAB_DISCOVERY_PROBE  "MBUDDYLAB-DISCOVER?"
#define LAB_DISCOVERY_PREFIX "MBUDDYLAB-HERE"
#define LAB_FALLBACK_HOST    "10.67.69.172"   // used only if discovery finds nothing
#define LAB_FALLBACK_PORT    5100
#define LAB_WS_PATH          "/ws/device"
#define LAB_REDISCOVER_AFTER_MS 15000

#define TX_QUEUE_DEPTH 8

struct LabConfig {
  uint16_t chunk;
  uint8_t  mask;
  float    gain;
  uint8_t  swap;
};

struct Packet {
  uint16_t bytes;
  uint8_t  data[PACKET_MAX];
};

static LabConfig activeCfg  = { DEFAULT_CHUNK, MASK_BOTH, DEFAULT_GAIN, 0 };
static LabConfig pendingCfg = { DEFAULT_CHUNK, MASK_BOTH, DEFAULT_GAIN, 0 };
static volatile bool cfgPending = false;
static volatile bool ackNeeded  = false;
static portMUX_TYPE cfgMux = portMUX_INITIALIZER_UNLOCKED;

static QueueHandle_t txQueue = nullptr;
static WebSocketsClient ws;
static volatile bool wsUp = false;
static uint32_t wsDownSince = 0;
static bool sendHelloPending = false;

// Diagnostics (single writer each, read for STAT/Serial)
static volatile uint32_t statPackets = 0, statQueueDrops = 0, statI2sErrors = 0, statSendFails = 0;
static volatile int32_t  peakSlot0 = 0, peakSlot1 = 0;   // raw 16-bit peaks per I2S slot, before swap/gain

// Raw bit-health counters (per slot, accumulated over the STAT interval)
static volatile uint32_t bitTotal = 0, bitTopBad[2] = {0, 0}, bitLowActive[2] = {0, 0};
static portMUX_TYPE bitMux = portMUX_INITIALIZER_UNLOCKED;

// Raw capture for the wiring diagnostic
#define DIAG_FRAMES        4096
#define DIAG_CHUNK_FRAMES  512
static int32_t diagBuf[DIAG_FRAMES * 2];
static volatile bool diagArm = false, diagReady = false;
static volatile uint32_t diagFill = 0;
static volatile bool quietMode = false;                // true while a "DIAG quiet" capture runs: no Wi-Fi audio/STAT traffic
static volatile bool diagArmPending = false;
static volatile uint32_t diagStartAt = 0;              // millis() when the capture may begin (lets Wi-Fi go idle first)

static String serverHost = LAB_FALLBACK_HOST;
static uint16_t serverPort = LAB_FALLBACK_PORT;
static const char* serverVia = "fallback";

// ── helpers ─────────────────────────────────────────────────────────────
static const char* micName(uint8_t mask) {
  return mask == MASK_LEFT ? "left" : mask == MASK_RIGHT ? "right" : "both";
}

static LabConfig copyActive() {
  portENTER_CRITICAL(&cfgMux);
  LabConfig c = activeCfg;
  portEXIT_CRITICAL(&cfgMux);
  return c;
}

static bool kvGet(const char* msg, const char* key, char* out, size_t outLen) {
  char pat[24];
  snprintf(pat, sizeof(pat), " %s=", key);
  const char* p = strstr(msg, pat);
  if (!p) return false;
  p += strlen(pat);
  size_t i = 0;
  while (*p && *p != ' ' && i < outLen - 1) out[i++] = *p++;
  out[i] = '\0';
  return i > 0;
}

static void put16(uint8_t* p, uint16_t v) { p[0] = v & 0xFF; p[1] = v >> 8; }
static void put32(uint8_t* p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (v >> (8 * i)) & 0xFF; }

// ── I2S microphone ──────────────────────────────────────────────────────
static bool setupI2S() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = LAB_SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = I2S_SCK;
  pins.ws_io_num = I2S_WS;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = I2S_SD;
  pins.mck_io_num = I2S_PIN_NO_CHANGE;

  if (i2s_driver_install(I2S_PORT, &cfg, 0, nullptr) != ESP_OK) { Serial.println("I2S install failed"); return false; }
  if (i2s_set_pin(I2S_PORT, &pins) != ESP_OK)                    { Serial.println("I2S pin setup failed"); return false; }
  i2s_zero_dma_buffer(I2S_PORT);
  Serial.println("I2S mic initialised (16 kHz, 2 slots)");
  return true;
}

// Acquisition task: read `chunk` frames from I2S, condition, packetise, queue.
// It is the only place the active configuration is switched, so an ACK means
// the hardware is really running that configuration.
static void micTask(void*) {
  static int32_t raw[CHUNK_MAX * 2];
  static Packet pkt;
  float dcX[2] = {0, 0}, dcY[2] = {0, 0};
  uint16_t seq = 0;

  for (;;) {
    if (cfgPending) {
      portENTER_CRITICAL(&cfgMux);
      activeCfg = pendingCfg;
      cfgPending = false;
      portEXIT_CRITICAL(&cfgMux);
      i2s_zero_dma_buffer(I2S_PORT);          // don't carry old-config samples into the new packets
      xQueueReset(txQueue);
      dcX[0] = dcX[1] = dcY[0] = dcY[1] = 0;
      ackNeeded = true;
    }

    const LabConfig cfg = activeCfg;
    const size_t want = (size_t)cfg.chunk * 2 * sizeof(int32_t);
    size_t got = 0;
    esp_err_t err = i2s_read(I2S_PORT, raw, want, &got, 1000 / portTICK_PERIOD_MS);
    if (err != ESP_OK || got < want) { statI2sErrors++; continue; }

    // Raw bit health, before any shifting: a healthy INMP441 sends 24 data bits left-justified in the
    // 32-bit slot, so in a quiet room the top 4 bits are all equal (within +/-12.5% of full scale) and the
    // bits 6..0 are 0 (bit 7 repeats bit 8). Garbage or shifted bits (bad WS / SCK / SD) break one or both.
    {
      uint32_t topBad[2] = {0, 0}, lowAct[2] = {0, 0};
      for (uint16_t i = 0; i < cfg.chunk; i++) {
        for (int sl = 0; sl < 2; sl++) {
          uint32_t w = (uint32_t)raw[2 * i + sl];
          uint32_t top = (w >> 28) & 0xF;
          if (top != 0 && top != 0xF) topBad[sl]++;
          if (w & 0x7F) lowAct[sl]++;          // bit 7 just repeats bit 8 on this hardware; bits 6..0 are always 0
        }
      }
      portENTER_CRITICAL(&bitMux);
      bitTotal += cfg.chunk;
      for (int sl = 0; sl < 2; sl++) { bitTopBad[sl] += topBad[sl]; bitLowActive[sl] += lowAct[sl]; }
      portEXIT_CRITICAL(&bitMux);
    }
    if (diagArmPending && (int32_t)(millis() - diagStartAt) >= 0) { diagFill = 0; diagArm = true; diagArmPending = false; }
    if (diagArm) {                                     // wiring-diagnostic capture of the raw words
      uint32_t n = DIAG_FRAMES - diagFill;
      if (n > cfg.chunk) n = cfg.chunk;
      memcpy(&diagBuf[diagFill * 2], raw, (size_t)n * 2 * sizeof(int32_t));
      diagFill += n;
      if (diagFill >= DIAG_FRAMES) { diagArm = false; diagReady = true; }
    }

    uint8_t* out = pkt.data + HEADER_BYTES;
    int32_t p0 = 0, p1 = 0;
    for (uint16_t i = 0; i < cfg.chunk; i++) {
      // INMP441: 24-bit left-justified in a 32-bit slot -> top 16 bits (same as production).
      int16_t slot[2] = { (int16_t)(raw[2 * i] >> 16), (int16_t)(raw[2 * i + 1] >> 16) };
      if (abs(slot[0]) > p0) p0 = abs(slot[0]);
      if (abs(slot[1]) > p1) p1 = abs(slot[1]);

      // swap fixes a wired/driver slot order that is the opposite of "left = slot 0".
      int16_t ch[2] = { slot[cfg.swap ? 1 : 0], slot[cfg.swap ? 0 : 1] };
      for (int c = 0; c < 2; c++) {
        // DC blocker per channel (production: y = x - x1 + 0.9946*y1), then digital gain.
        float x = (float)ch[c];
        float y = x - dcX[c] + 0.9946f * dcY[c];
        dcX[c] = x; dcY[c] = y;
        float g = y * cfg.gain;
        ch[c] = (int16_t)(g > 32767.0f ? 32767.0f : (g < -32768.0f ? -32768.0f : g));
      }
      if (cfg.mask == MASK_BOTH) { put16(out, (uint16_t)ch[0]); put16(out + 2, (uint16_t)ch[1]); out += 4; }
      else if (cfg.mask == MASK_LEFT) { put16(out, (uint16_t)ch[0]); out += 2; }
      else { put16(out, (uint16_t)ch[1]); out += 2; }
    }
    peakSlot0 = max((int32_t)peakSlot0, p0);
    peakSlot1 = max((int32_t)peakSlot1, p1);

    pkt.data[0] = 'M'; pkt.data[1] = 'B'; pkt.data[2] = 1; pkt.data[3] = cfg.mask;
    put16(pkt.data + 4, seq++);            // seq advances even if the packet is dropped below,
    put16(pkt.data + 6, cfg.chunk);        // so the server sees queue drops as sequence gaps
    put32(pkt.data + 8, millis());
    pkt.bytes = out - pkt.data;

    if (quietMode) seq--;                              // paused on purpose: don't report it as lost packets
    else if (xQueueSend(txQueue, &pkt, 0) != pdTRUE) statQueueDrops++;
    else statPackets++;
  }
}

// ── server discovery ────────────────────────────────────────────────────
static bool discoverServer(String& host, uint16_t& port) {
  WiFiUDP udp;
  if (!udp.begin(0)) return false;
  for (int attempt = 0; attempt < 3; attempt++) {
    udp.beginPacket(IPAddress(255, 255, 255, 255), LAB_DISCOVERY_PORT);
    udp.write((const uint8_t*)LAB_DISCOVERY_PROBE, strlen(LAB_DISCOVERY_PROBE));
    udp.endPacket();
    uint32_t deadline = millis() + 1200;
    while ((int32_t)(millis() - deadline) < 0) {
      if (udp.parsePacket() <= 0) { delay(10); continue; }
      char buf[96] = {0};
      int n = udp.read(buf, sizeof(buf) - 1);
      if (n <= 0 || strncmp(buf, LAB_DISCOVERY_PREFIX, strlen(LAB_DISCOVERY_PREFIX)) != 0) continue;
      char ip[48] = {0};
      unsigned int p = 0;
      if (sscanf(buf + strlen(LAB_DISCOVERY_PREFIX), "%47s %u", ip, &p) == 2 && p > 0) {
        host = ip; port = (uint16_t)p;
        udp.stop();
        return true;
      }
    }
  }
  udp.stop();
  return false;
}

static void connectServer() {
  String h; uint16_t p = 0;
  if (discoverServer(h, p)) { serverHost = h; serverPort = p; serverVia = "discovered"; }
  else { serverHost = LAB_FALLBACK_HOST; serverPort = LAB_FALLBACK_PORT; serverVia = "fallback"; }
  Serial.printf("Server: ws://%s:%u%s (%s)\n", serverHost.c_str(), serverPort, LAB_WS_PATH, serverVia);
  ws.disconnect();
  ws.begin(serverHost.c_str(), serverPort, LAB_WS_PATH);
  ws.setReconnectInterval(3000);
  ws.enableHeartbeat(10000, 3000, 2);
}

// ── WebSocket ───────────────────────────────────────────────────────────
static void handleConfig(const char* msg) {
  char v[24];
  LabConfig c = copyActive();
  if (kvGet(msg, "chunk", v, sizeof(v))) c.chunk = (uint16_t)atoi(v);
  if (kvGet(msg, "mic", v, sizeof(v))) {
    if (!strcmp(v, "left")) c.mask = MASK_LEFT;
    else if (!strcmp(v, "right")) c.mask = MASK_RIGHT;
    else if (!strcmp(v, "both")) c.mask = MASK_BOTH;
    else { ws.sendTXT("NAK bad mic"); return; }
  }
  if (kvGet(msg, "gain", v, sizeof(v))) c.gain = (float)atof(v);
  if (kvGet(msg, "swap", v, sizeof(v))) c.swap = atoi(v) ? 1 : 0;

  if (c.chunk < CHUNK_MIN || c.chunk > CHUNK_MAX || c.chunk % CHUNK_STEP) { ws.sendTXT("NAK chunk out of range"); return; }
  if (c.gain < GAIN_MIN || c.gain > GAIN_MAX) { ws.sendTXT("NAK gain out of range"); return; }

  portENTER_CRITICAL(&cfgMux);
  pendingCfg = c;
  cfgPending = true;
  portEXIT_CRITICAL(&cfgMux);
}

static void sendHello() {
  char build[32];
  snprintf(build, sizeof(build), "%s_%s", __DATE__, __TIME__);       // proves which image is running (e.g. after an OTA update)
  for (char* c = build; *c; c++) if (*c == ' ') *c = '_';
  char msg[200];
  snprintf(msg, sizeof(msg), "HELLO fw=mic_lab/5 build=%s fs=%d ip=%s rssi=%d chunk_min=%d chunk_max=%d",
           build, LAB_SAMPLE_RATE, WiFi.localIP().toString().c_str(), WiFi.RSSI(), CHUNK_MIN, CHUNK_MAX);
  ws.sendTXT(msg);
}

static void onWs(WStype_t type, uint8_t* payload, size_t len) {
  switch (type) {
    case WStype_CONNECTED:
      wsUp = true;
      xQueueReset(txQueue);
      sendHelloPending = true;
      labOledIdle("mic_lab fw 5", WiFi.localIP().toString().c_str(), true);
      Serial.println("WS connected");
      break;
    case WStype_DISCONNECTED:
      if (wsUp) wsDownSince = millis();
      wsUp = false;
      labOledIdle("mic_lab fw 5", WiFi.localIP().toString().c_str(), false);
      Serial.println("WS disconnected");
      break;
    case WStype_TEXT:
      if (len >= 4 && !strncmp((const char*)payload, "CFG ", 4)) handleConfig((const char*)payload);
      else if (len >= 3 && !strncmp((const char*)payload, "SPK", 3)) labSpeakerOnText((const char*)payload);
      else if (len >= 4 && !strncmp((const char*)payload, "DIAG", 4)) {
        if (!diagArm && !diagReady && !diagArmPending) {
          bool quiet = len >= 10 && !strncmp((const char*)payload + 5, "quiet", 5);
          quietMode = quiet;
          diagStartAt = millis() + (quiet ? 200 : 0);   // in quiet mode let the Wi-Fi radio settle before capturing
          diagArmPending = true;
        }
      }
      break;
    case WStype_BIN:
      labSpeakerOnBinary(payload, len);                 // speaker PCM from the server
      break;
    default:
      break;
  }
}

// ── Arduino ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nMBUDDY audio-lab firmware (mic_lab)");
  labOledBegin(8, 9);                                   // non-fatal: everything else works without a screen
  labOledIdle("mic_lab fw 5", nullptr, false);

  txQueue = xQueueCreate(TX_QUEUE_DEPTH, sizeof(Packet));
  if (!txQueue) { Serial.println("queue alloc failed"); while (true) delay(1000); }
  if (!setupI2S()) { while (true) delay(1000); }
  labSpeakerBegin();                                    // non-fatal: the mic tests still work without a speaker
  xTaskCreatePinnedToCore(micTask, "mic", 6144, nullptr, 3, nullptr, 1);

  WiFiManager wm;
  wm.autoConnect("MBUDDY_LAB_SETUP");
  Serial.printf("WiFi: %s  ip=%s\n", WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());

  ArduinoOTA.setHostname("mbuddy-lab");
  ArduinoOTA.setPassword(LAB_OTA_PASSWORD);
  ArduinoOTA.onStart([]() { quietMode = true; labOledOtaStart(); });
  ArduinoOTA.onProgress([](unsigned int done, unsigned int total) { labOledOtaProgress(done, total); });
  ArduinoOTA.onEnd([]() { labOledOtaDone(); });
  ArduinoOTA.onError([](ota_error_t e) { quietMode = false; labOledOtaError((int)e); });
  ArduinoOTA.begin();
  labOledIdle("mic_lab fw 5", WiFi.localIP().toString().c_str(), false);

  ws.onEvent(onWs);
  connectServer();
  wsDownSince = millis();
}

void loop() {
  static Packet tx;
  static uint32_t lastStat = 0;
  ArduinoOTA.handle();
  ws.loop();

  char spkDone[96];
  if (wsUp && labSpeakerTakeDone(spkDone, sizeof(spkDone))) ws.sendTXT(spkDone);

  if (wsUp && sendHelloPending) { sendHello(); sendHelloPending = false; }

  if (wsUp && diagReady) {                             // send the raw capture in 8 chunks of 512 frames
    static uint8_t diagPkt[8 + DIAG_CHUNK_FRAMES * 2 * 4];
    const uint8_t chunks = DIAG_FRAMES / DIAG_CHUNK_FRAMES;
    for (uint8_t c = 0; c < chunks; c++) {
      diagPkt[0] = 'D'; diagPkt[1] = 'G'; diagPkt[2] = 1; diagPkt[3] = c; diagPkt[4] = chunks;
      put16(diagPkt + 5, DIAG_CHUNK_FRAMES); diagPkt[7] = 0;
      memcpy(diagPkt + 8, &diagBuf[c * DIAG_CHUNK_FRAMES * 2], DIAG_CHUNK_FRAMES * 2 * sizeof(int32_t));
      ws.sendBIN(diagPkt, sizeof(diagPkt));
      ws.loop();
    }
    ws.sendTXT("DIAGDONE");
    diagReady = false;
    quietMode = false;
  }

  if (wsUp && ackNeeded) {
    LabConfig c = copyActive();
    char msg[96];
    snprintf(msg, sizeof(msg), "ACK chunk=%u mic=%s gain=%g swap=%u", c.chunk, micName(c.mask), c.gain, c.swap);
    ws.sendTXT(msg);
    ackNeeded = false;
  }

  // Drain a few packets per pass; if not connected, discard so stale audio is never sent on reconnect.
  for (int i = 0; i < 8 && xQueueReceive(txQueue, &tx, 0) == pdTRUE; i++) {
    if (wsUp && !quietMode) { if (!ws.sendBIN(tx.data, tx.bytes)) statSendFails++; }
  }

  uint32_t now = millis();
  if (now - lastStat >= 1000 && !quietMode) {
    lastStat = now;
    LabConfig c = copyActive();
    int32_t p0 = peakSlot0, p1 = peakSlot1;
    peakSlot0 = peakSlot1 = 0;
    Serial.printf("chunk=%u mic=%s gain=%g swap=%u | slot0 peak=%ld slot1 peak=%ld | pkts=%lu qdrop=%lu i2serr=%lu sendfail=%lu | ws=%s\n",
                  c.chunk, micName(c.mask), c.gain, c.swap, (long)p0, (long)p1,
                  (unsigned long)statPackets, (unsigned long)statQueueDrops, (unsigned long)statI2sErrors,
                  (unsigned long)statSendFails, wsUp ? "up" : "down");
    if (wsUp) {
      uint32_t total, top[2], low[2];
      portENTER_CRITICAL(&bitMux);
      total = bitTotal; top[0] = bitTopBad[0]; top[1] = bitTopBad[1]; low[0] = bitLowActive[0]; low[1] = bitLowActive[1];
      bitTotal = 0; bitTopBad[0] = bitTopBad[1] = 0; bitLowActive[0] = bitLowActive[1] = 0;
      portEXIT_CRITICAL(&bitMux);
      float t = total ? 100.0f / (float)total : 0.0f;
      char msg[352];
      snprintf(msg, sizeof(msg), "STAT rssi=%d heap=%u up=%lu qdrop=%lu i2serr=%lu sendfail=%lu slot0=%ld slot1=%ld s0big=%.1f s1big=%.1f s0low=%.1f s1low=%.1f",
               WiFi.RSSI(), (unsigned)ESP.getFreeHeap(), (unsigned long)(now / 1000),
               (unsigned long)statQueueDrops, (unsigned long)statI2sErrors, (unsigned long)statSendFails,
               (long)p0, (long)p1, top[0] * t, top[1] * t, low[0] * t, low[1] * t);
      labSpeakerStat(msg + strlen(msg), sizeof(msg) - strlen(msg));
      ws.sendTXT(msg);
    }
  }

  // The server's address may have changed (new DHCP lease): ask again after a long outage.
  if (!wsUp && now - wsDownSince > LAB_REDISCOVER_AFTER_MS) {
    wsDownSince = now;
    connectServer();
  }
  delay(1);
}
