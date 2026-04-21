from __future__ import annotations

import asyncio
import os
from pathlib import Path

import discord


DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
DEFAULT_SOUND_FILE = str(DEFAULT_ASSETS_DIR / "ding.mp3")


class AudioPlayer:
    def __init__(self, sound_file: str = DEFAULT_SOUND_FILE):
        self.sound_file = sound_file

    async def play(self, voice_client: discord.VoiceClient, volume: float = 1.0) -> None:
        if not self.file_exists():
            return
        if voice_client.is_playing():
            voice_client.stop()
        audio_source = discord.FFmpegPCMAudio(
            self.sound_file,
            options=f'-filter:a "volume={volume}"',
        )
        voice_client.play(audio_source)
        for _ in range(50):
            if not voice_client.is_playing():
                break
            await asyncio.sleep(0.1)

    def file_exists(self) -> bool:
        return os.path.exists(self.sound_file)
