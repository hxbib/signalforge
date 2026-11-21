"""Run storage: one SQLite file. A run is the JSON document of docs/CONTRACT.md section 4 plus its cleaned image."""
import json
import sqlite3
import threading
import time

from app.config import DB_PATH

_lock = threading.Lock()
_ready = False


def _connect() -> sqlite3.Connection:
    global _ready
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    if not _ready:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, session TEXT NOT NULL, doc TEXT NOT NULL,"
            " image BLOB NOT NULL, updated_at INTEGER NOT NULL)"
        )
        conn.commit()
        _ready = True
    return conn


def create_run(run: dict, session: str, image: bytes) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, session, doc, image, updated_at) VALUES (?,?,?,?,?)",
            (run["run_id"], session, json.dumps(run), image, int(time.time())),
        )


def get_run(run_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT doc FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save_run(run: dict) -> None:
    run["updated_at"] = int(time.time())
    with _lock, _connect() as conn:
        conn.execute("UPDATE runs SET doc=?, updated_at=? WHERE run_id=?", (json.dumps(run), run["updated_at"], run["run_id"]))


def get_image(run_id: str) -> bytes | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT image FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return bytes(row[0]) if row else None


def owner(run_id: str) -> str | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT session FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return row[0] if row else None


def active_runs(session: str | None = None) -> int:
    """Runs still working, started in the last two minutes (older ones are treated as abandoned)."""
    since = int(time.time()) - 120
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT session, doc FROM runs WHERE updated_at>=?", (since,)).fetchall()
    count = 0
    for owner_session, doc in rows:
        if session is not None and owner_session != session:
            continue
        if json.loads(doc).get("state") in ("reading", "searching", "verifying"):
            count += 1
    return count
