from __future__ import annotations

"""Final-result copy policy.

Keep the GFS model label in every product, but avoid repeating long generic
warnings in status, caption and repeat-command messages. Scientific caveats
remain in the product summary or PNG footer.
"""


def install() -> None:
    import telegram_aero as aero
    import telegram_cloudgram as cloud
    import telegram_map as map_module
    import telegram_route as route
    import telegram_windgram as wind

    if getattr(map_module, "_RESULT_COPY_PATCHED", False):
        return

    def aero_caption(result) -> str:
        return (
            "🧾 GFS · аэрологическая диаграмма\n"
            f"{result.run.date} {result.run.cycle}Z · +{result.lead_hour} ч · {result.valid_time_utc:%d.%m %H:%M UTC}\n"
            f"Узел {result.grid_lat:.3f}, {result.grid_lon:.3f}"
        )

    def wind_caption(data) -> str:
        step = data.leads[1] - data.leads[0] if len(data.leads) > 1 else 0
        name = wind.PARAM_NAMES.get(data.param, data.param)
        return (
            f"🟦 GFS Windgram · {name}\n"
            f"{data.run.date} {data.run.cycle}Z · +{data.leads[0]}…+{data.leads[-1]} ч · шаг {step} ч\n"
            f"Узел {data.grid_lat:.3f}, {data.grid_lon:.3f}"
        )

    def cloud_caption(data, mode: str = "pro") -> str:
        step = data.leads[1] - data.leads[0] if len(data.leads) > 1 else 0
        maximum = max((cell.hazard_score for cell in data.cells), default=0)
        missing = f"\nНет полей: {', '.join(data.missing_fields)}" if data.missing_fields else ""
        return (
            f"☁️ GFS Cloudgram · {'SIMPLE' if mode == 'simple' else 'PRO'}\n"
            f"{data.run.date} {data.run.cycle}Z · +{data.leads[0]}…+{data.leads[-1]} ч · шаг {step} ч\n"
            f"Максимальная оценка: {cloud._hazard_label(maximum)}{missing}"
        )

    def map_status(data: dict, *, animated: bool = False, series: bool = False, lead_count: int = 1) -> str:
        run = data["run"]
        point = data["point"]
        title = "🗺️ GFS карта"
        if animated:
            title = f"🗺️ GFS анимация · {lead_count} кадров"
        elif series:
            title = f"🗺️ GFS серия карт · {lead_count} кадров"
        return f"{title}\n📍 {point.label}\n🕒 {run.date} {run.cycle}Z · +{data['lead_hour']} ч"

    def map_file_caption(data: dict, *, animated: bool = False, series: bool = False, animation_format: str = "MP4-анимация") -> str:
        run = data["run"]
        point = data["point"]
        kind = animation_format if animated else "PNG-серия" if series else "PNG"
        lines = [
            f"{kind} · GFS MAP · {run.date} {run.cycle}Z",
            f"{point.label} · +{int(data['lead_hour'])} ч · радиус {int(data['radius_km'])} км",
        ]
        missing = data.get("missing") or set()
        if missing:
            lines.append("Нет полей: " + ", ".join(sorted(missing)))
        return "\n".join(lines)

    original_route_summary = route.route_summary

    def route_summary(data) -> str:
        lines = [
            line
            for line in original_route_summary(data).splitlines()
            if not line.startswith("ℹ ")
        ]
        return "\n".join(lines)

    aero.format_aero_caption = aero_caption
    wind.format_windgram_caption = wind_caption
    cloud.format_cloudgram_caption = cloud_caption
    map_module.format_map_status = map_status
    map_module.format_map_file_caption = map_file_caption
    route.route_summary = route_summary
    map_module._RESULT_COPY_PATCHED = True
