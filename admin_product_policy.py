from __future__ import annotations

"""Merge historical /skewt statistics into the single /aero product."""

from typing import Any


def install(namespace: dict[str, Any]) -> None:
    import admin_stats

    if getattr(admin_stats, "_AERO_ALIAS_INSTALLED", False):
        return

    original_usage_summary = admin_stats.usage_summary
    original_recent = namespace.get("format_recent_requests", admin_stats.format_recent_requests)

    def usage_summary(days: int = 7, db_path=None) -> dict[str, object]:
        data = dict(original_usage_summary(days=days, db_path=db_path))
        merged: dict[str, tuple[int, int]] = {}
        for product, count, avg_ms in data.get("products", []):
            key = "aero" if product == "skewt" else str(product)
            old_count, old_weighted = merged.get(key, (0, 0))
            merged[key] = (old_count + int(count), old_weighted + int(count) * int(avg_ms))
        data["products"] = [
            (product, count, int(weighted / count) if count else 0)
            for product, (count, weighted) in sorted(merged.items(), key=lambda item: item[1][0], reverse=True)
        ][:8]
        return data

    def format_recent_requests(limit: int = 10, db_path=None) -> str:
        return original_recent(limit=limit, db_path=db_path).replace("skewt", "aero ")

    admin_stats.usage_summary = usage_summary
    namespace["format_recent_requests"] = format_recent_requests
    admin_stats._AERO_ALIAS_INSTALLED = True
