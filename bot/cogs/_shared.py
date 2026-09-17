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
