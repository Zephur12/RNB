from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from . import _shared

log = logging.getLogger("rnb_wardogs.report")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LOG_PATH = DATA_DIR / "match_log.csv"

CSV_FIELDS = [
    "timestamp_utc",
    "author",
    "author_id",
    "map",
    "score_blue",
    "score_other",
    "status",
    "squads_present",
    "pincered",
    "screenshot_url",
    "source",
]

STATUS_CHOICES = [
    app_commands.Choice(name="В процессе", value="in_progress"),
    app_commands.Choice(name="Победа", value="win"),
    app_commands.Choice(name="Поражение", value="loss"),
]

STATUS_EMOJI = {"in_progress": "🟡", "win": "🟢", "loss": "🔴"}
STATUS_COLOR = {
    "in_progress": discord.Color.gold(),
    "win": discord.Color.green(),
    "loss": discord.Color.red(),
}


class Report(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="report", description="Отчёт по матчу: карта, счёт, статус")
    @app_commands.describe(
        map="Название карты",
        score_blue="Счёт РНБ (синие / Lonestar)",
        score_other="Счёт противника",
        status="Статус матча",
        squads_present="Отряды на карте, через запятую",
        pincered="Зажаты между двумя фронтами?",
        screenshot="Скриншот (опционально)",
    )
    @app_commands.choices(status=STATUS_CHOICES)
    async def report(
        self,
        interaction: discord.Interaction,
        map: str,
        score_blue: int,
        score_other: int,
        status: app_commands.Choice[str],
        squads_present: str,
        pincered: bool,
        screenshot: Optional[discord.Attachment] = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Эта команда доступна только на сервере.", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Не удалось определить твои роли.", ephemeral=True)
            return

        try:
            config = _shared.load_config()
        except FileNotFoundError:
            await interaction.response.send_message(f"Конфиг не найден: `{_shared.CONFIG_PATH}`", ephemeral=True)
            return
        except yaml.YAMLError as exc:
            await interaction.response.send_message(f"Ошибка чтения yaml: `{exc}`", ephemeral=True)
            return

        report_channel_name = config.get("report", {}).get("channel")
        allowed_role_names = _shared.allowed_role_names(config, report_channel_name)
        if not _shared.has_access(member, allowed_role_names):
            await interaction.response.send_message(
                "Команда доступна только Главе отряда, Офицеру или выше.", ephemeral=True
            )
            return

        target_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(report_channel_name)
        )
        if target_channel is None:
            await interaction.response.send_message(
                f"Канал «{report_channel_name}» не найден на сервере. Запусти `/setup-server`.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        embed = self._build_embed(
            member, config, map, score_blue, score_other, status, squads_present, pincered, screenshot
        )

        try:
            await target_channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(
                f"Нет прав отправлять сообщения в «{report_channel_name}».", ephemeral=True
            )
            return

        self._append_log(config, member, map, score_blue, score_other, status, squads_present, pincered, screenshot)

        await interaction.followup.send(f"Отчёт отправлен в #{report_channel_name}.", ephemeral=True)

    @staticmethod
    def _build_embed(
        member: discord.Member,
        config: dict,
        map_name: str,
        score_blue: int,
        score_other: int,
        status: app_commands.Choice[str],
        squads_present: str,
        pincered: bool,
        screenshot: Optional[discord.Attachment],
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"🗺️ {map_name}",
            color=STATUS_COLOR[status.value],
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Счёт",
            value=f"🔵 РНБ (Lonestar): **{score_blue}**\n⚔️ Противник: **{score_other}**",
            inline=True,
        )
        embed.add_field(name="Статус", value=f"{STATUS_EMOJI[status.value]} {status.name}", inline=True)
        embed.add_field(name="Отряды на карте", value=squads_present or "—", inline=False)
        if pincered:
            embed.add_field(
                name="⚠️ Зажаты между двумя фронтами",
                value="Координировать отход или прорыв — см. #тактика-обсуждение",
                inline=False,
            )
        if screenshot is not None:
            embed.set_image(url=screenshot.url)

        source = config.get("report", {}).get("source", "manual")
        embed.set_footer(
            text=f"Отчёт от {member.display_name} • источник: {source}",
            icon_url=member.display_avatar.url,
        )
        return embed

    @staticmethod
    def _append_log(
        config: dict,
        member: discord.Member,
        map_name: str,
        score_blue: int,
        score_other: int,
        status: app_commands.Choice[str],
        squads_present: str,
        pincered: bool,
        screenshot: Optional[discord.Attachment],
    ) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        is_new = not LOG_PATH.exists()

        with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow(
                {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "author": str(member),
                    "author_id": member.id,
                    "map": map_name,
                    "score_blue": score_blue,
                    "score_other": score_other,
                    "status": status.value,
                    "squads_present": squads_present,
                    "pincered": pincered,
                    "screenshot_url": screenshot.url if screenshot else "",
                    "source": config.get("report", {}).get("source", "manual"),
                }
            )

    @report.error
    async def report_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.exception("Ошибка в /report", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Report(bot))
