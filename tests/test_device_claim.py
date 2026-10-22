"""One live connection per device id (voice bridge moved into the app).

The app's hidden window and a Chrome tab were both "pc-shell": both mics
would feed one brain and MBUDDY would hear everything twice.
"""
import asyncio

from api.websocket_server import _DEVICE_CONNECTIONS, claim_device, release_device


class FakeWS:
    def __init__(self):
        self.closed = False
        self.close_code = None

    async def close(self, code=1000, message=b""):
        self.closed, self.close_code = True, code


def run(coro_fn):
    async def go():
        result = coro_fn()
        await asyncio.sleep(0)  # let the scheduled close run
        return result
    return asyncio.run(go())


def test_a_browser_tab_cannot_take_the_mic_from_the_app():
    _DEVICE_CONNECTIONS.clear()
    app, tab = FakeWS(), FakeWS()
    assert run(lambda: claim_device("pc-shell", "shell-app-0.2", app)) is None
    assert run(lambda: claim_device("pc-shell", "shell-0.1", tab)) == "superseded"
    assert not app.closed


def test_the_app_replaces_a_browser_tab_and_the_newest_app_wins():
    _DEVICE_CONNECTIONS.clear()
    tab, app, app2 = FakeWS(), FakeWS(), FakeWS()
    run(lambda: claim_device("pc-shell", "shell-web-0.2", tab))
    assert run(lambda: claim_device("pc-shell", "shell-app-0.2", app)) is None
    assert tab.closed and tab.close_code == 4001
    assert run(lambda: claim_device("pc-shell", "shell-app-0.2", app2)) is None and app.closed


def test_only_the_current_connection_marks_the_device_offline():
    _DEVICE_CONNECTIONS.clear()
    old, new = FakeWS(), FakeWS()
    run(lambda: claim_device("pc-shell", "shell-app-0.2", old))
    run(lambda: claim_device("pc-shell", "shell-app-0.2", new))
    assert release_device("pc-shell", old) is False
    assert release_device("pc-shell", new) is True
