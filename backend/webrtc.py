"""WebRTC直結モードのバックエンド支援。

WebSocket中継と違い、音声とイベントはブラウザとOpenAIが直接やり取りする。
サーバーの役割は縮んで、(1) 一時キー(ephemeral client secret)の発行だけになる。
※ function calling の検索実行は /api/search、履歴は /api/history/log として
  main.py にルートがあり、ブラウザ側がデータチャネル経由で自前処理する。
APIキー(OPENAI_API_KEY)は従来どおりブラウザへ渡さない。
"""

import httpx

from config import REALTIME_MODEL, get_openai_api_key
from personas import load_persona
from relay import build_session_config


async def mint_client_secret(persona_id: str, mode: str = "ptt") -> dict:
    """ペルソナ設定と会話モードを埋め込んだ一時キーを発行する(有効期限はOpenAI側の既定)。"""
    api_key = get_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY が .env に設定されていません")
    if mode not in ("ptt", "vad"):
        mode = "ptt"
    persona = load_persona(persona_id)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"session": build_session_config(persona, mode)},
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "value": data.get("value"),
        "expires_at": data.get("expires_at"),
        "model": REALTIME_MODEL,
        "persona": persona["name"],
    }
