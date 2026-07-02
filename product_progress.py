from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from threading import Lock
from typing import Any, TypeVar

T = TypeVar("T")


def _progress_text(header: str, event: dict[str, Any]) -> str:
    stage = str(event.get("stage", "start"))
    message = str(event.get("message", "Выполняю расчёт…"))

    if stage == "check":
        body = "1/6 Проверяю публикацию forecast-файла…"
    elif stage == "grid":
        body = f"2/6 Узел GFS: {event.get('grid_lat')}, {event.get('grid_lon')}"
    elif stage == "cache":
        body = "3/6 GRIB2 найден в кэше. Читаю профиль…"
    elif stage in {"download_start", "download"}:
        downloaded = int(event.get("downloaded") or 0)
        total = event.get("total")
        if total:
            pct = min(100, downloaded * 100 / int(total))
            body = f"3/6 Скачиваю GRIB2: {pct:.0f}%"
        else:
            body = "3/6 Скачиваю GRIB2…"
    elif stage == "download_done":
        body = "3/6 GRIB2 загружен. Читаю cfgrib/eccodes…"
    elif stage == "parse_start":
        body = "4/6 Читаю GRIB2 через cfgrib/eccodes…"
    elif stage == "parse_done":
        body = f"4/6 Профиль разобран: {event.get('rows')} уровней"
    elif stage == "done":
        body = f"5/6 Профиль готов: {event.get('rows')} уровней"
    elif stage == "plot_start":
        body = "5/6 Строю графический продукт…"
    elif stage == "plot_done":
        body = "6/6 Графический продукт готов. Отправляю…"
    elif stage == "windgram_step":
        body = f"{event.get('index')}/{event.get('total')} срок +{event.get('lead_hour')} ч: {message}"
    elif stage == "map_cycle":
        body = "1/7 Выбираю опубликованный цикл GFS…"
    elif stage == "map_cache":
        body = "2/7 Пространственный GRIB2 найден в кэше…"
    elif stage in {"map_download_start", "map_download"}:
        downloaded = int(event.get("downloaded") or 0)
        total = event.get("total")
        if total:
            pct = min(100, downloaded * 100 / int(total))
            body = f"2/7 Скачиваю spatial GRIB2: {pct:.0f}%"
        else:
            body = "2/7 Скачиваю spatial GRIB2…"
    elif stage == "map_download_done":
        body = "3/7 GRIB2 карты загружен. Читаю cfgrib/eccodes…"
    elif stage == "map_parse":
        body = "4/7 Читаю поля: осадки, облачность, гроза, видимость, AT500…"
    elif stage == "map_done":
        body = "5/7 Поля карты рассчитаны. Готовлю визуализацию…"
    elif stage == "map_plot":
        body = "6/7 Рисую карту, подложку, кольца, легенду и подписи…"
    elif stage == "map_plot_done":
        body = "7/7 PNG оптимизирован для Telegram. Отправляю…"
    elif stage == "map_step":
        body = f"Кадр {event.get('index')}/{event.get('total')} · срок +{event.get('lead_hour')} ч: {message}"
    elif stage == "map_animation_start":
        body = "Собираю GIF-анимацию для Telegram…"
    elif stage == "map_animation_frame":
        body = f"GIF: кадр {event.get('index')}/{event.get('total')} · +{event.get('lead_hour')} ч"
    elif stage == "map_animation_done":
        body = "GIF-анимация оптимизирована для Telegram. Отправляю…"
    else:
        body = message

    return f"{header}\n\n{body}"


async def run_product_with_progress(
    status_message,
    header: str,
    worker: Callable[[Callable[[dict[str, Any]], None]], T],
    interval_seconds: float = 2.0,
) -> T:
    """Run a blocking product worker in a thread and update one Telegram status message."""

    state: dict[str, Any] = {"stage": "start", "message": "Старт"}
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
            text = _progress_text(header, snapshot)
            if text != last_text:
                try:
                    await status_message.edit_text(text)
                    last_text = text
                except Exception:
                    pass
            await asyncio.sleep(interval_seconds)

    reporter_task = asyncio.create_task(reporter())
    try:
        return await asyncio.to_thread(worker, progress_callback)
    finally:
        stop = True
        await reporter_task
