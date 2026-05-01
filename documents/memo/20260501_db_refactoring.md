# 2026年5月1日 データベース設計のリファクタリング（大幅改修）まとめ

将来の自分に向けて、今回何を行ったのか、なぜ行ったのかを記録したレポートです。

---

## 1. 今回やったこと（概要）

Pomodoro Botが記録を保存している **データベース（DB: SQLite）の設計を根本的に作り直しました**。

これまでは「ユーザーAが合計◯分作業した」というだけの情報を保存していましたが、これを拡張し、「誰が・どんなタイマー設定で・いつ・何回作業したか」という**詳細な履歴まで辿れる仕組み**に変更しました。

また、それに伴って関連するPythonファイル（`src/storage.py`, `src/runner.py`, `src/cog.py`）の処理の書き換えを行いました。

---

## 2. なぜDBの設計を変えたのか？（初心者の方向け解説）

これまでのデータベースは1つのテーブル（表）だけで構成されていました。例えるなら、「名前と合計ポイントだけを書いた紙」のようなもので、とてもシンプルで扱いやすい反面、以下のような限界がありました。

- 「いつ作業したのか？」がわからない
- 「どのタイマー設定（例: 作業25分・休憩5分）でやった記録なのか？」がわからない
- 他のサーバーからの記録と混ざってしまう

そこで、今回の改修では **リレーショナルデータベース** の考え方を取り入れました。「ユーザー一覧」「タイマー設定一覧」「実際のセッション履歴」「参加者の貢献度」という**4つの表に分けてデータを管理し、それぞれをIDで結びつけること**で、将来的に「週ごとの作業時間グラフを表示する」や「特定のタイマー設定ごとのランキングを作る」といった高度な機能追加ができるようになります。

---

## 3. データベースの変更内容（Before / After）

### 変更前（1つの表のみ）

**📊 `stats` テーブル**
ただ「合計値」だけを保存していました。

| カラム名（項目） | データ型 | 説明 |
| :--- | :--- | :--- |
| `user_id` | 数値 | DiscordのユーザーID（主キー） |
| `total_minutes`| 数値 | 合計作業時間（分） |
| `sessions` | 数値 | 完了した合計セッション数 |

<br>

### 変更後（4つの表の組み合わせ）

**👤 `users` テーブル**
botを利用したことのあるユーザーの一覧。

| カラム名 | データ型 | 説明 |
| :--- | :--- | :--- |
| `user_id` | 数値 | DiscordのユーザーID（主キー） |
| `display_name` | 文字列 | ユーザーの表示名 |
| `created_at` | 文字列 | 登録日時 |

**⏱️ `timers` テーブル**
タイマーの設定テンプレート。誰が作ったどんな設定（25分作業・5分休憩など）かを保存します。

| カラム名 | データ型 | 説明 |
| :--- | :--- | :--- |
| `id` | 数値 | タイマー自体の固有ID（自動連番）|
| `owner_id` | 数値 | 作成者のユーザーID（`users`と結びつく） |
| `name` | 文字列 | タイマーの識別名（今回は "default" で作成） |
| `work_min` ~ `interval`| 数値 | 作業や休憩の時間設定 |

**📁 `sessions` テーブル**
「いつ、どのタイマーでポモドーロを開始したか」の1回1回の活動記録（セッション）。

| カラム名 | データ型 | 説明 |
| :--- | :--- | :--- |
| `id` | 数値 | セッション固有ID（自動連番） |
| `timer_id` | 数値 | 使用したタイマーのID（`timers`と結びつく） |
| `started_at` | 文字列 | 開始日時 |
| `ended_at` | 文字列 | 終了日時 |
| `completed_count`| 数値 | この期間内にポモドーロが何セット完了したか |

**👥 `session_members` テーブル**
「1つのセッションに、誰が参加して、何分貢献したか」の参加者ごとの記録。これが旧 `stats` をより詳細にしたものです。

| カラム名 | データ型 | 説明 |
| :--- | :--- | :--- |
| `session_id` | 数値 | セッションID（`sessions`と結びつく） |
| `user_id` | 数値 | ユーザーID（`users`と結びつく） |
| `work_minutes` | 数値 | このセッション中に作業した時間（分） |
| `completed_sessions`| 数値 | このセッション中に完了した回数 |

---

## 4. プログラム上の変更の工夫点

1. **外部キー制約の有効化 (`PRAGMA foreign_keys = ON`)**
   SQLiteはデフォルトで「テーブル同士の結びつき」のルールが緩いため、接続するたびにこのルールを厳格化するおまじないを追加しました。
2. **Upsert（あれば更新、なければ作成）の活用**
   Python側で「データがあるかな？」と探してから「作る」「更新する」を分けるのではなく、データベース側の機能 (`ON CONFLICT DO UPDATE`) を使って一撃で処理させることで不具合を減らしました。
3. **`runner.py` で処理とDBを連動**
   ポモドーロタイマーを開始した瞬間に `sessions` からIDを発行し、タイマーが止まった時に終了時刻を記録するようにロジックを繋ぎ合わせました。

---

## 5. 今回のAIのプロンプトと計画（裏側）

### ユーザー（私）のプロンプト

```markdown
## タスク概要

`src/storage.py` のDB設計をリファクタリングしてください。
既存の `stats` テーブルを廃止し、4テーブル構成に移行します。
`src/` 以外のファイルは変更しないでください。

---

## 現在のスキーマ

```sql
CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER PRIMARY KEY,
    total_minutes INTEGER DEFAULT 0,
    sessions INTEGER DEFAULT 0
)
```

---

## 移行後のスキーマ

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    display_name TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS timers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL REFERENCES users(user_id),
    guild_id    INTEGER,
    name        TEXT    NOT NULL,
    work_min    INTEGER NOT NULL DEFAULT 25,
    short_brk   INTEGER NOT NULL DEFAULT 5,
    long_brk    INTEGER NOT NULL DEFAULT 15,
    interval    INTEGER NOT NULL DEFAULT 4,
    is_shared   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timer_id        INTEGER NOT NULL REFERENCES timers(id),
    guild_id        INTEGER,
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    ended_at        TEXT,
    completed_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_members (
    session_id          INTEGER NOT NULL REFERENCES sessions(id),
    user_id             INTEGER NOT NULL REFERENCES users(user_id),
    work_minutes        INTEGER NOT NULL DEFAULT 0,
    completed_sessions  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, user_id)
);
```

---

## `StatsRepository` クラスに実装するメソッド

クラス名は `StatsRepository` のまま維持してください。

### 初期化

- `async def init() -> None`
  - 4テーブルをすべて CREATE TABLE IF NOT EXISTS で作成する
  - 既存の `stats` テーブルが残っていれば DROP する

### users 操作

- `async def upsert_user(user_id: int, display_name: str) -> None`
  - 存在しなければ INSERT、あれば display_name を UPDATE する

### timers 操作

- `async def create_timer(owner_id: int, name: str, guild_id: int | None, work_min: int, short_brk: int, long_brk: int, interval: int) -> int`
  - timers に INSERT してその id を返す

- `async def get_timer_by_name(owner_id: int, name: str) -> dict | None`
  - owner_id と name で検索して1行返す。なければ None

- `async def list_timers(owner_id: int) -> list[dict]`
  - owner_id のタイマー一覧を返す

### sessions 操作

- `async def start_session(timer_id: int, guild_id: int | None) -> int`
  - sessions に INSERT して session_id を返す

- `async def end_session(session_id: int, completed_count: int) -> None`
  - ended_at を現在時刻で UPDATE、completed_count も UPDATE する

### session_members 操作

- `async def add_work_minutes(session_id: int, user_ids: list[int], minutes: int) -> None`
  - session_members に対して user_ids 全員の work_minutes を加算する
  - 行がなければ INSERT、あれば UPDATE する

- `async def add_completed_session(session_id: int, user_ids: list[int]) -> None`
  - session_members の completed_sessions を +1 する

### 集計

- `async def get_stats(user_id: int) -> dict | None`
  - session_members を集計して以下を返す
  - `{ "total_minutes": int, "total_sessions": int }`
  - 記録がなければ None

- `async def get_stats_by_timer(user_id: int, timer_id: int) -> dict | None`
  - 特定タイマーに絞った同様の集計を返す

- `async def reset_stats(user_id: int) -> dict | None`
  - session_members から user_id の行を全削除する
  - 削除前の集計値を返す。記録がなければ None

---

## `src/runner.py` の変更点

`PomoRunner` は現在 `StatsRepository` の以下を呼んでいます：

- `stats.add_work_minutes(user_ids, minutes)` → 新シグネチャ `add_work_minutes(session_id, user_ids, minutes)` に合わせて修正
- `stats.add_completed_session(user_ids)` → 新シグネチャ `add_completed_session(session_id, user_ids)` に合わせて修正

`session_id` は `PomoRunner.run()` の開始時に `stats.start_session()` を呼んで取得し、
`self.session_id` としてインスタンス変数に保持してください。

`run()` 終了時に `stats.end_session(self.session_id, self.session.session_count)` を呼んでください。

---

## `src/cog.py` の変更点

`!pomo` コマンドの開始処理で `stats.upsert_user(user_id, display_name)` を呼んでください。

`!stats` コマンドは `stats.get_stats(user_id)` の戻り値が dict に変わるため、
`row["total_minutes"]` / `row["total_sessions"]` でアクセスするよう修正してください。

`!reset` コマンドも同様に dict アクセスに修正してください。

---

## 変更してはいけないもの

- `src/session.py` は変更しない
- `src/views.py` は変更しない
- `src/audio.py` は変更しない
- `src/timer.py` は変更しない
- `documents/` 以下は変更しない
- `README.md` は変更しない

---

## 補足・実装の制約

- DBエンジンは SQLite、ライブラリは `aiosqlite` を使用してください
- すべての DB 操作は async/await で実装してください
- 型ヒントを必ず付けてください

### データベース操作の要件

1. **外部キー制約**: SQLiteの外部キー制約はコネクションを確立するたびに無効化されるため、`aiosqlite.connect()` で接続するすべての箇所で、最初の操作として必ず `await db.execute("PRAGMA foreign_keys = ON")` を実行してください。

2. **トランザクション**: 複数テーブルへの書き込みを伴うメソッド（`start_session`、`end_session` など）は、途中でエラーが発生した場合にデータの不整合が起きないよう、1つの `async with aiosqlite.connect(...) as db:` ブロック内で処理をまとめ、最後に `await db.commit()` を呼んでください。エラー時は自動的にロールバックされます。

3. **Upsert処理**: `upsert_user` や `add_work_minutes` などの「なければINSERT、あればUPDATE」の処理は、Python側で SELECT して分岐するのではなく、SQLiteの `INSERT INTO ... ON CONFLICT (...) DO UPDATE SET ...` 構文を使って1クエリで実装してください。

4. **N+1問題の回避**: `get_stats` などの集計メソッドは、ループ内で都度クエリを発行するのではなく、`JOIN` と `SUM` / `GROUP BY` を使って1クエリで集計してください。
```

### AIが作成した実装計画（Plan）

```markdown
## Plan: Refactor DB Schema in storage.py

This plan refactors the `StatsRepository` in `src/storage.py` and updates dependent files to transition from a single `stats` table to a 4-table relational schema (`users`, `timers`, `sessions`, `session_members`) as required.

**Steps**
1. **Refactor `src/storage.py`**
   - Import `sqlite3` (if needed for `Row` or exceptions) but use `aiosqlite`.
   - Update `init()` to create `users`, `timers`, `sessions`, `session_members` and drop `stats`.
   - Implement helper `_get_db()` or similar to enforce `PRAGMA foreign_keys = ON` on every connection.
   - Implement `upsert_user`, `create_timer`, `get_timer_by_name`, `list_timers`, `start_session`, `end_session`, `add_work_minutes`, `add_completed_session`, `get_stats`, `get_stats_by_timer`, `reset_stats`.
2. **Update `src/cog.py`**
   - In `pomo` command: Call `await self.stats.upsert_user(ctx.author.id, ctx.author.display_name)` before starting the runner.
   - In `stats` command: Update to use dict access `row["total_minutes"]` and `row["total_sessions"]`.
   - In `reset` command: Update to use dict access.
3. **Update `src/runner.py`**
   - In `PomoRunner.run()`: Since it needs `timer_id` to start a session, look up a "default" timer via `get_timer_by_name`. If it doesn't exist, create it via `create_timer`.
   - Start the session with `session_id = await self.stats.start_session(timer_id, guild_id)`.
   - Pass `session_id` to `add_work_minutes` and `add_completed_session`.
   - On completion (exiting while loop), call `await self.stats.end_session(session_id, self.session.session_count)`.
```