# Job Referral Bot

An async Discord bot that searches public employer job listings, parses candidate resumes, and ranks jobs with Gemini. It exposes a FastAPI `/health` endpoint from the same Render web service.

## Job sources

- **Amazon Jobs**: public JSON search endpoint.
- **Google Careers**: Jina Reader-rendered search results.
- **Apple Jobs**: Jina Reader-rendered search results.
- **Remote aggregator**: Remotive's public remote-jobs API.
- **Additional company portals**: TCS, Cognizant, Accenture, Capgemini, Infosys,
  Wipro, HCLTech, LTIMindtree, Tech Mahindra, Oracle, Microsoft, Meta, Adobe,
  NVIDIA, Salesforce, ServiceNow, IBM, Deloitte, EY, PwC, SAP, Intel, and Cisco.

Additional company portals are best-effort integrations, not guaranteed live job
feeds: career-site layouts, access controls, and query handling vary, and a portal
may return no listings. Infosys listings come from the public job-search API used by
its careers site. The adapter returns the posting reference code and a direct
`jobdesc` URL built from the site's `jobReferenceCode` and `sourceId`. Other generic
portals are fetched through Jina Reader and parsed with the configured Groq model;
they require `GROQ_API_KEY`, and listings without a verifiable job-specific URL and
visible title are discarded. Wipro uses its SuccessFactors RSS search feed rather
than the retired `/global/en/search-results` route, and returns direct job links
with the posting ID. Wipro query terms are built from the candidate's extracted
primary job titles and technical skills, so searches are not tied to a particular
technology or role family. Career-site URLs are periodically updated as providers
move or retire job boards; the current adapters use the Accenture US job search,
Capgemini job search, LTM (formerly LTIMindtree), Tech Mahindra Careers, IBM Careers,
and Meta job-search destinations. Location
filters use the listing's reported location and retain listings explicitly labeled
unrestricted remote/worldwide, or remote listings that name the requested country;
roles with unknown locations are excluded when a city or country filter is provided.
Capgemini and Tech Mahindra do not accept the generic `q` query parameter on their
career pages, so their adapters fetch the official current-listings pages instead
and rank any verifiable results against the candidate profile.
Leaving `company_name` blank in `/find-jobs` searches all configured employer
sources (including Amazon, Google, and Apple), gathers results concurrently, and
scores a bounded candidate pool to return the strongest matches. Sources that fail
are skipped when other providers return jobs and are reported in the results.

Run the probe before deploying or after source changes:

```powershell
python -m scripts.probe_job_sources --query "software engineer" --limit 3
```

Portals change frequently; a failed probe is reported per source, and the command exits nonzero if any source fails. The probe tests the deterministic Amazon, Google, Apple, and Remotive sources.

The Groq integration is limited to extracting structured listings from Jina markdown when a deterministic parser finds no jobs; in that case, `GROQ_API_KEY` is required. It does not generate or execute scraper code. Executing LLM-generated code or silently rewriting deployed scrapers would be unsafe; source changes remain reviewed code changes. Search favors the candidate's first primary role and non-redundant skills, and listings whose titles match a primary-role anchor are preferred over unrelated jobs. Matching prioritizes core role and responsibilities over generic transferable skills.

## Setup

For Windows local development, follow [run.txt](./run.txt). In short, create a uv
environment with `uv venv --python 3.13 .venv` and install packages with
`uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt`.

1. Copy `.env.example` to `.env` and set `DISCORD_TOKEN`, `GEMINI_API_KEY`, and optionally `DATABASE_URL`.
2. Configure the Discord application with the `bot` and `applications.commands` scopes. The bot needs permission to use slash commands and send ephemeral responses.
3. Run locally with `.\.venv\Scripts\python.exe -m app.main`.

The `.env.example` contains placeholders only. If any real credentials were previously
placed in it, revoke/rotate those credentials before use.

The bot requires `GEMINI_API_KEY` for its primary resume parsing and job matching. If Gemini returns 503 or times out after the optional Gemini model fallback, a configured `GROQ_API_KEY` and `GROQ_EXTRACTION_MODEL` are used for resume parsing and job matching as well as structured job extraction. Keys are environment-only; users should not paste API keys into Discord.

The model IDs are configurable in `.env`: resume parsing and matching default to
`gemini-3.8-flash`, with `gemini-3.6-flash` as the fallback. `GROQ_EXTRACTION_MODEL`
defaults to `llama-3.3-70b-versatile`. These Gemini IDs support structured outputs
according to Google's model documentation; API-key access and quotas still depend on
the Google AI Studio project. On HTTP 404, 503, or 504, the bot tries the configured
Gemini fallback once. HTTP 429/503/504 or timeouts after that use the configured Groq
fallback; HTTP 400/403/404 are reported as model, access, or request-configuration
errors. Gemini calls have a 45-second default timeout and SDK automatic retries are
disabled. Set `LLM_TIMEOUT_SECONDS` to adjust the request timeout.

## Commands

- `/find-jobs resume:<PDF> company_name:<optional> job_count:<1-50> min_match_percentage:<50-99> city:<optional> country:<optional>` — searches a configured company portal or, when company is blank, remote listings from Remotive.
- `/referral-network resume:<PDF> network_file:<CSV/XLSX> min_jobs:<1-50> min_match_percentage:<50-99> city:<optional> country:<optional>` — accepts `friend_name`, `company_name`, and optional `phone_number` columns and searches the configured company portals.

The minimum match percentage filters the ranked results. If no listing reaches the
requested threshold, the bot shows the closest available jobs and identifies the best
score, rather than returning an empty result.

Both commands respond ephemerally. Resume text and referral contacts are processed in memory and are not persisted. Only job search results are cached in PostgreSQL for the configured TTL.

## Deployment and storage

Render's free service uses ephemeral local storage; this app does not write user uploads to disk. Set `DATABASE_URL` to a managed PostgreSQL service such as Neon or Supabase. The service creates its single cache table at startup. If the database is unset, the bot can still run but persistent caching is disabled and health reports that state.

Set `PORT` from Render's environment and configure an external uptime monitor to request `/health` every five minutes. This endpoint confirms the web process is responsive; `bot` reports whether Discord has connected.

The implementation uses bounded HTTP timeouts, small SQL connection pools, 10 MB attachment limits, read-only Excel parsing, and no browser automation. Employer APIs and Jina Reader are external dependencies and can change or rate-limit requests.

## Validation

Run unit tests with `.\.venv\Scripts\python.exe -m pytest -q`. Check live source availability with the probe command above. Live resume parsing and matching require valid Gemini credentials, and persistent caching requires a reachable PostgreSQL database.
