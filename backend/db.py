"""Database layer. Same functions for Supabase (main) and SQLite (fallback).

Switch with DB_BACKEND=supabase or DB_BACKEND=sqlite in .env.
Tables:
  calls  - one row per reminder (status, priority, retry plan, summary ...)
  events - the transcript: one row per line, tagged with the agent that acted
"""
import sqlite3
import threading
from datetime import datetime, timezone

import config

PENDING = ("scheduled", "rescheduled", "retrying")

CALL_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "phone": "TEXT NOT NULL",
    "message": "TEXT NOT NULL",
    "note": "TEXT DEFAULT ''",
    "sender_name": "TEXT DEFAULT ''",
    "language": "TEXT DEFAULT 'en'",
    "spoken_message": "TEXT DEFAULT ''",
    "priority": "TEXT DEFAULT 'normal'",
    "priority_reason": "TEXT DEFAULT ''",
    "max_attempts": "INTEGER DEFAULT 2",
    "retry_gap_seconds": "INTEGER DEFAULT 600",
    "attempts": "INTEGER DEFAULT 0",
    "failed_attempts": "INTEGER DEFAULT 0",
    "status": "TEXT DEFAULT 'scheduled'",
    "scheduled_at": "TEXT",
    "next_attempt_at": "TEXT",
    "summary": "TEXT DEFAULT ''",
    "twilio_sid": "TEXT DEFAULT ''",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Supabase
# --------------------------------------------------------------------------
class SupabaseDB:
    def __init__(self):
        from supabase import create_client

        if not config.SUPABASE_URL or not config.SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY missing in .env")
        self.sb = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        self.sb.table("calls").select("id").limit(1).execute()  # fail fast if tables are missing

    def insert_call(self, row: dict) -> dict:
        return self.sb.table("calls").insert(row).execute().data[0]

    def get_call(self, call_id: str):
        data = self.sb.table("calls").select("*").eq("id", call_id).limit(1).execute().data
        return data[0] if data else None

    def update_call(self, call_id: str, **fields):
        fields["updated_at"] = now_iso()
        self.sb.table("calls").update(fields).eq("id", call_id).execute()

    def list_calls(self):
        return self.sb.table("calls").select("*").order("created_at", desc=True).limit(50).execute().data

    def list_pending(self):
        return self.sb.table("calls").select("*").in_("status", list(PENDING)).execute().data

    def add_event(self, call_id: str, agent: str, text: str):
        self.sb.table("events").insert(
            {"call_id": call_id, "agent": agent, "text": text, "created_at": now_iso()}
        ).execute()

    def list_events(self, call_ids):
        if not call_ids:
            return []
        return self.sb.table("events").select("*").in_("call_id", call_ids).order("id").execute().data

    def reset(self):
        self.sb.table("events").delete().gte("id", 0).execute()
        self.sb.table("calls").delete().gte("created_at", "1970-01-01").execute()


# --------------------------------------------------------------------------
# SQLite (fallback)
# --------------------------------------------------------------------------
class SqliteDB:
    def __init__(self):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(config.SQLITE_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        cols = ", ".join(f"{k} {v}" for k, v in CALL_COLUMNS.items())
        with self.lock:
            self.conn.execute(f"CREATE TABLE IF NOT EXISTS calls ({cols})")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "call_id TEXT, agent TEXT, text TEXT, created_at TEXT)"
            )
            self.conn.commit()

    def _q(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return [dict(r) for r in cur.fetchall()]

    def insert_call(self, row: dict) -> dict:
        keys = list(row.keys())
        self._q(f"INSERT INTO calls ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [row[k] for k in keys])
        return self.get_call(row["id"])

    def get_call(self, call_id: str):
        rows = self._q("SELECT * FROM calls WHERE id=?", (call_id,))
        return rows[0] if rows else None

    def update_call(self, call_id: str, **fields):
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        self._q(f"UPDATE calls SET {sets} WHERE id=?", [*fields.values(), call_id])

    def list_calls(self):
        return self._q("SELECT * FROM calls ORDER BY created_at DESC LIMIT 50")

    def list_pending(self):
        return self._q(f"SELECT * FROM calls WHERE status IN ({','.join('?' * len(PENDING))})", PENDING)

    def add_event(self, call_id: str, agent: str, text: str):
        self._q("INSERT INTO events (call_id, agent, text, created_at) VALUES (?,?,?,?)", (call_id, agent, text, now_iso()))

    def list_events(self, call_ids):
        if not call_ids:
            return []
        return self._q(f"SELECT * FROM events WHERE call_id IN ({','.join('?' * len(call_ids))}) ORDER BY id", call_ids)

    def reset(self):
        self._q("DELETE FROM events")
        self._q("DELETE FROM calls")


def _make_db():
    if config.DB_BACKEND == "supabase":
        try:
            d = SupabaseDB()
            print("[db] Using Supabase")
            return d
        except Exception as e:
            print(f"[db] Supabase not working ({e}). Falling back to SQLite.")
    print(f"[db] Using SQLite file {config.SQLITE_PATH}")
    return SqliteDB()


db = _make_db()
BACKEND_NAME = "supabase" if isinstance(db, SupabaseDB) else "sqlite"
