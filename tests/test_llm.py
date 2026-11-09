import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import httpx
import pytest
from google.genai.errors import ServerError
from groq import RateLimitError

from app import llm
from app.llm import (
    _create_gemini_client,
    _generate_content,
    _is_verified_job_detail,
    _validate_job_matches,
)
from app.schemas import CandidateProfile, JobListing, JobMatchResult, JobMatchScore


def test_gemini_client_uses_bounded_timeout_without_sdk_retries() -> None:
    client = _create_gemini_client("test-key", timeout_seconds=23)
    try:
        options = client._api_client._http_options
        assert options.timeout == 23_000
        assert options.retry_options.attempts == 1
    finally:
        asyncio.run(client.aio.aclose())


def test_gemini_generation_retries_once_with_configured_fallback() -> None:
    response = object()
    generate_content = AsyncMock(side_effect=[ServerError(503, {}), response])
    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )

    result = asyncio.run(
        _generate_content(client, "primary", "fallback", contents="prompt")
    )

    assert result is response
    assert generate_content.await_args_list == [
        call(model="primary", contents="prompt"),
        call(model="fallback", contents="prompt"),
    ]


def test_gemini_generation_retries_fallback_on_504() -> None:
    response = object()
    generate_content = AsyncMock(side_effect=[ServerError(504, {}), response])
    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )

    result = asyncio.run(
        _generate_content(client, "primary", "fallback", contents="prompt")
    )

    assert result is response
    assert generate_content.await_args_list == [
        call(model="primary", contents="prompt"),
        call(model="fallback", contents="prompt"),
    ]


def test_gemini_generation_tries_fallback_for_unknown_model_404() -> None:
    response = object()
    generate_content = AsyncMock(side_effect=[ServerError(404, {}), response])
    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )

    result = asyncio.run(
        _generate_content(client, "unknown-model", "working-model", contents="prompt")
    )

    assert result is response
    assert generate_content.await_args_list == [
        call(model="unknown-model", contents="prompt"),
        call(model="working-model", contents="prompt"),
    ]


def test_gemini_generation_does_not_retry_without_fallback() -> None:
    error = ServerError(503, {})
    generate_content = AsyncMock(side_effect=error)
    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )

    with pytest.raises(ServerError):
        asyncio.run(_generate_content(client, "primary", None, contents="prompt"))

    generate_content.assert_awaited_once_with(model="primary", contents="prompt")


def test_job_match_validation_preserves_original_listing() -> None:
    job = JobListing(
        external_id="123",
        title="Backend Engineer",
        company="Example",
        url="https://example.com/jobs/123",
        source="example",
    )
    result = JobMatchResult(
        results=[
            JobMatchScore(
                external_id="123",
                source="example",
                match_score=90,
                rationale="The role matches the candidate's backend experience. The listed skills align with their profile.",
            )
        ]
    )

    assert _validate_job_matches(result, [job]) == [
        (
            job,
            90,
            "The role matches the candidate's backend experience. The listed skills align with their profile.",
        )
    ]


def test_extracted_jobs_must_have_a_real_job_detail_url_and_source_title() -> None:
    generic_page_job = JobListing(
        external_id="1",
        title="GenAI Developer",
        company="Example",
        url="https://example.com/jobs?q=genai",
        source="example",
    )
    actual_job = JobListing(
        external_id="2",
        title="GenAI Developer",
        company="Example",
        url="https://example.com/jobs/2",
        source="example",
    )
    urls = {
        "https://example.com/jobs?q=genai",
        "https://example.com/jobs/2",
    }
    normalized = "genai developer https example com jobs 2"

    assert not _is_verified_job_detail(
        generic_page_job, urls, "https://example.com/jobs?q=genai", normalized
    )
    assert _is_verified_job_detail(
        actual_job, urls, "https://example.com/jobs?q=genai", normalized
    )


def test_groq_job_extraction_retries_rate_limits_with_exponential_backoff(monkeypatch) -> None:
    settings = SimpleNamespace(
        groq_api_key="groq-key",
        groq_extraction_model="groq-model",
        llm_timeout_seconds=45,
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"jobs": []}'))]
    )
    create = AsyncMock(
        side_effect=[
            *(
                RateLimitError(
                    "rate limited",
                    response=httpx.Response(
                        429, request=httpx.Request("POST", "https://api.groq.com")
                    ),
                    body=None,
                )
                for _ in range(3)
            ),
            response,
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    class ClientContext:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *_):
            return None

    sleep = AsyncMock()
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "_create_groq_client", lambda _: ClientContext())
    monkeypatch.setattr(llm.asyncio, "sleep", sleep)

    jobs = asyncio.run(
        llm.extract_jobs_with_groq("page content", "Example", "https://example.com", 5)
    )

    assert jobs == []
    assert create.await_count == 4
    assert sleep.await_args_list == [call(2), call(4), call(8)]


def test_resume_parsing_uses_groq_after_both_gemini_models_return_503(monkeypatch) -> None:
    settings = SimpleNamespace(
        gemini_api_key="gemini-key",
        gemini_resume_model="primary",
        gemini_resume_fallback_model="fallback",
        groq_api_key="groq-key",
        groq_extraction_model="groq-model",
        llm_timeout_seconds=45,
    )
    generate_content = AsyncMock(side_effect=[ServerError(503, {}), ServerError(503, {})])
    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content),
            aclose=AsyncMock(),
        )
    )
    profile = CandidateProfile(
        full_name="Alex Example",
        years_of_experience=4,
        primary_roles=["Software Engineer"],
        technical_skills=["Python"],
        domain_knowledge=["AI"],
        seniority_level="Senior",
    )
    groq_parse = AsyncMock(return_value=profile)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "_create_gemini_client", lambda *_: client)
    monkeypatch.setattr(llm, "_parse_candidate_profile_with_groq", groq_parse)

    result = asyncio.run(llm.parse_candidate_profile("resume text"))

    assert result == profile
    groq_parse.assert_awaited_once_with("resume text", settings)
    client.aio.aclose.assert_awaited_once()


def test_job_matching_uses_groq_after_both_gemini_models_return_503(monkeypatch) -> None:
    settings = SimpleNamespace(
        gemini_api_key="gemini-key",
        gemini_match_model="primary",
        gemini_match_fallback_model="fallback",
        groq_api_key="groq-key",
        groq_extraction_model="groq-model",
        llm_timeout_seconds=45,
    )
    generate_content = AsyncMock(side_effect=[ServerError(503, {}), ServerError(503, {})])
    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content),
            aclose=AsyncMock(),
        )
    )
    profile = CandidateProfile(
        full_name="Alex Example",
        years_of_experience=4,
        primary_roles=["Software Engineer"],
        technical_skills=["Python"],
        domain_knowledge=["AI"],
        seniority_level="Senior",
    )
    job = JobListing(
        external_id="123",
        title="Backend Engineer",
        company="Example",
        url="https://example.com/jobs/123",
        source="example",
    )
    expected = [(job, 90, "The role matches the candidate. Their skills are relevant.")]
    groq_match = AsyncMock(return_value=expected)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "_create_gemini_client", lambda *_: client)
    monkeypatch.setattr(llm, "_match_jobs_with_groq", groq_match)

    result = asyncio.run(llm.match_jobs(profile, [job]))

    assert result == expected
    groq_match.assert_awaited_once_with(profile, [job], settings)
    client.aio.aclose.assert_awaited_once()
