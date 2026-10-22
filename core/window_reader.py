"""Reading what a window shows, the way a screen reader does: through Windows' accessibility interface (UI Automation).

This gives the real texts, buttons and fields of an app, which is far more exact than looking at a picture of it,
and it needs no change in the app. It only reads: nothing is clicked, typed or changed.
What it leaves out on purpose: password fields (their content is never read), and a short list of windows that hold
secrets (password managers, Windows security prompts). The result is short (a few thousand characters).
"""
from __future__ import annotations

import re

import utils.logger as log

TEXT_TYPES = {50020: "text", 50030: "document", 50007: "item", 50004: "field", 50005: "link", 50029: "title", 50026: "group", 50036: "table"}
CONTROL_TYPES = {50000: "button", 50011: "menu item", 50019: "tab", 50002: "check box", 50013: "radio button", 50003: "list box"}
SECRET_WINDOWS = re.compile(r"keepass|1password|bitwarden|lastpass|dashlane|windows security|user account control|credential|password manager", re.I)
MAX_NODES = 600


def refuses(title: str, app: str) -> str:
    """"" when this window may be read, else why not."""
    if SECRET_WINDOWS.search(f"{title} {app}"):
        return "That window holds passwords or security prompts, so I don't read it."
    return ""


def read(hwnd: int, max_chars: int = 3000) -> dict:
    """{"texts": [...], "controls": [...]} for the window, or {"error": "..."}."""
    try:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        try:
            mod = comtypes.client.GetModule("UIAutomationCore.dll")
            uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation)
            root = uia.ElementFromHandle(hwnd)
            found = root.FindAll(4, uia.ControlViewCondition)          # 4 = every descendant
            texts: list[str] = []
            controls: list[str] = []
            seen: set[str] = set()
            budget = max_chars
            for i in range(min(found.Length, MAX_NODES)):
                e = found.GetElement(i)
                try:
                    ctype, name = e.CurrentControlType, (e.CurrentName or "").strip()
                    if e.CurrentIsPassword:
                        continue                                        # never a password field
                    value = ""
                    if ctype == 50004:                                  # an edit box: what is typed in it
                        try:
                            pattern = e.GetCurrentPattern(10002).QueryInterface(mod.IUIAutomationValuePattern)
                            value = (pattern.CurrentValue or "").strip()
                        except Exception:
                            value = ""
                except Exception:
                    continue
                line = (f"{name}: {value}" if name and value else value or name)[:300]
                if not line or line in seen:
                    continue
                seen.add(line)
                if ctype in CONTROL_TYPES:
                    controls.append(f"[{CONTROL_TYPES[ctype]}] {line}")
                elif ctype in TEXT_TYPES:
                    texts.append(line)
                    budget -= len(line)
                    if budget <= 0:
                        break
            return {"texts": texts, "controls": controls[:40]}
        finally:
            comtypes.CoUninitialize()
    except Exception as exc:
        log.warn(f"[Screen] could not read a window: {type(exc).__name__}: {exc}")
        return {"error": f"Windows wouldn't let me read that window ({type(exc).__name__})."}


def describe(title: str, app: str, content: dict, limit: int = 2500) -> str:
    """The words the model gets: what the window shows, honestly short."""
    if "error" in content:
        return content["error"]
    body = "\n".join(content["texts"])[:limit]
    buttons = "; ".join(c for c in content["controls"][:25])
    if not body and not buttons:
        return f"{app} (“{title[:60]}”) shows nothing I can read: it doesn't share its contents with the accessibility interface."
    return f"{app} (“{title[:60]}”) shows:\n{body}" + (f"\nControls: {buttons}" if buttons else "")
