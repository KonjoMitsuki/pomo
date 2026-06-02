# VOICEVOX 化リファクタリング報告

作成日: 2026-06-02
作成者: 自動生成（リファクタリング作業）
対象ブランチ: feature/voice-announce

## 概要
既存の通知音（`assets/ding.mp3`）による固定MP3再生を廃止し、ローカルで稼働する VOICEVOX エンジン（`http://127.0.0.1:50021`）を利用して動的に音声を生成・再生するように差し替えました。使用するキャラクターは「ナースロボ＿タイプＴ（ノーマル）」で、`speaker_id = 47` を固定で利用します。

## 目的
- 通知音を固定ファイルから読み上げへ移行することでメッセージを動的に生成できるようにする
- 将来的な多言語/台本差し替えを容易にする
- VOICEVOX が停止している場合でも Bot 全体がフリーズしないようにフォールバックを入れる

## 変更ファイル（主要）
- `src/audio.py`
  - VOICEVOX 呼び出しロジックを実装。
  - `generate_voice(text: str) -> bool` を追加し、`/audio_query` と `/synthesis` を叩いて WAV を `assets/voicevox_temp.wav` に保存。
  - `play_voice(voice_client, text, volume=1.0) -> bool` で生成→再生を行う。内部で `asyncio.Lock()` による排他制御を行う。
  - `aiohttp` のタイムアウト設定と例外ハンドリングを実装し、生成失敗時は False を返す。

- `src/runner.py`
  - フェーズ切り替え時に動的メッセージを作成して `AudioPlayer.play_voice` を呼ぶように変更。
  - 読み上げタイミング:
    - 作業開始時: "第 {session_count} セットの作業を開始します。時間は {work_min} 分間です。集中していきましょう。"
    - 休憩開始時: "セッション {session_count} 完了です。これより {break_time} 分間の {break_type} に入ります。"
    - 全行程終了時: "予定されていたすべてのセッションが終了しました。大変お疲れ様でした。"
  - 旧 `file_exists()` / `play()` 呼び出しは削除。失敗時はチャットに警告して無音で継続するフォールバックを実装。

- `src/cog.py`
  - `!test` コマンドを VOICEVOX による音声テストに切り替え。テスト用テキスト:
    - "業務連絡。ナースロボ、タイプＴです。音声出力を確認しました。システム正常です。"
  - 旧ファイル存在確認 / 直接 `FFmpegPCMAudio` を使う処理を削除。

- `src/timer.py`
  - `AudioPlayer` の生成を `AudioPlayer()` に更新（旧: `AudioPlayer(SOUND_FILE)` を削除）。

- ドキュメント
  - `README.md`, `documents/manual.md`, `documents/specification.md` を更新し、`aiohttp` と VOICEVOX 前提の記述を追加、`assets/ding.mp3` 参照を削除。

## 実装上の注意点
- VOICEVOX サーバの状態確認やヘルスチェックは実装していません。API 呼び出しで失敗すると `generate_voice` が False を返し、上位でチャット通知して無音で進行します。
- 一時ファイルは `assets/voicevox_temp.wav` に固定で上書き保存します。ディスク書き込み負荷が気になる場合はメモリストリーム化の検討を推奨します。
- `speaker_id` を固定（47）にしているため、将来的に変更可能にする場合は設定ファイル・コマンドの追加を推奨します。

## テスト手順（推奨）
1. VOICEVOX エンジンをローカルで起動して `http://127.0.0.1:50021` が応答することを確認。
   - 例: `curl -s http://127.0.0.1:50021/version`
2. 仮想環境を有効化し、依存をインストール:
```bash
python -m venv venv
source venv/bin/activate
pip install discord.py aiosqlite aiohttp
```
3. Bot を起動（環境変数 `DISCORD_BOT_TOKEN` を設定）:
```bash
export DISCORD_BOT_TOKEN='your_token_here'
python src/timer.py
```
4. Discord でボイスチャンネルに入り、`!test` を実行して読み上げを確認。
5. `!pomo` を実行して、作業開始・休憩開始・全終了の各アナウンスが正常に再生されるか確認。

## ロールバック手順
- もし VOICEVOX を一時的に無効化したい場合は、デプロイ前のコミット（feature/voice-announce の前）に戻すか、`src/audio.py` を元の `FFmpeg` ベースの `AudioPlayer` に差し替えてください。

## 今後の改善候補
- `speaker_id` を設定可能にする（環境変数 / コマンド）。
- 一時ファイルを in-memory にしてディスク IO を削減。
- VOICEVOX のヘルスチェックエンドポイントを定期実行して、起動確認を Bot 起動時に行う。
- 音声合成に失敗した場合のリトライロジック（指数バックオフ）を導入。

---

作業履歴（主なコミット / 編集箇所の要約）
- `src/audio.py`: VOICEVOX 生成/再生ロジックを実装（`generate_voice`, `play_voice`）。
- `src/runner.py`: フェーズ切替の読み上げ呼び出し実装。フォールバック追加。
- `src/cog.py`: `!test` を VOICEVOX 音声テストへ変更。
- `src/timer.py`: `AudioPlayer()` 初期化に変更。
- ドキュメント更新: `README.md`, `documents/manual.md`, `documents/specification.md`。


## 付録: 生成AI（会話録）からの補足要約
ユーザー提供の生成AIとの会話録（`documents/report/voicevox導入会話録`）を踏まえ、以下の点を本報告へ追記します。

- 実装方式の比較
  - 動的生成方式（今回採用）: フェーズごとに VOICEVOX API を呼び出して音声を生成する方式。動的なセリフが可能だが、VOICEVOX が稼働していないと再生できない点に注意。
  - 事前生成方式: 起動前に音声ファイルを用意しておく方式。起動時のラグやVOICEVOX稼働依存を避けられるが、動的な文言は難しい。

- VOICEVOX の起動方法（会話録の推奨）
  - デスクトップアプリを起動する方法（手軽）。ただしアプリを終了するとAPIサーバーも止まる点に注意。
  - Docker コンテナでエンジンを常駐させる方法（サーバ運用に推奨）。会話録で紹介された起動例:

```bash
docker pull voicevox/voicevox_engine:cpu-latest
docker run -d --name voicevox-pomo -p 127.0.0.1:50021:50021 --restart unless-stopped voicevox/voicevox_engine:cpu-latest
```

- スピーカーID（会話録より）
  - `47` = ナースロボ＿タイプＴ（ノーマル） — 本実装でデフォルトに設定済み。
  - 他の性格（例: 48,49,50）へ変更可能である旨の補足あり。

- サーバ運用上の注意（DigitalOcean等の Droplet）
  - VOICEVOX エンジンは音声合成時にメモリを多く消費するため、1GB 未満の環境では不安定。最低2GB、理想は4GB以上を推奨。
  - メモリ不足の場合はスワップ領域の追加やインスタンスタイプの見直しを検討する必要がある。

- 実装のヒント（会話録より）
  - `audio_query` で取得した `query_json` の `speedScale` や `pitchScale` を書き換えれば、Bot 側で話速や音高さを調整可能。
  - 生成失敗時のスキップ、リトライや退避行動（無音で進行する等）の実装が推奨される。

本付録は、作業時に参照した生成AIの提案内容を要約したもので、実装方針や運用上の注意点の補完を目的としています。

