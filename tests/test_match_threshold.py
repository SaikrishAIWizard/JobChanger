import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import discord

from app.discord_bot import select_matches
from app.schemas import JobListing


def _match(external_id: str, score: int) -> tuple[JobListing, int, str]:
    return (
        JobListing(
            external_id=external_id,
            title=f"Role {external_id}",
            company="Example",
            url=f"https://example.com/jobs/{external_id}",
            source="example",
        ),
        score,
        "The role matches the candidate. Their skills are relevant.",
    )


def test_select_matches_filters_to_threshold_and_sorts() -> None:
    matches = [_match("low", 60), _match("high", 92), _match("mid", 80)]

    selected, summary = select_matches(matches, min_match_percentage=75, max_results=1)

    assert [score for _, score, _ in selected] == [92]
    assert summary == "2 jobs met the 75% minimum."


def test_select_matches_returns_closest_when_nothing_meets_threshold() -> None:
    matches = [_match("low", 55), _match("best", 68), _match("middle", 62)]

    selected, summary = select_matches(matches, min_match_percentage=90, max_results=2)

    assert [score for _, score, _ in selected] == [68, 62]
    assert summary == (
        "No jobs met the 90% minimum; showing the closest available matches (best: 68%)."
    )


def test_slash_commands_expose_match_threshold() -> None:
    from app.discord_bot import bot

    commands = {command.name: command for command in bot.tree.get_commands()}
    for command_name in ("find-jobs", "referral-network"):
        options = {option.name: option for option in commands[command_name].parameters}
        threshold = options["min_match_percentage"]
        assert threshold.min_value == 50
        assert threshold.max_value == 99
        assert options["city"].required is False
        assert options["country"].required is False


def test_job_command_response_is_persistent_in_channel() -> None:
    from app import discord_bot

    defer = AsyncMock()
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=defer)
    )
    asyncio.run(
        discord_bot._defer_persistent_response(cast(discord.Interaction, interaction))
    )

    defer.assert_awaited_once_with(ephemeral=False, thinking=True)


def test_infosys_embed_shows_reference_code_and_direct_job_url() -> None:
    from app.discord_bot import JobResultsView

    job = JobListing(
        external_id="INFSYS-EXTERNAL-256138",
        title="GenAI Developer",
        company="Infosys",
        location="BANGALORE, India",
        url=(
            "https://career.infosys.com/jobdesc?"
            "jobReferenceCode=INFSYS-EXTERNAL-256138&sourceId=1"
        ),
        source="infosys",
    )
    view = JobResultsView(
        [(job, 90, "The role matches the candidate. Their skills are relevant.", None)],
        candidate_name="Candidate",
    )

    listing = view.embed().fields[0].value

    assert "**Reference ID:** `INFSYS-EXTERNAL-256138`" in listing
    assert f"[View job posting]({job.url})" in listing
