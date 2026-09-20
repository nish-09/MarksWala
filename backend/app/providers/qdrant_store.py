"""Qdrant implementation of VectorStore. Every query is scoped to a course_id (tenant isolation)."""
from __future__ import annotations

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.core.config import settings
from app.providers.base import VectorHit, VectorPoint, VectorStore, VectorStoreError

log = logging.getLogger("MarksWala.qdrant")


class QdrantStore(VectorStore):
    def __init__(self, url: str | None = None, collection: str | None = None):
        self.collection = collection or settings.qdrant_collection
        self.client = QdrantClient(url=url or settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=30)
        self._ready_dims: int | None = None

    def _wrap(self, fn):
        try:
            return fn()
        except VectorStoreError:
            raise
        except Exception as e:
            log.error("qdrant call failed: %s", e)
            raise VectorStoreError(f"The vector database is unavailable ({type(e).__name__}).") from e

    def ping(self) -> None:
        self._wrap(lambda: self.client.get_collections())

    def ensure_collection(self, dimensions: int) -> None:
        if self._ready_dims == dimensions:
            return

        def go():
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(
                    self.collection,
                    vectors_config=qm.VectorParams(size=dimensions, distance=qm.Distance.COSINE),
                )
                for field in ("course_id", "resource_id"):
                    self.client.create_payload_index(self.collection, field, qm.PayloadSchemaType.KEYWORD)
            else:
                info = self.client.get_collection(self.collection)
                size = info.config.params.vectors.size  # type: ignore[union-attr]
                if size != dimensions:
                    raise VectorStoreError(
                        f"Collection '{self.collection}' has dimension {size} but embeddings are {dimensions}. "
                        "Re-process resources with a fresh collection."
                    )
            self._ready_dims = dimensions

        self._wrap(go)

    def upsert(self, points: list[VectorPoint]) -> None:
        if not points:
            return
        structs = [qm.PointStruct(id=str(p.id), vector=p.vector, payload=p.payload) for p in points]
        for i in range(0, len(structs), 64):
            self._wrap(lambda batch=structs[i : i + 64]: self.client.upsert(self.collection, points=batch, wait=True))

    def search(self, vector: list[float], *, course_id: uuid.UUID, top_k: int, min_score: float = 0.0) -> list[VectorHit]:
        flt = qm.Filter(must=[qm.FieldCondition(key="course_id", match=qm.MatchValue(value=str(course_id)))])
        res = self._wrap(
            lambda: self.client.query_points(
                self.collection, query=vector, query_filter=flt, limit=top_k, score_threshold=min_score, with_payload=True
            )
        )
        return [VectorHit(id=uuid.UUID(str(h.id)), score=float(h.score), payload=dict(h.payload or {})) for h in res.points]

    def delete_resource(self, resource_id: uuid.UUID) -> None:
        flt = qm.Filter(must=[qm.FieldCondition(key="resource_id", match=qm.MatchValue(value=str(resource_id)))])
        if not self._wrap(lambda: self.client.collection_exists(self.collection)):
            return
        self._wrap(lambda: self.client.delete(self.collection, points_selector=qm.FilterSelector(filter=flt), wait=True))

    def count_resource(self, resource_id: uuid.UUID) -> int:
        flt = qm.Filter(must=[qm.FieldCondition(key="resource_id", match=qm.MatchValue(value=str(resource_id)))])
        if not self._wrap(lambda: self.client.collection_exists(self.collection)):
            return 0
        return self._wrap(lambda: self.client.count(self.collection, count_filter=flt, exact=True).count)
