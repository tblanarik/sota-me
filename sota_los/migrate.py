"""Convert a legacy text-keyed database to the compact integer schema.

The legacy `los` table keyed rows on ('CN86mx', 'W7W/MC-001') and stored four
REAL measures — ~55 bytes per row across ~11.8M rows. This rewrites it against
dense integer IDs with quantised integer measures (~18 bytes/row), which is a
~66% reduction with every pair retained.

Usage:
    python -m sota_los.migrate [--src data/sota_los.sqlite] [--dst PATH] [--replace]

Writes a new file and leaves the source untouched unless --replace is given.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

import config
from sota_los.db import create_schema


def is_legacy(path: Path) -> bool:
    """True if `path` uses the old text-keyed los schema."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(los)")}
    finally:
        conn.close()
    if not cols:
        raise ValueError(f"{path} has no `los` table")
    return "grid6" in cols


def migrate(src: Path, dst: Path) -> None:
    if dst.exists():
        raise FileExistsError(f"{dst} already exists — remove it or pick another --dst")

    t0 = time.time()
    conn = sqlite3.connect(dst)
    # Bulk load into a fresh file: no journal needed, nothing to roll back to.
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-200000")
    create_schema(conn)
    conn.execute("ATTACH DATABASE ? AS src", (str(src),))

    print("Assigning dense IDs to summits and grids …")
    conn.execute("""
        INSERT INTO summits (summit_id, summit_ref, name, region, lat, lon, alt_m, dem_alt_m, points)
        SELECT ROW_NUMBER() OVER (ORDER BY summit_ref) - 1,
               summit_ref, name, region, lat, lon, alt_m, dem_alt_m, points
        FROM src.summits
    """)
    conn.execute("""
        INSERT INTO grids (grid_id, grid6, center_lat, center_lon, elev_max_m, max_lat, max_lon, elev_mean_m)
        SELECT ROW_NUMBER() OVER (ORDER BY grid6) - 1,
               grid6, center_lat, center_lon, elev_max_m, max_lat, max_lon, elev_mean_m
        FROM src.grids
    """)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) SELECT key, value FROM src.meta")
    conn.commit()

    n_sum = conn.execute("SELECT COUNT(*) FROM summits").fetchone()[0]
    n_grid = conn.execute("SELECT COUNT(*) FROM grids").fetchone()[0]
    print(f"  {n_sum:,} summits, {n_grid:,} grids")

    clamp = config.MARGIN_CLAMP_M
    print(f"Rewriting los rows (margins clamped to ±{clamp:,} m) …")
    t1 = time.time()
    conn.execute(f"""
        INSERT INTO los (grid_id, summit_id, margin_max_m, margin_mean_m, distance_hm, bearing_deg)
        SELECT g.grid_id,
               s.summit_id,
               CAST(MAX(-{clamp}, MIN({clamp}, ROUND(l.margin_max_m)))  AS INTEGER),
               CAST(MAX(-{clamp}, MIN({clamp}, ROUND(l.margin_mean_m))) AS INTEGER),
               CAST(ROUND(l.distance_km * 10) AS INTEGER),
               CAST(ROUND(l.bearing_deg)      AS INTEGER)
        FROM src.los l
        JOIN grids   g ON g.grid6      = l.grid6
        JOIN summits s ON s.summit_ref = l.summit_ref
        ORDER BY g.grid_id, s.summit_id
    """)
    conn.commit()
    n_los = conn.execute("SELECT COUNT(*) FROM los").fetchone()[0]
    print(f"  {n_los:,} rows in {time.time()-t1:.1f}s")

    print("Building index …")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_los_summit ON los (summit_id)")
    conn.execute("ANALYZE")
    conn.commit()

    src_n = sqlite3.connect(f"file:{src}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM los").fetchone()[0]
    conn.close()

    if n_los != src_n:
        raise RuntimeError(f"row count mismatch: source {src_n:,}, migrated {n_los:,}")

    src_mb = src.stat().st_size / 1e6
    dst_mb = dst.stat().st_size / 1e6
    print(f"\nsource : {src_mb:>8,.0f} MB")
    print(f"compact: {dst_mb:>8,.0f} MB   ({100 * (1 - dst_mb / src_mb):.0f}% smaller)")
    print(f"done in {time.time()-t0:.1f}s")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--src", type=Path, default=Path(config.DB_PATH))
    parser.add_argument("--dst", type=Path, default=None,
                        help="Output path (default: <src>.compact)")
    parser.add_argument("--replace", action="store_true",
                        help="On success, move the source to <src>.legacy and take its place")
    args = parser.parse_args(argv)

    src = args.src
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        sys.exit(1)

    if not is_legacy(src):
        print(f"{src} is already on the compact schema — nothing to do.")
        return

    dst = args.dst or src.with_suffix(src.suffix + ".compact")
    migrate(src, dst)

    if args.replace:
        legacy = src.with_suffix(src.suffix + ".legacy")
        os.replace(src, legacy)
        os.replace(dst, src)
        print(f"\n{src} now holds the compact database.")
        print(f"Original preserved at {legacy} — delete it once you are happy.")


if __name__ == "__main__":
    main()
