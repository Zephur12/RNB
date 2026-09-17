from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from . import _shared

log = logging.getLogger("rnb_wardogs.ops")

SQUAD_CHOICES = [app_commands.Choice(name=label, value=role_name) for label, role_name in _shared.SQUADS]
SQUAD_SLUGS = {role_name: label.lower() for label, role_name in _shared.SQUADS}


class OpRsvpView(discord.ui.View):
    """Persistent (survives restarts, one shared custom_id) 'Иду' toggle button.

    Deliberately stateless: the participant list lives only in the message's
    own embed field, not in a separate in-memory dict — so there's nothing to
    lose on a bot restart, at the cost of not knowing who clicked beyond
    what's already rendered.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Иду", style=discord.ButtonStyle.success, custom_id="rnb:op_rsvp")
    async def rsvp(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.message is None or not interaction.message.embeds:
            await interaction.response.send_message("Не удалось найти анонс операции.", ephemeral=True)
            return

        embed = interaction.message.embeds[0]
        field_index = next((i for i, f in enumerate(embed.fields) if f.name.startswith("Участники")), None)
        current = embed.fields[field_index].value if field_index is not None else "—"
        mentions = [] if current == "—" else current.split("\n")

        mention = interaction.user.mention
        if mention in mentions:
            mentions.remove(mention)  # повторный клик — выйти из списка (toggle off)
        else:
            mentions.append(mention)

        value = "\n".join(mentions)[:1024] if mentions else "—"
        name = f"Участники ({len(mentions)})"

        if field_index is not None:
            embed.set_field_at(field_index, name=name, value=value, inline=False)
        else:
            embed.add_field(name=name, value=value, inline=False)

        await interaction.response.edit_message(embed=embed)


class Ops(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active_watchers: dict[str, asyncio.Task] = {}
        self.bot.add_view(OpRsvpView())

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._reconcile_pending_ops()

    async def _reconcile_pending_ops(self) -> None:
        try:
            config = _shared.load_config()
        except (FileNotFoundError, yaml.YAMLError) as exc:
            log.warning("Не удалось прочитать конфиг для reconciliation операций: %s", exc)
            return

        temp_category_name = config.get("ops", {}).get("temp_category")
        delay = config.get("ops", {}).get("cleanup_delay_seconds", 300)

        for guild in self.bot.guilds:
            category = discord.utils.get(guild.categories, name=temp_category_name)
            if category is None:
                continue

            voice_by_name = {c.name: c for c in category.voice_channels}
            text_by_name = {c.name: c for c in category.text_channels}

            for name, voice_channel in voice_by_name.items():
                text_channel = text_by_name.get(name)
                if text_channel is None or name in self._active_watchers:
                    continue
                log.info("Восстанавливаю слежку за операцией «%s» после рестарта", name)
                self._start_watcher(name, voice_channel, text_channel, delay)

    def _start_watcher(
        self, name: str, voice_channel: discord.VoiceChannel, text_channel: discord.TextChannel, delay: int
    ) -> None:
        task = asyncio.create_task(self._watch_and_cleanup(name, voice_channel, text_channel, delay))
        self._active_watchers[name] = task

    async def _watch_and_cleanup(
        self, name: str, voice_channel: discord.VoiceChannel, text_channel: discord.TextChannel, delay: int
    ) -> None:
        try:
            while True:
                await asyncio.sleep(delay)

                refreshed = self.bot.get_channel(voice_channel.id)
                if refreshed is None:
                    return  # уже удалён (например, через /end-op)

                if len(refreshed.members) == 0:
                    await self._delete_op_channels(voice_channel, text_channel)
                    return
        except asyncio.CancelledError:
            raise
        finally:
            self._active_watchers.pop(name, None)

    @staticmethod
    async def _delete_op_channels(
        voice_channel: Optional[discord.VoiceChannel], text_channel: Optional[discord.TextChannel]
    ) -> None:
        for channel in (voice_channel, text_channel):
            if channel is None:
                continue
            try:
                await channel.delete(reason="РНБ: операция завершена")
            except discord.HTTPException:
                pass

    @app_commands.command(name="start-op", description="Начать операцию: создать временные каналы сбора")
    @app_commands.describe(
        server="Игровой сервер: регион + номер, например 'EU 3' или 'NA 12'",
        squad="Отряд (опционально)",
        map="Карта (опционально)",
        note="Заметка, например время сбора",
    )
    @app_commands.choices(squad=SQUAD_CHOICES)
    async def start_op(
        self,
        interaction: discord.Interaction,
        server: str,
        squad: Optional[app_commands.Choice[str]] = None,
        map: Optional[str] = None,
        note: Optional[str] = None,
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

        ops_cfg = config.get("ops", {})
        report_channel_name = config.get("report", {}).get("channel")
        allowed_role_names = _shared.allowed_role_names(config, report_channel_name)
        if not _shared.has_access(member, allowed_role_names):
            await interaction.response.send_message(
                "Команда доступна только Главе отряда, Офицеру или выше.", ephemeral=True
            )
            return

        temp_category_name = ops_cfg.get("temp_category")
        announce_channel_name = ops_cfg.get("announce_channel")
        delay = ops_cfg.get("cleanup_delay_seconds", 300)

        category = discord.utils.get(guild.categories, name=temp_category_name)
        announce_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(announce_channel_name)
        )
        if category is None or announce_channel is None:
            await interaction.response.send_message(
                f"Категория «{temp_category_name}» или канал «{announce_channel_name}» не найдены. "
                "Запусти `/setup-server`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        squad_slug = SQUAD_SLUGS.get(squad.value, "операция") if squad else "операция"
        op_name = f"операция-{squad_slug}-{secrets.token_hex(2)}"

        try:
            text_channel = await guild.create_text_channel(op_name, category=category, reason="РНБ /start-op")
            voice_channel = await guild.create_voice_channel(op_name, category=category, reason="РНБ /start-op")
        except discord.Forbidden:
            await interaction.followup.send("Нет прав создавать каналы (Forbidden).", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"Ошибка API при создании каналов: `{exc}`", ephemeral=True)
            return

        squad_role = discord.utils.get(guild.roles, name=squad.value) if squad else None

        embed = discord.Embed(
            title="🎯 Операция начата",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Сервер", value=server, inline=True)
        embed.add_field(name="Отряд", value=squad.name if squad else "Сборная / без отряда", inline=True)
        embed.add_field(name="Карта", value=map or "—", inline=True)
        if note:
            embed.add_field(name="Заметка", value=note, inline=False)
        embed.add_field(
            name="Каналы",
            value=f"💬 {text_channel.mention}\n🔊 {voice_channel.mention}",
            inline=False,
        )
        embed.add_field(name="Участники (0)", value="—", inline=False)
        embed.set_footer(text=f"Начал: {member.display_name}", icon_url=member.display_avatar.url)

        content = squad_role.mention if squad_role else None
        try:
            await announce_channel.send(content=content, embed=embed, view=OpRsvpView())
        except discord.Forbidden:
            log.warning("Нет прав постить анонс операции в «%s»", announce_channel_name)

        self._start_watcher(op_name, voice_channel, text_channel, delay)

        await interaction.followup.send(
            f"Операция начата: {text_channel.mention} / {voice_channel.mention}. "
            f"Анонс в {announce_channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="end-op", description="Досрочно завершить операцию (запускать в её текстовом канале)")
    async def end_op(self, interaction: discord.Interaction) -> None:
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
                "Команда доступна только Главе отряда, Офицеру или выше.", ephemeral=True
            )
            return

        temp_category_name = config.get("ops", {}).get("temp_category")
        channel = interaction.channel
        if (
            not isinstance(channel, discord.TextChannel)
            or channel.category is None
            or channel.category.name != temp_category_name
        ):
            await interaction.response.send_message(
                f"Эту команду нужно запускать внутри временного текстового канала операции "
                f"(категория «{temp_category_name}»).",
                ephemeral=True,
            )
            return

        voice_channel = discord.utils.get(channel.category.voice_channels, name=channel.name)

        task = self._active_watchers.pop(channel.name, None)
        if task is not None:
            task.cancel()

        await interaction.response.send_message("Операция завершена, удаляю каналы.", ephemeral=True)
        await self._delete_op_channels(voice_channel, channel)

    @start_op.error
    @end_op.error
    async def ops_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.exception("Ошибка в команде операций", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ops(bot))
