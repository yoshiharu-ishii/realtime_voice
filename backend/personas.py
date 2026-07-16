"""ペルソナ(キャラ設定+声)。personas/*.md を毎回読み込む。"""

import re

from config import PERSONA_DIR, VOICE

# どのペルソナにも必ず付く共通ルール(検索の使用と作話防止)
COMMON_RULES = (
    "\n\n【共通ルール】応答は日本語で、音声向けに簡潔にすること。"
    "最近の出来事・人物・統計・ニュースなど、学習データにない可能性がある事実を"
    "聞かれたら、必ず web_search ツールで調べてから答えること。"
    "よく知らない・確信の持てない人名や固有名詞が出てきたら、意見や比較を"
    "求められた場合でも、答える前に必ず web_search で実在と最新情報を確認すること。"
    "知らない人物を似た名前の別人と同一視したり、本名・別名の関係を"
    "検索の裏付けなしに断定したりしないこと。"
    "調べても分からないことは、推測で断定せず正直に「分からない」と言うこと。"
    "発話が不明瞭で意味が取れない場合は、聞こえたことにして質問を創作したり"
    "話を広げたりせず、「聞き取れなかったので、もう一度お願いします」と正直に聞き返すこと。"
    "あなたは音声しか受け取れず、画面・画像・映像は一切見えていない。"
    "「これどう?」のような文脈のない指示語だけの発話に対して、見えていないものを"
    "見えたことにして感想を述べず、何を指しているのかを確認すること。"
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


def list_personas() -> list:
    """ペルソナ一覧(default先頭、あとはファイル名順)。"""
    items = []
    if PERSONA_DIR.is_dir():
        for path in sorted(PERSONA_DIR.glob("*.md")):
            p = parse_persona(path.stem, path.read_text(encoding="utf-8"))
            items.append({"id": p["id"], "name": p["name"]})
    if not items:
        items.append({"id": "default", "name": FALLBACK_PERSONA["name"]})
    items.sort(key=lambda x: x["id"] != "default")  # defaultを先頭に
    return items
