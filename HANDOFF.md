# Handoff — viewshed geotransform bug + compact schema

Working doc for picking this up in a fresh session. Delete once the rebuild is
done and verified.

## TL;DR

A one-line bug corrupted **every margin value** in the database. It is fixed in
the code but the database has not been rebuilt yet. **Do not deploy the current
`data/sota_los.sqlite`.** The next step is a ~1 hour recompute.

---

## What was found

### 1. Viewshed geotransform mismatch (critical, fixed in code, DB not rebuilt)

`run_viewshed_for_summit` in `sota_los/viewshed.py` returned the viewshed array
paired with the **source DEM's** geotransform. With `maxDistance` set,
`gdal.ViewshedGenerate` clips its output to a window around the observer, so the
two have different origins and sizes.

Measured on W7W/WE-037 (Needham Hill):

| | size | origin easting |
|---|---|---|
| Source DEM | 7216 × 5138 px | 323,439 |
| Viewshed | 3486 × 4943 px | 659,139 |

Offset = 335,700 m = **3,730 columns of error in every pixel lookup**. The
summit's own pixel should be column 2,779; the code computed 6,509, past the
array's 3,486 width.

Three symptoms, one cause:

- **Every margin is wrong** wherever the bad index still landed in bounds.
- **2,039 of 8,142 grids** (all east of ~lon −118.5) had zero rows — their
  indices fell outside the array entirely.
- **22.6% of rows exceeded the 250 km cap** (max 649.8 km). `distance_hm` came
  from `geod.inv` and was always correct; the margin came from an unrelated
  pixel. The two were never connected, which is why the cap looked unenforced.

**Fix applied:** return `vs_ds.GetGeoTransform()` instead of the source `gt`.

**Verified on one summit:** the summit's own pixel now reports 874.1 m required
against an 879 m summit (a summit sees itself), and eastern grids produce 1,674
rows where they produced 0. Only spot-checked — the full rebuild is what
actually proves it.

### 2. 250 km cap now enforced explicitly

Added a `dist_m[i] > config.MAX_DISTANCE_M` guard in `_compute_los_for_summit`.
Still needed after the geotransform fix because GDAL's clip window is
rectangular — its corners reach ~1.41 × maxDistance (~354 km).

User confirmed 250 km is the intended cap.

### 3. Compact schema migration (done, working)

Database went 1.21 GB → 406 MB with all 11,785,513 pairs retained, driven by a
PythonAnywhere storage limit.

- Dense integer IDs replace text keys (`grid6` + `summit_ref` cost 18 bytes/row)
- Measures stored as quantised `INTEGER`; SQLite's varint encoding spends 1 byte
  on near-zero margins and 2 on deep ones, putting precision at the verdict
  boundary

Row size ~55 bytes → ~18. No index on `los.grid_id` — the `WITHOUT ROWID`
primary key `(grid_id, summit_id)` already covers it, and adding one costs
224 MB for nothing.

**Units — easy to misread:**

| column | unit |
|---|---|
| `margin_max_m`, `margin_mean_m` | whole metres, clamped ±`config.MARGIN_CLAMP_M` (32,000) |
| `distance_hm` | hectometres — divide by 10 for km |
| `bearing_deg` | whole degrees |

Read queries join on integer IDs and alias `distance_hm / 10.0 AS distance_km`
so the Jinja templates need no changes.

Rounding shifts ~5,869 rows (0.05%) across a verdict boundary by up to 0.5 m —
accepted noise on a 90 m DEM, not a bug to chase.

---

## What needs doing

1. **Rebuild the LOS data** (the actual next step):

   ```bash
   .venv/bin/python -m sota_los.build --stage compute_los --force
   ```

   ~15 s per viewshed × 2,762 summits across available cores ≈ 1 hour. Then:

   ```bash
   .venv/bin/python -m sota_los.build --stage build_indexes
   ```

2. **Verify the rebuild** before trusting it:
   - Eastern WA grids (lon > −118.5) must have rows — they had zero before
   - No row should exceed 250 km: `SELECT MAX(distance_hm) FROM los` ≤ 2500
   - Spot-check a summit sees itself: required elev ≈ summit alt
   - Sanity-check a few known-clear paths against the CLI

3. **Expect the DB to grow** past 406 MB despite the 250 km cap — eastern
   Washington gains ~2,039 grids' worth of rows it never had.

4. **Then** deploy to PythonAnywhere (paid tier needed for `scp`; the file is
   too large for the web uploader).

5. Delete `data/sota_los.sqlite.legacy` (1.2 GB) once satisfied — it holds the
   pre-migration data, which is also pre-fix and therefore wrong anyway.

---

## Current state

**Uncommitted** (nothing has been committed this session):

```
 M config.py            MARGIN_CLAMP_M added
 M flask_app.py         queries join on integer IDs
 M sota_los/build.py    single idx_los_summit index
 M sota_los/cli.py      queries join on integer IDs
 M sota_los/db.py       compact schema
 M sota_los/grid.py     grid_id assignment
 M sota_los/summits.py  summit_id assignment
 M sota_los/viewshed.py geotransform fix + 250 km guard + quantised writes
?? sota_los/migrate.py  legacy -> compact converter (idempotent)
```

Databases:

- `data/sota_los.sqlite` — 388 MB, compact schema, **margins are wrong**
- `data/sota_los.sqlite.legacy` — 1.2 GB, old schema, margins also wrong

Tests: 20 passing. Web app and CLI both work against the compact schema.

## Open questions

- `summits.dem_alt_m` is never populated. `summits.py:103` says "filled in by dem
  stage" but nothing writes it, so `viewshed.py` always falls back to `alt_m` via
  `COALESCE`. May be intentional; worth a look since it feeds the observer height
  correction.
- The DEM is reprojected to UTM zone 10N (`EPSG:32610`), which nominally covers
  lon −126..−120. Eastern WA reaches −116.9, well out of zone. It did not cause
  this bug — the DEM has valid data throughout and the raster covers it — but
  distortion out there is worth a sanity check once the rebuild lands.
