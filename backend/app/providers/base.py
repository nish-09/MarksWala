"""Provider interfaces. Application code depends on these, never on a vendor SDK."""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


# ---- errors ---------------------------------------------------------------------------------
class ProviderError(RuntimeError):
    """Base for provider failures. `retryable` says whether the same request may succeed later."""

    retryable = False
    code = "provider_error"


class AIRateLimited(ProviderError):
    retryable = True
    code = "ai_rate_limited"


class AIQuotaExhausted(ProviderError):
    """A per-day/billing quota is used up: retrying now cannot help."""

    retryable = False
    code = "ai_quota_exhausted"


class AITimeout(ProviderError):
    retryable = True
    code = "ai_timeout"


class AIUnavailable(ProviderError):
    retryable = True
    code = "ai_unavailable"


class AIAuthError(ProviderError):
    code = "ai_auth_error"


class AIMalformedOutput(ProviderError):
    """The model answered, but not in the required structure."""

    retryable = True
    code = "ai_malformed_output"


class VectorStoreError(ProviderError):
    retryable = True
    code = "vector_store_unavailable"


# ---- AI (text / multimodal, structured output) --------------------------------------------------
class AIProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        images: list[bytes] | None = None,
        temperature: float = 0.0,
    ) -> T:
        """Return `schema` validated from the model output. Raises ProviderError subclasses."""


# ---- OCR ---------------------------------------------------------------------------------------
class OcrHeader(BaseModel):
    student_name: str | None = None
    roll_number: str | None = None


class OcrPageResult(BaseModel):
    text: str
    confidence: float = Field(ge=0, le=1)
    has_diagram: bool = False
    unreadable_spans: list[str] = Field(default_factory=list)
    header: OcrHeader | None = None


class OCRProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def transcribe_page(self, image: bytes, *, page_number: int, mime_type: str = "image/png") -> OcrPageResult:
        """Faithfully transcribe one handwritten page. Must not correct or paraphrase the writing."""


# ---- embeddings + vector store -------------------------------------------------------------------
class EmbeddingProvider(ABC):
    name: str
    model: str
    dimensions: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...


@dataclass
class VectorPoint:
    id: uuid.UUID
    vector: list[float]
    payload: dict = field(default_factory=dict)


@dataclass
class VectorHit:
    id: uuid.UUID
    score: float
    payload: dict


class VectorStore(ABC):
    @abstractmethod
    def ping(self) -> None: ...

    @abstractmethod
    def ensure_collection(self, dimensions: int) -> None: ...

    @abstractmethod
    def upsert(self, points: list[VectorPoint]) -> None: ...

    @abstractmethod
    def search(
        self, vector: list[float], *, course_id: uuid.UUID, top_k: int, min_score: float = 0.0
    ) -> list[VectorHit]: ...

    @abstractmethod
    def delete_resource(self, resource_id: uuid.UUID) -> None: ...

    @abstractmethod
    def count_resource(self, resource_id: uuid.UUID) -> int: ...
