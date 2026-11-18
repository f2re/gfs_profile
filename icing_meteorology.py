"""Messenger-independent icing-environment diagnostics for GFS pressure levels.

M8D20 is the explicitly versioned historical Godske diagnostic:
``-20 <= T < 0 degC`` and ``T + 8 * (T - Td_water) <= 0``.  Layer
thickness is a geometric diagnostic, not ice accretion or icing severity.
Liquid-water products use CLWMR and hydrostatic, piecewise-linear integration.
No missing or below-ground interval is extrapolated.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from icing_config import ICING_PRODUCTS, METHOD_VERSION

WATER_THRESHOLD_GM3 = 0.001
EPSILON = 0.622
RD = 287.05
G0 = 9.80665

PRODUCT_TITLES = ICING_PRODUCTS
PRODUCT_UNITS = {
    "slw_level": "г/м³", "godske_sum": "гп.м", "godske_max": "гп.м",
    "godske_base": "гп.м", "godske_top": "гп.м", "godske_count": "слоёв",
    "slw_max": "г/м³", "slw_height": "гп.м", "slwp": "г/м²",
    "agreement": "категория согласия",
}
AGREEMENT_LABELS = (
    "Оба признака отсутствуют", "Только условия по Годске", "Только переохлаждённая вода",
    "Совпадение по высоте", "Признаки в разных слоях",
)


def vapour_pressure(q: np.ndarray, p_pa: np.ndarray) -> np.ndarray:
    """Vapour pressure from specific humidity (not dry-air mixing ratio)."""
    return q * p_pa / (EPSILON + (1.0 - EPSILON) * q)


def dewpoint_water(e_pa: np.ndarray) -> np.ndarray:
    """Magnus inversion over liquid water, returning degC; dry/invalid is NaN."""
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.log(np.where(e_pa > 0, e_pa, np.nan) / 610.94)
        return 243.04 * a / (17.625 - a)


def linear_intervals(constraints, valid: np.ndarray, strict=()):
    """Intersect linear c(z)<=0 constraints on adjacent valid samples.

    Strict constraints reject an identically zero interval. Endpoints themselves
    have zero measure, so the same root fractions apply to < and <= crossings.
    """
    lo = np.zeros(valid[:-1].shape, dtype=float)
    hi = np.ones_like(lo)
    ok = valid[:-1] & valid[1:]
    for index, constraint in enumerate(constraints):
        constraint = np.asarray(constraint, dtype=float)
        c0, c1 = constraint[:-1], constraint[1:]
        delta = c1 - c0
        ok &= np.isfinite(c0) & np.isfinite(c1)
        constant = np.abs(delta) < 1e-12
        ok &= ~(constant & ((c0 >= 0) if index in strict else (c0 > 0)))
        root = np.divide(-c0, delta, out=np.zeros_like(delta), where=~constant)
        hi = np.where(delta > 1e-12, np.minimum(hi, root), hi)
        lo = np.where(delta < -1e-12, np.maximum(lo, root), lo)
    lo, hi = np.clip(lo, 0, 1), np.clip(hi, 0, 1)
    ok &= hi > lo + 1e-12
    return np.where(ok, lo, np.nan), np.where(ok, hi, np.nan)


def layer_products(z: np.ndarray, valid: np.ndarray, constraints, strict=()):
    """Preserve disjoint intervals and mark their observed-range censoring."""
    lo, hi = linear_intervals(constraints, valid, strict)
    dz = np.diff(z, axis=0)
    if np.any(valid[:-1] & valid[1:] & (dz <= 0)):
        raise ValueError("Высота должна возрастать при уменьшении давления")
    active = np.isfinite(lo) & np.isfinite(hi) & (dz > 0)
    base = np.where(active, z[:-1] + lo * dz, np.nan)
    top = np.where(active, z[:-1] + hi * dz, np.nan)
    lengths = np.where(active, top - base, 0.0)
    shape = z.shape[1:]
    count = np.zeros(shape, dtype=np.int16)
    current_length = np.zeros(shape)
    max_length = np.zeros(shape)
    first_base = np.full(shape, np.nan)
    first_top = np.full(shape, np.nan)
    highest_top = np.full(shape, np.nan)
    first_base_open = np.zeros(shape, dtype=bool)
    first_top_open = np.zeros(shape, dtype=bool)
    previous_active = np.zeros(shape, dtype=bool)
    previous_top = np.full(shape, np.nan)
    node_condition = valid.copy()
    for index, constraint in enumerate(constraints):
        node_condition &= np.isfinite(constraint) & (
            (constraint < 0) if index in strict else (constraint <= 0)
        )
    for k in range(len(z) - 1):
        continuation = active[k] & previous_active & np.isclose(base[k], previous_top, rtol=0, atol=1e-7)
        new_layer = active[k] & ~continuation
        count += new_layer
        current_length = np.where(active[k], np.where(continuation, current_length + lengths[k], lengths[k]), 0)
        max_length = np.maximum(max_length, current_length)
        is_first = active[k] & (count == 1)
        first_base = np.where(new_layer & (count == 1), base[k], first_base)
        first_top = np.where(is_first, top[k], first_top)
        left_unavailable = np.ones(shape, dtype=bool) if k == 0 else ~valid[k - 1]
        right_unavailable = np.ones(shape, dtype=bool) if k + 2 == len(z) else ~valid[k + 2]
        start_open = (lo[k] <= 1e-12) & node_condition[k] & left_unavailable
        end_open = (hi[k] >= 1 - 1e-12) & node_condition[k + 1] & right_unavailable
        first_base_open = np.where(new_layer & (count == 1), start_open, first_base_open)
        first_top_open = np.where(is_first, end_open, first_top_open)
        highest_top = np.where(active[k], top[k], highest_top)
        previous_active, previous_top = active[k], top[k]
    any_valid = valid.any(axis=0)
    bottom = np.argmax(valid, axis=0)
    highest = len(z) - 1 - np.argmax(valid[::-1], axis=0)
    take = lambda a, indices: np.take_along_axis(a, indices[None], axis=0)[0]
    lower_open = take(node_condition, bottom) & any_valid
    upper_open = take(node_condition, highest) & any_valid
    interior_missing = np.zeros(shape, dtype=bool)
    for k in range(1, len(z) - 1):
        interior_missing |= (~valid[k]) & (bottom < k) & (highest > k) & any_valid
    coverage = (valid[:-1] & valid[1:]).any(axis=0)
    return {
        "sum_gpm": np.where(coverage, lengths.sum(axis=0), np.nan),
        "max_layer_gpm": np.where(coverage, max_length, np.nan),
        "base_first_gpm": first_base, "top_first_gpm": first_top,
        "base_first_open": first_base_open, "top_first_open": first_top_open,
        "top_highest_gpm": highest_top, "envelope_gpm": highest_top - first_base,
        "n_layers": np.where(coverage, count, np.nan),
        "lower_open": lower_open, "upper_open": upper_open,
        "interior_missing": interior_missing, "has_coverage": coverage,
        "segment_base_gpm": base, "segment_top_gpm": top,
        "segment_lo": lo, "segment_hi": hi,
    }


def cold_integrals(z, p, t, qc, lwc, valid):
    """Hydrostatic and height integrals over only valid, strictly cold portions."""
    lo, hi = linear_intervals([t], valid, strict=(0,))
    dz = np.diff(z, axis=0)
    dp = (p[:-1] - p[1:])[:, None, None]
    ok = np.isfinite(lo) & np.isfinite(hi) & (dz > 0)

    def integrate(values, measure):
        start, delta = values[:-1], values[1:] - values[:-1]
        pieces = (start * (hi - lo) + 0.5 * delta * (hi**2 - lo**2)) * measure
        return np.where(ok, pieces, 0).sum(axis=0)

    coverage = (valid[:-1] & valid[1:]).any(axis=0)
    return (
        np.where(coverage, integrate(qc, dp) * 1000 / G0, np.nan),
        np.where(coverage, integrate(lwc, dz), np.nan),
    )


def compute_icing_fields(
    pressure_hpa, temperature_k, specific_humidity, height_gpm, cloud_water_kgkg,
    surface_pressure_pa, surface_height_m,
) -> dict[str, np.ndarray]:
    """Compute diagnostics from aligned (level, y, x) GFS pressure-level fields.

    CLWMR is interpreted per mass of dry air and vapour; condensate loading is
    neglected in density/hydrostatic integration. Invalid physical samples are
    masked separately for thermodynamic and water diagnostics. At least two
    adjacent samples are necessary for layer geometry and column integrals.
    """
    p = np.asarray(pressure_hpa, dtype=float)
    t_k, q, z, qc = (np.asarray(a, dtype=float) for a in (
        temperature_k, specific_humidity, height_gpm, cloud_water_kgkg,
    ))
    ps, zs = np.asarray(surface_pressure_pa, dtype=float), np.asarray(surface_height_m, dtype=float)
    if p.ndim != 1 or p.size == 0 or not np.all(np.isfinite(p)) or np.any(p <= 0) or np.any(np.diff(p) >= 0):
        raise ValueError("Уровни давления должны быть конечными, положительными и строго убывать")
    if t_k.ndim != 3 or t_k.shape[0] != len(p) or min(t_k.shape) < 1:
        raise ValueError("Поля должны иметь форму (уровень, широта, долгота)")
    if any(a.shape != t_k.shape for a in (q, z, qc)) or ps.shape != t_k.shape[1:] or zs.shape != ps.shape:
        raise ValueError("Формы полей обледенения не совпадают")
    pressure = (p * 100)[:, None, None]
    surface_valid = np.isfinite(ps) & (ps > 0) & np.isfinite(zs)
    pressure_above = (pressure <= ps) & surface_valid
    above = pressure_above & np.isfinite(z) & (z >= zs - 1.0)
    base_valid = above & np.isfinite(t_k) & (t_k > 0) & np.isfinite(q) & (q >= 0) & (q < 1)
    if np.any(base_valid[:-1] & base_valid[1:] & (np.diff(z, axis=0) <= 0)):
        raise ValueError("Геопотенциальная высота не возрастает вверх по профилю")
    t = t_k - 273.15
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        e = vapour_pressure(q, pressure)
        td = dewpoint_water(e)
        rh_water = 100 * e / (610.94 * np.exp(17.625 * t / (243.04 + t)))
        density = pressure / (RD * t_k * (1 + (1 / EPSILON - 1) * q))
        lwc = 1000 * density * qc
    thermo_valid = base_valid & np.isfinite(td)
    water_valid = base_valid & np.isfinite(qc) & (qc >= 0) & (qc < 1) & np.isfinite(lwc)
    lwc = np.where(water_valid, lwc, np.nan)
    g = t + 8 * (t - td)
    godske = layer_products(z, thermo_valid, [g, t, -20.0 - t], strict=(1,))
    liquid = layer_products(z, water_valid, [t, WATER_THRESHOLD_GM3 - lwc], strict=(0,))
    slwp_p, slwp_z = cold_integrals(z, p * 100, t, qc, lwc, water_valid)
    cold = water_valid & (t < 0)
    has_cold = cold.any(axis=0)
    candidates = np.where(cold, lwc, -np.inf)
    maximum = np.where(has_cold, np.max(candidates, axis=0), np.nan)
    # For tied maxima choose the lowest original pressure-level sample.
    imax = np.argmax(candidates, axis=0)
    informative = has_cold & (maximum > 0)
    maximum_height = np.where(informative, np.take_along_axis(z, imax[None], axis=0)[0], np.nan)
    maximum_pressure = np.where(informative, p[imax], np.nan)
    common = thermo_valid & water_valid
    overlap = layer_products(z, common, [g, t, -20.0 - t, WATER_THRESHOLD_GM3 - lwc], strict=(1,))
    comparison_covered = overlap["has_coverage"] & ~(pressure_above & ~common).any(axis=0)
    godske_present, water_present = godske["sum_gpm"] > 0, liquid["sum_gpm"] > 0
    agreement = np.zeros(ps.shape, dtype=float)
    agreement[godske_present & ~water_present] = 1
    agreement[water_present & ~godske_present] = 2
    agreement[godske_present & water_present] = 4
    agreement[overlap["sum_gpm"] > 0] = 3
    agreement[~comparison_covered] = np.nan
    bottom = np.argmax(thermo_valid, axis=0)
    lowest = np.where(thermo_valid.any(axis=0), np.take_along_axis(z, bottom[None], axis=0)[0], np.nan)
    result = {
        "pressure_hpa": p, "temperature_C": t, "dewpoint_water_C": td,
        "specific_humidity_kgkg": q, "water_RH_pct": rh_water,
        "height_gpm": z, "cloud_water_kgkg": qc, "cloud_lwc_gm3": lwc,
        "valid_above_ground": thermo_valid, "water_valid_above_ground": water_valid,
        "surface_pressure_Pa": ps, "surface_height_m": zs, "godske_margin_C": g,
        "slwp_gm2": slwp_p, "slwp_height_approx_gm2": slwp_z,
        "max_slwc_gm3": maximum, "max_slwc_height_gpm": maximum_height,
        "max_slwc_pressure_hpa": maximum_pressure, "SLWC_has_cold_levels": has_cold,
        "lowest_available_height_gpm": lowest,
        "unresolved_bottom_depth_m": np.maximum(lowest - zs, 0),
        "SLWP_has_coverage": liquid["has_coverage"],
        "SLWP_interior_missing": liquid["interior_missing"],
        "agreement": agreement, "agreement_overlap_gpm": overlap["sum_gpm"],
        "agreement_has_coverage": comparison_covered,
    }
    for prefix, values in (("godske20", godske), ("slw001", liquid)):
        result.update({f"{prefix}_{key}": value for key, value in values.items()})
    return result


def select_icing_field(fields: Mapping[str, np.ndarray], product: str, level_hpa: int = 850) -> np.ndarray:
    """Return the displayed/exported field; unresolved first boundaries are NaN."""
    if product not in PRODUCT_TITLES:
        raise ValueError("Неизвестный продукт обледенения")
    if product == "slw_level":
        indices = np.flatnonzero(np.isclose(fields["pressure_hpa"], float(level_hpa), atol=1e-7, rtol=0))
        if not len(indices):
            raise ValueError(f"Уровень {level_hpa} гПа отсутствует в данных")
        k = int(indices[0])
        valid = fields["water_valid_above_ground"][k]
        return np.where(valid, np.where(fields["temperature_C"][k] < 0, fields["cloud_lwc_gm3"][k], 0.0), np.nan)
    key = {
        "godske_sum": "godske20_sum_gpm", "godske_max": "godske20_max_layer_gpm",
        "godske_base": "godske20_base_first_gpm", "godske_top": "godske20_top_first_gpm",
        "godske_count": "godske20_n_layers", "slw_max": "max_slwc_gm3",
        "slw_height": "max_slwc_height_gpm", "slwp": "slwp_gm2", "agreement": "agreement",
    }[product]
    value = np.array(fields[key], dtype=float, copy=True)
    if product in {"godske_base", "godske_top"}:
        side = "base" if product == "godske_base" else "top"
        value[np.asarray(fields[f"godske20_{side}_first_open"], dtype=bool)] = np.nan
    return value
