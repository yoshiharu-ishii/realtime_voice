"""チャット履歴 (SQLite)。会話の文字起こしと検索クエリをセッション単位で保存する。"""

import sqlite3
from datetime import datetime

from config import DB_PATH


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
        # 既存DBへの persona 列の後付けマイグレーション
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
        if "persona" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN persona TEXT NOT NULL DEFAULT ''")


def save_message(session_id: str, role: str, text: str, persona: str = "") -> None:
    if not text.strip():
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, text, created_at, persona)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, datetime.now().isoformat(timespec="seconds"), persona),
        )


def list_history(limit: int = 30) -> list:
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
                "SELECT role, text, created_at, persona FROM messages"
                " WHERE session_id = ? ORDER BY id",
                (sid,),
            ).fetchall()
            out.append(
                {
                    "session_id": sid,
                    "started_at": rows[0]["created_at"],
                    "persona": rows[0]["persona"],
                    "messages": [
                        {"role": r["role"], "text": r["text"], "created_at": r["created_at"]}
                        for r in rows
                    ],
                }
            )
    return out
