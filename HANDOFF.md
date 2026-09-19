# Handoff — deploy the rebuilt LOS database

Working doc for picking this up in a fresh session. Delete once the database is
deployed.

## TL;DR

The viewshed geotransform bug (every margin sampled from the wrong pixel) was
fixed in `4621631`, and on 2026-09-19 `data/sota_los.sqlite` was rebuilt and
verified. **The current `data/sota_los.sqlite` is good to deploy.** The next step
is the PythonAnywhere upload.

---

## Rebuild result (2026-09-19)

`compute_los --force` took 226 s on 19 workers, then `build_indexes` ran.

| | before (pre-fix) | after |
|---|---|---|
| rows | 11,785,513 | 12,015,310 |
| file size | 406 MB | 379 MB |
| max distance | 649.8 km | 250.0 km |
| grids with rows | 6,103 / 8,142 | 8,142 / 8,142 |
| eastern grids (lon > −118.5) with rows | 0 / 1,674 | 1,674 / 1,674 |

The file shrank even though eastern WA now has rows, because the 250 km cap
removed the ~22% of old rows that were past it.

### Verification

- **Independent line-of-sight trace**: a straight ray trace through the DEM
  (bilinear sampling, the same 4/3-earth curvature term) on 3,000 random pairs
  agrees with the stored verdict (clear/marginal/blocked) on **98.8%** of them, with
  a median signed difference of 0 m and a correlation of 0.984. The pre-fix data
  scored against the same trace managed 88.0% (most paths are blocked either
  way), with a median error of 867 m and a correlation of 0.757. GDAL's
  `GVM_Edge` mode is a raster approximation, not a ray trace, so small
  disagreements near the verdict boundary are expected: within ±200 m of it,
  92.5% agree and the median difference is 0 m.
- **Self-visibility**: every summit has a row for its own grid, and 96.9% of those
  are clear.
- **Known paths** (all clear, stored value = trace value): Rainier from Seattle
  (CN87uo), Rainier and Adams from Yakima (CN96ro), Baker from Seattle. The
  pre-fix DB had Rainier-from-Seattle as marginal (−41 m).
- CLI and the 20 tests pass against the rebuilt DB.

### Also changed

`summits.dem_alt_m` (feeds the §2.3 observer height correction) was only ever
populated by a step that is not in the codebase. `compute_los` now refreshes it
from the DEM on every run via `dem.read_elevation_at_points`. The values it
produces match the ones already stored exactly, so this rebuild's results are
unaffected. It stops a future `fetch_summits --force` from silently dropping the
correction. This change is **uncommitted**.

---

## What needs doing

1. **Commit** the `dem_alt_m` change (`sota_los/viewshed.py`, `sota_los/summits.py`).
2. **Deploy** `data/sota_los.sqlite` (379 MB) to PythonAnywhere. It needs the paid
   tier for `scp`, because the file is too large for the web uploader.
3. **Delete** `data/sota_los.sqlite.legacy` (1.2 GB). It holds pre-migration and
   pre-fix data, so it is wrong anyway.

## Units (unchanged, easy to misread)

| column | unit |
|---|---|
| `margin_max_m`, `margin_mean_m` | whole metres, clamped ±`config.MARGIN_CLAMP_M` (32,000) |
| `distance_hm` | hectometres — divide by 10 for km |
| `bearing_deg` | whole degrees |

## Open questions

- **Positive margins carry no information.** `GVOT_MIN_TARGET_HEIGHT_FROM_DEM`
  outputs `max(required, DEM)`, so for a clear path the required elevation equals
  the DEM value, and `margin_max_m` is exactly `ANTENNA_HEIGHT_M` (+2). No row
  exceeds +2, and 979,852 rows sit at exactly +2. Sorting by margin cannot rank
  clear paths, and "headroom" in the README is only meaningful below zero.
  `margin_mean_m` can exceed +2, but only because the mean elevation differs from
  the centre pixel, not because of real clearance.
- **9,371 rows have NULL `margin_max_m`**, all 246.6–250 km out. The grid centre is
  within 250 km but its max pixel falls outside GDAL's radius. The CLI shows NULL
  as "blocked", which is really "unknown". That is 0.08% of rows.
- **UTM zone 10N** nominally covers lon −126..−120, and eastern WA reaches −116.9.
  The independent trace agrees there too, but distortion is untested beyond that.
