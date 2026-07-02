from __future__ import annotations


def visibility_km(value):
    return None if value is None else max(0.0, float(value)) / 1000.0 if float(value) > 200.0 else max(0.0, float(value))
