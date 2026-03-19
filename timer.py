from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

import aiosqlite
import discord
from discord.ext import commands
from discord.ui import Button, View

SOUND_FILE = "ding.mp3"
DB_FILE = "pomo.db"


@dataclass
class PomoSession:
    host_id: int
    targets: set[int] = field(default_factory=set)
    join_order: list[int] = field(default_factory=list)
    work_min: int = 25
    short_brk: int = 5
    long_brk: int = 15
    interval: int = 4
    session_count: int = 0
    session_work: dict[int, int] = field(default_factory=dict)
    muted: bool = False
    active: bool = False
    pomo_view: "PomoView | None" = field(default=None, repr=False)
    pomo_msg: "discord.Message | None" = field(default=None, repr=False)
    control_msg: "discord.Message | None" = field(default=None, repr=False)
    join_view: "JoinView | None" = field(default=None, repr=False)
    join_msg: "discord.Message | None" = field(default=None, repr=False)

    def get_all_member_ids(self) -> set[int]:
        return {self.host_id} | set(self.targets)

    def get_vc_active_ids(self, voice_client: discord.VoiceClient | None) -> list[int]:
        if not voice_client or not voice_client.channel:
            return []
        vc_member_ids = {m.id for m in voice_client.channel.members if not m.bot}
        active_ids = vc_member_ids & self.get_all_member_ids()
        return list(active_ids)

    def has_active_members(self, voice_client: discord.VoiceClient | None) -> bool:
        return len(self.get_vc_active_ids(voice_client)) > 0

    def transfer_host(self) -> int | None:
        for user_id in self.join_order:
            if user_id != self.host_id and user_id in self.targets:
                self.targets.remove(user_id)
                self.host_id = user_id
                return user_id
        return None

    def add_member(self, user_id: int) -> bool:
        if user_id == self.host_id or user_id in self.targets:
            return False
        self.targets.add(user_id)
        if user_id not in self.join_order:
            self.join_order.append(user_id)
        return True

    def remove_member(self, user_id: int) -> bool:
        if user_id in self.targets:
            self.targets.remove(user_id)
            return True
        return False

    def get_target_line(self) -> str:
        mentions = [f"<@{self.host_id}>"]
        ordered_targets = [uid for uid in self.join_order if uid in self.targets]
        mentions.extend([f"<@{uid}>" for uid in ordered_targets])
        for uid in sorted(self.targets):
            if uid not in ordered_targets:
                mentions.append(f"<@{uid}>")
        return " ".join(mentions)


class SessionManager:
    def __init__(self):
        self._sessions: dict[int, PomoSession] = {}
        self._user_index: dict[int, int] = {}

    def create(self, author_id: int, **kwargs) -> PomoSession:
        session = PomoSession(host_id=author_id, **kwargs)
        session.join_order.append(author_id)
        self._sessions[author_id] = session
        self.update_index(author_id)
        return session

    def get(self, author_id: int) -> PomoSession | None:
        return self._sessions.get(author_id)

    def remove(self, author_id: int) -> None:
        self._sessions.pop(author_id, None)
        stale_users = [uid for uid, owner in self._user_index.items() if owner == author_id]
        for uid in stale_users:
            self._user_index.pop(uid, None)

    def find_by_user(self, user_id: int) -> tuple[int, PomoSession] | None:
        author_id = self._user_index.get(user_id)
        if author_id is None:
            return None
        session = self._sessions.get(author_id)
        if session is None:
            self._user_index.pop(user_id, None)
            return None
        return author_id, session

    def update_index(self, author_id: int) -> None:
        session = self._sessions.get(author_id)
        stale_users = [uid for uid, owner in self._user_index.items() if owner == author_id]
        for uid in stale_users:
            self._user_index.pop(uid, None)
        if session is None:
            return
        indexed_ids = session.get_all_member_ids() if session.active else {session.host_id}
        for uid in indexed_ids:
            self._user_index[uid] = author_id


class StatsRepository:
    def __init__(self, db_file: str = DB_FILE):
        self.db_file = db_file

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS stats (
                    user_id INTEGER PRIMARY KEY,
                    total_minutes INTEGER DEFAULT 0,
                    sessions INTEGER DEFAULT 0
                )
                """
            )
            await db.commit()

    async def add_work_minutes(self, user_ids: list[int], minutes: int) -> None:
        if not user_ids or minutes <= 0:
            return
        async with aiosqlite.connect(self.db_file) as db:
            await db.executemany(
                """
                INSERT INTO stats (user_id, total_minutes, sessions)
                VALUES (?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                total_minutes = total_minutes + ?
                """,
                [(uid, minutes, minutes) for uid in user_ids],
            )
            await db.commit()

    async def add_completed_session(self, user_ids: list[int]) -> None:
        if not user_ids:
            return
        async with aiosqlite.connect(self.db_file) as db:
            await db.executemany(
                """
                INSERT INTO stats (user_id, total_minutes, sessions)
                VALUES (?, 0, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                sessions = sessions + 1
                """,
                [(uid,) for uid in user_ids],
            )
            await db.commit()

    async def get_stats(self, user_id: int) -> tuple[int, int] | None:
        async with aiosqlite.connect(self.db_file) as db:
            async with db.execute(
                "SELECT total_minutes, sessions FROM stats WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row:
            return row[0], row[1]
        return None

    async def reset_stats(self, user_id: int) -> tuple[int, int] | None:
        before = await self.get_stats(user_id)
        if before is None:
            return None
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("DELETE FROM stats WHERE user_id = ?", (user_id,))
            await db.commit()
        return before


class AudioPlayer:
    def __init__(self, sound_file: str = SOUND_FILE):
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


class PomoView(View):
    def __init__(self, session: PomoSession):
        super().__init__(timeout=None)
        self.session = session
        self.paused = False
        self.stopped = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id in self.session.get_all_member_ids()

    @discord.ui.button(label="一時停止", style=discord.ButtonStyle.secondary, emoji="⏸️")
    async def pause_button(self, interaction: discord.Interaction, button: Button):
        self.paused = True
        button.disabled = True
        self.children[1].disabled = False
        await interaction.response.edit_message(content="⏸️ タイマーを一時停止しました。", view=self)

    @discord.ui.button(label="再開", style=discord.ButtonStyle.success, emoji="▶️", disabled=True)
    async def resume_button(self, interaction: discord.Interaction, button: Button):
        self.paused = False
        button.disabled = True
        self.children[0].disabled = False
        await interaction.response.edit_message(content="▶️ タイマーを再開します。", view=self)

    @discord.ui.button(label="終了", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: Button):
        self.stopped = True
        await interaction.response.edit_message(content="⏹️ タイマーを終了しました。", view=None)
        self.stop()


class JoinView(View):
    def __init__(self, session: PomoSession, manager: SessionManager, author_id: int):
        super().__init__(timeout=None)
        self.session = session
        self.manager = manager
        self.author_id = author_id

    @discord.ui.button(label="参加", style=discord.ButtonStyle.success, emoji="🙋")
    async def join_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        if user.bot:
            await interaction.response.send_message("⚠️ Botは参加できません。", ephemeral=True)
            return
        if self.session.add_member(user.id):
            self.manager.update_index(self.author_id)
            await interaction.response.send_message("✅ タイマー対象に参加しました。", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ 既に参加済みです。", ephemeral=True)

    @discord.ui.button(label="退出", style=discord.ButtonStyle.secondary, emoji="👋")
    async def leave_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user

        if user.id == self.session.host_id:
            new_host = self.session.transfer_host()
            self.manager.update_index(self.author_id)
            if new_host:
                await interaction.response.send_message(
                    f"👋 {user.mention} が退出しました。ホストが <@{new_host}> に移行しました。"
                )
            else:
                await interaction.response.send_message(
                    f"👋 {user.mention} が退出しました。タイマーは参加者がいる限り継続します。"
                )
            return

        if self.session.remove_member(user.id):
            self.manager.update_index(self.author_id)
            await interaction.response.send_message(f"👋 {user.mention} が退出しました。", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ 参加していません。", ephemeral=True)


class PomoRunner:
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

    async def run(self) -> None:
        self.session.active = True
        self.manager.update_index(self.author_id)
        self.session.control_msg = await self.ctx.send(
            f"🛑 **<@{self.session.host_id}> のタイマー**\n"
            "ポモドーロを終了する場合は、ボイスチャンネルから退出してください。"
        )
        await self._refresh_panels("開始")

        while self.session.has_active_members(self.vc):
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
                    await self.ctx.send("⚠️ 音声ファイル (ding.mp3) が見つかりませんでした。")

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
        if not self.session.has_active_members(self.vc):
            return "no_members"
        if view.stopped:
            return "stopped"
        if view.paused:
            await asyncio.sleep(1)
            return "paused"
        await asyncio.sleep(1)
        return "tick"

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


class PomoCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        manager: SessionManager,
        stats: StatsRepository,
        audio: AudioPlayer,
    ):
        self.bot = bot
        self.manager = manager
        self.stats = stats
        self.audio = audio

    async def _resolve_owned_session(self, user_id: int) -> tuple[int, PomoSession] | None:
        session = self.manager.get(user_id)
        if session is None:
            result = self.manager.find_by_user(user_id)
            if result is None:
                return None
            author_id, indexed_session = result
            if indexed_session.host_id != user_id:
                return None
            return author_id, indexed_session
        if session.host_id == user_id:
            return user_id, session
        return None

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.bot.user} としてログインしました。")

    @commands.command()
    async def pomo(
        self,
        ctx,
        work_minutes: int = 25,
        short_break: int = 5,
        long_break: int = 15,
        long_break_interval: int = 4,
    ):
        voice_client = ctx.voice_client
        if not voice_client and ctx.author.voice:
            try:
                voice_client = await ctx.author.voice.channel.connect()
            except Exception as e:
                await ctx.send(f"⚠️ ボイスチャンネルに接続できませんでした: {e}")
                return

        if not voice_client:
            await ctx.send("⚠️ ボイスチャンネルに参加してからコマンドを実行してください。")
            return

        existing = self.manager.get(ctx.author.id)
        if existing is not None and existing.active:
            await ctx.send("⚠️ すでにあなたのタイマーが動作中です。")
            return

        if existing is None:
            session = self.manager.create(
                ctx.author.id,
                work_min=work_minutes,
                short_brk=short_break,
                long_brk=long_break,
                interval=long_break_interval,
            )
        else:
            session = existing
            session.host_id = ctx.author.id
            session.work_min = work_minutes
            session.short_brk = short_break
            session.long_brk = long_break
            session.interval = long_break_interval
            session.session_count = 0
            session.active = False
            self.manager.update_index(ctx.author.id)

        runner = PomoRunner(session, voice_client, ctx, self.stats, self.audio, self.manager, ctx.author.id)
        try:
            await runner.run()
        finally:
            session.active = False
            session.pomo_view = None
            session.pomo_msg = None
            session.control_msg = None
            session.join_view = None
            session.join_msg = None
            self.manager.update_index(ctx.author.id)

    @commands.command()
    async def timer(self, ctx):
        result = self.manager.find_by_user(ctx.author.id)
        if result is None:
            await ctx.send("ℹ️ 稼働中のタイマーはありません。")
            return
        author_id, session = result
        if not session.active:
            await ctx.send("ℹ️ 稼働中のタイマーはありません。")
            return

        embed = discord.Embed(title="🍅 タイマー情報", color=discord.Color.red())
        embed.add_field(
            name="タイマー設定",
            value=(
                f"作業: {session.work_min}分 / "
                f"小休憩: {session.short_brk}分 / "
                f"長休憩: {session.long_brk}分 / "
                f"長休憩頻度: {session.interval}回ごと"
            ),
            inline=False,
        )
        total_work = sum(session.session_work.values())
        embed.add_field(
            name="進捗",
            value=f"完了セッション: {session.session_count}回\n合計作業時間: {total_work}分",
            inline=False,
        )
        all_ids = session.get_all_member_ids()
        participant_lines = []
        for uid in all_ids:
            minutes = session.session_work.get(uid, 0)
            label = "（ホスト）" if uid == session.host_id else ""
            participant_lines.append(f"<@{uid}>{label}: {minutes}分")
        embed.add_field(
            name=f"参加者 ({len(all_ids)}人)",
            value="\n".join(participant_lines),
            inline=False,
        )
        await ctx.send(embed=embed)

        # 参加パネルを最新位置に再投稿
        if session.join_view and session.join_msg:
            for child in session.join_view.children:
                if isinstance(child, Button):
                    child.disabled = True
            try:
                await session.join_msg.edit(view=session.join_view)
            except discord.HTTPException:
                pass
        join_view = JoinView(session, self.manager, author_id)
        session.join_view = join_view
        session.join_msg = await ctx.send(
            f"🙋 参加パネル (手動更新)\n対象: {session.get_target_line()}",
            view=join_view,
        )

    @commands.command()
    async def add(self, ctx, user: discord.Member):
        if user.bot:
            await ctx.send("⚠️ Botは加算対象に追加できません。")
            return

        resolved = await self._resolve_owned_session(ctx.author.id)
        if resolved is None:
            session = self.manager.get(ctx.author.id)
        else:
            _, session = resolved

        if session is None:
            session = self.manager.create(ctx.author.id)
            author_id = ctx.author.id
        else:
            result = self.manager.find_by_user(ctx.author.id)
            author_id = result[0] if result else ctx.author.id

        session.add_member(user.id)
        self.manager.update_index(author_id)
        await ctx.send(f"✅ {ctx.author.mention} のタイマー対象に {user.mention} を追加しました。")

    @commands.command(name="list")
    async def list_targets(self, ctx):
        resolved = await self._resolve_owned_session(ctx.author.id)
        if resolved is None:
            session = self.manager.get(ctx.author.id)
            if session is None:
                await ctx.send(f"ℹ️ {ctx.author.mention} の追加対象はありません。")
                return
        else:
            _, session = resolved

        if session is None:
            await ctx.send(f"ℹ️ {ctx.author.mention} の追加対象はありません。")
            return
        if not session.targets:
            await ctx.send(f"ℹ️ {ctx.author.mention} の追加対象はありません。")
            return
        mentions = " ".join([f"<@{user_id}>" for user_id in sorted(session.targets)])
        await ctx.send(f"📌 <@{session.host_id}> のタイマー対象: {mentions}")

    @commands.command()
    async def remove(self, ctx, user: discord.Member):
        if user.bot:
            await ctx.send("⚠️ Botは加算対象に含まれていません。")
            return

        result = await self._resolve_owned_session(ctx.author.id)
        if result is None:
            await ctx.send(f"ℹ️ {user.mention} は {ctx.author.mention} の対象に登録されていません。")
            return
        author_id, session = result

        if user.id not in session.targets:
            await ctx.send(f"ℹ️ {user.mention} は {ctx.author.mention} の対象に登録されていません。")
            return

        session.remove_member(user.id)
        self.manager.update_index(author_id)
        await ctx.send(f"✅ {ctx.author.mention} のタイマー対象から {user.mention} を削除しました。")

    @commands.command(name="stats")
    async def stats_cmd(self, ctx):
        row = await self.stats.get_stats(ctx.author.id)
        if row:
            minutes, sessions = row
            await ctx.send(
                f"📊 **{ctx.author.display_name} さんの記録**\n"
                f"累計作業時間: {minutes}分\n"
                f"完了セッション: {sessions}回"
            )
        else:
            await ctx.send("まだ記録がありません。!pomo で作業を始めましょう！")

    @commands.command()
    async def reset(self, ctx):
        before = await self.stats.reset_stats(ctx.author.id)
        if before is None:
            await ctx.send("ℹ️ リセットする記録がありません。")
            return
        minutes, sessions = before
        await ctx.send(
            f"🧹 記録をリセットしました。\n"
            f"削除前: {minutes}分 / {sessions}セッション"
        )

    @commands.command()
    async def mute(self, ctx):
        resolved = await self._resolve_owned_session(ctx.author.id)
        if resolved is None:
            await ctx.send("ℹ️ 稼働中のタイマーはありません。")
            return
        author_id, session = resolved
        if not session.active:
            await ctx.send("ℹ️ 稼働中のタイマーはありません。")
            return
        session.muted = not session.muted
        self.manager.update_index(author_id)
        await ctx.send("🔇 通知音をミュートしました。" if session.muted else "🔊 通知音のミュートを解除しました。")

    @commands.command()
    async def test(self, ctx):
        if not ctx.author.voice:
            await ctx.send("ボイスチャンネルに入ってからコマンドを打ってください。")
            return

        vc = await ctx.author.voice.channel.connect()
        await asyncio.sleep(1.5)

        if self.audio.file_exists():
            print("[DEBUG] ファイルを検出しました。再生を開始します...")
            vc.play(discord.FFmpegPCMAudio(self.audio.sound_file))
            while vc.is_playing():
                await asyncio.sleep(1)
            print("[DEBUG] 再生が終了しました。")
            await asyncio.sleep(1.0)
            await vc.disconnect()
        else:
            await ctx.send("❌ ding.mp3 が見つかりません！")
            await vc.disconnect()

    @commands.command(name="help")
    async def help_command(self, ctx):
        embed = discord.Embed(
            title="🍅 Pomodoro Bot コマンド一覧",
            description="ポモドーロタイマーを使って作業時間を管理しましょう！",
            color=discord.Color.red(),
        )

        embed.add_field(
            name="!pomo [作業時間] [小休憩] [長休憩] [長休憩頻度]",
            value="ポモドーロタイマーを開始します。\n"
            "デフォルト: `!pomo 25 5 15 4`\n"
            "例: `!pomo 50 10 20 4` → 50分作業、10分小休憩、20分長休憩、4回ごと\n"
            "※事前にボイスチャンネルに参加してください。",
            inline=False,
        )
        embed.add_field(
            name="!timer",
            value="現在の参加パネルを最新位置に再投稿します。",
            inline=False,
        )
        embed.add_field(
            name="!add @user",
            value="指定ユーザーをあなたのタイマー対象に追加します。\n"
            "作業中、同じVCにいる対象ユーザーの記録が加算されます。",
            inline=False,
        )
        embed.add_field(name="!list", value="現在の加算対象ユーザー一覧を表示します。", inline=False)
        embed.add_field(name="!remove @user", value="指定ユーザーを加算対象から削除します。", inline=False)
        embed.add_field(name="!stats", value="あなたの累計作業時間と完了セッション数を表示します。", inline=False)
        embed.add_field(name="!reset", value="あなたの統計をリセットします。", inline=False)
        embed.add_field(name="!mute", value="タイマー通知音のミュート切替を行います。", inline=False)
        embed.add_field(name="!test", value="ボイスチャンネルで音声再生テストを行います。", inline=False)
        embed.add_field(name="!help", value="このヘルプメッセージを表示します。", inline=False)
        embed.set_footer(text="タイマー中は一時停止⏸️・再開▶️・終了⏹️・参加🙋・退出👋ボタンが使用できます。")
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return
        if before.channel is None:
            return

        result = self.manager.find_by_user(member.id)
        if result is None:
            return
        author_id, session = result
        if not session.active:
            return

        if member.id == session.host_id:
            new_host = session.transfer_host()
            self.manager.update_index(author_id)
            if session.control_msg:
                if new_host is None:
                    await session.control_msg.channel.send("ℹ️ ホストが退出しました。残りメンバーがいないためセッションは自動終了します。")
                else:
                    await session.control_msg.channel.send(f"👑 ホストが <@{new_host}> に移行しました。")
        else:
            if session.remove_member(member.id):
                self.manager.update_index(author_id)


async def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("エラー: DISCORD_BOT_TOKEN 環境変数が設定されていません。")
        print("以下のコマンドで設定してください:")
        print("  export DISCORD_BOT_TOKEN='your_token_here'")
        raise SystemExit(1)

    stats = StatsRepository(DB_FILE)
    await stats.init()

    manager = SessionManager()
    audio = AudioPlayer(SOUND_FILE)

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

    await bot.add_cog(PomoCog(bot, manager, stats, audio))
    await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())