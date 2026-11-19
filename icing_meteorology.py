"""Messenger-independent icing-environment diagnostics for GFS pressure levels.

M8D20 is the explicitly versioned historical Godske diagnostic:
``-20 <= T < 0 degC`` and ``T + 8 * (T - Td_water) <= 0``.  Layer
thickness is a geometric diagnostic, not ice accretion or icing severity.
User-facing layers additionally require cloud cover and liquid water in the
same vertical interval. The SFIP adaptation is screened by these conditions;
its 0--100 scale is an index, not a calibrated probability or severity.
Raw thermodynamic fields remain available for comparison and reproducibility.
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
    "agreement": "категория согласия", "composite": "индекс 0–100",
}
AGREEMENT_LABELS = (
    "Оба признака отсутствуют", "Годске без облачной воды", "Облачная вода без Годске",
    "Совпадение в облачном слое", "Признаки в разных слоях",
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


def sfip_memberships(temperature_c, rh_water_pct, liquid_kgkg, upward_velocity_ms):
    """Published SFIP membership curves, with explicit input units.

    Temperature/water/velocity knots: Brown, Morcrette & Stretton, EGU2018,
    slide 5. RH quadratic: Morcrette et al. (2019), also reproduced in
    NORCE Energy & Technology report 2-2025, p.36. See the versioned method
    specification for provenance and the additional cloud/liquid screening.
    The water membership uses mass concentration in g/kg, NOT g/m3. The
    velocity membership uses m/s, positive upwards, NOT GFS omega in Pa/s.
    """
    t, rh, ql, w = np.broadcast_arrays(
        np.asarray(temperature_c, dtype=float), np.asarray(rh_water_pct, dtype=float),
        np.asarray(liquid_kgkg, dtype=float), np.asarray(upward_velocity_ms, dtype=float),
    )
    mt = np.interp(t, [-28.0, -12.0, -1.0, 1.0], [0.0, 1.0, 1.0, 0.0], left=0.0, right=0.0)
    mrh = np.clip((rh - 60.0) / 35.0, 0.0, 1.0) ** 2
    mw = np.interp(w, [-0.10, 0.0, 0.05], [-0.4, 0.0, 1.0], left=-0.4, right=1.0)
    ml = np.clip(1000.0 * ql / 0.4, 0.0, 1.0)
    return mt, mrh, mw, ml


def sfip_index(temperature_c, rh_water_pct, liquid_kgkg, upward_velocity_ms):
    """Unscreened SFIP adaptation on 0--100; invalid inputs remain unknown."""
    mt, mrh, mw, ml = sfip_memberships(
        temperature_c, rh_water_pct, liquid_kgkg, upward_velocity_ms,
    )
    values = 100.0 * np.clip(mt * (0.35 * mrh + 0.20 * mw + 0.45 * ml), 0.0, 1.0)
    valid = (np.isfinite(temperature_c) & np.isfinite(rh_water_pct) &
             np.isfinite(liquid_kgkg) & (np.asarray(liquid_kgkg) >= 0) &
             np.isfinite(upward_velocity_ms))
    return np.where(valid, values, np.nan)


def _layer_water_path(qc, pressure_pa, geometry):
    """Hydrostatic liquid mass ONLY inside the selected intersected intervals."""
    lo, hi = geometry["segment_lo"], geometry["segment_hi"]
    dp = (pressure_pa[:-1] - pressure_pa[1:])[:, None, None]
    piece = (qc[:-1] * (hi - lo) + 0.5 * np.diff(qc, axis=0) * (hi**2 - lo**2)) * dp * 1000 / G0
    active = np.isfinite(lo) & np.isfinite(hi)
    total = np.where(active, piece, 0.0).sum(axis=0)
    return np.where(geometry["has_coverage"], total, np.nan)


def _cloud_water_maximum(lwc, z, pressure_hpa, valid, eligible, geometry, temperature):
    """Maximise piecewise-linear LWC on cloudy cold intervals and valid nodes.

    Include interval endpoints: a thin cloud may lie between two samples
    neither of which individually satisfies all the masks. The same geometry
    is used for water path and Godske overlap, without column-wide gating.
    """
    candidates = np.where(eligible, lwc, -np.inf)
    imax = np.argmax(candidates, axis=0)
    maximum = np.take_along_axis(candidates, imax[None], axis=0)[0]
    height = np.take_along_axis(z, imax[None], axis=0)[0]
    pressure = pressure_hpa[imax]
    lo, hi = geometry["segment_lo"], geometry["segment_hi"]
    for k in range(len(z) - 1):
        for fraction in (lo[k], hi[k]):
            value = lwc[k] + fraction * (lwc[k + 1] - lwc[k])
            at_height = z[k] + fraction * (z[k + 1] - z[k])
            better = np.isfinite(fraction) & ((value > maximum) |
                     (np.isclose(value, maximum, rtol=0, atol=1e-12) & (at_height < height)))
            maximum = np.where(better, value, maximum)
            height = np.where(better, at_height, height)
            pressure = np.where(better, pressure_hpa[k] + fraction * (pressure_hpa[k + 1] - pressure_hpa[k]), pressure)
    has_cold = (valid & (temperature < 0)).any(axis=0) | np.isfinite(lo).any(axis=0)
    present = np.isfinite(maximum) & (maximum > 0)
    maximum = np.where(present, maximum, np.where(has_cold, 0.0, np.nan))
    return maximum, np.where(present, height, np.nan), np.where(present, pressure, np.nan)


def _cloud_composite(t, rh, qc, w, valid, eligible, geometry):
    """Screen SFIP locally, then reduce vertically; never gate a whole column.

    Split each cloudy interval at every membership-function knot. With linear
    input interpolation, MT is linear, MRH quadratic, and Mw/ML linear there;
    their product is a cubic. Its endpoints and real derivative roots give the
    maximum without missing a narrow peak or changing it with a sampling grid.
    Work one pressure interval at a time to bound memory on small servers.
    """
    raw = sfip_index(t, rh, qc, w)
    level = np.where(valid, np.where(eligible, raw, 0.0), np.nan)
    candidates = np.where(np.isfinite(level), level, -np.inf)
    maximum = candidates.max(axis=0)
    coverage = valid.any(axis=0)
    lo, hi = geometry["segment_lo"], geometry["segment_hi"]
    for k in range(len(t) - 1):
        resolved = valid[k] & valid[k + 1] & np.isfinite(lo[k]) & np.isfinite(hi[k])
        if not resolved.any():
            continue
        start = (t[k], rh[k], qc[k], w[k])
        delta = tuple(a[k + 1] - a[k] for a in (t, rh, qc, w))
        cuts = [lo[k], hi[k]]
        knots = ((-28., -12., -1., 1.), (60., 95.), (0., .0004), (-.1, 0., .05))
        for initial, change, thresholds in zip(start, delta, knots):
            for threshold in thresholds:
                root = np.divide(threshold - initial, change, out=lo[k].copy(), where=change != 0)
                cuts.append(np.clip(root, lo[k], hi[k]))
        cuts = np.sort(np.stack(cuts), axis=0)

        def unbounded(fraction):
            sample = tuple(a + fraction * d for a, d in zip(start, delta))
            mt, mrh, mw, ml = sfip_memberships(*sample)
            return mt * (.35 * mrh + .20 * mw + .45 * ml)

        for left, right in zip(cuts[:-1], cuts[1:]):
            active = resolved & (right > left)
            if not active.any():
                continue
            # Recover this cubic in a local s=0..1 coordinate. Four exact
            # evaluations avoid cancellation from global-fraction coefficients
            # on very thin intervals. Clipping is applied only after maximising.
            width = right - left
            y0, y1, y2, y3 = (unbounded(left + s * width) for s in (0., 1/3, 2/3, 1.))
            d1, d2, d3 = y1-y0, y2-2*y1+y0, y3-3*y2+3*y1-y0
            c1, c2, c3 = 3*d1-1.5*d2+d3, 4.5*(d2-d3), 4.5*d3
            a, b, c = 3*c3, 2*c2, c1
            best = np.maximum(y0, y3)
            quadratic = np.abs(a) > 1e-12
            discriminant = b*b - 4*a*c
            with np.errstate(invalid="ignore", divide="ignore"):
                root_term = -0.5 * (b + np.copysign(np.sqrt(np.maximum(discriminant, 0)), b))
                roots = (
                    np.divide(root_term, a, out=np.full_like(a, np.nan), where=quadratic & (discriminant >= 0)),
                    np.divide(c, root_term, out=np.full_like(a, np.nan), where=quadratic & (discriminant >= 0) & (root_term != 0)),
                    np.divide(-c, b, out=np.full_like(a, np.nan), where=~quadratic & (np.abs(b) > 1e-12)),
                )
                for root in roots:
                    value = y0 + root * (c1 + root * (c2 + root*c3))
                    best = np.maximum(best, np.where((root > 0) & (root < 1), value, -np.inf))
            maximum = np.maximum(maximum, np.where(active, 100*np.clip(best, 0, 1), -np.inf))
    return level, np.where(coverage, maximum, np.nan), coverage


def compute_icing_fields(
    pressure_hpa, temperature_k, specific_humidity, height_gpm, cloud_water_kgkg,
    surface_pressure_pa, surface_height_m, *, cloud_fraction_pct=None,
    cloud_ice_kgkg=None, rain_water_kgkg=None, vertical_velocity_pas=None,
) -> dict[str, np.ndarray]:
    """Compute diagnostics from aligned (level, y, x) GFS pressure-level fields.

    CLWMR is interpreted per mass of dry air and vapour; condensate loading is
    neglected in density/hydrostatic integration. Invalid physical samples are
    masked separately for thermodynamic and water diagnostics. Missing optional
    fields preserve the raw diagnostic API but NEVER become clear sky or zero
    water in the cloud-screened maps. Production IO requires all input fields.
    At least two adjacent samples are necessary for layer geometry/integrals.
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
    def optional(value):
        field = np.full_like(t_k, np.nan) if value is None else np.asarray(value, dtype=float)
        if field.shape != t_k.shape:
            raise ValueError("Формы полей облачности и обледенения не совпадают")
        return field
    cloud, qi, qr, omega = (optional(value) for value in (
        cloud_fraction_pct, cloud_ice_kgkg, rain_water_kgkg, vertical_velocity_pas,
    ))
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
    # Inverting Td can leave a positive 2e-14 at the inclusive G=0 boundary.
    # Snap only this thermodynamic margin, not water/cloud thresholds.
    g = np.where(np.abs(g) <= 1e-10, 0.0, g)
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
    cloud_valid = base_valid & np.isfinite(cloud) & (cloud >= 0) & (cloud <= 100)
    cloud_water_valid = cloud_valid & water_valid
    cloud_conditions = [t, -cloud, WATER_THRESHOLD_GM3 - lwc]
    cloud_liquid = layer_products(z, cloud_water_valid, cloud_conditions, strict=(0, 1))
    common = thermo_valid & cloud_water_valid
    overlap = layer_products(z, common, [g, t, -20.0 - t, -cloud, WATER_THRESHOLD_GM3 - lwc], strict=(1, 3))
    comparison_covered = overlap["has_coverage"] & ~(pressure_above & ~common).any(axis=0)
    godske_present, water_present = godske["sum_gpm"] > 0, cloud_liquid["sum_gpm"] > 0
    agreement = np.zeros(ps.shape, dtype=float)
    agreement[godske_present & ~water_present] = 1
    agreement[water_present & ~godske_present] = 2
    agreement[godske_present & water_present] = 4
    agreement[overlap["sum_gpm"] > 0] = 3
    agreement[~comparison_covered] = np.nan
    eligible = cloud_water_valid & (t < 0) & (cloud > 0) & (lwc >= WATER_THRESHOLD_GM3)
    cloud_max, cloud_max_height, cloud_max_pressure = _cloud_water_maximum(
        lwc, z, p, cloud_water_valid, eligible, cloud_liquid, t,
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        upward_w = -omega / (density * G0)
    composite_valid = cloud_water_valid & np.isfinite(upward_w) & np.isfinite(rh_water)
    composite_level, composite, composite_coverage = _cloud_composite(
        t, rh_water, qc, upward_w, composite_valid, eligible, cloud_liquid,
    )
    cloud_missing = (pressure_above & ~cloud_water_valid).any(axis=0)
    composite_missing = (pressure_above & ~composite_valid).any(axis=0)
    conflict_level = cloud_water_valid & (t < 0) & (cloud == 0) & (lwc >= WATER_THRESHOLD_GM3)
    conflict = conflict_level.any(axis=0)
    # Phase diagnostics are kept distinct: neither ice nor rain is added to
    # liquid CLOUD water or used as evidence of supercooled large droplets.
    ice_valid = base_valid & np.isfinite(qi) & (qi >= 0) & (qi < 1)
    rain_valid = base_valid & np.isfinite(qr) & (qr >= 0) & (qr < 1)
    ice_content = np.where(ice_valid, 1000 * density * qi, np.nan)
    rain_content = np.where(rain_valid, 1000 * density * qr, np.nan)
    ice_only = (cloud_water_valid & ice_valid & (t < 0) & (cloud > 0) &
                (lwc < WATER_THRESHOLD_GM3) & (ice_content >= WATER_THRESHOLD_GM3))
    cold_rain = rain_valid & (t < 0) & (rain_content >= WATER_THRESHOLD_GM3)
    bottom = np.argmax(thermo_valid, axis=0)
    lowest = np.where(thermo_valid.any(axis=0), np.take_along_axis(z, bottom[None], axis=0)[0], np.nan)
    result = {
        "pressure_hpa": p, "temperature_C": t, "dewpoint_water_C": td,
        "specific_humidity_kgkg": q, "water_RH_pct": rh_water,
        "height_gpm": z, "cloud_water_kgkg": qc, "cloud_lwc_gm3": lwc,
        "cloud_fraction_pct": cloud, "cloud_ice_kgkg": qi, "rain_water_kgkg": qr,
        "cloud_ice_gm3": ice_content, "rain_water_gm3": rain_content,
        "vertical_velocity_pas": omega, "upward_velocity_ms": upward_w,
        "air_density_kgm3": density,
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
        "cloud_liquid_valid_above_ground": cloud_water_valid,
        "cloud_liquid_eligible": eligible,
        "cloud_profile_missing": cloud_missing,
        "cloud_godske_profile_missing": (pressure_above & ~common).any(axis=0),
        "cloud_input_conflict": conflict,
        "cloud_input_conflict_level": conflict_level,
        "cloud_max_slwc_gm3": cloud_max,
        "cloud_max_slwc_height_gpm": cloud_max_height,
        "cloud_max_slwc_pressure_hpa": cloud_max_pressure,
        "cloud_slwp_gm2": _layer_water_path(qc, p * 100, cloud_liquid),
        "cloud_godske_slwp_gm2": _layer_water_path(qc, p * 100, overlap),
        "composite_index_level": composite_level, "composite_index": composite,
        "composite_has_coverage": composite_coverage,
        "composite_profile_missing": composite_missing,
        "composite_valid_above_ground": composite_valid,
        "cloud_ice_only_present": ice_only.any(axis=0),
        "cold_rain_present": cold_rain.any(axis=0),
        "ice_profile_missing": (pressure_above & ~ice_valid).any(axis=0),
        "rain_profile_missing": (pressure_above & ~rain_valid).any(axis=0),
    }
    for prefix, values in (("godske20", godske), ("slw001", liquid),
                           ("cloud_liquid", cloud_liquid), ("cloud_godske", overlap)):
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
        valid = fields["cloud_liquid_valid_above_ground"][k]
        return np.where(valid, np.where(fields["cloud_liquid_eligible"][k], fields["cloud_lwc_gm3"][k], 0.0), np.nan)
    key = {
        "godske_sum": "cloud_godske_sum_gpm", "godske_max": "cloud_godske_max_layer_gpm",
        "godske_base": "cloud_godske_base_first_gpm", "godske_top": "cloud_godske_top_first_gpm",
        "godske_count": "cloud_godske_n_layers", "slw_max": "cloud_max_slwc_gm3",
        "slw_height": "cloud_max_slwc_height_gpm", "slwp": "cloud_slwp_gm2", "agreement": "agreement",
        "composite": "composite_index",
    }[product]
    value = np.array(fields[key], dtype=float, copy=True)
    if product in {"godske_base", "godske_top"}:
        side = "base" if product == "godske_base" else "top"
        value[np.asarray(fields[f"cloud_godske_{side}_first_open"], dtype=bool)] = np.nan
    return value
