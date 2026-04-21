from __future__ import annotations

import asyncio
import time

import discord
from discord.ext import commands
from discord.ui import Button

from audio import AudioPlayer
from session import PomoSession, SessionManager
from storage import StatsRepository
from views import JoinView, PomoView


class PomoRunner:
    NO_MEMBER_GRACE_SECONDS = 12
    VC_DOWN_GRACE_SECONDS = 12

    def __init__(
        self,
        session: PomoSession,
        voice_client: discord.VoiceClient,
        ctx: commands.Context,
        stats: StatsRepository,
        audio: AudioPlayer,
        manager: SessionManager,
        author_id: int,
    ):
        self.session = session
        self.vc = voice_client
        self.ctx = ctx
        self.stats = stats
        self.audio = audio
        self.manager = manager
        self.author_id = author_id
        self._no_member_since: float | None = None
        self._vc_down_since: float | None = None

    async def run(self) -> None:
        self.session.active = True
        self.session.stop_requested = False
        self.manager.update_index(self.author_id)
        self.session.control_msg = await self.ctx.send(
            f"🛑 **<@{self.session.host_id}> のタイマー**\n"
            "ポモドーロを終了する場合は、ボイスチャンネルから退出してください。"
        )
        await self._refresh_panels("開始")

        while not self.session.stop_requested:
            if not self._has_members_with_grace():
                self.session.stop_requested = True
                break
            if not self.session.has_active_members(self.vc):
                await asyncio.sleep(1)
                continue

            self.session.session_count += 1
            label = f"セッション {self.session.session_count}"

            ok = await self.run_phase(self.session.work_min, label, "🍅")
            if not ok:
                return

            active_ids = self.session.get_vc_active_ids(self.vc)
            await self.stats.add_completed_session(active_ids)
            if self.session.pomo_msg:
                is_long_break = (self.session.session_count % self.session.interval == 0)
                break_time = self.session.long_brk if is_long_break else self.session.short_brk
                break_type = "長休憩" if is_long_break else "小休憩"
                await self.session.pomo_msg.edit(
                    content=(
                        f"🎉 **<@{self.session.host_id}> のセッション {self.session.session_count} 完了！** "
                        f"{self.session.work_min}分の作業が終わりました。\n"
                        f"💤 {break_type} {break_time}分を開始します..."
                    ),
                    view=None,
                )

            if not self.session.muted and self.vc.is_connected():
                if self.audio.file_exists():
                    await self.audio.play(self.vc, volume=1.0)
                else:
                    await self.ctx.send("⚠️ 音声ファイル (assets/ding.mp3) が見つかりませんでした。")

            is_long_break = (self.session.session_count % self.session.interval == 0)
            break_time = self.session.long_brk if is_long_break else self.session.short_brk
            break_type = "長休憩" if is_long_break else "小休憩"
            break_emoji = "☕" if is_long_break else "💤"

            if break_time > 0:
                ok = await self.run_phase(break_time, break_type, break_emoji)
                if not ok:
                    return
                if self.session.pomo_msg:
                    await self.session.pomo_msg.edit(
                        content=f"⏰ **<@{self.session.host_id}> の{break_type}終了！** 次のセッションを始めましょう。",
                        view=None,
                    )

                if not self.session.muted and self.vc.is_connected() and self.audio.file_exists():
                    await self.audio.play(self.vc, volume=1.5)

            await asyncio.sleep(2)

        if self.session.control_msg:
            await self.session.control_msg.edit(
                content=(
                    f"🎉 **<@{self.session.host_id}> のポモドーロ終了！** "
                    f"合計 {self.session.session_count} セッション完了しました。お疲れ様でした！"
                )
            )

        if self.vc and self.vc.is_connected():
            await self.vc.disconnect()

    async def run_phase(self, duration_min: int, label: str, emoji: str) -> bool:
        if duration_min <= 0:
            return True

        view = PomoView(self.session)
        self.session.pomo_view = view
        self.session.pomo_msg = await self.ctx.send(
            self._phase_start_text(duration_min, label, emoji),
            view=view,
        )
        await self._refresh_panels(label)

        remaining_seconds = duration_min * 60
        while remaining_seconds > 0:
            state = await self._wait_tick(view)
            if state == "paused":
                continue
            if state == "wait_members":
                continue
            if state == "wait_vc":
                continue
            if state == "stopped":
                if self.session.control_msg:
                    await self.session.control_msg.edit(content="⏹️ ポモドーロを終了しました。お疲れ様でした！")
                if self.vc and self.vc.is_connected():
                    await self.vc.disconnect()
                return False
            if state == "no_members":
                if self.session.pomo_msg:
                    await self.session.pomo_msg.edit(content="⏹️ ユーザーが退出したため終了しました。", view=None)
                if self.vc and self.vc.is_connected():
                    await self.vc.disconnect()
                return False

            remaining_seconds -= 1
            if emoji == "🍅" and remaining_seconds % 60 == 0:
                active_ids = self.session.get_vc_active_ids(self.vc)
                await self.stats.add_work_minutes(active_ids, 1)
                for uid in active_ids:
                    self.session.session_work[uid] = self.session.session_work.get(uid, 0) + 1

            if remaining_seconds % 60 == 0 and remaining_seconds != 0:
                await self.session.pomo_msg.edit(
                    content=self._phase_tick_text(remaining_seconds // 60, label, emoji),
                    view=view,
                )

        return True

    async def _wait_tick(self, view: PomoView) -> str:
        if self.session.stop_requested:
            return "no_members"

        if (not self.vc) or (not self.vc.is_connected()):
            guild = self.ctx.guild
            guild_vc = guild.voice_client if guild else None
            if guild_vc and guild_vc.is_connected():
                self.vc = guild_vc
                self._vc_down_since = None
            else:
                now = time.monotonic()
                if self._vc_down_since is None:
                    self._vc_down_since = now
                if now - self._vc_down_since >= self.VC_DOWN_GRACE_SECONDS:
                    print("[DEBUG] VC切断を検知したためタイマーを終了します。")
                    return "no_members"
                await asyncio.sleep(1)
                return "wait_vc"
        else:
            self._vc_down_since = None

        if view.stopped:
            return "stopped"

        if not self._has_members_with_grace():
            print("[DEBUG] 在席メンバー0人状態が継続したためタイマーを終了します。")
            return "no_members"
        if not self.session.has_active_members(self.vc):
            await asyncio.sleep(1)
            return "wait_members"

        if view.paused:
            await asyncio.sleep(1)
            return "paused"

        await asyncio.sleep(1)
        return "tick"

    def _has_members_with_grace(self) -> bool:
        if self.session.has_active_members(self.vc):
            self._no_member_since = None
            return True

        now = time.monotonic()
        if self._no_member_since is None:
            self._no_member_since = now
            return True

        return (now - self._no_member_since) < self.NO_MEMBER_GRACE_SECONDS

    async def _refresh_panels(self, label: str) -> None:
        if self.session.join_view and self.session.join_msg:
            for child in self.session.join_view.children:
                if isinstance(child, Button):
                    child.disabled = True
            try:
                await self.session.join_msg.edit(view=self.session.join_view)
            except discord.HTTPException:
                pass

        join_view = JoinView(self.session, self.manager, self.author_id)
        join_msg = await self.ctx.send(
            f"🙋 参加パネル ({label})\n対象: {self.session.get_target_line()}",
            view=join_view,
        )
        self.session.join_view = join_view
        self.session.join_msg = join_msg

    def _phase_start_text(self, duration_min: int, label: str, emoji: str) -> str:
        if emoji == "🍅":
            return (
                f"🍅 **<@{self.session.host_id}> の{label} 開始！** ({duration_min}分)\n"
                f"対象: {self.session.get_target_line()}\n"
                "集中しましょう！"
            )
        return f"{emoji} **<@{self.session.host_id}> の{label}！** ({duration_min}分)\nリラックスしましょう！"

    def _phase_tick_text(self, remaining_min: int, label: str, emoji: str) -> str:
        if emoji == "🍅":
            return f"🍅 **残り {remaining_min} 分** ({label})\n集中しましょう！"
        return f"{emoji} **<@{self.session.host_id}> の残り {remaining_min} 分** ({label})\nリラックスしましょう！"
