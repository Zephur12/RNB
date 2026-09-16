from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import discord
import yaml
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("rnb_wardogs.provisioning")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "server_structure.yaml"

CHANNEL_TYPE_MAP = {
    "text": discord.ChannelType.text,
    "voice": discord.ChannelType.voice,
    "voice_trigger": discord.ChannelType.voice,
}


def _format_list(items: list[str]) -> str:
    if not items:
        return "—"
    text = "\n".join(f"• {item}" for item in items)
    return text[:1024]


class Provisioning(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _load_config(self) -> dict:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    @app_commands.command(
        name="setup-server",
        description="Создать структуру сервера РНБ (роли/категории/каналы) из конфига. Идемпотентно.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_server(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Эта команда доступна только на сервере.", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
            await interaction.response.send_message(
                "Команда доступна только владельцу или администратору сервера.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            config = self._load_config()
        except FileNotFoundError:
            await interaction.followup.send(f"Конфиг не найден: `{CONFIG_PATH}`")
            return
        except yaml.YAMLError as exc:
            await interaction.followup.send(f"Ошибка чтения yaml: `{exc}`")
            return

        created: list[str] = []
        existed: list[str] = []
        failed: list[str] = []

        roles_by_name = await self._ensure_roles(guild, config.get("roles", []), created, existed, failed)
        await self._ensure_categories_and_channels(
            guild, config.get("categories", []), roles_by_name, created, existed, failed
        )

        embed = self._build_report_embed(created, existed, failed)
        await interaction.followup.send(embed=embed)

    async def _ensure_roles(
        self,
        guild: discord.Guild,
        roles_config: list[dict],
        created: list[str],
        existed: list[str],
        failed: list[str],
    ) -> dict[str, discord.Role]:
        roles_by_name: dict[str, discord.Role] = {r.name: r for r in guild.roles}

        for role_cfg in roles_config:
            name = role_cfg["name"]
            if name in roles_by_name:
                existed.append(f"Роль «{name}»")
                continue

            color_hex = role_cfg.get("color")
            color = discord.Color(int(color_hex.lstrip("#"), 16)) if color_hex else discord.Color.default()

            try:
                role = await guild.create_role(
                    name=name,
                    color=color,
                    hoist=bool(role_cfg.get("hoist", False)),
                    reason="РНБ /setup-server",
                )
                roles_by_name[name] = role
                created.append(f"Роль «{name}»")
            except discord.Forbidden:
                failed.append(f"Роль «{name}» — нет прав (Forbidden)")
            except discord.HTTPException as exc:
                failed.append(f"Роль «{name}» — ошибка API: {exc}")

        return roles_by_name

    async def _ensure_categories_and_channels(
        self,
        guild: discord.Guild,
        categories_config: list[dict],
        roles_by_name: dict[str, discord.Role],
        created: list[str],
        existed: list[str],
        failed: list[str],
    ) -> None:
        for cat_cfg in categories_config:
            cat_name = cat_cfg["name"]
            category = discord.utils.get(guild.categories, name=cat_name)

            if category is None:
                try:
                    category = await guild.create_category(cat_name, reason="РНБ /setup-server")
                    created.append(f"Категория «{cat_name}»")
                except discord.Forbidden:
                    failed.append(f"Категория «{cat_name}» — нет прав (Forbidden)")
                    continue
                except discord.HTTPException as exc:
                    failed.append(f"Категория «{cat_name}» — ошибка API: {exc}")
                    continue
            else:
                existed.append(f"Категория «{cat_name}»")

            for chan_cfg in cat_cfg.get("channels", []):
                await self._ensure_channel(guild, category, chan_cfg, roles_by_name, created, existed, failed)

    async def _ensure_channel(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        chan_cfg: dict,
        roles_by_name: dict[str, discord.Role],
        created: list[str],
        existed: list[str],
        failed: list[str],
    ) -> None:
        name = chan_cfg["name"]
        chan_type = CHANNEL_TYPE_MAP.get(chan_cfg.get("type", "text"), discord.ChannelType.text)

        existing = discord.utils.get(category.channels, name=name)
        if existing is not None:
            existed.append(f"Канал «{name}»")
            return

        overwrites = self._build_overwrites(guild, chan_cfg.get("restricted_to"), roles_by_name, failed, name)

        try:
            if chan_type == discord.ChannelType.voice:
                await guild.create_voice_channel(
                    name, category=category, overwrites=overwrites, reason="РНБ /setup-server"
                )
            else:
                await guild.create_text_channel(
                    name, category=category, overwrites=overwrites, reason="РНБ /setup-server"
                )
            created.append(f"Канал «{name}»")
        except discord.Forbidden:
            failed.append(f"Канал «{name}» — нет прав (Forbidden)")
        except discord.HTTPException as exc:
            failed.append(f"Канал «{name}» — ошибка API: {exc}")

    def _build_overwrites(
        self,
        guild: discord.Guild,
        restricted_to: Optional[list[str]],
        roles_by_name: dict[str, discord.Role],
        failed: list[str],
        channel_name: str,
    ) -> dict:
        if not restricted_to:
            return {}

        overwrites: dict = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        for role_name in restricted_to:
            role = roles_by_name.get(role_name)
            if role is None:
                failed.append(f"Канал «{channel_name}»: роль «{role_name}» из restricted_to не найдена")
                continue
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        return overwrites

    @staticmethod
    def _build_report_embed(created: list[str], existed: list[str], failed: list[str]) -> discord.Embed:
        embed = discord.Embed(
            title="Провижининг сервера РНБ",
            color=discord.Color.orange() if failed else discord.Color.green(),
        )
        embed.add_field(name=f"✅ Создано ({len(created)})", value=_format_list(created), inline=False)
        embed.add_field(name=f"➖ Уже было ({len(existed)})", value=_format_list(existed), inline=False)
        embed.add_field(name=f"❌ Не удалось ({len(failed)})", value=_format_list(failed), inline=False)
        return embed

    @setup_server.error
    async def setup_server_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "Команда доступна только владельцу или администратору сервера.", ephemeral=True
            )
            return

        log.exception("Ошибка в /setup-server", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Provisioning(bot))
