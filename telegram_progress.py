from __future__ import annotations

import asyncio
import time
from threading import Lock
from typing import Any

from geocode import GeoPoint
from gfs_core import GfsRun, ProfileResult, build_profile


def _progress_text(run: GfsRun, lead_hour: int, point: GeoPoint, event: dict[str, Any]) -> str:
    stage = str(event.get("stage", "start"))
    header = f"📈 Профиль · {point.label}\nGFS {run.date} {run.cycle}Z · +{lead_hour} ч\n\n"

    if stage == "check":
        return header + "1/5 Проверяю данные…"
    if stage == "grid":
        return header + f"2/5 Узел GFS: {event.get('grid_lat')}, {event.get('grid_lon')}"
    if stage == "cache":
        return header + "3/5 Данные найдены в кэше…"
    if stage in {"download_start", "download"}:
        downloaded = int(event.get("downloaded") or 0)
        total = event.get("total")
        if total:
            pct = min(100, downloaded * 100 / int(total))
            return header + f"3/5 Загружаю данные: {pct:.0f}%"
        return header + "3/5 Загружаю данные…"
    if stage == "download_done":
        return header + "3/5 Данные загружены…"
    if stage == "parse_start":
        return header + "4/5 Читаю профиль…"
    if stage == "parse_done":
        return header + f"4/5 Прочитано уровней: {event.get('rows')}"
    if stage == "done":
        return header + f"5/5 Готово: {event.get('rows')} уровней"
    return header + str(event.get("message", "Выполняю расчёт…"))


async def build_profile_with_progress(status_message, run: GfsRun, lead_hour: int, point: GeoPoint) -> ProfileResult:
    """Run build_profile in a worker thread and keep Telegram message updated."""

    state: dict[str, Any] = {"stage": "check", "message": "Старт"}
    lock = Lock()
    last_text = ""
    stop = False

    def progress_callback(event: dict[str, Any]) -> None:
        with lock:
            state.clear()
            state.update(event)
            state["updated_at"] = time.time()

    async def reporter() -> None:
        nonlocal last_text, stop
        while not stop:
            with lock:
                snapshot = dict(state)
            text = _progress_text(run, lead_hour, point, snapshot)
            if text != last_text:
                try:
                    await status_message.edit_text(text)
                    last_text = text
                except Exception:
                    pass
            await asyncio.sleep(2.0)

    reporter_task = asyncio.create_task(reporter())
    try:
        return await asyncio.to_thread(build_profile, run, lead_hour, point.lat, point.lon, progress_callback)
    finally:
        stop = True
        await reporter_task
