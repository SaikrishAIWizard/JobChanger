from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from app.config import get_settings
from app.database import cache_jobs, cache_key, get_cached_jobs
from app.ingestion import parse_referral_file, parse_resume_pdf
from app.job_sources import (
    ALL_COMPANY_NAMES,
    discover_company_jobs,
    discover_all_company_jobs,
    discover_network_jobs,
    is_supported_company,
    search_terms,
)
from app.llm import match_jobs, parse_candidate_profile
from app.schemas import CandidateProfile, JobListing, ReferralContact

logger = logging.getLogger(__name__)
settings = get_settings()


class JobResultsView(discord.ui.View):
    def __init__(
        self,
        results: list[tuple[JobListing, int, str, ReferralContact | None]],
        candidate_name: str,
        result_summary: str = "",
        timeout: float = 900,
    ) -> None:
        super().__init__(timeout=timeout)
        self.results = results
        self.candidate_name = candidate_name
        self.result_summary = result_summary
        self.page = 0
        self.page_size = 3

    def embed(self) -> discord.Embed:
        page_count = max(1, (len(self.results) + self.page_size - 1) // self.page_size)
        description = f"Page {self.page + 1} of {page_count}"
        if self.result_summary:
            description = f"{self.result_summary}\n{description}"
        embed = discord.Embed(
            title=f"Matched jobs ({len(self.results)})",
            description=description,
            color=discord.Color.blurple(),
        )
        start = self.page * self.page_size
        for job, score, rationale, contact in self.results[start : start + self.page_size]:
            listing_reference = (
                f"**Reference ID:** `{job.external_id[:80]}`"
                if job.source == "infosys"
                else f"**Req ID:** `{job.external_id[:80]}`"
            )
            embed.add_field(
                name=f"{job.title[:180]} · {score}% match",
                value=f"**Company:** {job.company[:120]}\n"
                f"**Location:** {job.location[:120]}\n"
                f"{listing_reference}\n"
                f"**Referral contact:** {_contact_text(contact)[:140]}\n"
                f"[View job posting]({job.url})"
                if job.source == "infosys"
                else f"[View role]({job.url})",
                inline=False,
            )
            embed.add_field(
                name="Match and referral pitch",
                value=f"**Why it matches:** {rationale[:320]}\n\n"
                f"**Referral pitch:**\n```{_referral_pitch(contact, job, self.candidate_name)[:350]}```",
                inline=False,
            )
        embed.set_footer(text="Listings are retrieved live; confirm availability on the employer site.")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        last_page = max(0, (len(self.results) - 1) // self.page_size)
        self.page = min(last_page, self.page + 1)
        await interaction.response.edit_message(embed=self.embed(), view=self)


class JobBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.none()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        await self.tree.sync()


bot = JobBot()


@bot.tree.command(name="find-jobs", description="Find and rank live jobs against your PDF resume.")
@app_commands.describe(
    resume="Text-based PDF resume",
    company_name="Optional employer; leave blank to search all configured company portals",
    job_count="Number of jobs to return (1-50)",
    min_match_percentage="Minimum resume match score (50-99); if none qualify, shows closest jobs",
    country="Optional country to search",
    city="Optional city to search",
)
async def find_jobs(
    interaction: discord.Interaction,
    resume: discord.Attachment,
    company_name: str | None = None,
    job_count: app_commands.Range[int, 1, 50] = 10,
    min_match_percentage: app_commands.Range[int, 50, 99] = 50,
    country: str | None = None,
    city: str | None = None,
) -> None:
    await _defer_persistent_response(interaction)
    try:
        company_name = company_name.strip() if company_name else None
        company_name = company_name or None
        await _update_progress(interaction, "find-jobs", "Reading your resume...")
        _check_attachment(resume, ".pdf")
        resume_bytes = await resume.read()
        text = parse_resume_pdf(resume_bytes)
        await _update_progress(interaction, "find-jobs", "Extracting your profile with AI...")
        profile = await parse_candidate_profile(text)
        query = search_terms(profile)
        cache_scope = company_name or "all-company-portals"
        key = cache_key(query, cache_scope, job_count, city, country)
        jobs = await get_cached_jobs(key)
        search_summary: str | None = None
        if jobs is None:
            if company_name:
                await _update_progress(
                    interaction, "find-jobs", "Searching current job listings..."
                )
                jobs = await discover_company_jobs(
                    company_name, profile, job_count, city, country
                )
            else:
                await _update_progress(
                    interaction,
                    "find-jobs",
                    f"Searching {len(ALL_COMPANY_NAMES)} employer portals...",
                )
                jobs, failed_sources = await discover_all_company_jobs(
                    profile, job_count, city, country
                )
                search_summary = (
                    f"Searched {len(ALL_COMPANY_NAMES)} employers; "
                    f"{len(failed_sources)} source(s) failed"
                    + (
                        f" ({', '.join(failed_sources[:8])}"
                        f"{'...' if len(failed_sources) > 8 else ''})."
                        if failed_sources
                        else "."
                    )
                )
            await cache_jobs(key, jobs)
        await _update_progress(
            interaction,
            "find-jobs",
            f"Found {len(jobs)} job listings. Scoring matches with AI...",
        )
        await _show_matches(
            interaction,
            profile,
            jobs,
            {},
            max_results=job_count,
            min_match_percentage=min_match_percentage,
            search_summary=search_summary,
        )
    except (ValueError, RuntimeError) as exc:
        await interaction.edit_original_response(content=str(exc), embed=None, view=None)
    except Exception:
        logger.exception("find-jobs command failed")
        await interaction.edit_original_response(
            content="Job search failed unexpectedly. Please try again later; the error has been logged.",
            embed=None,
            view=None,
        )


@bot.tree.command(name="referral-network", description="Find matching jobs at companies in your referral network.")
@app_commands.describe(
    resume="Text-based PDF resume",
    network_file="CSV/XLSX with friend_name, company_name, phone_number columns",
    min_jobs="Maximum jobs to return (1-50)",
    min_match_percentage="Minimum resume match score (50-99); if none qualify, shows closest jobs",
    country="Optional country to search",
    city="Optional city to search",
)
async def referral_network(
    interaction: discord.Interaction,
    resume: discord.Attachment,
    network_file: discord.Attachment,
    min_jobs: app_commands.Range[int, 1, 50] = 10,
    min_match_percentage: app_commands.Range[int, 50, 99] = 50,
    country: str | None = None,
    city: str | None = None,
) -> None:
    await _defer_persistent_response(interaction)
    try:
        await _update_progress(
            interaction, "referral-network", "Reading your resume and referral file..."
        )
        _check_attachment(resume, ".pdf")
        _check_attachment(network_file, ".csv", ".xlsx")
        resume_text = parse_resume_pdf(await resume.read())
        contacts = parse_referral_file(network_file.filename, await network_file.read())
        await _update_progress(
            interaction, "referral-network", "Extracting your profile with AI..."
        )
        profile = await parse_candidate_profile(resume_text)
        unsupported = sorted({
            contact.company_name for contact in contacts
            if not is_supported_company(contact.company_name)
        })
        await _update_progress(
            interaction, "referral-network", "Searching referral companies for open listings..."
        )
        company_jobs = await discover_network_jobs(
            [contact.company_name for contact in contacts],
            profile,
            min_jobs,
            city,
            country,
        )
        company_jobs_by_name = {
            company.casefold().strip(): jobs for company, jobs in company_jobs.items()
        }
        contact_by_company: dict[str, ReferralContact] = {}
        for contact in contacts:
            contact_by_company.setdefault(contact.company_name.casefold().strip(), contact)
        company_buckets = [
            (
                company.casefold().strip(),
                jobs,
                contact_by_company[company.casefold().strip()],
            )
            for company, jobs in company_jobs.items()
            if company.casefold().strip() in contact_by_company
        ]
        candidate_cap = min(50, min_jobs * 2)
        jobs_with_contacts: list[tuple[JobListing, ReferralContact]] = []
        seen_job_ids: set[str] = set()
        depth = 0
        while len(jobs_with_contacts) < candidate_cap:
            added = False
            for _, company_listings, contact in company_buckets:
                if depth >= len(company_listings):
                    continue
                job = company_listings[depth]
                key = f"{job.source}:{job.external_id}"
                if key not in seen_job_ids:
                    jobs_with_contacts.append((job, contact))
                    seen_job_ids.add(key)
                    added = True
                    if len(jobs_with_contacts) == candidate_cap:
                        break
            if not added:
                break
            depth += 1
        jobs = [job for job, _ in jobs_with_contacts]
        if unsupported:
            logger.info("Referral network includes unsupported companies: %s", unsupported)
        await _update_progress(
            interaction,
            "referral-network",
            f"Found {len(jobs)} job listings. Scoring matches with AI...",
        )
        await _show_matches(
            interaction,
            profile,
            jobs,
            {
                f"{job.source}:{job.external_id}": contact
                for job, contact in jobs_with_contacts
            },
            max_results=min_jobs,
            min_match_percentage=min_match_percentage,
        )
    except (ValueError, RuntimeError) as exc:
        await interaction.edit_original_response(content=str(exc), embed=None, view=None)
    except Exception:
        logger.exception("referral-network command failed")
        await interaction.edit_original_response(
            content="Referral matching failed unexpectedly. Please try again later; the error has been logged.",
            embed=None,
            view=None,
        )


async def _show_matches(
    interaction: discord.Interaction,
    profile: CandidateProfile,
    jobs: list[JobListing],
    contacts: dict[str, ReferralContact],
    max_results: int | None = None,
    min_match_percentage: int = 50,
    search_summary: str | None = None,
) -> None:
    if not jobs:
        await interaction.edit_original_response(
            content=(
                "No matching active listings were found for the selected sources."
                + (f"\n{search_summary}" if search_summary else "")
            ),
            embed=None,
            view=None,
        )
        return
    matches = await match_jobs(profile, jobs)
    selected_matches, summary = select_matches(
        matches,
        min_match_percentage=min_match_percentage,
        max_results=max_results,
    )
    if search_summary:
        summary = f"{summary}\n{search_summary}"
    results = [
        (job, score, rationale, contacts.get(f"{job.source}:{job.external_id}"))
        for job, score, rationale in selected_matches
    ]
    view = JobResultsView(
        results,
        candidate_name=profile.full_name,
        result_summary=summary,
    )
    await interaction.edit_original_response(content=None, embed=view.embed(), view=view)


def select_matches(
    matches: list[tuple[JobListing, int, str]],
    min_match_percentage: int,
    max_results: int | None = None,
) -> tuple[list[tuple[JobListing, int, str]], str]:
    ranked = sorted(matches, key=lambda match: match[1], reverse=True)
    qualified = [match for match in ranked if match[1] >= min_match_percentage]
    if qualified:
        selected = qualified[:max_results] if max_results is not None else qualified
        return selected, f"{len(qualified)} jobs met the {min_match_percentage}% minimum."

    closest = ranked[:max_results] if max_results is not None else ranked
    best_score = ranked[0][1] if ranked else 0
    return closest, (
        f"No jobs met the {min_match_percentage}% minimum; showing the closest available "
        f"matches (best: {best_score}%)."
    )


def _check_attachment(attachment: discord.Attachment, *extensions: str) -> None:
    if not attachment.filename.lower().endswith(extensions):
        raise ValueError(f"Unsupported file type: {attachment.filename}.")
    if attachment.size > settings.max_attachment_bytes:
        raise ValueError(f"File exceeds the {settings.max_attachment_bytes // (1024 * 1024)} MB upload limit.")


async def _update_progress(
    interaction: discord.Interaction,
    command_name: str,
    message: str,
) -> None:
    logger.info("%s: %s", command_name, message)
    await interaction.edit_original_response(content=message)


async def _defer_persistent_response(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=False, thinking=True)


def _contact_text(contact: ReferralContact | None) -> str:
    if not contact:
        return "Not provided"
    phone = f" · {contact.phone_number}" if contact.phone_number else ""
    return f"{contact.friend_name}{phone}"


def _referral_pitch(contact: ReferralContact | None, job: JobListing, candidate_name: str) -> str:
    if not contact:
        return "No referral contact was provided."
    safe_name = re.sub(r"[\r\n`]", " ", contact.friend_name).strip()
    safe_title = re.sub(r"[\r\n`]", " ", job.title).strip()
    safe_candidate = re.sub(r"[\r\n`]", " ", candidate_name).strip() or "the candidate"
    safe_company = re.sub(r"[\r\n`]", " ", job.company).strip()
    safe_req_id = re.sub(r"[\r\n`]", " ", job.external_id).strip()
    return (
        f"Hi, I'm {safe_name} from {safe_company}. I’d like to recommend {safe_candidate} "
        f"for the {safe_title} role (Req ID: {safe_req_id}). Their experience appears "
        "relevant to the role; would you be open to reviewing their application?"
    )


async def close_bot() -> None:
    if not bot.is_closed():
        await bot.close()
