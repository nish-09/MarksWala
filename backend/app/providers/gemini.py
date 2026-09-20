"""Gemini implementations of AIProvider, OCRProvider and EmbeddingProvider (V1 default)."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from pathlib import Path
from typing import TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.providers.base import (
    AIAuthError,
    AIMalformedOutput,
    AIQuotaExhausted,
    AIProvider,
    AIRateLimited,
    AITimeout,
    AIUnavailable,
    EmbeddingProvider,
    OCRProvider,
    OcrPageResult,
    ProviderError,
)

log = logging.getLogger("MarksWala.gemini")
T = TypeVar("T", bound=BaseModel)

_client: genai.Client | None = None


def _cache_get(kind: str, parts: list) -> tuple[str | None, "Path | None"]:
    """Opt-in dev/test response cache (AI_RESPONSE_CACHE_DIR). The key covers model, prompt, schema and images,
    so any prompt change misses. Production leaves this unset."""
    if not settings.ai_response_cache_dir:
        return None, None
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else str(part).encode())
        h.update(b"\x00")
    path = Path(settings.ai_response_cache_dir) / f"{kind}-{h.hexdigest()}.json"
    if path.exists():
        return path.read_text(encoding="utf-8"), path
    return None, path


def _cache_put(path: "Path | None", text: str) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise AIAuthError("GEMINI_API_KEY is not configured on the server.")
        _client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=settings.ai_timeout_seconds * 1000),
        )
    return _client


def _translate(e: Exception) -> ProviderError:
    """Map SDK exceptions onto our provider error taxonomy (no vendor detail is shown to users)."""
    if isinstance(e, genai_errors.APIError):
        code = getattr(e, "code", None)
        if code in (401, 403):
            return AIAuthError("The AI provider rejected the server's credentials.")
        if code == 429:
            # the quota id ("...PerDay...") lives in the structured details, not always in the message text
            blob = f"{getattr(e, 'message', '')} {json.dumps(getattr(e, 'details', None), default=str)} {e}"
            if "PerDay" in blob or "per day" in blob.lower():
                return AIQuotaExhausted("The AI provider's daily quota is exhausted.")
            return AIRateLimited("The AI provider is rate limiting requests.")
        if code in (408, 504):
            return AITimeout("The AI provider timed out.")
        if code and code >= 500:
            return AIUnavailable("The AI provider is temporarily unavailable.")
        if code == 400:
            return ProviderError(f"The AI provider rejected the request: {getattr(e, 'message', '')[:200]}")
    name = type(e).__name__.lower()
    if "timeout" in name:
        return AITimeout("The AI provider timed out.")
    if "connect" in name or "network" in name or "remoteprotocol" in name or "readerror" in name:
        return AIUnavailable("Could not reach the AI provider.")
    return AIUnavailable(f"Unexpected AI provider failure ({type(e).__name__}).")


def _with_retries(fn, *, what: str):
    attempts = max(1, settings.ai_max_retries)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ProviderError as e:
            err = e
        except Exception as e:  # SDK / transport
            err = _translate(e)
        if not err.retryable or attempt == attempts:
            raise err
        delay = min(2 ** attempt, 30) + (attempt * 0.3)
        log.warning("%s failed (%s), retry %d/%d in %.1fs", what, err.code, attempt, attempts - 1, delay)
        time.sleep(delay)
    raise AssertionError("unreachable")


def _strip_unsupported(schema: dict) -> dict:
    """Gemini's response_schema accepts a subset of JSON Schema; drop keywords it rejects."""
    banned = {"additionalProperties", "title", "default", "$defs", "examples"}

    def clean(node, is_property_map=False):
        if isinstance(node, dict):
            if is_property_map:  # keys here are field names, not schema keywords: keep them all
                return {k: clean(v) for k, v in node.items()}
            return {k: clean(v, is_property_map=(k == "properties")) for k, v in node.items() if k not in banned}
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(schema)


def _inline_refs(schema: dict) -> dict:
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return resolve(defs[name])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


class GeminiAI(AIProvider):
    name = "gemini"

    def __init__(self, model: str | None = None):
        self.model = model or settings.gemini_text_model

    def generate_structured(self, *, system, prompt, schema: type[T], images=None, temperature=0.0) -> T:
        json_schema = _strip_unsupported(_inline_refs(schema.model_json_schema()))
        parts: list = [types.Part.from_text(text=prompt)]
        for img in images or []:
            parts.insert(0, types.Part.from_bytes(data=img, mime_type="image/png"))
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",
            response_json_schema=json_schema,
        )

        cached, cache_path = _cache_get("ai", [self.model, system, prompt, json.dumps(json_schema, sort_keys=True), temperature, *(images or [])])

        def call() -> T:
            if cached is not None:
                try:
                    return schema.model_validate_json(cached)
                except (ValidationError, json.JSONDecodeError):
                    pass  # a stale/invalid cache entry is ignored
            resp = _get_client().models.generate_content(model=self.model, contents=parts, config=config)
            raw = resp.text
            if raw:
                try:
                    schema.model_validate_json(raw)
                    _cache_put(cache_path, raw)
                except (ValidationError, json.JSONDecodeError):
                    pass
            if not raw:
                reason = getattr(resp.candidates[0], "finish_reason", None) if resp.candidates else None
                raise AIMalformedOutput(f"The model returned no content (finish_reason={reason}).")
            try:
                return schema.model_validate_json(raw)
            except (ValidationError, json.JSONDecodeError) as e:
                raise AIMalformedOutput(f"The model output did not match the required structure: {str(e)[:300]}") from e

        return _with_retries(call, what=f"generate_structured[{schema.__name__}]")


class GeminiOCR(OCRProvider):
    name = "gemini"

    _SYSTEM = (
        "You are a meticulous transcriber of handwritten university exam answer sheets. "
        "Transcribe exactly what the student wrote. NEVER correct spelling, grammar or facts, and NEVER add content. "
        "Keep the student's line breaks. Preserve question labels exactly as written (e.g. 'Q1(a)', '2.b', 'Ans 3'). "
        "Write [illegible] for words you cannot read. Represent a drawn diagram/figure/table as one line "
        "'[DIAGRAM: short neutral description]' at the place it appears. Ignore printed ruling lines and margins."
    )

    def __init__(self, model: str | None = None):
        self.model = model or settings.gemini_vision_model

    def transcribe_page(self, image: bytes, *, page_number: int, mime_type: str = "image/png") -> OcrPageResult:
        prompt = (
            f"This is page {page_number} of a student's answer sheet. Return JSON with: "
            "text (full transcription), confidence (0..1: your honest estimate of how legible the handwriting was and "
            "how sure you are the transcription is faithful; use <0.7 if much was hard to read), "
            "has_diagram (true if any diagram/figure/table appears), unreadable_spans (list of short strings describing "
            "each hard-to-read passage, empty if none), header (student_name and roll_number if written on this page, "
            "otherwise null; roll_number is the exact identifier string, e.g. 'CS2024-001')."
        )
        parts = [types.Part.from_bytes(data=image, mime_type=mime_type), types.Part.from_text(text=prompt)]
        json_schema = _strip_unsupported(_inline_refs(OcrPageResult.model_json_schema()))
        config = types.GenerateContentConfig(
            system_instruction=self._SYSTEM,
            temperature=0.0,
            response_mime_type="application/json",
            response_json_schema=json_schema,
        )

        cached, cache_path = _cache_get("ocr", [self.model, self._SYSTEM, prompt, image])

        def call() -> OcrPageResult:
            if cached is not None:
                try:
                    return OcrPageResult.model_validate_json(cached)
                except (ValidationError, json.JSONDecodeError):
                    pass
            resp = _get_client().models.generate_content(model=self.model, contents=parts, config=config)
            if resp.text:
                try:
                    OcrPageResult.model_validate_json(resp.text)
                    _cache_put(cache_path, resp.text)
                except (ValidationError, json.JSONDecodeError):
                    pass
            if not resp.text:
                raise AIMalformedOutput("OCR returned no content.")
            try:
                return OcrPageResult.model_validate_json(resp.text)
            except (ValidationError, json.JSONDecodeError) as e:
                raise AIMalformedOutput(f"OCR output did not match the required structure: {str(e)[:300]}") from e

        return _with_retries(call, what=f"ocr page {page_number}")


class GeminiEmbedder(EmbeddingProvider):
    name = "gemini"
    BATCH = 50

    def __init__(self, model: str | None = None, dimensions: int | None = None):
        self.model = model or settings.gemini_embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        cfg = types.EmbedContentConfig(task_type=task, output_dimensionality=self.dimensions)

        cached, cache_path = _cache_get("emb", [self.model, task, self.dimensions, *texts])

        def call():
            if cached is not None:
                return json.loads(cached)
            resp = _get_client().models.embed_content(model=self.model, contents=texts, config=cfg)
            vecs = [list(e.values) for e in resp.embeddings]
            if len(vecs) != len(texts) or any(len(v) != self.dimensions for v in vecs):
                raise AIMalformedOutput("Embedding response had an unexpected shape.")
            out = [self._normalize(v) for v in vecs]
            _cache_put(cache_path, json.dumps(out))
            return out

        return _with_retries(call, what="embed")

    @staticmethod
    def _normalize(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            out.extend(self._embed(texts[i : i + self.BATCH], "RETRIEVAL_DOCUMENT"))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]
