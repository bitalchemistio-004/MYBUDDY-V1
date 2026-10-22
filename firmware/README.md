# The desk device (firmware)

The ESP32 that shows the buddy's face on your desk: a small OLED screen, a microphone and a speaker, with the same character art
as the apps.

| Folder | What it is |
|---|---|
| [`mbuddy/`](mbuddy) | The firmware (PlatformIO project: `platformio.ini`, `src/`, `lib/` including the eyes library) |
| [`diagnostics/oled_diag/`](diagnostics/oled_diag) | A small sketch to check the OLED wiring on its own |

**Build and flash:** install [PlatformIO](https://platformio.org), then from `firmware/mbuddy` run `pio run` and
`pio run -t upload` with the board connected. Parts, wiring and pin numbers are in [`docs/HARDWARE.md`](../docs/HARDWARE.md).

It stays connected to the PC brain over a WebSocket (it reconnects by itself) and is set up over Wi-Fi from a captive portal, so
no code change is needed to join a network. Details are in [`docs/HARDWARE.md`](../docs/HARDWARE.md).
