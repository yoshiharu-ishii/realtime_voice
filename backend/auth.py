"""Cognito認証。IDトークンのJWKS署名検証と、HTTP API用の認証依存。"""

import jwt as pyjwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from config import AUTH_ENABLED, COGNITO_CLIENT_ID, COGNITO_DOMAIN, COGNITO_ISSUER, COGNITO_REGION

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


def public_config() -> dict:
    """フロントがログインURLを組むための公開設定(シークレットは含まない)。"""
    return {
        "enabled": AUTH_ENABLED,
        "domain": COGNITO_DOMAIN,
        "client_id": COGNITO_CLIENT_ID,
        "region": COGNITO_REGION,
    }
