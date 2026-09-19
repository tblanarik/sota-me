"""Unit tests for the Maidenhead module (spec §4)."""

import pytest
from sota_los.maidenhead import bounds, center, encode, enumerate_wa, sw_corner


# ── Spec §4 canonical test cases ──────────────────────────────────────────

def test_encode_spec():
    """Spec: (lat 46.98, lon -122.95) must encode to 'CN86mx'."""
    assert encode(46.98, -122.95) == "CN86mx"


def test_sw_corner_spec():
    """Spec: CN86mx SW corner must be (46.958333…, -123.0)."""
    lat, lon = sw_corner("CN86mx")
    assert abs(lat - 46.958333) < 1e-5, f"lat = {lat}"
    assert abs(lon - (-123.0)) < 1e-9, f"lon = {lon}"


def test_center_spec():
    lat, lon = center("CN86mx")
    assert abs(lat - (46.958333 + 1 / 48)) < 1e-5
    assert abs(lon - (-123.0 + 1 / 24)) < 1e-9


# ── Encode/decode round-trip ───────────────────────────────────────────────

@pytest.mark.parametrize("lat,lon", [
    (46.98, -122.95),   # spec example
    (47.0, -121.5),     # eastern WA
    (48.7, -122.4),     # north WA
    (45.6, -118.5),     # SE corner
    (47.6062, -122.3321),   # Seattle
    (48.42, -119.49),       # Okanogan highlands
])
def test_encode_then_sw_contains_point(lat, lon):
    """Encoded grid's SW corner must be ≤ point < NE corner."""
    g = encode(lat, lon)
    assert len(g) == 6
    sw_lat, sw_lon = sw_corner(g)
    ne_lat = sw_lat + 1 / 24
    ne_lon = sw_lon + 2 / 24
    assert sw_lat <= lat < ne_lat, f"{g}: lat {lat} not in [{sw_lat}, {ne_lat})"
    assert sw_lon <= lon < ne_lon, f"{g}: lon {lon} not in [{sw_lon}, {ne_lon})"


# ── Case insensitivity ────────────────────────────────────────────────────

def test_case_insensitive_sw_corner():
    for variant in ("CN86mx", "cn86mx", "CN86MX", "cN86Mx"):
        lat, lon = sw_corner(variant)
        assert abs(lat - 46.958333) < 1e-5
        assert abs(lon - (-123.0)) < 1e-9


def test_case_insensitive_center():
    c1 = center("CN86mx")
    c2 = center("cn86MX")
    assert c1 == c2


# ── Bounds helper ─────────────────────────────────────────────────────────

def test_bounds_shape():
    sw_lat, sw_lon, ne_lat, ne_lon = bounds("CN86mx")
    assert ne_lat > sw_lat
    assert ne_lon > sw_lon
    assert abs((ne_lat - sw_lat) - 1 / 24) < 1e-12
    assert abs((ne_lon - sw_lon) - 2 / 24) < 1e-12


# ── Enumeration ───────────────────────────────────────────────────────────

def test_enumerate_wa_count():
    grids = list(enumerate_wa())
    assert 7000 < len(grids) < 9500, f"Got {len(grids)} subsquares"


def test_cn86mx_in_enumeration():
    assert "CN86mx" in set(enumerate_wa())


def test_enumeration_no_duplicates():
    grids = list(enumerate_wa())
    assert len(grids) == len(set(grids))


def test_enumeration_all_valid():
    """Every enumerated grid must decode back to a region overlapping WA."""
    from sota_los.maidenhead import _WA_LAT_MIN, _WA_LAT_MAX, _WA_LON_MIN, _WA_LON_MAX
    for g in enumerate_wa():
        sw_lat, sw_lon, ne_lat, ne_lon = bounds(g)
        assert ne_lat > _WA_LAT_MIN
        assert sw_lat < _WA_LAT_MAX
        assert ne_lon > _WA_LON_MIN
        assert sw_lon < _WA_LON_MAX


def test_encode_boundary_values():
    """Test exact boundary of subsquares (no off-by-one at grid edges)."""
    lat, lon = sw_corner("CN86mx")
    assert encode(lat, lon) == "CN86mx"
    # Just inside the NE corner stays in the same cell
    ne_lat = lat + 1 / 24
    ne_lon = lon + 2 / 24
    eps = 1e-10
    assert encode(ne_lat - eps, ne_lon - eps) == "CN86mx"


def test_invalid_grid_length():
    with pytest.raises(ValueError):
        sw_corner("CN86m")
    with pytest.raises(ValueError):
        sw_corner("CN86mxx")


def test_invalid_lat():
    with pytest.raises(ValueError):
        encode(91.0, 0.0)


def test_invalid_lon():
    with pytest.raises(ValueError):
        encode(0.0, 181.0)
