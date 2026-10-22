"""
core/discovery_beacon.py — let the device find the backend without a fixed IP.

The firmware had the server's address compiled in (`WS_HOST "10.67.69.172"`).
That address is a DHCP lease, so it changes when the router hands out a
different one or the PC joins another network — and when it does, the device is
stranded with no way to find the backend again. This was observed live: the PC
moved to 192.168.0.180 and the ESP32 kept trying 10.67.69.172.

There was already an mDNS broadcaster, but it advertises an A record under a
service name (`_mbuddy._tcp.local.`) rather than the PTR/SRV/TXT records a
service actually needs, so nothing could have resolved it. Rather than repair a
protocol implementation by hand, this is a small purpose-built beacon over UDP:
the device asks "where is MBUDDY?", the backend answers with its address, and
the device remembers it.

Protocol, deliberately plain text so it can be read with any UDP tool:

    device  -> broadcast :18830   "MBUDDY-DISCOVER?"
    backend -> unicast reply      "MBUDDY-HERE <ip> <port> <name>"

The backend also announces unprompted every few seconds, so a device that
misses the reply still learns the address by listening.
"""
from __future__ import annotations

import socket
import threading

import utils.logger as log

#: Port the beacon listens and announces on. Distinct from the HTTP port so a
#: misconfigured client cannot end up talking to the web server.
BEACON_PORT = 18830

PROBE = b"MBUDDY-DISCOVER?"
REPLY_PREFIX = "MBUDDY-HERE"

#: How often to announce unprompted. Frequent enough that a device rebooting
#: into a changed network finds the backend within a few seconds, rare enough
#: to be invisible on the network.
ANNOUNCE_INTERVAL_S = 5.0


def local_ip_for_lan() -> str:
    """
    This machine's address on the network the device can reach.

    A machine usually has several — virtual switches, VirtualBox host-only
    adapters, Bluetooth. Opening a UDP socket toward a public address makes the
    routing table pick the one that actually carries LAN traffic, which is the
    only one worth telling the device about.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        probe.close()


class DiscoveryBeacon:
    """Answers "where is MBUDDY?" and announces the backend's address."""

    def __init__(self, service_port: int = 5000, name: str = "mbuddy") -> None:
        self.service_port = service_port
        self.name = name
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sock: socket.socket | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────
    def start(self) -> None:
        """Begin answering probes and announcing, in background threads."""
        if self._threads:
            return
        self._stop.clear()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            sock.bind(("", BEACON_PORT))
            self._sock = sock
        except Exception as exc:
            log.warn(f"Discovery beacon could not bind :{BEACON_PORT} — {exc}")
            return

        for target, label in ((self._serve, "beacon-respond"), (self._announce, "beacon-announce")):
            thread = threading.Thread(target=target, name=label, daemon=True)
            thread.start()
            self._threads.append(thread)

        log.info(f"Discovery beacon on :{BEACON_PORT} -> {self.address()}")

    def stop(self) -> None:
        """Stop the beacon and release the socket."""
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._threads.clear()

    # ── Content ───────────────────────────────────────────────────────
    def address(self) -> str:
        """The `ip:port` the device should connect to, resolved fresh."""
        return f"{local_ip_for_lan()}:{self.service_port}"

    def _payload(self) -> bytes:
        """
        The announcement line.

        The address is resolved on every send rather than cached, so a machine
        that changes network starts advertising the new address immediately —
        which is the whole point of the beacon.
        """
        return f"{REPLY_PREFIX} {local_ip_for_lan()} {self.service_port} {self.name}".encode()

    # ── Threads ───────────────────────────────────────────────────────
    def _serve(self) -> None:
        """Reply to probes, unicast, so the asker gets an immediate answer."""
        while not self._stop.is_set() and self._sock is not None:
            try:
                data, sender = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break

            if PROBE not in data.strip():
                continue
            try:
                self._sock.sendto(self._payload(), sender)
                log.info(f"Discovery: answered {sender[0]}")
            except Exception:
                pass

    def _announce(self) -> None:
        """Broadcast the address periodically for devices that are listening."""
        while not self._stop.is_set():
            if self._sock is not None:
                try:
                    self._sock.sendto(self._payload(), ("255.255.255.255", BEACON_PORT))
                except Exception:
                    pass
            self._stop.wait(ANNOUNCE_INTERVAL_S)


_beacon: DiscoveryBeacon | None = None


def get_beacon(service_port: int = 5000) -> DiscoveryBeacon:
    """The process-wide discovery beacon."""
    global _beacon
    if _beacon is None:
        _beacon = DiscoveryBeacon(service_port=service_port)
    return _beacon
