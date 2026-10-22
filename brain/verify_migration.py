"""One-time, reversible: notes whose success was never checked by code are
marked unverified (U2, 2026-09-29).

Until U2, a turn where any action succeeded strengthened every recalled note,
and an app job counted "ran and the model's review passed" as a success. The
vault showed it: a "design a 3D car" skill at strength 0.90 from 4 "successes"
for a result that was never a car, and an app note strengthened although the
app was never driven. Those numbers aren't evidence, so every Skills and Apps
note without a code-checked success (no `verified` field) becomes:
status "unverified", strength at most 0.3 (below what recall shows), and one
line saying so above MBUDDY's managed text. Nothing is deleted.

The original frontmatter is backed up first (memory/backups/), running it
again changes nothing, and undo() puts every note back exactly.
"""
from __future__ import annotations

import json
from pathlib import Path

import utils.logger as log

MIGRATION = "2026-09-29"
LINE = f"Not verified by a check (migrated {MIGRATION})."
KINDS = ("Skills", "Apps")
BACKUP = Path("memory/backups") / f"unverified-{MIGRATION}.json"


def _vault():
    from brain import brain_store
    return brain_store.get_vault()


def _load(backup: Path) -> dict:
    try:
        return json.loads(backup.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def apply(vault=None, backup: Path = BACKUP) -> int:
    """Mark never-checked Skills and Apps notes unverified. Returns how many changed."""
    from brain.obsidian_vault import join_note
    vault = vault or _vault()
    if vault is None:
        return 0
    todo = []
    for kind in KINDS:
        folder = vault.base / kind
        for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
            meta, body, mtime = vault.read(path)
            if meta is None or meta.get("verified") or meta.get("migrated_unverified"):
                continue
            todo.append((path, meta, body, mtime))
    if not todo:
        return 0
    saved = _load(backup)
    for path, meta, body, _mtime in todo:
        saved.setdefault(str(path), {"meta": meta})
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(saved, indent=1, default=str), encoding="utf-8")  # before any note changes
    for path, meta, body, mtime in todo:
        new = dict(meta)
        new["status"] = "unverified"
        new["strength"] = round(min(float(meta.get("strength", 0.5)), 0.3), 4)
        new["migrated_unverified"] = MIGRATION
        new_body = body if LINE in body else f"{LINE}\n\n{body.lstrip()}"
        vault._atomic_write(path, join_note(new, new_body), expect_mtime=mtime)
    log.info(f"[Brain] marked {len(todo)} never-checked note(s) unverified (backup: {backup})")
    return len(todo)


def undo(vault=None, backup: Path = BACKUP) -> int:
    """Put every migrated note back as it was. Returns how many were restored."""
    from brain.obsidian_vault import join_note
    vault = vault or _vault()
    saved = _load(backup)
    if vault is None or not saved:
        return 0
    restored = 0
    for path_str, entry in saved.items():
        path = Path(path_str)
        if not path.is_file():
            continue
        _meta, body, mtime = vault.read(path)
        body = body.replace(f"{LINE}\n\n", "", 1).replace(LINE, "", 1)
        vault._atomic_write(path, join_note(entry["meta"], body), expect_mtime=mtime)
        restored += 1
    log.info(f"[Brain] restored {restored} note(s) from {backup}")
    return restored
