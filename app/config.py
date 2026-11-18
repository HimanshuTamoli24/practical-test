"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Values are read once at import time."""

    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", "").strip())
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    groq_fallback_model: str = field(
        default_factory=lambda: os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")
    )
    groq_base_url: str = field(
        default_factory=lambda: os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    )

    request_timeout: float = field(default_factory=lambda: _env_float("REQUEST_TIMEOUT", 30.0))
    llm_timeout: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT", 60.0))
    max_page_bytes: int = field(default_factory=lambda: _env_int("MAX_PAGE_BYTES", 2_000_000))
    max_content_chars: int = field(default_factory=lambda: _env_int("MAX_CONTENT_CHARS", 12_000))

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
