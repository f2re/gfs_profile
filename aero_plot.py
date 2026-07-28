from __future__ import annotations

import math
import tempfile
from pathlib import Path
from textwrap import fill

import numpy as np

from gfs_core import ProfileResult
from plot_style import METEO, add_footer, apply_meteo_rcparams, style_axis

SUPPORTED_AERO_DIAGRAMS = {"skewt"}
DEFAULT_AERO_DIAGRAM = "skewt"
AERO_LEVELS_HPA: tuple[int, ...] | None = None
ISOTHERM_TARGETS_C = (0.0, -10.0, -20.0)
DIAGRAM_RU_NAMES = {"skewt": "Skew-T log-P"}
ISOTHERM_LINE_COLORS = {0.0: "#C62828", -10.0: "#D94B4B", -20.0: "#E17878"}
HAZARD_COLORS = {
    "cloud": "#7C91A6",
    "icing": "#2F80D0",
    "turb": "#D98216",
    "conv": "#7A3EC8",
    "precip": "#2B78B8",
}
HAZARD_SOFT = {
    "cloud": "#DCE4EB",
    "icing": "#D8ECFF",
    "turb": "#FFE7C4",
    "conv": "#EADFFF",
    "precip": "#DDEEFF",
}
CARD_COLORS = {
    0: "#FFFFFF",
    1: "#EDF7F1",
    2: "#FFF7D6",
    3: "#FFE8C7",
    4: "#FFD7D7",
    5: "#FFBDBD",
}
G = 9.80665
RD_CP = 0.2854
MAIN_CURVE_COLORS = {
    "temperature": "#C62828",
    "dewpoint": "#138A66",
    "parcel": "#111827",
    "ice_saturation": "#4169A8",
}
INDEX_CARD_RECTS = tuple(
    (0.006 + col * 0.505, 0.765 - row * 0.245, 0.475, 0.205)
    for row in range(4)
    for col in range(2)
)


def _safe_suffix(result: ProfileResult, diagram_type: str = DEFAULT_AERO_DIAGRAM) -> str:
    suffix = (
        f"_{DEFAULT_AERO_DIAGRAM}_{result.run.date}_{result.run.cycle}_"
        f"f{result.lead_hour:03d}_{result.grid_lat:.3f}_{result.grid_lon:.3f}.png"
    )
    return suffix.replace("-", "m").replace(" ", "_")


def _diagram_title(result: ProfileResult) -> tuple[str, str]:
    title = "GFS 0.25 · аэрологическая диаграмма Skew-T log-P"
    subtitle = (
        f"{result.run.date} {result.run.cycle}Z · +{result.lead_hour} ч · "
        f"{result.valid_time_utc:%d.%m.%Y %H:%M UTC} · "
        f"{result.requested_lat:.3f}, {result.requested_lon:.3f} → "
        f"узел GFS {result.grid_lat:.3f}, {result.grid_lon:.3f}"
    )
    return title, subtitle


def _grad(values: np.ndarray, coord: np.ndarray) -> np.ndarray:
    if len(values) < 3 or len(np.unique(coord)) < 3:
        return np.full_like(values, np.nan, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.gradient(values.astype(float), coord.astype(float), edge_order=1)


def _thetae_bolton(df) -> np.ndarray:
    p = df["pressure_hpa"].to_numpy(dtype=float)
    t = df["temperature_k"].to_numpy(dtype=float)
    td_c = df["dewpoint_c"].to_numpy(dtype=float)
    td = td_c + 273.15
    e = 6.112 * np.exp((17.67 * td_c) / (td_c + 243.5))
    r = 0.622 * e / np.maximum(p - e, 0.1)
    theta = t * np.power(1000.0 / p, RD_CP)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        tl = 1.0 / (1.0 / np.maximum(td - 56.0, 1.0) + np.log(np.maximum(t / td, 1e-6)) / 800.0) + 56.0
        out = theta * np.exp((3376.0 / tl - 2.54) * r * (1.0 + 0.81 * r))
    out[~np.isfinite(out)] = np.nan
    return out


def _augment_profile(df):
    out = df.copy().sort_values("geopotential_height_m").reset_index(drop=True)
    z_m = out["geopotential_height_m"].to_numpy(dtype=float)
    z_km = z_m / 1000.0
    u = out["u_wind_ms"].to_numpy(dtype=float)
    v = out["v_wind_ms"].to_numpy(dtype=float)
    theta = (
        out["theta_k"].to_numpy(dtype=float)
        if "theta_k" in out
        else out["temperature_k"].to_numpy(dtype=float)
        * np.power(1000.0 / out["pressure_hpa"].to_numpy(dtype=float), RD_CP)
    )
    du = _grad(u, z_m)
    dv = _grad(v, z_m)
    shear = 1000.0 * np.sqrt(du**2 + dv**2)
    dtheta = _grad(theta, z_m)
    denom = du**2 + dv**2
    with np.errstate(divide="ignore", invalid="ignore"):
        ri = (G / np.maximum(theta, 1.0) * dtheta) / denom
    thetae = _thetae_bolton(out)
    out["vertical_shear_ms_per_km"] = shear
    out["gradient_richardson"] = np.where(np.isfinite(ri), ri, np.nan)
    out["thetae_k"] = thetae
    out["thetae_lapse_k_per_km"] = _grad(thetae, z_km)
    return out.sort_values("pressure_hpa", ascending=False).reset_index(drop=True)


def _prepare_profile(result: ProfileResult):
    required = [
        "pressure_hpa",
        "temperature_c",
        "dewpoint_c",
        "u_wind_ms",
        "v_wind_ms",
        "geopotential_height_m",
        "relative_humidity_pct",
    ]
    df = result.dataframe.dropna(subset=required).copy().sort_values("pressure_hpa", ascending=False)
    if df.empty:
        raise ValueError("Пустой профиль: нечего строить")
    return _augment_profile(df)


def _interpolate_isotherm_height(df, target_c: float) -> float | None:
    prof = df.sort_values("geopotential_height_m")[["temperature_c", "geopotential_height_m"]].dropna()
    temps = prof["temperature_c"].to_numpy(dtype=float)
    heights = prof["geopotential_height_m"].to_numpy(dtype=float)
    for i in range(len(temps) - 1):
        t0, t1 = temps[i], temps[i + 1]
        if math.isclose(t0, target_c, abs_tol=0.05):
            return float(heights[i])
        if (t0 >= target_c >= t1) or (t0 <= target_c <= t1):
            if math.isclose(t0, t1):
                return float(heights[i])
            return float(
                heights[i]
                + (target_c - t0) / (t1 - t0) * (heights[i + 1] - heights[i])
            )
    return None


def _pressure_at_height(df, height_m: float | None) -> float | None:
    if height_m is None:
        return None
    prof = df.sort_values("geopotential_height_m")[["geopotential_height_m", "pressure_hpa"]].dropna()
    h = prof["geopotential_height_m"].to_numpy(dtype=float)
    p = prof["pressure_hpa"].to_numpy(dtype=float)
    if len(h) < 2 or height_m < h.min() or height_m > h.max():
        return None
    return float(np.interp(height_m, h, p))


def _height_at_pressure(df, pressure_hpa: float | None) -> float | None:
    if pressure_hpa is None or not np.isfinite(float(pressure_hpa)):
        return None
    prof = df[["pressure_hpa", "geopotential_height_m"]].dropna().sort_values("pressure_hpa")
    p = prof["pressure_hpa"].to_numpy(dtype=float)
    z = prof["geopotential_height_m"].to_numpy(dtype=float)
    value = float(pressure_hpa)
    if len(p) < 2 or value < p.min() or value > p.max():
        return None
    return float(np.interp(value, p, z))


def _q(value, unit: str | None = None) -> float | None:
    if value is None:
        return None
    try:
        if hasattr(value, "m_as") and unit:
            return float(value.m_as(unit))
        if hasattr(value, "magnitude"):
            return float(np.asarray(value.magnitude).squeeze())
        return float(np.asarray(value).squeeze())
    except Exception:
        return None


def _metpy_diagnostics(df) -> dict[str, object]:
    diag: dict[str, object] = {"parcel": None}
    try:
        from metpy.calc import (
            cape_cin,
            el,
            k_index,
            lcl,
            lfc,
            mixed_layer_cape_cin,
            most_unstable_cape_cin,
            parcel_profile,
            precipitable_water,
            total_totals_index,
        )
        from metpy.units import units

        p = df["pressure_hpa"].to_numpy(dtype=float) * units.hPa
        t = df["temperature_c"].to_numpy(dtype=float) * units.degC
        td = df["dewpoint_c"].to_numpy(dtype=float) * units.degC
        parcel = parcel_profile(p, t[0], td[0]).to("degC")
        sbcape, sbcin = cape_cin(p, t, td, parcel)
        diag.update({"parcel": parcel, "sbcape": _q(sbcape), "sbcin": _q(sbcin)})
        for key, func in (("ml", mixed_layer_cape_cin), ("mu", most_unstable_cape_cin)):
            try:
                cape, cin = func(p, t, td)
                diag[f"{key}cape"] = _q(cape)
                diag[f"{key}cin"] = _q(cin)
            except Exception:
                diag[f"{key}cape"] = diag[f"{key}cin"] = None
        try:
            lcl_p, _ = lcl(p[0], t[0], td[0])
            diag["lcl"] = _q(lcl_p, "hPa")
        except Exception:
            diag["lcl"] = None
        for key, func in (("lfc", lfc), ("el", el)):
            try:
                val, _ = func(p, t, td)
                diag[key] = _q(val, "hPa")
            except Exception:
                diag[key] = None
        for key, func in (("pwat", precipitable_water), ("tt", total_totals_index), ("k", k_index)):
            try:
                diag[key] = _q(func(p, t, td))
            except Exception:
                diag[key] = None
    except Exception as exc:
        diag["error"] = str(exc)
    return diag


def _layerize(df, mask, kind: str, label: str, severity: int, reason: str) -> list[dict[str, object]]:
    d = df.sort_values("geopotential_height_m").reset_index(drop=True)
    mask = np.asarray(mask, dtype=bool)
    layers: list[dict[str, object]] = []
    start = None
    for i, active in enumerate(mask):
        if active and start is None:
            start = i
        if start is not None and (not active or i == len(mask) - 1):
            end = i if active and i == len(mask) - 1 else i - 1
            part = d.iloc[start : end + 1]
            if not part.empty:
                layers.append(
                    {
                        "kind": kind,
                        "label": label,
                        "severity": severity,
                        "reason": reason,
                        "base_hpa": float(part["pressure_hpa"].max()),
                        "top_hpa": float(part["pressure_hpa"].min()),
                        "base_km": float(part["geopotential_height_km"].min()),
                        "top_km": float(part["geopotential_height_km"].max()),
                    }
                )
            start = None
    return layers


def _diagnose_layers(df) -> list[dict[str, object]]:
    d = df.sort_values("geopotential_height_m").reset_index(drop=True)
    temp = d["temperature_c"].to_numpy(dtype=float)
    rh = d["relative_humidity_pct"].to_numpy(dtype=float)
    spread = temp - d["dewpoint_c"].to_numpy(dtype=float)
    cloud = (rh >= 85.0) | ((rh >= 78.0) & (spread <= 3.0))
    icing = cloud & (temp <= 0.0) & (temp >= -20.0)
    turb = (
        (d["gradient_richardson"].to_numpy(dtype=float) < 0.25)
        | (d["vertical_shear_ms_per_km"].to_numpy(dtype=float) >= 10.0)
    )
    conv = d["thetae_lapse_k_per_km"].to_numpy(dtype=float) <= -3.0
    precip = np.zeros_like(cloud, dtype=bool)
    for col in ("clwmr", "icmr", "rwmr", "snmr", "grle"):
        if col in d:
            precip |= d[col].to_numpy(dtype=float) > 0
    out: list[dict[str, object]] = []
    out += _layerize(d, cloud, "cloud", "Облачность", 2, "RH≥85% или T−Td≤3 °C")
    out += _layerize(d, icing, "icing", "Обледенение", 3, "влажно и 0…−20 °C")
    out += _layerize(d, turb, "turb", "Болтанка", 3, "Ri<0.25 или сдвиг≥10 м/с/км")
    out += _layerize(d, conv, "conv", "Конвективная неустойчивость", 3, "dθe/dz≤−3 K/км")
    out += _layerize(d, precip, "precip", "Осадки", 2, "гидрометеоры GFS")
    return out


def _frost_point_curve(df) -> np.ndarray:
    """Approximate frost-point temperature from vapour pressure over water."""

    td = df["dewpoint_c"].to_numpy(dtype=float)
    ambient = df["temperature_c"].to_numpy(dtype=float)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        vapour_pressure = 6.112 * np.exp((17.67 * td) / (td + 243.5))
        log_ratio = np.log(np.maximum(vapour_pressure, 1e-6) / 6.112)
        frost_point = 272.62 * log_ratio / np.maximum(22.46 - log_ratio, 1e-6)
    frost_point[(ambient > 0.0) | ~np.isfinite(frost_point)] = np.nan
    return frost_point


def _fmt(value, fmt: str = ".0f") -> str:
    return "—" if value is None or not np.isfinite(float(value)) else format(float(value), fmt)


def _hkm(value: float | None) -> str:
    return "—" if value is None else f"{value / 1000.0:.1f}"


def _ls(layers, kind: str) -> str:
    selected = [x for x in layers if x["kind"] == kind]
    if not selected:
        return "—"
    text = ", ".join(f"{x['base_km']:.1f}–{x['top_km']:.1f}" for x in selected[:2])
    return text + ("…" if len(selected) > 2 else "")


def _risk_level(name: str, value: float | None) -> int:
    if value is None or not np.isfinite(float(value)):
        return 0
    v = float(value)
    if name == "cape":
        return 5 if v >= 2500 else 4 if v >= 1500 else 3 if v >= 800 else 2 if v >= 300 else 1 if v >= 100 else 0
    if name == "cin":
        a = abs(v)
        return 4 if a >= 200 else 3 if a >= 100 else 2 if a >= 50 else 1 if a >= 25 else 0
    if name == "tt":
        return 4 if v >= 55 else 3 if v >= 50 else 2 if v >= 45 else 1 if v >= 40 else 0
    if name == "k":
        return 4 if v >= 35 else 3 if v >= 30 else 2 if v >= 25 else 1 if v >= 20 else 0
    if name == "shear":
        return 5 if v >= 16 else 4 if v >= 12 else 3 if v >= 8 else 2 if v >= 5 else 0
    if name == "ri":
        return 5 if v < 0.0 else 4 if v < 0.25 else 2 if v < 1.0 else 0
    return 0


def _card(ax, x: float, y: float, w: float, h: float, title: str, value: str, note: str, level: int) -> None:
    import matplotlib.patches as patches

    ax.add_patch(
        patches.FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.010,rounding_size=0.018",
            facecolor=CARD_COLORS[min(max(level, 0), 5)],
            edgecolor=METEO.annotation_edge,
            linewidth=0.75,
        )
    )
    ax.text(x + 0.018, y + h - 0.035, title, ha="left", va="top", fontsize=7.1, color=METEO.muted_text, fontweight="bold", clip_on=True)
    ax.text(x + 0.018, y + h * 0.51, value, ha="left", va="center", fontsize=10.4, color=METEO.axis_text, fontweight="bold", clip_on=True)
    ax.text(x + 0.018, y + 0.022, fill(note, width=25), ha="left", va="bottom", fontsize=6.1, color=METEO.muted_text, linespacing=1.05, clip_on=True)


def _plot_index_cards(ax, df, diag, layers) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.0, 1.02, "Ключевые показатели", ha="left", va="bottom", fontsize=11, fontweight="bold", color=METEO.axis_text)

    cape_values = [diag.get("sbcape"), diag.get("mlcape"), diag.get("mucape")]
    cape = max([float(v) for v in cape_values if v is not None and np.isfinite(float(v))], default=None)
    cin_values = [diag.get("sbcin"), diag.get("mlcin"), diag.get("mucin")]
    cin = min([float(v) for v in cin_values if v is not None and np.isfinite(float(v))], default=None)
    vmax = float(np.nanmax(df["wind_speed_ms"].to_numpy(dtype=float)))
    shear = float(np.nanmax(df["vertical_shear_ms_per_km"].to_numpy(dtype=float)))
    lcl_h = _height_at_pressure(df, diag.get("lcl"))
    lfc_h = _height_at_pressure(df, diag.get("lfc"))
    el_h = _height_at_pressure(df, diag.get("el"))
    iso = "/".join(_hkm(_interpolate_isotherm_height(df, t)) for t in ISOTHERM_TARGETS_C)
    hazard = max([int(x["severity"]) for x in layers], default=0)

    cards = [
        ("CAPE, максимум", f"{_fmt(cape)} Дж/кг", "энергия конвекции", _risk_level("cape", cape)),
        ("CIN", f"{_fmt(cin)} Дж/кг", "задержка конвекции", _risk_level("cin", cin)),
        ("TT / K", f"{_fmt(diag.get('tt'), '.1f')} / {_fmt(diag.get('k'), '.1f')}", "индексы неустойчивости", max(_risk_level("tt", diag.get("tt")), _risk_level("k", diag.get("k")))),
        ("Уровень конденсации", "—" if lcl_h is None else f"{lcl_h:.0f} м", f"{_fmt(diag.get('lcl'))} гПа", 1),
        ("Своб. конвекция / равновесие", f"{_hkm(lfc_h)} / {_hkm(el_h)} км", "LFC / EL", 1),
        ("0 / −10 / −20 °C", f"{iso} км", "высота изотерм", 1),
        ("Ветер / сдвиг", f"{vmax:.1f} / {shear:.1f}", "м/с · м/с/км", _risk_level("shear", shear)),
        ("Опасные слои", f"Лёд {_ls(layers, 'icing')}", f"Болтанка {_ls(layers, 'turb')} км", hazard),
    ]

    for rect, item in zip(INDEX_CARD_RECTS, cards):
        _card(ax, *rect, *item)


def _create_metpy_diagram(fig, diagram_type: str = DEFAULT_AERO_DIAGRAM):
    from metpy.plots import SkewT

    return SkewT(fig, rotation=30)


def _add_isotherm_guides(ax, df, tmin: float, tmax: float) -> None:
    offsets = {0.0: (8, -5), -10.0: (8, 6), -20.0: (8, 6)}
    for target in ISOTHERM_TARGETS_C:
        if tmin <= target <= tmax:
            ax.axvline(target, color=ISOTHERM_LINE_COLORS[target], linewidth=0.9, linestyle="-" if target == 0 else "--", alpha=0.72, zorder=1)
        height = _interpolate_isotherm_height(df, target)
        pressure = _pressure_at_height(df, height)
        if pressure is not None and height is not None and tmin <= target <= tmax:
            ax.annotate(
                f"{int(target)} °C · {height / 1000:.1f} км",
                xy=(target, pressure),
                xytext=offsets[target],
                textcoords="offset points",
                fontsize=6.8,
                color=ISOTHERM_LINE_COLORS[target],
                bbox={"boxstyle": "round,pad=0.16", "facecolor": METEO.axes_bg, "edgecolor": "none", "alpha": 0.84},
                zorder=12,
            )


def _draw_reference_levels(ax, df, diag) -> None:
    items = [
        (diag.get("lcl"), "Уровень конденсации", "#6D28D9", 0.02),
        (diag.get("lfc"), "Свободная конвекция", "#B45309", 0.36),
        (diag.get("el"), "Уровень равновесия", "#BE123C", 0.68),
    ]
    for pressure, label, color, x_fraction in items:
        if pressure is None or not 100 <= float(pressure) <= 1050:
            continue
        height = _height_at_pressure(df, float(pressure))
        if height is None:
            value = label
        elif height < 2000:
            value = f"{label} · {height:.0f} м"
        else:
            value = f"{label} · {height / 1000:.1f} км"
        ax.axhline(float(pressure), linewidth=0.78, color=color, linestyle=":", alpha=0.72, zorder=3)
        ax.text(
            x_fraction,
            float(pressure),
            value,
            transform=ax.get_yaxis_transform(),
            fontsize=6.7,
            color=color,
            va="center",
            ha="left",
            bbox={"boxstyle": "round,pad=0.18", "facecolor": METEO.axes_bg, "edgecolor": color, "linewidth": 0.45, "alpha": 0.90},
            zorder=13,
        )


def _pressure_axis(ax) -> None:
    ax.set_yscale("log")
    ax.set_ylim(1050, 100)
    ax.set_yticks((1000, 925, 850, 700, 500, 300, 200, 100))
    ax.get_yaxis().set_major_formatter(lambda value, _pos: f"{int(value)}")
    style_axis(ax)


def _draw_layer_spans(ax, layers) -> None:
    zorders = {"cloud": 0.5, "precip": 0.8, "icing": 1.0, "turb": 1.1, "conv": 1.2}
    alphas = {"cloud": 0.055, "icing": 0.085, "turb": 0.065, "conv": 0.055, "precip": 0.045}
    for layer in layers:
        kind = str(layer["kind"])
        ax.axhspan(
            float(layer["top_hpa"]),
            float(layer["base_hpa"]),
            color=HAZARD_SOFT[kind],
            alpha=alphas[kind],
            linewidth=0,
            zorder=zorders[kind],
        )

    for kind, x_fraction in (("cloud", 0.96), ("icing", 0.78), ("turb", 0.58)):
        selected = [layer for layer in layers if layer["kind"] == kind]
        if not selected:
            continue
        layer = max(selected, key=lambda item: float(item["top_km"]) - float(item["base_km"]))
        pressure = math.sqrt(float(layer["top_hpa"]) * float(layer["base_hpa"]))
        ax.text(
            x_fraction,
            pressure,
            f"{layer['label']}\n{layer['base_km']:.1f}–{layer['top_km']:.1f} км",
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="center",
            fontsize=6.3,
            color=HAZARD_COLORS[kind],
            bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": HAZARD_COLORS[kind], "linewidth": 0.45, "alpha": 0.84},
            zorder=11,
        )


def _hazards(ax, layers) -> None:
    cats = [
        ("cloud", "Облака"),
        ("icing", "Лёд"),
        ("turb", "Бол-\nтанка"),
        ("conv", "Конв."),
        ("precip", "Осадки"),
    ]
    ax.set_xlim(-0.5, 4.5)
    ax.set_xticks(range(5), [item[1] for item in cats], fontsize=7.0)
    ax.set_title("Слои по высоте", fontsize=9.3, fontweight="bold", pad=7)
    for index, (kind, _label) in enumerate(cats):
        ax.axvline(index, color=METEO.grid_minor, linewidth=0.55)
        for layer in [item for item in layers if item["kind"] == kind]:
            ax.fill_betweenx(
                [layer["top_hpa"], layer["base_hpa"]],
                index - 0.34,
                index + 0.34,
                color=HAZARD_COLORS[kind],
                alpha=0.18 + int(layer["severity"]) * 0.065,
                linewidth=0,
            )
            ax.text(
                index,
                math.sqrt(float(layer["top_hpa"]) * float(layer["base_hpa"])),
                str(layer["severity"]),
                ha="center",
                va="center",
                fontsize=7.3,
                fontweight="bold",
                color="#172033",
            )
    _pressure_axis(ax)


def _wind_panel(ax, df) -> None:
    ax.plot(df["wind_speed_ms"], df["pressure_hpa"], color="#203B63", linewidth=1.9, label="скорость")
    ax.plot(df["vertical_shear_ms_per_km"], df["pressure_hpa"], color="#D97706", linewidth=1.45, label="сдвиг")
    ax.axvspan(10, 15, color="#F59E0B", alpha=0.06, linewidth=0)
    ax.axvspan(15, 45, color="#DC2626", alpha=0.055, linewidth=0)
    ax.set_title("Ветер и сдвиг", fontsize=9.3, fontweight="bold", pad=7)
    ax.set_xlabel("м/с · м/с/км", fontsize=7.5)
    ax.legend(loc="lower right", fontsize=6.8, framealpha=0.90)
    _pressure_axis(ax)
    ax.tick_params(labelleft=False)


def _hodograph(ax, df) -> None:
    from matplotlib.collections import LineCollection

    profile = df.sort_values("geopotential_height_m")
    profile = profile[profile["geopotential_height_m"] <= 8000].dropna(subset=["u_wind_ms", "v_wind_ms"])
    if len(profile) >= 2:
        u = profile["u_wind_ms"].to_numpy(dtype=float)
        v = profile["v_wind_ms"].to_numpy(dtype=float)
        height = profile["geopotential_height_km"].to_numpy(dtype=float)
        points = np.column_stack([u, v])
        segments = np.stack([points[:-1], points[1:]], axis=1)
        collection = LineCollection(segments, cmap="viridis", linewidth=2.2, alpha=0.92)
        collection.set_array((height[:-1] + height[1:]) / 2.0)
        ax.add_collection(collection)
        ax.scatter(u, v, c=height, cmap="viridis", s=17, edgecolor="white", linewidth=0.35, zorder=4)
        for km in (0, 1, 3, 6, 8):
            row = profile.loc[(profile["geopotential_height_km"] - km).abs().idxmin()]
            ax.annotate(
                f"{km} км",
                (row["u_wind_ms"], row["v_wind_ms"]),
                xytext=(4, 4),
                textcoords="offset points",
                fontsize=6.8,
                fontweight="bold",
                bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "none", "alpha": 0.82},
            )
        limit = max(8.0, float(np.nanmax(np.abs(np.concatenate([u, v])))) + 4.0)
        limit = math.ceil(limit / 5.0) * 5.0
        ax.set_xlim(-limit, limit)
        ax.set_ylim(-limit, limit)
    else:
        ax.text(0.5, 0.5, "Годограф недоступен", ha="center", va="center", transform=ax.transAxes)
    ax.axhline(0, color=METEO.grid_minor, linewidth=0.7)
    ax.axvline(0, color=METEO.grid_minor, linewidth=0.7)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Годограф ветра 0–8 км", fontsize=9.3, fontweight="bold", pad=7)
    ax.set_xlabel("u, м/с", fontsize=7.5)
    ax.set_ylabel("v, м/с", fontsize=7.5)
    style_axis(ax)


def _legend(ax) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    ax.axis("off")
    handles = [
        Line2D([0], [0], color="#C62828", linewidth=2.6, label="Температура среды"),
        Line2D([0], [0], color="#138A66", linewidth=2.2, label="Точка росы"),
        Line2D([0], [0], color="#111827", linewidth=2.0, label="Кривая частицы"),
        Line2D([0], [0], color="#4169A8", linewidth=1.4, linestyle="--", label="Насыщение надо льдом"),
        Patch(facecolor=HAZARD_SOFT["cloud"], edgecolor=HAZARD_COLORS["cloud"], label="Облачность"),
        Patch(facecolor=HAZARD_SOFT["icing"], edgecolor=HAZARD_COLORS["icing"], label="Обледенение"),
        Patch(facecolor=HAZARD_SOFT["turb"], edgecolor=HAZARD_COLORS["turb"], label="Болтанка"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=6.7, frameon=False, handlelength=2.7, labelspacing=0.72)
    ax.text(
        0.0,
        0.06,
        "Слои рассчитаны по влажности, температуре,\nсдвигу ветра и числу Ричардсона.",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=6.5,
        color=METEO.muted_text,
    )


def _plot_metpy_diagram(result: ProfileResult, out_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from metpy.units import units

    apply_meteo_rcparams(plt)
    df = _prepare_profile(result)
    diag = _metpy_diagnostics(df)
    layers = _diagnose_layers(df)
    p = df["pressure_hpa"].to_numpy(dtype=float) * units.hPa
    t = df["temperature_c"].to_numpy(dtype=float) * units.degC
    td = df["dewpoint_c"].to_numpy(dtype=float) * units.degC
    u = df["u_wind_ms"].to_numpy(dtype=float) * units("m/s")
    v = df["v_wind_ms"].to_numpy(dtype=float) * units("m/s")
    frost = _frost_point_curve(df)

    fig = None
    try:
        fig = plt.figure(figsize=(16.2, 9.6), facecolor=METEO.figure_bg)
        diagram = _create_metpy_diagram(fig)
        diagram.ax.set_position([0.055, 0.095, 0.585, 0.79])
        diagram.ax.set_facecolor("#FAFCFE")

        _draw_layer_spans(diagram.ax, layers)
        diagram.plot(p, t, linewidth=2.8, color=MAIN_CURVE_COLORS["temperature"], label="Температура среды", zorder=9)
        diagram.plot(p, td, linewidth=2.35, color=MAIN_CURVE_COLORS["dewpoint"], label="Точка росы", zorder=8)
        if np.isfinite(frost).any():
            diagram.plot(p, frost * units.degC, linewidth=1.4, color=MAIN_CURVE_COLORS["ice_saturation"], linestyle="--", label="Насыщение надо льдом", zorder=7)
        if diag.get("parcel") is not None:
            diagram.plot(p, diag["parcel"], linewidth=2.05, color=MAIN_CURVE_COLORS["parcel"], label="Кривая частицы", zorder=10)
            try:
                diagram.shade_cape(p, t, diag["parcel"], alpha=0.14, color="#F59E0B")
                diagram.shade_cin(p, t, diag["parcel"], alpha=0.10, color="#718096")
            except Exception:
                pass

        diagram.plot_barbs(p, u, v, xloc=1.042, color="#203B63", linewidth=0.68)
        diagram.plot_dry_adiabats(linewidth=0.45, alpha=0.28, color="#C5A46B")
        diagram.plot_moist_adiabats(linewidth=0.48, alpha=0.30, color="#3A9B8A")
        diagram.plot_mixing_lines(linewidth=0.38, alpha=0.26, color="#6F9B77")

        tmin = min(-72.0, float(df[["temperature_c", "dewpoint_c"]].min().min()) - 8.0)
        tmax = max(36.0, float(df["temperature_c"].max()) + 8.0)
        diagram.ax.set_ylim(1050, 100)
        diagram.ax.set_xlim(tmin, tmax)
        _add_isotherm_guides(diagram.ax, df, tmin, tmax)
        _draw_reference_levels(diagram.ax, df, diag)
        style_axis(diagram.ax)
        diagram.ax.set_xlabel("Температура, °C")
        diagram.ax.set_ylabel("Давление, гПа")

        title, subtitle = _diagram_title(result)
        fig.text(0.055, 0.965, title, ha="left", va="top", fontsize=15.0, fontweight="bold", color=METEO.axis_text)
        fig.text(0.055, 0.935, subtitle, ha="left", va="top", fontsize=8.7, color=METEO.muted_text)
        handles, labels = diagram.ax.get_legend_handles_labels()
        fig.legend(
            handles,
            labels,
            loc="upper left",
            bbox_to_anchor=(0.055, 0.912, 0.585, 0.022),
            mode="expand",
            ncol=min(4, len(labels)),
            fontsize=7.2,
            frameon=False,
            borderaxespad=0,
        )

        cards = fig.add_axes([0.665, 0.605, 0.315, 0.285])
        _plot_index_cards(cards, df, diag, layers)
        hazards = fig.add_axes([0.665, 0.355, 0.15, 0.205], sharey=diagram.ax)
        _hazards(hazards, layers)
        wind = fig.add_axes([0.835, 0.355, 0.145, 0.205], sharey=diagram.ax)
        _wind_panel(wind, df)
        hodograph = fig.add_axes([0.665, 0.095, 0.15, 0.215])
        _hodograph(hodograph, df)
        legend = fig.add_axes([0.835, 0.095, 0.145, 0.215])
        _legend(legend)

        add_footer(
            fig,
            "GFS grid, не радиозонд. Облачность, обледенение и болтанка — диагностические модельные слои.",
            y=0.018,
        )
        fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor=METEO.figure_bg)
    finally:
        if fig is not None:
            plt.close(fig)


def write_aero_png(result: ProfileResult, diagram_type: str = DEFAULT_AERO_DIAGRAM) -> Path:
    """Render the single supported Skew-T log-P product with hodograph."""

    normalized = str(diagram_type or DEFAULT_AERO_DIAGRAM).lower().strip()
    if normalized != DEFAULT_AERO_DIAGRAM:
        raise ValueError("Доступна одна аэрологическая диаграмма: Skew-T log-P")
    tmp = tempfile.NamedTemporaryFile(
        prefix="gfs_aero",
        suffix=_safe_suffix(result),
        delete=False,
    )
    out_path = Path(tmp.name)
    tmp.close()
    try:
        _plot_metpy_diagram(result, out_path)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return out_path
