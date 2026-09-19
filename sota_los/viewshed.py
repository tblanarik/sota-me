"""Run GDAL viewshed in required-height mode for each summit and compute LOS margins.

Viewshed output semantics (verified in checkpoint 4 test):
  - Mode: GVOT_MIN_TARGET_HEIGHT_FROM_DEM
  - Output value at each target pixel: the ABSOLUTE elevation (meters ASL) the target
    must reach to be visible from the observer.
  - For visible pixels (no obstruction): output ≈ DEM elevation at that pixel.
  - For blocked pixels: output > DEM elevation (must be higher to clear terrain).
  - Nodata / out-of-range: output = noDataVal (we use -9999.0).

Margin formula (from spec §2.2):
    margin_m = (target_elevation + ANTENNA_HEIGHT_M) - viewshed_required_elevation

  margin ≥ 0 → clear; MARGINAL_THRESHOLD_M ≤ margin < 0 → marginal; else blocked.
"""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
from pathlib import Path
from typing import NamedTuple

import numpy as np
from osgeo import gdal
from pyproj import Geod, Transformer

import config


class GridStats(NamedTuple):
    grid_id: int
    grid6: str
    center_lat: float
    center_lon: float
    elev_max_m: float
    max_lat: float
    max_lon: float
    elev_mean_m: float


def run_viewshed_for_summit(
    dem_path: Path,
    summit_ref: str,
    summit_lat: float,
    summit_lon: float,
    summit_alt_m: float,
    dem_alt_m: float,
) -> tuple[np.ndarray, tuple]:
    """Return (required_elev_array, geotransform) for one summit.

    The geotransform returned is the *viewshed's*, not the source DEM's: with
    maxDistance set, GDAL clips the output to a window around the observer, so
    the two have different origins and sizes.
    """
    ds = gdal.Open(str(dem_path))

    to_utm = Transformer.from_crs("EPSG:4326", config.TARGET_CRS, always_xy=True)
    sx, sy = to_utm.transform(summit_lon, summit_lat)

    # Observer height correction (spec §2.3)
    obs_height = max(summit_alt_m - dem_alt_m, 0.0) + config.OBSERVER_ANTENNA_HEIGHT_M

    vs_ds = gdal.ViewshedGenerate(
        srcBand=ds.GetRasterBand(1),
        driverName="MEM",
        targetRasterName="",
        creationOptions=[],
        observerX=sx,
        observerY=sy,
        observerHeight=obs_height,
        targetHeight=0.0,
        visibleVal=1.0,
        invisibleVal=0.0,
        outOfRangeVal=-9999.0,
        noDataVal=-9999.0,
        dfCurvCoeff=config.CURVATURE_COEFF,
        mode=gdal.GVM_Edge,
        maxDistance=config.MAX_DISTANCE_M,
        heightMode=gdal.GVOT_MIN_TARGET_HEIGHT_FROM_DEM,
    )
    arr = vs_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    vs_gt = vs_ds.GetGeoTransform()
    ds = None
    return arr, vs_gt


def _compute_los_for_summit(args: tuple) -> list[tuple]:
    """Worker function: compute LOS rows for one summit.  Returns list of row tuples."""
    (dem_path_str, summit_id, summit_ref, s_lat, s_lon, s_alt_m, s_dem_alt_m, grids) = args

    dem_path = Path(dem_path_str)
    try:
        req_elev, gt = run_viewshed_for_summit(
            dem_path, summit_ref, s_lat, s_lon, s_alt_m, s_dem_alt_m
        )
    except Exception as exc:
        print(f"  ERROR viewshed {summit_ref}: {exc}")
        return []

    nrows, ncols = req_elev.shape
    to_utm = Transformer.from_crs("EPSG:4326", config.TARGET_CRS, always_xy=True)
    geod = Geod(ellps="WGS84")

    # Convert grid max/mean pixel locations to DEM pixel coordinates
    inv_gt = gdal.InvGeoTransform(gt)

    g_center_lats = np.array([g.center_lat for g in grids])
    g_center_lons = np.array([g.center_lon for g in grids])
    g_max_lats    = np.array([g.max_lat    for g in grids])
    g_max_lons    = np.array([g.max_lon    for g in grids])
    g_max_elev    = np.array([g.elev_max_m for g in grids])
    g_mean_elev   = np.array([g.elev_mean_m for g in grids])

    # Sample viewshed at max pixel
    mx_x, mx_y = to_utm.transform(g_max_lons, g_max_lats)
    mx_col = (inv_gt[0] + inv_gt[1] * mx_x + inv_gt[2] * mx_y).astype(int)
    mx_row = (inv_gt[3] + inv_gt[4] * mx_x + inv_gt[5] * mx_y).astype(int)
    valid_max = (mx_col >= 0) & (mx_col < ncols) & (mx_row >= 0) & (mx_row < nrows)

    req_at_max = np.full(len(grids), np.nan)
    req_at_max[valid_max] = req_elev[mx_row[valid_max], mx_col[valid_max]]

    # Sample viewshed at center pixel
    cx, cy = to_utm.transform(g_center_lons, g_center_lats)
    cx_col = (inv_gt[0] + inv_gt[1] * cx + inv_gt[2] * cy).astype(int)
    cx_row = (inv_gt[3] + inv_gt[4] * cx + inv_gt[5] * cy).astype(int)
    valid_cen = (cx_col >= 0) & (cx_col < ncols) & (cx_row >= 0) & (cx_row < nrows)

    req_at_cen = np.full(len(grids), np.nan)
    req_at_cen[valid_cen] = req_elev[cx_row[valid_cen], cx_col[valid_cen]]

    # Compute margins
    margin_max = (g_max_elev + config.ANTENNA_HEIGHT_M) - req_at_max
    margin_mean = (g_mean_elev + config.ANTENNA_HEIGHT_M) - req_at_cen

    # Distance and bearing (pyproj.Geod, from grid center to summit)
    az, _, dist_m = geod.inv(g_center_lons, g_center_lats,
                              np.full(len(grids), s_lon),
                              np.full(len(grids), s_lat))
    dist_km = dist_m / 1000.0
    bearing = (az % 360.0)

    # Mark nodata (out-of-range) as NaN
    nodata_mask = (req_at_max < -9000) | (req_at_cen < -9000)
    margin_max[nodata_mask] = np.nan
    margin_mean[nodata_mask] = np.nan

    # Quantise for storage — see the module docstring in sota_los.db.
    clamp = config.MARGIN_CLAMP_M
    q_margin_max = np.clip(np.round(margin_max), -clamp, clamp)
    q_margin_mean = np.clip(np.round(margin_mean), -clamp, clamp)
    q_dist_hm = np.round(dist_km * 10.0)
    q_bearing = np.round(bearing)

    rows = []
    for i, g in enumerate(grids):
        if np.isnan(margin_max[i]) and np.isnan(margin_mean[i]):
            continue   # out of range or no DEM data
        if dist_m[i] > config.MAX_DISTANCE_M:
            # GDAL clips the viewshed to a rectangular window, whose corners reach
            # ~1.41x maxDistance. Enforce a true circular radius here.
            continue
        if config.PRUNE_BELOW_M is not None and margin_max[i] < config.PRUNE_BELOW_M:
            continue
        rows.append((
            g.grid_id,
            summit_id,
            None if np.isnan(q_margin_max[i]) else int(q_margin_max[i]),
            None if np.isnan(q_margin_mean[i]) else int(q_margin_mean[i]),
            int(q_dist_hm[i]),
            int(q_bearing[i]),
        ))
    return rows


def compute_los(
    conn: sqlite3.Connection,
    force: bool = False,
    n_workers: int | None = None,
    batch_size: int = 10,
) -> int:
    """Compute LOS for all summits, parallelised by summit.  Returns row count."""
    if not force:
        n = conn.execute("SELECT COUNT(*) FROM los").fetchone()[0]
        if n > 0:
            print(f"LOS already computed ({n} rows), skipping.  Pass --force to rebuild.")
            return n

    grids_raw = conn.execute(
        "SELECT grid_id, grid6, center_lat, center_lon, elev_max_m, max_lat, max_lon, elev_mean_m "
        "FROM grids"
    ).fetchall()
    grids = [GridStats(*tuple(r)) for r in grids_raw]

    summits = conn.execute(
        "SELECT summit_id, summit_ref, lat, lon, alt_m, COALESCE(dem_alt_m, alt_m) FROM summits"
    ).fetchall()

    dem_path_str = str(config.DEM_PATH)
    n_workers = n_workers or max(1, (os.cpu_count() or 4) - 1)
    print(f"Computing LOS: {len(summits)} summits × {len(grids)} subsquares "
          f"using {n_workers} workers …")

    conn.execute("DELETE FROM los")
    conn.commit()

    work = [
        (dem_path_str, r[0], r[1], r[2], r[3], r[4], r[5], grids)
        for r in summits
    ]

    total_rows = 0
    with multiprocessing.Pool(n_workers) as pool:
        for i, result in enumerate(pool.imap_unordered(_compute_los_for_summit, work)):
            total_rows += len(result)
            if result:
                conn.executemany(
                    """INSERT OR REPLACE INTO los
                       (grid_id, summit_id, margin_max_m, margin_mean_m, distance_hm, bearing_deg)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    result,
                )
            if (i + 1) % batch_size == 0:
                conn.commit()
                pct = (i + 1) / len(summits) * 100
                print(f"  {i+1}/{len(summits)} summits ({pct:.0f}%), {total_rows:,} rows …")

    conn.commit()
    print(f"LOS computation complete: {total_rows:,} rows")
    return total_rows
