"""Central configuration — all tuneable constants live here."""

from pathlib import Path

# ── Antenna / viewshed physics ─────────────────────────────────────────────
ANTENNA_HEIGHT_M: float = 2.0           # chaser antenna height above representative point
OBSERVER_ANTENNA_HEIGHT_M: float = 2.0  # activator antenna height above true summit
MARGINAL_THRESHOLD_M: float = -50.0     # margins in [MARGINAL_THRESHOLD_M, 0) are "marginal"
MAX_DISTANCE_M: float = 250_000.0       # viewshed radius
PRUNE_BELOW_M: float | None = None      # drop rows with margin_max_m below this (None = keep all)
CURVATURE_COEFF: float = 0.85714        # 4/3-earth atmospheric refraction coefficient
DEM_PIXEL_M: float = 90.0              # reprojected DEM resolution (meters)
TARGET_CRS: str = "EPSG:32610"          # UTM zone 10N

# ── Paths ──────────────────────────────────────────────────────────────────
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
DB_PATH = DATA_DIR / "sota_los.sqlite"
DEM_PATH = PROCESSED_DIR / "dem_wa_utm10_90m.tif"

# ── DEM extent (WGS84, with 0.25° buffer around WA) ───────────────────────
DEM_LON_MIN: float = -125.25
DEM_LON_MAX: float = -116.5
DEM_LAT_MIN: float = 45.25
DEM_LAT_MAX: float = 49.25

# ── Maidenhead enumeration bounds (WA state) ───────────────────────────────
GRID_LON_MIN: float = -124.9
GRID_LON_MAX: float = -116.9
GRID_LAT_MIN: float = 45.5
GRID_LAT_MAX: float = 49.0

# ── SOTA data ──────────────────────────────────────────────────────────────
SUMMITS_URL: str = "https://www.sotadata.org.uk/summitslist.csv"
SOTA_ASSOCIATION: str = "W7W"
EARTH_RADIUS_M: float = 6_378_137.0    # WGS84 semi-major axis
