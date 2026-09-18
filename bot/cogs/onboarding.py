from __future__ import annotations

import logging

import discord
import yaml
from discord import app_commands
from discord.ext import commands

from . import _shared, ops, promotion

log = logging.getLogger("rnb_wardogs.onboarding")

RECRUIT_ROLE_NAME = "Новобранец | Recruit"
CONTRACT_CHANNEL_NAME = "📝-контракт"
ONBOARDING_CHANNEL_NAME = "правила-verification"
GUIDE_CHANNEL_NAME = "🪖-путеводитель-по-рангам"
OPS_ANNOUNCE_CHANNEL_NAME = "сбор-на-операцию"
TACTICS_CHANNEL_NAME = "тактика-обсуждение"
CHAT_CHANNEL_NAME = "💬-болталка-chat"
FEEDBACK_CHANNEL_NAME = "обратная-связь"

CONTRACT_EMBED_TITLE = "📜 Контракт РНБ"
RULES_EMBED_TITLE = "📜 Правила РНБ"
GUIDE_EMBED_TITLE = "🪖 Путеводитель по рангам"
OPS_EXPLAINER_TITLE = "📋 Как здесь появляются операции"

# Постоянный безлимитный инвайт этого сервера (max_age=0, max_uses=0) —
# создан вручную заранее. Раньше в "Полезные ссылки" была ссылка на другой,
# случайно истёкший инвайт (h6uesJCRb5, 404) — эта проверена живьём.
PERMANENT_INVITE_URL = "https://discord.gg/yw7N68FFjp"


def _build_contract_embed(guild: discord.Guild) -> discord.Embed:
    """Step 1 of 3 — sign-only, no squad info yet (that comes after rules,
    at the squad-picker step) so a brand-new member sees one small decision
    at a time instead of the whole flow at once. Posted in its OWN channel
    (not as a pin alongside other content) — this is deliberately the only
    thing a brand-new @everyone member can see in the category at all."""
    return discord.Embed(
        title=CONTRACT_EMBED_TITLE,
        description=(
            "Клан синих (Lonestar) в WarDogs. Нажимая кнопку ниже, ты подписываешь контракт:\n"
            "1. Слушаешь приказы своего Главы отряда на операции.\n"
            "2. Не сливаешь стратегию клана посторонним.\n"
            "3. Уважаешь остальных, даже когда не согласен.\n\n"
            "После подписи в списке каналов слева появятся правила и путеводитель по рангам."
        ),
        color=discord.Color.blurple(),
    )


def _build_guide_embed(guild: discord.Guild) -> discord.Embed:
    feedback_ref = _shared.channel_ref(guild, FEEDBACK_CHANNEL_NAME)
    embed = discord.Embed(
        title=GUIDE_EMBED_TITLE,
        description=(
            "**Новобранец** — стартовая роль после выбора отряда. Испытательный срок: показать, что "
            "тебе можно доверять место в отряде — слушаешь команды в бою, появляешься на операциях, "
            "не токсичен в чате/голосе. Формального списка галочек нет — Глава отряда, с которым ты "
            "играл, должен быть готов сказать «беру его в состав всерьёз».\n\n"
            "**Боец** — полноправный участник клана. Основа состава, полный доступ ко всем открытым "
            "каналам.\n\n"
            "**Глава отряда** — не выбирается голосованием и не выдаётся по стажу. Назначается за то, "
            "что человек уже делает на практике: держит связь в бою, не теряется, когда что-то идёт "
            "не по плану, за ним реально готовы идти. Решение — за Офицерами/ВГК.\n\n"
            "**Офицер** — то же самое, но на уровне всего клана, не одного отряда. Видимый, "
            "стабильный вклад за долгий срок, не разовая инициатива.\n\n"
            "**Верховный Главнокомандующий** — не ранг, который зарабатывают по чек-листу. Это "
            "решение о том, кто несёт ответственность за клан целиком.\n\n"
            "Готов(а)? Жми «🎖 Запросить повышение» ниже — запрос уйдёт в офицерский состав. "
            "Быть честным насчёт этого и есть весь путеводитель: выше — не механика, а доверие."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🔗 Полезные ссылки",
        value=(
            f"Пригласить друга: {PERMANENT_INVITE_URL}\n"
            f"Вопросы — в {feedback_ref}, не в личку офицерам."
        ),
        inline=False,
    )
    return embed


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
    """Step 1 — persistent, posted once via /post-onboarding in its own
    channel (CONTRACT_CHANNEL_NAME) — the only thing a brand-new @everyone
    member can see in the category at all."""

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

        # Роль "Новобранец" открывает view_channel на правила/путеводитель
        # (restricted_to в конфиге) — эти два канала появляются в списке
        # слева сами, ссылка на конкретное сообщение больше не нужна.
        rules_ref = _shared.channel_ref(guild, ONBOARDING_CHANNEL_NAME)
        guide_ref = _shared.channel_ref(guild, GUIDE_CHANNEL_NAME)
        await interaction.response.send_message(
            f"Подпись принята! В списке каналов слева появились {rules_ref} и {guide_ref} — "
            "загляни туда, дальше сам всё покажет.",
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
    """Step 2 — persistent, attached to the «📜 Правила РНБ» pinned message
    in ONBOARDING_CHANNEL_NAME. That channel itself is view-gated to
    Новобранец+ (restricted_to in config) — in the normal flow nobody
    without a signed contract can even SEE this channel to click the
    button, so this check is defensive fallback, not the primary gate."""

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
            contract_ref = _shared.channel_ref(guild, CONTRACT_CHANNEL_NAME)
            await interaction.response.send_message(
                f"Сначала подпиши контракт в {contract_ref}.", ephemeral=True
            )
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

        contract_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(CONTRACT_CHANNEL_NAME)
        )
        rules_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(ONBOARDING_CHANNEL_NAME)
        )
        guide_channel = discord.utils.get(
            guild.text_channels, name=_shared.normalize_channel_name(GUIDE_CHANNEL_NAME)
        )
        if contract_channel is None or rules_channel is None or guide_channel is None:
            missing = [
                name
                for name, chan in (
                    (CONTRACT_CHANNEL_NAME, contract_channel),
                    (ONBOARDING_CHANNEL_NAME, rules_channel),
                    (GUIDE_CHANNEL_NAME, guide_channel),
                )
                if chan is None
            ]
            await interaction.response.send_message(
                f"Канал(ы) не найдены: {', '.join(missing)}. Запусти `/setup-server`.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        results: list[str] = []

        try:
            await _shared.replace_pinned_by(
                contract_channel,
                marker_title=CONTRACT_EMBED_TITLE,
                embed=_build_contract_embed(guild),
                view=ContractSignView(),
            )
            results.append(f"«{CONTRACT_EMBED_TITLE}» — опубликовано и закреплено в {contract_channel.mention}.")
        except discord.Forbidden:
            await interaction.followup.send(
                f"Нет прав постить/закреплять в {contract_channel.mention} (Forbidden).", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Ошибка при публикации в {contract_channel.mention}: `{exc}`", ephemeral=True
            )
            return

        try:
            await _shared.replace_pinned_by(
                rules_channel,
                marker_title=RULES_EMBED_TITLE,
                embed=_build_rules_embed(guild),
                view=RulesAckView(),
            )
            results.append(f"«{RULES_EMBED_TITLE}» — опубликовано и закреплено в {rules_channel.mention}.")
        except discord.HTTPException as exc:
            results.append(f"⚠️ Не удалось опубликовать правила: `{exc}`")

        try:
            await _shared.replace_pinned_by(
                guide_channel,
                marker_title=GUIDE_EMBED_TITLE,
                embed=_build_guide_embed(guild),
                view=promotion.PromotionRequestView(),
            )
            results.append(f"«{GUIDE_EMBED_TITLE}» — опубликовано и закреплено в {guide_channel.mention}.")
        except discord.HTTPException as exc:
            results.append(f"⚠️ Не удалось опубликовать путеводитель: `{exc}`")

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
