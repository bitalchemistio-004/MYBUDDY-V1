"""Removing entries from the vector store (used to clear invented memories)."""
import numpy as np

from memory.vector_store import VectorStore


def test_remove_where_drops_matching_entries_and_persists(tmp_path):
    vs = VectorStore()
    vs.storage_path = str(tmp_path / "vectors.npz")
    vs.vectors = [np.ones(3, dtype=np.float32), np.zeros(3, dtype=np.float32)]
    vs.metadata = [{"text": "MBUDDY: You slept in after the 3 a.m. call."}, {"text": "Sam likes football"}]
    removed = vs.remove_where(lambda meta: "3 a.m." in meta.get("text", ""))
    assert removed == 1
    assert [m["text"] for m in vs.metadata] == ["Sam likes football"]
    assert len(vs.vectors) == 1

    reloaded = VectorStore()
    reloaded.storage_path = vs.storage_path
    reloaded._load()
    assert [m["text"] for m in reloaded.metadata] == ["Sam likes football"]
