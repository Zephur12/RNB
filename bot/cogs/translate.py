from __future__ import annotations

import logging
import re
from pathlib import Path

import discord
import yaml
from deep_translator import GoogleTranslator
from discord.ext import commands

log = logging.getLogger("rnb_wardogs.translate")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "server_structure.yaml"

CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")

MIRROR_WEBHOOK_NAME = "РНБ Translate Mirror"


def _load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _detect_direction(text: str) -> tuple[str, str]:
    if CYRILLIC_RE.search(text):
        return "ru", "en"
    return "en", "ru"


class Translate(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._mirror_enabled = False
        self._mirror_pairs: list[tuple[str, str]] = []
        self._webhook_cache: dict[int, discord.Webhook] = {}
        self._load_mirror_config()

    def _load_mirror_config(self) -> None:
        try:
            config = _load_config()
        except (FileNotFoundError, yaml.YAMLError) as exc:
            log.warning("Не удалось прочитать конфиг зеркалирования переводов: %s", exc)
            return

        translate_cfg = config.get("translate", {}) or {}
        self._mirror_enabled = bool(translate_cfg.get("mirror_pairs_enabled", False))
        self._mirror_pairs = [
            (pair["ru_channel"], pair["en_channel"])
            for pair in translate_cfg.get("mirror_pairs", []) or []
            if pair.get("ru_channel") and pair.get("en_channel")
        ]

    @commands.command(name="tr")
    async def translate_command(self, ctx: commands.Context, *, text: str) -> None:
        source, target = _detect_direction(text)
        try:
            translated = GoogleTranslator(source=source, target=target).translate(text)
        except Exception as exc:  # deep-translator raises several distinct exception types
            log.exception("Ошибка перевода")
            await ctx.reply(f"Не удалось перевести: `{exc}`", mention_author=False)
            return

        flag = "🇬🇧" if target == "en" else "🇷🇺"
        await ctx.reply(f"{flag} {translated}", mention_author=False)

    async def _get_mirror_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        cached = self._webhook_cache.get(channel.id)
        if cached is not None:
            return cached

        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, name=MIRROR_WEBHOOK_NAME)
        if webhook is None:
            webhook = await channel.create_webhook(name=MIRROR_WEBHOOK_NAME, reason="РНБ зеркалирование переводов")

        self._webhook_cache[channel.id] = webhook
        return webhook

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.webhook_id is not None or message.author.bot:
            return
        if not self._mirror_enabled or not self._mirror_pairs:
            return
        if message.guild is None or not isinstance(message.channel, discord.TextChannel):
            return

        content = message.content.strip()
        if not content or content.startswith(self.bot.command_prefix):
            return

        ru_channel_name = message.channel.name
        en_channel_name = next(
            (en for ru, en in self._mirror_pairs if ru == ru_channel_name),
            None,
        )
        if en_channel_name is None:
            return

        en_channel = discord.utils.get(message.guild.text_channels, name=en_channel_name)
        if en_channel is None:
            log.warning("Зеркальный канал «%s» не найден на сервере", en_channel_name)
            return

        try:
            translated = GoogleTranslator(source="ru", target="en").translate(content)
        except Exception:
            log.exception("Ошибка перевода при зеркалировании из «%s»", ru_channel_name)
            return

        try:
            webhook = await self._get_mirror_webhook(en_channel)
            await webhook.send(
                content=translated,
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url,
            )
        except discord.Forbidden:
            log.warning("Нет прав на webhook в канале «%s»", en_channel_name)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Translate(bot))
