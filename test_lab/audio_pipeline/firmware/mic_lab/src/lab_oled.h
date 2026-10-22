#pragma once
/*
 * OLED status screen for the mic_lab test firmware.
 *
 * Same panel and wiring as production (MBUDDY.ino / esp32-eyes): a 1.3" SH1106
 * 128x64 (VCOMH0 memory map) on hardware I2C, SDA=8, SCL=9, address 0x3C or 0x3D,
 * 100 kHz. The lab has no face; the screen shows what the board is doing:
 *
 *   idle       "MBUDDY LAB", firmware version, IP address, server link
 *   OTA update an animated download arrow dropping into a tray, falling data dots,
 *              the percentage and a progress bar; then DONE, or the error
 *
 * If no panel answers on I2C, every call is a harmless no-op and Serial says so.
 */
#include <Arduino.h>

bool labOledBegin(int sdaPin, int sclPin);
bool labOledReady();
void labOledIdle(const char* firmware, const char* ip, bool serverUp);   // redraws the idle screen (skipped during an update)
void labOledOtaStart();
void labOledOtaProgress(unsigned int done, unsigned int total);
void labOledOtaDone();
void labOledOtaError(int code);
