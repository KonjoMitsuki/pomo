from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path

import discord
from discord.ext import commands

from audio import AudioPlayer
from cog import PomoCog
from session import SessionManager
from storage import StatsRepository


BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
SOUND_FILE = str(ASSETS_DIR / "ding.mp3")
DB_FILE = str(ASSETS_DIR / "pomo.db")


def has_voice_runtime_dependencies(strict: bool = True) -> bool:
    missing = []
    if importlib.util.find_spec("nacl") is None:
        missing.append("PyNaCl")
    if importlib.util.find_spec("davey") is None:
        missing.append("davey")

    if not missing:
        return True

    level = "エラー" if strict else "警告"
    print(f"{level}: Voice機能の依存パッケージが不足しています。")
    print(f"不足: {', '.join(missing)}")
    print("不足依存をインストールしてください:")
    print("  pip install PyNaCl davey")
    return not strict


async def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("エラー: DISCORD_BOT_TOKEN 環境変数が設定されていません。")
        print("以下のコマンドで設定してください:")
        print("  export DISCORD_BOT_TOKEN='your_token_here'")
        raise SystemExit(1)

    strict_voice_deps = os.getenv("POMO_STRICT_VOICE_DEPS", "1").strip().lower() not in {"0", "false", "off", "no"}
    if not has_voice_runtime_dependencies(strict=strict_voice_deps):
        raise SystemExit(1)

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    stats = StatsRepository(DB_FILE)
    await stats.init()

    manager = SessionManager()
    audio = AudioPlayer(SOUND_FILE)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

    await bot.add_cog(PomoCog(bot, manager, stats, audio))
    await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())