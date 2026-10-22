"""A small store for one secret (the user's Ollama cloud key), encrypted for this Windows user.

Windows DPAPI (CryptProtectData) ties the encrypted file to the signed-in Windows account: another user,
or the file copied to another PC, cannot read it. The key is never written in clear text, never returned by
any API and never logged; callers get it only through `load()` inside the backend.
Where DPAPI is not available (not Windows) nothing is stored and `save()` says so.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


class SecretError(Exception):
    pass


def _path(name: str) -> Path:
    override = os.getenv("MBUDDY_SECRETS_DIR")
    return (Path(override) if override else Path.home() / ".buddy") / f"{name}.secret"


def available() -> bool:
    return sys.platform == "win32"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _protect(data: bytes) -> bytes:
        src, _keep = _blob(data)
        out = _Blob()
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), "MBUDDY", None, None, None, 0, ctypes.byref(out)):
            raise SecretError("Windows could not encrypt the key.")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    def _unprotect(data: bytes) -> bytes:
        src, _keep = _blob(data)
        out = _Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
            raise SecretError("Windows could not decrypt the key.")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
else:
    def _protect(data: bytes) -> bytes:
        raise SecretError("Safe key storage needs Windows.")

    _unprotect = _protect


def save(name: str, value: str) -> None:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_protect(value.encode("utf-8")))


def load(name: str) -> str:
    """The secret, or "" when none is stored or it can't be read (another account, damaged file)."""
    try:
        return _unprotect(_path(name).read_bytes()).decode("utf-8")
    except (OSError, SecretError, ValueError):
        return ""


def delete(name: str) -> None:
    try:
        _path(name).unlink()
    except OSError:
        pass


def exists(name: str) -> bool:
    return _path(name).exists()
