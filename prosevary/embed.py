"""
Embedding backends.

Priority order (first available wins unless forced):
  1. Ollama HTTP /api/embeddings
  2. sentence-transformers in-process
  3. HashEmbedder — deterministic pseudo-vectors for offline scaffold tests

Never lowercase technical prose before embedding.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from .store import Store


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class Embedder(ABC):
    model_id: str
    # False when cosine on this backend is not meaningfully semantic, so
    # callers can report that the tau gate is decorative.
    semantic: bool = True

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        ...

    def embed_cached(self, text: str, store: Optional[Store] = None) -> List[float]:
        if store is not None:
            hit = store.get_embedding(self.model_id, text)
            if hit is not None:
                return hit
        vec = self.embed(text)
        if store is not None:
            store.put_embedding(self.model_id, text, vec)
        return vec


class HashEmbedder(Embedder):
    """
    Offline stand-in: bag-of-hashed-tokens → fixed dim L2-normalized vector.
    Useful for scaffolding and freeze/pipeline tests without a model.
    Cosine is only weakly semantic — do not trust τ on this backend.
    """

    semantic = False

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.model_id = f"hash-embed-{dim}"

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in text.split():
            h = hashlib.sha256(tok.encode("utf-8")).digest()
            # two uint32s → two buckets with ±1
            for i in range(0, 8, 4):
                idx = struct.unpack_from("<I", h, i)[0] % self.dim
                sign = 1.0 if (h[i] & 1) == 0 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class OllamaEmbedder(Embedder):
    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 60.0,
    ):
        self.model_id = f"ollama:{model}"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def embed(self, text: str) -> List[float]:
        payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        emb = data.get("embedding")
        if not emb:
            raise RuntimeError(f"Ollama embeddings returned no vector: {data!r}")
        return [float(x) for x in emb]


class SentenceTransformerEmbedder(Embedder):
    """Optional heavy path — sentence-transformers in-process."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.model_id = f"st:{model_name}"
        self._model = SentenceTransformer(model_name)

    def embed(self, text: str) -> List[float]:
        # Do not lowercase identifiers. normalize_embeddings=True is L2.
        vec = self._model.encode(text, normalize_embeddings=True)
        return [float(x) for x in vec]


def ollama_available(base_url: str = "http://127.0.0.1:11434", timeout: float = 1.5) -> bool:
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def ollama_list_models(
    base_url: str = "http://127.0.0.1:11434", timeout: float = 1.5
) -> Optional[List[str]]:
    """Return model names from /api/tags, or None if the server is unreachable."""
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return None
    # Something other than Ollama may answer on this port — a proxy, a dev
    # server — and return a JSON array, null, or a bare string. This runs on
    # the default --embed auto path whenever the port responds, so an
    # unguarded .get() here tracebacks the CLI.
    if not isinstance(data, dict):
        return None
    names: List[str] = []
    rows = data.get("models")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name") or row.get("model")
        if name:
            names.append(str(name))
    return names


def ollama_has_model(
    model: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 1.5,
) -> bool:
    """
    True if Ollama lists this embedding/chat model.

    Names may be bare (`nomic-embed-text`) or tagged (`nomic-embed-text:latest`).
    """
    names = ollama_list_models(base_url=base_url, timeout=timeout)
    if names is None:
        return False
    want = model.strip()
    if not want:
        return False
    for name in names:
        if name == want or name.startswith(want + ":"):
            return True
    return False


def make_embedder(kind: str = "auto", model: Optional[str] = None) -> Embedder:
    """
    kind: auto | hash | ollama | st
    """
    kind = (kind or "auto").lower()
    embed_model = model or os.environ.get("PROSEVARY_EMBED_MODEL", "nomic-embed-text")
    if kind == "hash":
        return HashEmbedder()
    if kind == "ollama":
        return OllamaEmbedder(model=embed_model)
    if kind == "st":
        return SentenceTransformerEmbedder(
            model_name=model or os.environ.get(
                "PROSEVARY_ST_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
            )
        )
    # auto: only use Ollama when the configured embed model is actually present.
    if ollama_available() and ollama_has_model(embed_model):
        try:
            return OllamaEmbedder(model=embed_model)
        except Exception:
            pass
    try:
        return SentenceTransformerEmbedder(
            model_name=model
            or os.environ.get("PROSEVARY_ST_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        )
    except Exception:
        return HashEmbedder()
