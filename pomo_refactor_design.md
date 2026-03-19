# Discord ポモドーロBot OOPリファクタリング設計書

## タスク概要

添付の `timer.py` を、以下の設計書に従ってオブジェクト指向にリファクタリングしてください。
既存の機能・コマンド・UI動作はすべて保持すること。外部から見た動作は変えない。

---

## 現状の問題点

- `active_timers` と `timer_targets` という2つのグローバル辞書が常にセットで操作される必要があるが、別々に存在しているためデータ不整合のリスクがある
- `!pomo` コマンドが400行超の巨大関数で、作業ループ・休憩ループ・音声再生・DB保存がすべて同居している
- 作業ループと休憩ループがほぼ同じwhileループを2重に書いている
- `find_by_user`（`!timer`や`!mute`でユーザーが属するセッションを探す処理）が毎回 `active_timers.items()` を全件ループしている
- `on_voice_state_update` が全タイマーを全件走査している
- FFmpegの呼び出しコードが作業終了・休憩終了の両方に重複している

---

## クラス設計

### 1. `PomoSession` (dataclass)

**責務:** 1つのタイマーセッションに関わる状態をすべて保持する。現在の `active_timers[author_id]` と `timer_targets[author_id]` を統合する。

```python
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
    session_work: dict[int, int] = field(default_factory=dict)  # user_id -> 分数
    muted: bool = False
    pomo_view: "PomoView | None" = field(default=None, repr=False)
    pomo_msg: "discord.Message | None" = field(default=None, repr=False)
    control_msg: "discord.Message | None" = field(default=None, repr=False)
    join_view: "JoinView | None" = field(default=None, repr=False)
```

**メソッド:**

```python
def get_all_member_ids(self) -> set[int]:
    """ホスト + 参加者の全IDセットを返す"""

def get_vc_active_ids(self, voice_client) -> list[int]:
    """現在VCにいる対象メンバーIDリストを返す（作業時間加算に使用）"""

def has_active_members(self, voice_client) -> bool:
    """ホストまたは参加者がVCに残っているか"""

def transfer_host(self) -> int | None:
    """join_orderを使ってホストをtargetsの先頭ユーザーに移行する。
    成功したら新ホストIDを返す。候補がなければNoneを返す。"""

def add_member(self, user_id: int) -> bool:
    """targetsにユーザーを追加する。既に存在する場合はFalseを返す。"""

def remove_member(self, user_id: int) -> bool:
    """targetsからユーザーを削除する。存在しない場合はFalseを返す。"""

def get_target_line(self) -> str:
    """ホストと参加者のメンション文字列を構築して返す（例: '<@123> <@456>'）"""
```

---

### 2. `SessionManager`

**責務:** 複数の `PomoSession` の生成・取得・削除と、ユーザーIDからセッションを逆引きする機能を持つ。現在の `active_timers` と `timer_targets` グローバル辞書を置き換える。

```python
class SessionManager:
    def __init__(self):
        self._sessions: dict[int, PomoSession] = {}  # author_id -> PomoSession
        self._user_index: dict[int, int] = {}         # user_id -> author_id（逆引き）

    def create(self, author_id: int, **kwargs) -> PomoSession:
        """新しいPomoSessionを作成して登録する。author_idをjoin_orderの先頭に追加する。"""

    def get(self, author_id: int) -> PomoSession | None:
        """author_idでセッションを取得する"""

    def remove(self, author_id: int) -> None:
        """セッションを削除し、逆引きインデックスもクリアする"""

    def find_by_user(self, user_id: int) -> tuple[int, PomoSession] | None:
        """user_idが属するセッションを返す（ホスト・参加者どちらも対象）。
        戻り値は (author_id, session) のタプル。見つからなければNone。
        逆引きインデックスを使って O(1) で検索する。"""

    def update_index(self, author_id: int) -> None:
        """セッションの全メンバーIDを逆引きインデックスに登録する。
        参加者追加・削除・ホスト移行のたびに呼ぶ。"""
```

---

### 3. `PomoRunner`

**責務:** ポモドーロのメインループを担う。現在の `!pomo` コマンド内の巨大ループを切り出す。

```python
class PomoRunner:
    def __init__(
        self,
        session: PomoSession,
        voice_client: discord.VoiceClient,
        ctx: commands.Context,
        stats: "StatsRepository",
        audio: "AudioPlayer",
    ):
        self.session = session
        self.vc = voice_client
        self.ctx = ctx
        self.stats = stats
        self.audio = audio

    async def run(self) -> None:
        """ポモドーロのメインループ。セッションが終了するまで繰り返す。"""

    async def run_phase(self, duration_min: int, label: str, emoji: str) -> bool:
        """作業・休憩どちらも同じメソッドで処理する。
        - duration_min: フェーズの長さ（分）
        - label: 表示名（例: 'セッション 1'、'小休憩'）
        - emoji: 表示する絵文字（例: '🍅'、'💤'）
        - 戻り値: Trueなら正常終了、Falseなら停止・全員退出で中断
        1分ごとにstats.add_work_minutes()を呼ぶのは作業フェーズのみ。"""

    async def _wait_tick(self, view: "PomoView") -> str:
        """1秒待機し、状態を返す。
        戻り値: 'paused' | 'stopped' | 'no_members' | 'tick'"""

    async def _refresh_panels(self, label: str) -> None:
        """古い参加パネルを無効化し、最新位置に再投稿する"""
```

---

### 4. `StatsRepository`

**責務:** SQLiteへのアクセスをすべて集約する。

```python
class StatsRepository:
    def __init__(self, db_file: str = "pomo.db"):
        self.db_file = db_file

    async def init(self) -> None:
        """テーブルを初期化する（初回起動時に呼ぶ）"""

    async def add_work_minutes(self, user_ids: list[int], minutes: int) -> None:
        """指定ユーザー全員のtotal_minutesをminus加算する"""

    async def add_completed_session(self, user_ids: list[int]) -> None:
        """指定ユーザー全員のsessionsを1加算する"""

    async def get_stats(self, user_id: int) -> tuple[int, int] | None:
        """(total_minutes, sessions) を返す。レコードがなければNone。"""

    async def reset_stats(self, user_id: int) -> tuple[int, int] | None:
        """レコードを削除し、削除前の (total_minutes, sessions) を返す。"""
```

---

### 5. `AudioPlayer`

**責務:** FFmpegを使った音声再生を集約する。現在2か所に重複しているコードを1か所にまとめる。

```python
class AudioPlayer:
    def __init__(self, sound_file: str = "ding.mp3"):
        self.sound_file = sound_file

    async def play(self, voice_client: discord.VoiceClient, volume: float = 1.0) -> None:
        """sound_fileを再生し、完了まで待つ（最大5秒）。
        ファイルが存在しない場合は何もしない（呼び出し元に警告させる）。
        既に再生中の場合は先に停止する。"""

    def file_exists(self) -> bool:
        """sound_fileが存在するか確認する"""
```

---

### 6. `PomoView` / `JoinView`（既存クラスの修正）

**責務:** UIボタンのみ担当する。ロジックは `PomoSession` に委譲する。

修正方針:
- コンストラクタで `session: PomoSession` を受け取るように変更する
- ボタンコールバック内で `active_timers` や `timer_targets` グローバル変数を直接参照しているコードを、`self.session` のメソッド呼び出しに置き換える
- `interaction_check` は `session.get_all_member_ids()` を使う

---

### 7. `PomoCog` (commands.Cog)

**責務:** すべての `!` コマンドをまとめるCogクラス。グローバル変数の代わりに依存オブジェクトをコンストラクタで受け取る。

```python
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

    @commands.command()
    async def pomo(self, ctx, ...): ...

    @commands.command()
    async def timer(self, ctx): ...

    @commands.command()
    async def add(self, ctx, user): ...

    @commands.command()
    async def remove(self, ctx, user): ...

    @commands.command(name="list")
    async def list_targets(self, ctx): ...

    @commands.command()
    async def stats_cmd(self, ctx): ...

    @commands.command()
    async def reset(self, ctx): ...

    @commands.command()
    async def mute(self, ctx): ...

    @commands.command()
    async def test(self, ctx): ...

    @commands.command(name="help")
    async def help_command(self, ctx): ...

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after): ...
```

---

## エントリーポイント（`main` ブロック）

```python
async def main():
    stats = StatsRepository(DB_FILE)
    await stats.init()

    manager = SessionManager()
    audio = AudioPlayer(SOUND_FILE)

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

    await bot.add_cog(PomoCog(bot, manager, stats, audio))
    await bot.start(token)

asyncio.run(main())
```

---

## 実装時の注意事項

### 保持すること（変えてはいけない）

- すべての `!` コマンドの動作・引数・デフォルト値
- ボタンのラベル・絵文字・スタイル（⏸️一時停止、▶️再開、⏹️終了、🙋参加、👋退出）
- 長休憩の判定ロジック: `session_count % long_break_interval == 0`
- 作業時間は1分ごとに加算（`remaining_seconds % 60 == 0`）
- セッション完了時のみ `sessions` を加算（作業時間加算とは別）
- ホスト移行ロジック: `join_order` の中でtargetsに残っている最も早い参加者
- VCから全員退出したときの自動終了
- `!timer` コマンドで古いパネルを最新位置に再投稿する動作
- デバッグ用 `print` 文（`[DEBUG]` プレフィックスのもの）

### 削除すること

- グローバル変数 `active_timers`、`timer_targets`
- モジュールレベルの関数 `_transfer_host()`、`get_target_line()`、`has_active_members()`、`get_active_member_ids()`、`add_work_minutes()`、`add_completed_session()`、`init_db()`
  - これらはすべて対応するクラスのメソッドに移行する

### `on_voice_state_update` の修正

現状の全件ループを `manager.find_by_user(member.id)` を使った O(1) 検索に変更する:

```python
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

    if member.id == session.host_id:
        new_host = session.transfer_host()
        self.manager.update_index(author_id)
    else:
        session.remove_member(member.id)
        self.manager.update_index(author_id)
```

### `PomoRunner.run_phase()` で作業・休憩ループを統合する

現在2重に書かれているwhileループを `run_phase()` 1つに統合する。
呼び出し側のイメージ:

```python
# run() 内
while self.session.has_active_members(self.vc):
    session_count += 1
    ok = await self.run_phase(session.work_min, f"セッション {session_count}", "🍅")
    if not ok:
        return
    await self.stats.add_completed_session(active_ids)

    is_long = (session_count % session.interval == 0)
    brk_min = session.long_brk if is_long else session.short_brk
    emoji = "☕" if is_long else "💤"
    label = "長休憩" if is_long else "小休憩"

    await self.audio.play(self.vc)
    ok = await self.run_phase(brk_min, label, emoji)
    if not ok:
        return
    await self.audio.play(self.vc)
```

---

## ファイル構成（推奨）

単一ファイルのままでも構わないが、300行を超えるようであれば以下に分割すること。

```
timer.py          ← エントリーポイントのみ
pomo/
  __init__.py
  session.py      ← PomoSession, SessionManager
  runner.py       ← PomoRunner
  stats.py        ← StatsRepository
  audio.py        ← AudioPlayer
  views.py        ← PomoView, JoinView
  cog.py          ← PomoCog
```

---

## 完了条件

- [ ] グローバル変数 `active_timers`、`timer_targets` が存在しない
- [ ] `!pomo`、`!timer`、`!add`、`!remove`、`!list`、`!stats`、`!reset`、`!mute`、`!test`、`!help` がすべて動作する
- [ ] 同時に複数ユーザーが `!pomo` を実行できる（セッションが独立している）
- [ ] VC退出時のホスト自動移行が動作する
- [ ] 作業フェーズと休憩フェーズが `run_phase()` で共通化されている
- [ ] `python -m py_compile timer.py`（または分割した場合は各ファイル）がエラーなく通る
