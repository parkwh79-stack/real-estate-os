from __future__ import annotations

from agents.retrieval import (
    EmbeddingRetrievalIndex,
    LexicalRetrievalIndex,
    build_retrieval_index,
    tokenize,
)


def test_tokenize_handles_latin_and_korean():
    toks = tokenize("Daegu 주상복합 PF")
    assert "daegu" in toks and "pf" in toks
    assert "주상" in toks  # CJK bigram


def test_lexical_ranks_relevant_doc_first():
    idx = LexicalRetrievalIndex()
    idx.add([
        {"id": "a", "text": "대구 주상복합 아파트 PF 미분양", "metadata": {}},
        {"id": "b", "text": "춘천 물류센터 임대 cap rate", "metadata": {}},
        {"id": "c", "text": "부산 오피스텔 분양", "metadata": {}},
    ])
    hits = idx.search("대구 주상복합 미분양", k=2)
    assert hits and hits[0]["document"]["id"] == "a"
    assert all(h["score"] > 0 for h in hits)


def test_lexical_empty_query_or_corpus():
    idx = LexicalRetrievalIndex()
    assert idx.search("anything") == []
    idx.add([{"id": "a", "text": "x", "metadata": {}}])
    assert idx.search("") == []


def test_embedding_index_with_fake_embedder():
    # 2-D vectors: query closest to doc 'b'
    vectors = {
        "q": [1.0, 0.0],
        "a": [0.0, 1.0],
        "b": [0.9, 0.1],
        "c": [-1.0, 0.0],
    }
    idx = EmbeddingRetrievalIndex(lambda t: vectors[t])
    idx.add([{"id": k, "text": k, "metadata": {}} for k in ("a", "b", "c")])
    hits = idx.search("q", k=2)
    assert hits[0]["document"]["id"] == "b"
    # opposite vector (negative cosine) is filtered out
    assert all(h["document"]["id"] != "c" for h in hits)


def test_factory_default_is_lexical(monkeypatch):
    monkeypatch.delenv("REALYTICS_RETRIEVAL_BACKEND", raising=False)
    assert build_retrieval_index().backend == "lexical"


def test_factory_embedding_uses_injected_embed_fn():
    idx = build_retrieval_index(backend="embedding", embed_fn=lambda t: [1.0, 0.0])
    assert idx.backend == "embedding"
