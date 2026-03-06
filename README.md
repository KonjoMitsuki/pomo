# Discord ポモドーロタイマー Bot

Discordのボイスチャンネルで使えるポモドーロタイマーBotです。  
作業 → 休憩を自動で繰り返し、参加者ごとの作業時間とセッション数を記録します。

## 主な機能

- ポモドーロ自動ループ（小休憩 / 長休憩）
- 参加・退出ボタン（🙋 / 👋）
- ホスト退出時のホスト自動移行
- VC退出メンバーの自動除外
- 一時停止 / 再開 / 終了ボタン
- 統計保存（SQLite: `pomo.db`）
- 通知音のミュート切り替え（`!mute`）

## 必要ファイル

- `timer.py`
- `ding.mp3`（通知音）
- `pomo.db`（初回起動時に自動作成）

## 依存関係

```bash
pip install discord.py aiosqlite
```

音声通知のためにFFmpegも必要です。

```bash
# Ubuntu / Debian
sudo apt install ffmpeg
```

## セットアップ

1. Botトークンを環境変数に設定

```bash
export DISCORD_BOT_TOKEN='your_token_here'
```

2. Botを起動

```bash
python timer.py
```

## コマンド

### `!pomo [作業] [小休憩] [長休憩] [長休憩頻度]`

ポモドーロタイマーを開始します。デフォルトは `25 5 15 4` です。

例:

```bash
!pomo
!pomo 50 10 20 3
!pomo 1 1 1 2
```

### `!timer`

現在のタイマー設定、進捗、参加者ごとの今回作業時間を表示します。

### `!add @user` / `!remove @user` / `!list`

参加対象の追加・削除・一覧表示を行います。

### `!stats` / `!reset`

自分の累計作業時間とセッション数の表示 / リセットを行います。

### `!mute`

通知音のミュートを切り替えます（再実行で解除）。

### `!test`

VCで通知音再生テストを行います。

### `!help`

コマンド一覧を表示します。

## ボタン操作

- `⏸️ 一時停止`: カウント停止
- `▶️ 再開`: カウント再開
- `⏹️ 終了`: タイマー終了とBot退出
- `🙋 参加`: タイマー対象に参加
- `👋 退出`: タイマー対象から退出

## 動作のポイント

- ホストまたは参加者がVCに残っている限り継続
- 全員がVCから抜けると自動終了
- 長休憩は「完了セッション数 % 長休憩頻度 == 0」で発生
- 作業時間はVC内の対象者のみ加算

## データベース

`pomo.db` に以下を保存します。

```sql
CREATE TABLE stats (
    user_id INTEGER PRIMARY KEY,
    total_minutes INTEGER DEFAULT 0,
    sessions INTEGER DEFAULT 0
)
```

## トラブルシューティング

### 音が鳴らない

```bash
ffmpeg -version
ls -l ding.mp3
```

その後、`!test` で確認してください。

### 起動しない

```bash
echo $DISCORD_BOT_TOKEN
```

空なら環境変数が未設定です。

## セキュリティ注意

- トークンはコードに直接書かない
- `.env` を使う場合は `.gitignore` に追加

```bash
echo ".env" >> .gitignore
```

---

Version: 2026-03
