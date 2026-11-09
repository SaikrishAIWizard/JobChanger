from app.config import get_settings


def test_llm_models_have_expected_defaults(monkeypatch) -> None:
    for key in (
        "GEMINI_RESUME_MODEL",
        "GEMINI_RESUME_FALLBACK_MODEL",
        "GEMINI_MATCH_MODEL",
        "GEMINI_MATCH_FALLBACK_MODEL",
        "GROQ_EXTRACTION_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = get_settings()

    assert settings.gemini_resume_model == "gemini-3.8-flash"
    assert settings.gemini_resume_fallback_model == "gemini-3.6-flash"
    assert settings.gemini_match_model == "gemini-3.8-flash"
    assert settings.gemini_match_fallback_model == "gemini-3.6-flash"
    assert settings.groq_extraction_model == "llama-3.3-70b-versatile"
    assert settings.llm_timeout_seconds == 45


def test_llm_models_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_RESUME_MODEL", "gemini-resume-test")
    monkeypatch.setenv("GEMINI_RESUME_FALLBACK_MODEL", "gemini-resume-fallback")
    monkeypatch.setenv("GEMINI_MATCH_MODEL", "gemini-match-test")
    monkeypatch.setenv("GEMINI_MATCH_FALLBACK_MODEL", "gemini-match-fallback")
    monkeypatch.setenv("GROQ_EXTRACTION_MODEL", "groq-test")

    settings = get_settings()

    assert settings.gemini_resume_model == "gemini-resume-test"
    assert settings.gemini_resume_fallback_model == "gemini-resume-fallback"
    assert settings.gemini_match_model == "gemini-match-test"
    assert settings.gemini_match_fallback_model == "gemini-match-fallback"
    assert settings.groq_extraction_model == "groq-test"
