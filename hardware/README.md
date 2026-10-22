# Hardware abstraction (PC side)

A hardware abstraction layer in Python: one interface (audio, display, sensors, storage, network) that each platform implements, so
the same buddy code can run on a PC or drive a device.

| File | What it is |
|---|---|
| [`common/hal_interface.py`](common/hal_interface.py) | The interface every platform implements |
| [`desktop/windows_platform.py`](desktop/windows_platform.py) | The Windows desktop implementation |
| [`desktop/video_converter.py`](desktop/video_converter.py) | Converts video into formats for the ESP32 display, the desktop, or GIF sequences (has a CLI) |

The ESP32 firmware itself is in [`firmware/`](../firmware); parts and wiring are in [`docs/HARDWARE.md`](../docs/HARDWARE.md).
