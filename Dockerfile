# uv入りのPython 3.12スリムイメージ。uv.lockどおりに依存を再現する
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# 1) 依存だけ先にインストールしてレイヤーキャッシュを効かせる
#    (アプリのコードを変えても依存の再インストールは走らない)
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) アプリ本体。コンテナ内のパス関係はリポジトリと同じにする
#    (config.py が backend/ の親の frontend/ を静的配信の起点として参照するため)
#    .env と chat_history.db は .dockerignore で除外(秘密と状態はイメージに焼かない)
COPY backend/ ./
COPY frontend/ /app/frontend/

# 3) 会話履歴DBはボリューム前提の /data に置く(composeやECSでマウントを当てる)
ENV DB_PATH=/data/chat_history.db
RUN mkdir -p /data

EXPOSE 8000
# uv syncで作った .venv を直接使う(コンテナ内で再解決はしない)
CMD ["/app/backend/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
