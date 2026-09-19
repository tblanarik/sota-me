"""SQLite database setup and helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import config


def get_connection(path: Path | str = config.DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS summits (
      summit_ref   TEXT PRIMARY KEY,
      name         TEXT,
      region       TEXT,
      lat          REAL,
      lon          REAL,
      alt_m        REAL,
      dem_alt_m    REAL,
      points       INTEGER
    );

    CREATE TABLE IF NOT EXISTS grids (
      grid6        TEXT PRIMARY KEY,
      center_lat   REAL,
      center_lon   REAL,
      elev_max_m   REAL,
      max_lat      REAL,
      max_lon      REAL,
      elev_mean_m  REAL
    );

    CREATE TABLE IF NOT EXISTS los (
      grid6          TEXT NOT NULL,
      summit_ref     TEXT NOT NULL,
      margin_max_m   REAL,
      margin_mean_m  REAL,
      distance_km    REAL,
      bearing_deg    REAL,
      PRIMARY KEY (grid6, summit_ref)
    ) WITHOUT ROWID;

    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None
