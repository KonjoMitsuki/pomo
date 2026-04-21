# Discord ポモドーロ Bot 仕様書

## 1. 目的

本Botは、Discordのボイスチャンネル(以下 VC)を前提として、ポモドーロタイマーの実行、参加者管理、作業時間の記録、通知音再生を提供する。

主目的は次の3点。

1. VC参加者の実在席ベースで作業時間を記録する。
2. 作業・休憩フェーズを継続実行し、手動停止または在席条件で自動終了する。
3. ホスト交代や参加/退出UIを通じて、セッションを柔軟に運用できるようにする。

## 2. ディレクトリ構成

現在の実体は次の3区分で管理する。

- `src/`: Pythonソースコード
- `assets/`: `ding.mp3` と `pomo.db`
- `documents/`: ドキュメント類

## 3. 実行前提

### 3.1 実行環境

- Python 3系
- discord.py
- aiosqlite
- FFmpeg 実行環境
- 通知音ファイル `assets/ding.mp3`

### 3.2 環境変数

- `DISCORD_BOT_TOKEN`:
  - 必須。
  - 未設定時はエラー表示後に終了する。
- `POMO_STRICT_VOICE_DEPS`:
  - 任意。デフォルトは `1` (strict)
  - `0/false/off/no` の場合は依存不足を警告扱いにする。

### 3.3 Voice依存確認

起動時に以下を import 解決可能か確認する。

- `nacl` (PyNaCl)
- `davey`

不足時の挙動:

- strict=true: エラー表示して終了
- strict=false: 警告表示して継続

## 4. アーキテクチャ

責務ごとにモジュール分割する構成。

- `src/timer.py`: エントリーポイント。環境変数検証、依存チェック、Bot初期化
- `src/session.py`: セッション状態 `PomoSession` と管理 `SessionManager`
- `src/storage.py`: SQLite永続化 `StatsRepository`
- `src/audio.py`: 通知音再生 `AudioPlayer`
- `src/views.py`: Discord UIボタン `PomoView` と `JoinView`
- `src/runner.py`: 実行ループ `PomoRunner`
- `src/cog.py`: コマンド/イベント `PomoCog`

依存方向は概ね次の通り。

- `src/timer.py` -> `src/cog.py`, `src/session.py`, `src/storage.py`, `src/audio.py`
- `src/cog.py` -> `src/runner.py`, `src/session.py`, `src/storage.py`, `src/audio.py`, `src/views.py`
- `src/runner.py` -> `src/session.py`, `src/storage.py`, `src/audio.py`, `src/views.py`
- `src/views.py` -> `src/session.py`

## 5. データモデル

### 5.1 PomoSession

単一セッションの状態を保持する。

主要フィールド:

- `host_id: int`
- `targets: set[int]`
- `join_order: list[int]`
- `work_min: int` (既定25)
- `short_brk: int` (既定5)
- `long_brk: int` (既定15)
- `interval: int` (既定4)
- `session_count: int`
- `session_work: dict[int, int]` (ユーザーごとの作業分)
- `muted: bool`
- `active: bool`
- `stop_requested: bool`
- UIメッセージ/ビュー参照:
  - `pomo_view`, `pomo_msg`, `control_msg`, `join_view`, `join_msg`

主要メソッド:

- `get_all_member_ids()`:
  - `host_id` と `targets` の和集合を返す。
- `get_vc_active_ids(voice_client)`:
  - VC在席判定に基づく有効対象ID配列を返す。
  - 優先: `voice_states`
  - フォールバック: `channel.members`
  - 追加フォールバック: hostの `Member.voice`
- `has_active_members(voice_client)`:
  - 在席対象が1人以上かを返す。
- `transfer_host(active_ids=None)`:
  - `join_order` 順に次ホストへ移管する。
  - 移管したIDを返す。不可なら `None`。
- `add_member(user_id)` / `remove_member(user_id)`
- `get_target_line()`:
  - 表示用メンション文字列を返す。

### 5.2 SessionManager

セッション全体管理と逆引きインデックスを担当する。

内部構造:

- `_sessions: dict[author_id, PomoSession]`
- `_user_index: dict[user_id, author_id]`

主要メソッド:

- `create(author_id, **kwargs)`
- `get(author_id)`
- `remove(author_id)`
- `find_by_user(user_id)`:
  - ユーザーが属するセッションをO(1)で逆引きする。
- `update_index(author_id)`:
  - 該当セッションに関する index を再構築する。
  - `session.active=False` 時は host のみ index 対象。

## 6. 永続化スキーマ

SQLiteテーブル `stats`。

```sql
CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER PRIMARY KEY,
    total_minutes INTEGER DEFAULT 0,
    sessions INTEGER DEFAULT 0
)
```

意味:

- `user_id`: DiscordユーザーID
- `total_minutes`: 累計作業分数
- `sessions`: 完了セッション数

## 7. 実行フロー

### 7.1 起動

1. `DISCORD_BOT_TOKEN` を検証する。
2. Voice依存チェックを行う。
3. `assets/` を前提に DB と音声のパスを設定する。
4. `StatsRepository.init()` を実行する。
5. `SessionManager` と `AudioPlayer` を生成する。
6. `PomoCog` を Bot に登録して起動する。

### 7.2 `!pomo` 開始

1. 実行者がVC参加済みか検証する。
2. Botを実行者VCへ接続/移動する。
3. 既存セッション状態を確認する。
   - active中なら拒否する。
   - inactive既存は再利用して設定を上書きする。
4. `PomoRunner.run()` を開始する。
5. 実行終了後にセッションUI参照をクリアし index を更新する。

### 7.3 `PomoRunner.run`

1. 在席猶予判定を行う。
2. 在席者がいるまで待機する。
3. `session_count += 1`
4. 作業フェーズを実行する。
5. 完了セッション数を記録する。
6. 休憩フェーズ(0分でなければ)を実行する。
7. 次セッションへ進む。

終了時:

- 終了メッセージを更新する。
- VC切断を行う。

### 7.4 `run_phase`

フェーズ共通処理。

- `PomoView` を生成してメッセージ送信する。
- 1秒tickループを実行する。
- 状態判定 `_wait_tick` に応じて分岐する。

作業フェーズ(emoji=🍅)のみ:

- 60秒ごとに `add_work_minutes(active_ids, 1)` を呼ぶ。
- `session_work[user]` を加算する。

### 7.5 `_wait_tick` の状態

戻り値:

- `tick`: 通常進行
- `paused`: 一時停止中
- `stopped`: 終了ボタン押下
- `wait_members`: 在席待ち
- `wait_vc`: VC復帰待ち
- `no_members`: 終了扱い

VC断/在席0に対しては、即終了せず猶予を置く。

- `VC_DOWN_GRACE_SECONDS = 12`
- `NO_MEMBER_GRACE_SECONDS = 12`

## 8. UI仕様

### 8.1 `PomoView`

対象: セッションメンバーのみ操作可能。

- 一時停止:
  - `paused=True`
  - 一時停止ボタンを無効化、再開を有効化する。
- 再開:
  - `paused=False`
  - 再開ボタンを無効化、一時停止を有効化する。
- 終了:
  - `stopped=True`
  - View停止する。

### 8.2 `JoinView`

- 参加:
  - botユーザーは拒否する。
  - `add_member` 成功時に index 更新する。
- 退出(非ホスト):
  - `remove_member` 成功時に index 更新する。
- 退出(ホスト):
  - 在席対象を基準に `transfer_host` を実行する。
  - 成功時はホスト移譲通知を出す。
  - 失敗時は `stop_requested=True` で終了要求する。

## 9. コマンド仕様

全コマンドは `!` プレフィックス。

### 9.1 `!pomo [work] [short] [long] [interval]`

- 既定: `25 5 15 4`
- VC未参加なら開始不可
- 既に同一実行者セッションが active なら拒否

### 9.2 `!timer`

- 自分が属する active セッション情報をEmbed表示する。
- 参加パネルを最新位置へ再投稿する。

### 9.3 `!add @user`

- botは追加不可
- 自分のセッション対象へ追加
- セッションが無ければ新規作成

### 9.4 `!list`

- 自分の対象一覧を表示
- 対象が無ければ案内文

### 9.5 `!remove @user`

- botは対象外
- 自分の対象から削除

### 9.6 `!stats`

- 自分の `total_minutes` と `sessions` を表示

### 9.7 `!reset`

- 自分の統計行を削除
- 削除前値を表示

### 9.8 `!mute`

- activeセッションの通知音ミュートをトグル

### 9.9 `!test`

- 実行者VCに接続し通知音再生テスト

### 9.10 `!help`

- コマンド一覧Embed表示

## 10. イベント仕様

### 10.1 `on_ready`

- ログインユーザー情報を標準出力する。

### 10.2 `on_voice_state_update`

対象条件:

- チャンネル変化あり
- 離脱前チャンネルが存在
- botでない
- 対象セッションが active

分岐:

- 離脱者がホスト:
  - 在席対象基準で `transfer_host` を実行する。
  - 移譲不可なら `stop_requested=True` にする。
- 離脱者が非ホスト対象:
  - `remove_member` を実行する。

## 11. ホスト移譲仕様

移譲ルール:

1. `join_order` を先頭から走査する。
2. 現ホストIDは候補外にする。
3. `targets` に含まれないIDは候補外にする。
4. `active_ids` 指定時は在席IDのみ候補にする。
5. 最初に合致したユーザーへ移譲する。

副作用:

- 新ホストは `targets` から削除される。
- `host_id` が更新される。

## 12. 終了条件

セッションは以下で終了する。

1. 終了ボタン押下 (`view.stopped=True`)
2. `stop_requested=True`
3. 在席0状態が猶予時間を超過
4. VC切断状態が猶予時間を超過

## 13. 通知音仕様

- ファイル存在時のみ再生する。
- 再生中なら一旦停止してから新規再生する。
- 作業終了: volume=1.0
- 休憩終了: volume=1.5
- 再生待機は最大約5秒(0.1秒 x 50回)

## 14. 記録仕様

- 分加算は作業フェーズのみ
- 加算対象はその時点でVC在席中の対象メンバー
- セッション完了数は作業完了時に在席対象へ +1
- 途中退出者は退出後の分/回は増えない

## 15. 例外/障害時の挙動

- VC接続/移動失敗時は理由を返信して開始中止
- 音声ファイル欠如時はテキスト警告のみ
- 旧JoinView無効化時の edit 失敗は握りつぶして継続
- セッション終了後は view/message 参照を必ずクリア

## 16. 制約

- 権限(ロール)ベースの操作制限は未実装
- タイマー状態の永続復元は未実装 (プロセス再起動で消失)
- 単一プロセス前提。複数プロセス共有状態は未対応
- 時間入力の妥当性制約(上限/下限)は厳密チェック未実装

## 17. 拡張ポイント

1. セッション状態のDB永続化
2. Slash Command化
3. 設定管理(ギルド単位既定値)
4. エラー/監視ログの構造化
5. 大規模同時運用向けにtick実装最適化

## 18. 保守時の注意

- `PomoSession` のフィールド変更時は、`SessionManager.update_index` と UI参照クリア処理を同時に見直すこと。
- ホスト移譲ロジックを変更する場合、`JoinView.leave_button` と `on_voice_state_update` の両経路を必ず同時修正すること。
- 実行ループ条件を変更する場合、`_wait_tick` と `_has_members_with_grace` の整合を維持すること。
- 資産を追加する場合は `assets/` に置き、パス定義を `src/timer.py` 側で集約すること。
