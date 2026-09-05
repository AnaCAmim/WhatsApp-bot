import sqlite3
from pathlib import Path

from bot.models import WhatsAppMessage


DATABASE_PATH = Path("database") / "bot.db"


class MessageRepository:
    def __init__(self, database_path=DATABASE_PATH):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(
            self.database_path,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_key TEXT PRIMARY KEY,
                    source_id TEXT,
                    chat_name TEXT NOT NULL,
                    sender TEXT,
                    content TEXT NOT NULL,
                    raw_metadata TEXT,
                    is_from_me INTEGER NOT NULL,
                    captured_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DETECTED',
                    processed_at TEXT
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_chat
                ON messages(chat_name)
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_captured
                ON messages(captured_at)
                """
            )

    def insert_if_new(self, message: WhatsAppMessage):
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    message_key,
                    source_id,
                    chat_name,
                    sender,
                    content,
                    raw_metadata,
                    is_from_me,
                    captured_at,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_key,
                    message.source_id,
                    message.chat_name,
                    message.sender,
                    message.content,
                    message.raw_metadata,
                    int(message.is_from_me),
                    message.captured_at,
                    "DETECTED",
                ),
            )

            return cursor.rowcount == 1

    def count(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM messages"
            ).fetchone()
            return int(row["total"])

    def list_recent(self, limit=20):
        safe_limit = max(1, int(limit))

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    message_key,
                    source_id,
                    chat_name,
                    sender,
                    content,
                    raw_metadata,
                    is_from_me,
                    captured_at,
                    status,
                    processed_at
                FROM messages
                ORDER BY captured_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

        return [dict(row) for row in rows]
