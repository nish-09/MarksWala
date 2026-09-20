"""RAG retrieval over a course's processed materials, with provenance."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Resource, ResourceChunk
from app.providers.base import EmbeddingProvider, VectorStore


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    resource_id: uuid.UUID
    resource_title: str
    page_number: int | None
    slide_number: int | None
    section: str | None
    score: float
    text: str

    @property
    def locator(self) -> str:
        if self.slide_number:
            return f"slide {self.slide_number}"
        if self.page_number:
            return f"p. {self.page_number}"
        return "-"


def retrieve(
    db: Session,
    *,
    course_id: uuid.UUID,
    query: str,
    embedder: EmbeddingProvider,
    store: VectorStore,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    """Vector search (course-scoped) then hydrate from PostgreSQL, dropping chunks whose resource was deleted."""
    top_k = top_k or settings.rag_top_k
    min_score = settings.rag_min_score if min_score is None else min_score
    if not query.strip():
        return []
    hits = store.search(embedder.embed_query(query[:6000]), course_id=course_id, top_k=top_k, min_score=min_score)
    if not hits:
        return []
    rows = db.execute(
        select(ResourceChunk, Resource.title)
        .join(Resource, Resource.id == ResourceChunk.resource_id)
        .where(ResourceChunk.id.in_([h.id for h in hits]), Resource.deleted_at.is_(None))
    ).all()
    by_id = {c.id: (c, title) for c, title in rows}
    out: list[RetrievedChunk] = []
    for h in hits:
        if h.id in by_id:
            c, title = by_id[h.id]
            out.append(
                RetrievedChunk(c.id, c.resource_id, title, c.page_number, c.slide_number, c.section, h.score, c.text)
            )
    return out


def retrieval_confidence(chunks: list[RetrievedChunk]) -> float:
    """0..1 heuristic: how strongly the best passages match, tempered by how many independent passages agree.
    This is a relevance signal, not a probability of correctness."""
    if not chunks:
        return 0.0
    top = chunks[0].score
    supporting = sum(1 for c in chunks[:3] if c.score >= settings.rag_min_score + 0.1)
    base = min(1.0, max(0.0, (top - 0.35) / 0.4))  # cosine 0.35 -> 0, 0.75 -> 1
    return round(min(1.0, base * (0.8 + 0.1 * supporting)), 3)
