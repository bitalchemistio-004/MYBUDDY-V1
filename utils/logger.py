"""Lightweight structured logger."""
import os
import sys
import traceback


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe)


def info(msg: str):
    _safe_print(f"[INFO] {msg}")


def ok(msg: str):
    _safe_print(f"[OK] {msg}")


def warn(msg: str):
    _safe_print(f"[WARN] {msg}")


def error(msg: str, exc: Exception | None = None):
    _safe_print(f"[ERROR] {msg}")
    if exc:
        _safe_print(traceback.format_exc())


def debug(msg: str):
    if os.getenv("DEBUG"):
        _safe_print(f"[DEBUG] {msg}")


def mic(msg: str):
    _safe_print(f"[MIC] {msg}")


def brain(msg: str):
    _safe_print(f"[BRAIN] {msg}")


def mem(msg: str):
    _safe_print(f"[MEM] {msg}")


def tts(msg: str):
    _safe_print(f"[TTS] {msg}")


def ws(msg: str):
    _safe_print(f"[WS] {msg}")
