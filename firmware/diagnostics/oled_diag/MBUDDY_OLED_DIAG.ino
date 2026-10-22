#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>

static const uint8_t OLED_SDA = 21;
static const uint8_t OLED_SCL = 22;
static uint8_t oledAddress = 0x3C;

static uint8_t scanOledAddress() {
  const uint8_t candidates[] = {0x3C, 0x3D};
  for (uint8_t i = 0; i < sizeof(candidates); i++) {
    Wire.beginTransmission(candidates[i]);
    if (Wire.endTransmission() == 0) {
      return candidates[i];
    }
  }
  return 0x3C;
}

template <typename Display>
static void testDriver(const char* name) {
  Serial.printf("Testing OLED driver: %s addr=0x%02X\n", name, oledAddress);

  Display u8g2(U8G2_R0, OLED_SCL, OLED_SDA, U8X8_PIN_NONE);
  u8g2.setI2CAddress(oledAddress << 1);
  u8g2.setBusClock(50000);
  u8g2.begin();
  u8g2.setPowerSave(0);
  u8g2.setContrast(255);

  for (uint8_t frame = 0; frame < 6; frame++) {
    u8g2.clearBuffer();
    u8g2.setDrawColor(1);
    u8g2.drawFrame(0, 0, 128, 64);
    u8g2.drawBox(4 + frame * 4, 6, 18, 18);
    u8g2.drawBox(106 - frame * 4, 6, 18, 18);
    u8g2.setFont(u8g2_font_6x10_tf);
    u8g2.drawStr(8, 40, name);
    u8g2.drawStr(8, 55, "If visible: note name");
    u8g2.sendBuffer();
    delay(1000);
  }
}

void setup() {
  Serial.begin(115200);
  delay(800);
  Serial.println("\nMBUDDY OLED diagnostic");
  Serial.println("Wiring expected: SDA=21, SCL/SCK=22");

  Wire.begin(OLED_SDA, OLED_SCL);
  Wire.setClock(50000);
  oledAddress = scanOledAddress();
  Serial.printf("I2C scan selected addr=0x%02X\n", oledAddress);

  testDriver<U8G2_SH1106_128X64_NONAME_F_SW_I2C>("SH1106 NONAME");
  testDriver<U8G2_SH1106_128X64_VCOMH0_F_SW_I2C>("SH1106 VCOMH0");
  testDriver<U8G2_SH1106_128X64_WINSTAR_F_SW_I2C>("SH1106 WINSTAR");
  testDriver<U8G2_SSD1306_128X64_NONAME_F_SW_I2C>("SSD1306 NONAME");
  testDriver<U8G2_SSD1306_128X64_VCOMH0_F_SW_I2C>("SSD1306 VCOMH0");
  testDriver<U8G2_SSD1306_128X64_ALT0_F_SW_I2C>("SSD1306 ALT0");

  Serial.println("OLED diagnostic finished. Holding final marker.");
}

void loop() {
  static U8G2_SH1106_128X64_VCOMH0_F_SW_I2C hold(U8G2_R0, OLED_SCL, OLED_SDA, U8X8_PIN_NONE);
  static bool initialized = false;

  if (!initialized) {
    hold.setI2CAddress(oledAddress << 1);
    hold.setBusClock(50000);
    hold.begin();
    hold.setPowerSave(0);
    hold.setContrast(255);
    initialized = true;
  }

  hold.clearBuffer();
  hold.setDrawColor(1);
  hold.drawFrame(0, 0, 128, 64);
  hold.setFont(u8g2_font_6x10_tf);
  hold.drawStr(12, 30, "DIAG HOLD");
  hold.drawStr(12, 48, "SH1106 VCOMH0");
  hold.sendBuffer();
  delay(1000);
}
