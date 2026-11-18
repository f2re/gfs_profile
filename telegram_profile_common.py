from __future__ import annotations

import asyncio
import html
from threading import Lock
from typing import Any

from messenger.contracts import ProgressEvent
from messenger.profile_service import build_profile_product, cleanup_product_result


def _progress_text(point: Any, lead_hour: int, event: ProgressEvent) -> str:
    data = dict(event.data)
    header = (
        "⏳ Профиль GFS\n"
        f"📍 {getattr(point, 'label', '')}\n"
        f"🕒 срок +{int(lead_hour)} ч\n"
    )
    if event.stage == "check":
        body = "1/5 Проверяю данные…"
    elif event.stage == "grid":
        body = f"2/5 Узел GFS: {data.get('grid_lat')}, {data.get('grid_lon')}"
    elif event.stage == "cache":
        body = "3/5 Данные найдены в кэше…"
    elif event.stage in {"download_start", "download", "download_done"}:
        total = data.get("total")
        downloaded = data.get("downloaded")
        if total and downloaded:
            pct = min(100.0, float(downloaded) * 100.0 / float(total))
            body = f"3/5 Загружаю данные: {pct:.0f}%"
        else:
            body = "3/5 Загружаю данные…"
    elif event.stage in {"parse_start", "parse_done", "done"}:
        body = "4/5 Читаю профиль…"
    elif event.stage in {"plot_start", "plot_done"}:
        body = "5/5 Формирую PNG и CSV…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


async def _run_common_profile(namespace: dict[str, Any], message, point, lead_hour: int, run=None) -> bool:
    from geocode import GeocodeError
    from gfs_core import GfsProfileError, GfsRun
    from telegram import InputFile
    from telegram.constants import ParseMode

    status = await message.reply_text(
        "⏳ Профиль GFS\n"
        f"📍 {point.label}\n"
        f"🕒 срок +{lead_hour} ч\n"
        "1/5 Проверяю опубликованный цикл GFS…"
    )
    state = {"event": ProgressEvent(stage="check", message="Проверяю данные")}
    lock = Lock()
    stop = asyncio.Event()
    last_text = ""
    result = None

    def progress(event: ProgressEvent) -> None:
        with lock:
            state["event"] = event

    async def reporter() -> None:
        nonlocal last_text
        while not stop.is_set():
            with lock:
                event = state["event"]
            text = _progress_text(point, lead_hour, event)
            if text != last_text:
                try:
                    await status.edit_text(text)
                    last_text = text
                except Exception:
                    pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    reporter_task = asyncio.create_task(reporter())
    try:
        async with namespace["GFS_SEMAPHORE"]:
            result = await asyncio.to_thread(
                build_profile_product,
                point,
                lead_hour,
                run,
                progress_callback=progress,
            )
        stop.set()
        await reporter_task

        summary_html = str(result.metadata.get("summary_html") or html.escape(result.summary))
        await status.edit_text(summary_html, parse_mode=ParseMode.HTML)
        for attachment in result.attachments:
            with attachment.path.open("rb") as file_obj:
                input_file = InputFile(file_obj, filename=attachment.filename)
                if attachment.kind == "image":
                    await message.reply_photo(photo=input_file, caption=attachment.caption)
                elif attachment.kind == "animation":
                    await message.reply_animation(animation=input_file, caption=attachment.caption)
                else:
                    await message.reply_document(document=input_file, caption=attachment.caption)

        run_date = str(result.metadata.get("run_date") or "")
        run_cycle = str(result.metadata.get("run_cycle") or "")
        if run_date and run_cycle:
            selected_run = GfsRun(run_date, run_cycle)
            await message.reply_text(
                namespace["_profile_repeat_message"](point, lead_hour, selected_run),
                parse_mode=ParseMode.HTML,
            )
        elif result.repeat_command:
            await message.reply_text(
                f"📋 Повторить профиль:\n<code>{html.escape(result.repeat_command)}</code>",
                parse_mode=ParseMode.HTML,
            )
        return True
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        stop.set()
        await reporter_task
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        stop.set()
        await reporter_task
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        stop.set()
        if not reporter_task.done():
            await reporter_task
        if result is not None:
            cleanup_product_result(result)
    return False


def install(namespace: dict[str, Any]) -> None:
    """Replace only Telegram profile execution with the common profile service."""

    async def run_profile(message, point, lead_hour: int, run=None) -> bool:
        return await _run_common_profile(namespace, message, point, lead_hour, run)

    namespace["run_profile"] = run_profile
