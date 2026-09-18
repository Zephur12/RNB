from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

import discord
import yaml
from discord.ext import commands

from . import _shared

log = logging.getLogger("rnb_wardogs.ops")

SQUAD_SLUGS = {role_name: label.lower() for label, role_name in _shared.SQUADS}

NO_SQUAD_VALUE = "__none__"


class OpsConfigError(Exception):
    """Raised when ops.temp_category / ops.announce_channel are missing on
    the server — surfaced to the user as a friendly message, not a crash."""


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


class EndOpView(discord.ui.View):
    """Posted once inside each operation's own temp text channel. Not
    registered via bot.add_view() — doesn't survive a bot restart, unlike
    OpRsvpView/StartOpEntryView which live on long-lived pinned messages.
    Accepted trade-off: if the bot restarts mid-op, the button on that one
    channel goes dead, but the channel still self-cleans via the normal
    empty-voice watcher (reconciled on_ready same as always) — nothing is
    stuck forever, worst case someone just waits instead of clicking early.
    """

    def __init__(self, op_name: str) -> None:
        super().__init__(timeout=None)
        self.op_name = op_name
        button = discord.ui.Button(
            label="🛑 Завершить операцию",
            style=discord.ButtonStyle.danger,
            custom_id=f"rnb:end_op:{op_name}",
        )
        button.callback = self._end
        self.add_item(button)

    async def _end(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
            return

        try:
            config = _shared.load_config()
        except (FileNotFoundError, yaml.YAMLError) as exc:
            await interaction.response.send_message(f"Ошибка чтения конфига: `{exc}`", ephemeral=True)
            return

        report_channel_name = config.get("report", {}).get("channel")
        allowed = _shared.allowed_role_names(config, report_channel_name)
        if not _shared.has_access(member, allowed):
            await interaction.response.send_message(
                "Доступно только Главе отряда, Офицеру или выше.", ephemeral=True
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
                "Эта кнопка работает только внутри канала операции.", ephemeral=True
            )
            return

        cog = interaction.client.get_cog("Ops")
        voice_channel = discord.utils.get(channel.category.voice_channels, name=channel.name)
        task = cog._active_watchers.pop(channel.name, None) if cog else None
        if task is not None:
            task.cancel()

        await interaction.response.send_message("Операция завершена, удаляю каналы.", ephemeral=True)
        await Ops._delete_op_channels(voice_channel, channel)


class StartOpModal(discord.ui.Modal, title="Начать операцию"):
    server_id = discord.ui.TextInput(label="Server ID (6 цифр)", min_length=6, max_length=6, placeholder="123456")
    map_name = discord.ui.TextInput(label="Карта (необязательно)", required=False, max_length=100)

    def __init__(self, squad_value: Optional[str]) -> None:
        super().__init__()
        self.squad_value = squad_value

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта форма работает только на сервере.", ephemeral=True)
            return

        server_id_value = str(self.server_id.value).strip()
        if not server_id_value.isdigit() or len(server_id_value) != 6:
            await interaction.response.send_message(
                f"Server ID должен быть ровно 6 цифр, получено: «{server_id_value}».", ephemeral=True
            )
            return

        cog = interaction.client.get_cog("Ops")
        if cog is None:
            await interaction.response.send_message("Внутренняя ошибка: cog Ops не загружен.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        map_value = str(self.map_name.value).strip() if self.map_name.value else None
        try:
            text_channel, voice_channel, announce_msg = await cog.create_operation(
                guild, member, self.squad_value, server_id_value, map_value
            )
        except OpsConfigError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send("Нет прав создавать каналы (Forbidden).", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"Ошибка API при создании операции: `{exc}`", ephemeral=True)
            return

        await interaction.followup.send(
            f"Операция начата: {text_channel.mention} / {voice_channel.mention}. Анонс: {announce_msg.jump_url}",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Ошибка в StartOpModal", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class SquadSelectView(discord.ui.View):
    """Short-lived (not persistent — this ephemeral step only needs to
    survive the few seconds until the user picks an option)."""

    def __init__(self) -> None:
        super().__init__(timeout=180)
        options = [discord.SelectOption(label=label, value=role_name) for label, role_name in _shared.SQUADS]
        options.append(discord.SelectOption(label="Без привязки к отряду", value=NO_SQUAD_VALUE))
        select = discord.ui.Select(placeholder="Выбери отряд для операции", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        squad_value = None if value == NO_SQUAD_VALUE else value
        await interaction.response.send_modal(StartOpModal(squad_value=squad_value))


class StartOpEntryView(discord.ui.View):
    """Persistent entry point — posted in the ops-explainer message in
    #сбор-на-операцию. Registered via bot.add_view() in Ops.__init__ so it
    keeps working across restarts. Also carries the "Готов играть сейчас"
    toggle (moved here from the onboarding contract panel — thematically
    this is where it belongs, next to the button that starts a game)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(_shared.ReadyToggleButton(row=1))

    @discord.ui.button(label="🚀 Начать операцию", style=discord.ButtonStyle.primary, custom_id="rnb:start_op_entry")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
            return

        try:
            config = _shared.load_config()
        except (FileNotFoundError, yaml.YAMLError) as exc:
            await interaction.response.send_message(f"Ошибка чтения конфига: `{exc}`", ephemeral=True)
            return

        report_channel_name = config.get("report", {}).get("channel")
        allowed = _shared.allowed_role_names(config, report_channel_name)
        if not _shared.has_access(member, allowed):
            await interaction.response.send_message(
                "Доступно только Главе отряда, Офицеру или выше.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Выбери отряд для операции:", view=SquadSelectView(), ephemeral=True
        )


class Ops(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active_watchers: dict[str, asyncio.Task] = {}
        self.bot.add_view(OpRsvpView())
        self.bot.add_view(StartOpEntryView())

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
                    return  # уже удалён (например, кнопкой "Завершить операцию")

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

    async def create_operation(
        self,
        guild: discord.Guild,
        member: discord.Member,
        squad_value: Optional[str],
        server_id: str,
        map_name: Optional[str],
    ) -> tuple[discord.TextChannel, discord.VoiceChannel, discord.Message]:
        """Core operation-creation logic, shared by the button+modal flow.
        Raises OpsConfigError if ops.temp_category/announce_channel aren't
        on the server yet (caller should tell the user to run /setup-server).
        """
        config = _shared.load_config()
        ops_cfg = config.get("ops", {})
        temp_category_name = ops_cfg.get("temp_category")
        announce_channel_name = ops_cfg.get("announce_channel")
        delay = ops_cfg.get("cleanup_delay_seconds", 300)

        category = discord.utils.get(guild.categories, name=temp_category_name)
        announce_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(announce_channel_name)
        )
        if category is None or announce_channel is None:
            raise OpsConfigError(
                f"Категория «{temp_category_name}» или канал «{announce_channel_name}» не найдены. "
                "Попроси Офицера прогнать `/setup-server`."
            )

        squad_slug = SQUAD_SLUGS.get(squad_value, "операция") if squad_value else "операция"
        op_name = f"операция-{squad_slug}-{secrets.token_hex(2)}"

        text_channel = await guild.create_text_channel(op_name, category=category, reason="РНБ: начата операция")
        voice_channel = await guild.create_voice_channel(op_name, category=category, reason="РНБ: начата операция")

        squad_role = discord.utils.get(guild.roles, name=squad_value) if squad_value else None
        squad_label = next((label for label, name in _shared.SQUADS if name == squad_value), None)

        embed = discord.Embed(
            title="🎯 Операция начата",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Server ID",
            value=f"`{server_id}` — зайти: Обозреватель серверов → Join By ID",
            inline=False,
        )
        embed.add_field(name="Отряд", value=squad_label or "Сборная / без отряда", inline=True)
        embed.add_field(name="Карта", value=map_name or "—", inline=True)
        embed.add_field(
            name="Каналы",
            value=f"💬 {text_channel.mention}\n🔊 {voice_channel.mention}",
            inline=False,
        )
        embed.add_field(name="Участники (0)", value="—", inline=False)
        embed.set_footer(text=f"Начал: {member.display_name}", icon_url=member.display_avatar.url)

        content = squad_role.mention if squad_role else None
        announce_msg = await announce_channel.send(content=content, embed=embed, view=OpRsvpView())

        try:
            await text_channel.send(
                "Панель этой операции. Когда закончите (или собрались зря) — жмите кнопку ниже, "
                "не дожидаясь автоочистки.",
                view=EndOpView(op_name),
            )
        except discord.Forbidden:
            pass

        self._start_watcher(op_name, voice_channel, text_channel, delay)
        return text_channel, voice_channel, announce_msg


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ops(bot))
