from __future__ import annotations

"""Retrieval layer for RAG (grounding only — never numbers/decisions).

A small, dependency-free lexical index (TF-IDF) is the default so semantic
retrieval works on-prem with no extra services. An embedding index is available
for higher recall; it takes a pluggable embed function so the cosine logic is
unit-tested with a fake embedder, and the production default embeds via the
auto-selected AI provider (lazy). Every hit carries its source citation so
retrieved context is auditable.
"""

import math
import re
from typing import Any, Callable, Dict, List, Optional

_LATIN = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[가-힣぀-ヿ一-鿿]+")


def tokenize(text: str) -> List[str]:
    """Latin word tokens + CJK character bigrams (handles spaceless Korean)."""
    text = str(text or "").lower()
    tokens: List[str] = _LATIN.findall(text)
    for run in _CJK.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


class LexicalRetrievalIndex:
    """Deterministic TF-IDF retrieval. No external dependencies."""

    backend = "lexical"

    def __init__(self) -> None:
        self._docs: List[Dict[str, Any]] = []
        self._tokens: List[List[str]] = []
        self._df: Dict[str, int] = {}

    def add(self, documents: List[Dict[str, Any]]) -> None:
        for doc in documents:
            toks = tokenize(doc.get("text", ""))
            self._docs.append(doc)
            self._tokens.append(toks)
            for term in set(toks):
                self._df[term] = self._df.get(term, 0) + 1

    def _idf(self, term: str) -> float:
        n = len(self._docs)
        df = self._df.get(term, 0)
        return math.log((n + 1) / (df + 1)) + 1.0

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        q_terms = tokenize(query)
        if not q_terms or not self._docs:
            return []
        q_weight = {t: self._idf(t) for t in set(q_terms)}
        results: List[Dict[str, Any]] = []
        for doc, toks in zip(self._docs, self._tokens):
            if not toks:
                continue
            tf: Dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            score = sum(q_weight[t] * (tf.get(t, 0) / len(toks)) for t in q_weight)
            if score > 0:
                results.append({"document": doc, "score": round(score, 6)})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:k]


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class EmbeddingRetrievalIndex:
    """Cosine-similarity retrieval over embeddings from a pluggable embed fn.

    embed_fn(text) -> vector. Production wires it to the auto-selected provider's
    embeddings endpoint (lazy); tests inject a deterministic fake embedder.
    """

    backend = "embedding"

    def __init__(self, embed_fn: Callable[[str], List[float]]):
        self._embed = embed_fn
        self._docs: List[Dict[str, Any]] = []
        self._vectors: List[List[float]] = []

    def add(self, documents: List[Dict[str, Any]]) -> None:
        for doc in documents:
            self._docs.append(doc)
            self._vectors.append(self._embed(doc.get("text", "")))

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        if not self._docs:
            return []
        q = self._embed(query)
        scored = [
            {"document": doc, "score": round(_cosine(q, vec), 6)}
            for doc, vec in zip(self._docs, self._vectors)
        ]
        scored = [s for s in scored if s["score"] > 0]
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]


def default_embed_fn(ai_context: Optional[Dict[str, Any]] = None) -> Callable[[str], List[float]]:
    """Embed through the central Brain Router and audited AI connection."""
    from agents.ai_router import select_ai_context
    from agents.ai_connection import AIConnection

    ctx = ai_context or select_ai_context(tier="retrieval")
    if not ctx:
        raise RuntimeError("No AI provider configured for embeddings; use the lexical backend.")

    def _embed(text: str) -> List[float]:
        return AIConnection().embed_text(text, ai_context=ctx)

    return _embed


def build_retrieval_index(
    *,
    backend: Optional[str] = None,
    embed_fn: Optional[Callable[[str], List[float]]] = None,
):
    """Factory: 'lexical' (default, on-prem, no deps) or 'embedding'."""
    import os

    backend = (backend or os.environ.get("REALYTICS_RETRIEVAL_BACKEND") or "lexical").strip().lower()
    if backend == "embedding":
        return EmbeddingRetrievalIndex(embed_fn or default_embed_fn())
    return LexicalRetrievalIndex()
