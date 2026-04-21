# Discord ポモドーロBot 設計メモ

この文書は、現在の実装構成をまとめた設計メモである。ソースは `src/`、音声とDBは `assets/`、説明書類は `documents/` に整理している。

## 1. 目的

Discordのボイスチャンネル上で動くポモドーロBotの責務分割と、各モジュールの役割を明確化する。

## 2. 現在の配置

### 2.1 ディレクトリ

- `src/`: 実装本体
- `assets/`: 音声とデータ保存
- `documents/`: 仕様書・マニュアル・設計書

### 2.2 ファイル

- `src/timer.py`: 起動入口。依存チェックと Bot 初期化
- `src/session.py`: `PomoSession` と `SessionManager`
- `src/storage.py`: `StatsRepository`
- `src/audio.py`: `AudioPlayer`
- `src/views.py`: `PomoView` と `JoinView`
- `src/runner.py`: `PomoRunner`
- `src/cog.py`: `PomoCog`
- `assets/ding.mp3`: 通知音
- `assets/pomo.db`: SQLite データベース

## 3. 実行の流れ

1. `src/timer.py` が `DISCORD_BOT_TOKEN` を確認する。
2. Voice依存を確認する。
3. `assets/` を前提に DB と音声のパスを設定する。
4. `StatsRepository` を初期化する。
5. `SessionManager` と `AudioPlayer` を生成する。
6. `PomoCog` を Bot に登録して起動する。

## 4. モジュール責務

### 4.1 `PomoSession`

単一セッションの状態を保持する。ホスト、対象参加者、順序情報、進捗、UI参照をまとめる。

主な役割:

- ホストと対象メンバーの管理
- VC在席判定
- ホスト移譲
- 参加/退出の反映
- 表示用の対象列生成

### 4.2 `SessionManager`

複数セッションの集合を管理し、ユーザーIDから所属セッションを O(1) で引けるようにする。

主な役割:

- セッション作成
- セッション取得
- セッション削除
- ユーザー逆引き
- index 更新

### 4.3 `PomoRunner`

作業フェーズと休憩フェーズを回す実行本体。

主な役割:

- メインループ制御
- 1秒 tick の状態遷移
- 作業分数の加算
- セッション完了時のDB更新
- 休憩/作業メッセージ更新
- VC切断や在席ゼロの終了判定

### 4.4 `StatsRepository`

SQLiteへの読み書きを集約する。

主な役割:

- テーブル初期化
- 作業分数の加算
- 完了セッション数の加算
- 集計取得
- リセット

### 4.5 `AudioPlayer`

FFmpeg経由で `assets/ding.mp3` を再生する。

主な役割:

- ファイル存在確認
- 再生中停止
- 再生完了待機

### 4.6 `PomoView` / `JoinView`

Discord UI ボタンのみに責務を絞る。

`PomoView`:

- 一時停止
- 再開
- 終了

`JoinView`:

- 参加
- 退出
- ホスト退出時の譲渡または終了要求

### 4.7 `PomoCog`

コマンドとイベントの入口。Botの外部インターフェースをまとめる。

対象コマンド:

- `!pomo`
- `!timer`
- `!add`
- `!remove`
- `!list`
- `!stats`
- `!reset`
- `!mute`
- `!test`
- `!help`

イベント:

- `on_ready`
- `on_voice_state_update`

## 5. 主要なルール

- 作業分は 1 分ごとに加算する。
- 長休憩は `session_count % interval == 0` で判定する。
- ホストは `join_order` の順で移譲する。
- VC在席がなくなった場合は猶予を置いて自動終了する。
- `!timer` は古い参加パネルを無効化し、新しいパネルを再投稿する。

## 6. 保守の指針

- セッション状態に関する変更は `PomoSession` と `SessionManager` を同時に確認する。
- 終了条件の変更は `PomoRunner._wait_tick()` と `PomoRunner._has_members_with_grace()` を対で見直す。
- ホスト移譲の条件変更は `JoinView.leave_button()` と `PomoCog.on_voice_state_update()` を同時に更新する。
- 資産追加時は `assets/` に置き、コード内のパス定数を通す。

## 7. ひとことで

このプロジェクトは、`src/` に実装、`assets/` に実体、`documents/` に説明書を置く構成で運用する。
