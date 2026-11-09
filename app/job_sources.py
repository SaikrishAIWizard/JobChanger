from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urljoin
from urllib.parse import parse_qsl, urlencode as encode_query, urlsplit, urlunsplit

import httpx

from app.schemas import CandidateProfile, JobListing

logger = logging.getLogger(__name__)

USER_AGENT = "JobSearchBot/1.0 (+https://github.com/job-search-bot)"
AMAZON_SEARCH_URL = "https://www.amazon.jobs/en/search.json"
GOOGLE_SEARCH_URL = "https://careers.google.com/jobs/results/"
APPLE_SEARCH_URL = "https://jobs.apple.com/en-us/search"
JINA_URL = "https://r.jina.ai/"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
INFOSYS_SEARCH_API_URL = (
    "https://intapgateway.infosysapps.com/careersci/search/intapjbsrch/"
    "getCareerSearchJobs"
)
INFOSYS_JOB_DETAIL_URL = "https://career.infosys.com/jobdesc"
INFOSYS_SOURCE_IDS = "1,21"
MAX_INFOSYS_RESPONSE_BYTES = 12 * 1024 * 1024
WIPRO_RSS_URL = "https://careers.wipro.com/services/rss/job/"
MAX_WIPRO_FEED_BYTES = 2 * 1024 * 1024
WIPRO_COUNTRIES = {
    "AU": "Australia",
    "BR": "Brazil",
    "CA": "Canada",
    "DE": "Germany",
    "FR": "France",
    "GB": "United Kingdom",
    "IN": "India",
    "JP": "Japan",
    "MX": "Mexico",
    "PH": "Philippines",
    "PL": "Poland",
    "RO": "Romania",
    "SG": "Singapore",
    "US": "United States",
}

COMPANY_PORTALS = {
    "tata consultancy services": ("Tata Consultancy Services", "https://www.tcs.com/careers"),
    "cognizant": ("Cognizant", "https://careers.cognizant.com/global-en/jobs/"),
    "accenture": ("Accenture", "https://www.accenture.com/us-en/careers/jobsearch"),
    "capgemini": ("Capgemini", "https://www.capgemini.com/careers/job-search/"),
    "infosys": ("Infosys", "https://career.infosys.com/joblist"),
    "wipro": (
        "Wipro",
        "https://careers.wipro.com/content/Experienced-Professionals/?locale=en_US",
    ),
    "hcltech": ("HCLTech", "https://www.hcltech.com/careers"),
    "ltimindtree": ("LTIMindtree", "https://careers.ltm.com/search"),
    "tech mahindra": ("Tech Mahindra", "https://careers.techmahindra.com/"),
    "oracle": ("Oracle", "https://careers.oracle.com/en/sites/jobsearch/jobs"),
    "microsoft": ("Microsoft", "https://apply.careers.microsoft.com/careers"),
    "meta": ("Meta", "https://www.metacareers.com/jobsearch/"),
    "adobe": ("Adobe", "https://careers.adobe.com/us/en/search-results"),
    "nvidia": ("NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"),
    "salesforce": ("Salesforce", "https://careers.salesforce.com/en/jobs/"),
    "servicenow": ("ServiceNow", "https://careers.servicenow.com/jobs/"),
    "ibm": ("IBM", "https://www.ibm.com/careers/search"),
    "deloitte": ("Deloitte", "https://careers.deloitte.com/"),
    "ey": ("EY", "https://careers.ey.com/"),
    "pwc": ("PwC", "https://www.pwc.com/gx/en/careers.html"),
    "sap": ("SAP", "https://jobs.sap.com/"),
    "intel": ("Intel", "https://jobs.intel.com/en"),
    "cisco": ("Cisco", "https://jobs.cisco.com/"),
}
COMPANY_ALIASES = {
    "tcs": "tata consultancy services",
    "tata consultancy services": "tata consultancy services",
    "hcl": "hcltech",
    "hcl technologies": "hcltech",
    "ltimindtree": "ltimindtree",
    "lti mindtree": "ltimindtree",
    "techmahindra": "tech mahindra",
    "service now": "servicenow",
    "pricewaterhousecoopers": "pwc",
    "pwc": "pwc",
    "ernst young": "ey",
}
ALL_COMPANY_NAMES = tuple(
    sorted(
        {
            "Amazon",
            "Google",
            "Apple",
            *(display for display, _ in COMPANY_PORTALS.values()),
        }
    )
)
SUPPORTED_COMPANIES = {
    "amazon", "aws", "amazon web services", "google", "apple",
    *COMPANY_PORTALS.keys(),
    *COMPANY_ALIASES.keys(),
}
LOCATION_UNIVERSAL_TERMS = {"remote", "worldwide", "global", "anywhere", "all locations"}
LOCATION_UNIVERSAL_WORDS = {"remote", "worldwide", "global", "anywhere", "all", "locations"}
ROLE_TITLE_STOPWORDS = {
    "associate",
    "developer",
    "engineer",
    "entry",
    "intern",
    "junior",
    "lead",
    "manager",
    "mid",
    "principal",
    "senior",
    "software",
    "staff",
    "specialist",
}
WIPRO_MAX_SEARCH_QUERIES = 4
WIPRO_ROLE_QUERY_COUNT = 2
GLOBAL_SEARCH_CONCURRENCY = 5
GLOBAL_SEARCH_CANDIDATE_MULTIPLIER = 2
GLOBAL_SEARCH_MAX_CANDIDATES = 50


def search_terms(profile: CandidateProfile) -> str:
    roles = [role.strip() for role in profile.primary_roles if role.strip()]
    terms = [roles[0]] if roles else []
    role_text = _normalized_search_text(roles[0]) if roles else ""
    for skill in profile.technical_skills:
        normalized_skill = _normalized_search_text(skill)
        if not skill.strip() or normalized_skill in role_text:
            continue
        terms.append(skill.strip())
        if len(terms) == 4:
            break
    if not terms:
        terms = [skill.strip() for skill in profile.technical_skills[:4] if skill.strip()]
    query = " ".join(dict.fromkeys(terms))
    if not query:
        raise ValueError("The resume profile does not include searchable roles or technical skills.")
    return query


def _normalize_company(company: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", company.casefold()).strip()
    return COMPANY_ALIASES.get(normalized, normalized)


def is_supported_company(company: str) -> bool:
    return _normalize_company(company) in SUPPORTED_COMPANIES


def _supported_display_names() -> list[str]:
    return sorted(
        ["Amazon", "Google", "Apple", *(display for display, _ in COMPANY_PORTALS.values())]
    )


def _portal_search_url(
    portal_url: str,
    query: str,
    city: str | None,
    country: str | None,
    query_param: str = "q",
) -> str:
    params = {query_param: query}
    location = ", ".join(part.strip() for part in (city, country) if part and part.strip())
    if location:
        params["location"] = location
    separator = "&" if "?" in portal_url else "?"
    return f"{portal_url}{separator}{urlencode(params)}"


def _normalized_search_text(value: str) -> str:
    value = value.casefold().replace("genai", "generative ai").replace("gen ai", "generative ai")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _role_anchors(profile: CandidateProfile) -> list[str]:
    anchors = {
        " ".join(
            word
            for word in _normalized_search_text(role).split()
            if word not in ROLE_TITLE_STOPWORDS
        )
        for role in profile.primary_roles
    }
    return sorted((anchor for anchor in anchors if anchor), key=len, reverse=True)


def rank_jobs_for_profile(
    jobs: list[JobListing],
    profile: CandidateProfile,
) -> list[JobListing]:
    if not jobs:
        return []

    anchors = _role_anchors(profile)
    if not anchors:
        return jobs

    def role_anchor_match(job: JobListing) -> bool:
        title = f" {_normalized_search_text(job.title)} "
        return any(f" {anchor} " in title for anchor in anchors)

    title_matches = [job for job in jobs if role_anchor_match(job)]
    if title_matches:
        ordered = title_matches
    else:
        ordered = jobs

    normalized_roles = [_normalized_search_text(role) for role in profile.primary_roles]
    normalized_skills = [_normalized_search_text(skill) for skill in profile.technical_skills]
    normalized_domains = [_normalized_search_text(domain) for domain in profile.domain_knowledge]

    def relevance(job: JobListing) -> tuple[int, int]:
        title = f" {_normalized_search_text(job.title)} "
        description = f" {_normalized_search_text(job.description)} "
        role_score = sum(
            4 if role and f" {role} " in title else
            2 if role and f" {role} " in description else
            0
            for role in normalized_roles
        )
        skill_score = sum(
            3 if skill and f" {skill} " in title else
            1 if skill and f" {skill} " in description else
            0
            for skill in normalized_skills
        )
        domain_score = sum(
            2 if domain and f" {domain} " in title else
            1 if domain and f" {domain} " in description else
            0
            for domain in normalized_domains
        )
        return (role_score + skill_score + domain_score, int(role_anchor_match(job)))

    return sorted(
        ordered,
        key=lambda job: (int(role_anchor_match(job)), relevance(job)[0]),
        reverse=True,
    )


def filter_jobs_by_location(
    jobs: list[JobListing],
    city: str | None,
    country: str | None,
) -> list[JobListing]:
    requested = [part.strip().casefold() for part in (city, country) if part and part.strip()]
    if not requested:
        return jobs

    filtered: list[JobListing] = []
    for job in jobs:
        location = job.location.casefold().strip()
        if not location or location in {"not specified", "n/a", "unknown"}:
            continue
        normalized_location = re.sub(r"[^a-z0-9]+", " ", location).strip()
        location_words = set(normalized_location.split())
        if (
            normalized_location in LOCATION_UNIVERSAL_TERMS
            or (
                bool(location_words)
                and location_words.issubset(LOCATION_UNIVERSAL_WORDS)
            )
        ):
            filtered.append(job)
        elif "remote" in location_words:
            if country and country.strip().casefold() in location:
                filtered.append(job)
        elif all(term in location for term in requested):
            filtered.append(job)
    return filtered


async def discover_company_jobs(
    company: str,
    profile: CandidateProfile,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> list[JobListing]:
    query = search_terms(profile)
    normalized = _normalize_company(company)
    if normalized in {"amazon", "aws", "amazon web services"}:
        jobs = await search_amazon(query, limit, city, country)
    elif normalized == "google":
        jobs = await search_google(query, limit, city, country)
    elif normalized == "apple":
        jobs = await search_apple(query, limit, city, country)
    elif normalized == "wipro":
        jobs = await search_wipro_jobs(profile, limit, city, country)
    else:
        company_info = COMPANY_PORTALS.get(normalized)
        if not company_info:
            raise ValueError(
                f"No career portal is configured for {company!r}. "
                f"Supported companies: {', '.join(_supported_display_names())}."
            )
        display_name, portal_url = company_info
        jobs = await search_company_portal(
            display_name, portal_url, query, limit, city, country
        )
    jobs = filter_jobs_by_location(jobs, city, country)
    return rank_jobs_for_profile(jobs, profile)[:limit]


async def discover_network_jobs(
    companies: list[str],
    profile: CandidateProfile,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> dict[str, list[JobListing]]:
    output: dict[str, list[JobListing]] = {}
    seen = {_normalize_company(company): company for company in companies}
    for company in seen.values():
        if not is_supported_company(company):
            logger.info("No configured job source for referral company %s", company)
            output[company] = []
            continue
        output[company] = await discover_company_jobs(company, profile, limit, city, country)
    return output


async def discover_all_company_jobs(
    profile: CandidateProfile,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> tuple[list[JobListing], list[str]]:
    source_limit = min(max(limit, 5), 10)
    candidate_limit = min(
        GLOBAL_SEARCH_MAX_CANDIDATES,
        max(limit * GLOBAL_SEARCH_CANDIDATE_MULTIPLIER, 10),
    )
    semaphore = asyncio.Semaphore(GLOBAL_SEARCH_CONCURRENCY)

    async def search(company: str) -> tuple[str, list[JobListing], bool]:
        async with semaphore:
            try:
                jobs = await discover_company_jobs(
                    company, profile, source_limit, city, country
                )
                return company, jobs, False
            except Exception:
                logger.exception("Global job search failed for %s", company)
                return company, [], True

    source_results = await asyncio.gather(
        *(search(company) for company in ALL_COMPANY_NAMES)
    )
    jobs_by_key: dict[str, JobListing] = {}
    failed_sources: list[str] = []
    for company, jobs, failed in source_results:
        if failed:
            failed_sources.append(company)
        for job in jobs:
            jobs_by_key.setdefault(f"{job.source}:{job.external_id}", job)

    if not jobs_by_key and failed_sources:
        raise RuntimeError(
            "No configured employer portal returned job listings. Sources unavailable: "
            + ", ".join(failed_sources)
        )

    ranked_jobs = rank_jobs_for_profile(list(jobs_by_key.values()), profile)
    return ranked_jobs[:candidate_limit], failed_sources


async def search_amazon(
    query: str,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> list[JobListing]:
    location_query = ", ".join(part.strip() for part in (city, country) if part and part.strip())
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    ) as client:
        response = await client.get(
            AMAZON_SEARCH_URL,
            params={
                "base_query": query,
                "loc_query": location_query,
                "limit": min(limit, 50),
            },
        )
        response.raise_for_status()
        payload = response.json()

    jobs: list[JobListing] = []
    for item in payload.get("jobs", [])[:limit]:
        job_path = item.get("job_path") or ""
        if not job_path:
            logger.warning("Skipping Amazon job without a listing URL.")
            continue
        identifier = str(item.get("id") or item.get("job_id") or item.get("job_path") or "")
        url = urljoin("https://www.amazon.jobs/", job_path)
        if not identifier:
            identifier = url.rsplit("/", 1)[-1]
        jobs.append(
            JobListing(
                external_id=identifier,
                title=item.get("title") or "Untitled role",
                company=item.get("company_name") or "Amazon",
                location=", ".join(
                    part for part in (item.get("city"), item.get("state"), item.get("country")) if part
                )
                or "Not specified",
                url=url,
                description=_strip_html(item.get("description") or ""),
                source="amazon",
            )
        )
    return jobs


async def search_google(
    query: str,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> list[JobListing]:
    url = _portal_search_url(GOOGLE_SEARCH_URL, query, city, country)
    markdown = await _fetch_jina(url)
    jobs = _parse_google_markdown(markdown, limit)
    if jobs:
        return jobs
    from app.llm import extract_jobs_with_groq

    return await extract_jobs_with_groq(markdown, "Google", url, limit)


async def search_apple(
    query: str,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> list[JobListing]:
    url = _portal_search_url(APPLE_SEARCH_URL, query, city, country, query_param="search")
    markdown = await _fetch_jina(url)
    jobs = _parse_linked_job_markdown(markdown, "Apple", url, limit)
    if jobs:
        return jobs
    from app.llm import extract_jobs_with_groq

    return await extract_jobs_with_groq(markdown, "Apple", url, limit)


async def search_company_portal(
    company: str,
    portal_url: str,
    query: str,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> list[JobListing]:
    if company.casefold() in {"capgemini", "tech mahindra"}:
        target_url = portal_url
    else:
        target_url = _portal_search_url(portal_url, query, city, country)
    if company.casefold() == "infosys":
        return await _search_infosys_jobs(query, city, country)

    markdown = await _fetch_jina(target_url)
    from app.llm import extract_jobs_with_groq

    return await extract_jobs_with_groq(markdown, company, target_url, max(limit * 3, 30))


def _wipro_search_terms(profile: CandidateProfile) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for role in profile.primary_roles[:WIPRO_ROLE_QUERY_COUNT]:
        query = role.strip()
        normalized = _normalized_search_text(query)
        if normalized and normalized not in seen:
            queries.append(query)
            seen.add(normalized)

    role_text = " ".join(_normalized_search_text(role) for role in profile.primary_roles)
    for skill in profile.technical_skills:
        query = skill.strip()
        normalized = _normalized_search_text(query)
        if (
            normalized
            and normalized not in seen
            and not re.search(rf"\b{re.escape(normalized)}\b", role_text)
        ):
            queries.append(query)
            seen.add(normalized)
        if len(queries) >= WIPRO_MAX_SEARCH_QUERIES:
            break

    return queries[:WIPRO_MAX_SEARCH_QUERIES]


async def search_wipro_jobs(
    profile: CandidateProfile,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> list[JobListing]:
    queries = _wipro_search_terms(profile)
    if not queries:
        raise ValueError("The resume profile does not include searchable Wipro roles or skills.")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30),
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml",
        },
    ) as client:
        responses = await asyncio.gather(
            *(
                client.get(
                    WIPRO_RSS_URL,
                    params={"locale": "en_US", "keywords": f"({query})"},
                )
                for query in queries
            )
        )

    jobs_by_id: dict[str, JobListing] = {}
    for response in responses:
        response.raise_for_status()
        if len(response.content) > MAX_WIPRO_FEED_BYTES:
            raise RuntimeError("Wipro returned an unexpectedly large job feed.")
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise RuntimeError("Wipro returned an invalid job feed.") from exc
        for item in root.findall("./channel/item"):
            job = _parse_wipro_rss_item(item)
            if job:
                jobs_by_id.setdefault(job.external_id, job)

    jobs = filter_jobs_by_location(list(jobs_by_id.values()), city, country)
    ranked = rank_jobs_for_profile(jobs, profile)
    return ranked[:limit]


def _parse_wipro_rss_item(item: ET.Element) -> JobListing | None:
    title_with_location = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or item.findtext("guid") or "").strip()
    description = item.findtext("description") or ""
    posting_id_match = re.search(r"/job/[^/?#]+/(\d+)/?", urlsplit(link).path)
    if not title_with_location or not link or not posting_id_match:
        return None

    location_match = re.search(r"\(([^()]*)\)\s*$", title_with_location)
    title = title_with_location[: location_match.start()].strip() if location_match else title_with_location
    location = _wipro_location(location_match.group(1)) if location_match else "Not specified"
    parts = urlsplit(link)
    clean_query = encode_query(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key not in {"feedId", "utm_source", "utm_medium", "utm_campaign"}
        ]
    )
    return JobListing(
        external_id=posting_id_match.group(1),
        title=title,
        company="Wipro",
        location=location,
        url=urlunsplit((parts.scheme, parts.netloc, parts.path, clean_query, "")),
        description=_strip_html(description)[:5000],
        source="wipro",
    )


def _wipro_location(feed_location: str) -> str:
    components = [component.strip() for component in feed_location.split(",") if component.strip()]
    if not components:
        return "Not specified"
    city = components[0]
    country_code = next(
        (component for component in components[1:] if re.fullmatch(r"[A-Z]{2}", component)),
        "",
    )
    country = WIPRO_COUNTRIES.get(country_code, country_code)
    return ", ".join(part for part in (city, country) if part) or "Not specified"


async def search_remotive(
    query: str,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> list[JobListing]:
    fetch_limit = 50 if city or country else min(limit, 50)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20), headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(
            REMOTIVE_URL, params={"search": query, "limit": fetch_limit}
        )
        response.raise_for_status()
        payload = response.json()

    results: list[JobListing] = []
    for item in payload.get("jobs", [])[:fetch_limit]:
        url = item.get("url") or ""
        external_id = str(item.get("id", ""))
        if not url or not external_id:
            logger.warning("Skipping Remotive job without a listing URL or id.")
            continue
        results.append(
            JobListing(
                external_id=external_id,
                title=item.get("title") or "Untitled role",
                company=item.get("company_name") or "Unknown",
                location=item.get("candidate_required_location") or "Remote",
                url=url,
                description=_strip_html(item.get("description") or ""),
                source="remotive",
            )
        )
    return filter_jobs_by_location(results, city, country)[:limit]


async def search_google_global(
    query: str,
    limit: int,
    city: str | None = None,
    country: str | None = None,
) -> list[JobListing]:
    return await search_remotive(query, limit, city, country)


async def probe_sources(query: str = "software engineer", limit: int = 3) -> dict[str, dict[str, object]]:
    probes = {
        "amazon": lambda: search_amazon(query, limit),
        "google": lambda: search_google(query, limit),
        "apple": lambda: search_apple(query, limit),
        "remotive": lambda: search_remotive(query, limit),
    }
    results: dict[str, dict[str, object]] = {}
    for name, search in probes.items():
        try:
            jobs = await search()
            results[name] = {"ok": bool(jobs), "count": len(jobs), "sample_title": jobs[0].title if jobs else None}
        except (httpx.HTTPError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return results


async def _fetch_jina(target_url: str) -> str:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(35),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
    ) as client:
        response = await client.get(f"{JINA_URL}{target_url}")
        response.raise_for_status()
        return response.text[:100_000]


def _parse_google_markdown(markdown: str, limit: int) -> list[JobListing]:
    sections = re.split(r"(?m)^\s*###\s+", markdown)
    jobs: list[JobListing] = []
    for section in sections[1:]:
        title_match = re.match(r"(.+?)(?:\s{2,}|$)", section.splitlines()[0].strip())
        if not title_match:
            continue
        title = title_match.group(1).strip().strip("*")
        link_match = re.search(r"Learn more\s*\[[^\]]*\]\((https?://[^)\s]+)", section)
        if not link_match:
            link_match = re.search(r"\]\((https?://[^)\s]*jobs/results/[^)\s]+)\)", section)
        if not link_match:
            continue
        location_match = re.search(r"Google\s*\|\s*([^|\n]+)", section)
        external_id_match = re.search(r"/jobs/results/(\d+)", link_match.group(1))
        jobs.append(
            JobListing(
                external_id=external_id_match.group(1) if external_id_match else link_match.group(1),
                title=title,
                company="Google",
                location=location_match.group(1).strip() if location_match else "Not specified",
                url=link_match.group(1),
                description=_clean_markdown(section)[:5000],
                source="google",
            )
        )
        if len(jobs) >= limit:
            break
    return jobs


def _parse_linked_job_markdown(markdown: str, company: str, base_url: str, limit: int) -> list[JobListing]:
    jobs: list[JobListing] = []
    pattern = re.compile(
        r"(?m)^\s*[*-]\s+###\s+\[([^\]]+)\]\((https?://jobs\.apple\.com/[^)\s]+/details/\d+[^)\s]*)\)"
    )
    matches = list(pattern.finditer(markdown))
    for index, match in enumerate(matches[:limit]):
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        job_url = match.group(2)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        section = markdown[match.end() : end]
        identifier = job_url.rstrip("/").rsplit("/", 1)[-1]
        id_match = re.search(r"/details/(\d+)", job_url)
        jobs.append(
            JobListing(
                external_id=id_match.group(1) if id_match else identifier,
                title=title,
                company=company,
                location="Not specified",
                url=urljoin(base_url, job_url),
                description=_clean_markdown(section)[:5000],
                source=company.casefold(),
            )
        )
        if len(jobs) >= limit:
            break
    return jobs


async def _search_infosys_jobs(
    query: str,
    city: str | None,
    country: str | None,
) -> list[JobListing]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(35),
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://career.infosys.com",
            "Referer": "https://career.infosys.com/",
        },
    ) as client:
        async with client.stream(
            "GET",
            INFOSYS_SEARCH_API_URL,
            params={"sourceId": INFOSYS_SOURCE_IDS, "searchText": query},
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_INFOSYS_RESPONSE_BYTES:
                    raise RuntimeError(
                        "Infosys returned an unexpectedly large job-list response."
                    )
                body.extend(chunk)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Infosys returned invalid JSON for its job listings.") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Infosys returned an unexpected job-list response format.")

    return filter_jobs_by_location(_parse_infosys_jobs(payload), city, country)


def _parse_infosys_jobs(records: list[dict[str, object]]) -> list[JobListing]:
    jobs: list[JobListing] = []
    seen: set[str] = set()
    for record in records:
        title = str(record.get("postingTitle") or record.get("roleDesignation") or "").strip()
        reference_code = str(record.get("referenceCode") or "").strip()
        if not title or not reference_code:
            continue

        if reference_code in seen:
            continue
        seen.add(reference_code)

        source_id = str(record.get("sourceId") or "1")
        query_params = urlencode(
            {"jobReferenceCode": reference_code, "sourceId": source_id}
        )
        location_parts = [
            str(record.get("location") or "").strip(),
            str(record.get("country") or "").strip(),
        ]
        location = ", ".join(part for part in location_parts if part) or "Not specified"
        detail_fields = (
            "postingDescription",
            "technicalRequirement",
            "rolesResponsibilities",
            "additionalResponsibility",
            "preferredSkills",
            "genericSkills",
            "skills",
            "educationalRequirement",
        )
        description_parts = [
            str(record[field]).strip()
            for field in detail_fields
            if record.get(field)
        ]
        minimum = record.get("minExperienceLevel")
        maximum = record.get("maxExperienceLevel")
        if minimum is not None or maximum is not None:
            description_parts.append(f"Required experience: {minimum or 0} to {maximum or 'unspecified'} years.")

        jobs.append(
            JobListing(
                external_id=reference_code,
                title=title,
                company=str(record.get("company") or "Infosys"),
                location=location,
                url=f"{INFOSYS_JOB_DETAIL_URL}?{query_params}",
                description=_strip_html("\n".join(description_parts))[:5000],
                source="infosys",
            )
        )
    return jobs


def _clean_markdown(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"`{1,3}", "", value)
    value = re.sub(r"(?m)^\s*[*_-]{1,3}\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _strip_html(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()
