#include "lab_speaker.h"

#include <driver/i2s.h>
#include <freertos/FreeRTOS.h>
#include <freertos/ringbuf.h>

#define SPK_PORT      I2S_NUM_1
#define SPK_BCLK      5
#define SPK_LRC       4
#define SPK_DIN       6
#define SPK_RATE      22050
#define SPK_RING_BYTES (64 * 1024)          // ~1.5 s of audio
#define SPK_IDLE_END_MS 1500                // a stream that stops without an end marker is treated as finished after this

static RingbufHandle_t ring = nullptr;
static volatile bool spkOk = false;
static volatile bool endMarked = false;
static volatile bool playing = false;
static volatile bool doneFlag = false;
static volatile uint32_t playedBytes = 0, underruns = 0, droppedBytes = 0;

static void spkTask(void*) {
  static int16_t silence[256];
  memset(silence, 0, sizeof(silence));
  bool inGap = false;
  uint32_t lastDataMs = 0;

  for (;;) {
    size_t n = 0;
    uint8_t* d = (uint8_t*)xRingbufferReceiveUpTo(ring, &n, pdMS_TO_TICKS(20), 1024);
    if (d) {
      playing = true;
      inGap = false;
      lastDataMs = millis();
      size_t w = 0;
      i2s_write(SPK_PORT, d, n, &w, portMAX_DELAY);
      playedBytes += w;
      vRingbufferReturnItem(ring, d);
      continue;
    }
    if (!playing) continue;

    bool idleTooLong = (millis() - lastDataMs) > SPK_IDLE_END_MS;
    if (endMarked || idleTooLong) {                   // finished: pad silence so the last samples are not cut off
      size_t w = 0;
      for (int i = 0; i < 4; i++) i2s_write(SPK_PORT, silence, sizeof(silence), &w, portMAX_DELAY);
      i2s_zero_dma_buffer(SPK_PORT);
      playing = false;
      endMarked = false;
      inGap = false;
      doneFlag = true;
    } else if (!inGap) {                              // the ring ran dry mid-stream: one underrun per gap
      underruns++;
      inGap = true;
    }
  }
}

bool labSpeakerBegin() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
  cfg.sample_rate = SPK_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;      // mono, like production
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = true;                       // silence, not a repeating buffer, on underrun

  i2s_pin_config_t pins = {};
  pins.bck_io_num = SPK_BCLK;
  pins.ws_io_num = SPK_LRC;
  pins.data_out_num = SPK_DIN;
  pins.data_in_num = I2S_PIN_NO_CHANGE;
  pins.mck_io_num = I2S_PIN_NO_CHANGE;

  ring = xRingbufferCreate(SPK_RING_BYTES, RINGBUF_TYPE_BYTEBUF);
  if (!ring) { Serial.println("speaker: ring buffer allocation failed"); return false; }
  if (i2s_driver_install(SPK_PORT, &cfg, 0, nullptr) != ESP_OK) { Serial.println("speaker: I2S install failed"); return false; }
  if (i2s_set_pin(SPK_PORT, &pins) != ESP_OK)                    { Serial.println("speaker: I2S pin setup failed"); return false; }
  i2s_zero_dma_buffer(SPK_PORT);
  xTaskCreatePinnedToCore(spkTask, "spk", 4096, nullptr, 2, nullptr, 0);
  spkOk = true;
  Serial.println("speaker initialised (22.05 kHz mono, BCLK=5 LRC=4 DIN=6)");
  return true;
}

bool labSpeakerReady() { return spkOk; }

void labSpeakerOnBinary(const uint8_t* data, size_t len) {
  if (!spkOk || len < 7 || data[0] != 'S' || data[1] != 'P' || data[2] != 1) return;
  const uint8_t* pcm = data + 6;
  size_t n = (len - 6) & ~(size_t)1;                   // whole int16 samples only
  if (n > 0) {
    if (xRingbufferSend(ring, pcm, n, 0) != pdTRUE) droppedBytes += n;   // ring full: the server is sending too fast
  }
  if (data[3] & 1) endMarked = true;                   // last chunk of the stream
}

bool labSpeakerOnText(const char* msg) {
  if (strncmp(msg, "SPK", 3) != 0) return false;
  if (spkOk && !strncmp(msg, "SPK reset", 9)) {
    size_t n; void* p;
    while ((p = xRingbufferReceive(ring, &n, 0)) != nullptr) vRingbufferReturnItem(ring, p);
    endMarked = false; playing = false;
    i2s_zero_dma_buffer(SPK_PORT);
    playedBytes = 0; underruns = 0; droppedBytes = 0;
  }
  return true;
}

size_t labSpeakerStat(char* out, size_t outLen) {
  if (outLen == 0) return 0;
  unsigned buffered = spkOk ? (unsigned)(SPK_RING_BYTES - xRingbufferGetCurFreeSize(ring)) : 0;
  int n = snprintf(out, outLen, " spkok=%d spkplayed=%lu spkunder=%lu spkdrop=%lu spkbuf=%u",
                   spkOk ? 1 : 0, (unsigned long)playedBytes, (unsigned long)underruns, (unsigned long)droppedBytes, buffered);
  return n < 0 ? 0 : (size_t)n;
}

bool labSpeakerTakeDone(char* out, size_t outLen) {
  if (!doneFlag) return false;
  snprintf(out, outLen, "SPKDONE played=%lu under=%lu drop=%lu",
           (unsigned long)playedBytes, (unsigned long)underruns, (unsigned long)droppedBytes);
  doneFlag = false;
  return true;
}
