import os
import time
import json
import threading
import numpy as np

# Quiet Hugging Face / transformers before heavy imports
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

ENABLE_VECTOR_MEMORY = os.getenv("MBUDDY_ENABLE_VECTOR_MEMORY", "1").lower() in {"1", "true", "yes"}
VECTOR_MODEL_NAME = os.getenv("MBUDDY_VECTOR_MODEL", "all-MiniLM-L6-v2")

_shared_store: "VectorStore | None" = None


class VectorStore:
    """
    Local semantic memory store using embeddings and cosine similarity.
    """

    def __init__(self, model_name: str = VECTOR_MODEL_NAME):
        self.enabled = ENABLE_VECTOR_MEMORY
        self.model_name = model_name
        self.model = None
        self._model_load_failed = False
        self._lock = threading.RLock()
        self.vectors = []  # List of np.ndarray
        self.metadata = [] # List of dicts (text, time, etc.)
        self.storage_path = "memory/vectors.npz"
        # Additions since the last disk write, and when that write happened.
        self._pending = 0
        self._last_flush = time.time()
        self._load()

    def _ensure_model(self) -> bool:
        """Load the embedding model only when semantic memory is actually used."""
        if not self.enabled:
            return False
        with self._lock:
            if self.model is not None:
                return True
            if self._model_load_failed:
                return False
            try:
                from sentence_transformers import SentenceTransformer

                # The cached copy first: asking the Hub on every start took
                # 13.8 s against 0.3 s local (M2 S4, G1: the first turn's
                # memory search timed out on it). Download only when missing.
                try:
                    self.model = SentenceTransformer(self.model_name, local_files_only=True)
                    return True
                except Exception:
                    pass
                hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
                if hf_token:
                    try:
                        self.model = SentenceTransformer(self.model_name, token=hf_token)
                    except TypeError:
                        self.model = SentenceTransformer(self.model_name, use_auth_token=hf_token)
                else:
                    self.model = SentenceTransformer(self.model_name)
                return True
            except Exception:
                self._model_load_failed = True
                return False

    def add(self, text: str, metadata: dict | None = None) -> None:
        """Embed text and add to the local index."""
        if not text or not self._ensure_model():
            return

        try:
            with self._lock:
                vector = self.model.encode(text, show_progress_bar=False)
                self.vectors.append(np.asarray(vector, dtype=np.float32))
                self.metadata.append({"text": text, **(metadata or {})})
                self._pending += 1
                self._maybe_flush()
        except Exception:
            pass

    def search(self, query: str, top_k: int = 4, load_if_needed: bool = False) -> list[dict]:
        """Return top_k most semantically similar stored entries."""
        if not query or not self.vectors:
            return []
        if self.model is None and (not load_if_needed or not self._ensure_model()):
            return []

        try:
            with self._lock:
                query_vec = np.asarray(self.model.encode(query, show_progress_bar=False), dtype=np.float32)
                vectors = list(self.vectors)
                metadata = list(self.metadata)

            scores = []
            norm_b = np.linalg.norm(query_vec)
            for vec in vectors:
                norm_a = np.linalg.norm(vec)
                score = np.dot(vec, query_vec) / (norm_a * norm_b + 1e-9)
                scores.append(float(score))

            top_indices = np.argsort(scores)[::-1][:top_k]

            results = []
            for idx in top_indices:
                if scores[idx] > 0.4:
                    results.append(metadata[idx])
            return results
        except Exception:
            return []

    def warmup(self) -> bool:
        """Best-effort model preload for runtime; safe to call in a background thread."""
        return self._ensure_model()

    # Writing the index costs the whole index, so a burst of additions should
    # cost one write rather than one each.
    FLUSH_AFTER_ADDS = 8
    FLUSH_AFTER_SECONDS = 20.0

    def _maybe_flush(self, force: bool = False) -> None:
        """
        Write to disk if enough has changed, or enough time has passed.

        Called with the lock held. Anything not yet flushed is still present in
        memory and searchable; only durability is deferred, and `flush()` on
        shutdown closes that window.
        """
        if self._pending <= 0:
            return
        overdue = (time.time() - self._last_flush) >= self.FLUSH_AFTER_SECONDS
        if force or overdue or self._pending >= self.FLUSH_AFTER_ADDS:
            self._save()
            self._pending = 0
            self._last_flush = time.time()

    def flush(self) -> None:
        """Persist anything still pending. Safe to call at any time."""
        with self._lock:
            self._maybe_flush(force=True)

    def _save(self):
        """Persist vectors and metadata to disk."""
        if not self.vectors:
            return
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            np.savez_compressed(
                self.storage_path,
                vectors=np.asarray(self.vectors, dtype=np.float32),
                metadata=json.dumps(self.metadata)
            )
        except Exception:
            pass

    def _load(self):
        """Restore vectors and metadata from disk."""
        if not os.path.exists(self.storage_path):
            return
        try:
            data = np.load(self.storage_path, allow_pickle=True)
            self.vectors = [np.asarray(v, dtype=np.float32) for v in data["vectors"]]
            raw_metadata = data["metadata"]
            if hasattr(raw_metadata, "item"):
                raw_metadata = raw_metadata.item()
            self.metadata = json.loads(str(raw_metadata))
        except Exception:
            self.vectors = []
            self.metadata = []

    def remove_where(self, predicate) -> int:
        """
        Drop every entry whose metadata matches `predicate`, save, and return
        how many went. Used to clear memories that should never have been
        stored, such as a reply in which the model invented a past event.
        """
        with self._lock:
            keep = [i for i, meta in enumerate(self.metadata) if not predicate(meta)]
            removed = len(self.metadata) - len(keep)
            if removed:
                self.vectors = [self.vectors[i] for i in keep]
                self.metadata = [self.metadata[i] for i in keep]
                if self.vectors:
                    self._save()
                elif os.path.exists(self.storage_path):
                    os.remove(self.storage_path)
            return removed

    def clear(self) -> None:
        """Wipe the in-memory index and storage."""
        self.vectors = []
        self.metadata = []
        if os.path.exists(self.storage_path):
            os.remove(self.storage_path)


def get_vector_store() -> VectorStore:
    """One shared embedding model for the whole process (avoids reload per reply)."""
    global _shared_store
    if _shared_store is None:
        _shared_store = VectorStore()
    return _shared_store
