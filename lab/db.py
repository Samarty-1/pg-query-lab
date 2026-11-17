"""Connection and small helpers shared by every command."""
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DSN = "postgresql://postgres@localhost:5544/stacklab"


def dsn() -> str:
    return os.environ.get("PGLAB_DSN", DEFAULT_DSN)


def connect(autocommit: bool = True) -> psycopg.Connection:
    return psycopg.connect(dsn(), autocommit=autocommit)


def run_sql_file(conn: psycopg.Connection, path: Path) -> None:
    """Run a .sql file that may hold several statements."""
    conn.execute(path.read_text(encoding="utf-8"))  # simple protocol: no parameters


def scalar(conn: psycopg.Connection, sql: str, params=None):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None
