"""The real icon of a program on this PC, as PNG bytes.

The buddy shows which app it is working in. A drawn stand-in would be a
guess; Windows already has each program's own icon, so that is what is read
(read only: nothing on the PC changes). None when there is no icon to read,
and the caller shows a letter instead.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading

_lock = threading.Lock()
# One icon read at a time, and never inside the server process. Reading an
# icon loads numpy, PIL and pywin32 and makes GDI/COM calls; run in the
# server's worker threads during boot, those stalled every request (the GIL
# held through a DLL load or a GDI wait, for minutes). A child process can
# stall alone and be killed after a timeout.
_work_lock = threading.Lock()
CHILD_TIMEOUT = 10.0
_NO_ICON = 3  # the child's exit code for "this program has no icon"
_cache: dict[tuple[str, float, int], bytes | None] = {}


def _target(path: str) -> str:
    """A shortcut's target; any other path as it is."""
    if path.lower().endswith(".lnk"):
        try:
            import win32com.client
            return win32com.client.Dispatch("WScript.Shell").CreateShortcut(path).TargetPath or path
        except Exception:
            return path
    return path


def _child_argv(path: str, size: int) -> list[str]:
    return [sys.executable, "-m", "core.app_icon", path, str(size)]


def _read_in_child(path: str, size: int) -> tuple[bytes | None, bool]:
    """(png, final). final is False when the child timed out or failed to run,
    so the caller does not remember the miss and asks again later."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(_child_argv(path, size), cwd=root, capture_output=True,
                              timeout=CHILD_TIMEOUT, creationflags=flags)
    except (subprocess.TimeoutExpired, OSError):
        return None, False
    if done.returncode == 0 and done.stdout:
        return done.stdout, True
    return None, done.returncode == _NO_ICON


def for_path(path: str, size: int = 64) -> bytes | None:
    """The icon of the program (or shortcut) at `path`, `size` px square.
    None when there is none (or it could not be read in time)."""
    path = str(path or "")
    try:
        key = (os.path.normcase(path), os.path.getmtime(path), size)
    except OSError:
        return None
    with _lock:
        if key in _cache:
            return _cache[key]
    with _work_lock:
        with _lock:
            if key in _cache:  # read by the request that held the lock before
                return _cache[key]
        png, final = _read_in_child(path, size)
    if final:
        with _lock:
            if len(_cache) > 64:
                _cache.clear()
            _cache[key] = png
    return png


def _icon_handle(path: str, size: int) -> int:
    """The program's first icon at `size` px (Windows picks the closest drawn
    size in the file, so a 64 px request is sharp, not a stretched 32)."""
    import ctypes
    from ctypes import wintypes
    extract = ctypes.windll.user32.PrivateExtractIconsW
    extract.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT), wintypes.UINT, wintypes.UINT]
    extract.restype = wintypes.UINT
    handle = wintypes.HICON()
    got = extract(path, 0, size, size, ctypes.byref(handle), None, 1, 0)
    return handle.value if got not in (0, 0xFFFFFFFF) and handle.value else 0


def _read(path: str, size: int) -> bytes | None:
    try:
        import numpy as np
        import win32con
        import win32gui
        import win32ui
        from PIL import Image
    except ImportError:
        return None
    icon = _icon_handle(path, size)
    if not icon:
        return None
    try:
        screen = win32gui.GetDC(0)
        try:
            dc = win32ui.CreateDCFromHandle(screen)
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(dc, size, size)
            mem = dc.CreateCompatibleDC()
            old = mem.SelectObject(bitmap)
            # Drawn onto black and onto white: where the two differ the icon
            # is see-through. That gives its real alpha whatever format the
            # icon is stored in (old icons have a mask, new ones an alpha channel).
            shots = []
            for colour in (0x000000, 0xFFFFFF):
                mem.FillSolidRect((0, 0, size, size), colour)
                win32gui.DrawIconEx(mem.GetSafeHdc(), 0, 0, icon, size, size, 0, None, win32con.DI_NORMAL)
                bits = np.frombuffer(bitmap.GetBitmapBits(True), dtype=np.uint8).reshape(size, size, 4)
                shots.append(bits[:, :, :3].astype(np.float32))   # BGR
            mem.SelectObject(old)
            mem.DeleteDC()
            win32gui.DeleteObject(bitmap.GetHandle())
        finally:
            win32gui.ReleaseDC(0, screen)
    finally:
        win32gui.DestroyIcon(icon)
    on_black, on_white = shots
    alpha = 255.0 - (on_white - on_black).mean(axis=2)
    if alpha.max() < 1:
        return None  # nothing was drawn
    colour = np.where(alpha[..., None] > 0, on_black * 255.0 / np.maximum(alpha[..., None], 1.0), 0)
    rgba = np.dstack([colour[:, :, ::-1], alpha[..., None]]).clip(0, 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(out, "PNG")
    return out.getvalue()


def _main(argv: list[str]) -> int:
    """`python -m core.app_icon PATH SIZE`: the PNG on stdout (the child of for_path)."""
    try:
        png = _read(_target(argv[1]), int(argv[2]))
    except Exception:
        png = None
    if not png:
        return _NO_ICON
    sys.stdout.buffer.write(png)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
