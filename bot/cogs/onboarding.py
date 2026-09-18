from __future__ import annotations

import logging

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from . import _shared, ops, promotion

log = logging.getLogger("rnb_wardogs.onboarding")

RECRUIT_ROLE_NAME = "Новобранец | Recruit"
ONBOARDING_CHANNEL_NAME = "правила-verification"
OPS_ANNOUNCE_CHANNEL_NAME = "сбор-на-операцию"
TACTICS_CHANNEL_NAME = "тактика-обсуждение"
CHAT_CHANNEL_NAME = "💬-болталка-chat"

CONTRACT_EMBED_TITLE = "📜 Контракт РНБ"
RULES_EMBED_TITLE = "📜 Правила РНБ"
OPS_EXPLAINER_TITLE = "📋 Как здесь появляются операции"


def _build_contract_embed(guild: discord.Guild) -> discord.Embed:
    """Step 1 of 3 — sign-only, no squad info yet (that comes after rules,
    at the squad-picker step) so a brand-new member sees one small decision
    at a time instead of the whole flow at once."""
    return discord.Embed(
        title=CONTRACT_EMBED_TITLE,
        description=(
            "Клан синих (Lonestar) в WarDogs. Нажимая кнопку ниже, ты подписываешь контракт:\n"
            "1. Слушаешь приказы своего Главы отряда на операции.\n"
            "2. Не сливаешь стратегию клана посторонним.\n"
            "3. Уважаешь остальных, даже когда не согласен.\n\n"
            "Полные условия — в закреплённых правилах ниже. После подписи покажем, что делать дальше."
        ),
        color=discord.Color.blurple(),
    )


def _build_rules_embed(guild: discord.Guild) -> discord.Embed:
    tactics_ref = _shared.channel_ref(guild, TACTICS_CHANNEL_NAME)
    return discord.Embed(
        title=RULES_EMBED_TITLE,
        description=(
            "1. Уважение ко всем участникам — обязательно, без исключений.\n"
            "2. На игровом сервере приказы назначенного Главы отряда обязательны для всех бойцов РНБ "
            "на этой сессии, независимо от общей иерархии клана. Споры о тактике — после матча, не во время.\n"
            "3. Никакого слива стратегии клана посторонним.\n"
            "4. Неактивность 3+ недели без предупреждения = перевод в резерв.\n"
            "5. Токсичность в чате/голосе: первое — предупреждение, повторное — мут 24ч, злостное — исключение.\n\n"
            f"Полная версия и тактика — в {tactics_ref}.\n\n"
            "Прочитал(а)? Жми «✅ Ознакомлен» ниже — там же выберешь отряд."
        ),
        color=discord.Color.blurple(),
    )


def _build_ops_explainer_embed(guild: discord.Guild) -> discord.Embed:
    chat_ref = _shared.channel_ref(guild, CHAT_CHANNEL_NAME)
    return discord.Embed(
        title=OPS_EXPLAINER_TITLE,
        description=(
            "Здесь появляются анонсы операций — их создают Главы отрядов/Офицеры кнопкой "
            "«🚀 Начать операцию» под этим сообщением.\n\n"
            f"Если сейчас пусто — операция ещё не назначена, загляни позже или спроси в {chat_ref}.\n\n"
            "Конкретный игровой сервер объявляется в каждом анонсе отдельно (Server ID) — "
            "фиксированного постоянного сервера у нас пока нет.\n\n"
            "Кнопка «🎮 Готов играть сейчас» ниже — отметка для внеплановых игр, не связана с анонсами."
        ),
        color=discord.Color.blurple(),
    )


def _has_signed_contract(member: discord.Member) -> bool:
    """True once someone has progressed past 'just joined' — holds Новобранец
    OR any higher rank OR any squad role. Deliberately broader than "holds
    Новобранец" alone: a promoted Боец has Новобранец stripped by the
    promotion flow's rank-hierarchy cleanup, but obviously already signed —
    checking only Новобранец would wrongly tell them to sign again."""
    progressed_role_names = set(promotion.RANK_HIERARCHY) | {name for _, name in _shared.SQUADS}
    return any(r.name in progressed_role_names for r in member.roles)


class ContractSignView(discord.ui.View):
    """Step 1 — persistent, posted once via /post-onboarding, attached to
    the «📜 Контракт РНБ» pinned message."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="✍️ Подписать контракт", style=discord.ButtonStyle.success, custom_id="rnb:contract_sign")
    async def sign(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
            return

        recruit_role = discord.utils.get(guild.roles, name=RECRUIT_ROLE_NAME)
        if recruit_role is None:
            await interaction.response.send_message(
                f"Роль «{RECRUIT_ROLE_NAME}» не найдена на сервере. Попроси Офицера прогнать `/setup-server`.",
                ephemeral=True,
            )
            return

        if not _has_signed_contract(member):
            try:
                await member.add_roles(recruit_role, reason="РНБ: подписан контракт")
            except discord.Forbidden:
                await interaction.response.send_message(
                    "Не хватает прав выдать роль — проверь, что роль бота стоит ВЫШЕ ролей отрядов "
                    "в списке ролей сервера.",
                    ephemeral=True,
                )
                return

        rules_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(ONBOARDING_CHANNEL_NAME)
        )
        rules_msg = await _shared.find_pinned_by_title(rules_channel, RULES_EMBED_TITLE)

        if rules_msg is not None:
            link_view = discord.ui.View()
            link_view.add_item(
                discord.ui.Button(style=discord.ButtonStyle.link, url=rules_msg.jump_url, label="→ Читать правила")
            )
            await interaction.response.send_message(
                "Подпись принята! Теперь прочитай правила ниже и нажми там «✅ Ознакомлен»:",
                view=link_view,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Подпись принята! Прочитай закреплённые правила в этом канале и нажми там «✅ Ознакомлен».",
                ephemeral=True,
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
        is_first_squad = not previous_squad_roles and new_role not in member.roles
        recruit_role = discord.utils.get(guild.roles, name=RECRUIT_ROLE_NAME)

        roles_to_add = [] if new_role in member.roles else [new_role]
        if recruit_role is not None and recruit_role not in member.roles and not _has_signed_contract(member):
            roles_to_add.append(recruit_role)

        try:
            if previous_squad_roles:
                await member.remove_roles(*previous_squad_roles, reason="РНБ: смена отряда")
            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="РНБ: выбор отряда")
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
                f"Готово — ты в отряде «{self.label}»! Там объявляются ближайшие игры:",
                view=link_view,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Готово — ты в отряде «{self.label}»! Канал «{OPS_ANNOUNCE_CHANNEL_NAME}» не найден — "
                "попроси Офицера прогнать `/setup-server`.",
                ephemeral=True,
            )

        # Публичное поздравление — только при ПЕРВОМ выборе отряда (реальное
        # "оформление"), не при каждой смене отряда позже, чтобы не спамить.
        if is_first_squad:
            chat_channel = discord.utils.get(
                guild.text_channels, name=_shared.normalize_channel_name(CHAT_CHANNEL_NAME)
            )
            if chat_channel is not None:
                try:
                    await chat_channel.send(f"🎉 Добро пожаловать в РНБ, {member.mention}! Отряд: **{self.label}**.")
                except discord.Forbidden:
                    pass


class SquadPickView(discord.ui.View):
    """Persistent (survives restart), but delivered on-demand as an ephemeral
    response from RulesAckView — not posted as its own standing panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        for label, role_name in _shared.SQUADS:
            self.add_item(SquadButton(label, role_name))


SQUAD_PICKER_INTRO = (
    "Осталось выбрать отряд:\n\n"
    "**Отряды:** ⚔️ Штурм · 📦 Логистика · 🔍 Разведка · 🚚 Транспорт · 🛠 Поддержка\n"
    "Не уверен какой? Разведка/Логистика проще для новичков в WarDogs, Штурм — если уже "
    "есть опыт в тактических шутерах (Arma, Tarkov, Squad)."
)


class RulesAckView(discord.ui.View):
    """Step 2 — persistent, attached to the «📜 Правила РНБ» pinned message.
    Gated on having signed the contract first (step 1)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Ознакомлен", style=discord.ButtonStyle.success, custom_id="rnb:rules_ack")
    async def ack(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
            return

        if not _has_signed_contract(member):
            rules_channel = discord.utils.get(
                guild.text_channels, name=_shared.normalize_channel_name(ONBOARDING_CHANNEL_NAME)
            )
            contract_msg = await _shared.find_pinned_by_title(rules_channel, CONTRACT_EMBED_TITLE)
            if contract_msg is not None:
                link_view = discord.ui.View()
                link_view.add_item(
                    discord.ui.Button(style=discord.ButtonStyle.link, url=contract_msg.jump_url, label="→ Подписать контракт")
                )
                await interaction.response.send_message(
                    "Сначала подпиши контракт выше:", view=link_view, ephemeral=True
                )
            else:
                await interaction.response.send_message("Сначала подпиши контракт выше.", ephemeral=True)
            return

        await interaction.response.send_message(SQUAD_PICKER_INTRO, view=SquadPickView(), ephemeral=True)


class Onboarding(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bot.add_view(ContractSignView())
        self.bot.add_view(RulesAckView())
        self.bot.add_view(SquadPickView())

    @app_commands.command(
        name="post-onboarding",
        description="Обновить онбординг (контракт/правила/операции). Officer+.",
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

        results: list[str] = []

        try:
            await _shared.replace_pinned_by(
                target_channel,
                marker_title=CONTRACT_EMBED_TITLE,
                embed=_build_contract_embed(guild),
                view=ContractSignView(),
            )
            results.append(f"«{CONTRACT_EMBED_TITLE}» — опубликовано и закреплено в {target_channel.mention}.")
        except discord.Forbidden:
            await interaction.followup.send(
                f"Нет прав постить/закреплять в {target_channel.mention} (Forbidden).", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"Ошибка при публикации в {target_channel.mention}: `{exc}`", ephemeral=True)
            return

        try:
            await _shared.replace_pinned_by(
                target_channel,
                marker_title=RULES_EMBED_TITLE,
                embed=_build_rules_embed(guild),
                view=RulesAckView(),
            )
            results.append(f"«{RULES_EMBED_TITLE}» — опубликовано и закреплено в {target_channel.mention}.")
        except discord.HTTPException as exc:
            results.append(f"⚠️ Не удалось опубликовать правила: `{exc}`")

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
                results.append(f"Explainer + «🚀 Начать операцию» + «🎮 Готов играть» — в {ops_channel.mention}.")
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
