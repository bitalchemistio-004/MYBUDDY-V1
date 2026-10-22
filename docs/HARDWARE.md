# The desk device

A small ESP32 companion that sits on your desk: animated eyes on a little screen, a microphone to hear you, and a speaker to answer.
It is the buddy's body; the thinking happens on the PC (or a brain you host), which it reaches over Wi-Fi.

<img src="images/hardware-wiring.svg" alt="Wiring: the ESP32-S3 in the middle, the OLED on I2C pins 8 and 9, the I2S microphone on 16, 17 and 18, the I2S speaker amplifier on 5, 4 and 6, and 5 V USB power" width="760">

## How it looks

A small box with a 128×64 screen for a face. The face is a pair of eyes that blink, look around and change shape with the buddy's
mood. These are the expressions the eyes can show:


<sub>Expression sheet from the [esp32-eyes](https://github.com/playfultechnology/esp32-eyes) library the firmware uses (GPL-3.0).
A photo of the assembled device belongs here: add one as `docs/images/device-photo.jpg`.</sub>

## What it does

- **Expressive eyes**: blinking, looking around and emotions, drawn on a 128×64 OLED with the
  [esp32-eyes](https://github.com/playfultechnology/esp32-eyes) library (in `firmware/mbuddy/lib/`).
- **Listens**: an I2S MEMS microphone streams 16 kHz audio to the brain, which hears "hey buddy" and what you say.
- **Talks**: the brain's voice comes back and plays through an I2S amplifier and speaker.
- **Stays connected**: a WebSocket to the brain at `/ws` that reconnects by itself, Wi-Fi set up from a captive portal
  (WiFiManager, no code change needed), and settings kept in flash.
- **Updates over the air**: once flashed by cable, new firmware can be sent over Wi-Fi.

## Parts

| Part | Used |
|---|---|
| Board | ESP32-S3 (an ESP32-S3-WROOM-1 N16R8: 16 MB flash, 8 MB PSRAM) |
| Screen | 128×64 OLED, I2C (SH1106 or SSD1306) |
| Microphone | I2S MEMS microphone (INMP441 or similar) |
| Speaker | I2S amplifier (MAX98357A) and a 4–8 Ω speaker |
| Power | 5 V USB, 2 A |

## Wiring (ESP32-S3)

| Signal | GPIO |
|---|---|
| OLED SDA / SCL | 8 / 9 |
| Microphone SCK / WS / SD | 16 / 17 / 18 |
| Speaker BCLK / LRC / DIN | 5 / 4 / 6 |
| Status LED | 2 |

The pins stay clear of GPIO 26–37, which the S3 uses for its own flash and PSRAM (using them breaks flash access).

## Install

1. Install [PlatformIO](https://platformio.org) (VS Code extension or the command line).
2. Connect the board by USB. In [`firmware/mbuddy/platformio.ini`](../firmware/mbuddy/platformio.ini), set `upload_port` to its COM port.
3. Choose where the brain is, in [`firmware/mbuddy/src/MBUDDY.ino`](../firmware/mbuddy/src/MBUDDY.ino):
   `USE_LOCAL_BRAIN true` for the PC on the same Wi-Fi (`run.bat` prints the PC's address), or `false` for a hosted brain
   (`run_tunnel.bat`).
4. Flash it:
   ```bash
   cd firmware/mbuddy
   pio run -t upload
   pio device monitor
   ```
5. On first start the device opens a Wi-Fi network of its own. Join it from your phone and choose your home Wi-Fi.
6. Start the brain on the PC (`run.bat`, or `python main.py`). The eyes wake when it connects.

Later updates over Wi-Fi: `pio run -e ota -t upload --upload-port <board IP>`. The OTA password is a shared default: change
`MBUDDY_OTA_PASSWORD` in `MBUDDY.ino` and `--auth` in `platformio.ini` together.

If the screen stays dark, flash [`firmware/diagnostics/oled_diag`](../firmware/diagnostics/oled_diag) to check the wiring and the
screen's address.
