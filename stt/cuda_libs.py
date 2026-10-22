"""
stt/cuda_libs.py - make pip-installed CUDA libraries loadable on Windows.

faster-whisper's CTranslate2 needs cuBLAS 12 and cuDNN 9 at run time. The
`nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels put those DLLs under
site-packages/nvidia/<lib>/bin, which Windows does not search. Without this,
loading the model works and the first transcription fails with
"cublas64_12.dll is not found".
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_registered: list[str] = []


def register_cuda_dll_dirs() -> list[str]:
    """Add every site-packages/nvidia/*/bin to the DLL search path, once."""
    if _registered or sys.platform != "win32":
        return _registered
    for base in map(Path, sys.path):
        nvidia = base / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in sorted(nvidia.glob("*/bin")):
            os.add_dll_directory(str(bin_dir))
            # Some loaders resolve dependencies through PATH, not the
            # add_dll_directory list, so both are set.
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            _registered.append(str(bin_dir))
    return _registered
