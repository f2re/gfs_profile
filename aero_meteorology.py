from __future__ import annotations

import numpy as np

import aero_plot as base


def _height_at_pressure_log(frame, pressure_hpa: float | None) -> float | None:
    if pressure_hpa is None or not np.isfinite(float(pressure_hpa)):
        return None
    profile = frame[["pressure_hpa", "geopotential_height_m"]].dropna().sort_values("pressure_hpa")
    pressure = profile["pressure_hpa"].to_numpy(dtype=float)
    height = profile["geopotential_height_m"].to_numpy(dtype=float)
    value = float(pressure_hpa)
    if len(pressure) < 2 or value < pressure.min() or value > pressure.max():
        return None
    return float(np.interp(np.log(value), np.log(pressure), height))


def _layerize_scores(frame, scores, kind: str, label: str, reason: str) -> list[dict[str, object]]:
    ordered = frame.sort_values("geopotential_height_m").reset_index(drop=True)
    values = np.asarray(scores, dtype=int)
    layers: list[dict[str, object]] = []
    start: int | None = None
    for index, score in enumerate(values):
        active = int(score) > 0
        if active and start is None:
            start = index
        if start is not None and (not active or index == len(values) - 1):
            end = index if active and index == len(values) - 1 else index - 1
            part = ordered.iloc[start : end + 1]
            if not part.empty:
                layers.append(
                    {
                        "kind": kind,
                        "label": label,
                        "severity": int(np.max(values[start : end + 1])),
                        "reason": reason,
                        "base_hpa": float(part["pressure_hpa"].max()),
                        "top_hpa": float(part["pressure_hpa"].min()),
                        "base_km": float(part["geopotential_height_km"].min()),
                        "top_km": float(part["geopotential_height_km"].max()),
                    }
                )
            start = None
    return layers


def diagnose_layers(frame) -> list[dict[str, object]]:
    """Translate dimensioned GFS diagnostics into labelled vertical layers."""

    ordered = frame.sort_values("geopotential_height_m").reset_index(drop=True)
    cloud = ordered.get("cloud_proxy")
    if cloud is None:
        temperature = ordered["temperature_c"].to_numpy(dtype=float)
        spread = temperature - ordered["dewpoint_c"].to_numpy(dtype=float)
        rh = ordered["relative_humidity_pct"].to_numpy(dtype=float)
        cloud_scores = ((rh >= 90.0) | ((rh >= 80.0) & (spread <= 2.5))).astype(int)
    else:
        cloud_scores = np.asarray(cloud, dtype=bool).astype(int) * 2

    icing_scores = np.asarray(ordered.get("icing_proxy_score", np.zeros(len(ordered))), dtype=int)
    turbulence_scores = np.asarray(ordered.get("turbulence_proxy_score", np.zeros(len(ordered))), dtype=int)
    thetae_lapse = np.asarray(ordered.get("thetae_lapse_k_per_km", np.full(len(ordered), np.nan)), dtype=float)
    convective_scores = np.where(thetae_lapse <= -3.0, 2, 0)

    precip_mass = np.zeros(len(ordered), dtype=float)
    available = False
    for column in ("rain_mixing_ratio_kgkg", "snow_mixing_ratio_kgkg", "graupel_mixing_ratio_kgkg"):
        if column in ordered:
            available = True
            precip_mass += ordered[column].fillna(0.0).to_numpy(dtype=float)
    precip_scores = np.where(precip_mass >= 1e-7, 1, 0) if available else np.zeros(len(ordered), dtype=int)

    layers: list[dict[str, object]] = []
    layers += _layerize_scores(ordered, cloud_scores, "cloud", "Облачный слой", "гидрометеоры GFS; резерв T/RH")
    layers += _layerize_scores(
        ordered,
        icing_scores,
        "icing",
        "Прокси обледенения",
        "содержание переохлаждённой жидкой воды GFS; резерв T/RH максимум 1",
    )
    layers += _layerize_scores(
        ordered,
        turbulence_scores,
        "turb",
        "Прокси болтанки",
        "вертикальный сдвиг и градиентное число Ричардсона; не EDR",
    )
    layers += _layerize_scores(ordered, convective_scores, "conv", "Конвективная неустойчивость", "dθe/dz ≤ −3 K/км")
    layers += _layerize_scores(ordered, precip_scores, "precip", "Гидрометеоры осадков", "RWMR/SNMR/GRLE на изобарических уровнях")
    return layers


def metpy_diagnostics(frame) -> dict[str, object]:
    """MetPy thermodynamics using the lowest physically available profile row."""

    diagnostics: dict[str, object] = {"parcel": None}
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

        profile = frame.sort_values("pressure_hpa", ascending=False).dropna(subset=["pressure_hpa", "temperature_c", "dewpoint_c"])
        pressure = profile["pressure_hpa"].to_numpy(dtype=float) * units.hPa
        temperature = profile["temperature_c"].to_numpy(dtype=float) * units.degC
        dewpoint = profile["dewpoint_c"].to_numpy(dtype=float) * units.degC
        parcel = parcel_profile(pressure, temperature[0], dewpoint[0]).to("degC")
        sbcape, sbcin = cape_cin(pressure, temperature, dewpoint, parcel)
        diagnostics.update(
            {
                "parcel": parcel,
                "sbcape": base._q(sbcape),
                "sbcin": base._q(sbcin),
                "parcel_source": str(profile.iloc[0].get("level_source", "lowest available GFS level")),
            }
        )

        for key, function in (("ml", mixed_layer_cape_cin), ("mu", most_unstable_cape_cin)):
            try:
                cape, cin = function(pressure, temperature, dewpoint)
                diagnostics[f"{key}cape"] = base._q(cape)
                diagnostics[f"{key}cin"] = base._q(cin)
            except Exception:
                diagnostics[f"{key}cape"] = diagnostics[f"{key}cin"] = None

        try:
            lcl_pressure, _ = lcl(pressure[0], temperature[0], dewpoint[0])
            diagnostics["lcl"] = base._q(lcl_pressure, "hPa")
        except Exception:
            diagnostics["lcl"] = None

        for key, function in (("lfc", lfc), ("el", el)):
            try:
                value, _ = function(pressure, temperature, dewpoint)
                diagnostics[key] = base._q(value, "hPa")
            except Exception:
                diagnostics[key] = None

        try:
            diagnostics["pwat"] = base._q(precipitable_water(pressure, dewpoint), "millimeter")
        except Exception:
            diagnostics["pwat"] = None

        for key, function in (("tt", total_totals_index), ("k", k_index)):
            try:
                diagnostics[key] = base._q(function(pressure, temperature, dewpoint))
            except Exception:
                diagnostics[key] = None
    except Exception as exc:
        diagnostics["error"] = str(exc)
    return diagnostics


def install() -> None:
    if getattr(base, "_METEOROLOGY_AUDIT_PATCHED", False):
        return
    base._diagnose_layers = diagnose_layers
    base._metpy_diagnostics = metpy_diagnostics
    base._height_at_pressure = _height_at_pressure_log
    base._METEOROLOGY_AUDIT_PATCHED = True
