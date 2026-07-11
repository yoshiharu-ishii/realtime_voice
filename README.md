# Push-to-Talk リアルタイム音声通話

ブラウザ(Push-to-Talk) ↔ FastAPI(WebSocket中継) ↔ OpenAI Realtime API の構成。
APIキーはサーバー側の `.env` にのみ保持し、ブラウザには一切渡らない。

## 構成

```
ブラウザ                    FastAPI (main.py)              OpenAI
─────────                  ─────────────────              ──────
マイク → AudioWorklet       /ws で中継                     Realtime API
  24kHz PCM16 化      ──→  許可イベントのみ転送      ──→   (gpt-realtime)
スピーカー ← Web Audio ←──  サーバーイベントを転送    ←──   音声delta
```

- 押している間: `input_audio_buffer.append`(base64 PCM16 24kHz)を送信
- 離した時: `input_audio_buffer.commit` + `response.create`
- 応答中に押すと `response.cancel` + 再生停止で割り込み(バージイン)
- サーバーVAD(`turn_detection`)は無効化し、PTTで発話区間を制御
- 会話の文字起こしは SQLite(`chat_history.db`)に自動保存され「履歴」タブで見返せる
- モデルが最新情報を必要と判断すると `web_search` ツールを呼び出し、
  サーバーが OpenAI Responses API の Web 検索で調べて結果を返す(ハルシネーション対策)

## セットアップ

[uv](https://docs.astral.sh/uv/) を使用(依存関係は pyproject.toml / uv.lock で管理)。

```bash
cd realtime_voice
cp .env.example .env   # OPENAI_API_KEY を設定
uv sync                # .venv 作成 + 依存インストール
```

## 起動

```bash
uv run uvicorn main:app --port 8000
```

ブラウザで http://localhost:8000 を開き、ボタン(またはスペースキー)を
押している間だけ話す。マイク許可が必要。

※ getUserMedia の制約上、localhost 以外で使う場合は HTTPS が必要。
