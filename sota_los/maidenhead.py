"""Maidenhead grid square encoding, decoding, and enumeration.

6-character subsquares (e.g. CN86mx):
  - field:    2 uppercase letters  A–R,  20° lon × 10° lat
  - square:   2 digits             0–9,   2° lon ×  1° lat
  - subsquare:2 lowercase letters  a–x,   5′ lon × 2.5′ lat  (2/24° × 1/24°)

Test case (from spec §4):
  encode(46.98, -122.95) == 'CN86mx'
  sw_corner('CN86mx')    == (46.958333…, -123.0)
"""

from __future__ import annotations

from typing import Iterator, Tuple

# Washington state bounds used for grid enumeration (configurable via kwargs)
_WA_LON_MIN = -124.9
_WA_LON_MAX = -116.9
_WA_LAT_MIN = 45.5
_WA_LAT_MAX = 49.0

# Subsquare angular sizes
_SUB_LON = 2.0 / 24   # degrees longitude per subsquare
_SUB_LAT = 1.0 / 24   # degrees latitude per subsquare


def encode(lat: float, lon: float) -> str:
    """Return the canonical 6-char subsquare for a WGS84 lat/lon point."""
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"lat {lat!r} out of range [-90, 90]")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"lon {lon!r} out of range [-180, 180]")

    adj_lon = lon + 180.0
    adj_lat = lat + 90.0

    fi = int(adj_lon / 20.0)
    fj = int(adj_lat / 10.0)
    fi = min(fi, 17)
    fj = min(fj, 17)

    rem_lon = adj_lon - fi * 20.0
    rem_lat = adj_lat - fj * 10.0

    si = int(rem_lon / 2.0)
    sj = int(rem_lat / 1.0)
    si = min(si, 9)
    sj = min(sj, 9)

    rem_lon -= si * 2.0
    rem_lat -= sj * 1.0

    ubi = int(rem_lon * 12.0)   # 1.0 / _SUB_LON = 12
    ubj = int(rem_lat * 24.0)   # 1.0 / _SUB_LAT = 24
    ubi = min(ubi, 23)
    ubj = min(ubj, 23)

    return (
        chr(ord("A") + fi)
        + chr(ord("A") + fj)
        + str(si)
        + str(sj)
        + chr(ord("a") + ubi)
        + chr(ord("a") + ubj)
    )


def sw_corner(grid6: str) -> Tuple[float, float]:
    """Return the SW corner (lat, lon) of a 6-char grid square.

    Accepts any case; canonical form is AB12cd (upper, digits, lower).
    """
    g = _normalise(grid6)
    lon = (ord(g[0]) - ord("A")) * 20.0 - 180.0
    lat = (ord(g[1]) - ord("A")) * 10.0 - 90.0
    lon += int(g[2]) * 2.0
    lat += int(g[3]) * 1.0
    lon += (ord(g[4]) - ord("a")) * _SUB_LON
    lat += (ord(g[5]) - ord("a")) * _SUB_LAT
    return lat, lon


def center(grid6: str) -> Tuple[float, float]:
    """Return the center (lat, lon) of a 6-char grid square."""
    lat, lon = sw_corner(grid6)
    return lat + _SUB_LAT / 2.0, lon + _SUB_LON / 2.0


def bounds(grid6: str) -> Tuple[float, float, float, float]:
    """Return (sw_lat, sw_lon, ne_lat, ne_lon) for a 6-char grid square."""
    lat, lon = sw_corner(grid6)
    return lat, lon, lat + _SUB_LAT, lon + _SUB_LON


def _normalise(grid6: str) -> str:
    if len(grid6) != 6:
        raise ValueError(f"Expected 6-char grid, got {grid6!r}")
    return grid6[:2].upper() + grid6[2:4] + grid6[4:].lower()


def enumerate_wa(
    lon_min: float = _WA_LON_MIN,
    lon_max: float = _WA_LON_MAX,
    lat_min: float = _WA_LAT_MIN,
    lat_max: float = _WA_LAT_MAX,
) -> Iterator[str]:
    """Yield every 6-char subsquare whose area intersects the given bounding box.

    Defaults to the Washington state bounding box used in the spec.
    """
    for fi in range(18):
        f_lon0 = fi * 20.0 - 180.0
        if f_lon0 + 20.0 <= lon_min or f_lon0 >= lon_max:
            continue
        for fj in range(18):
            f_lat0 = fj * 10.0 - 90.0
            if f_lat0 + 10.0 <= lat_min or f_lat0 >= lat_max:
                continue
            for si in range(10):
                s_lon0 = f_lon0 + si * 2.0
                if s_lon0 + 2.0 <= lon_min or s_lon0 >= lon_max:
                    continue
                for sj in range(10):
                    s_lat0 = f_lat0 + sj * 1.0
                    if s_lat0 + 1.0 <= lat_min or s_lat0 >= lat_max:
                        continue
                    for ubi in range(24):
                        sub_lon0 = s_lon0 + ubi * _SUB_LON
                        if sub_lon0 + _SUB_LON <= lon_min or sub_lon0 >= lon_max:
                            continue
                        for ubj in range(24):
                            sub_lat0 = s_lat0 + ubj * _SUB_LAT
                            if sub_lat0 + _SUB_LAT <= lat_min or sub_lat0 >= lat_max:
                                continue
                            yield (
                                chr(ord("A") + fi)
                                + chr(ord("A") + fj)
                                + str(si)
                                + str(sj)
                                + chr(ord("a") + ubi)
                                + chr(ord("a") + ubj)
                            )
