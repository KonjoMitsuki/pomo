# 名前付きタイマー機能 実装報告

作成日: 2026-05-20

概要
---
本作業では、`!pomo` の名前付きタイマー対応と、タイマー設定のDB管理機能を実装しました。サーバー単位（`guild_id` を含む）でタイマーを管理できるようにし、タイマーの作成・更新・削除・一覧表示・統計取得の機能を追加しています。

変更の目的
---
- ユーザーが `!p [timer_name]` で名前付きタイマーを起動できるようにする。
- 名前に紐づくタイマー設定（作業時間・休憩等）を DB に保管し、再利用・共有できるようにする。
- タイマー管理コマンド（`!pconfig` / `!pdel` / `!plist`）と統計表示（`!stats` の拡張）を追加する。

変更ファイル一覧（主な差分）
---
- `src/storage.py`
  - `timers` テーブル定義に `UNIQUE(owner_id, guild_id, name)` を追加（`init()` の CREATE TABLE に記載）。
  - `create_timer(...)` のパラメータ順序を微修正（`owner_id, name, guild_id, ...`）して呼び出し整合性を改善。
  - `get_timer_by_name(owner_id, name, guild_id)` を追加（サーバー単位で検索。`guild_id` が NULL の行との比較も考慮）。
  - `list_timers(owner_id, guild_id=None)` を追加（guild_id 指定でそのサーバの設定を返す）。
  - 新規実装: `upsert_timer(owner_id, name, guild_id, work_min, short_brk, long_brk, interval) -> int`（INSERT ... ON CONFLICT(owner_id,guild_id,name) DO UPDATE）
  - 新規実装: `delete_timer(owner_id, name, guild_id) -> bool`（参照されているセッションがあれば削除不可）
  - 新規実装: `get_stats_per_timer(user_id) -> list[dict]`（タイマーごとの累計作業分を返す）

- `src/session.py`
  - `PomoSession` に `timer_name: str = "original"` フィールドを追加（`timer` コマンド等で表示するため）。

- `src/runner.py`
  - `PomoRunner.__init__` に `timer_id: int` 引数を追加し `self.timer_id` を保持。
  - `run()` の先頭で行っていた "default タイマーの DB 検索・作成" ブロックを削除し、`self.timer_id` を直接使用して `start_session()` を呼ぶよう変更。

- `src/cog.py`
  - `pomo` コマンドのシグネチャを `pomo(self, ctx, timer_name: str = "original")` に変更。
  - `pomo` 実行時に `await self.stats.upsert_user(...)` の直後で `get_timer_by_name(ctx.author.id, timer_name, guild_id)` を呼び、存在しなければ `create_timer(...)` で自動作成。取得した `timer` の値で `session.work_min` 等を上書きし、`session.timer_name = timer_name` を設定。`PomoRunner` へは `timer_id` を渡す。
  - `timer` コマンドの Embed に「タイマー名」フィールドを追加して `session.timer_name` を表示。
  - `list_targets` (`!list`) を `plist` (`!plist`, `!pl`) に置き換え。`plist` は `list_timers(ctx.author.id, guild_id)` を使ってタイマー一覧を Embed 表示。
  - 新規コマンド `pconfig` を追加（`!pconfig <name> [work] [short] [long] [interval]`。`upsert_timer` を呼ぶ）。
  - 新規コマンド `pdel` を追加（`!pdel <name>`。`delete_timer` を呼ぶ。記録がある場合は削除不可）。
  - `stats` コマンドを拡張：引数なし → `get_stats_per_timer` で全タイマー累計一覧表示、引数あり → そのタイマーの `get_stats_by_timer` を表示。
  - `help_command` を更新して新コマンド説明を追加・既存説明をタイマー名仕様に合わせて修正。

実装上のポイントと注意点
---
1. サーバー単位の取り扱い
   - タイマーは `owner_id` + `guild_id` + `name` の組み合わせで一意に管理します。`guild_id` は NULL を許容するため、グローバル（どのサーバにも属さない）設定も可能です。

2. DB スキーマ変更の影響
   - `init()` 内の `CREATE TABLE IF NOT EXISTS timers (...)` に `UNIQUE(owner_id, guild_id, name)` を追加しましたが、`CREATE TABLE IF NOT EXISTS` は既存テーブルに変更を適用しません。したがって既存データベースに重複レコードが存在する場合は、手動マイグレーション（重複解消）またはテーブル再作成が必要です。
   - 実運用DBがある場合は、以下いずれかを検討してください:
     - マイグレーションスクリプト: 重複を調査し統合/削除するSQLを実行する。
     - メンテナンス時間にテーブル再作成（バックアップ → DROP → CREATE → データ復元）。

3. 削除ポリシー
   - `delete_timer` はそのタイマーを参照する `sessions` レコードが1件でも存在すれば削除を拒否します（安全のため、未参照タイマーのみ削除可能）。

4. 一貫性
   - すべての DB 操作の先頭で `PRAGMA foreign_keys = ON` を実行し、`aiosqlite` を非同期で使用します。既存実装の慣習に合わせています。

実施した簡易検査
---
- Python ファイルの構文チェック: `python -m compileall -q .` を実行し、ワークスペース内の Python ファイルは全てコンパイルに成功しました（エラーなし）。
- 重要なモジュール読み込みに関しては、変更後にインポートエラーが出るかをローカルで確認することを推奨します（Bot の実行は Discord API トークンが必要なためここでは未実行）。

今後の手順（推奨）
---
1. マイグレーション作業（既存DBがある場合）
   - 既存 DB に重複 `timers` がないかを確認するSQLを実行してください。重複が無ければそのまま運用可能です。

2. ランタイム検証
   - 開発環境で Bot を起動し、次の操作を確認してください:
     - `!p`（`original` タイマーの自動作成と起動）
     - `!p work`（`work` タイマーの自動作成と起動）
     - `!pconfig work 50 10 20 4`（設定の作成・上書き）
     - `!p work` を実行して作業時間が 50 分になっていることを確認
     - `!plist`（タイマー一覧）
     - `!pdel work`（記録のないタイマーは削除可能、記録がある場合は削除拒否）
     - `!stats` / `!stats work`（統計表示）
     - `!timer`（稼働中セッションの Embed にタイマー名が表示される）

   実行例（簡易チェック）:
   ```bash
   python -m compileall -q . && python -c "import sys; sys.path.insert(0,'src'); import storage, runner, session, cog; print('IMPORT_OK')"
   ```

3. 追加テスト
   - `session` 経過に合わせた end-to-end テスト（手動で VC に接続し動作確認）を行ってください。

差分の要約（短く）
---
- 名前付きタイマーの DB 管理とコマンド群を追加しました。
- サーバー単位（`guild_id` を含む）でタイマーを一意管理します。
- 既存の `!add` / `!remove` などの機能は変更していません。

変更を反映したファイル
---
- `src/storage.py` — タイマー関連のスキーマ改変・新規/変更メソッドを追加
- `src/session.py` — `PomoSession.timer_name` を追加
- `src/runner.py` — `PomoRunner` に `timer_id` を追加し run() を変更
- `src/cog.py` — `pomo` / `timer` / `plist` / `pconfig` / `pdel` / `stats` / `help` の変更・追加

補足
---
必要であれば、既存 DB のマイグレーションスクリプト（重複検出と統合）を作成します。ご希望があれば作成しますのでお知らせください。

以上です。
