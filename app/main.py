from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import discord
import uvicorn
from fastapi import FastAPI

from app.config import get_settings
from app.database import close_database, initialize_database, storage_status
from app.discord_bot import bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await initialize_database()
    try:
        yield
    finally:
        await close_database()


app = FastAPI(title="Job Referral Bot", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "bot": "connected" if bot.is_ready() else "connecting",
        "database": storage_status(),
    }


async def run() -> None:
    if not settings.discord_token:
        raise RuntimeError("DISCORD_TOKEN is required to run the combined Discord bot and health service.")

    server = uvicorn.Server(
        uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info")
    )
    web_task = asyncio.create_task(server.serve(), name="health-api")
    bot_task = asyncio.create_task(bot.start(settings.discord_token), name="discord-bot")
    try:
        done, pending = await asyncio.wait(
            {web_task, bot_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            error = task.exception()
            if error:
                raise error
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        server.should_exit = True
        if not bot.is_closed():
            await bot.close()
        await asyncio.gather(web_task, bot_task, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except discord.LoginFailure:
        logger.exception("Discord rejected DISCORD_TOKEN.")
        raise
