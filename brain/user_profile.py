"""
brain/user_profile.py - what the user wrote about themselves (S83).

The previous brain told the model, in a fixed prompt, who the user is and how
to be his companion. Now each user writes that themselves in the app
(Preferences > About you): `about_me` and `companion_style` in
core/ui_settings.py. This turns them into a short system-prompt block,
read at the moment the prompt is built, so an edit applies on the next turn.
"""
from __future__ import annotations

from core import ui_settings


def profile_text() -> str:
    """The user's own profile as a system-prompt block, or "" when both are empty."""
    try:
        about = str(ui_settings.get("about_me") or "").strip()
        style = str(ui_settings.get("companion_style") or "").strip()
    except Exception:
        return ""  # never break a reply over the profile
    parts = []
    if about:
        parts.append(f"ABOUT THE USER (written by them):\n{about}")
    if style:
        parts.append("HOW THEY WANT YOU TO BE WITH THEM (their words; follow it unless it asks "
                     f"for something unsafe or dishonest):\n{style}")
    return "\n\n".join(parts)
