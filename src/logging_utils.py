from __future__ import annotations

import csv
import sys
from datetime import datetime


def _safe_text(value: object | None, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def log_action(
    *,
    guild_name: object | None,
    user_name: object | None,
    action: object | None,
    occurred_at: datetime | None = None,
) -> None:
    timestamp = (occurred_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    row = [
        timestamp,
        _safe_text(guild_name, "DM"),
        _safe_text(user_name, "Bot"),
        _safe_text(action, ""),
    ]
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(row)
    sys.stdout.flush()


def log_from_context(ctx: object, action: str) -> None:
    guild = getattr(ctx, "guild", None)
    author = getattr(ctx, "author", None)
    log_action(
        guild_name=getattr(guild, "name", None),
        user_name=getattr(author, "display_name", None) or getattr(author, "name", None),
        action=action,
        occurred_at=getattr(getattr(ctx, "message", None), "created_at", None),
    )


def log_from_interaction(interaction: object, action: str) -> None:
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    log_action(
        guild_name=getattr(guild, "name", None),
        user_name=getattr(user, "display_name", None) or getattr(user, "name", None),
        action=action,
        occurred_at=getattr(interaction, "created_at", None),
    )
