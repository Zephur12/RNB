from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rnb_wardogs")

COGS = [
    "cogs.provisioning",
    "cogs.report",
    "cogs.translate",
    "cogs.ops",
    "cogs.onboarding",
    "cogs.stats",
    "cogs.promotion",
]

intents = discord.Intents.default()
intents.message_content = True


class RNBBot(commands.Bot):
    async def setup_hook(self) -> None:
        for cog in COGS:
            await self.load_extension(cog)

        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d slash command(s) to guild %s", len(synced), guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d slash command(s) globally (may take up to an hour to appear)", len(synced))


bot = RNBBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id if bot.user else "?")


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
    bot.run(token)


if __name__ == "__main__":
    main()
