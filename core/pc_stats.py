"""How the PC is doing right now: the numbers the buddy's panel shows.

Read from the PC each time (psutil, and the graphics card's own tool for its
temperature). A value that can't be read is None and the panel leaves it
out; nothing is estimated. Read only: nothing on the PC changes.
"""
from __future__ import annotations

import shutil
import subprocess
import time

import psutil

STRAIN_CPU = 90        # percent
STRAIN_GPU_TEMP = 85   # degrees C
STRAIN_READS = 3       # this many high CPU reads in a row is strain (one busy moment is not)
GPU_EVERY_S = 6.0      # the graphics card's tool is a program start: not on every read

_high_cpu = 0
_gpu: tuple[float, int | None] = (0.0, None)


def _gpu_temp() -> int | None:
    """The graphics card's temperature in C, from nvidia-smi when the PC has
    it. None when there is no such tool or it says nothing usable."""
    global _gpu
    at, value = _gpu
    if time.monotonic() - at < GPU_EVERY_S and at:
        return value
    value = None
    tool = shutil.which("nvidia-smi")
    if tool:
        try:
            out = subprocess.run([tool, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=3,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            first = out.strip().splitlines()[0].strip() if out.strip() else ""
            value = int(first) if first.isdigit() else None
        except Exception:
            value = None
    _gpu = (time.monotonic(), value)
    return value


def read() -> dict:
    """{"cpu", "memory", "battery", "charging", "gpu_temp", "strain"}: whole
    numbers (percent, degrees C), None for what this PC does not report."""
    global _high_cpu
    cpu = round(psutil.cpu_percent(interval=None))   # since the last call: never waits
    memory = round(psutil.virtual_memory().percent)
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    gpu = _gpu_temp()
    _high_cpu = _high_cpu + 1 if cpu >= STRAIN_CPU else 0
    return {
        "cpu": cpu,
        "memory": memory,
        "battery": round(battery.percent) if battery is not None else None,
        "charging": bool(battery.power_plugged) if battery is not None else None,
        "gpu_temp": gpu,
        "strain": _high_cpu >= STRAIN_READS or (gpu is not None and gpu >= STRAIN_GPU_TEMP),
    }


def reset() -> None:
    """Tests only."""
    global _high_cpu, _gpu
    _high_cpu, _gpu = 0, (0.0, None)
