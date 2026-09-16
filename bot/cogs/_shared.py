from __future__ import annotations

from pathlib import Path

import discord
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "server_structure.yaml"


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
