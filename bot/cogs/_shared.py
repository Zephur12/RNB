from __future__ import annotations

import re
from pathlib import Path

import discord
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "server_structure.yaml"

_WHITESPACE_RE = re.compile(r"\s+")

# Single source of truth for the 5 squads: (short label, full bilingual role
# name). Used by ops.py (/start-op choices + slugs) and onboarding.py (squad
# picker buttons) — was duplicated between them, now isn't.
SQUADS: list[tuple[str, str]] = [
    ("Штурм", "Штурм | Assault"),
    ("Логистика", "Логистика | Logistics"),
    ("Разведка", "Разведка | Recon"),
    ("Транспорт", "Транспорт | Transport"),
    ("Поддержка", "Поддержка | Support"),
]

READY_ROLE_NAME = "🎮 Готов играть сейчас"


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_channel_name(name: str) -> str:
    """Mirror Discord's own text/voice channel name normalization (lowercase,
    whitespace -> hyphens) so config authors don't have to pre-normalize names
    by hand, and so comparisons against real channel objects always match.
    Category names are NOT affected by this on Discord's side — don't use this
    for category names.
    """
    return _WHITESPACE_RE.sub("-", name.strip()).lower()


def allowed_role_names(config: dict, channel_name: str) -> list[str]:
    """Roles listed in `restricted_to` for the channel with this name in `categories`."""
    for category in config.get("categories", []):
        for chan in category.get("channels", []):
            if chan.get("name") == channel_name:
                return chan.get("restricted_to", [])
    return []


def has_access(member: discord.Member, allowed_role_names: list[str]) -> bool:
    if member.guild_permissions.administrator:
        return True
    member_role_names = {r.name for r in member.roles}
    return bool(member_role_names & set(allowed_role_names))


def channel_ref(guild: discord.Guild, name: str) -> str:
    """A clickable channel mention if the channel exists, else a plain-text
    fallback (`#name`) so messages never show a raw broken reference."""
    channel = discord.utils.get(guild.text_channels, name=normalize_channel_name(name))
    return channel.mention if channel is not None else f"#{name}"


async def replace_pinned_by(channel, *, marker_title: str, **send_kwargs):
    """Delete any previous bot message in `channel` whose embed title matches
    `marker_title`, then post+pin a new one — so re-running a content-posting
    command updates in place instead of accumulating duplicate pinned
    messages. Works for any Messageable that supports .pins()/.send()/pin()
    (TextChannel today; forum starter posts pin differently, see
    replace_pinned_thread below)."""
    async for old in channel.pins():
        if old.author.bot and old.embeds and old.embeds[0].title == marker_title:
            await old.unpin(reason="РНБ: обновление контента")
            await old.delete()

    message = await channel.send(**send_kwargs)
    await message.pin(reason="РНБ: обновление контента")
    return message


async def replace_pinned_thread(forum: discord.ForumChannel, *, marker_title: str, **create_kwargs):
    """Same idea as replace_pinned_by, but for a forum channel's starter
    post: a forum has no channel-level .pins() for messages — instead a
    whole THREAD gets pinned (Thread.edit(pinned=True)). Finds a previous
    bot-authored pinned thread whose title matches, unpins+deletes it, then
    creates+pins a new one. Returns the created discord.Thread."""
    for thread in forum.threads:
        if thread.flags.pinned and thread.owner_id == forum.guild.me.id and thread.name == marker_title:
            await thread.delete()

    result = await forum.create_thread(name=marker_title, **create_kwargs)
    thread = result.thread
    await thread.edit(pinned=True, reason="РНБ: обновление стартового поста")
    return thread


class ReadyToggleButton(discord.ui.Button):
    """Standalone toggle unrelated to onboarding/squad choice — lives here (not
    in onboarding.py or ops.py) because both cogs attach it to their own
    persistent panel, and onboarding.py already imports ops.py, so a
    definition in either would create a circular import."""

    def __init__(self, *, row: int | None = None) -> None:
        super().__init__(
            label=READY_ROLE_NAME,
            style=discord.ButtonStyle.secondary,
            custom_id="rnb:ready_toggle",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
            return

        role = discord.utils.get(guild.roles, name=READY_ROLE_NAME)
        if role is None:
            await interaction.response.send_message(
                f"Роль «{READY_ROLE_NAME}» не найдена. Попроси Офицера прогнать `/setup-server`.", ephemeral=True
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="РНБ: снял отметку «готов играть»")
                await interaction.response.send_message("Убрал отметку «Готов играть сейчас».", ephemeral=True)
            else:
                await member.add_roles(role, reason="РНБ: отметил «готов играть»")
                await interaction.response.send_message(
                    "Отмечено! Тебя можно позвать пингом в #сбор-на-операцию на внеплановую игру.",
                    ephemeral=True,
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Не хватает прав — роль бота должна быть выше этой роли в списке ролей сервера.", ephemeral=True
            )
