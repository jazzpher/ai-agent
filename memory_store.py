"""Durable memory backend: Neon (any Postgres) when DATABASE_URL is set, else the local file.

The file stays as a cache and as the fallback, so memory never breaks if the database is
unreachable. The database is the source of truth when it answers. Never logs the URL.
"""
import os

TABLE = "agent_memory"
_ready = False


def db_url() -> str:
    return (os.environ.get("AGENT_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()


def enabled() -> bool:
    return bool(db_url())


def _connect():
    import psycopg
    return psycopg.connect(db_url(), connect_timeout=8, autocommit=True)


def _ensure(conn):
    global _ready
    if _ready:
        return
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} ("
                 "pos integer PRIMARY KEY, line text NOT NULL, updated_at timestamptz DEFAULT now())")
    _ready = True


def db_read():
    """Lines from the database, or None when the database is off or unreachable."""
    if not enabled():
        return None
    try:
        with _connect() as conn:
            _ensure(conn)
            rows = conn.execute(f"SELECT line FROM {TABLE} ORDER BY pos").fetchall()
        return [r[0] for r in rows]
    except Exception:
        return None


def db_write(lines) -> bool:
    if not enabled():
        return False
    try:
        with _connect() as conn:
            _ensure(conn)
            with conn.transaction():
                conn.execute(f"DELETE FROM {TABLE}")
                for i, ln in enumerate(lines):
                    conn.execute(f"INSERT INTO {TABLE} (pos, line) VALUES (%s, %s)", (i, ln))
        return True
    except Exception:
        return False


def status() -> str:
    if not enabled():
        return "file only (no DATABASE_URL)"
    lines = db_read()
    return "database unreachable, using file" if lines is None else f"database ok ({len(lines)} lines)"
