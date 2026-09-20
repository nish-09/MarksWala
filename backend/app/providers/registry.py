"""Provider registry: the only place that names concrete implementations."""
from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.providers.base import AIProvider, EmbeddingProvider, OCRProvider, VectorStore


@lru_cache
def get_vector_store() -> VectorStore:
    from app.providers.qdrant_store import QdrantStore

    return QdrantStore()


@lru_cache
def get_ai() -> AIProvider:
    if settings.ai_provider == "gemini":
        from app.providers.gemini import GeminiAI

        return GeminiAI()
    raise RuntimeError(f"unknown AI provider: {settings.ai_provider}")


@lru_cache
def get_grader() -> AIProvider:
    """The model that marks answers and writes feedback (may differ from the parsing/segmentation model)."""
    if settings.ai_provider == "gemini":
        from app.providers.gemini import GeminiAI

        return GeminiAI(settings.gemini_eval_model or settings.gemini_text_model)
    raise RuntimeError(f"unknown AI provider: {settings.ai_provider}")


@lru_cache
def get_ocr() -> OCRProvider:
    if settings.ai_provider == "gemini":
        from app.providers.gemini import GeminiOCR

        return GeminiOCR()
    raise RuntimeError(f"unknown OCR provider: {settings.ai_provider}")


@lru_cache
def get_embedder() -> EmbeddingProvider:
    if settings.ai_provider == "gemini":
        from app.providers.gemini import GeminiEmbedder

        return GeminiEmbedder()
    raise RuntimeError(f"unknown embedding provider: {settings.ai_provider}")
