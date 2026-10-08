import hashlib
import json
import sqlite3
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def masked(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


class History:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY, received_at TEXT NOT NULL,
                scope TEXT NOT NULL, chat_hash TEXT NOT NULL, message_hash TEXT NOT NULL,
                user_hash TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
                material_id TEXT, image_path TEXT, fallback_reason TEXT, error_type TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, elapsed_seconds REAL,
                sent_message_hash TEXT, finished_at TEXT,
                UNIQUE(scope, chat_hash, message_hash)
            );
            CREATE TABLE IF NOT EXISTS ai_usage (
                id INTEGER PRIMARY KEY, conversation_id INTEGER,
                called_at TEXT NOT NULL, model TEXT NOT NULL, candidate_count INTEGER NOT NULL,
                elapsed_seconds REAL NOT NULL, usage_json TEXT NOT NULL,
                selected_id TEXT, scene TEXT, reason TEXT, error_type TEXT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            );
        """)
        self.db.commit()

    def recover(self):
        # A process crash must not cause the same incoming event to be resent.
        self.db.execute("UPDATE conversations SET status='interrupted', finished_at=? WHERE status='processing'", (now(),))
        self.db.commit()

    def claim(self, event, content):
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO conversations(received_at,scope,chat_hash,message_hash,user_hash,content,status) "
            "VALUES(?,?,?,?,?,?,'processing')",
            (now(), event.chat_scope, masked(event.chat_id), masked(event.message_id), masked(event.user_id), content),
        )
        self.db.commit()
        return cursor.lastrowid if cursor.rowcount else None

    def finish(self, conversation_id, **fields):
        fields["finished_at"] = None if fields.get("status") == "processing" else now()
        self.db.execute(f"UPDATE conversations SET {','.join(k + '=?' for k in fields)} WHERE id=?",
                        (*fields.values(), conversation_id))
        self.db.commit()

    def usage(self, conversation_id, selection):
        self.db.execute(
            "INSERT INTO ai_usage(conversation_id,called_at,model,candidate_count,elapsed_seconds,usage_json,"
            "selected_id,scene,reason,error_type) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (conversation_id, selection.called_at, selection.model, selection.candidate_count,
             selection.elapsed_seconds, json.dumps(selection.usage), selection.material_id,
             selection.scene, selection.reason, selection.error_type),
        )
        self.db.commit()

    def recent_replies(self, event):
        rows = self.db.execute(
            "SELECT c.content AS message, c.material_id, c.fallback_reason, a.scene, a.reason "
            "FROM conversations c LEFT JOIN ai_usage a ON a.id = "
            "(SELECT MAX(id) FROM ai_usage WHERE conversation_id=c.id) "
            "WHERE c.scope=? AND c.chat_hash=? AND c.status='sent' "
            "ORDER BY c.finished_at DESC, c.id DESC LIMIT 10",
            (event.chat_scope, masked(event.chat_id)),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def close(self):
        self.db.close()
