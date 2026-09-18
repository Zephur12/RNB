from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
import yaml
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
    "score_us",
    "score_valkyra",
    "score_manticore",
    "status",
    "pincered_by",
    "source",
]

STATUS_OPTIONS = [
    ("in_progress", "🟡 В процессе"),
    ("win", "🟢 Победа"),
    ("loss", "🔴 Поражение"),
]
STATUS_LABELS = dict(STATUS_OPTIONS)
STATUS_COLOR = {
    "in_progress": discord.Color.gold(),
    "win": discord.Color.green(),
    "loss": discord.Color.red(),
}

PINCERED_OPTIONS = [
    ("none", "Нет"),
    ("valkyra", "Valkyra"),
    ("manticore", "Manticore"),
    ("both", "Обе"),
]
PINCERED_LABELS = dict(PINCERED_OPTIONS)


def _has_report_access(config: dict, member: discord.Member) -> bool:
    report_channel_name = config.get("report", {}).get("channel")
    allowed = _shared.allowed_role_names(config, report_channel_name)
    return _shared.has_access(member, allowed)


def _parse_score(raw: str, field_label: str) -> int:
    value = raw.strip()
    if not value.lstrip("-").isdigit():
        raise ValueError(f"«{field_label}» должно быть числом, получено: «{raw}».")
    return int(value)


class StatusPincerView(discord.ui.View):
    """Ephemeral follow-up after the report Modal: pick status + who
    pincered us via buttons, then submit. Holds the modal's text fields
    plus the running selection directly as Python attributes — this view
    only needs to survive one interactive minute for one person, so there's
    no reason to persist it or encode state into custom_ids."""

    def __init__(self, member: discord.Member, map_name: str, score_us: int, score_valkyra: int,
                 score_manticore: int, notes: Optional[str]) -> None:
        super().__init__(timeout=300)
        self.member = member
        self.map_name = map_name
        self.score_us = score_us
        self.score_valkyra = score_valkyra
        self.score_manticore = score_manticore
        self.notes = notes
        self.status: Optional[str] = None
        self.pincered_by: Optional[str] = None

        for value, label in STATUS_OPTIONS:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=0)
            btn.callback = self._make_status_callback(value)
            self.add_item(btn)

        for value, label in PINCERED_OPTIONS:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=1)
            btn.callback = self._make_pincered_callback(value)
            self.add_item(btn)

        submit_btn = discord.ui.Button(label="✅ Отправить", style=discord.ButtonStyle.success, row=2)
        submit_btn.callback = self._submit
        self.add_item(submit_btn)

    def _make_status_callback(self, value: str):
        async def callback(interaction: discord.Interaction) -> None:
            self.status = value
            await interaction.response.edit_message(content=self._summary(), view=self)

        return callback

    def _make_pincered_callback(self, value: str):
        async def callback(interaction: discord.Interaction) -> None:
            self.pincered_by = value
            await interaction.response.edit_message(content=self._summary(), view=self)

        return callback

    def _summary(self) -> str:
        status_text = STATUS_LABELS.get(self.status, "не выбрано")
        pincered_text = PINCERED_LABELS.get(self.pincered_by, "не выбрано")
        return (
            f"**{self.map_name}** — 🔵{self.score_us} / 🟣Valkyra {self.score_valkyra} / "
            f"🟠Manticore {self.score_manticore}\n"
            f"Статус: {status_text}\nЗажаты: {pincered_text}\n\n"
            f"Жми «✅ Отправить», когда всё выбрано."
        )

    async def _submit(self, interaction: discord.Interaction) -> None:
        if self.status is None or self.pincered_by is None:
            await interaction.response.send_message(
                "Выбери и статус, и «зажаты кем» перед отправкой.", ephemeral=True
            )
            return

        try:
            config = _shared.load_config()
        except (FileNotFoundError, yaml.YAMLError) as exc:
            await interaction.response.send_message(f"Ошибка чтения конфига: `{exc}`", ephemeral=True)
            return

        report_channel_name = config.get("report", {}).get("channel")
        target_channel = discord.utils.get(
            interaction.guild.text_channels, name=_shared.normalize_channel_name(report_channel_name)
        )
        if target_channel is None:
            await interaction.response.send_message(
                f"Канал «{report_channel_name}» не найден. Запусти `/setup-server`.", ephemeral=True
            )
            return

        embed = self._build_embed(config)
        try:
            await target_channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"Нет прав отправлять сообщения в «{report_channel_name}».", ephemeral=True
            )
            return

        self._append_log(config)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"Отчёт отправлен в {target_channel.mention}.", view=self
        )
        self.stop()

    def _build_embed(self, config: dict) -> discord.Embed:
        embed = discord.Embed(
            title=f"🗺️ {self.map_name}",
            color=STATUS_COLOR[self.status],
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Счёт",
            value=(
                f"🔵 РНБ: **{self.score_us}**\n"
                f"🟣 Valkyra: **{self.score_valkyra}**\n"
                f"🟠 Manticore: **{self.score_manticore}**"
            ),
            inline=True,
        )
        embed.add_field(name="Статус", value=STATUS_LABELS[self.status], inline=True)
        if self.pincered_by != "none":
            embed.add_field(
                name="⚠️ Зажаты между двумя фронтами",
                value=f"Кем: {PINCERED_LABELS[self.pincered_by]}. Координировать отход/прорыв — "
                      f"см. #тактика-обсуждение",
                inline=False,
            )
        if self.notes:
            embed.add_field(name="Заметки", value=self.notes, inline=False)

        source = config.get("report", {}).get("source", "manual")
        embed.set_footer(
            text=f"Отчёт от {self.member.display_name} • источник: {source}",
            icon_url=self.member.display_avatar.url,
        )
        return embed

    def _append_log(self, config: dict) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        is_new = not LOG_PATH.exists()

        if not is_new:
            # "is_new" only means "file exists", not "header matches CSV_FIELDS".
            # If someone changes CSV_FIELDS later without clearing the old file,
            # DictWriter would silently append new-schema values under an
            # old-schema header — every column shifts, no error, no warning.
            # Caught this exact bug once already; refuse instead of corrupting.
            with LOG_PATH.open("r", newline="", encoding="utf-8") as f:
                existing_header = f.readline().strip().split(",")
            if existing_header != CSV_FIELDS:
                raise RuntimeError(
                    f"{LOG_PATH} has header {existing_header}, but CSV_FIELDS is now {CSV_FIELDS}. "
                    "Schema changed — move/delete the old file (or migrate it) before writing more rows."
                )

        with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow(
                {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "author": str(self.member),
                    "author_id": self.member.id,
                    "map": self.map_name,
                    "score_us": self.score_us,
                    "score_valkyra": self.score_valkyra,
                    "score_manticore": self.score_manticore,
                    "status": self.status,
                    "pincered_by": self.pincered_by,
                    "source": config.get("report", {}).get("source", "manual"),
                }
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        log.exception("Ошибка в StatusPincerView", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class ReportModal(discord.ui.Modal, title="Отчёт по матчу"):
    map_name = discord.ui.TextInput(label="Карта", max_length=100)
    score_us = discord.ui.TextInput(label="Наш счёт (РНБ)", max_length=6)
    score_valkyra = discord.ui.TextInput(label="Счёт Valkyra", max_length=6)
    score_manticore = discord.ui.TextInput(label="Счёт Manticore", max_length=6)
    notes = discord.ui.TextInput(label="Заметки (необязательно)", required=False, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Не удалось определить твои роли.", ephemeral=True)
            return

        try:
            score_us = _parse_score(str(self.score_us.value), "Наш счёт")
            score_valkyra = _parse_score(str(self.score_valkyra.value), "Счёт Valkyra")
            score_manticore = _parse_score(str(self.score_manticore.value), "Счёт Manticore")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        view = StatusPincerView(
            member=member,
            map_name=str(self.map_name.value).strip(),
            score_us=score_us,
            score_valkyra=score_valkyra,
            score_manticore=score_manticore,
            notes=str(self.notes.value).strip() if self.notes.value else None,
        )
        await interaction.response.send_message(view._summary(), view=view, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Ошибка в ReportModal", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class ReportEntryView(discord.ui.View):
    """Persistent entry point — posted in #карта-и-счёт alongside its
    explainer. Registered via bot.add_view() in Report.__init__."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Отчёт по матчу", style=discord.ButtonStyle.primary, custom_id="rnb:report_entry")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
            return

        try:
            config = _shared.load_config()
        except (FileNotFoundError, yaml.YAMLError) as exc:
            await interaction.response.send_message(f"Ошибка чтения конфига: `{exc}`", ephemeral=True)
            return

        if not _has_report_access(config, member):
            await interaction.response.send_message(
                "Доступно только Главе отряда, Офицеру или выше.", ephemeral=True
            )
            return

        await interaction.response.send_modal(ReportModal())


class Report(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bot.add_view(ReportEntryView())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Report(bot))
