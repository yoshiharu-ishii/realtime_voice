"""web_searchツール。

Realtimeモデルは検索できないので関数として「求人票」だけ公開し、
実行はこのサーバーが OpenAI Responses API のホスト型Web検索へ橋渡しする。
"""

import re

import httpx

from config import SEARCH_MODEL

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
