from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VECTOR_PATH = ROOT / "memory" / "vectors.npz"

REPLACEMENTS = {
    r"\bAllah Akbar\b": "الله أكبر",
    r"\bla ilaha illallah\b": "لا إله إلا الله",
    r"\bwalillahil hamd\b": "ولله الحمد",
    r"\bana bekhair\b": "أنا بخير",
    r"\byalla\b": "يلا",
    r"\bhabibi\b": "حبيبي",
    r"\bkifak\b": "كيفك",
    r"\bkeefuck\b": "كيفك",
    r"\bmni7\b": "منيح",
}


def main() -> int:
    if not VECTOR_PATH.exists():
        print("No vector store found.")
        return 0

    data = np.load(VECTOR_PATH, allow_pickle=True)
    vectors = data["vectors"]
    raw_metadata = data["metadata"]
    if hasattr(raw_metadata, "item"):
        raw_metadata = raw_metadata.item()
    metadata = json.loads(str(raw_metadata))

    scrub_count = 0
    for entry in metadata:
        original = entry.get("text")
        if not isinstance(original, str):
            continue
        new_text = original
        for pattern, replacement in REPLACEMENTS.items():
            new_text = re.sub(pattern, replacement, new_text, flags=re.IGNORECASE)
        if new_text != original:
            entry["text"] = new_text
            scrub_count += 1

    if scrub_count:
        print(f"Scrubbed {scrub_count} entries. Saving...")
        np.savez_compressed(VECTOR_PATH, vectors=vectors, metadata=json.dumps(metadata, ensure_ascii=False))
        print("Done.")
    else:
        print("No Arabizi found in vectors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
