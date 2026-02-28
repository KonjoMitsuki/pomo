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

# ボタンUIの定義
class PomoView(View):
    def __init__(self, author_id):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.paused = False
        self.stopped = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # コマンド実行者または参加者がボタンを押せる
        allowed_ids = {self.author_id} | timer_targets.get(self.author_id, set())
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
        # Botと起動者自身は対象外
        if user.bot or user.id == self.author_id:
            await interaction.response.send_message("⚠️ 起動者は既に参加しています。", ephemeral=True)
            return

        targets = timer_targets.setdefault(self.author_id, set())
        if user.id in targets:
            await interaction.response.send_message(f"ℹ️ {user.mention} は既に参加しています。", ephemeral=True)
            return

        targets.add(user.id)
        await interaction.response.send_message(f"🙋 {user.mention} が参加しました！")

        # ボタンのメッセージを更新して現在の参加者を表示
        target_line = get_target_line(self.author_id, interaction.guild)
        await interaction.message.edit(
            content=f"🛑 **<@{self.author_id}> のタイマー**\n対象: {target_line}\n参加するには下のボタンを押してください。",
            view=self
        )

    @discord.ui.button(label="退出", style=discord.ButtonStyle.secondary, emoji="👋")
    async def leave_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        if user.bot or user.id == self.author_id:
            await interaction.response.send_message("⚠️ 起動者は退出できません。", ephemeral=True)
            return

        targets = timer_targets.get(self.author_id, set())
        if user.id not in targets:
            await interaction.response.send_message(f"ℹ️ {user.mention} は参加していません。", ephemeral=True)
            return

        targets.discard(user.id)
        await interaction.response.send_message(f"👋 {user.mention} が退出しました。")

        # ボタンのメッセージを更新して現在の参加者を表示
        target_line = get_target_line(self.author_id, interaction.guild)
        await interaction.message.edit(
            content=f"🛑 **<@{self.author_id}> のタイマー**\n対象: {target_line}\n参加するには下のボタンを押してください。",
            view=self
        )


def get_target_line(author_id, guild=None):
    """起動者と参加者のメンション文字列を構築する"""
    mentions = [f"<@{author_id}>"]
    extra_ids = timer_targets.get(author_id, set())
    if extra_ids:
        mentions += [f"<@{user_id}>" for user_id in extra_ids]
    return " ".join(mentions)


def has_active_members(voice_client, author_id):
    """ボットのVCに起動者または参加者が残っているかチェック"""
    if not voice_client or not voice_client.is_connected():
        return False
    vc_member_ids = {m.id for m in voice_client.channel.members if not m.bot}
    targets = {author_id} | timer_targets.get(author_id, set())
    return bool(vc_member_ids & targets)


@bot.event
async def on_ready():
    await init_db()
    print(f"{bot.user} としてログインしました。")

@bot.event
async def on_voice_state_update(member, before, after):
    """参加者がVCから退出したら timer_targets から除外する"""
    # チャンネルが変わっていない場合は無視（ミュート切替など）
    if before.channel == after.channel:
        return

    # VCから退出した、または別のチャンネルに移動した場合
    if before.channel is not None:
        for author_id, targets in timer_targets.items():
            if member.id in targets and member.id != author_id:
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
    }

    # 起動者または参加者がボイスチャンネルにいる限り繰り返す
    while has_active_members(voice_client, ctx.author.id):
        session_count += 1

        # 作業タイマー
        view = PomoView(ctx.author.id)
        target_line = get_target_line(ctx.author.id)

        msg = await ctx.send(
            f"🍅 **{ctx.author.mention} のセッション {session_count} 開始！** ({work_minutes}分)\n"
            f"対象: {target_line}\n集中しましょう！",
            view=view
        )

        remaining_seconds = work_minutes * 60

        # 作業タイマーのメインループ
        while remaining_seconds > 0:
            # 起動者・参加者が全員VCから退出したかチェック
            if not has_active_members(voice_client, ctx.author.id):
                await msg.edit(content="⏹️ 全員が退出したため終了しました。", view=None)
                timer_targets.pop(ctx.author.id, None)
                active_timers.pop(ctx.author.id, None)
                if voice_client: await voice_client.disconnect()
                return

            if view.stopped:
                await control_msg.edit(content="⏹️ ポモドーロを終了しました。お疲れ様でした！")
                timer_targets.pop(ctx.author.id, None)
                active_timers.pop(ctx.author.id, None)
                if voice_client: await voice_client.disconnect()
                return

            if view.paused:
                await asyncio.sleep(1)
                continue

            await asyncio.sleep(1)
            remaining_seconds -= 1

            if remaining_seconds % 60 == 0 and remaining_seconds != 0:
                await msg.edit(content=f"🍅 **残り {remaining_seconds // 60} 分** (セッション {session_count})\n集中しましょう！", view=view)

        # 作業完了 - データベースへ記録（追加された対象 + 実行者、同じVC内のみ）
        member_ids = []
        if voice_client and voice_client.is_connected():
            vc_member_ids = {m.id for m in voice_client.channel.members if not m.bot}
            targets = set(timer_targets.get(ctx.author.id, set())) | {ctx.author.id}
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
            else:
                await db.execute("""
                    INSERT INTO stats (user_id, total_minutes, sessions)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id) DO UPDATE SET
                    total_minutes = total_minutes + ?,
                    sessions = sessions + 1
                """, (ctx.author.id, work_minutes, work_minutes))
            await db.commit()

        # 長休憩か小休憩か判定
        is_long_break = (session_count % long_break_interval == 0)
        break_time = long_break if is_long_break else short_break
        break_type = "長休憩" if is_long_break else "小休憩"

        await msg.edit(
            content=(
                f"🎉 **{ctx.author.mention} のセッション {session_count} 完了！** "
                f"{work_minutes}分の作業が終わりました。\n"
                f"対象: {target_line}\n💤 {break_type} {break_time}分を開始します..."
            ),
            view=None
        )

        # 音声を再生（音量を2倍に増幅）
        if voice_client and voice_client.is_connected():
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
                f"{emoji} **{ctx.author.mention} の{break_type}！** ({break_time}分)\n"
                f"対象: {target_line}\nリラックスしましょう！",
                view=break_view
            )

            remaining_seconds = break_time * 60

            while remaining_seconds > 0:
                # 起動者・参加者が全員VCから退出したかチェック
                if not has_active_members(voice_client, ctx.author.id):
                    await break_msg.edit(content="⏹️ 全員が退出したため終了しました。", view=None)
                    timer_targets.pop(ctx.author.id, None)
                    active_timers.pop(ctx.author.id, None)
                    if voice_client: await voice_client.disconnect()
                    return

                if break_view.stopped:
                    await control_msg.edit(content="⏹️ ポモドーロを終了しました。お疲れ様でした！")
                    timer_targets.pop(ctx.author.id, None)
                    active_timers.pop(ctx.author.id, None)
                    if voice_client: await voice_client.disconnect()
                    return

                if break_view.paused:
                    await asyncio.sleep(1)
                    continue

                await asyncio.sleep(1)
                remaining_seconds -= 1

                if remaining_seconds % 60 == 0 and remaining_seconds != 0:
                    await break_msg.edit(
                        content=(
                            f"{emoji} **{ctx.author.mention} の残り {remaining_seconds // 60} 分** "
                            f"({break_type})\n対象: {target_line}\nリラックスしましょう！"
                        ),
                        view=break_view
                    )

            # 休憩終了
            await break_msg.edit(
                content=f"⏰ **{ctx.author.mention} の{break_type}終了！** 次のセッションを始めましょう。\n対象: {target_line}",
                view=None
            )

            # 音声を再生（音量を2倍に増幅）
            if voice_client and voice_client.is_connected():
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

    targets = timer_targets.setdefault(ctx.author.id, set())
    targets.add(user.id)
    await ctx.send(f"✅ {ctx.author.mention} のタイマー対象に {user.mention} を追加しました。")

@bot.command(name="list")
async def list_targets(ctx):
    """加算対象ユーザーの一覧を表示します（!list）"""
    targets = timer_targets.get(ctx.author.id, set())
    if not targets:
        await ctx.send(f"ℹ️ {ctx.author.mention} の追加対象はありません。")
        return

    mentions = " ".join([f"<@{user_id}>" for user_id in sorted(targets)])
    await ctx.send(f"📌 {ctx.author.mention} のタイマー対象: {mentions}")

@bot.command()
async def remove(ctx, user: discord.Member):
    """加算対象ユーザーを削除します（!remove @user）"""
    if user.bot:
        await ctx.send("⚠️ Botは加算対象に含まれていません。")
        return

    targets = timer_targets.get(ctx.author.id, set())
    if user.id not in targets:
        await ctx.send(f"ℹ️ {user.mention} は {ctx.author.mention} の対象に登録されていません。")
        return

    targets.remove(user.id)
    await ctx.send(f"✅ {ctx.author.mention} のタイマー対象から {user.mention} を削除しました。")
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()

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
    # コマンド実行者が起動者または参加者であるタイマーを探す
    timer_author_id = None
    for author_id, info in active_timers.items():
        targets = {author_id} | timer_targets.get(author_id, set())
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
    all_ids = {timer_author_id} | timer_targets.get(timer_author_id, set())
    participant_lines = []
    for uid in all_ids:
        minutes = session_work.get(uid, 0)
        label = "（起動者）" if uid == timer_author_id else ""
        participant_lines.append(f"<@{uid}>{label}: {minutes}分")

    embed.add_field(
        name=f"参加者 ({len(all_ids)}人)",
        value="\n".join(participant_lines),
        inline=False
    )

    await ctx.send(embed=embed)

@bot.command()
async def test(ctx):
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
              "※起動者が退出しても参加者がVCにいればタイマーは継続します。",
        inline=False
    )

    embed.add_field(
        name="!timer",
        value="現在のタイマー情報を表示します。\n"
              "タイマー設定、完了セッション数、参加者ごとの作業時間を確認できます。",
        inline=False
    )

    embed.add_field(
        name="!add @user",
        value="指定ユーザーをあなたのタイマー対象に追加します。\n"
              "※参加ボタンからも追加できます。",
        inline=False
    )

    embed.add_field(
        name="!remove @user",
        value="指定ユーザーを加算対象から削除します。",
        inline=False
    )

    embed.add_field(
        name="!list",
        value="現在の加算対象ユーザー一覧を表示します。",
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
        name="!test",
        value="ボイスチャンネルで音声再生テストを行います。",
        inline=False
    )

    embed.add_field(
        name="!help",
        value="このヘルプメッセージを表示します。",
        inline=False
    )

    embed.set_footer(text="タイマー中は起動者・参加者が一時停止⏸️・再開▶️・終了⏹️ボタンを使用できます。")

    await ctx.send(embed=embed)

# 環境変数からトークンを取得（セキュリティ向上）
token = os.getenv('DISCORD_BOT_TOKEN')
if not token:
    print("エラー: DISCORD_BOT_TOKEN 環境変数が設定されていません。")
    print("以下のコマンドで設定してください:")
    print("  export DISCORD_BOT_TOKEN='your_token_here'")
    exit(1)

bot.run(token)