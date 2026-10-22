"""Cloudflare quick tunnel lifecycle control for the local dashboard."""
from __future__ import annotations

from collections import deque
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any


class TunnelManager:
    def __init__(self) -> None:
        self._available_cache: bool | None = None
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._logs: deque[str] = deque(maxlen=120)
        self.url: str = ""
        self.started_at: float = 0.0
        self.last_error: str = ""

    @property
    def available(self) -> bool:
        """
        Whether cloudflared is installed.

        The lookup is cached because `shutil.which` scans every directory on
        PATH, and this property is read on every /status request — which the
        desktop app polls. That filesystem walk was costing roughly 19 ms per
        status call for an answer that cannot change while the process runs.
        """
        if self._available_cache is None:
            self._available_cache = shutil.which("cloudflared") is not None
        return self._available_cache

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, url: str = "http://127.0.0.1:5000") -> dict[str, Any]:
        if not self.available:
            raise RuntimeError("cloudflared is not installed or not on PATH.")
        with self._lock:
            if self.running:
                return self.snapshot()
            self.url = ""
            self.last_error = ""
            self.started_at = time.time()
            self._logs.clear()
            self._process = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", url],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            threading.Thread(target=self._reader, name="mbuddy-tunnel", daemon=True).start()
            # api/auth.py's require_auth() and validate_device_token() both
            # read this to decide whether the backend is reachable beyond
            # the LAN. It previously had to be set by hand before startup;
            # setting it here means both auth checks actually tighten the
            # moment a tunnel is really running, not only when an operator
            # remembers the env var.
            os.environ["MBUDDY_TUNNEL_ACTIVE"] = "1"
        return self.snapshot()

    def stop(self) -> None:
        with self._lock:
            proc = self._process
            self._process = None
            os.environ["MBUDDY_TUNNEL_ACTIVE"] = "0"
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": self.available,
                "running": self.running,
                "url": self.url,
                "started_at": self.started_at,
                "last_error": self.last_error,
                "logs": list(self._logs)[:40],
            }

    def _reader(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        pattern = re.compile(r"https://[-a-zA-Z0-9.]+\.trycloudflare\.com")
        try:
            for line in proc.stdout:
                text = line.strip()
                if not text:
                    continue
                match = pattern.search(text)
                if match:
                    self.url = match.group(0)
                self._logs.appendleft(text)
        except Exception as exc:
            self.last_error = str(exc)


tunnel_manager = TunnelManager()
