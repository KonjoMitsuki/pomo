from __future__ import annotations

import asyncio

import discord
from discord.ext import commands
from discord.ui import Button

from audio import AudioPlayer
from runner import PomoRunner
from session import PomoSession, SessionManager
from storage import StatsRepository
from views import JoinView


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
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("⚠️ ボイスチャンネルに参加してからコマンドを実行してください。")
            return

        target_channel = ctx.author.voice.channel
        voice_client = ctx.voice_client
        if voice_client is None:
            try:
                voice_client = await target_channel.connect(reconnect=True)
            except Exception as e:
                await ctx.send(f"⚠️ ボイスチャンネルに接続できませんでした: {e}")
                return
        else:
            try:
                if not voice_client.is_connected():
                    try:
                        await voice_client.disconnect(force=True)
                    except Exception:
                        pass
                    voice_client = await target_channel.connect(reconnect=True)
                elif voice_client.channel != target_channel:
                    await voice_client.move_to(target_channel)
            except Exception as e:
                await ctx.send(f"⚠️ ボイスチャンネル接続の更新に失敗しました: {e}")
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
            session.stop_requested = False
            self.manager.update_index(ctx.author.id)

        runner = PomoRunner(session, voice_client, ctx, self.stats, self.audio, self.manager, ctx.author.id)
        try:
            await runner.run()
        finally:
            session.active = False
            session.stop_requested = False
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
            await ctx.send("❌ assets/ding.mp3 が見つかりません！")
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
        if member.bot:
            return

        result = self.manager.find_by_user(member.id)
        if result is None:
            return
        author_id, session = result
        if not session.active:
            return

        if member.id == session.host_id:
            guild_vc = member.guild.voice_client if member.guild else None
            active_ids = set(session.get_vc_active_ids(guild_vc))
            new_host = session.transfer_host(active_ids=active_ids)
            self.manager.update_index(author_id)
            if session.control_msg:
                if new_host is None:
                    session.stop_requested = True
                    print(f"[DEBUG] stop_requested=True (voice_state_update) host={member.id}")
                    await session.control_msg.channel.send("ℹ️ ホストが退出しました。残りメンバーがいないためセッションは自動終了します。")
                else:
                    await session.control_msg.channel.send(f"👑 ホストが <@{new_host}> に移行しました。")
        else:
            if session.remove_member(member.id):
                self.manager.update_index(author_id)
