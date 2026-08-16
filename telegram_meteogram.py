from __future__ import annotations

import asyncio
import html
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from admin_stats import record_request_finish, record_request_start, record_telegram_user
from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from meteogram_core import MeteogramError, available_periods, fetch_meteogram, source_for_id, sources_by_kind
from meteogram_plot import write_meteogram_png
from meteogram_request import MeteogramRequest, parse_meteogram_request
from meteogram_report import MeteogramReportError, write_meteogram_report
from telegram_file_send import reply_png_file
from user_location_session import get_recent_locations, match_recent_location_button, remember_location

SESSION_KEY = "meteogram_wizard"
MAX_CONCURRENT_METEOGRAM = max(1, int(os.getenv("MAX_CONCURRENT_METEOGRAM", "2")))
METEOGRAM_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_METEOGRAM)


METEOGRAM_OUTPUT_FORMATS = ("png", "docx", "pdf")
_OUTPUT_FORMAT_RE = re.compile(
    r"(?<!\S)(?:format|output|report|формат)\s*=\s*([^\s]+)",
    re.IGNORECASE,
)


def _normalise_output_format(value: object) -> str:
    key = str(value or "png").strip().lower().lstrip(".")
    aliases = {
        "image": "png",
        "photo": "png",
        "картинка": "png",
        "word": "docx",
        "document": "docx",
        "документ": "docx",
        "portable": "pdf",
    }
    key = aliases.get(key, key)
    if key not in METEOGRAM_OUTPUT_FORMATS:
        raise MeteogramError("Формат результата: png, docx или pdf")
    return key


def _extract_output_format(raw: str) -> tuple[str, str]:
    matches = [_normalise_output_format(item) for item in _OUTPUT_FORMAT_RE.findall(raw)]
    if len(set(matches)) > 1:
        raise MeteogramError("Указаны противоречивые форматы результата")
    output_format = matches[0] if matches else "png"
    cleaned = " ".join(_OUTPUT_FORMAT_RE.sub(" ", raw).split())
    if not cleaned:
        raise MeteogramError("Не указана точка прогноза")
    return output_format, cleaned


def _output_label(value: object) -> str:
    return {
        "png": "PNG-метеограмма",
        "docx": "отчёт DOCX",
        "pdf": "отчёт PDF",
    }[_normalise_output_format(value)]


def _output_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🖼 PNG · быстро", callback_data="meteo:format:png")],
            [
                InlineKeyboardButton("📄 DOCX · отчёт", callback_data="meteo:format:docx"),
                InlineKeyboardButton("🧾 PDF · отчёт", callback_data="meteo:format:pdf"),
            ],
            [
                InlineKeyboardButton("Назад", callback_data="meteo:back:period"),
                InlineKeyboardButton("Отмена", callback_data="meteo:cancel"),
            ],
        ]
    )


def _output_prompt(source_id: str, days: int) -> str:
    source = source_for_id(source_id)
    kind = "ансамбль" if source.ensemble else "одна модель"
    return (
        f"{source.label} · {kind} · {days} сут\n"
        "Выберите результат.\n\n"
        "🖼 PNG — только метеограмма.\n"
        "📄 DOCX / 🧾 PDF — краткая сводка, таблицы по суткам и срокам и метеограмма."
    )


def _confirm_keyboard(output_format: str) -> InlineKeyboardMarkup:
    action = {
        "png": "Построить PNG",
        "docx": "Создать DOCX",
        "pdf": "Создать PDF",
    }[_normalise_output_format(output_format)]
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(action, callback_data="meteo:run")],
            [
                InlineKeyboardButton("Формат", callback_data="meteo:back:format"),
                InlineKeyboardButton("Период", callback_data="meteo:back:period"),
            ],
            [
                InlineKeyboardButton("Модель", callback_data="meteo:back:model"),
                InlineKeyboardButton("Отмена", callback_data="meteo:cancel"),
            ],
        ]
    )


def _pack_point(point: GeoPoint) -> dict[str, object]:
    return {"lat": point.lat, "lon": point.lon, "label": point.label, "source": point.source}


def _unpack_point(value: dict[str, object]) -> GeoPoint:
    return GeoPoint(float(value["lat"]), float(value["lon"]), str(value["label"]), str(value.get("source", "manual")))


def _point_keyboard(user_id: int):
    import telegram_concise_ux
    return telegram_concise_ux.point_keyboard(get_recent_locations(user_id))


def _remove_keyboard(message):
    import telegram_concise_ux
    return telegram_concise_ux._remove_reply_keyboard(message)


def _type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Одна модель", callback_data="meteo:kind:deterministic"), InlineKeyboardButton("Ансамбль", callback_data="meteo:kind:ensemble")],
        [InlineKeyboardButton("Отмена", callback_data="meteo:cancel")],
    ])


def _model_keyboard(ensemble: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(source.label, callback_data=f"meteo:source:{source.source_id}")] for source in sources_by_kind(ensemble)]
    rows.append([InlineKeyboardButton("Назад", callback_data="meteo:back:type"), InlineKeyboardButton("Отмена", callback_data="meteo:cancel")])
    return InlineKeyboardMarkup(rows)


def _period_keyboard(source_id: str) -> InlineKeyboardMarkup:
    source = source_for_id(source_id)
    periods = available_periods(source)
    rows = []
    for index in range(0, len(periods), 3):
        rows.append([InlineKeyboardButton(f"{days} сут", callback_data=f"meteo:days:{days}") for days in periods[index:index + 3]])
    rows.append([InlineKeyboardButton("Другая модель", callback_data="meteo:back:model"), InlineKeyboardButton("Отмена", callback_data="meteo:cancel")])
    return InlineKeyboardMarkup(rows)


def _place_keyboard(candidates: list[GeoPoint]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(" ".join(point.label.split())[:58], callback_data=f"meteo:place:{index}")] for index, point in enumerate(candidates[:5])]
    rows.append([InlineKeyboardButton("Отмена", callback_data="meteo:cancel")])
    return InlineKeyboardMarkup(rows)


def _summary(state: dict[str, object]) -> str:
    point = _unpack_point(state["point"])
    source = source_for_id(str(state["source_id"]))
    kind = "ансамбль" if source.ensemble else "одна модель"
    output_format = _normalise_output_format(state.get("output_format", "png"))
    return (
        f"📊 Метеограмма\n"
        f"📍 {point.label} · {point.lat:.4f}, {point.lon:.4f}\n"
        f"Модель: {source.label} · {kind}\n"
        f"Период: {int(state['days'])} суток\n"
        f"Результат: {_output_label(output_format)}"
    )


def _repeat_command(
    point: GeoPoint,
    request: MeteogramRequest,
    output_format: str = "png",
) -> str:
    return (
        f"/meteogram {point.lat:.4f} {point.lon:.4f} "
        f"source={request.source_id} days={request.days} "
        f"format={_normalise_output_format(output_format)}"
    )


async def meteogram_command(update: Update, context) -> None:
    message = update.effective_message
    if not message:
        return
    record_telegram_user(update.effective_user)
    raw = " ".join(context.args or []).strip()
    if not raw:
        context.user_data[SESSION_KEY] = {"step": "point"}
        await message.reply_text("📊 Метеограмма\n\nУкажите город, координаты или отправьте геолокацию.", reply_markup=_point_keyboard(int(update.effective_user.id if update.effective_user else 0)))
        return
    await _remove_keyboard(message)
    await _resolve_direct(message, raw, update.effective_user)


async def _resolve_direct(message, raw: str, user) -> bool:
    try:
        output_format, request_text = _extract_output_format(raw)
        request = parse_meteogram_request(request_text)
        candidates = await asyncio.to_thread(
            search_location_candidates,
            request.location_query,
            3,
        )
    except (MeteogramError, GeocodeError, ValueError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return False
    if not candidates:
        await message.reply_text("Точка не найдена. Уточните город или координаты.")
        return False
    if len(candidates) > 1:
        labels = "\n".join(
            f"{index + 1}. {point.label}" for index, point in enumerate(candidates)
        )
        await message.reply_text(
            "Найдено несколько точек. Уточните запрос или используйте координаты:"
            f"\n\n{labels}"
        )
        return False
    point = candidates[0]
    remember_location(int(getattr(user, "id", 0) or 0), point)
    return await _run_product(
        message,
        point,
        request,
        user,
        output_format=output_format,
    )


async def _run_product(
    message,
    point: GeoPoint,
    request: MeteogramRequest,
    user=None,
    *,
    output_format: str = "png",
) -> bool:
    output_format = _normalise_output_format(output_format)
    source = source_for_id(request.source_id)
    total_steps = 5 if output_format == "png" else 6
    status = await message.reply_text(
        f"⏳ Метеограмма · {source.label}\n"
        f"📍 {point.label}\n"
        f"1/{total_steps} Загружаю прогноз…"
    )
    started = time.perf_counter()
    request_id = record_request_start(
        product="meteogram",
        user_id=int(getattr(user, "id", 0) or 0) or None,
        username=getattr(user, "username", None),
        city=point.label,
        request_text=_repeat_command(point, request, output_format),
        lead_from=0,
        lead_to=request.days * 24,
    )
    png_path: Path | None = None
    report_result = None
    report_dir: Path | None = None
    progress_state = {"text": f"1/{total_steps} Загружаю прогноз…"}
    progress_step = 1
    stop = False

    def progress(text: str) -> None:
        nonlocal progress_step
        progress_step = min(3, progress_step + 1)
        progress_state["text"] = f"{progress_step}/{total_steps} {text}…"

    async def reporter() -> None:
        last = ""
        while not stop:
            text = (
                f"⏳ Метеограмма · {source.label}\n"
                f"📍 {point.label}\n"
                f"{progress_state['text']}"
            )
            if text != last:
                try:
                    await status.edit_text(text)
                    last = text
                except Exception:
                    pass
            await asyncio.sleep(1.5)

    reporter_task = asyncio.create_task(reporter())
    try:
        async with METEOGRAM_SEMAPHORE:
            series = await asyncio.to_thread(
                fetch_meteogram,
                request.source_id,
                point.label,
                point.lat,
                point.lon,
                request.days,
                progress,
            )
            progress_state["text"] = (
                f"4/{total_steps} Строю метеограмму…"
            )
            png_path = Path(await asyncio.to_thread(write_meteogram_png, series))
            if output_format != "png":
                progress_state["text"] = (
                    f"5/{total_steps} Формирую файл…"
                )
                report_dir = Path(tempfile.mkdtemp(prefix="gfs_meteogram_report_"))
                report_result = await asyncio.to_thread(
                    write_meteogram_report,
                    series,
                    png_path,
                    output_format,
                    output_dir=report_dir,
                )

        progress_state["text"] = (
            f"{total_steps}/{total_steps} Отправляю файл…"
        )
        member_line = ""
        warning_line = ""
        if source.ensemble:
            observed = series.member_count or 0
            expected = series.expected_member_count or observed
            member_line = f"\nАнсамбль: {observed}/{expected} членов"
            per_time = series.values("ensemble_member_count")
            finite_counts = per_time[np.isfinite(per_time)]
            minimum_per_time = (
                int(np.nanmin(finite_counts)) if finite_counts.size else observed
            )
            if expected and minimum_per_time < expected:
                warning_line = (
                    f"\n⚠️ На отдельных сроках доступно от "
                    f"{minimum_per_time}/{expected} членов."
                )

        actual_format = output_format
        fallback_line = ""
        if report_result is not None:
            actual_format = report_result.format
            if report_result.fallback_reason:
                fallback_line = (
                    "\n⚠️ PDF создать не удалось; отправляю DOCX."
                )
        await status.edit_text(
            f"📊 {'Ансамблевая ' if source.ensemble else ''}метеограмма готова\n"
            f"📍 {point.label}\n"
            f"{source.model}\n"
            f"{source.provider}{member_line}\n"
            f"{series.times[0]:%d.%m %H:%M} — "
            f"{series.times[-1]:%d.%m %H:%M} · местное время"
            f"{warning_line}{fallback_line}\n"
            f"Результат: {_output_label(actual_format)}\n"
            "ℹ Модельный прогноз, не наблюдение."
        )

        if output_format == "png":
            caption = (
                f"PNG · METEOGRAM · {source.model} · {source.provider} · "
                f"{request.days} сут · {point.label}"
            )[:1024]
            await reply_png_file(
                message,
                png_path,
                caption=caption,
                prefer_photo=True,
            )
        else:
            if report_result is None:
                raise MeteogramReportError("Отчёт не сформирован")
            kind = "Ансамблевый отчёт" if source.ensemble else "Модельный отчёт"
            caption = (
                f"{report_result.format.upper()} · {kind} · {source.model} · "
                f"{request.days} сут · {point.label}"
            )[:1024]
            with report_result.path.open("rb") as document:
                await message.reply_document(
                    document=document,
                    filename=report_result.path.name,
                    caption=caption,
                )
            if report_result.fallback_reason:
                await message.reply_text(
                    "PDF сформировать не удалось. DOCX содержит ту же сводку, "
                    "таблицы, текст и метеограмму."
                )

        await message.reply_text(
            f"📋 <code>{html.escape(_repeat_command(point, request, output_format))}</code>",
            parse_mode=ParseMode.HTML,
        )
        record_request_finish(
            request_id,
            status="ok",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return True
    except (MeteogramError, MeteogramReportError, ValueError) as exc:
        await status.edit_text(f"Ошибка метеограммы: {exc}")
        record_request_finish(
            request_id,
            status="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc)[:500],
        )
        return False
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
        record_request_finish(
            request_id,
            status="error",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc)[:500],
        )
        return False
    finally:
        stop = True
        await reporter_task
        if png_path:
            png_path.unlink(missing_ok=True)
        if report_result is not None:
            for path in report_result.cleanup_paths:
                path.unlink(missing_ok=True)
        if report_dir is not None:
            shutil.rmtree(report_dir, ignore_errors=True)


async def meteogram_text(update: Update, context) -> None:
    state = context.user_data.get(SESSION_KEY)
    message = update.effective_message
    if not isinstance(state, dict) or not message or not message.text:
        return
    text = message.text.strip()
    if text == "✖ Отмена":
        context.user_data.pop(SESSION_KEY, None)
        await _show_home(message, "Выбор метеограммы сброшен.")
        raise ApplicationHandlerStop
    if state.get("step") != "point":
        return
    user_id = int(update.effective_user.id if update.effective_user else 0)
    recent = match_recent_location_button(user_id, text)
    try:
        candidates = [recent] if recent is not None else await asyncio.to_thread(search_location_candidates, text, 5)
    except (GeocodeError, ValueError) as exc:
        await message.reply_text(f"Не удалось найти точку: {exc}")
        raise ApplicationHandlerStop
    if not candidates:
        await message.reply_text("Точка не найдена. Уточните город или координаты.")
        raise ApplicationHandlerStop
    await _accept_candidates(message, context, state, candidates, user_id)
    raise ApplicationHandlerStop


async def meteogram_location(update: Update, context) -> None:
    state = context.user_data.get(SESSION_KEY)
    message = update.effective_message
    if not isinstance(state, dict) or state.get("step") != "point" or not message or not message.location:
        return
    point = GeoPoint(message.location.latitude, message.location.longitude, "Текущая геолокация", "telegram")
    user_id = int(update.effective_user.id if update.effective_user else 0)
    remember_location(user_id, point)
    state.update({"point": _pack_point(point), "step": "type"})
    await _remove_keyboard(message)
    await message.reply_text(f"📍 {point.label}\nВыберите тип прогноза.", reply_markup=_type_keyboard())
    raise ApplicationHandlerStop


async def _accept_candidates(message, context, state, candidates, user_id):
    candidates = [point for point in candidates if point is not None]
    if len(candidates) > 1:
        state["candidates"] = [_pack_point(point) for point in candidates[:5]]
        state["step"] = "place"
        await _remove_keyboard(message)
        await message.reply_text("Выберите точку:", reply_markup=_place_keyboard(candidates))
        return
    point = candidates[0]
    remember_location(user_id, point)
    state.update({"point": _pack_point(point), "step": "type"})
    await _remove_keyboard(message)
    await message.reply_text(f"📍 {point.label}\nВыберите тип прогноза.", reply_markup=_type_keyboard())


async def meteogram_callback(update: Update, context) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    if data == "home:meteogram":
        await query.answer()
        context.user_data[SESSION_KEY] = {"step": "point"}
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        if query.message:
            await query.message.reply_text(
                "📊 Метеограмма\n\nУкажите город, координаты или отправьте геолокацию.",
                reply_markup=_point_keyboard(
                    int(update.effective_user.id if update.effective_user else 0)
                ),
            )
        raise ApplicationHandlerStop
    if not data.startswith("meteo:"):
        return
    await query.answer()
    state = context.user_data.get(SESSION_KEY)
    if not isinstance(state, dict):
        await query.edit_message_text("Выбор устарел. Запустите /meteogram.")
        raise ApplicationHandlerStop
    if data == "meteo:cancel":
        context.user_data.pop(SESSION_KEY, None)
        await query.edit_message_text("Выбор метеограммы сброшен.")
        if query.message:
            await _show_home(query.message)
        raise ApplicationHandlerStop
    if data.startswith("meteo:place:"):
        index = int(data.rsplit(":", 1)[1])
        candidates = state.get("candidates") or []
        if index >= len(candidates):
            await query.edit_message_text("Выбор устарел.")
            raise ApplicationHandlerStop
        point = _unpack_point(candidates[index])
        remember_location(
            int(update.effective_user.id if update.effective_user else 0),
            point,
        )
        state.update({"point": _pack_point(point), "step": "type"})
        state.pop("candidates", None)
        await query.edit_message_text(
            f"📍 {point.label}\nВыберите тип прогноза.",
            reply_markup=_type_keyboard(),
        )
    elif data.startswith("meteo:kind:"):
        ensemble = data.endswith("ensemble")
        state.update({"ensemble": ensemble, "step": "model"})
        await query.edit_message_text(
            "Выберите ансамблевую систему." if ensemble else "Выберите модель.",
            reply_markup=_model_keyboard(ensemble),
        )
    elif data.startswith("meteo:source:"):
        source_id = data.rsplit(":", 1)[1]
        state.update({"source_id": source_id, "step": "period"})
        await query.edit_message_text(
            f"{source_for_id(source_id).label}\nВыберите период.",
            reply_markup=_period_keyboard(source_id),
        )
    elif data.startswith("meteo:days:"):
        days = int(data.rsplit(":", 1)[1])
        state.update({"days": days, "step": "output"})
        await query.edit_message_text(
            _output_prompt(str(state["source_id"]), days),
            reply_markup=_output_keyboard(),
        )
    elif data.startswith("meteo:format:"):
        output_format = _normalise_output_format(data.rsplit(":", 1)[1])
        state.update({"output_format": output_format, "step": "confirm"})
        await query.edit_message_text(
            _summary(state),
            reply_markup=_confirm_keyboard(output_format),
        )
    elif data == "meteo:back:type":
        state["step"] = "type"
        await query.edit_message_text(
            "Выберите тип прогноза.",
            reply_markup=_type_keyboard(),
        )
    elif data == "meteo:back:model":
        state["step"] = "model"
        await query.edit_message_text(
            "Выберите ансамблевую систему."
            if bool(state.get("ensemble"))
            else "Выберите модель.",
            reply_markup=_model_keyboard(bool(state.get("ensemble"))),
        )
    elif data == "meteo:back:period":
        state["step"] = "period"
        await query.edit_message_text(
            f"{source_for_id(str(state['source_id'])).label}\nВыберите период.",
            reply_markup=_period_keyboard(str(state["source_id"])),
        )
    elif data == "meteo:back:format":
        state["step"] = "output"
        await query.edit_message_text(
            _output_prompt(str(state["source_id"]), int(state["days"])),
            reply_markup=_output_keyboard(),
        )
    elif data == "meteo:run":
        point = _unpack_point(state["point"])
        request = MeteogramRequest(
            f"{point.lat} {point.lon}",
            str(state["source_id"]),
            int(state["days"]),
        )
        output_format = _normalise_output_format(
            state.get("output_format", "png")
        )
        context.user_data.pop(SESSION_KEY, None)
        await query.edit_message_text(
            f"📊 Запускаю метеограмму · {source_for_id(request.source_id).label}\n"
            f"Результат: {_output_label(output_format)}"
        )
        if query.message:
            await _run_product(
                query.message,
                point,
                request,
                update.effective_user,
                output_format=output_format,
            )
    raise ApplicationHandlerStop


async def _show_home(message, prefix: str | None = None) -> None:
    import telegram_concise_ux
    await _remove_keyboard(message)
    text = telegram_concise_ux.home_text()
    await message.reply_text(f"{prefix}\n\n{text}" if prefix else text, reply_markup=telegram_concise_ux.home_keyboard())


def register_meteogram_handlers(application) -> None:
    application.add_handler(CommandHandler("meteogram", meteogram_command), group=-4)
    application.add_handler(CallbackQueryHandler(meteogram_callback, pattern=r"^(home:meteogram|meteo:)"), group=-4)
    application.add_handler(MessageHandler(filters.LOCATION, meteogram_location), group=-4)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, meteogram_text), group=-4)
