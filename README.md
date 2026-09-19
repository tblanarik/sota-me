# SOTA W7W Line-of-Sight Lookup

Precomputed terrain line-of-sight from any Washington-area Maidenhead grid square to every W7W SOTA summit. Given a 6-character grid like `CN86mx`, it returns every summit with a rough LOS verdict (clear / marginal / blocked), distance, and bearing.

The question it answers: *"Is there obviously terrain between me and that summit?"* It is a sanity-check tool for VHF/UHF chasers, not a propagation model. All viewsheds are precomputed; the query is a single SQLite lookup.

---

## Requirements

- Python 3.12 (via [uv](https://github.com/astral-sh/uv))
- No conda required — GDAL is bundled via rasterio

## Setup

```sh
git clone <this-repo>
cd sota-me

# Create venv and install dependencies
uv venv --python 3.12
source .venv/bin/activate
uv pip install rasterio numpy pyproj requests pytest

# Build GDAL Python bindings against rasterio's bundled libgdal
# (see notes below under "GDAL installation")
```

The repo ships a `sitecustomize.py` inside the venv that auto-loads rasterio's bundled `libgdal` and sets `PROJ_LIB` on startup — no environment variables needed.

### GDAL Python bindings

Standard `pip install gdal` will not work here because it tries to link against a system GDAL. The workaround:

1. Download the GDAL PyPI sdist (contains pre-generated SWIG wrappers):
   ```sh
   pip download --no-deps --no-binary :all: "gdal==3.12.4" -d /tmp/gdal-sdist
   ```
2. Extract it and build against rasterio's bundled `libgdal`:
   ```sh
   tar xf /tmp/gdal-sdist/gdal-3.12.4.tar.gz -C /tmp
   # create a fake gdal-config pointing at .venv/lib/python3.12/site-packages/rasterio.libs/
   # set GDAL_CONFIG and pip install /tmp/gdal-3.12.4
   ```
   The specifics of the fake `gdal-config` and required header stubs are recorded in the project's git history.

---

## Building the database

Run stages in order. Each stage is idempotent — it checks for its output and skips if already present. Pass `--force` to recompute.

```sh
# Run all stages end to end (takes ~15 min: mostly the LOS computation)
python -m sota_los.build

# Or run individual stages
python -m sota_los.build --stage fetch_summits
python -m sota_los.build --stage fetch_dem
python -m sota_los.build --stage build_grid
python -m sota_los.build --stage compute_los
python -m sota_los.build --stage build_indexes
```

### What each stage does

| Stage | Output | Time |
|---|---|---|
| `fetch_summits` | `data/raw/summitslist_<date>.csv` + `summits` table | seconds |
| `fetch_dem` | `data/processed/dem_wa_utm10_90m.tif` (7216×5138 px, UTM 10N) | ~5 min (47 tiles) |
| `build_grid` | `grids` table (8,142 subsquares) | ~3 min |
| `compute_los` | `los` table (11.7 M rows) | ~5 min (19 workers) |
| `build_indexes` | indexes + VACUUM | ~20 sec |

Final database: `data/sota_los.sqlite`, ~1.2 GB.

### Data sources

- **Summit list:** `https://www.sotadata.org.uk/summitslist.csv` — 2,762 active W7W summits as of the build date
- **DEM:** Copernicus GLO-90 from `s3://copernicus-dem-90m/` (public, no auth). Tiles are named `Copernicus_DSM_COG_30_N{lat}_00_W{lon}_00_DEM` — note `COG_30`, not `COG_10` as the spec guessed; each tile is 1200×1200 pixels at ~92 m/px.

---

## Querying

```sh
python -m sota_los.cli CN86mx
```

```
LOS from CN86mx  (67 results)

Ref            Name                       V   Margin    Dist   Brg  Pts
-----------------------------------------------------------------------
W7W/SO-081     1864 Intermediate Freq   ✅    +37m   7.9km  287°  2pt
W7W/SO-073     Rock Candy Mountain      ✅    +36m  12.1km  286°  2pt
W7W/SO-123     1420                     ✅    +46m  13.7km  246°  1pt
...
W7W/WH-001     Mount Baker              ✅    +20m 217.4km   23° 10pt
```

### Options

```
python -m sota_los.cli GRID6 [options]
```

| Option | Default | Description |
|---|---|---|
| `--status clear\|marginal\|blocked\|all` | `clear` | Filter by verdict |
| `--max-km N` | none | Cap distance |
| `--sort distance\|margin\|bearing` | `distance` | Sort order |
| `--mean` | off | Use `margin_mean_m` (pessimistic) instead of `margin_max_m` (optimistic) |
| `--json` | off | Machine-readable JSON output |

### Examples

```sh
# All summits within 50 km, any verdict, sorted by margin
python -m sota_los.cli CN86mx --status all --max-km 50 --sort margin

# High-value summits with clear shot, sorted by bearing (scan the horizon)
python -m sota_los.cli CN86mx --sort bearing

# Marginal summits worth attempting
python -m sota_los.cli CN86mx --status marginal

# JSON output for scripting
python -m sota_los.cli CN86mx --json | jq '.results[] | select(.points >= 6)'
```

### Grid square input

The tool accepts any valid 6-character Maidenhead subsquare covering Washington state. Input is case-insensitive (`CN86MX`, `cn86mx`, and `CN86mx` are all accepted).

If your grid square isn't in the database it either falls outside the computed area or has no DEM coverage. The tool will say so explicitly.

---

## Verdicts explained

Each summit is assessed from the **highest-elevation DEM pixel** in your grid square (the optimistic view) and from the **mean elevation at the grid center** (the typical view). The `--mean` flag switches which is used for the verdict.

| Verdict | Condition | Meaning |
|---|---|---|
| ✅ clear | margin ≥ 0 m | LOS exists with 2 m antenna clearance |
| ⚠️ marginal | −50 m ≤ margin < 0 m | Terrain nearly clears; VHF may diffract through |
| ❌ blocked | margin < −50 m | Terrain is significantly in the way |

**Margin** is the headroom in meters: how far above (positive) or below (negative) the required line-of-sight height your antenna position sits.

The 50 m marginal threshold is configurable in `config.py` (`MARGINAL_THRESHOLD_M`).

---

## How it works

For each of the 2,762 active W7W summits, one GDAL viewshed is computed against a 90 m UTM-projected DEM using the `GVOT_MIN_TARGET_HEIGHT_FROM_DEM` mode. This mode outputs, at every pixel, the **minimum absolute elevation** (meters ASL) a target at that location must reach to be visible from the observer.

Margin is then:
```
margin_m = (representative_elevation + ANTENNA_HEIGHT_M) - viewshed_required_elevation
```

**Verified finding:** `GVOT_MIN_TARGET_HEIGHT_FROM_DEM` outputs absolute elevation (ASL), not height above DEM. For a flat, unobstructed pixel at DEM elevation 100 m, the output is 100.0. For a blocked pixel the output is the elevation you'd need to clear the obstruction. This was confirmed by a synthetic single-pixel DEM test during development.

Observer height correction accounts for DEM smoothing at peaks:
```
observer_height = max(summit_alt_m - dem_elevation_at_summit, 0) + 2 m
```

Earth curvature uses the standard 4/3-earth atmospheric refraction coefficient (0.85714).

---

## Configuration

All constants are in `config.py`:

| Name | Default | Meaning |
|---|---|---|
| `ANTENNA_HEIGHT_M` | 2.0 m | Chaser antenna height above the representative point |
| `OBSERVER_ANTENNA_HEIGHT_M` | 2.0 m | Activator antenna height above the true summit |
| `MARGINAL_THRESHOLD_M` | −50 m | Lower bound of "marginal" verdict |
| `MAX_DISTANCE_M` | 250,000 m | Viewshed radius (250 km) |
| `CURVATURE_COEFF` | 0.85714 | 4/3-earth atmospheric refraction |
| `DEM_PIXEL_M` | 90 m | Reprojected DEM resolution |
| `TARGET_CRS` | EPSG:32610 | UTM Zone 10N |

Changing any constant that affects LOS computation requires rerunning `compute_los` (and optionally `build_indexes`).

---

## Limitations

- **90 m DEM resolution.** Ridgelines and narrow valleys are smoothed. Treat results as a rough screen, not ground truth.
- **One point per grid square.** A 6-char subsquare is ~5 km wide. The "optimistic" representative point is the highest pixel in that square, which may be in a corner far from your actual position.
- **Washington only.** The DEM and summit list cover W7W; other associations are not included.
- **No Fresnel zones.** Grazing-angle paths flagged as "clear" may still suffer diffraction loss in practice.
- **Static data.** The database reflects the SOTA summit list as of the build date. Re-run `fetch_summits` + `compute_los` to refresh.

---

## Running tests

```sh
pytest tests/
```

Tests cover the Maidenhead encode/decode/enumeration module (20 tests).
