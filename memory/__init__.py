"""
memory - SQLite-backed long-term persistence and vector store.
"""
from memory.long_term import (
    load_memory, save_memory,
    extract_and_save_facts, tag_emotional_memory,
    compress_identity_snapshot,
)
from memory.vector_store import VectorStore, get_vector_store
from memory.companion_memory import (
    build_daily_stats,
    ensure_companion_memory,
    maybe_capture_reminder,
    maybe_update_project_thread,
    maybe_write_daily_summary,
)

__all__ = [
    # Long-term persistence
    "load_memory", "save_memory",
    "extract_and_save_facts", "tag_emotional_memory",
    "compress_identity_snapshot",
    # Vector store
    "VectorStore", "get_vector_store",
    "ensure_companion_memory", "maybe_capture_reminder",
    "maybe_update_project_thread", "maybe_write_daily_summary",
    "build_daily_stats",
]
