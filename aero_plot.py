from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np

from gfs_core import ProfileResult
from plot_style import METEO, add_footer, annotation_box_kwargs, apply_meteo_rcparams, style_axis

SUPPORTED_AERO_DIAGRAMS = {"stuve", "emagram", "skewt"}
AERO_LEVELS_HPA: tuple[int, ...] | None = None
ISOTHERM_TARGETS_C = (0.0, -10.0, -20.0)
DIAGRAM_RU_NAMES = {"stuve": "Stüve", "emagram": "Эмаграмма", "skewt": "Skew-T log-P"}
ISOTHERM_LINE_COLORS = {0.0: METEO.freezing, -10.0: METEO.minus10, -20.0: METEO.minus20}
HAZARD_COLORS = {"cloud": "#90A4B8", "icing": "#3B82F6", "turb": "#F59E0B", "conv": "#DC2626", "precip": "#16A34A"}
CARD_COLORS = {0: "#FFFFFF", 1: "#ECFDF5", 2: "#FEF9C3", 3: "#FED7AA", 4: "#FECACA", 5: "#FCA5A5"}
G = 9.80665
RD_CP = 0.2854


def _safe_suffix(result: ProfileResult, diagram_type: str) -> str:
    suffix = f"_{diagram_type}_{result.run.date}_{result.run.cycle}_f{result.lead_hour:03d}_{result.grid_lat:.3f}_{result.grid_lon:.3f}.png"
    return suffix.replace("-", "m").replace(" ", "_")


def _diagram_title(result: ProfileResult, diagram_type: str) -> str:
    name = DIAGRAM_RU_NAMES.get(diagram_type, diagram_type.upper())
    return (
        f"GFS 0.25 · аэрограмма {name} · запуск {result.run.date} {result.run.cycle}Z · "
        f"+{result.lead_hour} ч · {result.valid_time_utc:%Y-%m-%d %H:%M UTC}\n"
        f"{result.requested_lat:.3f},{result.requested_lon:.3f} → ⊞GFS {result.grid_lat:.3f},{result.grid_lon:.3f}; модельный профиль, не радиозонд"
    )


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
    theta = out["theta_k"].to_numpy(dtype=float) if "theta_k" in out else out["temperature_k"].to_numpy(dtype=float) * np.power(1000.0 / out["pressure_hpa"].to_numpy(dtype=float), RD_CP)
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
    required = ["pressure_hpa", "temperature_c", "dewpoint_c", "u_wind_ms", "v_wind_ms", "geopotential_height_m", "relative_humidity_pct"]
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
            return float(heights[i]) if math.isclose(t0, t1) else float(heights[i] + (target_c - t0) / (t1 - t0) * (heights[i + 1] - heights[i]))
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
        from metpy.calc import cape_cin, el, k_index, lcl, lfc, mixed_layer_cape_cin, most_unstable_cape_cin, parcel_profile, precipitable_water, total_totals_index
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
    layers = []
    start = None
    for i, active in enumerate(mask):
        if active and start is None:
            start = i
        if start is not None and (not active or i == len(mask) - 1):
            end = i if active and i == len(mask) - 1 else i - 1
            part = d.iloc[start : end + 1]
            if not part.empty:
                layers.append({
                    "kind": kind,
                    "label": label,
                    "severity": severity,
                    "reason": reason,
                    "base_hpa": float(part["pressure_hpa"].max()),
                    "top_hpa": float(part["pressure_hpa"].min()),
                    "base_km": float(part["geopotential_height_km"].min()),
                    "top_km": float(part["geopotential_height_km"].max()),
                })
            start = None
    return layers


def _diagnose_layers(df) -> list[dict[str, object]]:
    d = df.sort_values("geopotential_height_m").reset_index(drop=True)
    temp = d["temperature_c"].to_numpy(dtype=float)
    rh = d["relative_humidity_pct"].to_numpy(dtype=float)
    spread = temp - d["dewpoint_c"].to_numpy(dtype=float)
    cloud = (rh >= 85.0) | ((rh >= 78.0) & (spread <= 3.0))
    icing = cloud & (temp <= 0.0) & (temp >= -20.0)
    turb = (d["gradient_richardson"].to_numpy(dtype=float) < 0.25) | (d["vertical_shear_ms_per_km"].to_numpy(dtype=float) >= 8.0)
    conv = d["thetae_lapse_k_per_km"].to_numpy(dtype=float) <= -3.0
    precip = np.zeros_like(cloud, dtype=bool)
    for col in ("clwmr", "icmr", "rwmr", "snmr", "grle"):
        if col in d:
            precip |= d[col].to_numpy(dtype=float) > 0
    out = []
    out += _layerize(d, cloud, "cloud", "Облачность", 2, "RH≥85% или T−Td≤3 °C")
    out += _layerize(d, icing, "icing", "Обледенение", 3, "влажно и 0…−20 °C")
    out += _layerize(d, turb, "turb", "Турбулентность", 4, "Ri<0.25 или сдвиг≥8 м/с/км")
    out += _layerize(d, conv, "conv", "КНС", 3, "dθe/dz≤−3 K/км")
    out += _layerize(d, precip, "precip", "Осадки", 3, "гидрометеоры GFS")
    return out


def _fmt(value, fmt: str = ".0f") -> str:
    return "—" if value is None or not np.isfinite(float(value)) else format(float(value), fmt)


def _hkm(value: float | None) -> str:
    return "—" if value is None else f"{value / 1000.0:.1f}"


def _ls(layers, kind: str) -> str:
    selected = [x for x in layers if x["kind"] == kind]
    if not selected:
        return "—"
    return ", ".join(f"{x['base_km']:.1f}–{x['top_km']:.1f}" for x in selected[:2]) + ("…" if len(selected) > 2 else "")


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

    ax.add_patch(patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.018", facecolor=CARD_COLORS[min(max(level, 0), 5)], edgecolor=METEO.annotation_edge, linewidth=0.8))
    ax.text(x + 0.025, y + h - 0.055, title, ha="left", va="top", fontsize=8.5, color=METEO.muted_text, fontweight="bold")
    ax.text(x + 0.025, y + h * 0.50, value, ha="left", va="center", fontsize=13.2, color=METEO.axis_text, fontweight="bold")
    ax.text(x + 0.025, y + 0.035, note, ha="left", va="bottom", fontsize=7.2, color=METEO.muted_text)


def _plot_index_cards(ax, df, diag, layers) -> None:
    ax.axis("off")
    ax.text(0.0, 1.02, "Индексы и критические значения", ha="left", va="bottom", fontsize=11, fontweight="bold", color=METEO.axis_text)
    cape_values = [diag.get("sbcape"), diag.get("mlcape"), diag.get("mucape")]
    cape = max([float(v) for v in cape_values if v is not None and np.isfinite(float(v))], default=None)
    cin_values = [diag.get("sbcin"), diag.get("mlcin"), diag.get("mucin")]
    cin = min([float(v) for v in cin_values if v is not None and np.isfinite(float(v))], default=None)
    vmax = float(np.nanmax(df["wind_speed_ms"].to_numpy(dtype=float)))
    shear = float(np.nanmax(df["vertical_shear_ms_per_km"].to_numpy(dtype=float)))
    ri = float(np.nanmin(df["gradient_richardson"].to_numpy(dtype=float)))
    iso = "/".join(_hkm(_interpolate_isotherm_height(df, t)) for t in ISOTHERM_TARGETS_C)
    hazard = max([int(x["severity"]) for x in layers], default=0)
    cards = [
        ("CAPE max", f"{_fmt(cape)} Дж/кг", "≥800 заметно, ≥1500 сильно", _risk_level("cape", cape)),
        ("CIN", f"{_fmt(cin)} Дж/кг", "|CIN|≥50 тормозит конвекцию", _risk_level("cin", cin)),
        ("TT / K", f"{_fmt(diag.get('tt'), '.1f')} / {_fmt(diag.get('k'), '.1f')}", "TT≥50, K≥30 подсвечены", max(_risk_level("tt", diag.get("tt")), _risk_level("k", diag.get("k")))),
        ("LCL/LFC/EL", f"{_fmt(diag.get('lcl'))}/{_fmt(diag.get('lfc'))}/{_fmt(diag.get('el'))}", "гПа", 0),
        ("0/−10/−20 °C", iso, "км", 1),
        ("Vmax / shear", f"{_fmt(vmax, '.1f')} / {_fmt(shear, '.1f')}", "м/с; м/с/км", _risk_level("shear", shear)),
        ("min Ri", _fmt(ri, ".2f"), "Ri<0.25 критично для турб.", _risk_level("ri", ri)),
        ("Слои", f"Лёд {_ls(layers, 'icing')}  Турб {_ls(layers, 'turb')}", "км", hazard),
    ]
    w, h = 0.235, 0.22
    for n, item in enumerate(cards):
        row, col = divmod(n, 4)
        _card(ax, 0.005 + col * 0.248, 0.735 - row * 0.255, w, h, *item)


def _create_metpy_diagram(fig, diagram_type: str):
    from metpy.plots import Emagram, SkewT, Stuve
    if diagram_type == "skewt":
        return SkewT(fig, rotation=30)
    if diagram_type == "emagram":
        return Emagram(fig)
    return Stuve(fig)


def _add_isotherm_guides(ax, df, tmin: float, tmax: float) -> None:
    for target in ISOTHERM_TARGETS_C:
        if tmin <= target <= tmax:
            ax.axvline(target, color=ISOTHERM_LINE_COLORS[target], linewidth=1.0, linestyle="-" if target == 0 else "--", alpha=0.85, zorder=1)
        h = _interpolate_isotherm_height(df, target)
        p = _pressure_at_height(df, h)
        if p is not None and tmin <= target <= tmax:
            ax.text(target, p, f" {int(target)} °C · {h / 1000:.1f} км", ha="left", va="center", fontsize=7.4, color=ISOTHERM_LINE_COLORS[target], bbox={"facecolor": METEO.axes_bg, "edgecolor": "none", "alpha": 0.8, "pad": 1.2}, zorder=9)


def _level(ax, p: float | None, label: str, color: str) -> None:
    if p is None or not 100 <= p <= 1050:
        return
    ax.axhline(p, linewidth=0.8, color=color, linestyle=":", alpha=0.8)
    x0, x1 = ax.get_xlim()
    ax.text(x0 + 0.02 * (x1 - x0), p, label, fontsize=7.4, color=color, va="center", bbox={"facecolor": METEO.axes_bg, "edgecolor": "none", "alpha": 0.82})


def _pressure_axis(ax) -> None:
    ax.set_yscale("log")
    ax.set_ylim(1050, 100)
    ax.set_yticks((1000, 925, 850, 700, 500, 300, 200, 100))
    ax.get_yaxis().set_major_formatter(lambda v, _p: f"{int(v)}")
    style_axis(ax)


def _hazards(ax, layers) -> None:
    cats = [("cloud", "Обл"), ("icing", "Лёд"), ("turb", "Турб"), ("conv", "КНС"), ("precip", "Ос")]
    ax.set_xlim(-0.5, 4.5)
    ax.set_xticks(range(5), [x[1] for x in cats], fontsize=8)
    ax.set_title("Слои риска по высоте", fontsize=9.5, fontweight="bold")
    for i, (kind, _label) in enumerate(cats):
        ax.axvline(i, color=METEO.grid_minor, linewidth=0.6)
        for layer in [x for x in layers if x["kind"] == kind]:
            ax.fill_betweenx([layer["top_hpa"], layer["base_hpa"]], i - 0.36, i + 0.36, color=HAZARD_COLORS[kind], alpha=0.25 + int(layer["severity"]) * 0.10, linewidth=0)
            ax.text(i, math.sqrt(layer["top_hpa"] * layer["base_hpa"]), str(layer["severity"]), ha="center", va="center", fontsize=8, fontweight="bold")
    ax.set_xlabel("риск 1–5")
    _pressure_axis(ax)


def _wind_panel(ax, df) -> None:
    ax.plot(df["wind_speed_ms"], df["pressure_hpa"], color=METEO.wind, linewidth=1.9, label="V")
    ax.plot(df["vertical_shear_ms_per_km"], df["pressure_hpa"], color="#D97706", linewidth=1.45, label="сдвиг")
    ax.axvspan(8, 40, color="#F59E0B", alpha=0.08, linewidth=0)
    ax.axvspan(12, 40, color="#DC2626", alpha=0.07, linewidth=0)
    ax.set_title("Ветер и сдвиг", fontsize=9.5, fontweight="bold")
    ax.set_xlabel("м/с; м/с/км")
    ax.legend(loc="lower right", fontsize=7.5)
    _pressure_axis(ax)
    ax.tick_params(labelleft=False)


def _hodograph(ax, df) -> None:
    p = df.sort_values("geopotential_height_m")
    p = p[p["geopotential_height_m"] <= 8000]
    if len(p) >= 2:
        ax.plot(p["u_wind_ms"], p["v_wind_ms"], color=METEO.wind, linewidth=1.55)
        ax.scatter(p["u_wind_ms"], p["v_wind_ms"], s=14, color=METEO.wind)
        for km in (0, 1, 3, 6):
            row = p.loc[(p["geopotential_height_km"] - km).abs().idxmin()]
            ax.text(row["u_wind_ms"], row["v_wind_ms"], str(km), fontsize=8, fontweight="bold")
    else:
        ax.text(0.5, 0.5, "Годограф\nн/д", ha="center", va="center", transform=ax.transAxes)
    ax.axhline(0, color=METEO.grid_minor, linewidth=0.6)
    ax.axvline(0, color=METEO.grid_minor, linewidth=0.6)
    ax.set_title("Годограф 0–8 км", fontsize=9.5, fontweight="bold")
    ax.set_xlabel("u")
    ax.set_ylabel("v")
    style_axis(ax)


def _legend(ax) -> None:
    ax.axis("off")
    text = (
        "ЛЕГЕНДА\n"
        "Кривая состояния — T среды\n"
        "Td — точка росы\n"
        "Кривая частицы — подъём нижнего уровня\n"
        "CAPE/CIN — плавучесть/задержка\n"
        "LCL/LFC/EL — осн. уровни конвекции\n"
        "Обл — RH≥85% или T−Td≤3 °C\n"
        "Лёд — влажно и 0…−20 °C\n"
        "Турб — Ri<0.25 или shear≥8\n"
        "КНС — dθe/dz≤−3 K/км\n"
        "Цвет карточек: зелёный→красный риск."
    )
    ax.text(0, 1, text, ha="left", va="top", fontsize=8.0, color=METEO.axis_text, bbox=annotation_box_kwargs())


def _plot_metpy_diagram(result: ProfileResult, diagram_type: str, out_path: Path) -> None:
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

    fig = None
    try:
        fig = plt.figure(figsize=(15.8, 9.2), facecolor=METEO.figure_bg)
        diagram = _create_metpy_diagram(fig, diagram_type)
        diagram.ax.set_position([0.055, 0.105, 0.585, 0.79])
        diagram.ax.set_facecolor(METEO.axes_bg)
        diagram.plot(p, t, linewidth=2.75, color="#111827", label="кривая состояния", zorder=8)
        diagram.plot(p, td, linewidth=2.25, color=METEO.dewpoint, label="Td — точка росы", zorder=7)
        if diag.get("parcel") is not None:
            diagram.plot(p, diag["parcel"], linewidth=1.85, color="#B45309", linestyle="--", label="кривая частицы", zorder=7)
            try:
                diagram.shade_cape(p, t, diag["parcel"], alpha=0.22, color="#F97316")
                diagram.shade_cin(p, t, diag["parcel"], alpha=0.18, color="#64748B")
            except Exception:
                pass
        diagram.plot_barbs(p, u, v, xloc=1.045, color=METEO.wind, linewidth=0.72)
        diagram.plot_dry_adiabats(linewidth=0.50, alpha=0.42, color="#C7A569")
        diagram.plot_moist_adiabats(linewidth=0.55, alpha=0.42, color="#3BA99C")
        diagram.plot_mixing_lines(linewidth=0.42, alpha=0.35, color="#74A57F")
        tmin = min(-72.0, float(df[["temperature_c", "dewpoint_c"]].min().min()) - 8.0)
        tmax = max(36.0, float(df["temperature_c"].max()) + 8.0)
        diagram.ax.set_ylim(1050, 100)
        diagram.ax.set_xlim(tmin, tmax)
        _add_isotherm_guides(diagram.ax, df, tmin, tmax)
        _level(diagram.ax, diag.get("lcl"), "LCL", "#6D28D9")
        _level(diagram.ax, diag.get("lfc"), "LFC", "#B45309")
        _level(diagram.ax, diag.get("el"), "EL", "#BE123C")
        style_axis(diagram.ax)
        diagram.ax.set_title(_diagram_title(result, diagram_type), fontsize=10.8, fontweight="bold", color=METEO.axis_text, pad=14)
        diagram.ax.set_xlabel("Температура T, °C")
        diagram.ax.set_ylabel("Давление p, гПа")
        diagram.ax.legend(loc="upper right", fontsize=8.0, framealpha=0.95)

        cards = fig.add_axes([0.665, 0.665, 0.315, 0.23])
        _plot_index_cards(cards, df, diag, layers)
        hz = fig.add_axes([0.665, 0.365, 0.15, 0.25], sharey=diagram.ax)
        _hazards(hz, layers)
        wx = fig.add_axes([0.835, 0.365, 0.145, 0.25], sharey=diagram.ax)
        _wind_panel(wx, df)
        hodo = fig.add_axes([0.665, 0.105, 0.15, 0.22])
        _hodograph(hodo, df)
        leg = fig.add_axes([0.835, 0.105, 0.145, 0.22])
        _legend(leg)
        add_footer(fig, "NOMADS subset • GFS grid, не радиозонд. Подсветка индексов и слоёв — модельная диагностика, не факт наблюдения.", y=0.018)
        fig.savefig(out_path, dpi=180, bbox_inches="tight")
    finally:
        if fig is not None:
            plt.close(fig)


def write_aero_png(result: ProfileResult, diagram_type: str = "stuve") -> Path:
    """Render a full Russian MetPy aerogram and return a temporary PNG path."""
    diagram_type = diagram_type.lower().strip()
    if diagram_type not in SUPPORTED_AERO_DIAGRAMS:
        raise ValueError(f"Неподдерживаемый тип аэродиаграммы: {diagram_type}")
    tmp = tempfile.NamedTemporaryFile(prefix="gfs_aero", suffix=_safe_suffix(result, diagram_type), delete=False)
    out_path = Path(tmp.name)
    tmp.close()
    try:
        _plot_metpy_diagram(result, diagram_type, out_path)
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return out_path
