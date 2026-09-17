from __future__ import annotations

import csv
import logging
from collections import Counter, defaultdict

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from . import _shared
from .report import LOG_PATH

log = logging.getLogger("rnb_wardogs.stats")


def _format_list(items: list[str]) -> str:
    if not items:
        return "—"
    text = "\n".join(f"• {item}" for item in items)
    return text[:1024]


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="stats", description="Статистика по data/match_log.csv: карты, W/L, зажимы")
    async def stats(self, interaction: discord.Interaction) -> None:
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
        except (FileNotFoundError, yaml.YAMLError) as exc:
            await interaction.response.send_message(f"Ошибка чтения конфига: `{exc}`", ephemeral=True)
            return

        report_channel_name = config.get("report", {}).get("channel")
        allowed_role_names = _shared.allowed_role_names(config, report_channel_name)
        if not _shared.has_access(member, allowed_role_names):
            await interaction.response.send_message(
                "Команда доступна только Главе отряда, Офицеру или выше (тот же уровень, что и /report — "
                "данные из того же источника).",
                ephemeral=True,
            )
            return

        if not LOG_PATH.exists():
            await interaction.response.send_message(
                "Данных ещё нет — никто не отправлял `/report`.", ephemeral=True
            )
            return

        with LOG_PATH.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        if not rows:
            await interaction.response.send_message("Лог пуст.", ephemeral=True)
            return

        total = len(rows)
        pincered_count = sum(1 for row in rows if row.get("pincered") == "True")
        status_counts = Counter(row.get("status", "") for row in rows)

        by_map: dict[str, Counter] = defaultdict(Counter)
        for row in rows:
            by_map[row.get("map", "—")][row.get("status", "")] += 1

        embed = discord.Embed(title="📊 Статистика РНБ", color=discord.Color.blurple())
        embed.add_field(name="Всего операций", value=str(total), inline=True)
        embed.add_field(
            name="🟢 Победы / 🔴 Поражения / 🟡 В процессе",
            value=f"{status_counts.get('win', 0)} / {status_counts.get('loss', 0)} / {status_counts.get('in_progress', 0)}",
            inline=True,
        )
        embed.add_field(
            name="⚠️ Зажаты между двумя фронтами",
            value=f"{pincered_count} из {total} ({pincered_count * 100 // total}%)",
            inline=True,
        )

        map_lines = []
        for map_name, counts in sorted(by_map.items(), key=lambda kv: sum(kv[1].values()), reverse=True):
            map_lines.append(
                f"**{map_name}** — 🟢{counts.get('win', 0)} 🔴{counts.get('loss', 0)} 🟡{counts.get('in_progress', 0)}"
            )
        embed.add_field(name=f"По картам ({len(by_map)})", value=_format_list(map_lines), inline=False)

        embed.set_footer(text=f"Источник: {LOG_PATH.name}")

        await interaction.response.send_message(embed=embed)

    @stats.error
    async def stats_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.exception("Ошибка в /stats", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Stats(bot))
