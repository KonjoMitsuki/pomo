# DB 設計仕様書

## 概要
このドキュメントは、本プロジェクトが使用する SQLite データベースの設計と、プログラム内でどのように利用されているかをまとめたものです。

- DB ファイル: `assets/pomo.db`（既定）
- DB 接続: `aiosqlite` を使用し非同期でアクセス
- 初期化: `StatsRepository.init()` がスキーマを作成

参照実装: [src/storage.py](src/storage.py)

## テーブル一覧（スキーマ）

### `users`
- 目的: ユーザー情報（Discord ユーザー）を一意に保存
- カラム:
  - `user_id` INTEGER PRIMARY KEY — Discord のユーザーID
  - `display_name` TEXT — 表示名（アップサートで更新される）
  - `created_at` TEXT NOT NULL DEFAULT (datetime('now')) — 登録日時

### `timers`
- 目的: 名前付きタイマー設定を保存（ユーザー毎／サーバー毎に管理可能）
- カラム:
  - `id` INTEGER PRIMARY KEY AUTOINCREMENT
  - `owner_id` INTEGER NOT NULL REFERENCES users(user_id)
  - `guild_id` INTEGER — サーバーID（NULL の場合はユーザー単位のグローバル設定）
  - `name` TEXT NOT NULL — タイマー名
  - `work_min` INTEGER NOT NULL DEFAULT 25
  - `short_brk` INTEGER NOT NULL DEFAULT 5
  - `long_brk` INTEGER NOT NULL DEFAULT 15
  - `interval` INTEGER NOT NULL DEFAULT 4
  - `is_shared` INTEGER NOT NULL DEFAULT 0
  - `created_at` TEXT NOT NULL DEFAULT (datetime('now'))
  - UNIQUE(owner_id, guild_id, name)

### `sessions`
- 目的: ポモドーロの実行セッション（開始・終了・完了回数）を記録
- カラム:
  - `id` INTEGER PRIMARY KEY AUTOINCREMENT
  - `timer_id` INTEGER NOT NULL REFERENCES timers(id)
  - `guild_id` INTEGER — 実行時のサーバーID（参照用）
  - `started_at` TEXT NOT NULL DEFAULT (datetime('now'))
  - `ended_at` TEXT — 終了時刻（NULL = 実行中）
  - `completed_count` INTEGER NOT NULL DEFAULT 0 — セッション内での完了セッション数

### `session_members`
- 目的: セッション単位での参加者ごとの作業時間・完了セッション数を保存
- カラム:
  - `session_id` INTEGER NOT NULL REFERENCES sessions(id)
  - `user_id` INTEGER NOT NULL REFERENCES users(user_id)
  - `work_minutes` INTEGER NOT NULL DEFAULT 0
  - `completed_sessions` INTEGER NOT NULL DEFAULT 0
  - PRIMARY KEY(session_id, user_id)

## 外部キー・制約・挙動
- 外部キー制約は `PRAGMA foreign_keys = ON` を有効にしている（`StatsRepository` の各接続で実行）
- `timers` の `(owner_id, guild_id, name)` がユニーク制約になっており、同一オーナー／同一サーバー（NULL を含む）の同名タイマー重複を防止
- `guild_id` は NULL を許容するため「ユーザー単位設定」と「サーバー単位設定」を共存させる設計

## 初期化とマイグレーションに関する注意
- 初期化は `StatsRepository.init()`（[src/storage.py](src/storage.py)）で行われる。
- 実装上、`init()` 内で `DROP TABLE IF EXISTS stats` を実行している箇所が残っている（現行スキーマでは `stats` テーブルは存在しない）。将来的なマイグレーションを行う場合は注意。

## プログラム内での利用（呼び出し箇所と振る舞い）

以下は主要な機能と、それに対応する DB 操作の一覧です。ファイル参照は実装ファイルへのリンクを示します。

- ユーザー情報の登録/更新
  - メソッド: `StatsRepository.upsert_user(user_id, display_name)`
  - 呼び出し元: [src/cog.py](src/cog.py)（`!pomo`, `!pconfig` など実行時）
  - 目的: `users` に存在しなければ挿入、存在すれば `display_name` を更新

- タイマー設定の作成 / 取得 / 列挙 / 更新 / 削除
  - メソッド: `create_timer`, `get_timer_by_name`, `list_timers`, `upsert_timer`, `delete_timer`
  - 呼び出し元: [src/cog.py](src/cog.py)（`!pomo`, `!plist`, `!pconfig`, `!pdel`）
  - 挙動: ユーザー+サーバー（NULL 許容）スコープでタイマー設定を保持。`delete_timer` は該当タイマーに紐づく `sessions` がある場合は削除不可（履歴保護）

- セッションの開始 / 終了
  - メソッド: `start_session(timer_id, guild_id)` → 新規 `sessions` 行を挿入して `session_id` を返す
  - メソッド: `end_session(session_id, completed_count)` → `sessions.ended_at` と `completed_count` を更新
  - 呼び出し元: [src/runner.py](src/runner.py)（タイマー実行中の管理）

- 作業時間・完了セッション数の集計更新
  - メソッド: `add_work_minutes(session_id, user_ids, minutes)`
    - `session_members` に対して `INSERT ... ON CONFLICT ... DO UPDATE` を用い、既存行なら `work_minutes` を加算
  - メソッド: `add_completed_session(session_id, user_ids)`
    - `completed_sessions` を加算する同様の ON CONFLICT パターン
  - 呼び出し元: [src/runner.py](src/runner.py)（1分ごとの加算、セッション完了時の加算）

- 統計取得（表示用）
  - メソッド: `get_stats_per_timer(user_id)` — 各タイマーごとの合計作業時間を取得（`JOIN sessions, timers, session_members`）
  - メソッド: `get_stats(user_id)` — 全体の合計作業時間・完了セッション数を返す
  - メソッド: `get_stats_by_timer(user_id, timer_id)` — 指定タイマーの集計を返す
  - 呼び出し元: [src/cog.py](src/cog.py)（`!stats`, `!reset` コマンドの表示ロジック）

## 実装上の挙動・注意点
- `aiosqlite` を都度 `async with aiosqlite.connect(self.db_file)` で開く実装になっている。軽量な利用では問題になりにくいが、短時間に大量の接続が発生する運用では接続コストが影響する可能性あり。
- 各接続で `PRAGMA foreign_keys = ON` を必ず実行しているため、外部キー制約は有効化された状態で動作する。
- クエリ内で `row_factory = aiosqlite.Row` を使って dict ライクに扱い、結果を `dict(row)` に変換して返す実装パターンが多用されている（コード参照: [src/storage.py](src/storage.py)）。
- `guild_id` の比較は SQL 内で `(guild_id = ? OR (guild_id IS NULL AND ? IS NULL))` のように NULL を意識した比較を行っている点に注意（サーバー設定とグローバル設定の区別を実現）

## 拡張提案 / 保守ポイント
- マイグレーション: 現状 `init()` はスキーマ作成向けだが、本格運用では破壊的な `DROP` を避け、バージョン管理＋マイグレーションを導入する（例: `alembic` 相当の方針または手作りのバージョンテーブル）
- インデックス: 現状は主キー／ユニークのみ。`session_members.user_id` や `sessions.timer_id` にインデックスを追加すると集計クエリが高速化される
- 同時実行: 高負荷時は接続プールや単一の永続接続を検討（`aiosqlite` は内部で SQLite を使用するため、同時書き込みのロック競合に注意）
- テスト: 主要クエリ（加算ロジック、ON CONFLICT の動作、NULL を含む timer 検索）をユニットテストでカバーすることを推奨

## 参照実装ファイル
- ストレージ実装: [src/storage.py](src/storage.py)
- コマンドハンドラ（タイマー／統計系）: [src/cog.py](src/cog.py)
- ランナー（セッション実行、作業時間加算）: [src/runner.py](src/runner.py)
- 起動スクリプト（DB 初期化呼び出し）: [src/timer.py](src/timer.py)

----
作成日: 2026-05-20
