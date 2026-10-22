#include "lab_oled.h"

#include <U8g2lib.h>
#include <Wire.h>

// Same driver class as production (Common.h: MBUDDY_OLED_USE_SH1106_VCOMH0 = 1).
static U8G2_SH1106_128X64_VCOMH0_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);
static bool oledOk = false;
static bool updating = false;
static uint32_t lastDraw = 0;
static uint32_t tick = 0;

#define OTA_FRAME_MS 120           // one 128x64 frame takes ~90 ms to send at 100 kHz, so this is close to the fastest useful rate

bool labOledReady() { return oledOk; }

bool labOledBegin(int sdaPin, int sclPin) {
  Wire.begin(sdaPin, sclPin);
  Wire.setClock(100000);
  uint8_t addr = 0;
  const uint8_t candidates[] = {0x3C, 0x3D};
  for (uint8_t c : candidates) {
    Wire.beginTransmission(c);
    if (Wire.endTransmission() == 0) { addr = c; break; }
  }
  if (addr == 0) {
    Serial.println("OLED: nothing answered on I2C at 0x3C/0x3D (check SDA=8, SCL=9, power) - screen output disabled");
    return false;
  }
  oled.setI2CAddress(addr << 1);
  oled.setBusClock(100000);
  oled.begin();
  oled.setPowerSave(0);
  oledOk = true;
  Serial.printf("OLED: panel answered at 0x%02X\n", addr);
  return true;
}

// ── idle screen ─────────────────────────────────────────────────────────
void labOledIdle(const char* firmware, const char* ip, bool serverUp) {
  if (!oledOk || updating) return;
  oled.clearBuffer();
  oled.setFont(u8g2_font_ncenB10_tr);
  oled.drawStr(4, 15, "MBUDDY LAB");
  oled.drawHLine(0, 19, 128);
  oled.setFont(u8g2_font_6x12_tr);
  oled.drawStr(4, 33, firmware ? firmware : "");
  oled.drawStr(4, 46, ip && ip[0] ? ip : "no Wi-Fi yet");
  oled.drawStr(4, 60, serverUp ? "server: connected" : "server: waiting");
  oled.sendBuffer();
}

// ── OTA download animation ──────────────────────────────────────────────
// An arrow drops into a tray while data dots fall down both sides; the percentage sits top right
// and a progress bar runs along the bottom. Only lines, boxes and text are used.
static void drawOtaFrame(int pct, uint32_t t, bool done) {
  oled.clearBuffer();
  oled.setMaxClipWindow();
  oled.setFont(u8g2_font_6x12_tr);
  oled.drawStr(2, 10, done ? "DONE!" : "UPDATING");
  if (!done) {
    for (uint32_t i = 0; i < (t / 3) % 4; i++) oled.drawBox(52 + i * 5, 8, 2, 2);        // 0..3 dots after the word
  }
  char buf[8];
  snprintf(buf, sizeof(buf), "%d%%", pct);
  oled.drawStr(126 - oled.getStrWidth(buf), 10, buf);

  // tray: an open box the arrow drops into
  oled.drawLine(46, 36, 46, 44);
  oled.drawLine(46, 44, 82, 44);
  oled.drawLine(82, 44, 82, 36);

  if (done) {                                                                              // tick mark instead of the arrow
    oled.drawLine(54, 26, 61, 33);
    oled.drawLine(61, 33, 76, 16);
    oled.drawLine(54, 27, 61, 34);
    oled.drawLine(61, 34, 76, 17);
  } else {
    int y = 10 + (int)(t % 14);                                                            // arrow top, 10..23
    oled.setClipWindow(0, 14, 127, 44);                                                    // hidden above the title row, "inside" the tray at the bottom
    oled.drawBox(60, y, 8, 10);                                                            // shaft
    oled.drawTriangle(54, y + 10, 74, y + 10, 64, y + 20);                                 // head
    oled.setMaxClipWindow();
    for (int k = 0; k < 3; k++) {                                                          // falling data dots on both sides
      int dy = 16 + (int)((t * 2 + k * 9) % 27);
      oled.drawBox(28, dy, 2, 2);
      oled.drawBox(98, 16 + (int)((t * 2 + k * 9 + 13) % 27), 2, 2);
    }
  }

  oled.drawFrame(4, 50, 120, 10);
  oled.drawBox(6, 52, (116 * pct) / 100, 6);
  oled.sendBuffer();
}

void labOledOtaStart() {
  updating = true;
  tick = 0;
  lastDraw = 0;
  Serial.println("OTA: update started");
  if (oledOk) drawOtaFrame(0, 0, false);
}

void labOledOtaProgress(unsigned int done, unsigned int total) {
  int pct = total ? (int)((uint64_t)done * 100 / total) : 0;
  static int lastPrinted = -10;
  if (pct >= lastPrinted + 10 || pct == 100) { Serial.printf("OTA: %d%%\n", pct); lastPrinted = pct; }
  if (!oledOk) return;
  uint32_t now = millis();
  if (lastDraw != 0 && now - lastDraw < OTA_FRAME_MS && pct < 100) return;                // don't let the display slow the transfer
  lastDraw = now;
  drawOtaFrame(pct, ++tick, false);
}

void labOledOtaDone() {
  Serial.println("OTA: complete, rebooting");
  if (oledOk) {
    drawOtaFrame(100, 0, true);
    delay(900);                                                                            // the board restarts right after this returns
  }
}

void labOledOtaError(int code) {
  updating = false;
  Serial.printf("OTA: failed (error %d)\n", code);
  if (!oledOk) return;
  oled.clearBuffer();
  oled.setFont(u8g2_font_ncenB10_tr);
  oled.drawStr(6, 24, "UPDATE FAILED");
  oled.setFont(u8g2_font_6x12_tr);
  char buf[24];
  snprintf(buf, sizeof(buf), "error %d - still", code);
  oled.drawStr(6, 42, buf);
  oled.drawStr(6, 55, "on the old firmware");
  oled.sendBuffer();
}
