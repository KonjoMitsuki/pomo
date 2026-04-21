# Discord Pomodoro Bot マニュアル

## 概要

このボットはポモドーロタイマーを提供します。音声通知と簡易的な統計機能があり、ボイスチャンネル内で動作します。コマンド実行者と、同じボイスチャンネル内の追加対象メンバーの作業セッションを記録します。

## 必要なもの

- Python 3.9 以上
- Discord Bot Token（環境変数: `DISCORD_BOT_TOKEN`）
- `ffmpeg` が PATH 上にあること
- `assets/ding.mp3` を配置
- `assets/pomo.db` は自動生成

## セットアップ

1. Discord アプリと Bot を作成し、メッセージと音声の権限でサーバーに招待します。
2. 依存関係をインストールします。

```bash
pip install discord.py aiosqlite
```

3. トークンを設定します。

```bash
export DISCORD_BOT_TOKEN="your_token_here"
```

4. `assets/ding.mp3` を配置します。
5. 起動します。

```bash
python src/timer.py
```

## 基本的な使い方

すべてのコマンドは `!` プレフィックスです。

### ポモドーロ開始

```text
!pomo [work_minutes] [short_break] [long_break] [long_break_interval]
```

- デフォルト: `!pomo 25 5 15 4`
- 例: `!pomo 50 10 20 4`

注意:

- 事前にボイスチャンネルに参加している必要があります。
- ボットがボイスチャンネルに参加し、区切りで音が鳴ります。
- 操作メッセージに以下のボタンが表示されます。
  - 一時停止 (⏸️)
  - 再開 (▶️)
  - 終了 (⏹️)

### 加算対象ユーザーを追加

```text
!add @user
```

指定ユーザーをあなたのタイマー対象に追加します。作業セッション完了時に、同じボイスチャンネルにいる対象ユーザーのみが加算されます。

### 対象一覧

```text
!list
```

現在の対象ユーザー一覧を表示します。

### 対象から削除

```text
!remove @user
```

指定ユーザーを対象から外します。

### 統計表示

```text
!stats
```

累計作業時間と完了セッション数を表示します。

### 音声テスト

```text
!test
```

ボイスチャンネルに接続して `assets/ding.mp3` を一度再生します。

## ヒント

- ボイスに接続できない場合は、権限とサーバー設定を確認してください。
- 音が鳴らない場合は、`ffmpeg` の導入と `assets/ding.mp3` の配置を確認してください。
- データベース `assets/pomo.db` は自動生成されます。
- 実装は `src/`、音声とDBは `assets/`、説明書類は `documents/` に分けています。
