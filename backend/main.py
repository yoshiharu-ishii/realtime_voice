"""Push-to-Talk リアルタイム音声通話サーバー(エントリポイント)。

ブラウザ <-> FastAPI(WebSocket) <-> OpenAI Realtime API の中継を行う。
APIキーはサーバー側の .env にのみ保持し、ブラウザには渡さない。

構成: config(設定) / auth(Cognito) / personas / history(SQLite) /
search(web_searchツール) / relay(WebSocket中継)。このファイルは
FastAPIの組み立てとルーティングだけを持つ。
"""

import asyncio

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import auth
import history
import personas
import relay
import search
import webrtc
from config import AUTH_ENABLED, FRONTEND_DIR, get_openai_api_key

history.init_db()

app = FastAPI()


@app.middleware("http")
async def cache_control(request: Request, call_next):
    """キャッシュ制御。/ は認証状態でアプリと門番ページを出し分けるため、
    キャッシュされると「ログアウト時代の門番ページ」が使い回されて
    ログイン画面との無限ループになる。絶対にキャッシュさせない。"""
    response = await call_next(request)
    if request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static"):
        # 静的ファイルは毎回サーバーへ再検証(304なら転送なし)。
        # 更新した app.js が古いまま動く事故を防ぐ
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/auth/config")
def auth_config() -> dict:
    return auth.public_config()


@app.get("/api/history")
def get_history(limit: int = 30, user: dict = Depends(auth.require_auth)) -> list:
    return history.list_history(limit)


@app.get("/api/personas")
def get_personas(user: dict = Depends(auth.require_auth)) -> list:
    return personas.list_personas()


# ---- WebRTC直結モード用 ----
# 音声・イベントはブラウザ⇄OpenAI直結になるため、サーバーは
# 一時キー発行・検索の実行代行・履歴の受け取りだけを担う


@app.get("/api/webrtc/secret")
async def webrtc_secret(
    persona: str = "default", mode: str = "ptt", user: dict = Depends(auth.require_auth)
) -> dict:
    try:
        return await webrtc.mint_client_secret(persona, mode)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"一時キーの発行に失敗: {exc}")


@app.post("/api/search")
async def api_search(body: dict, user: dict = Depends(auth.require_auth)) -> dict:
    """WebRTCモードのfunction calling実行代行(ブラウザにAPIキーを渡さないため)。"""
    query = str(body.get("query", ""))[:400]
    if not query:
        raise HTTPException(status_code=400, detail="queryが必要です")
    result = await search.run_web_search(get_openai_api_key(), query)
    return {"result": result}


@app.post("/api/history/log")
def api_history_log(body: dict, user: dict = Depends(auth.require_auth)) -> dict:
    """WebRTCモードでは中継が文字起こしを見られないため、ブラウザが履歴を送ってくる。"""
    role = body.get("role")
    if role not in {"user", "assistant", "search"}:
        raise HTTPException(status_code=400, detail="roleが不正です")
    session_id = str(body.get("session_id", ""))[:64]
    if not session_id:
        raise HTTPException(status_code=400, detail="session_idが必要です")
    history.save_message(session_id, role, str(body.get("text", "")), str(body.get("persona", ""))[:64])
    return {"ok": True}


app.websocket("/ws")(relay.relay)


@app.get("/")
async def index(request: Request) -> FileResponse:
    """認証有効時はCookieのIDトークンを検証してからアプリ本体を返す。

    未認証にはアプリのUIを一切見せず、門番ページ(login.html)だけを返す。
    これで「一瞬アプリが映る」フラッシュも、UI構造の情報開示もなくなる。
    """
    if AUTH_ENABLED:
        token = request.cookies.get("id_token", "")
        try:
            await asyncio.to_thread(auth.verify_token, token)
        except Exception:
            return FileResponse(FRONTEND_DIR / "login.html")
        if request.query_params:
            # 認証済みなのに ?code= 等が付いている(Cognitoセッションでの
            # 再ログインや履歴からの再訪)。認可コードをアドレスバーや
            # 履歴に残さないよう、クリーンなURLへリダイレクトする
            return RedirectResponse("/")
    return FileResponse(FRONTEND_DIR / "index.html")


# URLパスは /static のまま、配信元は frontend/ ディレクトリ
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
