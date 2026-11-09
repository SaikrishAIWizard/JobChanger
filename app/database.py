from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings
from app.schemas import JobListing

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class SearchCache(Base):
    __tablename__ = "job_search_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    jobs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


settings = get_settings()
engine = (
    create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=2, max_overflow=1)
    if settings.database_url
    else None
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False) if engine else None


async def initialize_database() -> None:
    if engine is None:
        logger.warning("DATABASE_URL is unset; persistent job caching is disabled.")
        return
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def close_database() -> None:
    if engine:
        await engine.dispose()


def cache_key(
    query: str,
    company: str | None,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> str:
    normalized = (
        f"job-search-v3|{query.casefold().strip()}|{(company or '').casefold().strip()}|"
        f"{(city or '').casefold().strip()}|{(country or '').casefold().strip()}|{limit}"
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def get_cached_jobs(key: str) -> list[JobListing] | None:
    if SessionFactory is None:
        return None
    async with SessionFactory() as session:
        record = await session.get(SearchCache, key)
        if record is None:
            return None
        if record.expires_at <= datetime.now(timezone.utc):
            await session.delete(record)
            await session.commit()
            return None
        return [JobListing.model_validate(item) for item in record.jobs]


async def cache_jobs(key: str, jobs: list[JobListing]) -> None:
    if SessionFactory is None:
        return
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.cache_ttl_seconds)
    async with SessionFactory() as session:
        record = await session.get(SearchCache, key)
        if record is None:
            record = SearchCache(cache_key=key, jobs=[], expires_at=expires_at)
            session.add(record)
        record.jobs = [job.model_dump(mode="json") for job in jobs]
        record.expires_at = expires_at
        await session.commit()


def storage_status() -> str:
    return "configured" if engine else "unconfigured"
