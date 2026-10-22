MBUDDY maintenance tools
========================

Run these from the repository root with the project virtual environment.

- `search_sqlite.py`: read-only search for "amara" in the SQLite memory database.
- `purge_amara.py`: mutates SQLite memory by deleting rows that contain "amara".
- `scrub_vectors.py`: mutates `memory/vectors.npz` by replacing known Arabizi text fragments.
- `verify_vad_threshold.py`: read-only diagnostic for the VAD silence gate math.

These tools are intentionally outside the runtime package tree.
