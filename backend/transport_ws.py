"""WebSocket回線: 中継サーバー(通訳者)。

ブラウザとOpenAI Realtime APIの間に立ち、2つのループを並走させる:
  browser_to_openai: ブラウザの声を聞き続ける(ホワイトリスト検査して転送)
  openai_to_browser: OpenAIの声を聞き続ける(転送+履歴保存+ツール検知)
どちらかの電話が切れたら、もう片方も必ず切る(切断は正常系)。
セッション設定は session.py(両プロトコル共通)から取る。
"""

import asyncio
import json
import uuid
from datetime import datetime

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from auth import verify_token
from config import (
    ALLOWED_CLIENT_EVENTS,
    AUTH_ENABLED,
    OPENAI_WS_URL,
    REALTIME_MODEL,
    get_openai_api_key,
)
from history import save_message
from personas import load_persona
from search import run_web_search
from session import session_update_event



async def relay(browser_ws: WebSocket) -> None:
    await browser_ws.accept()

    # 認証: 最初のメッセージで proxy.auth {token} を要求
    # (トークンをURLに載せるとアクセスログに残るため、メッセージで受け取る)
    user_email = ""
    if AUTH_ENABLED:
        try:
            raw = await asyncio.wait_for(browser_ws.receive_text(), timeout=10)
            ev = json.loads(raw)
            if ev.get("type") != "proxy.auth":
                raise ValueError("最初のメッセージが proxy.auth ではない")
            claims = await asyncio.to_thread(verify_token, ev.get("token", ""))
            user_email = claims.get("email", "")
        except Exception:
            await browser_ws.send_json(
                {"type": "proxy.error", "message": "認証に失敗しました。再ログインしてください"}
            )
            await browser_ws.close()
            return

    persona = load_persona(browser_ws.query_params.get("persona", "default"))
    mode = browser_ws.query_params.get("mode", "ptt")
    if mode not in ("ptt", "vad"):
        mode = "ptt"

    api_key = get_openai_api_key()
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

    session_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    async with openai_ws:
        await openai_ws.send(json.dumps(session_update_event(persona, mode), ensure_ascii=False))
        await browser_ws.send_json(
            {
                "type": "proxy.ready",
                "model": REALTIME_MODEL,
                "persona": persona["name"],
                "user": user_email,
            }
        )

        async def browser_to_openai() -> None:
            while True:
                raw = await browser_ws.receive_text()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event.get("type") in ALLOWED_CLIENT_EVENTS:
                    await openai_ws.send(raw)

        async def handle_function_calls(ev: dict) -> None:
            """response.done に含まれる web_search 呼び出しを実行し、結果を返して応答を再開する。"""
            calls = [
                item
                for item in ev.get("response", {}).get("output", [])
                if item.get("type") == "function_call" and item.get("name") == "web_search"
            ]
            if not calls:
                return
            for call in calls:
                try:
                    query = json.loads(call.get("arguments", "{}")).get("query", "")
                except json.JSONDecodeError:
                    query = ""
                await browser_ws.send_json({"type": "proxy.search", "query": query})
                save_message(session_id, "search", query, persona["name"])
                try:
                    result = await run_web_search(api_key, query)
                except Exception as exc:
                    result = f"検索に失敗しました: {exc}"
                await openai_ws.send(
                    json.dumps(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call.get("call_id"),
                                "output": result,
                            },
                        },
                        ensure_ascii=False,
                    )
                )
            # ツール結果を踏まえた応答を生成させる
            await openai_ws.send(json.dumps({"type": "response.create"}))

        async def openai_to_browser() -> None:
            async for raw in openai_ws:
                # 保存・ツール実行が必要なイベントだけJSONを見る
                # (音声deltaが大半なので、まず安価な部分一致で絞る)
                if (
                    "transcription.completed" in raw
                    or "transcript.done" in raw
                    or "function_call" in raw
                ):
                    try:
                        ev = json.loads(raw)
                        etype = ev.get("type", "")
                        if etype == "conversation.item.input_audio_transcription.completed":
                            save_message(
                                session_id, "user", ev.get("transcript", ""), persona["name"]
                            )
                        elif etype in (
                            "response.output_audio_transcript.done",
                            "response.audio_transcript.done",
                        ):
                            save_message(
                                session_id, "assistant", ev.get("transcript", ""), persona["name"]
                            )
                        elif etype == "response.done":
                            await browser_ws.send_text(raw)
                            await handle_function_calls(ev)
                            continue
                    except json.JSONDecodeError:
                        pass
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
