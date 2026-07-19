"""OpenAIセッション設定(両プロトコル共通)。

WebSocket中継(transport_ws)では session.update に包んで送り、
WebRTC直結(transport_webrtc)では一時キー(client_secrets)発行時に埋め込む。
「モデルに何をさせるか」はここで一元管理し、トランスポートは運び方だけを持つ。
"""

from config import TRANSCRIBE_MODEL
from search import WEB_SEARCH_TOOL


def build_session_config(persona: dict, mode: str = "ptt") -> dict:
    """セッション設定。

    会話モード:
      ptt — サーバーVADを無効化し、手動commitで発話を区切る(押して話す)
      vad — サーバーVADが発話の始終を検知し、自動でcommit+応答する(ハンズフリー通話)
    """
    if mode == "vad":
        # 発話の区切り検知・自動応答・割り込み(バージイン)はOpenAI側が担う。
        # server_vad(無音500msで機械的に区切る)だと息継ぎで文が分割されて
        # 応答が二重に出るため、文の完結を意味で判断する semantic_vad を使う
        turn_detection = {"type": "semantic_vad"}
    else:
        turn_detection = None
    return {
        "type": "realtime",
        "output_modalities": ["audio"],
        "instructions": persona["instructions"],
        "tools": [WEB_SEARCH_TOOL],
        "tool_choice": "auto",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                # OpenAI側のノイズリダクション。VADと文字起こしの手前で入力を掃除し、
                # 衝撃音(机を叩く・マイク接触など)を発話と誤検知するのを抑える。
                # near_field は口元マイク(ヘッドセット・ノートPC)向けのプロファイル
                "noise_reduction": {"type": "near_field"},
                "turn_detection": turn_detection,
                "transcription": {"model": TRANSCRIBE_MODEL, "language": "ja"},
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "voice": persona["voice"],
            },
        },
    }


def session_update_event(persona: dict, mode: str = "ptt") -> dict:
    return {"type": "session.update", "session": build_session_config(persona, mode)}
