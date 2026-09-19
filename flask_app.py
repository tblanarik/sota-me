"""SOTA LOS webapp — PythonAnywhere WSGI entry point.

PythonAnywhere looks for `app` in this file.
Set SOTA_DB_PATH env var to override the database location.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is importable (required on PythonAnywhere)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import sqlite3

from flask import Flask, abort, g, jsonify, render_template, request

app = Flask(__name__)

_DEFAULT_DB = _HERE / "data" / "sota_los.sqlite"
DB_PATH = Path(os.environ.get("SOTA_DB_PATH", _DEFAULT_DB))

MARGINAL_THRESHOLD_M = -50.0


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalise_grid(raw: str) -> str:
    s = raw.strip()
    if len(s) != 6:
        raise ValueError(f"Grid must be 6 characters (e.g. CN86mx), got {s!r}")
    return s[:2].upper() + s[2:4] + s[4:].lower()


def verdict(margin_m: float | None) -> str:
    if margin_m is None:
        return "blocked"
    if margin_m >= 0:
        return "clear"
    if margin_m >= MARGINAL_THRESHOLD_M:
        return "marginal"
    return "blocked"


def _matches_filter(v: str, status_filter: str) -> bool:
    if status_filter == "all":
        return True
    if status_filter == "visible":
        return v in ("clear", "marginal")
    return v == status_filter


# ── database ──────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        if not DB_PATH.exists():
            raise FileNotFoundError(
                f"Database not found at {DB_PATH}. "
                "Set SOTA_DB_PATH or run the build pipeline first."
            )
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ── sort helpers ──────────────────────────────────────────────────────────────

_GRID_SORT = {
    "distance": "l.distance_hm ASC",
    "margin":   "l.margin_max_m DESC",
    "bearing":  "l.bearing_deg ASC",
    "points":   "s.points DESC, l.distance_hm ASC",
}

_SUMMIT_SORT = {
    "distance": "l.distance_hm ASC",
    "margin":   "l.margin_max_m DESC",
    "bearing":  "l.bearing_deg ASC",
    "elev":     "g.elev_max_m DESC",
}


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/docs")
def docs():
    return render_template("docs.html")


@app.route("/health")
def health():
    return jsonify({"ok": True, "db": str(DB_PATH), "db_exists": DB_PATH.exists()})


@app.route("/")
def index():
    raw_grid   = request.args.get("grid",   "").strip()
    raw_summit = request.args.get("summit", "").strip()
    status_filter = request.args.get("status", "visible")
    sort          = request.args.get("sort",   "distance")
    fmt           = request.args.get("format", "html")

    error      = None
    mode       = None
    results    = None
    grid_info  = None
    summit_info = None
    grid6      = None
    summit_ref = None

    # Validate / normalise inputs
    if raw_grid:
        try:
            grid6 = _normalise_grid(raw_grid)
        except ValueError as e:
            error = str(e)

    if raw_summit:
        summit_ref = raw_summit.upper()

    # Only proceed with queries if no validation error
    if not error and (grid6 or summit_ref):
        try:
            db = get_db()
        except FileNotFoundError as e:
            error = str(e)
            db = None

        if db is not None:
            if grid6 and summit_ref:
                mode = "los"
                row = db.execute("""
                    SELECT g.grid6, s.summit_ref,
                           s.name, s.region, s.alt_m, s.points,
                           g.center_lat, g.center_lon, g.elev_max_m,
                           l.margin_max_m, l.margin_mean_m,
                           l.distance_hm / 10.0 AS distance_km, l.bearing_deg
                    FROM grids g
                    JOIN summits s ON s.summit_ref = ?
                    JOIN los l ON l.grid_id = g.grid_id AND l.summit_id = s.summit_id
                    WHERE g.grid6 = ?
                """, (summit_ref, grid6)).fetchone()

                if row is None:
                    error = (
                        f"No LOS data for {grid6} ↔ {summit_ref}. "
                        "Check that both exist in the database."
                    )
                else:
                    v = verdict(row["margin_max_m"])
                    results = {**dict(row), "verdict": v}

            elif grid6:
                mode = "grid"
                grid_info = db.execute(
                    "SELECT * FROM grids WHERE grid6 = ?", (grid6,)
                ).fetchone()

                if grid_info is None:
                    error = f"Grid {grid6!r} not found — it may be outside the computed area."
                else:
                    order = _GRID_SORT.get(sort, "l.distance_hm ASC")
                    rows = db.execute(f"""
                        SELECT s.summit_ref, s.name, s.region, s.alt_m, s.points,
                               l.margin_max_m, l.margin_mean_m,
                               l.distance_hm / 10.0 AS distance_km, l.bearing_deg
                        FROM los l
                        JOIN summits s ON s.summit_id = l.summit_id
                        WHERE l.grid_id = ?
                        ORDER BY {order}
                    """, (grid_info["grid_id"],)).fetchall()

                    results = [
                        {**dict(r), "verdict": verdict(r["margin_max_m"])}
                        for r in rows
                        if _matches_filter(verdict(r["margin_max_m"]), status_filter)
                    ]

            else:  # summit only
                mode = "summit"
                summit_info = db.execute(
                    "SELECT * FROM summits WHERE summit_ref = ?", (summit_ref,)
                ).fetchone()

                if summit_info is None:
                    error = f"Summit {summit_ref!r} not found."
                else:
                    order = _SUMMIT_SORT.get(sort, "l.distance_hm ASC")
                    rows = db.execute(f"""
                        SELECT g.grid6,
                               g.center_lat, g.center_lon, g.elev_max_m,
                               l.margin_max_m, l.margin_mean_m,
                               l.distance_hm / 10.0 AS distance_km, l.bearing_deg
                        FROM los l
                        JOIN grids g ON g.grid_id = l.grid_id
                        WHERE l.summit_id = ?
                        ORDER BY {order}
                    """, (summit_info["summit_id"],)).fetchall()

                    results = [
                        {**dict(r), "verdict": verdict(r["margin_max_m"])}
                        for r in rows
                        if _matches_filter(verdict(r["margin_max_m"]), status_filter)
                    ]

    if fmt == "json":
        return jsonify({
            "mode":    mode,
            "grid":    grid6,
            "summit":  summit_ref,
            "filter":  status_filter,
            "error":   error,
            "results": results,
        })

    return render_template(
        "index.html",
        mode=mode,
        raw_grid=raw_grid,
        raw_summit=raw_summit,
        grid6=grid6,
        summit_ref=summit_ref,
        status_filter=status_filter,
        sort=sort,
        error=error,
        results=results,
        grid_info=dict(grid_info) if grid_info else None,
        summit_info=dict(summit_info) if summit_info else None,
        result_count=len(results) if isinstance(results, list) else None,
    )


if __name__ == "__main__":
    app.run(debug=True)
