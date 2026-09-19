"""Download and parse the SOTA summits list, populate the summits table."""

from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime
from pathlib import Path

import requests

import config
from sota_los.db import set_meta


def fetch_summits_csv(force: bool = False) -> Path:
    """Download the SOTA summits CSV, cache by date. Returns cache path."""
    raw_dir = Path(config.RAW_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    cache_path = raw_dir / f"summitslist_{today}.csv"

    if cache_path.exists() and not force:
        print(f"Using cached summits: {cache_path}")
        return cache_path

    print(f"Downloading summits list from {config.SUMMITS_URL} …")
    resp = requests.get(config.SUMMITS_URL, allow_redirects=True, timeout=60)
    resp.raise_for_status()
    cache_path.write_bytes(resp.content)
    print(f"Saved {len(resp.content):,} bytes → {cache_path}")
    return cache_path


def load_summits(conn: sqlite3.Connection, force: bool = False) -> int:
    """Download, filter, and insert W7W summits. Returns count inserted."""
    if not force:
        n = conn.execute("SELECT COUNT(*) FROM summits").fetchone()[0]
        if n > 0:
            print(f"Summits already loaded ({n} rows), skipping. Pass --force to reload.")
            return n

    csv_path = fetch_summits_csv(force)
    rows = _parse_csv(csv_path)

    conn.execute("DELETE FROM summits")
    conn.executemany(
        """INSERT INTO summits
           (summit_id, summit_ref, name, region, lat, lon, alt_m, dem_alt_m, points)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(i, *r) for i, r in enumerate(rows)],
    )
    set_meta(conn, "summits_date", date.today().isoformat())
    set_meta(conn, "summits_source", config.SUMMITS_URL)
    conn.commit()
    print(f"Inserted {len(rows):,} {config.SOTA_ASSOCIATION} summits")
    return len(rows)


def _parse_csv(csv_path: Path) -> list[tuple]:
    today = date.today()
    rows = []

    with open(csv_path, encoding="utf-8-sig") as f:
        first_line = f.readline()
        print(f"  CSV title line: {first_line.strip()[:100]}")

        reader = csv.DictReader(f)
        # Print headers so we can verify column names
        print(f"  CSV headers: {reader.fieldnames}")

        for row in reader:
            summit_ref = (row.get("SummitCode") or "").strip()
            if not summit_ref.startswith(f"{config.SOTA_ASSOCIATION}/"):
                continue

            # Filter to currently valid summits
            valid_to_str = (row.get("ValidTo") or "").strip()
            if valid_to_str:
                try:
                    valid_to = datetime.strptime(valid_to_str, "%d/%m/%Y").date()
                    if valid_to < today:
                        continue
                except ValueError:
                    pass  # unparseable date → include

            try:
                lat = float(row.get("Latitude") or 0)
                lon = float(row.get("Longitude") or 0)
                alt_m = float(row.get("AltM") or 0)
                points = int(row.get("Points") or 0)
            except ValueError:
                continue

            rows.append((
                summit_ref,
                (row.get("SummitName") or "").strip(),
                (row.get("RegionName") or "").strip(),
                lat,
                lon,
                alt_m,
                None,   # dem_alt_m filled in by dem stage
                points,
            ))

    return rows
