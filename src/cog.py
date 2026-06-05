from __future__ import annotations

import asyncio

import discord
from discord.ext import commands
from discord.ui import Button

from audio import AudioPlayer
from logging_utils import log_action, log_from_context
from runner import PomoRunner
from session import PomoSession, SessionManager
from storage import StatsRepository
from views import JoinView


"""
Discordのコマンド群を定義するCog。

`PomoCog` はユーザーコマンド（`!pomo` など）を受け取り、セッション作成、
タイマー設定、統計表示、参加者操作などをハンドルします。
"""


class PomoCog(commands.Cog):
    """Botのコマンドとイベントハンドラをまとめた Cog クラス。

    - `pomo`: ポモドーロを開始する主要コマンド
    - `tconfig`, `tlist`, `pdel` などのタイマー管理コマンド
    - voice state の更新を監視してホスト移譲などを行うリスナー
    """
    def __init__(
        self,
        bot: commands.Bot,
        manager: SessionManager,
        stats: StatsRepository,
        audio: AudioPlayer,
    ):
        # Cogの初期化: botやセッション管理、ストレージ、音声プレイヤーを保持
        self.bot = bot
        self.manager = manager
        self.stats = stats
        self.audio = audio

    async def _resolve_owned_session(self, user_id: int) -> tuple[int, PomoSession] | None:
        # ユーザーが所有する（ホストである）セッションを解決する
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
        # Botが起動して準備完了したときにログ出力する
        log_action(
            guild_name="-",
            user_name=getattr(self.bot.user, "display_name", None) or getattr(self.bot.user, "name", None),
            action=f"{self.bot.user} としてログインしました。" if self.bot.user else "Bot としてログインしました。",
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        # サーバー参加時のログを出力する
        log_action(
            guild_name=guild.name,
            user_name=getattr(self.bot.user, "display_name", None) or getattr(self.bot.user, "name", None),
            action=f"サーバー参加: {guild.id}",
        )

    @commands.command(aliases=["p"])
    async def pomo(
        self,
        ctx,
        timer_name: str = "original",
        work_min: int | None = None,
        short_brk: int | None = None,
        long_brk: int | None = None,
        interval: int | None = None,
    ):
        # ポモドーロを開始するコマンド処理
        log_from_context(
            ctx,
            f"!pomo {timer_name}"
            + (
                ""
                if all(v is None for v in (work_min, short_brk, long_brk, interval))
                else f" {work_min} {short_brk} {long_brk} {interval}"
            ),
        )

        provided = [work_min, short_brk, long_brk, interval]
        provided_count = sum(v is not None for v in provided)
        if provided_count not in (0, 4):
            await ctx.send(
                "⚠️ 時間指定は4つすべて入力してください。\n"
                "使い方: `!p <timer_name> <作業分> <小休憩分> <長休憩分> <長休憩頻度>`"
            )
            return

        if provided_count == 4:
            if (work_min or 0) <= 0:
                await ctx.send("⚠️ 作業時間は1以上で指定してください。")
                return
            if (short_brk or 0) < 0 or (long_brk or 0) < 0:
                await ctx.send("⚠️ 休憩時間は0以上で指定してください。")
                return
            if (interval or 0) <= 0:
                await ctx.send("⚠️ 長休憩頻度は1以上で指定してください。")
                return
        if not ctx.author.voice or not ctx.author.voice.channel:
            # ボイスチャンネルに参加していなければエラーを返す
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

        log_action(
            # 成功した接続をログに残す
            guild_name=getattr(ctx.guild, "name", None),
            user_name=ctx.author.display_name,
            action=f"VC接続成功: {target_channel.name}",
        )

        existing = self.manager.get(ctx.author.id)
        if existing is not None and existing.active:
            await ctx.send("⚠️ すでにあなたのタイマーが動作中です。")
            return

        if existing is None:
            session = self.manager.create(ctx.author.id)
        else:
            session = existing
            session.host_id = ctx.author.id
            session.reset_members()
            session.session_count = 0
            session.active = False
            session.stop_requested = False
            self.manager.update_index(ctx.author.id)

        await self.stats.upsert_user(ctx.author.id, ctx.author.display_name)

        # タイマー設定を取得または作成する（サーバー単位）
        guild_id = ctx.guild.id if ctx.guild else None
        if provided_count == 4:
            await self.stats.upsert_timer(
                ctx.author.id,
                timer_name,
                guild_id,
                int(work_min),
                int(short_brk),
                int(long_brk),
                int(interval),
            )

        timer = await self.stats.get_timer_by_name(ctx.author.id, timer_name, guild_id)
        if not timer:
            await self.stats.upsert_timer(
                ctx.author.id,
                timer_name,
                guild_id,
                25,
                5,
                15,
                4,
            )
            timer = await self.stats.get_timer_by_name(ctx.author.id, timer_name, guild_id)

        if not timer:
            await ctx.send(f"⚠️ タイマー `{timer_name}` の作成に失敗しました。")
            try:
                await voice_client.disconnect()
            except Exception:
                pass
            return

        timer_id = timer["id"]
        session.work_min = timer["work_min"]
        session.short_brk = timer["short_brk"]
        session.long_brk = timer["long_brk"]
        session.interval = timer["interval"]
        session.timer_name = timer_name

        runner = PomoRunner(session, voice_client, ctx, self.stats, self.audio, self.manager, ctx.author.id, timer_id)
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
            session.reset_members()
            self.manager.update_index(ctx.author.id)

    @commands.command(aliases=["t"])
    async def timer(self, ctx):
        # 現在のタイマー情報を表示するコマンド
        log_from_context(ctx, "!timer")
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
        embed.add_field(
            name="タイマー名",
            value=session.timer_name,
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
        # 指定ユーザーを自分のタイマー対象に追加するコマンド
        log_from_context(ctx, f"!add {user.display_name}")
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

    @commands.command(name="tlist", aliases=["tls", "tl"])
    async def tlist(self, ctx):
        # 自分のタイマー一覧を表示するコマンド
        log_from_context(ctx, "!tlist")
        timers = await self.stats.list_timers(ctx.author.id, ctx.guild.id if ctx.guild else None)
        if not timers:
            await ctx.send("ℹ️ タイマーがまだありません。`!pomo` を実行すると `original` タイマーが自動作成されます。")
            return

        embed = discord.Embed(title="⏱️ タイマー一覧", color=discord.Color.red())
        for t in timers:
            embed.add_field(
                name=t["name"],
                value=(
                    f"作業: {t['work_min']}分 / "
                    f"小休憩: {t['short_brk']}分 / "
                    f"長休憩: {t['long_brk']}分 / "
                    f"長休憩頻度: {t['interval']}回ごと"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="tconfig", aliases=["tc"])
    async def tconfig(self, ctx, name: str, work_min: int = 25, short_brk: int = 5, long_brk: int = 15, interval: int = 4):
        # タイマー設定を作成/更新するコマンド
        log_from_context(ctx, f"!tconfig {name} {work_min} {short_brk} {long_brk} {interval}")
        await self.stats.upsert_user(ctx.author.id, ctx.author.display_name)
        await self.stats.upsert_timer(
            ctx.author.id,
            name,
            ctx.guild.id if ctx.guild else None,
            work_min,
            short_brk,
            long_brk,
            interval,
        )
        updated_running_session = False
        resolved = await self._resolve_owned_session(ctx.author.id)
        if resolved is not None:
            author_id, session = resolved
            if session.active and session.timer_name == name:
                session.work_min = work_min
                session.short_brk = short_brk
                session.long_brk = long_brk
                session.interval = interval
                self.manager.update_index(author_id)
                updated_running_session = True

        suffix = "\n現在稼働中の同名タイマーにも反映しました。" if updated_running_session else ""
        await ctx.send(
            f"✅ タイマー `{name}` を設定しました。\n"
            f"作業: {work_min}分 / 小休憩: {short_brk}分 / 長休憩: {long_brk}分 / 長休憩頻度: {interval}回ごと"
            f"{suffix}"
        )

    @commands.command()
    async def pdel(self, ctx, name: str):
        # タイマーを削除するコマンド
        log_from_context(ctx, f"!pdel {name}")
        result = await self.stats.delete_timer(ctx.author.id, name, ctx.guild.id if ctx.guild else None)
        if result:
            await ctx.send(f"🗑️ タイマー `{name}` を削除しました。")
        else:
            timer = await self.stats.get_timer_by_name(ctx.author.id, name, ctx.guild.id if ctx.guild else None)
            if timer is None:
                await ctx.send(f"ℹ️ タイマー `{name}` は存在しません。")
            else:
                await ctx.send(f"⚠️ タイマー `{name}` には記録が残っているため削除できません。")

    @commands.command(aliases=["rm"])
    async def remove(self, ctx, user: discord.Member):
        # 自分のタイマー対象からユーザーを削除するコマンド
        log_from_context(ctx, f"!remove {user.display_name}")
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

    @commands.command(name="stats", aliases=["st"])
    async def stats_cmd(self, ctx, timer_name: str = ""):
        # 統計を表示するコマンド（タイマー名指定でそのタイマーの詳細）
        log_from_context(ctx, f"!stats {timer_name}".strip())

        if not timer_name:
            rows = await self.stats.get_stats_per_timer(ctx.author.id)
            if not rows:
                await ctx.send("まだ記録がありません。`!pomo` で作業を始めましょう！")
                return
            embed = discord.Embed(
                title=f"📊 {ctx.author.display_name} さんの記録",
                color=discord.Color.red(),
            )
            for row in rows:
                embed.add_field(
                    name=row["timer_name"],
                    value=f"累計作業時間: {row['total_minutes']}分",
                    inline=False,
                )
            await ctx.send(embed=embed)
        else:
            timer = await self.stats.get_timer_by_name(ctx.author.id, timer_name, ctx.guild.id if ctx.guild else None)
            if timer is None:
                await ctx.send(f"ℹ️ タイマー `{timer_name}` が見つかりません。")
                return
            row = await self.stats.get_stats_by_timer(ctx.author.id, timer["id"])
            if row is None:
                await ctx.send(f"ℹ️ タイマー `{timer_name}` にはまだ記録がありません。")
                return
            await ctx.send(
                f"📊 **{ctx.author.display_name} さんの `{timer_name}` の記録**\n"
                f"累計作業時間: {row['total_minutes']}分\n"
                f"完了セッション: {row['total_sessions']}回"
            )

    @commands.command()
    async def reset(self, ctx):
        # 自分の統計をリセットするコマンド
        log_from_context(ctx, "!reset")
        before = await self.stats.reset_stats(ctx.author.id)
        if before is None:
            await ctx.send("ℹ️ リセットする記録がありません。")
            return
        minutes = before["total_minutes"]
        sessions = before["total_sessions"]
        await ctx.send(
            f"🧹 記録をリセットしました。\n"
            f"削除前: {minutes}分 / {sessions}セッション"
        )

    @commands.command()
    async def mute(self, ctx):
        # 通知音のミュート切替コマンド
        log_from_context(ctx, "!mute")
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
        # 音声再生をテストするコマンド
        log_from_context(ctx, "!test")
        if not ctx.author.voice:
            await ctx.send("ボイスチャンネルに入ってからコマンドを打ってください。")
            return

        vc = await ctx.author.voice.channel.connect()
        log_action(
            guild_name=getattr(ctx.guild, "name", None),
            user_name=ctx.author.display_name,
            action=f"VC接続成功: {ctx.author.voice.channel.name}",
        )
        await asyncio.sleep(1.5)

        test_text = "業務連絡。ナースロボ、タイプＴです。音声出力を確認しました。システム正常です。"
        if not await self.audio.play_voice(vc, test_text):
            await ctx.send("⚠️ VOICEVOX の音声生成に失敗したため、音声テストをスキップしました。")
        await vc.disconnect()

    @commands.command(name="help", aliases=["h"])
    async def help_command(self, ctx):
        # ヘルプメッセージを表示するコマンド
        log_from_context(ctx, "!help")
        embed = discord.Embed(
            title="🍅 Pomodoro Bot コマンド一覧",
            description="ポモドーロタイマーを使って作業時間を管理しましょう！",
            color=discord.Color.red(),
        )

        embed.add_field(
            name="!pomo [timer_name]",
            value=(
                "ポモドーロタイマーを開始します。\n"
                "短縮形: `!p`\n"
                "タイマー名を省略すると `original` を使用します。\n"
                "例: `!p work` → `work` タイマーで起動\n"
                "例: `!p work 50 10 20 4` → `work` を設定更新してそのまま起動\n"
                "※事前にボイスチャンネルに参加してください。"
            ),
            inline=False,
        )
        embed.add_field(
            name="!tconfig <name> [作業] [小休憩] [長休憩] [頻度]",
            value=(
                "名前付きタイマーの設定を作成・上書きします。\n"
                "デフォルト値: `25 5 15 4`\n"
                "例: `!tconfig work 50 10 20 4`"
            ),
            inline=False,
        )
        embed.add_field(
            name="!tlist",
            value="あなたのタイマー設定一覧を表示します。\n短縮形: `!tl`",
            inline=False,
        )
        embed.add_field(
            name="!pdel <name>",
            value="指定タイマーを削除します。記録が残っているタイマーは削除できません。",
            inline=False,
        )
        embed.add_field(
            name="!timer",
            value="現在の参加パネルを最新位置に再投稿します。\n短縮形: `!t`",
            inline=False,
        )
        embed.add_field(
            name="!add @user",
            value=(
                "指定ユーザーをあなたのタイマー対象に追加します。\n"
                "作業中、同じVCにいる対象ユーザーの記録が加算されます。"
            ),
            inline=False,
        )
        embed.add_field(
            name="!remove @user",
            value="指定ユーザーを加算対象から削除します。\n短縮形: `!rm`",
            inline=False,
        )
        embed.add_field(
            name="!stats [timer_name]",
            value=(
                "記録を表示します。\n"
                "名前省略時: 全タイマーの作業時間一覧\n"
                "名前指定時: そのタイマーの詳細統計\n"
                "短縮形: `!st`"
            ),
            inline=False,
        )
        embed.add_field(name="!reset", value="あなたの統計をリセットします。", inline=False)
        embed.add_field(name="!mute", value="タイマー通知音のミュート切替を行います。", inline=False)
        embed.add_field(name="!test", value="ボイスチャンネルで音声再生テストを行います。", inline=False)
        embed.add_field(name="!help", value="このヘルプメッセージを表示します。\n短縮形: `!h`", inline=False)
        embed.set_footer(text="タイマー中は一時停止⏸️・再開▶️・終了⏹️・参加🙋・退出👋ボタンが使用できます。")

        # チャンネル内に既に投稿されている同タイトルのヘルプを検索する。
        # 実行ごとに再投稿するため、既存helpは先に削除して整理する。
        help_title = embed.title
        help_messages: list[discord.Message] = []
        async for m in ctx.channel.history(limit=100):
            if m.author == self.bot.user and m.embeds:
                try:
                    e = m.embeds[0]
                    if getattr(e, "title", None) == help_title:
                        help_messages.append(m)
                except Exception:
                    continue

        # 既存のhelpは整理し、実行ごとに新しいhelpを再投稿する。
        if help_messages:
            for old in help_messages:
                try:
                    await old.delete()
                except Exception:
                    pass

        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # ボイス状態の変更を監視し、ホスト移譲や参加者の退出を処理する
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
