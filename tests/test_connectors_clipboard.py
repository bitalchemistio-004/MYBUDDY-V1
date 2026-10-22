"""Clipboard connector against a fake win32clipboard (the real clipboard is never touched)."""
import pytest

import brain.connectors as conn
import brain.connectors.clipboard as clip
from brain.tools import ToolFailure


class FakeClipboard:
    CF_UNICODETEXT = 13
    CF_HDROP = 15
    CF_DIB = 8

    def __init__(self, data=None, busy=0, drop_writes=False):
        self.data = dict(data or {})
        self.busy = busy          # OpenClipboard fails this many times first
        self.open = False
        self.drop_writes = drop_writes

    def OpenClipboard(self):
        if self.busy:
            self.busy -= 1
            raise OSError("in use by another app")
        self.open = True

    def CloseClipboard(self):
        self.open = False

    def EmptyClipboard(self):
        assert self.open
        self.data.clear()

    def SetClipboardText(self, text, fmt):
        assert self.open
        if not self.drop_writes:
            self.data[fmt] = text

    def IsClipboardFormatAvailable(self, fmt):
        return fmt in self.data

    def GetClipboardData(self, fmt):
        assert self.open
        return self.data[fmt]


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        f = FakeClipboard(**kw)
        monkeypatch.setattr(clip, "_win32", f)
        monkeypatch.setattr(clip.time, "sleep", lambda s: None)
        return f
    return install


def test_read_text(fake):
    f = fake(data={13: "hello world"})
    out = conn.run("clipboard_read")
    assert "hello world" in out and "11 characters" in out
    assert not f.open, "clipboard must be closed again"


def test_read_long_text_is_cut_and_says_so(fake):
    fake(data={13: "x" * 5000})
    out = conn.run("clipboard_read")
    assert "cut" in out and "5000" in out
    assert out.count("x") <= clip.MAX_READ_CHARS + 10


def test_read_files_and_image(fake):
    fake(data={15: ("C:\\a.txt", "C:\\b.png"), 8: b"..."})
    out = conn.run("clipboard_read")
    assert "no text" in out and "C:\\a.txt" in out and "an image" in out


def test_read_empty_is_a_failure(fake):
    fake()
    assert isinstance(conn.run("clipboard_read"), ToolFailure)


def test_busy_clipboard_is_retried(fake):
    fake(data={13: "ok"}, busy=3)
    assert "ok" in conn.run("clipboard_read")


def test_write_is_verified_by_reading_back(fake):
    f = fake(data={13: "old"})
    out = conn.run("clipboard_write", {"text": "new text"})
    assert out == "Copied 8 characters to the clipboard."
    assert f.data[13] == "new text" and not f.open


def test_write_that_did_not_land_is_a_failure(fake):
    fake(drop_writes=True)
    out = conn.run("clipboard_write", {"text": "lost"})
    assert isinstance(out, ToolFailure)


def test_write_preview_quotes_the_text():
    a = conn.get("clipboard_write")
    assert a.risk == "low"
    assert "hello" in a.describe({"text": "hello"})
    assert a.describe({"text": "y" * 200}).endswith("...\"")
