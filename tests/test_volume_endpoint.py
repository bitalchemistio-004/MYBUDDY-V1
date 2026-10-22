"""The volume tool finds an output device when the default one isn't there for
a moment, and says plainly when Windows has none (U1b: "Element not found")."""
from types import SimpleNamespace

import pytest

import brain.tools as tools


class Audio:
    def __init__(self, speakers_fail=2, default_ok=False, active=()):
        self.calls, self.speakers_fail, self.default_ok, self.active = 0, speakers_fail, default_ok, active

    def GetSpeakers(self):
        self.calls += 1
        if self.calls <= self.speakers_fail:
            raise OSError("Element not found.")
        return SimpleNamespace(EndpointVolume="speakers")

    def GetDeviceEnumerator(self):
        if not self.default_ok:
            raise OSError("Element not found.")
        raise OSError("not used in this test")

    def GetAllDevices(self, data_flow, device_state):
        return [SimpleNamespace(EndpointVolume=v) for v in self.active]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(tools.time, "sleep", lambda s: None)


def test_a_default_that_comes_back_is_used():
    assert tools._output_volume(Audio(speakers_fail=1)) == "speakers"


def test_an_active_device_is_used_when_there_is_no_default():
    assert tools._output_volume(Audio(speakers_fail=9, active=("usb headset",))) == "usb headset"


def test_no_device_at_all_is_said_plainly():
    with pytest.raises(OSError, match="no active output device"):
        tools._output_volume(Audio(speakers_fail=9))
