"""環境変数と定数。他のモジュールはここから設定を読む。"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent  # backend/
FRONTEND_DIR = BASE_DIR.parent / "frontend"
PERSONA_DIR = BASE_DIR / "personas"
# コンテナではボリューム(/data等)に逃がせるよう環境変数で上書き可能にする
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "chat_history.db"))

load_dotenv(BASE_DIR / ".env")

REALTIME_MODEL = os.getenv("REALTIME_MODEL", "gpt-realtime")
VOICE = os.getenv("REALTIME_VOICE", "marin")
TRANSCRIBE_MODEL = os.getenv("REALTIME_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
SEARCH_MODEL = os.getenv("SEARCH_MODEL", "gpt-5-mini")

OPENAI_WS_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"

# ブラウザから転送を許可するイベント種別(それ以外は破棄)
ALLOWED_CLIENT_EVENTS = {
    "input_audio_buffer.append",
    "input_audio_buffer.commit",
    "input_audio_buffer.clear",
    "response.create",
    "response.cancel",
}

# ---- Cognito認証の設定 ----
# COGNITO_* が設定されていなければ認証なしで動作する(ローカル開発用)
COGNITO_REGION = os.getenv("COGNITO_REGION", "")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN", "").rstrip("/")
AUTH_ENABLED = bool(COGNITO_REGION and COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID)
COGNITO_ISSUER = (
    f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
)


def get_openai_api_key() -> str:
    """接続ごとに .env を再読み込みして返す(キー追記後のサーバー再起動を不要にする)。"""
    load_dotenv(BASE_DIR / ".env", override=True)
    return os.getenv("OPENAI_API_KEY", "")
