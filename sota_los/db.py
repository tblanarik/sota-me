"""SQLite database setup and helpers.

The `los` table holds one row per (grid, summit) pair — ~11.8M rows for W7W.
At that scale the row encoding dominates file size, so it is tuned two ways:

  * Grid and summit are referenced by dense integer IDs rather than their text
    keys ('CN86mx', 'W7W/MC-001'), which would cost 18 bytes on every row.
  * Measures are stored as quantised INTEGERs, not REALs. SQLite's varint
    encoding then spends 1 byte on near-zero margins and 2 on deep ones, which
    puts the precision where the clear/marginal/blocked decision happens and
    costs almost nothing on paths that are hopelessly blocked either way.

Together these take the row from ~55 bytes to ~18.
"""

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
      summit_id    INTEGER PRIMARY KEY,
      summit_ref   TEXT NOT NULL UNIQUE,
      name         TEXT,
      region       TEXT,
      lat          REAL,
      lon          REAL,
      alt_m        REAL,
      dem_alt_m    REAL,
      points       INTEGER
    );

    CREATE TABLE IF NOT EXISTS grids (
      grid_id      INTEGER PRIMARY KEY,
      grid6        TEXT NOT NULL UNIQUE,
      center_lat   REAL,
      center_lon   REAL,
      elev_max_m   REAL,
      max_lat      REAL,
      max_lon      REAL,
      elev_mean_m  REAL
    );

    CREATE TABLE IF NOT EXISTS los (
      grid_id       INTEGER NOT NULL,
      summit_id     INTEGER NOT NULL,
      margin_max_m  INTEGER,   -- whole metres, clamped to ±config.MARGIN_CLAMP_M
      margin_mean_m INTEGER,   -- whole metres, same clamp
      distance_hm   INTEGER,   -- hectometres (0.1 km); divide by 10 for km
      bearing_deg   INTEGER,   -- whole degrees, 0–359
      PRIMARY KEY (grid_id, summit_id)
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
