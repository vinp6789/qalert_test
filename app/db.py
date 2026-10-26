"""Database connection + migration runner. Migrations are numbered SQL files."""
from __future__ import annotations
import pathlib
import psycopg
from psycopg.types.json import Json
from .config import settings

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or settings.database_url)


def migrate(conn: psycopg.Connection) -> list[int]:
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_version
                    (version INT PRIMARY KEY,
                     applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_version")}
    done = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        v = int(f.name.split("_")[0])
        if v in applied:
            continue
        conn.execute(f.read_text())
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (%s) ON CONFLICT DO NOTHING", (v,))
        done.append(v)
    conn.commit()
    return done


def jsonb(obj) -> Json:
    return Json(obj)
