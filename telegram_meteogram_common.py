from __future__ import annotations

"""Install the common meteogram service behind the existing Telegram wizard."""

import asyncio
import html
import time
from threading import Lock

from telegram.constants import ParseMode

from messenger.contracts import ProgressEvent
from messenger.meteogram_service import build_meteogram_product_result
from messenger.profile_service import cleanup_product_result
from telegram_file_send import reply_png_file


def install() -> None:
    import telegram_meteogram as module

    if getattr(module, "_COMMON_METEOGRAM_SERVICE_INSTALLED", False):
        return

    async def run_product(message, point, request, user=None, *, output_format="png") -> bool:
        source = module.source_for_id(request.source_id)
        status = await message.reply_text(
            f"⏳ Метеограмма · {source.label}\n📍 {point.label}\n1/5 Получаю прогноз…"
        )
        started = time.perf_counter()
        request_id = module.record_request_start(
            product="meteogram",
            user_id=int(getattr(user, "id", 0) or 0) or None,
            username=getattr(user, "username", None),
            city=point.label,
            request_text=module._repeat_command(point, request, output_format),
            lead_from=0,
            lead_to=int(request.days) * 24,
        )
        state = {"event": ProgressEvent("fetch_start", "Получаю прогноз")}
        lock = Lock()
        stop = asyncio.Event()
        last = ""
        result = None

        def progress(event: ProgressEvent) -> None:
            with lock:
                state["event"] = event

        def progress_text(event: ProgressEvent) -> str:
            if event.stage == "fetch_start": body = "1/5 Получаю прогноз…"
            elif event.stage == "fetch": body = f"2/5 {event.message}…"
            elif event.stage == "plot_start": body = "3/5 Строю PNG…"
            elif event.stage == "report_start": body = f"4/5 {event.message}…"
            else: body = event.message or "Выполняю расчёт…"
            return f"⏳ Метеограмма · {source.label}\n📍 {point.label}\n{body}"

        async def reporter() -> None:
            nonlocal last
            while not stop.is_set():
                with lock:
                    event = state["event"]
                text = progress_text(event)
                if text != last:
                    try:
                        await status.edit_text(text)
                        last = text
                    except Exception:
                        pass
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.5)
                except asyncio.TimeoutError:
                    pass

        task = asyncio.create_task(reporter())
        try:
            async with module.METEOGRAM_SEMAPHORE:
                result = await asyncio.to_thread(
                    build_meteogram_product_result,
                    point,
                    request.source_id,
                    request.days,
                    output_format,
                    progress_callback=progress,
                )
            stop.set()
            await task
            await status.edit_text(result.summary)
            for attachment in result.attachments:
                if attachment.kind == "image":
                    await reply_png_file(message, attachment.path, caption=attachment.caption[:1024], prefer_photo=True)
                else:
                    with attachment.path.open("rb") as document:
                        await message.reply_document(document=document, filename=attachment.filename, caption=attachment.caption[:1024])
            if result.repeat_command:
                await message.reply_text(f"📋 <code>{html.escape(result.repeat_command)}</code>", parse_mode=ParseMode.HTML)
            module.record_request_finish(request_id, status="ok", duration_ms=int((time.perf_counter() - started) * 1000))
            return True
        except Exception as exc:
            stop.set()
            await task
            await status.edit_text(f"Ошибка метеограммы: {exc}")
            module.record_request_finish(
                request_id,
                status="failed",
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc)[:500],
            )
            return False
        finally:
            stop.set()
            if not task.done():
                await task
            if result is not None:
                cleanup_product_result(result)

    module._run_product = run_product
    module._COMMON_METEOGRAM_SERVICE_INSTALLED = True
