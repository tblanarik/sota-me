"""Build the grids table: per-subsquare elevation statistics from the DEM.

For each 6-char Maidenhead subsquare intersecting Washington state:
  - elev_max_m:  highest DEM pixel value in the subsquare
  - max_lat/lon: WGS84 location of that pixel (the 'optimistic' representative point)
  - elev_mean_m: mean DEM pixel value in the subsquare
  - center_lat/lon: geographic centre of the subsquare (the 'typical' representative point)

All DEM sampling is vectorised with numpy; no Python loops over pixels.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
from osgeo import gdal
from pyproj import Transformer

import config
from sota_los import maidenhead
from sota_los.db import set_meta


def build_grid(conn: sqlite3.Connection, force: bool = False) -> int:
    """Populate the grids table.  Returns number of subsquares inserted."""
    if not force:
        n = conn.execute("SELECT COUNT(*) FROM grids").fetchone()[0]
        if n > 0:
            print(f"Grids already built ({n} rows), skipping.  Pass --force to rebuild.")
            return n

    dem_path = Path(config.DEM_PATH)
    if not dem_path.exists():
        raise FileNotFoundError(f"DEM not found at {dem_path}; run fetch_dem first")

    ds = gdal.Open(str(dem_path))
    gt = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()

    print("Reading DEM into memory …")
    dem = band.ReadAsArray().astype(np.float32)
    nrows, ncols = dem.shape
    if nodata is not None:
        dem[dem == nodata] = np.nan

    print(f"DEM shape: {ncols}×{nrows} (col×row), computing pixel coords …")

    # WGS84 ← UTM 10N back-projection
    to_wgs84 = Transformer.from_crs(config.TARGET_CRS, "EPSG:4326", always_xy=True)

    # Pixel-centre UTM coordinates (vectors, then broadcast)
    col_idx = np.arange(ncols, dtype=np.float64)
    row_idx = np.arange(nrows, dtype=np.float64)
    xs = gt[0] + (col_idx + 0.5) * gt[1]          # shape (ncols,)
    ys = gt[3] + (row_idx + 0.5) * gt[5]           # shape (nrows,)

    # Full UTM grids — these are large (~44M per array) but tractable at float32
    # Broadcasting: ys[:,None] + xs[None,:] would be (nrows, ncols) — that's 2GB, too big.
    # Instead we vectorise at the per-subsquare level using pixel assignment.

    print("Assigning pixels to subsquares (vectorised) …")

    # Encode each pixel's subsquare by computing field/square/subsquare indices
    # from its WGS84 lat/lon.
    # Strategy: compute lat/lon for every pixel centre, then integer arithmetic.

    # Full grids computed row-by-row to avoid 2D broadcasting OOM
    # xs is 1D (ncols,), ys is 1D (nrows,)
    # Build full 2D lon and lat arrays:
    # xs_2d[r,c] = xs[c],  ys_2d[r,c] = ys[r]
    # Convert from UTM to WGS84 in one big call
    print("  Converting UTM → WGS84 (this may take a moment) …")
    xs_2d = np.broadcast_to(xs[np.newaxis, :], (nrows, ncols)).copy()
    ys_2d = np.broadcast_to(ys[:, np.newaxis], (nrows, ncols)).copy()
    lons_2d, lats_2d = to_wgs84.transform(xs_2d.ravel(), ys_2d.ravel())
    lons_2d = lons_2d.reshape(nrows, ncols).astype(np.float32)
    lats_2d = lats_2d.reshape(nrows, ncols).astype(np.float32)
    del xs_2d, ys_2d

    # Encode subsquare ID as a flat integer for grouping.
    # ID = fi*18*10*10*24*24 + fj*10*10*24*24 + si*10*24*24 + sj*24*24 + ubi*24 + ubj
    print("  Computing subsquare indices …")
    adj_lon = (lons_2d + 180).astype(np.float64)
    adj_lat = (lats_2d + 90).astype(np.float64)

    fi = np.clip(adj_lon / 20, 0, 17).astype(np.int32)
    fj = np.clip(adj_lat / 10, 0, 17).astype(np.int32)
    rem_lon = adj_lon - fi * 20.0
    rem_lat = adj_lat - fj * 10.0
    si = np.clip(rem_lon / 2, 0, 9).astype(np.int32)
    sj = np.clip(rem_lat / 1, 0, 9).astype(np.int32)
    rem_lon -= si * 2.0
    rem_lat -= sj * 1.0
    ubi = np.clip(rem_lon * 12, 0, 23).astype(np.int32)
    ubj = np.clip(rem_lat * 24, 0, 23).astype(np.int32)
    del adj_lon, adj_lat, rem_lon, rem_lat

    M = 18 * 10 * 10 * 24 * 24
    grid_id = (
        fi.astype(np.int64) * (18 * 10 * 10 * 24 * 24)
        + fj.astype(np.int64) * (10 * 10 * 24 * 24)
        + si.astype(np.int64) * (10 * 24 * 24)
        + sj.astype(np.int64) * (24 * 24)
        + ubi.astype(np.int64) * 24
        + ubj.astype(np.int64)
    )
    del fi, fj, si, sj, ubi, ubj

    print("  Computing per-subsquare max and mean (vectorised sort-reduceat) …")
    flat_id = grid_id.ravel()
    flat_dem = dem.ravel()
    flat_lon = lons_2d.ravel()
    flat_lat = lats_2d.ravel()
    del grid_id, lons_2d, lats_2d

    # Mask out nodata
    valid = np.isfinite(flat_dem)
    flat_id = flat_id[valid]
    flat_dem = flat_dem[valid]
    flat_lon = flat_lon[valid]
    flat_lat = flat_lat[valid]

    # Sort by grid ID
    order = np.argsort(flat_id, kind='stable')
    flat_id = flat_id[order]
    flat_dem = flat_dem[order]
    flat_lon = flat_lon[order]
    flat_lat = flat_lat[order]

    # Find unique IDs and group boundaries
    unique_ids, first_idx, counts = np.unique(flat_id, return_index=True, return_counts=True)

    # Max elevation per group (use reduceat)
    max_elev = np.maximum.reduceat(flat_dem, first_idx)
    sum_elev = np.add.reduceat(flat_dem, first_idx)
    mean_elev = sum_elev / counts

    # Find the index of the max pixel within each group
    # For argmax per group, we need a local offset
    local_max_idx = _reduceat_argmax(flat_dem, first_idx)
    global_max_idx = first_idx + local_max_idx

    max_lat = flat_lat[global_max_idx]
    max_lon = flat_lon[global_max_idx]

    # Only include subsquares intersecting the WA bounding box
    wa_grids = set(maidenhead.enumerate_wa())

    print(f"  Filtering to WA subsquares (n_unique={len(unique_ids)}) …")
    rows = []
    for i, gid in enumerate(unique_ids):
        # Decode gid back to 6-char label
        g = _decode_grid_id(int(gid))
        if g not in wa_grids:
            continue
        c_lat, c_lon = maidenhead.center(g)
        rows.append((
            g,
            float(c_lat),
            float(c_lon),
            float(max_elev[i]),
            float(max_lat[i]),
            float(max_lon[i]),
            float(mean_elev[i]),
        ))

    conn.execute("DELETE FROM grids")
    conn.executemany(
        """INSERT INTO grids (grid6, center_lat, center_lon, elev_max_m, max_lat, max_lon, elev_mean_m)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    set_meta(conn, "grid_count", str(len(rows)))
    conn.commit()
    print(f"Inserted {len(rows)} subsquare rows")
    return len(rows)


def _reduceat_argmax(values: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """Return the within-group argmax index for each group defined by starts."""
    n_groups = len(starts)
    ends = np.empty(n_groups, dtype=np.int64)
    ends[:-1] = starts[1:]
    ends[-1] = len(values)

    result = np.empty(n_groups, dtype=np.int64)
    for i in range(n_groups):
        seg = values[starts[i]:ends[i]]
        result[i] = np.argmax(seg)
    return result


def _decode_grid_id(gid: int) -> str:
    """Inverse of the encoding in build_grid."""
    ubj = gid % 24;  gid //= 24
    ubi = gid % 24;  gid //= 24
    sj  = gid % 10;  gid //= 10
    si  = gid % 10;  gid //= 10
    fj  = gid % 18;  gid //= 18
    fi  = gid % 18
    return (
        chr(ord('A') + fi)
        + chr(ord('A') + fj)
        + str(si)
        + str(sj)
        + chr(ord('a') + ubi)
        + chr(ord('a') + ubj)
    )
