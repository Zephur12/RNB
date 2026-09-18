from __future__ import annotations

import logging
from typing import Optional

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from . import _shared

log = logging.getLogger("rnb_wardogs.provisioning")

CHANNEL_TYPE_MAP = {
    "text": discord.ChannelType.text,
    "voice": discord.ChannelType.voice,
    "voice_trigger": discord.ChannelType.voice,
    "forum": discord.ChannelType.forum,
}


def _format_list(items: list[str]) -> str:
    if not items:
        return "—"
    text = "\n".join(f"• {item}" for item in items)
    return text[:1024]


class Provisioning(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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
            config = _shared.load_config()
        except FileNotFoundError:
            await interaction.followup.send(f"Конфиг не найден: `{_shared.CONFIG_PATH}`")
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

    @staticmethod
    def _role_permissions(role_cfg: dict) -> discord.Permissions:
        perm_names = role_cfg.get("permissions", [])
        return discord.Permissions(**{name: True for name in perm_names})

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
            color_hex = role_cfg.get("color")
            color = discord.Color(int(color_hex.lstrip("#"), 16)) if color_hex else discord.Color.default()
            hoist = bool(role_cfg.get("hoist", False))
            mentionable = bool(role_cfg.get("mentionable", False))
            permissions = self._role_permissions(role_cfg)

            existing = roles_by_name.get(name)
            if existing is not None:
                # Как и с правами категорий — сверяем с конфигом при КАЖДОМ
                # запуске, не только при создании. Иначе правка permissions/
                # hoist/etc. в yaml никогда не долетала бы до уже созданной
                # роли (ровно на этом уже спотыкались с категориями).
                # Обратная сторона: если кто-то руками поменял права роли
                # через Discord UI в обход конфига — следующий /setup-server
                # это молча перезапишет обратно под конфиг.
                changed = (
                    existing.color != color
                    or existing.hoist != hoist
                    or existing.mentionable != mentionable
                    or existing.permissions != permissions
                )
                if changed:
                    try:
                        await existing.edit(
                            color=color,
                            hoist=hoist,
                            mentionable=mentionable,
                            permissions=permissions,
                            reason="РНБ /setup-server: синхронизация роли",
                        )
                        existed.append(f"Роль «{name}» — обновлена")
                    except discord.Forbidden:
                        failed.append(f"Роль «{name}»: не удалось обновить (Forbidden)")
                    except discord.HTTPException as exc:
                        failed.append(f"Роль «{name}»: ошибка обновления: {exc}")
                else:
                    existed.append(f"Роль «{name}»")
                continue

            try:
                role = await guild.create_role(
                    name=name,
                    color=color,
                    hoist=hoist,
                    mentionable=mentionable,
                    permissions=permissions,
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
            cat_restricted_to = cat_cfg.get("restricted_to")
            cat_overwrites = self._build_overwrites(guild, cat_restricted_to, roles_by_name, failed, cat_name)

            category = discord.utils.get(guild.categories, name=cat_name)

            if category is None:
                try:
                    category = await guild.create_category(
                        cat_name, overwrites=cat_overwrites, reason="РНБ /setup-server"
                    )
                    created.append(f"Категория «{cat_name}»")
                except discord.Forbidden:
                    failed.append(f"Категория «{cat_name}» — нет прав (Forbidden)")
                    continue
                except discord.HTTPException as exc:
                    failed.append(f"Категория «{cat_name}» — ошибка API: {exc}")
                    continue
            else:
                existed.append(f"Категория «{cat_name}»")
                # setup-server никогда не трогает существующие КАНАЛЫ, но права
                # существующих КАТЕГОРИЙ синхронизирует — иначе правка
                # restricted_to в конфиге никогда бы не долетала до уже
                # созданных категорий (создание — разовое, а не идемпотентная
                # проверка прав). Трогаем только когда restricted_to реально
                # задан в конфиге — категории без него (например "НАЧАЛО")
                # не трогаем, что бы на них ни настроили руками.
                if cat_restricted_to and category.overwrites != cat_overwrites:
                    try:
                        await category.edit(
                            overwrites=cat_overwrites, reason="РНБ /setup-server: синхронизация прав категории"
                        )
                        existed[-1] += " — права обновлены"
                    except discord.Forbidden:
                        failed.append(f"Категория «{cat_name}»: не удалось обновить права (Forbidden)")
                    except discord.HTTPException as exc:
                        failed.append(f"Категория «{cat_name}»: ошибка обновления прав: {exc}")

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
        name = _shared.normalize_channel_name(chan_cfg["name"])
        chan_type = CHANNEL_TYPE_MAP.get(chan_cfg.get("type", "text"), discord.ChannelType.text)

        chan_restricted_to = chan_cfg.get("restricted_to")
        overwrites = self._build_overwrites(guild, chan_restricted_to, roles_by_name, failed, name)

        existing = discord.utils.get(category.channels, name=name)
        if existing is not None:
            existed.append(f"Канал «{name}»")
            # Как и с категориями/ролями — если у канала в конфиге задан
            # restricted_to, сверяем его с уже существующим на сервере при
            # КАЖДОМ запуске, не только при создании. Без этого правка
            # restricted_to для уже существующего канала (например, когда
            # раньше открытый канал нужно закрыть) никогда бы не долетала.
            if chan_restricted_to and existing.overwrites != overwrites:
                try:
                    await existing.edit(
                        overwrites=overwrites, reason="РНБ /setup-server: синхронизация прав канала"
                    )
                    existed[-1] += " — права обновлены"
                except discord.Forbidden:
                    failed.append(f"Канал «{name}»: не удалось обновить права (Forbidden)")
                except discord.HTTPException as exc:
                    failed.append(f"Канал «{name}»: ошибка обновления прав: {exc}")
            return

        try:
            if chan_type == discord.ChannelType.voice:
                await guild.create_voice_channel(
                    name, category=category, overwrites=overwrites, reason="РНБ /setup-server"
                )
            elif chan_type == discord.ChannelType.forum:
                await guild.create_forum(
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

    @app_commands.command(
        name="sync-server",
        description="Найти и удалить роли/каналы, которых нет в конфиге (с подтверждением). Officer+.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def sync_server(self, interaction: discord.Interaction) -> None:
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

        allowed = config.get("sync", {}).get("allowed_roles", [])
        if not _shared.has_access(member, allowed):
            await interaction.response.send_message(
                "Команда доступна только Офицеру или Верховному Главнокомандующему.", ephemeral=True
            )
            return

        roles_to_delete, categories_to_delete, channels_to_delete = self._find_sync_candidates(guild, config)

        if not roles_to_delete and not categories_to_delete and not channels_to_delete:
            await interaction.response.send_message(
                "Сервер уже соответствует конфигу — удалять нечего.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="⚠️ Сверка с конфигом — предпросмотр удаления",
            description="Это необратимо. Подтверди или отмени ниже.",
            color=discord.Color.orange(),
        )
        if roles_to_delete:
            embed.add_field(
                name=f"Роли ({len(roles_to_delete)})",
                value=_format_list([r.name for r in roles_to_delete]),
                inline=False,
            )
        if categories_to_delete:
            embed.add_field(
                name=f"Категории целиком, с каналами внутри ({len(categories_to_delete)})",
                value=_format_list([c.name for c in categories_to_delete]),
                inline=False,
            )
        if channels_to_delete:
            embed.add_field(
                name=f"Каналы ({len(channels_to_delete)})",
                value=_format_list([c.name for c in channels_to_delete]),
                inline=False,
            )

        view = SyncConfirmView(invoker_id=member.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        await view.wait()

        if not view.confirmed:
            return

        deleted, failed = await self._delete_sync_candidates(roles_to_delete, categories_to_delete, channels_to_delete)

        result_embed = discord.Embed(
            title="Сверка завершена",
            color=discord.Color.orange() if failed else discord.Color.green(),
        )
        result_embed.add_field(name=f"🗑️ Удалено ({len(deleted)})", value=_format_list(deleted), inline=False)
        result_embed.add_field(name=f"❌ Не удалось ({len(failed)})", value=_format_list(failed), inline=False)
        await view.message.edit(embed=result_embed, view=None)

    @staticmethod
    def _find_sync_candidates(
        guild: discord.Guild, config: dict
    ) -> tuple[list[discord.Role], list[discord.CategoryChannel], list[discord.abc.GuildChannel]]:
        configured_role_names = {r["name"] for r in config.get("roles", [])}
        roles_to_delete = [
            r for r in guild.roles if not r.is_default() and not r.managed and r.name not in configured_role_names
        ]

        categories_config = config.get("categories", [])
        configured_category_names = {c["name"] for c in categories_config}
        dynamic_category_names = {c["name"] for c in categories_config if c.get("dynamic")}
        expected_channels_by_category = {
            c["name"]: {_shared.normalize_channel_name(ch["name"]) for ch in c.get("channels", [])}
            for c in categories_config
        }

        categories_to_delete: list[discord.CategoryChannel] = []
        channels_to_delete: list[discord.abc.GuildChannel] = []

        for category in guild.categories:
            if category.name not in configured_category_names:
                categories_to_delete.append(category)
                continue
            if category.name in dynamic_category_names:
                continue
            expected = expected_channels_by_category.get(category.name, set())
            for chan in category.channels:
                if chan.name not in expected:
                    channels_to_delete.append(chan)

        for chan in guild.channels:
            if isinstance(chan, discord.CategoryChannel):
                continue
            if chan.category is None:
                channels_to_delete.append(chan)

        return roles_to_delete, categories_to_delete, channels_to_delete

    @staticmethod
    async def _delete_sync_candidates(
        roles_to_delete: list[discord.Role],
        categories_to_delete: list[discord.CategoryChannel],
        channels_to_delete: list[discord.abc.GuildChannel],
    ) -> tuple[list[str], list[str]]:
        deleted: list[str] = []
        failed: list[str] = []

        for chan in channels_to_delete:
            try:
                await chan.delete(reason="РНБ /sync-server")
                deleted.append(f"Канал «{chan.name}»")
            except discord.Forbidden:
                failed.append(f"Канал «{chan.name}» — нет прав (Forbidden)")
            except discord.HTTPException as exc:
                failed.append(f"Канал «{chan.name}» — ошибка API: {exc}")

        for category in categories_to_delete:
            for chan in list(category.channels):
                try:
                    await chan.delete(reason="РНБ /sync-server (внутри удаляемой категории)")
                except discord.HTTPException as exc:
                    failed.append(f"Канал «{chan.name}» в «{category.name}» — ошибка API: {exc}")
            try:
                await category.delete(reason="РНБ /sync-server")
                deleted.append(f"Категория «{category.name}»")
            except discord.Forbidden:
                failed.append(f"Категория «{category.name}» — нет прав (Forbidden)")
            except discord.HTTPException as exc:
                failed.append(f"Категория «{category.name}» — ошибка API: {exc}")

        for role in roles_to_delete:
            try:
                await role.delete(reason="РНБ /sync-server")
                deleted.append(f"Роль «{role.name}»")
            except discord.Forbidden:
                failed.append(f"Роль «{role.name}» — нет прав (Forbidden)")
            except discord.HTTPException as exc:
                failed.append(f"Роль «{role.name}» — ошибка API: {exc}")

        return deleted, failed

    @sync_server.error
    async def sync_server_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.exception("Ошибка в /sync-server", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class SyncConfirmView(discord.ui.View):
    def __init__(self, invoker_id: int, timeout: float = 60) -> None:
        super().__init__(timeout=timeout)
        self.invoker_id = invoker_id
        self.confirmed: Optional[bool] = None
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Эта кнопка не для тебя.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Подтвердить удаление", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = False
        self._disable_all()
        await interaction.response.edit_message(content="Отменено, ничего не удалено.", embed=None, view=self)
        self.stop()

    async def on_timeout(self) -> None:
        self.confirmed = False
        self._disable_all()
        if self.message is not None:
            try:
                await self.message.edit(content="Время вышло, ничего не удалено.", embed=None, view=self)
            except discord.HTTPException:
                pass

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Provisioning(bot))
