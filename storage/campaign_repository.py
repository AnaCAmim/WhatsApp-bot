import sqlite3
import uuid
from datetime import datetime
from pathlib import Path


DATABASE_PATH = Path("database") / "bot.db"


class CampaignRepository:
    def __init__(self, database_path=DATABASE_PATH):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.recover_interrupted()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    text TEXT,
                    media_path TEXT,
                    media_name TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS campaign_recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'QUEUED',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    sent_at TEXT,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );

                CREATE TABLE IF NOT EXISTS queue_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    paused INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT
                );

                INSERT OR IGNORE INTO queue_settings (id, paused, updated_at)
                VALUES (1, 0, NULL);

                CREATE INDEX IF NOT EXISTS idx_campaign_recipients_queue
                ON campaign_recipients(status, campaign_id, id);
                """
            )

    def recover_interrupted(self):
        with self._connect() as connection:
            connection.execute(
                "UPDATE campaign_recipients SET status='QUEUED' WHERE status='SENDING'"
            )
            connection.execute(
                "UPDATE campaigns SET status='QUEUED' WHERE status='RUNNING'"
            )

    def create_campaign(self, message_type, text, contacts, media_path=None, media_name=None):
        campaign_id = uuid.uuid4().hex
        now = datetime.now().isoformat()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO campaigns (
                    id, status, message_type, text, media_path, media_name, created_at
                ) VALUES (?, 'QUEUED', ?, ?, ?, ?, ?)
                """,
                (campaign_id, message_type, text, media_path, media_name, now),
            )

            connection.executemany(
                """
                INSERT INTO campaign_recipients (campaign_id, name, phone, status)
                VALUES (?, ?, ?, 'QUEUED')
                """,
                [(campaign_id, c.name, c.phone) for c in contacts],
            )

        return campaign_id

    def set_media_path(self, campaign_id, media_path):
        with self._connect() as connection:
            connection.execute(
                "UPDATE campaigns SET media_path=? WHERE id=?",
                (media_path, campaign_id),
            )

    # ========================================================
    # FILA GLOBAL
    # ========================================================

    def is_queue_paused(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT paused FROM queue_settings WHERE id=1"
            ).fetchone()
            return bool(row["paused"]) if row else False

    def set_queue_paused(self, paused):
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE queue_settings
                SET paused=?, updated_at=?
                WHERE id=1
                """,
                (1 if paused else 0, datetime.now().isoformat()),
            )
        return self.is_queue_paused()

    def claim_next_recipient(self):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            paused = connection.execute(
                "SELECT paused FROM queue_settings WHERE id=1"
            ).fetchone()
            if paused and paused["paused"]:
                return None

            row = connection.execute(
                """
                SELECT
                    r.id AS recipient_id,
                    r.campaign_id,
                    r.name,
                    r.phone,
                    c.message_type,
                    c.text,
                    c.media_path,
                    c.media_name
                FROM campaign_recipients r
                JOIN campaigns c ON c.id = r.campaign_id
                WHERE r.status='QUEUED'
                  AND c.status IN ('QUEUED', 'RUNNING')
                ORDER BY c.created_at ASC, r.id ASC
                LIMIT 1
                """
            ).fetchone()

            if row is None:
                return None

            now = datetime.now().isoformat()
            connection.execute(
                """
                UPDATE campaign_recipients
                SET status='SENDING', attempts=attempts+1
                WHERE id=? AND status='QUEUED'
                """,
                (row["recipient_id"],),
            )
            connection.execute(
                """
                UPDATE campaigns
                SET status='RUNNING', started_at=COALESCE(started_at, ?), completed_at=NULL
                WHERE id=?
                """,
                (now, row["campaign_id"]),
            )

            return dict(row)

    def get_queue_overview(self, pending_limit=100, failed_limit=100):
        with self._connect() as connection:
            paused_row = connection.execute(
                "SELECT paused, updated_at FROM queue_settings WHERE id=1"
            ).fetchone()

            counts = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status='QUEUED' THEN 1 ELSE 0 END) AS queued,
                    SUM(CASE WHEN status='SENDING' THEN 1 ELSE 0 END) AS sending,
                    SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) AS errors,
                    SUM(CASE WHEN status='SENT' THEN 1 ELSE 0 END) AS sent
                FROM campaign_recipients
                """
            ).fetchone()

            current = connection.execute(
                """
                SELECT
                    r.id,
                    r.campaign_id,
                    r.name,
                    r.phone,
                    r.attempts,
                    c.message_type,
                    c.text,
                    c.media_name,
                    c.created_at
                FROM campaign_recipients r
                JOIN campaigns c ON c.id = r.campaign_id
                WHERE r.status='SENDING'
                ORDER BY r.id ASC
                LIMIT 1
                """
            ).fetchone()

            pending = connection.execute(
                """
                SELECT
                    r.id,
                    r.campaign_id,
                    r.name,
                    r.phone,
                    r.attempts,
                    c.message_type,
                    c.text,
                    c.media_name,
                    c.created_at
                FROM campaign_recipients r
                JOIN campaigns c ON c.id = r.campaign_id
                WHERE r.status='QUEUED'
                  AND c.status IN ('QUEUED', 'RUNNING')
                ORDER BY c.created_at ASC, r.id ASC
                LIMIT ?
                """,
                (pending_limit,),
            ).fetchall()

            failed = connection.execute(
                """
                SELECT
                    r.id,
                    r.campaign_id,
                    r.name,
                    r.phone,
                    r.attempts,
                    r.last_error,
                    c.message_type,
                    c.text,
                    c.media_name,
                    c.created_at
                FROM campaign_recipients r
                JOIN campaigns c ON c.id = r.campaign_id
                WHERE r.status='ERROR'
                ORDER BY r.id DESC
                LIMIT ?
                """,
                (failed_limit,),
            ).fetchall()

            return {
                "paused": bool(paused_row["paused"]) if paused_row else False,
                "paused_updated_at": paused_row["updated_at"] if paused_row else None,
                "counts": {
                    "queued": counts["queued"] or 0,
                    "sending": counts["sending"] or 0,
                    "errors": counts["errors"] or 0,
                    "sent": counts["sent"] or 0,
                },
                "current": dict(current) if current else None,
                "pending": [dict(row) for row in pending],
                "failed": [dict(row) for row in failed],
            }

    def retry_recipient(self, recipient_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT campaign_id, status FROM campaign_recipients WHERE id=?",
                (recipient_id,),
            ).fetchone()
            if row is None or row["status"] != "ERROR":
                return False

            connection.execute(
                """
                UPDATE campaign_recipients
                SET status='QUEUED', last_error=NULL
                WHERE id=?
                """,
                (recipient_id,),
            )
            connection.execute(
                """
                UPDATE campaigns
                SET status='QUEUED', completed_at=NULL
                WHERE id=? AND status IN ('PARTIAL', 'COMPLETED')
                """,
                (row["campaign_id"],),
            )
            return True

    def retry_all_failed(self):
        with self._connect() as connection:
            campaign_ids = [
                row["campaign_id"]
                for row in connection.execute(
                    "SELECT DISTINCT campaign_id FROM campaign_recipients WHERE status='ERROR'"
                ).fetchall()
            ]

            cursor = connection.execute(
                """
                UPDATE campaign_recipients
                SET status='QUEUED', last_error=NULL
                WHERE status='ERROR'
                """
            )

            if campaign_ids:
                placeholders = ",".join("?" for _ in campaign_ids)
                connection.execute(
                    f"""
                    UPDATE campaigns
                    SET status='QUEUED', completed_at=NULL
                    WHERE id IN ({placeholders})
                      AND status IN ('PARTIAL', 'COMPLETED')
                    """,
                    campaign_ids,
                )

            return cursor.rowcount

    def remove_queue_item(self, recipient_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT campaign_id, status FROM campaign_recipients WHERE id=?",
                (recipient_id,),
            ).fetchone()
            if row is None:
                return False, "not_found"
            if row["status"] == "SENDING":
                return False, "sending"
            if row["status"] not in ("QUEUED", "ERROR"):
                return False, "invalid_status"

            connection.execute(
                """
                UPDATE campaign_recipients
                SET status='CANCELLED'
                WHERE id=?
                """,
                (recipient_id,),
            )
            self._finalize_if_done(connection, row["campaign_id"])
            return True, None

    def clear_pending_queue(self):
        with self._connect() as connection:
            campaign_ids = [
                row["campaign_id"]
                for row in connection.execute(
                    "SELECT DISTINCT campaign_id FROM campaign_recipients WHERE status='QUEUED'"
                ).fetchall()
            ]

            cursor = connection.execute(
                "UPDATE campaign_recipients SET status='CANCELLED' WHERE status='QUEUED'"
            )

            for campaign_id in campaign_ids:
                self._finalize_if_done(connection, campaign_id)

            return cursor.rowcount

    # ========================================================
    # RESULTADOS DE ENVIO
    # ========================================================

    def mark_sent(self, recipient_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT campaign_id FROM campaign_recipients WHERE id=?",
                (recipient_id,),
            ).fetchone()
            if row is None:
                return

            connection.execute(
                """
                UPDATE campaign_recipients
                SET status='SENT', sent_at=?, last_error=NULL
                WHERE id=?
                """,
                (datetime.now().isoformat(), recipient_id),
            )
            self._finalize_if_done(connection, row["campaign_id"])

    def mark_error(self, recipient_id, error):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT campaign_id FROM campaign_recipients WHERE id=?",
                (recipient_id,),
            ).fetchone()
            if row is None:
                return

            connection.execute(
                """
                UPDATE campaign_recipients
                SET status='ERROR', last_error=?
                WHERE id=?
                """,
                (str(error)[:1000], recipient_id),
            )
            self._finalize_if_done(connection, row["campaign_id"])

    def _finalize_if_done(self, connection, campaign_id):
        campaign = connection.execute(
            "SELECT status FROM campaigns WHERE id=?",
            (campaign_id,),
        ).fetchone()

        if campaign is None or campaign["status"] == "CANCELLED":
            return

        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN status='QUEUED' THEN 1 ELSE 0 END) AS queued,
                SUM(CASE WHEN status='SENDING' THEN 1 ELSE 0 END) AS sending,
                SUM(CASE WHEN status='SENT' THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) AS errors,
                SUM(CASE WHEN status='CANCELLED' THEN 1 ELSE 0 END) AS cancelled
            FROM campaign_recipients
            WHERE campaign_id=?
            """,
            (campaign_id,),
        ).fetchone()

        if (counts["queued"] or 0) + (counts["sending"] or 0) > 0:
            return

        sent = counts["sent"] or 0
        errors = counts["errors"] or 0
        cancelled = counts["cancelled"] or 0

        if errors > 0:
            status = "PARTIAL"
        elif sent == 0 and cancelled > 0:
            status = "CANCELLED"
        else:
            status = "COMPLETED"

        connection.execute(
            "UPDATE campaigns SET status=?, completed_at=? WHERE id=?",
            (status, datetime.now().isoformat(), campaign_id),
        )

    # ========================================================
    # CAMPANHAS
    # ========================================================

    def cancel_campaign(self, campaign_id):
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM campaigns WHERE id=?",
                (campaign_id,),
            ).fetchone()
            if not exists:
                return False

            connection.execute(
                "UPDATE campaigns SET status='CANCELLED', completed_at=? WHERE id=? AND status IN ('QUEUED','RUNNING')",
                (datetime.now().isoformat(), campaign_id),
            )
            connection.execute(
                "UPDATE campaign_recipients SET status='CANCELLED' WHERE campaign_id=? AND status='QUEUED'",
                (campaign_id,),
            )
            return True

    def get_campaign(self, campaign_id):
        with self._connect() as connection:
            campaign = connection.execute(
                "SELECT * FROM campaigns WHERE id=?",
                (campaign_id,),
            ).fetchone()
            if campaign is None:
                return None

            return self._campaign_payload(connection, campaign)

    def get_latest(self):
        with self._connect() as connection:
            campaign = connection.execute(
                "SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if campaign is None:
                return None

            return self._campaign_payload(connection, campaign)

    def _campaign_payload(self, connection, campaign):
        stats = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='QUEUED' THEN 1 ELSE 0 END) AS queued,
                SUM(CASE WHEN status='SENDING' THEN 1 ELSE 0 END) AS sending,
                SUM(CASE WHEN status='SENT' THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) AS errors,
                SUM(CASE WHEN status='CANCELLED' THEN 1 ELSE 0 END) AS cancelled
            FROM campaign_recipients WHERE campaign_id=?
            """,
            (campaign["id"],),
        ).fetchone()

        errors = connection.execute(
            """
            SELECT name, phone, last_error
            FROM campaign_recipients
            WHERE campaign_id=? AND status='ERROR'
            ORDER BY id DESC LIMIT 10
            """,
            (campaign["id"],),
        ).fetchall()

        return {
            "id": campaign["id"],
            "status": campaign["status"],
            "message_type": campaign["message_type"],
            "text": campaign["text"] or "",
            "media_name": campaign["media_name"],
            "created_at": campaign["created_at"],
            "started_at": campaign["started_at"],
            "completed_at": campaign["completed_at"],
            "total": stats["total"] or 0,
            "queued": stats["queued"] or 0,
            "sending": stats["sending"] or 0,
            "sent": stats["sent"] or 0,
            "errors": stats["errors"] or 0,
            "cancelled": stats["cancelled"] or 0,
            "error_items": [dict(row) for row in errors],
        }
