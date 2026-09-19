"""Download Copernicus GLO-90 DEM tiles, mosaic, and reproject to UTM 10N.

Tile naming (verified from bucket listing):
  Copernicus_DSM_COG_30_N{lat:02d}_00_W{lon:03d}_00_DEM/
    Copernicus_DSM_COG_30_N{lat:02d}_00_W{lon:03d}_00_DEM.tif

  Despite the "90m" bucket name, the DEM tiles inside are named COG_30 (not COG_10).
  Each tile is 1200×1200 pixels per 1°×1°, pixel size = 3 arc-seconds ≈ 92m.

  NOTE: tiles exist only where land data is available; ocean tiles are absent.
"""

from __future__ import annotations

import os
import sqlite3
import urllib.request
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

import config
from sota_los.db import set_meta

# S3 endpoint (HTTP, since HTTPS cert path varies by OS; GDAL also handles this)
_BUCKET_HTTP = "http://copernicus-dem-90m.s3.amazonaws.com"
_TILE_PREFIX = "Copernicus_DSM_COG_30"


def _tile_name(lat: int, lon: int) -> str:
    """Return tile base name for integer floor lat/lon."""
    ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
    ew = f"W{-lon:03d}" if lon < 0 else f"E{lon:03d}"
    return f"{_TILE_PREFIX}_{ns}_00_{ew}_00_DEM"


def _tile_url(lat: int, lon: int) -> str:
    name = _tile_name(lat, lon)
    return f"{_BUCKET_HTTP}/{name}/{name}.tif"


def needed_tiles(
    lon_min: float = config.DEM_LON_MIN,
    lon_max: float = config.DEM_LON_MAX,
    lat_min: float = config.DEM_LAT_MIN,
    lat_max: float = config.DEM_LAT_MAX,
) -> list[tuple[int, int]]:
    """Return (lat_floor, lon_floor) pairs covering the bounding box."""
    tiles = []
    for lat in range(int(np.floor(lat_min)), int(np.ceil(lat_max))):
        for lon in range(int(np.floor(lon_min)), int(np.ceil(lon_max))):
            tiles.append((lat, lon))
    return tiles


def download_tiles(force: bool = False) -> list[Path]:
    """Download all tiles for the WA extent using requests (not GDAL vsicurl).

    GDAL's bundled libcurl may have DNS issues in some environments; using
    Python requests avoids that.  Tiles are downloaded as raw GeoTIFFs.
    """
    import requests as _requests

    raw_dir = Path(config.RAW_DIR) / "dem_tiles"
    raw_dir.mkdir(parents=True, exist_ok=True)

    tiles = needed_tiles()
    print(f"Checking {len(tiles)} potential tile locations …")

    paths: list[Path] = []
    for lat, lon in tiles:
        name = _tile_name(lat, lon)
        cache = raw_dir / f"{name}.tif"
        if cache.exists() and not force:
            paths.append(cache)
            continue

        url = _tile_url(lat, lon)
        # HEAD probe to check existence (ocean tiles are absent)
        try:
            resp = _requests.head(url, timeout=10, allow_redirects=True)
        except Exception:
            continue
        if resp.status_code == 404:
            continue
        if not resp.ok:
            print(f"  WARNING: HEAD {url} → {resp.status_code}")
            continue

        print(f"  Downloading {name} …")
        try:
            resp = _requests.get(url, timeout=120, stream=True)
            resp.raise_for_status()
            tmp = cache.with_suffix(".tmp")
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
            tmp.rename(cache)
            paths.append(cache)
        except Exception as exc:
            print(f"  WARNING: download failed for {name}: {exc}")
            if cache.with_suffix(".tmp").exists():
                cache.with_suffix(".tmp").unlink()

    print(f"Got {len(paths)} tiles")
    return paths


def build_dem(conn: sqlite3.Connection | None = None, force: bool = False) -> Path:
    """Download tiles, mosaic, reproject to UTM 10N at 90m.  Returns DEM path."""
    out_path = Path(config.DEM_PATH)
    if out_path.exists() and not force:
        print(f"DEM already exists at {out_path}, skipping.  Pass --force to rebuild.")
        return out_path

    tile_paths = download_tiles(force)
    if not tile_paths:
        raise RuntimeError("No DEM tiles downloaded — check network access")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Building VRT mosaic from {len(tile_paths)} tiles …")
    vrt_path = str(out_path.with_suffix(".vrt"))
    vrt = gdal.BuildVRT(vrt_path, [str(p) for p in tile_paths])
    if vrt is None:
        raise RuntimeError("BuildVRT failed")
    vrt.FlushCache()
    vrt = None

    print(f"Reprojecting to {config.TARGET_CRS} at {config.DEM_PIXEL_M:.0f} m …")
    warp_opts = gdal.WarpOptions(
        dstSRS=config.TARGET_CRS,
        xRes=config.DEM_PIXEL_M,
        yRes=config.DEM_PIXEL_M,
        resampleAlg="bilinear",
        outputBounds=_wa_utm_bounds(),
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"],
        multithread=True,
    )
    result = gdal.Warp(str(out_path), vrt_path, options=warp_opts)
    if result is None:
        raise RuntimeError("Warp failed")
    result.FlushCache()
    result = None

    # Report raster properties
    ds = gdal.Open(str(out_path))
    gt = ds.GetGeoTransform()
    print(f"DEM saved: {ds.RasterXSize}×{ds.RasterYSize} pixels, {ds.GetProjection()[:40]}…")
    ds = None

    if conn is not None:
        set_meta(conn, "dem_source", "Copernicus GLO-90 (COG_30 tiles, copernicus-dem-90m S3 bucket)")
        set_meta(conn, "dem_crs", config.TARGET_CRS)
        set_meta(conn, "dem_pixel_m", str(config.DEM_PIXEL_M))
        set_meta(conn, "dem_path", str(out_path))

    return out_path


def _wa_utm_bounds() -> tuple[float, float, float, float]:
    """Return (xmin, ymin, xmax, ymax) for the DEM extent in UTM 10N."""
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", config.TARGET_CRS, always_xy=True)
    xmin, ymin = t.transform(config.DEM_LON_MIN, config.DEM_LAT_MIN)
    xmax, ymax = t.transform(config.DEM_LON_MAX, config.DEM_LAT_MAX)
    return xmin, ymin, xmax, ymax


def read_elevation_at_points(
    dem_path: Path,
    lats: np.ndarray,
    lons: np.ndarray,
) -> np.ndarray:
    """Sample DEM elevation (meters) at WGS84 lat/lon points via nearest-pixel."""
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", config.TARGET_CRS, always_xy=True)
    xs, ys = t.transform(lons, lats)

    ds = gdal.Open(str(dem_path))
    gt = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()

    # UTM -> pixel (col, row)
    inv_gt = gdal.InvGeoTransform(gt)
    cols = (inv_gt[0] + inv_gt[1] * xs + inv_gt[2] * ys).astype(int)
    rows = (inv_gt[3] + inv_gt[4] * xs + inv_gt[5] * ys).astype(int)

    # Clamp
    cols = np.clip(cols, 0, ds.RasterXSize - 1)
    rows = np.clip(rows, 0, ds.RasterYSize - 1)

    # Read unique pixels efficiently
    data = band.ReadAsArray()
    elev = data[rows, cols].astype(float)
    if nodata is not None:
        elev[elev == nodata] = np.nan
    return elev
