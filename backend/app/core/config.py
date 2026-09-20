"""Application settings, loaded from environment variables / a .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"  # development | test | production
    log_level: str = "INFO"

    # --- infrastructure -------------------------------------------------
    database_url: str = "postgresql+psycopg://marks:marks@127.0.0.1:5432/markswala"
    redis_url: str = "redis://127.0.0.1:6379/0"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "markswala_course_chunks"

    # --- AI provider (Gemini is the V1 implementation) -----------------------
    ai_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_text_model: str = "gemini-3.5-flash"
    gemini_vision_model: str = "gemini-3.5-flash"
    gemini_eval_model: str | None = None  # model used for grading + feedback; defaults to gemini_text_model
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    ai_timeout_seconds: int = 120
    ai_max_retries: int = 4
    # dev/test only: replay recorded provider responses from this directory (leave unset in production)
    ai_response_cache_dir: str | None = None

    # --- storage --------------------------------------------------------
    storage_backend: str = "local"
    storage_local_root: str = str(BACKEND_DIR / "storage")
    max_resource_upload_mb: int = 50
    max_question_paper_upload_mb: int = 20
    max_answer_sheet_upload_mb: int = 100
    max_answer_sheet_pages: int = 60

    # --- sessions / security -------------------------------------------------
    secret_key: str = "dev-only-insecure-secret-change-me"
    session_cookie_name: str = "markswala_session"
    session_ttl_hours: int = 12
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cors_origins: str = "http://localhost:3000"
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300
    allow_registration: bool = True

    # --- grading policy (defaults; exams may override) ---------------------
    confidence_high_threshold: float = Field(default=0.85, ge=0, le=1)
    confidence_review_threshold: float = Field(default=0.70, ge=0, le=1)
    default_pass_percentage: float = Field(default=40.0, ge=0, le=100)
    rag_top_k: int = 5
    rag_min_score: float = 0.35

    # --- workers ---------------------------------------------------------
    job_max_attempts: int = 3
    stale_job_minutes: int = 30

    @field_validator("cookie_samesite")
    @classmethod
    def _samesite(cls, v: str) -> str:
        v = v.lower()
        if v not in {"lax", "strict", "none"}:
            raise ValueError("cookie_samesite must be lax, strict or none")
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.app_env == "production":
            if self.secret_key.startswith("dev-only") or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be set to a random value of 32+ chars in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
        if self.confidence_review_threshold > self.confidence_high_threshold:
            raise ValueError("confidence_review_threshold must be <= confidence_high_threshold")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
