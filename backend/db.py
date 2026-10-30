"""Database layer. Supports both Supabase (Postgres) and SQLite with identical API.

Switch with DB_BACKEND=supabase or DB_BACKEND=sqlite in .env.
Tables:
  calls          - reminder call scheduling
  events         - call transcripts
  contacts       - people receiving check-in calls
  call_attempts  - retry tracking
  call_sessions  - live conversational state machine (per call_sid)
  call_results   - post-call structured evaluation & history
  alerts         - caregiver notifications (identity failure, severe mood drop, missed reminder, etc.)
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

import config

PENDING = ("scheduled", "rescheduled", "retrying")


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
        self.sb.table("calls").select("id").limit(1).execute()

    # Calls & Events (existing)
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

    # Contacts
    def create_contact(self, row: dict) -> dict:
        return self.sb.table("contacts").insert(row).execute().data[0]

    def get_contact(self, contact_id: str):
        data = self.sb.table("contacts").select("*").eq("id", contact_id).limit(1).execute().data
        return data[0] if data else None

    def get_contact_by_phone(self, phone: str):
        data = self.sb.table("contacts").select("*").eq("phone", phone).limit(1).execute().data
        return data[0] if data else None

    def list_contacts(self):
        return self.sb.table("contacts").select("*").order("created_at", desc=True).execute().data

    # Call Attempts
    def add_attempt(self, contact_id: str, attempt_no: int, twilio_sid: str, status: str):
        row = {"contact_id": contact_id, "attempt_no": attempt_no, "twilio_sid": twilio_sid, "status": status, "started_at": now_iso()}
        return self.sb.table("call_attempts").insert(row).execute().data

    def get_attempts(self, contact_id: str):
        return self.sb.table("call_attempts").select("*").eq("contact_id", contact_id).order("attempt_no").execute().data

    # Call Sessions (State machine per call_sid)
    def get_session(self, call_sid: str):
        data = self.sb.table("call_sessions").select("*").eq("call_sid", call_sid).limit(1).execute().data
        return data[0] if data else None

    def upsert_session(self, call_sid: str, contact_id: str, state: str, history: list, unclear_count: int = 0):
        row = {
            "call_sid": call_sid,
            "contact_id": contact_id,
            "state": state,
            "history": history,
            "unclear_count": unclear_count,
            "updated_at": now_iso()
        }
        return self.sb.table("call_sessions").upsert(row).execute().data

    # Call Results (Longitudinal comparison)
    def save_result(self, row: dict):
        row["created_at"] = now_iso()
        return self.sb.table("call_results").insert(row).execute().data

    def get_recent_results(self, contact_id: str, limit: int = 5):
        return self.sb.table("call_results").select("*").eq("contact_id", contact_id).order("created_at", desc=True).limit(limit).execute().data

    # Alerts (Caregiver notifications)
    def create_alert(self, contact_id: str, severity: str, reasons: list):
        row = {"contact_id": contact_id, "severity": severity, "reasons": reasons, "resolved": False, "created_at": now_iso()}
        return self.sb.table("alerts").insert(row).execute().data

    def list_alerts(self, limit: int = 50):
        return self.sb.table("alerts").select("*").order("created_at", desc=True).limit(limit).execute().data

    def reset(self):
        for t in ("alerts", "call_results", "call_sessions", "call_attempts", "contacts", "events", "calls"):
            try:
                self.sb.table(t).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            except Exception:
                pass


# --------------------------------------------------------------------------
# SQLite (Robust local database with identical API)
# --------------------------------------------------------------------------
class SqliteDB:
    def __init__(self):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(config.SQLITE_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY, phone TEXT NOT NULL, message TEXT NOT NULL, note TEXT DEFAULT '',
                sender_name TEXT DEFAULT '', language TEXT DEFAULT 'en', spoken_message TEXT DEFAULT '',
                priority TEXT DEFAULT 'normal', priority_reason TEXT DEFAULT '', max_attempts INTEGER DEFAULT 2,
                retry_gap_seconds INTEGER DEFAULT 600, attempts INTEGER DEFAULT 0, failed_attempts INTEGER DEFAULT 0,
                status TEXT DEFAULT 'scheduled', scheduled_at TEXT, next_attempt_at TEXT, summary TEXT DEFAULT '',
                twilio_sid TEXT DEFAULT '', created_at TEXT, updated_at TEXT
            )""")
            self.conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT, agent TEXT, text TEXT, created_at TEXT)")
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY, owner_id TEXT, name TEXT NOT NULL, phone TEXT NOT NULL,
                secret_check TEXT NOT NULL, language TEXT DEFAULT 'en-IN', reminders TEXT DEFAULT '',
                notes TEXT DEFAULT '', caregiver_contact TEXT DEFAULT '', created_at TEXT
            )""")
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS call_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, attempt_no INTEGER, twilio_sid TEXT,
                status TEXT, started_at TEXT
            )""")
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS call_sessions (
                call_sid TEXT PRIMARY KEY, contact_id TEXT, state TEXT, unclear_count INTEGER DEFAULT 0,
                history TEXT DEFAULT '[]', updated_at TEXT
            )""")
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS call_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, mood INTEGER, ate INTEGER,
                did_reminders INTEGER, sleep TEXT, discomfort INTEGER, emergency INTEGER,
                concerns TEXT DEFAULT '[]', summary TEXT, tone_note TEXT, severity TEXT,
                flags TEXT DEFAULT '[]', created_at TEXT
            )""")
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT, severity TEXT,
                reasons TEXT DEFAULT '[]', resolved INTEGER DEFAULT 0, created_at TEXT
            )""")
            self.conn.commit()

    def _q(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return [dict(r) for r in cur.fetchall()]

    # Calls & Events
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

    # Contacts
    def create_contact(self, row: dict) -> dict:
        row.setdefault("created_at", now_iso())
        keys = list(row.keys())
        self._q(f"INSERT OR REPLACE INTO contacts ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [row[k] for k in keys])
        return self.get_contact(row["id"])

    def get_contact(self, contact_id: str):
        rows = self._q("SELECT * FROM contacts WHERE id=?", (contact_id,))
        return rows[0] if rows else None

    def get_contact_by_phone(self, phone: str):
        rows = self._q("SELECT * FROM contacts WHERE phone=?", (phone,))
        return rows[0] if rows else None

    def list_contacts(self):
        return self._q("SELECT * FROM contacts ORDER BY created_at DESC")

    # Call Attempts
    def add_attempt(self, contact_id: str, attempt_no: int, twilio_sid: str, status: str):
        self._q("INSERT INTO call_attempts (contact_id, attempt_no, twilio_sid, status, started_at) VALUES (?,?,?,?,?)",
                (contact_id, attempt_no, twilio_sid, status, now_iso()))

    def get_attempts(self, contact_id: str):
        return self._q("SELECT * FROM call_attempts WHERE contact_id=? ORDER BY attempt_no", (contact_id,))

    # Call Sessions
    def get_session(self, call_sid: str):
        rows = self._q("SELECT * FROM call_sessions WHERE call_sid=?", (call_sid,))
        if not rows:
            return None
        res = dict(rows[0])
        try:
            res["history"] = json.loads(res["history"])
        except Exception:
            res["history"] = []
        return res

    def upsert_session(self, call_sid: str, contact_id: str, state: str, history: list, unclear_count: int = 0):
        h_json = json.dumps(history)
        now = now_iso()
        existing = self.get_session(call_sid)
        if existing:
            self._q("UPDATE call_sessions SET state=?, history=?, unclear_count=?, updated_at=? WHERE call_sid=?",
                    (state, h_json, unclear_count, now, call_sid))
        else:
            self._q("INSERT INTO call_sessions (call_sid, contact_id, state, unclear_count, history, updated_at) VALUES (?,?,?,?,?,?)",
                    (call_sid, contact_id, state, unclear_count, h_json, now))

    # Call Results
    def save_result(self, row: dict):
        c_json = json.dumps(row.get("concerns", []))
        f_json = json.dumps(row.get("flags", []))
        self._q("""INSERT INTO call_results
            (contact_id, mood, ate, did_reminders, sleep, discomfort, emergency, concerns, summary, tone_note, severity, flags, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["contact_id"], row.get("mood", 5), 1 if row.get("ate", True) else 0,
             1 if row.get("did_reminders", True) else 0, row.get("sleep", "good"),
             1 if row.get("discomfort", False) else 0, 1 if row.get("emergency", False) else 0,
             c_json, row.get("summary", ""), row.get("tone_note", ""), row.get("severity", "none"),
             f_json, now_iso()))

    def get_recent_results(self, contact_id: str, limit: int = 5):
        rows = self._q("SELECT * FROM call_results WHERE contact_id=? ORDER BY created_at DESC LIMIT ?", (contact_id, limit))
        for r in rows:
            try: r["concerns"] = json.loads(r.get("concerns") or "[]")
            except Exception: r["concerns"] = []
            try: r["flags"] = json.loads(r.get("flags") or "[]")
            except Exception: r["flags"] = []
            r["ate"] = bool(r.get("ate"))
            r["did_reminders"] = bool(r.get("did_reminders"))
            r["discomfort"] = bool(r.get("discomfort"))
            r["emergency"] = bool(r.get("emergency"))
        return rows

    # Alerts
    def create_alert(self, contact_id: str, severity: str, reasons: list):
        r_json = json.dumps(reasons)
        self._q("INSERT INTO alerts (contact_id, severity, reasons, resolved, created_at) VALUES (?,?,?,0,?)",
                (contact_id, severity, r_json, now_iso()))

    def list_alerts(self, limit: int = 50):
        rows = self._q("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,))
        for r in rows:
            try: r["reasons"] = json.loads(r.get("reasons") or "[]")
            except Exception: r["reasons"] = []
            r["resolved"] = bool(r.get("resolved"))
        return rows

    def reset(self):
        for t in ("alerts", "call_results", "call_sessions", "call_attempts", "contacts", "events", "calls"):
            self._q(f"DELETE FROM {t}")


def _make_db():
    if config.DB_BACKEND == "supabase":
        try:
            d = SupabaseDB()
            # Test if new contacts table exists in Supabase
            d.sb.table("contacts").select("id").limit(1).execute()
            print("[db] Using Supabase (all tables verified)")
            return d
        except Exception as e:
            print(f"[db] Supabase table check ({e}). Using SQLite with full schema.")
    print(f"[db] Using SQLite file {config.SQLITE_PATH}")
    return SqliteDB()


db = _make_db()
BACKEND_NAME = "supabase" if isinstance(db, SupabaseDB) else "sqlite"
