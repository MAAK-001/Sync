"""Local sentence embeddings (fastembed, ONNX on CPU) plus small vector helpers."""
from __future__ import annotations

import threading

import numpy as np

from .config import settings

DIM = 384


class Embedder:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(
                        model_name=settings.embed_model,
                        cache_dir=str(settings.models_dir / "embed"),
                        threads=settings.embed_threads,
                    )
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        """Unit-normalised float32 matrix, one row per text."""
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        model = self._load()
        with self._lock:  # onnxruntime sessions are not re-entrant across our threads
            vecs = np.asarray(list(model.embed(texts, batch_size=32)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-9)

    def encode1(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


embedder = Embedder()


def to_blob(v: np.ndarray | None) -> bytes | None:
    return None if v is None else np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes | None) -> np.ndarray | None:
    return None if b is None else np.frombuffer(b, dtype=np.float32)


def top_k(query: np.ndarray, matrix: np.ndarray, k: int = 5, min_score: float = -1.0) -> list[tuple[int, float]]:
    """Indices and cosine scores of the k rows most similar to ``query`` (rows are unit vectors)."""
    if matrix.size == 0:
        return []
    scores = matrix @ query
    idx = np.argsort(-scores)[:k]
    return [(int(i), float(scores[i])) for i in idx if scores[i] >= min_score]


def stack(blobs: list[bytes | None]) -> np.ndarray:
    rows = [from_blob(b) for b in blobs]
    rows = [r if r is not None else np.zeros(DIM, dtype=np.float32) for r in rows]
    return np.vstack(rows) if rows else np.zeros((0, DIM), dtype=np.float32)
