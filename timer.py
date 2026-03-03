import discord
from discord.ext import commands
from discord.ui import Button, View
import asyncio
import aiosqlite
import os

# Botの設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# 音声ファイルのパス（同じフォルダに ding.mp3 を置いてください）
SOUND_FILE = "ding.mp3"
DB_FILE = "pomo.db"

# タイマーごとの加算対象（コマンド実行者ID -> set(ユーザーID)）
timer_targets = {}

# アクティブなタイマー情報（コマンド実行者ID -> dict）
active_timers = {}

# データベースの初期化
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                user_id INTEGER PRIMARY KEY,
                total_minutes INTEGER DEFAULT 0,
                sessions INTEGER DEFAULT 0
            )
        """)
        await db.commit()


def _transfer_host(author_id):
    """現在のホストをtimer_targetsの中で最も参加が早い人に移行する。
    成功したら新ホストIDを返す。候補がなければNoneを返す。"""
    timer_info = active_timers.get(author_id)
    if timer_info is None:
        return None
    targets = timer_targets.get(author_id, set())
    join_order = timer_info.get("join_order", [])
    for uid in join_order:
        if uid in targets:
            timer_info["host_id"] = uid
            targets.discard(uid)
            return uid
    return None


# ボタンUIの定義
class PomoView(View):
    def __init__(self, author_id):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.paused = False
        self.stopped = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # ホストまたは参加者がボタンを押せる
        host_id = active_timers.get(self.author_id, {}).get("host_id", self.author_id)
        allowed_ids = {host_id} | timer_targets.get(self.author_id, set())
        return interaction.user.id in allowed_ids

    @discord.ui.button(label="一時停止", style=discord.ButtonStyle.secondary, emoji="⏸️")
    async def pause_button(self, interaction: discord.Interaction, button: Button):
        self.paused = True
        button.disabled = True
        self.children[1].disabled = False  # 再開ボタンを有効化
        await interaction.response.edit_message(content="⏸️ タイマーを一時停止しました。", view=self)

    @discord.ui.button(label="再開", style=discord.ButtonStyle.success, emoji="▶️", disabled=True)
    async def resume_button(self, interaction: discord.Interaction, button: Button):
        self.paused = False
        button.disabled = True
        self.children[0].disabled = False  # 一時停止ボタンを有効化
        await interaction.response.edit_message(content="▶️ タイマーを再開します。", view=self)

    @discord.ui.button(label="終了", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: Button):
        self.stopped = True
        await interaction.response.edit_message(content="⏹️ タイマーを終了しました。", view=None)
        self.stop()

# 参加ボタンUIの定義
class JoinView(View):
    def __init__(self, author_id):
        super().__init__(timeout=None)
        self.author_id = author_id

    @discord.ui.button(label="参加", style=discord.ButtonStyle.primary, emoji="🙋")
    async def join_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        if user.bot:
            return

        timer_info = active_timers.get(self.author_id)
        host_id = timer_info.get("host_id", self.author_id) if timer_info else self.author_id

        # 既にホストなら参加済み
        if user.id == host_id:
            await interaction.response.send_message("⚠️ 既に参加しています。", ephemeral=True)
            return

        targets = timer_targets.setdefault(self.author_id, set())
        if user.id in targets:
            await interaction.response.send_message(f"ℹ️ {user.mention} は既に参加しています。", ephemeral=True)
            return

        targets.add(user.id)
        # join_orderに追加（参加順を記録）
        if timer_info and user.id not in timer_info.get("join_order", []):
            timer_info["join_order"].append(user.id)
        await interaction.response.send_message(f"🙋 {user.mention} が参加しました！")

        # ボタンのメッセージを更新して現在の参加者を表示
        target_line = get_target_line(self.author_id)
        host_id = active_timers.get(self.author_id, {}).get("host_id", self.author_id)
        await interaction.message.edit(
            content=f"🛑 **<@{host_id}> のタイマー**\n対象: {target_line}\n参加するには下のボタンを押してください。",
            view=self
        )

    @discord.ui.button(label="退出", style=discord.ButtonStyle.secondary, emoji="👋")
    async def leave_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        if user.bot:
            return

        timer_info = active_timers.get(self.author_id)
        host_id = timer_info.get("host_id", self.author_id) if timer_info else self.author_id
        targets = timer_targets.get(self.author_id, set())

        # ホストが退出する場合 → ホスト移行
        if user.id == host_id:
            new_host = _transfer_host(self.author_id)
            if new_host:
                await interaction.response.send_message(
                    f"👋 {user.mention} が退出しました。ホストが <@{new_host}> に移行しました。"
                )
            else:
                await interaction.response.send_message(
                    f"👋 {user.mention} が退出しました。タイマーは参加者がいる限り継続します。"
                )
            target_line = get_target_line(self.author_id)
            new_host_id = active_timers.get(self.author_id, {}).get("host_id", self.author_id)
            await interaction.message.edit(
                content=f"🛑 **<@{new_host_id}> のタイマー**\n対象: {target_line}\n参加するには下のボタンを押してください。",
                view=self
            )
            return

        # 一般参加者の退出
        if user.id not in targets:
            await interaction.response.send_message(f"ℹ️ {user.mention} は参加していません。", ephemeral=True)
            return

        targets.discard(user.id)
        await interaction.response.send_message(f"👋 {user.mention} が退出しました。")

        target_line = get_target_line(self.author_id)
        await interaction.message.edit(
            content=f"🛑 **<@{host_id}> のタイマー**\n対象: {target_line}\n参加するには下のボタンを押してください。",
            view=self
        )


def get_target_line(author_id, guild=None):
    """ホストと参加者のメンション文字列を構築する"""
    host_id = active_timers.get(author_id, {}).get("host_id", author_id)
    mentions = [f"<@{host_id}>"]
    extra_ids = timer_targets.get(author_id, set())
    if extra_ids:
        mentions += [f"<@{user_id}>" for user_id in extra_ids]
    return " ".join(mentions)


def has_active_members(voice_client, author_id):
    """ボットのVCにホストまたは参加者が残っているかチェック"""
    if not voice_client or not voice_client.is_connected():
        return False
    if not voice_client.channel:
        return False
    try:
        vc_member_ids = {m.id for m in voice_client.channel.members if not m.bot}
    except Exception:
        return False
    host_id = active_timers.get(author_id, {}).get("host_id", author_id)
    targets = {host_id} | timer_targets.get(author_id, set())
    return bool(vc_member_ids & targets)


@bot.event
async def on_ready():
    await init_db()
    print(f"{bot.user} としてログインしました。")

@bot.event
async def on_voice_state_update(member, before, after):
    """参加者やホストがVCから退出したら処理する"""
    # チャンネルが変わっていない場合は無視（ミュート切替など）
    if before.channel == after.channel:
        return

    # VCから退出した、または別のチャンネルに移動した場合
    if before.channel is not None:
        for author_id, targets in list(timer_targets.items()):
            timer_info = active_timers.get(author_id)
            if timer_info is None:
                continue
            host_id = timer_info.get("host_id", author_id)

            if member.id == host_id:
                # ホストが退出 → 移行
                _transfer_host(author_id)
            elif member.id in targets:
                # 一般参加者が退出
                targets.discard(member.id)

@bot.command()
async def pomo(ctx, work_minutes: int = 25, short_break: int = 5, long_break: int = 15, long_break_interval: int = 4):
    """
    !pomo [作業時間] [小休憩] [長休憩] [長休憩頻度] でタイマーを開始します
    デフォルト: 作業25分、小休憩5分、長休憩15分、4セッションごとに長休憩
    例: !pomo 50 10 20 4 → 50分作業、10分小休憩、20分長休憩、4回ごと
    """
    # ボイスチャンネルへの接続処理
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

    # 接続が完全に確立されるまで待つ
    await asyncio.sleep(1)
    if not voice_client.is_connected():
        await ctx.send("⚠️ ボイスチャンネルの接続に失敗しました。もう一度お試しください。")
        return

    # デバッグ: 接続状態確認
    try:
        vc_members = voice_client.channel.members
        print(f"[DEBUG] 接続直後のVC メンバー数: {len(vc_members)}")
        for m in vc_members:
            print(f"  - {m.name} (ID: {m.id}, Bot: {m.bot})")
        print(f"[DEBUG] ctx.author: {ctx.author.name} (ID: {ctx.author.id})")
    except Exception as e:
        print(f"[DEBUG] メンバー確認エラー: {e}")

    session_count = 0
    join_view = JoinView(ctx.author.id)
    target_line = get_target_line(ctx.author.id)
    control_msg = await ctx.send(
        f"🛑 **{ctx.author.mention} のタイマー**\n対象: {target_line}\n参加するには下のボタンを押してください。",
        view=join_view
    )

    # アクティブタイマー情報を登録
    active_timers[ctx.author.id] = {
        "work_minutes": work_minutes,
        "short_break": short_break,
        "long_break": long_break,
        "long_break_interval": long_break_interval,
        "session_count": 0,
        "session_work": {},  # user_id -> 今回のタイマーでの作業分数
        "muted": False,
        "host_id": ctx.author.id,  # 現在のホスト
        "join_order": [ctx.author.id],  # 参加順リスト（ホスト移行時に使用）
        "control_msg": control_msg,  # 参加パネルのメッセージ参照
        "join_view": join_view,      # JoinViewの参照
    }

    # デバッグ: has_active_members チェック
    has_members = has_active_members(voice_client, ctx.author.id)
    print(f"[DEBUG] has_active_members: {has_members}")

    # ホストまたは参加者がボイスチャンネルにいる限り繰り返す
    while has_active_members(voice_client, ctx.author.id):
        session_count += 1

        # 作業タイマー
        view = PomoView(ctx.author.id)
        target_line = get_target_line(ctx.author.id)
        host_id = active_timers[ctx.author.id]["host_id"]

        msg = await ctx.send(
            f"🍅 **<@{host_id}> のセッション {session_count} 開始！** ({work_minutes}分)\n"
            f"対象: {target_line}\n集中しましょう！",
            view=view
        )
        active_timers[ctx.author.id]["pomo_view"] = view
        active_timers[ctx.author.id]["pomo_msg"] = msg

        # 古い参加パネルを無効化し、新しいパネルをセットで送信
        old_join_msg = active_timers[ctx.author.id].get("control_msg")
        if old_join_msg:
            try:
                await old_join_msg.edit(content="⚠️ このパネルは移動しました。最新は下をご確認ください。", view=None)
            except Exception:
                pass
        new_join_view = JoinView(ctx.author.id)
        control_msg = await ctx.send(
            f"👥 **参加 / 退出パネル**\n対象: {target_line}",
            view=new_join_view
        )
        active_timers[ctx.author.id]["control_msg"] = control_msg
        active_timers[ctx.author.id]["join_view"] = new_join_view

        remaining_seconds = work_minutes * 60

        # 作業タイマーのメインループ
        while remaining_seconds > 0:
            if ctx.author.id not in active_timers:
                return
            cur_view = active_timers[ctx.author.id]["pomo_view"]
            cur_msg = active_timers[ctx.author.id]["pomo_msg"]

            # ホスト・参加者が全員VCから退出したかチェック
            if not has_active_members(voice_client, ctx.author.id):
                await cur_msg.edit(content="⏹️ 全員が退出したため終了しました。", view=None)
                timer_targets.pop(ctx.author.id, None)
                active_timers.pop(ctx.author.id, None)
                if voice_client: await voice_client.disconnect()
                return

            if cur_view.stopped:
                join_msg = active_timers[ctx.author.id].get("control_msg")
                if join_msg:
                    try:
                        await join_msg.edit(content="⏹️ ポモドーロを終了しました。お疲れ様でした！", view=None)
                    except Exception:
                        pass
                timer_targets.pop(ctx.author.id, None)
                active_timers.pop(ctx.author.id, None)
                if voice_client: await voice_client.disconnect()
                return

            if cur_view.paused:
                await asyncio.sleep(1)
                continue

            await asyncio.sleep(1)
            remaining_seconds -= 1

            if remaining_seconds % 60 == 0 and remaining_seconds != 0:
                cur_view = active_timers[ctx.author.id]["pomo_view"]
                cur_msg = active_timers[ctx.author.id]["pomo_msg"]
                await cur_msg.edit(content=f"🍅 **残り {remaining_seconds // 60} 分** (セッション {session_count})\n集中しましょう！", view=cur_view)

        # 作業完了 - データベースへ記録（VCにいる かつ タイマーリストに残っている人が対象）
        member_ids = []
        if voice_client and voice_client.is_connected():
            vc_member_ids = {m.id for m in voice_client.channel.members if not m.bot}
            current_host = active_timers.get(ctx.author.id, {}).get("host_id", ctx.author.id)
            targets = set(timer_targets.get(ctx.author.id, set())) | {current_host}
            member_ids = list(vc_member_ids & targets)

        # アクティブタイマー情報を更新
        timer_info = active_timers.get(ctx.author.id)
        if timer_info:
            timer_info["session_count"] = session_count
            for uid in member_ids:
                timer_info["session_work"][uid] = timer_info["session_work"].get(uid, 0) + work_minutes

        async with aiosqlite.connect(DB_FILE) as db:
            if member_ids:
                await db.executemany("""
                    INSERT INTO stats (user_id, total_minutes, sessions)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id) DO UPDATE SET
                    total_minutes = total_minutes + ?,
                    sessions = sessions + 1
                """, [(user_id, work_minutes, work_minutes) for user_id in member_ids])
            await db.commit()

        # 長休憩か小休憩か判定
        is_long_break = (session_count % long_break_interval == 0)
        break_time = long_break if is_long_break else short_break
        break_type = "長休憩" if is_long_break else "小休憩"

        target_line = get_target_line(ctx.author.id)
        host_id = active_timers.get(ctx.author.id, {}).get("host_id", ctx.author.id)

        await msg.edit(
            content=(
                f"🎉 **<@{host_id}> のセッション {session_count} 完了！** "
                f"{work_minutes}分の作業が終わりました。\n"
                f"対象: {target_line}\n💤 {break_type} {break_time}分を開始します..."
            ),
            view=None
        )

        # 音声を再生
        if voice_client and voice_client.is_connected() and not active_timers.get(ctx.author.id, {}).get("muted"):
            if os.path.exists(SOUND_FILE):
                # 既に再生中の場合は停止
                if voice_client.is_playing():
                    voice_client.stop()

                # 音量を上げるため volume オプションを使用
                audio_source = discord.FFmpegPCMAudio(
                    SOUND_FILE,
                    options='-filter:a "volume=1.0"'
                )
                voice_client.play(audio_source)

                # 再生完了を待つ（最大5秒）
                for _ in range(50):
                    if not voice_client.is_playing():
                        break
                    await asyncio.sleep(0.1)
            else:
                await ctx.send("⚠️ 音声ファイル (ding.mp3) が見つかりませんでした。")

        # 休憩タイマー開始
        if break_time > 0:
            break_view = PomoView(ctx.author.id)
            emoji = "☕" if is_long_break else "💤"
            break_msg = await ctx.send(
                f"{emoji} **<@{host_id}> の{break_type}！** ({break_time}分)\n"
                f"対象: {target_line}\nリラックスしましょう！",
                view=break_view
            )
            active_timers[ctx.author.id]["pomo_view"] = break_view
            active_timers[ctx.author.id]["pomo_msg"] = break_msg

            # 古い参加パネルを無効化し、新しいパネルをセットで送信
            old_join_msg = active_timers[ctx.author.id].get("control_msg")
            if old_join_msg:
                try:
                    await old_join_msg.edit(content="⚠️ このパネルは移動しました。最新は下をご確認ください。", view=None)
                except Exception:
                    pass
            new_join_view = JoinView(ctx.author.id)
            control_msg = await ctx.send(
                f"👥 **参加 / 退出パネル**\n対象: {target_line}",
                view=new_join_view
            )
            active_timers[ctx.author.id]["control_msg"] = control_msg
            active_timers[ctx.author.id]["join_view"] = new_join_view

            remaining_seconds = break_time * 60

            while remaining_seconds > 0:
                if ctx.author.id not in active_timers:
                    return
                cur_view = active_timers[ctx.author.id]["pomo_view"]
                cur_msg = active_timers[ctx.author.id]["pomo_msg"]

                # ホスト・参加者が全員VCから退出したかチェック
                if not has_active_members(voice_client, ctx.author.id):
                    await cur_msg.edit(content="⏹️ 全員が退出したため終了しました。", view=None)
                    timer_targets.pop(ctx.author.id, None)
                    active_timers.pop(ctx.author.id, None)
                    if voice_client: await voice_client.disconnect()
                    return

                if cur_view.stopped:
                    join_msg = active_timers[ctx.author.id].get("control_msg")
                    if join_msg:
                        try:
                            await join_msg.edit(content="⏹️ ポモドーロを終了しました。お疲れ様でした！", view=None)
                        except Exception:
                            pass
                    timer_targets.pop(ctx.author.id, None)
                    active_timers.pop(ctx.author.id, None)
                    if voice_client: await voice_client.disconnect()
                    return

                if cur_view.paused:
                    await asyncio.sleep(1)
                    continue

                await asyncio.sleep(1)
                remaining_seconds -= 1

                if remaining_seconds % 60 == 0 and remaining_seconds != 0:
                    host_id = active_timers.get(ctx.author.id, {}).get("host_id", ctx.author.id)
                    target_line = get_target_line(ctx.author.id)
                    cur_view = active_timers[ctx.author.id]["pomo_view"]
                    cur_msg = active_timers[ctx.author.id]["pomo_msg"]
                    await cur_msg.edit(
                        content=(
                            f"{emoji} **<@{host_id}> の残り {remaining_seconds // 60} 分** "
                            f"({break_type})\n対象: {target_line}\nリラックスしましょう！"
                        ),
                        view=cur_view
                    )

            # 休憩終了
            host_id = active_timers.get(ctx.author.id, {}).get("host_id", ctx.author.id)
            target_line = get_target_line(ctx.author.id)
            cur_msg = active_timers.get(ctx.author.id, {}).get("pomo_msg", break_msg)
            await cur_msg.edit(
                content=f"⏰ **<@{host_id}> の{break_type}終了！** 次のセッションを始めましょう。\n対象: {target_line}",
                view=None
            )

            # 音声を再生
            if voice_client and voice_client.is_connected() and not active_timers.get(ctx.author.id, {}).get("muted"):
                if os.path.exists(SOUND_FILE):
                    if voice_client.is_playing():
                        voice_client.stop()

                    audio_source = discord.FFmpegPCMAudio(
                        SOUND_FILE,
                        options='-filter:a "volume=1.5"'
                    )
                    voice_client.play(audio_source)

                    for _ in range(50):
                        if not voice_client.is_playing():
                            break
                        await asyncio.sleep(0.1)

        # 短い待機時間を入れて次のセッションへ
        await asyncio.sleep(2)

    # ループ終了（全員がVCから退出）
    target_line = get_target_line(ctx.author.id)
    timer_targets.pop(ctx.author.id, None)
    active_timers.pop(ctx.author.id, None)

    await control_msg.edit(
        content=f"🎉 **ポモドーロ終了！** 合計 {session_count} セッション完了しました。お疲れ様でした！\n対象: {target_line}",
        view=None
    )

@bot.command()
async def add(ctx, user: discord.Member):
    """加算対象ユーザーを追加します（!add @user）"""
    if user.bot:
        await ctx.send("⚠️ Botは加算対象に追加できません。")
        return

    # ホスト権限チェック
    host_timer_author_id = None
    for author_id, info in active_timers.items():
        if info.get("host_id") == ctx.author.id:
            host_timer_author_id = author_id
            break

    if host_timer_author_id is None:
        await ctx.send("ℹ️ ホストのみがこのコマンドを実行できます。")
        return

    targets = timer_targets.setdefault(host_timer_author_id, set())
    targets.add(user.id)
    await ctx.send(f"✅ タイマー対象に {user.mention} を追加しました。")

@bot.command(name="list")
async def list_targets(ctx):
    """参加しているタイマーのリストを表示します"""
    # 実行者がホストまたは参加者であるタイマーを探す
    participating_timers = []
    for author_id, info in active_timers.items():
        host_id = info.get("host_id", author_id)
        targets = timer_targets.get(author_id, set())
        if ctx.author.id == host_id or ctx.author.id in targets:
            participating_timers.append((author_id, info, host_id))

    if not participating_timers:
        await ctx.send("ℹ️ 参加しているタイマーはありません。")
        return

    # タイマー情報を表示
    lines = []
    for author_id, info, host_id in participating_timers:
        role = "（ホスト）" if ctx.author.id == host_id else "（参加者）"
        targets = timer_targets.get(author_id, set())
        all_ids = {host_id} | targets
        participant_count = len(all_ids)
        lines.append(f"ホスト: <@{host_id}>{role} - 参加者: {participant_count}人")

    await ctx.send("📌 参加しているタイマー:\n" + "\n".join(lines))

@bot.command()
async def remove(ctx, user: discord.Member):
    """加算対象ユーザーを削除します（!remove @user）"""
    if user.bot:
        await ctx.send("⚠️ Botは加算対象に含まれていません。")
        return

    # ホスト権限チェック
    host_timer_author_id = None
    for author_id, info in active_timers.items():
        if info.get("host_id") == ctx.author.id:
            host_timer_author_id = author_id
            break

    if host_timer_author_id is None:
        await ctx.send("ℹ️ ホストのみがこのコマンドを実行できます。")
        return

    targets = timer_targets.get(host_timer_author_id, set())
    if user.id not in targets:
        await ctx.send(f"ℹ️ {user.mention} はタイマー対象に登録されていません。")
        return

    targets.remove(user.id)
    await ctx.send(f"✅ タイマー対象から {user.mention} を削除しました。")

@bot.command()
async def stats(ctx):
    """自分の累計作業時間を表示します"""
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT total_minutes, sessions FROM stats WHERE user_id = ?", (ctx.author.id,)) as cursor:
            row = await cursor.fetchone()

    if row:
        minutes, sessions = row
        await ctx.send(f"📊 **{ctx.author.display_name} さんの記録**\n累計作業時間: {minutes}分\n完了セッション: {sessions}回")
    else:
        await ctx.send("まだ記録がありません。!pomo で作業を始めましょう！")

@bot.command()
async def reset(ctx):
    """自分の累計作業時間をリセットします"""
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT total_minutes, sessions FROM stats WHERE user_id = ?", (ctx.author.id,)) as cursor:
            row = await cursor.fetchone()

        if not row:
            await ctx.send("リセットする記録がありません。")
            return

        minutes, sessions = row
        await db.execute("DELETE FROM stats WHERE user_id = ?", (ctx.author.id,))
        await db.commit()

    await ctx.send(f"🔄 **{ctx.author.display_name} さんの記録をリセットしました**\n削除された記録: {minutes}分 / {sessions}セッション")

@bot.command(name="timer")
async def timer_info(ctx):
    """現在のタイマー情報を表示します"""
    # コマンド実行者がホストまたは参加者であるタイマーを探す
    timer_author_id = None
    for author_id, info in active_timers.items():
        host_id = info.get("host_id", author_id)
        targets = {host_id} | timer_targets.get(author_id, set())
        if ctx.author.id in targets:
            timer_author_id = author_id
            break

    if timer_author_id is None:
        await ctx.send("ℹ️ 現在アクティブなタイマーに参加していません。")
        return

    info = active_timers[timer_author_id]
    session_count = info["session_count"]
    work_minutes = info["work_minutes"]
    short_break = info["short_break"]
    long_break = info["long_break"]
    long_break_interval = info["long_break_interval"]
    session_work = info["session_work"]
    host_id = info.get("host_id", timer_author_id)

    # 全参加者の合計作業時間
    total_work = sum(session_work.values())

    embed = discord.Embed(
        title="🍅 タイマー情報",
        color=discord.Color.red()
    )

    embed.add_field(
        name="タイマー設定",
        value=f"作業: {work_minutes}分 / 小休憩: {short_break}分 / 長休憩: {long_break}分 / 長休憩頻度: {long_break_interval}回ごと",
        inline=False
    )

    embed.add_field(
        name="進捗",
        value=f"完了セッション: {session_count}回\n合計作業時間: {total_work}分",
        inline=False
    )

    # 参加者一覧と各自の作業時間
    all_ids = {host_id} | timer_targets.get(timer_author_id, set())
    participant_lines = []
    for uid in all_ids:
        minutes = session_work.get(uid, 0)
        label = "（ホスト）" if uid == host_id else ""
        participant_lines.append(f"<@{uid}>{label}: {minutes}分")

    embed.add_field(
        name=f"参加者 ({len(all_ids)}人)",
        value="\n".join(participant_lines),
        inline=False
    )

    await ctx.send(embed=embed)

    # 古いPomoViewを無効化し、新しいPomoViewを再投稿
    old_pomo_msg = info.get("pomo_msg")
    if old_pomo_msg:
        try:
            await old_pomo_msg.edit(view=None)
        except Exception:
            pass

    new_pomo_view = PomoView(timer_author_id)
    new_pomo_msg = await ctx.send("⏯️ **タイマーコントロール**", view=new_pomo_view)
    info["pomo_view"] = new_pomo_view
    info["pomo_msg"] = new_pomo_msg

    # 古い参加パネルを無効化し、最新の位置に再投稿
    old_control_msg = info.get("control_msg")
    if old_control_msg:
        try:
            await old_control_msg.edit(content="⚠️ このパネルは移動しました。最新は下をご確認ください。", view=None)
        except Exception:
            pass

    target_line = get_target_line(timer_author_id)
    new_join_view = JoinView(timer_author_id)
    new_control_msg = await ctx.send(
        f"👥 **参加 / 退出パネル**\n対象: {target_line}",
        view=new_join_view
    )
    info["control_msg"] = new_control_msg
    info["join_view"] = new_join_view

@bot.command()
async def mute(ctx):
    """タイマーの通知音をミュート/ミュート解除します"""
    # コマンド実行者がホストまたは参加者であるタイマーを探す
    timer_author_id = None
    for author_id, info in active_timers.items():
        host_id = info.get("host_id", author_id)
        targets = {host_id} | timer_targets.get(author_id, set())
        if ctx.author.id in targets:
            timer_author_id = author_id
            break

    if timer_author_id is None:
        await ctx.send("ℹ️ 現在アクティブなタイマーに参加していません。")
        return

    info = active_timers[timer_author_id]
    info["muted"] = not info["muted"]

    if info["muted"]:
        await ctx.send("🔇 通知音をミュートしました。")
    else:
        await ctx.send("🔊 通知音のミュートを解除しました。")

@bot.command()
async def test(ctx):
    """ボイスチャンネルで音声再生テストを行います"""
    if ctx.author.voice:
        # 1. 接続
        vc = await ctx.author.voice.channel.connect()

        # ★重要: 接続が安定するまで少し待つ（これを入れないと頭切れします）
        await asyncio.sleep(1.5)

        if os.path.exists("ding.mp3"):
            print("ファイルを検出しました。再生を開始します...")

            # 2. 再生
            # options="-loglevel panic" はログを綺麗にするためですが、なくても動きます
            vc.play(discord.FFmpegPCMAudio("ding.mp3"))

            # 再生中ループ
            while vc.is_playing():
                await asyncio.sleep(1)

            print("再生が終了しました。")

            # ★重要: 余韻のため少し待ってから切断
            await asyncio.sleep(1.0)

            await vc.disconnect()
        else:
            await ctx.send("❌ ding.mp3 が見つかりません！")
            await vc.disconnect()
    else:
        await ctx.send("ボイスチャンネルに入ってからコマンドを打ってください。")

@bot.command(name="help")
async def help_command(ctx):
    """ボットの使い方を表示します"""
    embed = discord.Embed(
        title="🍅 Pomodoro Bot コマンド一覧",
        description="ポモドーロタイマーを使って作業時間を管理しましょう！",
        color=discord.Color.red()
    )

    embed.add_field(
        name="!pomo [作業時間] [小休憩] [長休憩] [長休憩頻度]",
        value="ポモドーロタイマーを開始します。\n"
              "デフォルト: `!pomo 25 5 15 4`\n"
              "例: `!pomo 50 10 20 4` → 50分作業、10分小休憩、20分長休憩、4回ごと\n"
              "※事前にボイスチャンネルに参加してください。\n"
              "※他のユーザーは🙋参加 / 👋退出ボタンで参加・退出できます。\n"
              "※ホストが退出すると次の参加者にホストが移行します。",
        inline=False
    )

    embed.add_field(
        name="!timer",
        value="現在のタイマー情報を表示します。\n"
              "タイマー設定、完了セッション数、参加者ごとの作業時間を確認できます。\n"
              "また、埋もれた参加パネルを最新の位置に再投稿します。",
        inline=False
    )

    embed.add_field(
        name="!add @user",
        value="ホストが指定ユーザーをタイマー対象に追加します。\n"
              "※ホスト権限が必要です。",
        inline=False
    )

    embed.add_field(
        name="!remove @user",
        value="ホストが指定ユーザーをタイマー対象から削除します。\n"
              "※ホスト権限が必要です。",
        inline=False
    )

    embed.add_field(
        name="!list",
        value="自分が参加しているタイマーのリストを表示します。\n"
              "ホストと参加者の両方がホスト情報や参加者数を確認できます。",
        inline=False
    )

    embed.add_field(
        name="!stats",
        value="あなたの累計作業時間と完了セッション数を表示します。",
        inline=False
    )

    embed.add_field(
        name="!reset",
        value="あなたの累計作業時間をリセットします。",
        inline=False
    )

    embed.add_field(
        name="!mute",
        value="タイマーの通知音をミュート/ミュート解除します。\n"
              "もう一度実行するとミュート解除されます。",
        inline=False
    )

    embed.add_field(
        name="!test",
        value="ボイスチャンネルで音声再生テストを行います。",
        inline=False
    )

    embed.add_field(
        name="!help",
        value="このヘルプメッセージを表示します。",
        inline=False
    )

    embed.set_footer(text="タイマー中はホスト・参加者が一時停止⏸️・再開▶️・終了⏹️ボタンを使用できます。")

    await ctx.send(embed=embed)

# 環境変数からトークンを取得（セキュリティ向上）
token = os.getenv('DISCORD_BOT_TOKEN')
if not token:
    print("エラー: DISCORD_BOT_TOKEN 環境変数が設定されていません。")
    print("以下のコマンドで設定してください:")
    print("  export DISCORD_BOT_TOKEN='your_token_here'")
    exit(1)

bot.run(token)
