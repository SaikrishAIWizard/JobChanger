import asyncio
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock

from app import job_sources
from app.job_sources import (
    COMPANY_PORTALS,
    _normalize_company,
    _parse_infosys_jobs,
    _parse_google_markdown,
    _parse_linked_job_markdown,
    _parse_wipro_rss_item,
    _portal_search_url,
    _strip_html,
    _wipro_search_terms,
    filter_jobs_by_location,
    is_supported_company,
    rank_jobs_for_profile,
    search_terms,
)
from app.schemas import CandidateProfile, JobListing


def test_parse_google_markdown_extracts_job_details() -> None:
    markdown = """
### Software Engineer, Cloud
Google | Bengaluru, India

Build reliable distributed services.

Learn more[](https://www.google.com/about/careers/applications/jobs/results/123-cloud?q=engineer)
"""
    jobs = _parse_google_markdown(markdown, 5)
    assert len(jobs) == 1
    assert jobs[0].external_id == "123"
    assert jobs[0].company == "Google"
    assert jobs[0].location == "Bengaluru, India"


def test_linked_markdown_parser_ignores_non_job_headings() -> None:
    markdown = """
## Search Jobs
## Refine by
[Unrelated job](https://jobs.apple.com/en-us/details/111/wrong-section)
## Search Results
*   ### [Software Engineer](https://jobs.apple.com/en-us/details/123/software-engineer?team=SW)
    *   [See full role description](https://jobs.apple.com/en-us/details/123/software-engineer?team=SW)
## Software Engineer
Location: Cupertino
[Details](https://jobs.apple.com/en-us/details/123/software-engineer)
"""
    jobs = _parse_linked_job_markdown(markdown, "Apple", "https://jobs.apple.com", 5)
    assert len(jobs) == 1
    assert jobs[0].title == "Software Engineer"
    assert jobs[0].external_id == "123"


def test_strip_html_decodes_entities_and_breaks() -> None:
    assert _strip_html("A&amp;B<br/>C") == "A&B C"


def test_company_aliases_map_to_supported_career_portals() -> None:
    assert _normalize_company("TCS") == "tata consultancy services"
    assert _normalize_company("Cognizant") == "cognizant"
    assert _normalize_company("Accenture") == "accenture"
    assert _normalize_company("Capgemini") == "capgemini"
    assert COMPANY_PORTALS["tata consultancy services"][0] == "Tata Consultancy Services"
    assert is_supported_company("Tech-Mahindra")
    assert is_supported_company("HCL Technologies")


def test_company_portals_use_current_job_search_pages() -> None:
    assert COMPANY_PORTALS["accenture"][1] == (
        "https://www.accenture.com/us-en/careers/jobsearch"
    )
    assert COMPANY_PORTALS["capgemini"][1] == (
        "https://www.capgemini.com/careers/job-search/"
    )
    assert COMPANY_PORTALS["ltimindtree"][1] == "https://careers.ltm.com/search"
    assert COMPANY_PORTALS["tech mahindra"][1] == "https://careers.techmahindra.com/"
    assert COMPANY_PORTALS["ibm"][1] == "https://www.ibm.com/careers/search"
    assert COMPANY_PORTALS["meta"][1] == "https://www.metacareers.com/jobsearch/"


def test_discover_all_company_jobs_searches_every_portal_and_limits_candidates(
    monkeypatch,
) -> None:
    calls: list[tuple[str, int, str | None, str | None]] = []
    failed_company = job_sources.ALL_COMPANY_NAMES[0]

    async def discover(company, profile, limit, city, country):
        calls.append((company, limit, city, country))
        if company == failed_company:
            raise RuntimeError("source unavailable")
        return [
            JobListing(
                external_id=company,
                title="Data Engineer",
                company=company,
                location="Bengaluru, India",
                url=f"https://example.com/{company}",
                source=company.casefold(),
            )
        ]

    monkeypatch.setattr(job_sources, "discover_company_jobs", discover)
    profile = CandidateProfile(
        full_name="Candidate",
        years_of_experience=4,
        primary_roles=["Data Engineer"],
        technical_skills=["Python"],
        domain_knowledge=["Data Platforms"],
        seniority_level="Mid",
    )

    jobs, failed_sources = asyncio.run(
        job_sources.discover_all_company_jobs(
            profile, 2, city="Bengaluru", country="India"
        )
    )

    assert {company for company, _, _, _ in calls} == set(job_sources.ALL_COMPANY_NAMES)
    assert all(limit == 5 for _, limit, _, _ in calls)
    assert all(city == "Bengaluru" and country == "India" for _, _, city, country in calls)
    assert len(jobs) == 10
    assert failed_sources == [failed_company]


def test_discover_all_company_jobs_reports_when_every_source_fails(monkeypatch) -> None:
    async def fail(company, profile, limit, city, country):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(job_sources, "discover_company_jobs", fail)
    profile = CandidateProfile(
        full_name="Candidate",
        years_of_experience=4,
        primary_roles=["Data Engineer"],
        technical_skills=["Python"],
        domain_knowledge=["Data Platforms"],
        seniority_level="Mid",
    )

    try:
        asyncio.run(job_sources.discover_all_company_jobs(profile, 10))
    except RuntimeError as exc:
        assert "No configured employer portal returned job listings" in str(exc)
        assert all(company in str(exc) for company in job_sources.ALL_COMPANY_NAMES)
    else:
        raise AssertionError("Expected an error when every configured source fails.")


def test_company_portal_pages_omit_unsupported_query_parameter(monkeypatch) -> None:
    import app.llm

    requested_urls: list[str] = []

    async def fetch(url: str) -> str:
        requested_urls.append(url)
        return "page"

    async def extract(markdown: str, company: str, source_url: str, limit: int):
        return []

    monkeypatch.setattr(job_sources, "_fetch_jina", fetch)
    monkeypatch.setattr(app.llm, "extract_jobs_with_groq", extract)

    async def run() -> None:
        for key in ("capgemini", "tech mahindra"):
            display_name, portal_url = COMPANY_PORTALS[key]
            await job_sources.search_company_portal(
                display_name, portal_url, "software engineer", 3
            )

    asyncio.run(run())

    assert requested_urls == [
        COMPANY_PORTALS["capgemini"][1],
        COMPANY_PORTALS["tech mahindra"][1],
    ]


def test_portal_search_url_includes_encoded_city_and_country() -> None:
    url = _portal_search_url(
        "https://careers.example.com/jobs",
        "Data Engineer",
        "New York",
        "United States",
    )
    assert url == (
        "https://careers.example.com/jobs?q=Data+Engineer"
        "&location=New+York%2C+United+States"
    )


def test_location_filter_keeps_matching_or_remote_jobs() -> None:
    jobs = [
        JobListing(
            external_id="city",
            title="Engineer",
            company="Example",
            location="Bengaluru, India",
            url="https://example.com/city",
            source="example",
        ),
        JobListing(
            external_id="remote",
            title="Remote engineer",
            company="Example",
            location="Remote",
            url="https://example.com/remote",
            source="example",
        ),
        JobListing(
            external_id="remote-us",
            title="Remote engineer, US",
            company="Example",
            location="Remote - United States",
            url="https://example.com/remote-us",
            source="example",
        ),
        JobListing(
            external_id="other",
            title="Engineer",
            company="Example",
            location="London, United Kingdom",
            url="https://example.com/other",
            source="example",
        ),
        JobListing(
            external_id="unknown",
            title="Engineer",
            company="Example",
            location="Not specified",
            url="https://example.com/unknown",
            source="example",
        ),
    ]

    filtered = filter_jobs_by_location(jobs, "Bengaluru", "India")

    assert [job.external_id for job in filtered] == ["city", "remote"]
    us_filtered = filter_jobs_by_location(jobs, None, "United States")
    assert [job.external_id for job in us_filtered] == ["remote", "remote-us"]


def test_infosys_parser_extracts_real_reference_and_job_detail_url() -> None:
    jobs = _parse_infosys_jobs(
        [
            {
                "postingTitle": "GenAI Developer",
                "location": "BANGALORE",
                "country": "India",
                "postingId": 254165,
                "requisitionId": 256138,
                "referenceCode": "INFSYS-EXTERNAL-256138",
                "sourceId": 1,
                "company": "Infosys Limited",
                "minExperienceLevel": 3,
                "maxExperienceLevel": 5,
                "postingDescription": "Design and deploy GenAI applications.",
                "technicalRequirement": "Python, RAG, Prompt Engineering.",
                "rolesResponsibilities": "Build enterprise AI solutions.",
            },
            {"postingTitle": "No reference", "location": "BANGALORE"},
        ]
    )

    assert len(jobs) == 1
    assert jobs[0].title == "GenAI Developer"
    assert jobs[0].external_id == "INFSYS-EXTERNAL-256138"
    assert jobs[0].location == "BANGALORE, India"
    assert jobs[0].source == "infosys"
    assert jobs[0].url == (
        "https://career.infosys.com/jobdesc?"
        "jobReferenceCode=INFSYS-EXTERNAL-256138&sourceId=1"
    )
    assert "Python, RAG, Prompt Engineering." in jobs[0].description
    assert "Required experience: 3 to 5 years." in jobs[0].description


def test_profile_role_match_filters_unrelated_infosys_hot_jobs() -> None:
    profile = CandidateProfile(
        full_name="Candidate",
        years_of_experience=4,
        primary_roles=["GenAI Engineer", "Generative AI Engineer"],
        technical_skills=["RAG", "LLMs", "Python"],
        domain_knowledge=["Generative AI"],
        seniority_level="Mid",
    )
    jobs = [
        JobListing(
            external_id="privacy",
            title="Senior Advisor - Privacy & Data Protection",
            company="Infosys",
            location="Bangalore, India",
            url="https://career.infosys.com/joblist",
            description="Privacy policies and data protection controls.",
            source="infosys",
        ),
        JobListing(
            external_id="genai",
            title="GenAI Developer",
            company="Infosys",
            location="Bangalore, India",
            url="https://career.infosys.com/joblist",
            description="Build generative AI solutions with RAG and Python.",
            source="infosys",
        ),
    ]

    ranked = rank_jobs_for_profile(jobs, profile)

    assert [job.external_id for job in ranked] == ["genai"]


def test_search_query_prioritizes_role_and_avoids_redundant_resume_terms() -> None:
    profile = CandidateProfile(
        full_name="Candidate",
        years_of_experience=4,
        primary_roles=["GenAI Engineer", "Generative AI Engineer"],
        technical_skills=["Generative AI", "LLMs", "Prompt Engineering", "RAG"],
        domain_knowledge=["Generative AI"],
        seniority_level="Mid",
    )

    assert search_terms(profile) == "GenAI Engineer LLMs Prompt Engineering RAG"


def test_infosys_search_returns_role_aligned_results(monkeypatch) -> None:
    jobs = [
        JobListing(
            external_id="INFSYS-EXTERNAL-1",
            title="Industry Principal GCC",
            company="Infosys",
            location="HYDERABAD, India",
            url="https://career.infosys.com/jobdesc?jobReferenceCode=1&sourceId=1",
            description="Lead cloud platform strategy.",
            source="infosys",
        ),
        JobListing(
            external_id="INFSYS-EXTERNAL-2",
            title="GenAI Developer",
            company="Infosys",
            location="BANGALORE, India",
            url="https://career.infosys.com/jobdesc?jobReferenceCode=2&sourceId=1",
            description="Build generative AI solutions with RAG and Python.",
            source="infosys",
        ),
    ]
    search = AsyncMock(return_value=jobs)
    monkeypatch.setattr(job_sources, "_search_infosys_jobs", search)
    profile = CandidateProfile(
        full_name="Candidate",
        years_of_experience=4,
        primary_roles=["GenAI Engineer"],
        technical_skills=["RAG", "LLMs"],
        domain_knowledge=["Generative AI"],
        seniority_level="Mid",
    )

    jobs = asyncio.run(
        job_sources.discover_company_jobs(
            "Infosys", profile, 10, country="India"
        )
    )

    assert [job.title for job in jobs] == ["GenAI Developer"]
    search.assert_awaited_once_with(
        "GenAI Engineer RAG LLMs", None, "India"
    )


def test_wipro_search_terms_follow_profile_roles_and_skills() -> None:
    genai_profile = CandidateProfile(
        full_name="Candidate",
        years_of_experience=4,
        primary_roles=["GenAI Engineer", "Generative AI Engineer"],
        technical_skills=["Python", "RAG", "LLMs"],
        domain_knowledge=["Generative AI"],
        seniority_level="Mid",
    )
    data_profile = CandidateProfile(
        full_name="Candidate",
        years_of_experience=4,
        primary_roles=["Data Engineer", "Analytics Engineer"],
        technical_skills=["SQL", "Spark", "Airflow", "Python"],
        domain_knowledge=["Data Platforms"],
        seniority_level="Mid",
    )

    assert _wipro_search_terms(genai_profile) == [
        "GenAI Engineer",
        "Python",
        "RAG",
        "LLMs",
    ]
    assert _wipro_search_terms(data_profile) == [
        "Data Engineer",
        "Analytics Engineer",
        "SQL",
        "Spark",
    ]
    assert all("genai" not in term.casefold() for term in _wipro_search_terms(data_profile))


def test_wipro_rss_parser_extracts_posting_id_direct_url_and_location() -> None:
    item = ET.fromstring(
        """<item>
          <title>Gen AI Innovation Developer (Mumbai, IND-27, IN, 400708)</title>
          <link>https://careers.wipro.com/job/Mumbai-Gen-AI-Innovation-Developer-IND-400708/1352246255/?feedId=null&amp;utm_source=J2WRSS</link>
          <description><![CDATA[<p>Build GenAI solutions with Python &amp; RAG.</p>]]></description>
        </item>"""
    )

    job = _parse_wipro_rss_item(item)

    assert job is not None
    assert job.title == "Gen AI Innovation Developer"
    assert job.external_id == "1352246255"
    assert job.location == "Mumbai, India"
    assert job.url == (
        "https://careers.wipro.com/job/"
        "Mumbai-Gen-AI-Innovation-Developer-IND-400708/1352246255/"
    )
    assert "Python & RAG." in job.description
