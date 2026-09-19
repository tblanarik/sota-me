"""Query CLI for the SOTA LOS database.

Usage:
    sota-los CN86mx [--status clear|marginal|all] [--max-km 150]
                    [--sort distance|margin|bearing] [--mean] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
from sota_los.db import get_connection
from sota_los.maidenhead import _normalise


def verdict(margin: float | None, use_mean: bool = False) -> str:
    if margin is None:
        return "blocked"
    if margin >= 0:
        return "clear"
    if margin >= config.MARGINAL_THRESHOLD_M:
        return "marginal"
    return "blocked"


def verdict_emoji(v: str) -> str:
    return {"clear": "✅", "marginal": "⚠️ ", "blocked": "❌"}.get(v, "?")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="sota-los",
        description="Look up line-of-sight from a Maidenhead grid square to W7W summits",
    )
    parser.add_argument("grid6", help="6-char Maidenhead grid (e.g. CN86mx)")
    parser.add_argument(
        "--status", choices=["clear", "marginal", "blocked", "all"], default="clear",
        help="Filter by LOS verdict (default: clear only)"
    )
    parser.add_argument("--max-km", type=float, default=None, help="Maximum distance (km)")
    parser.add_argument(
        "--sort", choices=["distance", "margin", "bearing"], default="distance",
        help="Sort order (default: distance)"
    )
    parser.add_argument(
        "--mean", action="store_true",
        help="Judge verdict on margin_mean_m (typical) instead of margin_max_m (optimistic)"
    )
    parser.add_argument("--summit", metavar="SUMMIT_REF", help="Filter to a single summit (e.g. W7W/MC-001)")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args(argv)

    # Validate grid
    try:
        grid6 = _normalise(args.grid6)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    db_path = Path(config.DB_PATH)
    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}. Run: python -m sota_los.build", file=sys.stderr)
        sys.exit(1)

    conn = get_connection(db_path)

    # Check grid exists
    grid_row = conn.execute("SELECT * FROM grids WHERE grid6 = ?", (grid6,)).fetchone()
    if grid_row is None:
        print(f"ERROR: Grid square {grid6!r} not found in database. "
              "It may be outside the computed area or have no DEM coverage.", file=sys.stderr)
        sys.exit(1)

    margin_col = "margin_mean_m" if args.mean else "margin_max_m"
    sort_map = {
        "distance": "l.distance_hm ASC",
        "margin": f"l.{margin_col} DESC",
        "bearing": "l.bearing_deg ASC",
    }
    order_by = sort_map[args.sort]

    q = f"""
        SELECT s.summit_ref, s.name, s.region, s.alt_m, s.points,
               l.margin_max_m, l.margin_mean_m,
               l.distance_hm / 10.0 AS distance_km, l.bearing_deg
        FROM los l
        JOIN summits s ON s.summit_id = l.summit_id
        WHERE l.grid_id = ?
    """
    params = [grid_row["grid_id"]]
    if args.summit is not None:
        summit_ref = args.summit.upper()
        row = conn.execute(
            "SELECT summit_id FROM summits WHERE summit_ref = ?", (summit_ref,)
        ).fetchone()
        if row is None:
            print(f"ERROR: Summit {summit_ref!r} not found in database.", file=sys.stderr)
            sys.exit(1)
        q += " AND l.summit_id = ?"
        params.append(row["summit_id"])
    if args.max_km is not None:
        q += " AND l.distance_hm <= ?"
        params.append(args.max_km * 10)
    q += f" ORDER BY {order_by}"

    rows = conn.execute(q, params).fetchall()

    if not rows:
        print(f"No results for {grid6} (database may not be fully computed yet).")
        return

    margin_col_idx = 6 if args.mean else 5  # margin_mean_m or margin_max_m

    results = []
    for r in rows:
        ref, name, region, alt_m, pts, m_max, m_mean, dist_km, bearing = tuple(r)
        margin = m_mean if args.mean else m_max
        v = verdict(margin)
        if args.status != "all" and v != args.status:
            continue
        results.append({
            "summit_ref": ref,
            "name": name,
            "region": region,
            "alt_m": alt_m,
            "points": pts,
            "margin_max_m": m_max,
            "margin_mean_m": m_mean,
            "distance_km": dist_km,
            "bearing_deg": bearing,
            "verdict": v,
        })

    if not results:
        print(f"No {args.status} summits found for {grid6}.")
        return

    if args.json:
        print(json.dumps({"grid6": grid6, "results": results}, indent=2))
        return

    # Human-readable table
    print(f"\nLOS from {grid6}  ({len(results)} result{'s' if len(results)!=1 else ''})\n")
    hdr = f"{'Ref':<14} {'Name':<24} {'V':>3} {'Margin':>8} {'Dist':>7} {'Brg':>5} {'Pts':>4}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        ref = r["summit_ref"]
        name = r["name"][:23]
        v = r["verdict"]
        margin = r["margin_max_m"] if not args.mean else r["margin_mean_m"]
        margin_str = f"{margin:+.0f}m" if margin is not None else "  N/A"
        dist_str = f"{r['distance_km']:.1f}km"
        brg_str = f"{r['bearing_deg']:.0f}°"
        pts_str = f"{r['points']}pt"
        emoji = verdict_emoji(v)
        print(f"{ref:<14} {name:<24} {emoji} {margin_str:>7} {dist_str:>7} {brg_str:>5} {pts_str:>4}")


if __name__ == "__main__":
    main()
