from __future__ import annotations

import logging

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from . import _shared, ops

log = logging.getLogger("rnb_wardogs.onboarding")

RECRUIT_ROLE_NAME = "Новобранец | Recruit"
READY_ROLE_NAME = "🎮 Готов играть сейчас"
ONBOARDING_CHANNEL_NAME = "правила-verification"
OPS_ANNOUNCE_CHANNEL_NAME = "сбор-на-операцию"
TACTICS_CHANNEL_NAME = "тактика-обсуждение"
CHAT_CHANNEL_NAME = "💬-болталка-chat"

WELCOME_EMBED_TITLE = "📜 Контракт РНБ"
OPS_EXPLAINER_TITLE = "📋 Как здесь появляются операции"

SQUAD_DESCRIPTIONS = [
    ("⚔️ Штурм | Assault", "первая линия, берёт и держит точки"),
    ("📦 Логистика | Logistics", "снабжение, чтобы штурм не остался без ресурсов"),
    ("🔍 Разведка | Recon", "идёт впереди, докладывает обстановку"),
    ("🚚 Транспорт | Transport", "перевозит людей и технику по вызову"),
    ("🛠 Поддержка | Support", "прикрывает отход, огневая поддержка"),
]


def _build_welcome_embed(guild: discord.Guild) -> discord.Embed:
    ops_ref = _shared.channel_ref(guild, OPS_ANNOUNCE_CHANNEL_NAME)
    tactics_ref = _shared.channel_ref(guild, TACTICS_CHANNEL_NAME)

    embed = discord.Embed(
        title=WELCOME_EMBED_TITLE,
        description=(
            "Клан синих (Lonestar) в WarDogs. Нажатием кнопки отряда ниже ты ставишь подпись "
            "под этим контрактом:\n"
            "— слушаешь приказы своего Главы отряда на операции;\n"
            "— не сливаешь стратегию клана посторонним;\n"
            "— уважаешь остальных, даже когда не согласен.\n\n"
            "Полные условия — в закреплённых правилах выше. Дальше — 3 шага:\n"
            "1️⃣ Выбери отряд и подпишись (30 сек)\n"
            f"2️⃣ Загляни в {ops_ref} — там анонсы ближайших игр, жми «✅ Иду»\n"
            "3️⃣ В назначенное время — голосовой канал операции, дальше вместе в саму игру"
        ),
        color=discord.Color.blurple(),
    )
    for name, desc in SQUAD_DESCRIPTIONS:
        embed.add_field(name=name, value=desc, inline=False)

    embed.add_field(
        name="Не уверен, что выбрать?",
        value=(
            "Разведка и Логистика — хороший старт, если WarDogs для тебя новая игра "
            "(учишь карту без давления первой линии). Штурм — если уже есть опыт в тактических "
            "шутерах (Arma, Tarkov, Squad и т.п.) и хочешь сразу в бой."
        ),
        inline=False,
    )
    embed.add_field(
        name="📚 Хочешь знать больше?",
        value=f"Тактика клана подробно — в {tactics_ref}, необязательно читать сейчас.",
        inline=False,
    )
    return embed


def _build_ops_explainer_embed(guild: discord.Guild) -> discord.Embed:
    chat_ref = _shared.channel_ref(guild, CHAT_CHANNEL_NAME)
    return discord.Embed(
        title=OPS_EXPLAINER_TITLE,
        description=(
            "Здесь появляются анонсы операций — их создают Главы отрядов/Офицеры кнопкой "
            "«🚀 Начать операцию» под этим сообщением.\n\n"
            f"Если сейчас пусто — операция ещё не назначена, загляни позже или спроси в {chat_ref}.\n\n"
            "Конкретный игровой сервер объявляется в каждом анонсе отдельно (Server ID) — "
            "фиксированного постоянного сервера у нас пока нет."
        ),
        color=discord.Color.blurple(),
    )


class SquadButton(discord.ui.Button):
    def __init__(self, label: str, role_name: str) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"rnb:onboard_squad:{role_name}",
        )
        self.role_name = role_name

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
            return

        new_role = discord.utils.get(guild.roles, name=self.role_name)
        if new_role is None:
            await interaction.response.send_message(
                f"Роль «{self.role_name}» не найдена на сервере. Попроси Офицера прогнать `/setup-server`.",
                ephemeral=True,
            )
            return

        squad_role_names = {name for _, name in _shared.SQUADS}
        previous_squad_roles = [r for r in member.roles if r.name in squad_role_names and r != new_role]
        recruit_role = discord.utils.get(guild.roles, name=RECRUIT_ROLE_NAME)

        roles_to_add = [] if new_role in member.roles else [new_role]
        if recruit_role is not None and recruit_role not in member.roles:
            roles_to_add.append(recruit_role)

        try:
            if previous_squad_roles:
                await member.remove_roles(*previous_squad_roles, reason="РНБ: смена отряда через /post-onboarding")
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="РНБ: выбор отряда через /post-onboarding")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Не хватает прав выдать/снять роль — проверь, что роль бота стоит ВЫШЕ ролей отрядов "
                "в списке ролей сервера.",
                ephemeral=True,
            )
            return

        ops_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(OPS_ANNOUNCE_CHANNEL_NAME)
        )
        if ops_channel is not None:
            link_view = discord.ui.View()
            link_view.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    url=ops_channel.jump_url,
                    label="→ Перейти в сбор-на-операцию",
                )
            )
            await interaction.response.send_message(
                f"Подпись принята — ты в отряде «{self.label}»! Там объявляются ближайшие игры:",
                view=link_view,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Подпись принята — ты в отряде «{self.label}»! Канал «{OPS_ANNOUNCE_CHANNEL_NAME}» не найден — "
                "попроси Офицера прогнать `/setup-server`.",
                ephemeral=True,
            )


class ReadyToggleButton(discord.ui.Button):
    """Standalone toggle, unrelated to squad choice — just marks 'available to
    play right now' so others can @-mention the role in #сбор-на-операцию for
    an impromptu game, separate from the planned /start-op flow."""

    def __init__(self) -> None:
        super().__init__(
            label=READY_ROLE_NAME,
            style=discord.ButtonStyle.secondary,
            custom_id="rnb:ready_toggle",
            row=1,
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


class SquadPickView(discord.ui.View):
    """Persistent (survives restart) squad-picker posted once via /post-onboarding."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        for label, role_name in _shared.SQUADS:
            self.add_item(SquadButton(label, role_name))
        self.add_item(ReadyToggleButton())


class Onboarding(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bot.add_view(SquadPickView())

    @app_commands.command(
        name="post-onboarding",
        description="Обновить онбординг и explainer операций (закреплённые сообщения). Officer+.",
    )
    async def post_onboarding(self, interaction: discord.Interaction) -> None:
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

        allowed = config.get("sync", {}).get("allowed_roles", [])
        if not _shared.has_access(member, allowed):
            await interaction.response.send_message(
                "Команда доступна только Офицеру или Верховному Главнокомандующему.", ephemeral=True
            )
            return

        target_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(ONBOARDING_CHANNEL_NAME)
        )
        if target_channel is None:
            await interaction.response.send_message(
                f"Канал «{ONBOARDING_CHANNEL_NAME}» не найден. Запусти `/setup-server`.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            await _shared.replace_pinned_by(
                target_channel,
                marker_title=WELCOME_EMBED_TITLE,
                embed=_build_welcome_embed(guild),
                view=SquadPickView(),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Нет прав постить/закреплять в {target_channel.mention} (Forbidden).", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"Ошибка при публикации в {target_channel.mention}: `{exc}`", ephemeral=True)
            return

        results = [f"Опубликовано и закреплено в {target_channel.mention}."]

        ops_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(OPS_ANNOUNCE_CHANNEL_NAME)
        )
        if ops_channel is None:
            results.append(f"⚠️ Канал «{OPS_ANNOUNCE_CHANNEL_NAME}» не найден — explainer туда не запощен.")
        else:
            try:
                await _shared.replace_pinned_by(
                    ops_channel,
                    marker_title=OPS_EXPLAINER_TITLE,
                    embed=_build_ops_explainer_embed(guild),
                    view=ops.StartOpEntryView(),
                )
                results.append(f"Explainer + кнопка «🚀 Начать операцию» — в {ops_channel.mention}.")
            except discord.HTTPException as exc:
                results.append(f"⚠️ Не удалось опубликовать explainer в {ops_channel.mention}: `{exc}`")

        await interaction.followup.send("\n".join(results), ephemeral=True)

    @post_onboarding.error
    async def post_onboarding_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.exception("Ошибка в /post-onboarding", exc_info=error)
        message = f"Непредвиденная ошибка: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Onboarding(bot))
