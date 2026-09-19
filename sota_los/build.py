"""Pipeline entry point.

Usage:
    python -m sota_los.build [--stage STAGE] [--force]

Stages (run in order if no --stage given):
    fetch_summits   Download and cache SOTA summit list
    fetch_dem       Download, mosaic, and reproject Copernicus GLO-90 DEM
    build_grid      Compute per-subsquare elevation statistics
    compute_los     Run viewsheds and populate los table
    build_indexes   Create SQLite indexes and VACUUM
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import config
from sota_los.db import create_schema, get_connection, set_meta


def fetch_summits(conn, force: bool) -> None:
    from sota_los.summits import load_summits
    load_summits(conn, force=force)


def fetch_dem(conn, force: bool) -> None:
    from sota_los.dem import build_dem
    build_dem(conn, force=force)


def build_grid(conn, force: bool) -> None:
    from sota_los.grid import build_grid as _build_grid
    _build_grid(conn, force=force)


def compute_los(conn, force: bool) -> None:
    from sota_los.viewshed import compute_los as _compute_los
    _compute_los(conn, force=force)


def build_indexes(conn, force: bool) -> None:
    print("Building indexes …")
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_los_grid6      ON los (grid6);
    CREATE INDEX IF NOT EXISTS idx_los_summit_ref ON los (summit_ref);
    """)
    print("Running VACUUM and ANALYZE …")
    conn.execute("VACUUM")
    conn.execute("ANALYZE")
    conn.commit()
    print("Done.")


STAGES = {
    "fetch_summits": fetch_summits,
    "fetch_dem": fetch_dem,
    "build_grid": build_grid,
    "compute_los": compute_los,
    "build_indexes": build_indexes,
}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Build the SOTA LOS database")
    parser.add_argument("--stage", choices=list(STAGES.keys()), help="Run only this stage")
    parser.add_argument("--force", action="store_true", help="Recompute even if output exists")
    args = parser.parse_args(argv)

    conn = get_connection()
    create_schema(conn)

    from osgeo import gdal
    gdal.UseExceptions()
    set_meta(conn, "gdal_version", gdal.__version__)

    stages = [args.stage] if args.stage else list(STAGES.keys())
    for name in stages:
        t0 = time.time()
        print(f"\n{'='*60}")
        print(f"Stage: {name}")
        print(f"{'='*60}")
        STAGES[name](conn, args.force)
        print(f"[{name}] done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
