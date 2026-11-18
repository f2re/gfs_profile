"""Meteorological conversions shared by WN3 products; arrays preserve missing data."""
from __future__ import annotations
import numpy as np


def wind_from(u, v):
    u, v = np.asarray(u, dtype=float), np.asarray(v, dtype=float)
    speed = np.hypot(u, v)
    direction = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
    return np.where(np.isfinite(speed) & (speed > 0.05), direction, np.nan)


def relative_humidity(t_c, td_c):
    t_c, td_c = np.asarray(t_c, dtype=float), np.asarray(td_c, dtype=float)
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        rh = 100 * np.exp(17.625 * td_c / (243.04 + td_c) - 17.625 * t_c / (243.04 + t_c))
    return np.where(np.isfinite(t_c) & np.isfinite(td_c), np.clip(rh, 0, 100), np.nan)


def dewpoint_from_specific_humidity(q, pressure_hpa):
    q, p = np.asarray(q, dtype=float), np.asarray(pressure_hpa, dtype=float)
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        e = q * p / (0.622 + 0.378 * q)
        gamma = np.log(e / 6.112)
        td = 243.5 * gamma / (17.67 - gamma)
    return np.where((q > 0) & (q < 1) & (p > 0) & np.isfinite(q), td, np.nan)


def exceedance_probability(values, threshold: float, *, axis=0):
    """Empirical ensemble percentage; missing members are not non-events."""
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    count = finite.sum(axis=axis)
    hits = ((values > threshold) & finite).sum(axis=axis)
    result = np.full(np.shape(count), np.nan, dtype=float)
    np.divide(hits * 100.0, count, out=result, where=count > 0)
    return result, count
