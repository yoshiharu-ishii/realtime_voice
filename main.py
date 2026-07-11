"""Push-to-Talk リアルタイム音声通話サーバー。

ブラウザ <-> FastAPI(WebSocket) <-> OpenAI Realtime API の中継を行う。
APIキーはサーバー側の .env にのみ保持し、ブラウザには渡さない。
"""

import asyncio
import json
import os
from pathlib import Path

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-realtime")
VOICE = os.getenv("REALTIME_VOICE", "marin")
TRANSCRIBE_MODEL = os.getenv("REALTIME_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
INSTRUCTIONS = os.getenv(
    "REALTIME_INSTRUCTIONS",
    "あなたは親切な音声アシスタントです。日本語で簡潔に応答してください。",
)

OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"

# ブラウザから転送を許可するイベント種別(それ以外は破棄)
ALLOWED_CLIENT_EVENTS = {
    "input_audio_buffer.append",
    "input_audio_buffer.commit",
    "input_audio_buffer.clear",
    "response.create",
    "response.cancel",
}

app = FastAPI()


def session_update_event() -> dict:
    """Push-to-Talk 用のセッション設定(サーバーVADは無効化し、手動commitで区切る)。"""
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": INSTRUCTIONS,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": None,
                    "transcription": {"model": TRANSCRIBE_MODEL, "language": "ja"},
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": VOICE,
                },
            },
        },
    }


@app.websocket("/ws")
async def relay(browser_ws: WebSocket) -> None:
    await browser_ws.accept()

    # 接続ごとに .env を再読み込み(キー追記後のサーバー再起動を不要にする)
    load_dotenv(BASE_DIR / ".env", override=True)
    api_key = os.getenv("OPENAI_API_KEY", "")

    if not api_key:
        await browser_ws.send_json(
            {"type": "proxy.error", "message": "OPENAI_API_KEY が .env に設定されていません"}
        )
        await browser_ws.close()
        return

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        openai_ws = await websockets.connect(
            OPENAI_WS_URL, additional_headers=headers, max_size=16 * 1024 * 1024
        )
    except Exception as exc:  # 認証エラー・ネットワーク断など
        await browser_ws.send_json(
            {"type": "proxy.error", "message": f"OpenAI への接続に失敗: {exc}"}
        )
        await browser_ws.close()
        return

    async with openai_ws:
        await openai_ws.send(json.dumps(session_update_event()))
        await browser_ws.send_json({"type": "proxy.ready", "model": REALTIME_MODEL})

        async def browser_to_openai() -> None:
            while True:
                raw = await browser_ws.receive_text()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event.get("type") in ALLOWED_CLIENT_EVENTS:
                    await openai_ws.send(raw)

        async def openai_to_browser() -> None:
            async for raw in openai_ws:
                await browser_ws.send_text(raw)

        tasks = [
            asyncio.create_task(browser_to_openai()),
            asyncio.create_task(openai_to_browser()),
        ]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            # 例外があればログに出す(WebSocketDisconnect は正常終了扱い)
            for task in done:
                exc = task.exception()
                if exc and not isinstance(
                    exc, (WebSocketDisconnect, websockets.ConnectionClosed)
                ):
                    print(f"relay error: {exc!r}")
        finally:
            for task in tasks:
                task.cancel()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
