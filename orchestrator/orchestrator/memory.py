from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from typing import Any

import httpx
from sqlalchemy import text

from orchestrator.config import settings
from orchestrator.db.models import AgentRun, BoardReview, Event, Memo, Postmortem, Venture
from orchestrator.db.session import engine, session_scope


class EmbeddingsUnavailable(RuntimeError):
    pass


_CACHE: OrderedDict[str, list[float]] = OrderedDict()
MAX_CACHE = 256
VOYAGE_MODEL = "voyage-3-lite"
OPENAI_MODEL = "text-embedding-3-small"


def _provider() -> str:
    return (settings.memory_provider or "voyage").strip().lower()


def embeddings_available() -> bool:
    return bool(settings.openai_api_key) if _provider() == "openai" else bool(settings.voyage_api_key)


def _cache_get(text_value: str) -> list[float] | None:
    key = hashlib.sha256(text_value.encode()).hexdigest()
    if key not in _CACHE:
        return None
    value = _CACHE.pop(key)
    _CACHE[key] = value
    return value


def _cache_set(text_value: str, embedding: list[float]) -> None:
    key = hashlib.sha256(text_value.encode()).hexdigest()
    _CACHE[key] = embedding
    while len(_CACHE) > MAX_CACHE:
        _CACHE.popitem(last=False)


def _normalize_vector(vec: list[float], dim: int = 1024) -> list[float]:
    values = [float(x) for x in vec[:dim]]
    if len(values) < dim:
        values.extend([0.0] * (dim - len(values)))
    return values


def _record_embedding_spend(texts: list[str], model: str) -> None:
    tokens = max(1, sum(len(t) for t in texts) // 4)
    cost = (tokens / 1_000_000.0) * (0.10 if model == VOYAGE_MODEL else 0.02)
    with session_scope() as s:
        s.add(AgentRun(agent="memory_indexer", role="Embedding Indexer", model=model, input_messages=[{"role":"system","content":f"embedding_batch:{len(texts)}"}], system_prompt="Vector memory embedding batch", output_text=f"Embedded {len(texts)} chunk(s)", input_tokens=tokens, output_tokens=0, cost_usd=cost, status="ok"))


def embed_texts(texts: list[str]) -> list[list[float]]:
    cleaned = [str(t or "").strip() for t in texts]
    out: list[list[float] | None] = []
    missing: list[str] = []
    for t in cleaned:
        cached = _cache_get(t)
        out.append(cached)
        if cached is None:
            missing.append(t)
    if missing:
        provider = _provider()
        if provider == "openai":
            if not settings.openai_api_key:
                raise EmbeddingsUnavailable("OPENAI_API_KEY is not configured for vector memory")
            with httpx.Client(timeout=30) as client:
                resp = client.post("https://api.openai.com/v1/embeddings", headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}, json={"model": OPENAI_MODEL, "input": missing, "dimensions": 1024})
                resp.raise_for_status()
                vectors = [_normalize_vector(row["embedding"]) for row in resp.json().get("data", [])]
            model = OPENAI_MODEL
        else:
            if not settings.voyage_api_key:
                raise EmbeddingsUnavailable("VOYAGE_API_KEY is not configured for vector memory")
            with httpx.Client(timeout=30) as client:
                resp = client.post("https://api.voyageai.com/v1/embeddings", headers={"Authorization": f"Bearer {settings.voyage_api_key}", "Content-Type": "application/json"}, json={"model": VOYAGE_MODEL, "input": missing, "input_type": "document"})
                resp.raise_for_status()
                vectors = [_normalize_vector(row["embedding"]) for row in resp.json().get("data", [])]
            model = VOYAGE_MODEL
        for text_value, vector in zip(missing, vectors):
            _cache_set(text_value, vector)
        _record_embedding_spend(missing, model)
    return [v if v is not None else (_cache_get(t) or []) for t, v in zip(cleaned, out)]


def _chunks(text_value: str, max_chars: int = 2400) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text_value or "") if p.strip()]
    chunks: list[str] = []
    current = ""
    for part in parts or [text_value]:
        if len(current) + len(part) + 2 <= max_chars:
            current = f"{current}\n\n{part}".strip()
        else:
            if current:
                chunks.append(current)
            current = part[:max_chars]
    if current:
        chunks.append(current[:max_chars])
    return chunks[:12]


def _source_text(source_kind: str, source_id: int) -> tuple[str, dict[str, Any]] | None:
    with session_scope() as s:
        if source_kind == "memo":
            row = s.get(Memo, source_id)
            return (row.content, {"memo_id": row.id, "idea_id": row.idea_id, "recommendation": row.recommendation}) if row else None
        if source_kind == "postmortem":
            row = s.get(Postmortem, source_id)
            return (f"{row.content_md}\n\n## Lessons\n{row.lessons_md}", {"postmortem_id": row.id, "venture_id": row.venture_id}) if row else None
        if source_kind == "lesson":
            row = s.get(Postmortem, source_id)
            return (row.lessons_md, {"postmortem_id": row.id, "venture_id": row.venture_id, "priority": "lesson"}) if row else None
        if source_kind == "charter":
            row = s.get(Venture, source_id)
            return (row.charter, {"venture_id": row.id, "slug": row.slug, "name": row.name}) if row else None
        if source_kind == "board_decision":
            row = s.get(BoardReview, source_id)
            return (row.rationale, {"board_review_id": row.id, "memo_id": row.memo_id, "persona": row.persona, "vote": row.vote}) if row else None
    return None


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def reindex_source(source_kind: str, source_id: int) -> int:
    source = _source_text(source_kind, source_id)
    if not source:
        return 0
    text_value, metadata = source
    chunks = _chunks(text_value)
    if not chunks:
        return 0
    vectors = embed_texts(chunks)
    with engine.begin() as conn:
        for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
            conn.execute(text("""INSERT INTO memory_embeddings (source_kind, source_id, chunk_index, content_chunk, embedding, metadata)
                        VALUES (:kind, :sid, :idx, :chunk, CAST(:embedding AS vector), CAST(:metadata AS jsonb))
                        ON CONFLICT (source_kind, source_id, chunk_index)
                        DO UPDATE SET content_chunk=EXCLUDED.content_chunk, embedding=EXCLUDED.embedding, metadata=EXCLUDED.metadata, created_at=now()"""),
                {"kind": source_kind, "sid": source_id, "idx": idx, "chunk": chunk, "embedding": _vector_literal(vector), "metadata": json.dumps(metadata)})
    return len(chunks)


def memory_reindex_tick() -> dict[str, Any]:
    if not embeddings_available():
        with session_scope() as s:
            s.add(Event(kind="memory_unavailable", actor="memory", message="Vector memory skipped: embedding provider key is not configured."))
        return {"indexed": 0, "available": False}
    targets: list[tuple[str, int]] = []
    with session_scope() as s:
        targets += [("memo", r.id) for r in s.query(Memo).limit(100).all()]
        targets += [("postmortem", r.id) for r in s.query(Postmortem).limit(100).all()]
        targets += [("lesson", r.id) for r in s.query(Postmortem).limit(100).all()]
        targets += [("charter", r.id) for r in s.query(Venture).limit(100).all()]
        targets += [("board_decision", r.id) for r in s.query(BoardReview).limit(200).all()]
    indexed = 0
    errors = 0
    for kind, sid in targets:
        try:
            indexed += reindex_source(kind, sid)
        except Exception as exc:
            errors += 1
            with session_scope() as s:
                s.add(Event(kind="memory_index_error", actor="memory", message=f"Failed indexing {kind} #{sid}: {exc}"))
    with session_scope() as s:
        s.add(Event(kind="memory_reindex", actor="memory", message=f"Vector memory indexed {indexed} chunk(s), errors={errors}", payload={"indexed": indexed, "errors": errors}))
    return {"indexed": indexed, "errors": errors, "available": True}


def search_memory(query: str, kinds: list[str] | None = None, limit: int = 5) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query or not embeddings_available():
        return []
    limit = max(1, min(int(limit or 5), 10))
    vector = embed_texts([query])[0]
    params: dict[str, Any] = {"embedding": _vector_literal(vector), "limit": limit}
    where = ""
    if kinds:
        allowed = [k for k in kinds if k in {"memo", "postmortem", "charter", "lesson", "board_decision"}]
        if allowed:
            where = "WHERE source_kind = ANY(:kinds)"
            params["kinds"] = allowed
    sql = text(f"""SELECT source_kind, source_id, chunk_index, content_chunk, metadata, (embedding <=> CAST(:embedding AS vector)) AS distance
                   FROM memory_embeddings {where}
                   ORDER BY embedding <=> CAST(:embedding AS vector)
                   LIMIT :limit""")
    with engine.begin() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [{"source_kind": r["source_kind"], "source_id": r["source_id"], "chunk_index": r["chunk_index"], "content_chunk": r["content_chunk"], "distance": float(r["distance"]), "metadata": r["metadata"]} for r in rows]
