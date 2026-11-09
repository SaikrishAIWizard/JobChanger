from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    discord_token: str | None
    database_url: str | None
    gemini_api_key: str | None
    gemini_resume_model: str
    gemini_resume_fallback_model: str | None
    gemini_match_model: str
    gemini_match_fallback_model: str | None
    groq_api_key: str | None
    groq_extraction_model: str
    llm_timeout_seconds: int
    host: str
    port: int
    cache_ttl_seconds: int
    max_attachment_bytes: int


def get_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL")
    if database_url and database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url and database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return Settings(
        discord_token=os.getenv("DISCORD_TOKEN"),
        database_url=database_url,
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_resume_model=os.getenv("GEMINI_RESUME_MODEL", "gemini-3.8-flash").strip()
        or "gemini-3.8-flash",
        gemini_resume_fallback_model=os.getenv(
            "GEMINI_RESUME_FALLBACK_MODEL", "gemini-3.6-flash"
        ).strip()
        or None,
        gemini_match_model=os.getenv("GEMINI_MATCH_MODEL", "gemini-3.8-flash").strip()
        or "gemini-3.8-flash",
        gemini_match_fallback_model=os.getenv(
            "GEMINI_MATCH_FALLBACK_MODEL", "gemini-3.6-flash"
        ).strip()
        or None,
        groq_api_key=os.getenv("GROQ_API_KEY"),
        groq_extraction_model=os.getenv(
            "GROQ_EXTRACTION_MODEL", "llama-3.3-70b-versatile"
        ).strip()
        or "llama-3.3-70b-versatile",
        llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "45")),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "10000")),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "900")),
        max_attachment_bytes=int(os.getenv("MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024))),
    )
