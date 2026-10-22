# Audio pipeline test bench

Tests the first part of the pipeline in isolation: **microphones → firmware → PCM chunks → server → speech-to-text**.
Everything here is new; no existing project file is modified. The production STT function is only *called*.

```
test_lab/audio_pipeline/
  firmware/mic_lab/     test firmware (own PlatformIO project)
  web/                  the web interface
  server.py             receives PCM from the device, serves the UI, records WAVs, calls STT
  protocol.py           wire format shared by firmware and server
  analysis.py           levels + direction estimation
  calibration.py        channel-balance measurement, per-channel gain and noise gate
  listen.py             per-browser queue for live listening
  diag.py               bit-level analysis of raw I2S words (wiring diagnostic)
  bench_hub.py          shared plumbing for the extra benches; benches.py registers them
  speaker_bench.py / speaker_analysis.py   speaker tests and the signal maths
  barge_bench.py / barge.py                echo and barge-in (exact replica of the firmware rule)
  stt_bench.py / vad_bench.py / net_bench.py / corpus.py   speech, voice-detection and network benches
  stt_branch.py         WAV -> production stt.transcriber.transcribe_audio
  tests/                unit + end-to-end tests, and a simulated device
  recordings/           your WAVs land here (git-ignored)
```

## Run it

1. **Flash the test firmware** (replaces the robot firmware until you re-flash `firmware/mbuddy`):
   ```
   cd test_lab/audio_pipeline/firmware/mic_lab
   pio run -t upload          # edit upload_port in platformio.ini if not COM9
   ```
   Wi-Fi credentials already stored on the board are reused (WiFiManager, same as production). If none are stored it opens the `MBUDDY_LAB_SETUP` access point.
2. **Start the server:** double-click `START_AUDIO_LAB.bat`, or from the project root `python -m test_lab.audio_pipeline` (port 5100; production uses 5000, so both can run).
3. Open http://localhost:5100/. The device finds the server by UDP broadcast (port 18831); `LAB_FALLBACK_HOST` in `main.cpp` is only a fallback. Windows Firewall must allow Python on private networks.
4. Optional, no hardware: `python -m test_lab.audio_pipeline.tests.fake_device --azimuth -30` connects a simulated device.

The UI and REST endpoints answer **loopback only** (they expose a live microphone). Only the device socket is open to the LAN. `--allow-lan-ui` lifts that.

## More test benches (http://localhost:5100/benches)

One firmware and one server serve every bench, so nothing needs re-flashing between tests (the board only talks to one server).

| Bench | What it measures | Needs |
|---|---|---|
| Speaker | Plays a 1 kHz tone, tones at rising levels and a 100 Hz - 7.5 kHz sweep through the robot's speaker and hears them with its own microphone: audible?, level tracking, harmonic distortion, frequency response, delay. Nothing louder than -6 dBFS is ever played. | firmware v4+ |
| Echo & barge-in | The robot speaks at several levels; production's barge-in rule (replicated exactly in `barge.py`) is run on what its microphone hears. Reports false self-interruptions and the safety margin. The Interruption test asks you to talk over it and says whether a peak threshold can separate you from the echo. | firmware v4+ |
| Speech-to-text | Generated English/Arabic speech, clean and noisy, through the production STT stage: word and character error rate, language detection, latency. | Groq key |
| Voice detection & wake | Production's `AudioSessionState` gate on a virtual clock (false starts on noise, utterance capture on speech), the raw VAD, and production's wake-phrase and hallucination filters. | none |
| Network | Loss, jitter, longest gap, queue drops, signal and reconnects at each PCM chunk size. | streaming board |

Not built yet: a display/emotion bench (needs the eyes library in the lab firmware, or the production firmware with a backend emulator).

## Updating the board without a cable (OTA)

After one USB flash of firmware v4 or newer: `cd firmware/mic_lab && pio run -e ota -t upload --upload-port <board IP>` (the IP is in the page header). The password is a shared default (`LAB_OTA_PASSWORD` in `platformio.ini` and `--auth` in the `ota` env: change both together). While an update arrives the robot's OLED shows a download animation (an arrow dropping into a tray, falling data dots, percentage and progress bar), then DONE, or the error; the idle screen shows the firmware version, IP and server link. If no OLED answers on I2C the firmware logs it and carries on. The first OTA after installing the OLED firmware (v5) cannot show the animation, because the running image has no display code yet.

## What each section does

| Section | What it does | Where the truth comes from |
|---|---|---|
| Microphone | Left / Right / Both. The firmware transmits only the selected channel(s). Swap and gain are applied in the firmware. | Pill says "confirmed" only when the device ACKed **and** packet headers show that channel mask. |
| Volume | RMS + peak bars for left, right, and the L+R mix, in dBFS. | Computed from PCM that arrived. A channel that isn't transmitted is greyed, not shown as silent. |
| Listen live | Streams the audio the server receives to your browser and plays it in real time (stereo with "Both": left mic in your left ear). Volume and delay (80 / 150 / 300 ms) controls, level bar, and a status line with buffer, gaps and skipped batches. | It is the processed audio (after the channel gain and noise gate), so it is exactly what the meters, recordings and speech-to-text get. Nothing is played when the device isn't streaming. |
| Wiring diagnostic | Captures 0.25 s of the microphones' RAW 32-bit I2S words (firmware `DIAG`, needs mic_lab v2+) and reports per slot: garbage/full-scale-sized values, bits arriving shifted (bits 6..0 must be 0), stuck data, one slot mirroring the other, and a glitch that repeats every 256-frame I2S buffer. "Capture with Wi-Fi audio paused" (v3) repeats it with the radio idle so the two can be compared. A live raw-bit-health line under the raw slots updates every second. | Fingerprints of what the bits are consistent with, not proof. Real INMP441 data: bits 6..0 are 0 and bit 7 repeats bit 8. |
| Channel balance | Measures the imbalance between the two mics (quiet phase, then a steady sound) and applies a gain trim to the weaker one; "Verify only" re-measures without changing anything; manual trims too. Saved to `calibration.json`. | Speech-band level with each mic's noise floor subtracted; refuses (with the reason) if the sound is too weak, unsteady, or the room wasn't quiet. Reports the noise-floor cost. |
| Direction | Bearing from the inter-mic time lag (needs mic spacing), plus a level-balance bar. "Set current sound as straight ahead" calibrates the centre. | Nothing is drawn if a mic is missing or the room is quiet (< −55 dBFS). |
| Chunk size | 32–1024 samples per channel (multiples of 32), sent to the firmware live. | Shows requested / device ACK / **measured from packet headers** side by side, plus rate and sequence-gap loss. |
| Record | Writes what arrived, with the channel-balance trims applied (recorded in the sidecar `.json`): mono for left/right, stereo for both. 16 kHz, 16-bit. Playback and download in the page. | Lost packets are counted in the sidecar, never filled with fake audio. |
| Transcribe | Sends a recording through production DSP → silence trim → Groq Whisper. Choose channel (L / R / mix / louder-per-sample like production), language (auto / English / Arabic), DSP on/off. | Real `transcribe_audio`; needs `GROQ_API_KEY` in `.env`. |

## Things to know before trusting a number

* **Channel order is unverified.** The code treats I2S slot 0 as "left" like production does, but which physical mic lands in which slot depends on wiring and the driver. Tap one mic: if the *other* bar moves, tick "Swap left / right". The firmware's serial log (`slot0 peak / slot1 peak`, raw, before swap) shows the ground truth.
* **Mic spacing default (10 cm) is a guess** — enter your real spacing. The time-difference bearing scales with it. Two mics cannot distinguish front from back, and sample-rate resolution limits precision to roughly 10–15° near the centre at 10 cm; use "straight ahead" calibration to remove any fixed offset between the two I2S slots.
* **Level balance is not converted to degrees**: that needs a calibration curve for your mics and enclosure.
* **The wiring diagnostic separates "the bus is broken" from "the microphone is bad".** Word-select, clock or data faults show up as garbage or shifted bits in the raw words. If both slots have clean, identically aligned words (as on the tested board) the digital interface is working and the fault is in the microphone or its power/ground. The raw capture is kept in `diag_last_capture.npy`.
* **Use headphones when listening.** Speakers near the robot are picked up by its microphones and will squeal (acoustic feedback). Latency is the chosen delay plus about 40 ms batching plus Wi-Fi; the page skips ahead when it falls more than 120 ms behind that, and counts gaps and skips so glitches are visible rather than hidden. The stream is loopback-only like the rest of the UI.
* **Recording and mic selection are locked while recording or calibrating** so a file or a measurement never changes layout mid-way.
* **A trim fixes signal balance, not signal-to-noise.** Boosting a weak mic boosts its electrical noise equally, so that channel stays noisier (the calibration result shows the noise floor after trims). Direction from time lag and STT on that channel still suffer; replacing the mic is the real fix.
* **Calibrate with the sound centred and equidistant**; any real difference in distance or angle is otherwise baked into the trim. Trims belong to the left/right *channel*, so recalibrate after re-wiring or swapping a mic. The firmware applies the same gain to both channels, so changing the digital gain does not invalidate the trims.
* **The STT branch covers the STT stage only** (DSP → trim → Whisper). Live-only stages — VAD, hallucination filters, dedup, language policy — are not applied. If no text comes back, production's function returns nothing for silence, rate limits and API errors alike; the server console has the reason.
* Test firmware DSP mirrors production (DC blocker, 12× gain) but is applied **per channel**; production picks the louder slot per sample first.

## What is and is not verified

Verified in software (run `python -m unittest discover -s test_lab/audio_pipeline/tests -t .`):
protocol encode/decode, level maths, calibration accuracy (recovers a known imbalance, refuses unreliable input, meters balanced afterwards, trims persisted and recorded), lag sign and size, direction refusal on silence, WAV contents, config validation, chunk-size change end-to-end against a simulated device, sequence-gap accounting, cross-origin/loopback guard, path-traversal handling. The firmware **compiles** for ESP32-S3 (`pio run`), and English and Arabic speech transcribed correctly through the STT branch against live Groq.

**Observed on the real device (2026-09-21):** the flashed firmware connects over Wi-Fi, streams stereo audio at 256 samples/packet (confirmed by ACK and by packet headers), and reports raw per-slot levels. Those readings exposed a microphone roughly 18-20 dB less sensitive than the other, which followed the physical mic when the L/R pin wiring was swapped and was unchanged by cleaning; that is what the channel-balance calibration compensates for.

**Measured on the real device (2026-09-21):** speaker audible 30-40 dB above the room with a clean linear level response (slope 1.02 dB/dB) but about 14% harmonic distortion at -12 dBFS and above measured at the microphone, usable band about 400-3150 Hz, delay from sending to capture 19-44 ms (device-clock timing); echo peak rises steadily with speaker level and at -6 dBFS the robot's own voice is 4.6 dB below its barge-in threshold; production's noise gate starts "listening" on loud steady noise (see the Voice detection bench); over Wi-Fi 256 and 1024-sample chunks were clean while 512 lost packets in two runs and 128 in one (device send queue overflowed).

**Still to verify on the device:** the chunk-size change taking effect on the hardware at sizes other than 256 (the "measured" row will show it), packet loss over your Wi-Fi at each chunk size, the calibration result on real speech, and real direction accuracy. Results from `fake_device` prove the server and page only.
