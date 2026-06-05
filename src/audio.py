from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp
import discord


DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
DEFAULT_VOICEVOX_URL = "http://127.0.0.1:50021"
DEFAULT_SPEAKER_ID = 47
DEFAULT_TEMP_WAV_FILE = str(DEFAULT_ASSETS_DIR / "voicevox_temp.wav")


class AudioPlayer:
    """VOICEVOX を利用してテキストを音声に変換し、Discord に再生するヘルパークラス。

    - `generate_voice(text)`: VOICEVOX に問い合わせて WAV を生成し一時ファイルに保存します。
    - `play_voice(voice_client, text, volume)`: 生成した音声を `voice_client` で再生します。
    同時再生を避けるため内部でロックを取ります。
    """
    def __init__(
        self,
        voicevox_url: str = DEFAULT_VOICEVOX_URL,
        speaker_id: int = DEFAULT_SPEAKER_ID,
        temp_wav_file: str = DEFAULT_TEMP_WAV_FILE,
    ):
        # AudioPlayer の初期化（VOICEVOX URLや一時ファイルパスを設定）
        self.voicevox_url = voicevox_url.rstrip("/")
        self.speaker_id = speaker_id
        self.temp_wav_path = Path(temp_wav_file)
        self._timeout = aiohttp.ClientTimeout(total=20, connect=5, sock_connect=5, sock_read=15)
        self._play_lock = asyncio.Lock()

    async def generate_voice(self, text: str) -> bool:
        # テキストからVOICEVOXでWAVを生成して一時ファイルに保存する
        """VOICEVOX の HTTP API を呼び、WAV ファイルを一時ファイルに保存します。

        成功すれば `True` を、失敗すれば `False` を返します。
        """
        self.temp_wav_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    f"{self.voicevox_url}/audio_query",
                    params={"text": text, "speaker": self.speaker_id},
                ) as query_response:
                    if query_response.status != 200:
                        return False
                    audio_query = await query_response.json(content_type=None)

                async with session.post(
                    f"{self.voicevox_url}/synthesis",
                    params={"speaker": self.speaker_id},
                    json=audio_query,
                ) as synthesis_response:
                    if synthesis_response.status != 200:
                        return False
                    wav_bytes = await synthesis_response.read()

            self.temp_wav_path.write_bytes(wav_bytes)
            return True
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            return False

    async def play_voice(self, voice_client: discord.VoiceClient, text: str, volume: float = 1.0) -> bool:
        # 生成した音声をDiscordのVoiceClientで再生する
        """`generate_voice` で音声を生成して `voice_client` で再生します。

        再生終了まで待機し、成功時に `True` を返します。
        """
        async with self._play_lock:
            if not await self.generate_voice(text):
                return False

            if voice_client.is_playing():
                voice_client.stop()

            try:
                audio_source = discord.FFmpegPCMAudio(
                    str(self.temp_wav_path),
                    options=f'-filter:a "volume={volume}"',
                )
                voice_client.play(audio_source)
            except Exception:
                return False

            for _ in range(600):
                if not voice_client.is_playing():
                    break
                await asyncio.sleep(0.1)

            return True
