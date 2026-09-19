# SOTA W7W Line-of-Sight Lookup — Project Spec & Agent Instructions

## 1. What we're building

A quick-and-dirty, **precomputed** lookup tool for the amateur radio Summits on the Air (SOTA) program. Given a 6-character Maidenhead grid square (e.g. `CN86mx`), it instantly returns every Washington (W7W association) SOTA summit, each with a rough line-of-sight verdict, distance, and bearing.

The question it answers: *"Is there obviously terrain between me and that summit?"* That's all. It is a sanity-check tool for chasers working VHF/UHF, not a propagation model.

All the expensive work happens once, offline. The end-user query is a single indexed SQLite lookup.

### Non-goals

- Precision. A 90 m DEM and one representative point per grid square are acceptable.
- Fresnel zones, diffraction, or propagation modeling.
- Coverage outside Washington (for now).
- A web UI. A CLI is enough for v1. Keep the data layer clean so a UI can come later.

---

## 2. Approach (read this before writing code)

### 2.1 Grid squares as single points

A 6-character Maidenhead subsquare is 5′ of longitude × 2.5′ of latitude (about 6.3 × 4.6 km in Washington). Each subsquare is represented by one point, computed two ways and stored side by side:

- **Max (optimistic):** the location and elevation of the highest DEM pixel in the subsquare. This answers "is there anywhere in this square with a shot?" It is the primary answer.
- **Mean (typical):** the mean DEM elevation of the subsquare, placed at the subsquare's center. This is the pessimistic companion value.

### 2.2 One viewshed per summit, in "required height" mode

Do **not** trace millions of individual paths. Instead, for each summit, run one GDAL viewshed using the mode that outputs, for every pixel, the **minimum elevation a target at that pixel would need in order to see the observer**:

- CLI: `gdal_viewshed -om DEM ...`
- Python: `gdal.ViewshedGenerate(..., heightMode=gdal.GVOT_MIN_TARGET_HEIGHT_FROM_DEM)`

For each subsquare, read that raster at the representative pixel and compute:

```
margin_m = (representative_elevation + ANTENNA_HEIGHT_M) - required_elevation_at_that_pixel
```

- `margin_m >= 0` means clear line of sight.
- `MARGINAL_THRESHOLD_M <= margin_m < 0` means marginal (VHF may diffract over the obstruction; the DEM isn't exact anyway).
- `margin_m < MARGINAL_THRESHOLD_M` means blocked.

Store the numeric margin, not a boolean, so thresholds can be tuned later without recomputing anything.

> ⚠️ **Verify before relying on it:** confirm empirically what the `-om DEM` output values mean in the installed GDAL version (absolute elevation vs. something else, and how nodata/out-of-range pixels are encoded). Write a small test comparing it against a `-om NORMAL` run from the same observer: pixels marked visible in NORMAL mode should have required elevation ≤ DEM elevation + target height. Document what you find in the README.

### 2.3 Observer height correction

DEMs smooth peaks down, so the DEM value at a summit's coordinates is usually lower than the true summit. For each summit, set the viewshed observer height (height above ground) to:

```
observer_height = max(summit.alt_m - dem_elevation_at_summit, 0) + OBSERVER_ANTENNA_HEIGHT_M
```

Here `summit.alt_m` is the official SOTA altitude.

### 2.4 Earth curvature

Use GDAL's curvature coefficient default of `0.85714`, which is the standard 4/3-earth radio refraction assumption.

---

## 3. Data sources

### 3.1 Summit list

- SOTA publishes a full summits list as CSV: `https://www.sotadata.org.uk/summitslist.csv` (it may redirect to `storage.sota.org.uk`).
- The **first line is a title/date line**, not the header. Skip it.
- Expected columns include `SummitCode, AssociationName, RegionName, SummitName, AltM, AltFt, GridRef1, GridRef2, Longitude, Latitude, Points, BonusPoints, ValidFrom, ValidTo, ...`. **Verify the actual header**; don't trust this list blindly.
- Filter to `SummitCode` starting with `W7W/`, and to summits that are currently valid (`ValidTo` in the future; dates are `dd/mm/yyyy`).
- Cache the raw download in `data/raw/` with its download date.

### 3.2 DEM

- Use **Copernicus GLO-90** (90 m global DSM), which is public on AWS Open Data (`s3://copernicus-dem-90m/`, no auth, also reachable over HTTPS). It is made of 1°×1° Cloud-Optimized GeoTIFF tiles. **Verify the tile naming scheme** from the bucket listing rather than guessing.
- Extent: Washington bounding box plus a buffer, roughly **lat 45.25–49.25 N, lon 125.25–116.5 W**. Because both summits and targets are inside Washington, obstructions along the paths lie inside this box too.
- Mosaic the tiles, then **reproject to a meter-based CRS** before running viewsheds. The viewshed math assumes projected, square pixels.
  - Use `EPSG:32610` (UTM 10N) at a 90 m pixel size. The distortion at the eastern edge of the state is well under 1% and acceptable for this tool.
- Save the result as `data/processed/dem_wa_utm10_90m.tif`.

---

## 4. Maidenhead details

- Encoding: field (A–R, 20°×10°), square (0–9, 2°×1°), subsquare (a–x, 5′×2.5′).
- Canonical formatting: `CN86mx` (field uppercase, subsquare lowercase). Queries must be **case-insensitive**.
- Enumerate every subsquare whose area intersects the Washington bounding box (lat 45.5–49.0 N, lon 124.9–116.9 W). That's roughly 8,000 subsquares. Subsquares over the ocean or outside the state are fine to keep. Drop any with no valid DEM pixels.

**Test case:** the point `(lat 46.98, lon -122.95)` must encode to `CN86mx`, and `CN86mx` must decode to a southwest corner of `(46.958333, -123.0)`.

---

## 5. Pipeline

Build this as idempotent stages. Each stage checks for its output and skips if it's already present, unless `--force` is passed.

1. **`fetch_summits`**: download and cache the CSV, filter to W7W and currently valid, and write the `summits` table.
2. **`fetch_dem`**: download the needed GLO-90 tiles, mosaic them, clip to the extent, and reproject to UTM 10N at 90 m.
3. **`build_grid`**:
   - Compute lat/lon for every DEM pixel center (inverse-transform the UTM grid).
   - Assign each pixel a subsquare ID, and store that as a raster or array for reuse.
   - Per subsquare, compute: `elev_max`, the row/col of the max pixel, `elev_mean`, and the row/col of the pixel nearest the subsquare center.
   - Vectorize this with numpy (sort by ID, `np.maximum.reduceat`, etc.). Don't loop over pixels in Python.
   - Write the `grids` table.
4. **`compute_los`**: for each summit, run the viewshed in required-height mode into an in-memory raster (MEM driver or `/vsimem/`), with max distance `MAX_DISTANCE_M`. Then, vectorized over all subsquares:
   - Read the required elevation at each subsquare's max pixel and at its center pixel.
   - Compute `margin_max_m` and `margin_mean_m`.
   - Compute `distance_km` and `bearing_deg` (initial bearing **from the subsquare center to the summit**) using `pyproj.Geod` on WGS84.
   - Skip subsquares that are out of range or nodata.
   - Optionally drop rows where `margin_max_m < PRUNE_BELOW_M`, to save space.
   - **Parallelize across summits** with `multiprocessing`, one DEM handle per worker. Show progress.
   - Write results in batches inside transactions.
5. **`build_indexes`**: create indexes and run `VACUUM` / `ANALYZE`.

Expected scale: about 2,000 summits × about 8,000 subsquares. Each viewshed on the 90 m DEM should take seconds, so a full run should take an hour or two on a laptop and less when parallelized. If it's dramatically slower, investigate before continuing.

---

## 6. Output schema (SQLite: `data/sota_los.sqlite`)

```sql
CREATE TABLE summits (
  summit_ref   TEXT PRIMARY KEY,   -- e.g. 'W7W/XX-001'
  name         TEXT,
  region       TEXT,
  lat          REAL,
  lon          REAL,
  alt_m        REAL,               -- official SOTA altitude
  dem_alt_m    REAL,               -- DEM value at summit (for diagnostics)
  points       INTEGER
);

CREATE TABLE grids (
  grid6        TEXT PRIMARY KEY,   -- canonical 'CN86mx'
  center_lat   REAL,
  center_lon   REAL,
  elev_max_m   REAL,
  max_lat      REAL,
  max_lon      REAL,
  elev_mean_m  REAL
);

CREATE TABLE los (
  grid6          TEXT NOT NULL,
  summit_ref     TEXT NOT NULL,
  margin_max_m   REAL,             -- primary answer
  margin_mean_m  REAL,             -- pessimistic companion
  distance_km    REAL,
  bearing_deg    REAL,             -- from grid center to summit
  PRIMARY KEY (grid6, summit_ref)
) WITHOUT ROWID;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
-- record: build date, summit list date, DEM source, CRS, all config values, GDAL version
```

---

## 7. Query CLI

```
sota-los CN86mx [--status clear|marginal|all] [--max-km 150] [--sort distance|margin|bearing] [--mean] [--json]
```

- The default output is a readable table: summit ref, name, verdict (✅ clear / ⚠️ marginal / ❌ blocked), margin, distance, bearing, and points.
- `--mean` judges the verdict on `margin_mean_m` instead of `margin_max_m`.
- `--json` produces machine-readable output for a future UI.
- The default is to hide blocked summits.
- Handle an invalid or unknown grid square with a clear error message.

---

## 8. Configuration (single `config.py` or `config.toml`)

| Name | Default | Meaning |
|---|---|---|
| `ANTENNA_HEIGHT_M` | 2 | Chaser antenna height above the representative point |
| `OBSERVER_ANTENNA_HEIGHT_M` | 2 | Activator antenna height above the true summit |
| `MARGINAL_THRESHOLD_M` | -50 | Margins between this value and 0 are "marginal" |
| `MAX_DISTANCE_M` | 250000 | Viewshed radius |
| `PRUNE_BELOW_M` | None | Optionally drop hopeless rows at build time |
| `CURVATURE_COEFF` | 0.85714 | 4/3-earth refraction |
| `DEM_PIXEL_M` | 90 | Reprojected DEM resolution |
| `TARGET_CRS` | EPSG:32610 | Projected CRS for viewsheds |

---

## 9. Tech stack & repo layout

- Python 3.11+.
- **Use conda-forge (or mamba) for GDAL**; installing GDAL with pip is painful. Provide an `environment.yml`.
- Libraries: `gdal`, `numpy`, `pyproj`, `requests`. Use `rasterio` only if it genuinely simplifies something. Use the standard library `sqlite3`, and `pytest` for tests.

```
.
├── PROJECT_SPEC.md          # this file
├── README.md                # setup, build, query, findings from §2.2 verification
├── environment.yml
├── config.py
├── sota_los/
│   ├── maidenhead.py        # encode/decode/enumerate, fully unit-tested
│   ├── summits.py
│   ├── dem.py
│   ├── grid.py
│   ├── viewshed.py
│   ├── db.py
│   ├── build.py             # pipeline entry point: python -m sota_los.build [--stage X] [--force]
│   └── cli.py               # sota-los query command
├── tests/
└── data/                    # gitignored: raw/, processed/, sota_los.sqlite
```

---

## 10. Build order & checkpoints

Work in this order. Stop and report at each checkpoint before moving on.

1. **Maidenhead module + tests.** Checkpoint: all encode/decode/enumeration tests pass, including the §4 test case.
2. **Summit fetch.** Checkpoint: report the W7W summit count and print a few rows.
3. **DEM fetch + reprojection.** Checkpoint: report raster size and CRS, and spot-check the DEM elevation at 3–4 summits against their official `AltM`. Expect the DEM to read somewhat low; big differences mean a coordinate or CRS bug.
4. **Viewshed mode verification (§2.2).** Checkpoint: document the output semantics.
5. **Grid build.** Checkpoint: report the subsquare count, and the `elev_max` / `elev_mean` values for `CN86mx`.
6. **Single-summit LOS run.** Pick one prominent summit and run it end to end. Checkpoint: a summit's own subsquare should show a non-negative margin, and results should look geographically plausible (e.g. squares behind a major ridge are blocked).
7. **Full parallel run.** Checkpoint: report runtime, row count, and database size.
8. **Query CLI.** Checkpoint: show sample output for `CN86mx`.

### General guidance for the agent

- Prefer vectorized numpy over Python loops for anything per-pixel or per-subsquare.
- Don't hardcode anything this spec marks as "verify." Check it, then record what you found.
- Keep stages independent so a failed full run can resume without redoing downloads or the grid build.
- If something in this spec turns out to be wrong (a URL, a column name, GDAL behavior), fix it in the code **and** add a note to the README rather than silently working around it.
