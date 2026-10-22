"""
UDP discovery for the mic_lab firmware, so it finds this PC without a fixed IP.

Same idea as the production beacon (core/discovery_beacon.py) but on its own
port and with its own message prefix, so the lab can run next to the production
backend without either answering the other's device.

    device -> broadcast :18831  "MBUDDYLAB-DISCOVER?"
    server -> unicast reply     "MBUDDYLAB-HERE <ip> <port>"
"""
from __future__ import annotations

import socket
import threading

BEACON_PORT = 18831
PROBE = b"MBUDDYLAB-DISCOVER?"
REPLY_PREFIX = "MBUDDYLAB-HERE"
ANNOUNCE_INTERVAL_S = 5.0


def lan_ip() -> str:
    """The address of the interface that routes to the LAN (not a virtual adapter)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Beacon:
    def __init__(self, service_port: int) -> None:
        self.service_port = service_port
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self.error: str | None = None

    def payload(self) -> bytes:
        return f"{REPLY_PREFIX} {lan_ip()} {self.service_port}".encode()

    def start(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            sock.bind(("", BEACON_PORT))
        except OSError as exc:
            self.error = f"could not bind UDP :{BEACON_PORT} ({exc}); device must use the compiled-in host"
            return
        self._sock = sock
        threading.Thread(target=self._serve, name="lab-beacon-reply", daemon=True).start()
        threading.Thread(target=self._announce, name="lab-beacon-announce", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()
            self._sock = None

    def _serve(self) -> None:
        while not self._stop.is_set() and self._sock:
            try:
                data, sender = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                return
            if PROBE in data:
                try:
                    self._sock.sendto(self.payload(), sender)
                except OSError:
                    pass

    def _announce(self) -> None:
        while not self._stop.is_set():
            if self._sock:
                try:
                    self._sock.sendto(self.payload(), ("255.255.255.255", BEACON_PORT))
                except OSError:
                    pass
            self._stop.wait(ANNOUNCE_INTERVAL_S)
