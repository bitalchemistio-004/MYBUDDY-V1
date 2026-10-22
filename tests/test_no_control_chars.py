"""No stray control characters in source (a shell-escaping slip turned regex
word boundaries into literal backspaces three times; each silently broke a
regex: character reactions, the mic list, the user-name setting)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIRS = ["api", "brain", "core", "memory", "stt", "tts", "tools", "tests", "shell/ui", "shell/src-tauri/src"]
EXTS = {".py", ".js", ".rs", ".css", ".html", ".json"}
BAD = {chr(c) for c in range(32)} - {"\n", "\r", "\t"}


def test_source_has_no_control_characters():
    offenders = []
    for d in DIRS:
        for path in (ROOT / d).rglob("*"):
            if path.suffix in EXTS and path.is_file() and "__pycache__" not in path.parts:
                text = path.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if any(ch in BAD for ch in line):
                        offenders.append(f"{path.relative_to(ROOT)}:{i}")
    assert not offenders, offenders


def test_every_ui_script_parses():
    """A syntax error in one module blanks the whole app window; catch it here."""
    import shutil
    import subprocess

    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    bad = []
    for path in sorted((ROOT / "shell" / "ui").rglob("*.js")):
        r = subprocess.run([node, "--check", "--input-type=module"], input=path.read_text(encoding="utf-8"),
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            bad.append(f"{path.relative_to(ROOT)}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'error'}")
    assert not bad, bad
