from __future__ import annotations

import logging

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from . import _shared

log = logging.getLogger("rnb_wardogs.promotion")

# Порядок имеет значение — это и есть иерархия. Держим здесь, не в _shared,
# потому что это специфика именно повышения, а не общая структура сервера.
RANK_HIERARCHY = [
    "Новобранец | Recruit",
    "Боец | Trooper",
    "Глава отряда | Squad Lead",
    "Офицер | Officer",
    "Верховный Главнокомандующий | Supreme Commander",
]

ANNOUNCE_CHANNEL_NAME = "объявления-announcements"
OFFICER_DESK_CHANNEL_NAME = "офицерский-стол"


def _rank_label(role_name: str) -> str:
    return role_name.split(" | ")[0]


def _higher_ranks_for(member: discord.Member) -> list[str]:
    """Ranks strictly above the member's current (highest-held) rank. Empty
    if already at the top or holding no rank at all is treated as "below
    Новобранец" (returns the full hierarchy) — shared by both the officer-
    side «РНБ: Повысить» menu and the member-side «Запросить повышение»
    button, so the two stay in sync by construction."""
    current_index = -1
    for i, rank_name in enumerate(RANK_HIERARCHY):
        if discord.utils.get(member.roles, name=rank_name) is not None:
            current_index = i  # берём САМЫЙ высокий, если вдруг держит несколько
    return RANK_HIERARCHY[current_index + 1 :]


class PromoteSelectView(discord.ui.View):
    """Ephemeral, short-lived — one Officer picking one new rank for one
    member. No reason to persist this across a restart."""

    def __init__(self, target: discord.Member, higher_ranks: list[str]) -> None:
        super().__init__(timeout=120)
        self.target = target
        select = discord.ui.Select(
            placeholder="Новый ранг",
            options=[discord.SelectOption(label=_rank_label(name), value=name) for name in higher_ranks],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        new_rank_name = interaction.data["values"][0]
        guild = interaction.guild
        new_role = discord.utils.get(guild.roles, name=new_rank_name)
        if new_role is None:
            await interaction.response.send_message(
                f"Роль «{new_rank_name}» не найдена на сервере. Попроси прогнать `/setup-server`.",
                ephemeral=True,
            )
            return

        old_rank_roles = [r for r in self.target.roles if r.name in RANK_HIERARCHY and r != new_role]

        try:
            if old_rank_roles:
                await self.target.remove_roles(*old_rank_roles, reason=f"РНБ: повышение до {new_rank_name}")
            await self.target.add_roles(new_role, reason=f"РНБ: повышение до {new_rank_name}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Не хватает прав — роль бота должна быть выше ранговых ролей в списке ролей сервера.",
                ephemeral=True,
            )
            return

        rank_label = _rank_label(new_rank_name)
        await interaction.response.send_message(
            f"Готово: {self.target.mention} теперь «{rank_label}».", ephemeral=True
        )

        try:
            await self.target.send(f"Тебя повысили до «{rank_label}» в РНБ! 🎉")
        except discord.Forbidden:
            pass  # ЛС закрыты — это не ошибка, просто без уведомления

        announce_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(ANNOUNCE_CHANNEL_NAME)
        )
        if announce_channel is not None:
            try:
                await announce_channel.send(f"🎉 {self.target.mention} повышен(а) до «{rank_label}»!")
            except discord.Forbidden:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        log.exception("Ошибка в PromoteSelectView", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class PromotionRequestModal(discord.ui.Modal, title="Запрос на повышение"):
    reason = discord.ui.TextInput(
        label="Что ты сделал / почему считаешь, что готов?",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, higher_ranks: list[str]) -> None:
        super().__init__()
        self.higher_ranks = higher_ranks

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта форма работает только на сервере.", ephemeral=True)
            return

        current_rank = next((r for r in reversed(RANK_HIERARCHY) if discord.utils.get(member.roles, name=r)), None)
        current_label = _rank_label(current_rank) if current_rank else "без ранга"

        desk_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(OFFICER_DESK_CHANNEL_NAME)
        )
        if desk_channel is None:
            await interaction.response.send_message(
                f"Канал «{OFFICER_DESK_CHANNEL_NAME}» не найден. Попроси Офицера прогнать `/setup-server`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎖 Запрос на повышение",
            description=f"{member.mention} (сейчас: «{current_label}») считает, что готов(а) к повышению.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Обоснование", value=str(self.reason.value), inline=False)

        try:
            await desk_channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"Не хватает прав постить в {desk_channel.mention} (Forbidden).", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Запрос отправлен в {desk_channel.mention} — с тобой свяжутся.", ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Ошибка в PromotionRequestModal", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class PromotionRequestView(discord.ui.View):
    """Persistent — posted once via /post-onboarding on the rank-guide
    message. Replaces the old "спроси у Главы отряда/#обратная-связь"
    text with an actual, undroppable request instead of hoping someone
    remembers a chat message."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="🎖 Запросить повышение", style=discord.ButtonStyle.primary, custom_id="rnb:promotion_request")
    async def request(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
            return

        higher_ranks = _higher_ranks_for(member)
        if not higher_ranks:
            current_rank = next((r for r in reversed(RANK_HIERARCHY) if discord.utils.get(member.roles, name=r)), None)
            current_label = _rank_label(current_rank) if current_rank else "без ранга"
            await interaction.response.send_message(
                f"Ты уже «{current_label}» — выше повышать некуда.", ephemeral=True
            )
            return

        await interaction.response.send_modal(PromotionRequestModal(higher_ranks))


async def _promote_callback(interaction: discord.Interaction, member: discord.Member) -> None:
    # discord.py raises TypeError at import time if a context menu callback is
    # defined as a class method ("context menus cannot be defined inside a
    # class") — has to be a plain module-level function, unlike
    # @app_commands.command which works fine inside a Cog. Registered onto
    # the tree manually in Promotion.__init__ instead of auto-discovered.
    guild = interaction.guild
    invoker = interaction.user
    if guild is None or not isinstance(invoker, discord.Member):
        await interaction.response.send_message("Работает только на сервере.", ephemeral=True)
        return

    try:
        config = _shared.load_config()
    except (FileNotFoundError, yaml.YAMLError) as exc:
        await interaction.response.send_message(f"Ошибка чтения конфига: `{exc}`", ephemeral=True)
        return

    allowed = config.get("sync", {}).get("allowed_roles", [])
    if not _shared.has_access(invoker, allowed):
        await interaction.response.send_message(
            "Доступно только Офицеру или Верховному Главнокомандующему.", ephemeral=True
        )
        return

    higher_ranks = _higher_ranks_for(member)
    if not higher_ranks:
        current_rank = next((r for r in reversed(RANK_HIERARCHY) if discord.utils.get(member.roles, name=r)), None)
        current_label = _rank_label(current_rank) if current_rank else "без ранга"
        await interaction.response.send_message(
            f"{member.mention} уже «{current_label}» — выше повышать некуда.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"Выбери новый ранг для {member.mention}:",
        view=PromoteSelectView(member, higher_ranks),
        ephemeral=True,
    )


async def _promote_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.exception("Ошибка в контекстном меню «РНБ: Повысить»", exc_info=error)
    message = f"Непредвиденная ошибка: `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class Promotion(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bot.add_view(PromotionRequestView())
        self.promote_menu = app_commands.ContextMenu(name="РНБ: Повысить", callback=_promote_callback)
        self.promote_menu.error(_promote_error)
        self.bot.tree.add_command(self.promote_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.promote_menu.name, type=self.promote_menu.type)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Promotion(bot))
