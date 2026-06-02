from __future__ import annotations

import csv
import sys
from datetime import datetime


"""
簡易ログユーティリティ。

標準出力へCSV形式でログを出力します。Botの操作履歴やインタラクション履歴を
記録するためのヘルパー関数群を提供します。
"""


def _safe_text(value: object | None, fallback: str) -> str:
    # 値を文字列化し、空ならフォールバックを返す
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
    """与えられた情報をCSV形式で標準出力に1行出力します。

    フィールド: `timestamp, guild_name, user_name, action`
    """
    # CSVとして標準出力に行を出力する
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
    # Context から必要情報を抜き取りログ出力するラッパー
    """`commands.Context` からログ情報を抽出して `log_action` を呼び出します。"""
    guild = getattr(ctx, "guild", None)
    author = getattr(ctx, "author", None)
    log_action(
        guild_name=getattr(guild, "name", None),
        user_name=getattr(author, "display_name", None) or getattr(author, "name", None),
        action=action,
        occurred_at=getattr(getattr(ctx, "message", None), "created_at", None),
    )


def log_from_interaction(interaction: object, action: str) -> None:
    # Interaction から情報を抜き出してログ出力するラッパー
    """Interaction からログ情報を抽出して `log_action` を呼び出します。"""
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    log_action(
        guild_name=getattr(guild, "name", None),
        user_name=getattr(user, "display_name", None) or getattr(user, "name", None),
        action=action,
        occurred_at=getattr(interaction, "created_at", None),
    )
