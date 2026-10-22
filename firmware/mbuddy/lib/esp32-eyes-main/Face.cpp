/***************************************************
Copyright (c) 2020 Luis Llamas
(www.luisllamas.es)

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or (at your option) any later version. 

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program.  If not, see <http://www.gnu.org/licenses 
****************************************************/


#include "Face.h"
#include "Common.h"
#include <Wire.h>

// ── OLED DRIVER SELECTION ──────────────────────────────────
// Hardware I2C uses Wire.begin(21, 22) from the sketch.
// Keep the normal SH1106 memory map and flip the mounted panel after begin().
MbuddyOled u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);

// Whether the probe actually got an ACK from a device on the bus, as opposed
// to falling back to the default address. Without this the boot log prints
// "addr=0x3C" in both cases and cannot tell a panel that answered from an
// empty bus -- exactly the question being asked when the screen is dark.
static bool oledAddressAcked = false;

static uint8_t detectOledAddress() {
  const uint8_t candidates[] = {0x3C, 0x3D};
  for (uint8_t i = 0; i < sizeof(candidates); i++) {
    Wire.beginTransmission(candidates[i]);
    uint8_t err = Wire.endTransmission();
    Serial.printf("I2C probe 0x%02X -> %u (%s)\n", candidates[i], err,
                  err == 0 ? "ACK" : "no device");
    if (err == 0) {
      oledAddressAcked = true;
      return candidates[i];
    }
  }
  oledAddressAcked = false;
  return 0x3C;
}

static const char* oledDriverName() {
#if MBUDDY_OLED_USE_SH1106_VCOMH0
  return "SH1106 VCOMH0";
#else
  return "SSD1306 VCOMH0";
#endif
}

Face::Face(uint16_t screenWidth, uint16_t screenHeight, uint16_t eyeSize)
	: LeftEye(*this), RightEye(*this), Blink(*this), Look(*this), Behavior(*this), Expression(*this) {

  // Unlike almost every other Arduino library (and the I2C address scanner script etc.)
  // u8g2 uses 8-bit I2C address, so we shift the 7-bit address left by one
  uint8_t oledAddress = detectOledAddress();
  Serial.printf("OLED driver=%s addr=0x%02X (%s)\n", oledDriverName(), oledAddress,
                oledAddressAcked ? "detected" : "FALLBACK - nothing answered on I2C");
  u8g2.setI2CAddress(oledAddress << 1);
  u8g2.setBusClock(100000);  // Reduced from 400kHz to 100kHz to prevent brownout

  bool initResult = u8g2.begin();
  // u8g2 does not verify I2C ACKs, so this is 1 even with no panel attached.
  // It reports that the init sequence was sent, not that anything received it.
  Serial.printf("OLED u8g2.begin() returned: %d (send-only, not proof of panel)\n",
                initResult);

  u8g2.setFlipMode(0);
  u8g2.setPowerSave(0);
  u8g2.setContrast(190);
  u8g2.clearBuffer();
  u8g2.setDrawColor(1);

  // Simple test pattern without fonts to avoid null pointer crashes
  u8g2.drawBox(0, 0, 128, 8);
  u8g2.drawFrame(10, 20, 108, 24);
  u8g2.drawBox(30, 28, 68, 8);  // Simple rectangle instead of text
  u8g2.sendBuffer();
  Serial.println("OLED test pattern sent");

	Width = screenWidth;
	Height = screenHeight;
	EyeSize = eyeSize;

	CenterX = Width / 2;
	CenterY = Height / 2;

	LeftEye.IsMirrored = true;

  Behavior.Clear();
	Behavior.Timer.Start();
}

void Face::LookFront() {
	Look.LookAt(0.0, 0.0);
}

void Face::LookRight() {
	Look.LookAt(-1.0, 0.0);
}

void Face::LookLeft() {
	Look.LookAt(1.0, 0.0);
}

void Face::LookTop() {
	Look.LookAt(0.0, 1.0);
}

void Face::LookBottom() {
	Look.LookAt(0.0, -1.0);
}

void Face::Wait(unsigned long milliseconds) {
	unsigned long start;
	start = millis();
	while (millis() - start < milliseconds) {
		Draw();
	}
}

void Face::DoBlink() {
	Blink.Blink();
}

void Face::Update() {
	if(RandomBehavior) Behavior.Update();
	if(RandomLook) Look.Update();
	if(RandomBlink)	Blink.Update();
	Draw();
}

void Face::Draw() {
  // Clear the display
  u8g2.clearBuffer();
  // Draw left eye
	LeftEye.CenterX = CenterX - EyeSize / 2 - EyeInterDistance;
	LeftEye.CenterY = CenterY;
	LeftEye.Draw();
  // Draw right eye
	RightEye.CenterX = CenterX + EyeSize / 2 + EyeInterDistance;
	RightEye.CenterY = CenterY;
	RightEye.Draw();
  // Transfer the redrawn buffer to the display
  // MBUDDY Fix: do NOT send buffer here; let MBUDDY.ino add overlays and send it
  // u8g2.sendBuffer();
}
