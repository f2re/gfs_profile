from __future__ import annotations

"""Install common route service behind the existing Telegram route wizard."""

import asyncio
import html
import time
from threading import Lock

from telegram.constants import ParseMode

from messenger.contracts import ProgressEvent
from messenger.profile_service import cleanup_product_result
from messenger.route_service import build_route_product_result
from telegram_file_send import reply_png_file


def install() -> None:
    import telegram_route as module

    if getattr(module, "_COMMON_ROUTE_SERVICE_INSTALLED", False):
        return

    async def run_route_product(message, origin, destination, parsed, *, user=None) -> bool:
        status = await message.reply_text(
            f"⏳ Маршрутный профиль GFS\n🧭 {origin.label} → {destination.label}\n1/6 Проверяю опубликованный цикл…"
        )
        started = time.perf_counter()
        request_id = module.record_request_start(
            product="route",
            user_id=int(getattr(user, "id", 0) or 0) or None,
            username=getattr(user, "username", None),
            city=f"{origin.label} -> {destination.label}",
            request_text=f"route:{origin.label}->{destination.label}",
            lead_from=parsed.departure_lead,
            lead_to=parsed.departure_lead,
            run_date=parsed.run.date if parsed.run else None,
            run_cycle=parsed.run.cycle if parsed.run else None,
        )
        state = {"event": ProgressEvent("check", "")}
        lock = Lock(); stop = asyncio.Event(); last = ""; result = None

        def progress(event: ProgressEvent) -> None:
            with lock: state["event"] = event

        def render(event: ProgressEvent) -> str:
            data = dict(event.data)
            header = (
                f"⏳ Маршрутный профиль GFS\n🧭 {origin.label} → {destination.label}\n"
                f"+{parsed.departure_lead} ч · {parsed.speed_kmh} км/ч · сетка {parsed.spatial_step_km} км\n"
            )
            if event.stage in {"check", "run"}:
                body = "1/6 Проверяю опубликованный цикл…"
                if event.stage == "run" and data.get("run_date"):
                    body = f"1/6 GFS {data['run_date']} {data.get('run_cycle')}Z"
            elif event.stage in {"route_step", "download_start", "download", "download_done", "cache"}:
                body = "2/6 Загружаю профили вдоль маршрута…"
            elif event.stage in {"parse_start", "parse_done"}:
                body = "3/6 Читаю вертикальные профили…"
            elif event.stage in {"plot_start", "plot_done"}:
                body = "5/6 Формирую PNG/CSV…"
            else:
                body = event.message or "Выполняю расчёт…"
            return header + body

        async def reporter() -> None:
            nonlocal last
            while not stop.is_set():
                with lock: event = state["event"]
                text = render(event)
                if text != last:
                    try: await status.edit_text(text); last = text
                    except Exception: pass
                try: await asyncio.wait_for(stop.wait(), timeout=1.5)
                except asyncio.TimeoutError: pass

        task = asyncio.create_task(reporter())
        try:
            semaphore = module._GFS_SEMAPHORE
            if semaphore is None:
                result = await asyncio.to_thread(
                    build_route_product_result,
                    origin, destination, parsed.departure_lead, parsed.speed_kmh, parsed.mode,
                    parsed.spatial_step_km, parsed.run, progress_callback=progress,
                    run_selector=module.latest_available_run_for_lead,
                )
            else:
                async with semaphore:
                    result = await asyncio.to_thread(
                        build_route_product_result,
                        origin, destination, parsed.departure_lead, parsed.speed_kmh, parsed.mode,
                        parsed.spatial_step_km, parsed.run, progress_callback=progress,
                        run_selector=module.latest_available_run_for_lead,
                    )
            stop.set(); await task
            await status.edit_text(result.summary)
            for attachment in result.attachments:
                if attachment.kind == "image":
                    await reply_png_file(message, attachment.path, caption=attachment.caption[:1024], prefer_photo=True)
                else:
                    with attachment.path.open("rb") as file_obj:
                        await message.reply_document(document=file_obj, filename=attachment.filename, caption=attachment.caption[:1024])
            if result.repeat_command:
                await message.reply_text(f"📋 <code>{html.escape(result.repeat_command)}</code>", parse_mode=ParseMode.HTML)
            module.record_request_finish(request_id, status="ok", duration_ms=int((time.perf_counter() - started) * 1000))
            return True
        except Exception as exc:
            stop.set(); await task
            # Preserve the established Telegram smoke/error contract.
            await status.edit_text(f"Ошибка: {exc}")
            module.record_request_finish(request_id, status="failed", duration_ms=int((time.perf_counter() - started) * 1000), error=str(exc)[:500])
            return False
        finally:
            stop.set()
            if not task.done(): await task
            if result is not None: cleanup_product_result(result)

    module.run_route_product = run_route_product
    module._COMMON_ROUTE_SERVICE_INSTALLED = True
