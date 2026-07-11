"""Push-to-Talk リアルタイム音声通話サーバー。

ブラウザ <-> FastAPI(WebSocket) <-> OpenAI Realtime API の中継を行う。
APIキーはサーバー側の .env にのみ保持し、ブラウザには渡さない。
"""

import asyncio
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import httpx
import jwt as pyjwt
import websockets
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from jwt import PyJWKClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-realtime")
VOICE = os.getenv("REALTIME_VOICE", "marin")
TRANSCRIBE_MODEL = os.getenv("REALTIME_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
SEARCH_MODEL = os.getenv("SEARCH_MODEL", "gpt-5-mini")

# ---- ペルソナ ----
PERSONA_DIR = BASE_DIR / "personas"

# どのペルソナにも必ず付く共通ルール(検索の使用と作話防止)
COMMON_RULES = (
    "\n\n【共通ルール】応答は日本語で、音声向けに簡潔にすること。"
    "最近の出来事・人物・統計・ニュースなど、学習データにない可能性がある事実を"
    "聞かれたら、必ず web_search ツールで調べてから答えること。"
    "調べても分からないことは、推測で断定せず正直に「分からない」と言うこと。"
)

FALLBACK_PERSONA = {
    "id": "default",
    "name": "標準アシスタント",
    "voice": VOICE,
    "instructions": "あなたは親切な音声アシスタントです。" + COMMON_RULES,
}


def parse_persona(persona_id: str, text: str) -> dict:
    """personas/*.md の frontmatter(name, voice)と本文(instructions)を解釈する。"""
    name, voice, body = persona_id, VOICE, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta, body = parts[1], parts[2]
            for line in meta.strip().splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "name":
                    name = value.strip()
                elif key.strip() == "voice":
                    voice = value.strip()
    return {
        "id": persona_id,
        "name": name,
        "voice": voice,
        "instructions": body.strip() + COMMON_RULES,
    }


def load_persona(persona_id: str) -> dict:
    """ペルソナを毎回ファイルから読む(編集がサーバー再起動なしで反映される)。"""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", persona_id or ""):
        persona_id = "default"
    path = PERSONA_DIR / f"{persona_id}.md"
    if not path.is_file():
        path = PERSONA_DIR / "default.md"
        persona_id = "default"
    if not path.is_file():
        return FALLBACK_PERSONA
    return parse_persona(persona_id, path.read_text(encoding="utf-8"))

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

# ---- Cognito認証 ----
# COGNITO_* が設定されていなければ認証なしで動作する(ローカル開発用)
COGNITO_REGION = os.getenv("COGNITO_REGION", "")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN", "").rstrip("/")
AUTH_ENABLED = bool(COGNITO_REGION and COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID)
COGNITO_ISSUER = (
    f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
)

_jwks_client: PyJWKClient | None = None


def verify_token(token: str) -> dict:
    """Cognito発行のIDトークンを検証してクレームを返す。失敗時は例外。"""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(f"{COGNITO_ISSUER}/.well-known/jwks.json")
    key = _jwks_client.get_signing_key_from_jwt(token).key
    claims = pyjwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=COGNITO_CLIENT_ID,
        issuer=COGNITO_ISSUER,
    )
    if claims.get("token_use") != "id":
        raise ValueError("IDトークンではありません")
    return claims


def require_auth(authorization: str = Header(default="")) -> dict:
    """HTTP API用の認証依存。認証無効時は素通し。"""
    if not AUTH_ENABLED:
        return {}
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="認証が必要です")
    try:
        return verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="トークンが無効です")


@app.get("/api/auth/config")
def auth_config() -> dict:
    """フロントがログインURLを組むための公開設定(シークレットは含まない)。"""
    return {
        "enabled": AUTH_ENABLED,
        "domain": COGNITO_DOMAIN,
        "client_id": COGNITO_CLIENT_ID,
        "region": COGNITO_REGION,
    }


# ---- チャット履歴 (SQLite) ----
DB_PATH = BASE_DIR / "chat_history.db"


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )


init_db()


def save_message(session_id: str, role: str, text: str) -> None:
    if not text.strip():
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, text, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, text, datetime.now().isoformat(timespec="seconds")),
        )


@app.get("/api/history")
def history(limit: int = 30, user: dict = Depends(require_auth)) -> list:
    """セッション単位でグループ化した履歴を新しい順に返す。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        session_ids = [
            r["session_id"]
            for r in conn.execute(
                "SELECT session_id, MAX(id) AS last FROM messages"
                " GROUP BY session_id ORDER BY last DESC LIMIT ?",
                (limit,),
            )
        ]
        out = []
        for sid in session_ids:
            rows = conn.execute(
                "SELECT role, text, created_at FROM messages"
                " WHERE session_id = ? ORDER BY id",
                (sid,),
            ).fetchall()
            out.append(
                {
                    "session_id": sid,
                    "started_at": rows[0]["created_at"],
                    "messages": [dict(r) for r in rows],
                }
            )
    return out


# Realtimeモデルに公開するツール定義。実行はこのサーバーが担当する
WEB_SEARCH_TOOL = {
    "type": "function",
    "name": "web_search",
    "description": (
        "インターネットで最新情報を検索する。最近の出来事・人物・成績・ニュースなど、"
        "学習データにない可能性のある事実を答える前に必ず使うこと。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "検索クエリ(日本語可)"},
        },
        "required": ["query"],
    },
}


async def run_web_search(api_key: str, query: str) -> str:
    """Responses API の web_search ツールで検索し、音声向けの短い回答文を返す。"""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": SEARCH_MODEL,
                "input": query,
                "tools": [{"type": "web_search"}],
                "instructions": (
                    "Web検索を使って質問に日本語で簡潔に答える。"
                    "結果は音声で読み上げられるため、URL・マークダウン・箇条書きは使わない。"
                ),
            },
        )
        resp.raise_for_status()
        data = resp.json()
    texts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
    text = "\n".join(texts).strip()
    # 音声読み上げの邪魔になる出典リンクを除去
    text = re.sub(r"\s*\(\[[^\]]*\]\([^)]*\)\)", "", text)  # ([site](url)) 形式
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [text](url) 形式
    return text or "検索結果を取得できませんでした。"


def session_update_event(persona: dict) -> dict:
    """Push-to-Talk 用のセッション設定(サーバーVADは無効化し、手動commitで区切る)。"""
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": persona["instructions"],
            "tools": [WEB_SEARCH_TOOL],
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": None,
                    "transcription": {"model": TRANSCRIBE_MODEL, "language": "ja"},
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": persona["voice"],
                },
            },
        },
    }


@app.get("/api/personas")
def personas(user: dict = Depends(require_auth)) -> list:
    """personas/ ディレクトリのペルソナ一覧(default先頭、あとはファイル名順)。"""
    items = []
    if PERSONA_DIR.is_dir():
        for path in sorted(PERSONA_DIR.glob("*.md")):
            p = parse_persona(path.stem, path.read_text(encoding="utf-8"))
            items.append({"id": p["id"], "name": p["name"]})
    if not items:
        items.append({"id": "default", "name": FALLBACK_PERSONA["name"]})
    items.sort(key=lambda x: x["id"] != "default")  # defaultを先頭に
    return items


@app.websocket("/ws")
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

    session_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    async with openai_ws:
        await openai_ws.send(json.dumps(session_update_event(persona), ensure_ascii=False))
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
                save_message(session_id, "search", query)
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
                            save_message(session_id, "user", ev.get("transcript", ""))
                        elif etype in (
                            "response.output_audio_transcript.done",
                            "response.audio_transcript.done",
                        ):
                            save_message(session_id, "assistant", ev.get("transcript", ""))
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


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
