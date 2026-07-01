from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import NamedTuple

from telegram import InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from formatters import format_profile_summary, write_profile_csv
from geocode import GeoPoint, GeocodeError
from geocode_choices import search_location_candidates
from gfs_core import CACHE_DIR, GfsProfileError, GfsRun, latest_available_run, latest_available_run_for_lead, validate_lead
from profile_plot import write_profile_png
from telegram_progress import build_profile_with_progress
from telegram_ui import lead_keyboard, lead_page_text, location_keyboard, place_keyboard

DEFAULT_LEAD = int(os.getenv("DEFAULT_LEAD", "24"))
MAX_CONCURRENT_GFS = int(os.getenv("MAX_CONCURRENT_GFS", "2"))
MAX_CONCURRENT_GEOCODE = int(os.getenv("MAX_CONCURRENT_GEOCODE", "2"))
GFS_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_GFS)
GEOCODE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_GEOCODE)
RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
LEAD_RE = re.compile(r"(?:^|\s)(?:lead=|\+|f)?(?P<lead>\d{1,3})(?:\s*(?:h|ч|час|часа|часов))?\s*$", re.IGNORECASE)


class ParsedRequest(NamedTuple):
    location_query: str
    lead_hour: int
    run: GfsRun | None
    lead_from_user: bool


def parse_request(raw_text: str) -> ParsedRequest:
    text = raw_text.strip()
    run: GfsRun | None = None
    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = (text[: run_match.start()] + text[run_match.end() :]).strip()

    lead_hour = DEFAULT_LEAD
    lead_from_user = False
    lead_match = LEAD_RE.search(text)
    if lead_match:
        lead_hour = int(lead_match.group("lead"))
        lead_from_user = True
        text = text[: lead_match.start()].strip()

    validate_lead(lead_hour)
    if not text:
        raise ValueError("Не указана точка. Напишите город, координаты или отправьте геолокацию.")
    return ParsedRequest(text, lead_hour, run, lead_from_user)


def _pack_point(point: GeoPoint) -> dict[str, object]:
    return {"lat": point.lat, "lon": point.lon, "label": point.label, "source": point.source}


def _unpack_point(payload: dict[str, object]) -> GeoPoint:
    return GeoPoint(float(payload["lat"]), float(payload["lon"]), str(payload["label"]), str(payload.get("source", "manual")))


def _pack_run(run: GfsRun | None) -> dict[str, str] | None:
    return {"date": run.date, "cycle": run.cycle} if run else None


def _unpack_run(payload: dict[str, str] | None) -> GfsRun | None:
    return GfsRun(payload["date"], payload["cycle"]) if payload else None


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("pending_profile", None)
    context.user_data.pop("pending_candidates", None)


def _set_pending_point(context: ContextTypes.DEFAULT_TYPE, point: GeoPoint, run: GfsRun | None = None) -> None:
    context.user_data["pending_profile"] = {"point": _pack_point(point), "run": _pack_run(run)}


def _point_brief(point: GeoPoint) -> str:
    return f"{point.label}\n{point.lat:.4f}, {point.lon:.4f}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "🌦️ Профиль GFS 0.25\n"
        "Модельный вертикальный профиль ближайшего узла GFS: температура, точка росы, влажность, ветер и уровень 0 °C.\n\n"
        "Минимальный путь:\n"
        "1) отправьте геолокацию;\n"
        "2) выберите срок кнопкой;\n"
        "3) видите ход проверки, загрузки GRIB2 и построения;\n"
        "4) получите сводку, PNG и CSV.\n\n"
        "Можно просто написать: Москва, 55.75 37.62 или /profile Москва +24. Полный диапазон сроков GFS — до +384 ч.",
        reply_markup=location_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "Как пользоваться:\n"
        "• 📍 отправьте геолокацию и выберите срок;\n"
        "• или напишите город: Москва;\n"
        "• или координаты: 55.75 37.62;\n"
        "• экспертно: /profile Москва run=20260630/06 +24.\n\n"
        "Кнопки показывают частые сроки, через пагинацию доступны все сроки GFS до +384 ч.\n"
        "Во время расчёта бот показывает этапы: проверка fXXX.idx, загрузка GRIB2, cfgrib/eccodes, построение PNG/CSV.\n"
        "Это модель GFS, не радиозонд.",
        reply_markup=location_keyboard(),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_pending(context)
    message = update.effective_message
    if message:
        await message.reply_text("Текущий выбор сброшен. Напишите город или отправьте геолокацию.", reply_markup=location_keyboard())


async def cycle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    try:
        run = await asyncio.to_thread(latest_available_run)
        await message.reply_text(f"Последний опубликованный анализ GFS: {run.date} {run.cycle}Z")
    except GfsProfileError as exc:
        await message.reply_text(str(exc))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    lines = ["Состояние бота:"]
    for lead in dict.fromkeys((0, DEFAULT_LEAD, 24, 48, 120, 240, 384)):
        try:
            run = await asyncio.to_thread(latest_available_run_for_lead, lead)
            lines.append(f"• +{lead} ч: GFS {run.date} {run.cycle}Z опубликован")
        except GfsProfileError as exc:
            lines.append(f"• +{lead} ч: недоступно — {exc}")
    lines.append(f"• Одновременных GFS-запросов: {MAX_CONCURRENT_GFS}")
    lines.append(f"• Одновременных геокодинг-запросов: {MAX_CONCURRENT_GEOCODE}")
    lines.append(f"• Кэш GRIB2: {CACHE_DIR}")
    await message.reply_text("\n".join(lines))


async def run_profile(message, point: GeoPoint, lead_hour: int, run: GfsRun | None = None) -> None:
    status = await message.reply_text("0/5 Ищу опубликованный цикл GFS для выбранного срока…")
    csv_path: Path | None = None
    png_path: Path | None = None
    try:
        async with GFS_SEMAPHORE:
            selected_run = run or await asyncio.to_thread(latest_available_run_for_lead, lead_hour)
            result = await build_profile_with_progress(status, selected_run, lead_hour, point)
            await status.edit_text("5/5 Профиль рассчитан. Формирую компактную сводку, PNG и CSV…")
            summary = format_profile_summary(result)
            csv_path = write_profile_csv(result)
            png_path = write_profile_png(result)
        await status.edit_text(summary, parse_mode=ParseMode.HTML)
        if png_path:
            with png_path.open("rb") as file_obj:
                await message.reply_photo(photo=InputFile(file_obj, filename=png_path.name), caption="График профиля GFS")
        if csv_path:
            with csv_path.open("rb") as file_obj:
                await message.reply_document(document=InputFile(file_obj, filename=csv_path.name), caption="CSV-профиль по изобарическим уровням")
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)
        if csv_path:
            csv_path.unlink(missing_ok=True)


async def resolve_profile_request(message, context: ContextTypes.DEFAULT_TYPE, raw: str) -> None:
    try:
        parsed = parse_request(raw)
        async with GEOCODE_SEMAPHORE:
            candidates = await asyncio.to_thread(search_location_candidates, parsed.location_query, 3)
    except (GeocodeError, ValueError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return

    if not candidates:
        await message.reply_text("Точка не найдена. Пришлите координаты или геолокацию Telegram.")
        return

    if len(candidates) > 1:
        context.user_data["pending_candidates"] = {
            "candidates": [_pack_point(point) for point in candidates[:3]],
            "lead_hour": parsed.lead_hour,
            "run": _pack_run(parsed.run),
            "lead_from_user": parsed.lead_from_user,
        }
        await message.reply_text("Найдено несколько точек. Выберите нужную:", reply_markup=place_keyboard([point.label for point in candidates]))
        return

    point = candidates[0]
    if parsed.lead_from_user:
        await run_profile(message, point, parsed.lead_hour, parsed.run)
        return

    _set_pending_point(context, point, parsed.run)
    await message.reply_text(f"Точка выбрана:\n{_point_brief(point)}\n\n{lead_page_text(0)}", reply_markup=lead_keyboard(0))


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await message.reply_text("Напишите точку: /profile Москва +24, /profile 55.75 37.62 или отправьте геолокацию.")
        return
    await resolve_profile_request(message, context, raw)


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return
    text = message.text.strip()
    if text in {"❓ Помощь", "Помощь", "help"}:
        await help_command(update, context)
        return
    await resolve_profile_request(message, context, text)


async def location_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.location:
        return
    point = GeoPoint(message.location.latitude, message.location.longitude, "геолокация Telegram", "telegram")
    _set_pending_point(context, point)
    await message.reply_text(f"Геолокация получена:\n{_point_brief(point)}\n\n{lead_page_text(0)}", reply_markup=lead_keyboard(0))


async def lead_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if not data.startswith("lead:"):
        return
    pending = context.user_data.get("pending_profile")
    if not pending:
        await query.edit_message_text("Сначала отправьте геолокацию или напишите город/координаты.")
        return
    point = _unpack_point(pending["point"])
    run = _unpack_run(pending.get("run"))
    lead_hour = int(data.split(":", 1)[1])
    _clear_pending(context)
    if query.message:
        await query.edit_message_text(f"Срок +{lead_hour} ч выбран. Строю профиль…")
        await run_profile(query.message, point, lead_hour, run)


async def lead_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    pending = context.user_data.get("pending_profile")
    if not pending:
        await query.edit_message_text("Сначала выберите точку: отправьте город, координаты или геолокацию.")
        return
    page = int((query.data or "leadpage:0").split(":", 1)[1])
    point = _unpack_point(pending["point"])
    await query.edit_message_text(f"Точка:\n{_point_brief(point)}\n\n{lead_page_text(page)}", reply_markup=lead_keyboard(page))


async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer("Текущая страница")


async def place_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if data == "cancel":
        _clear_pending(context)
        await query.edit_message_text("Выбор отменён. Напишите город или отправьте геолокацию.")
        return
    if not data.startswith("place:"):
        return
    pending = context.user_data.get("pending_candidates")
    if not pending:
        await query.edit_message_text("Список вариантов устарел. Повторите запрос.")
        return
    index = int(data.split(":", 1)[1])
    candidates = pending.get("candidates", [])
    if index < 0 or index >= len(candidates):
        await query.edit_message_text("Некорректный вариант. Повторите запрос.")
        return
    point = _unpack_point(candidates[index])
    run = _unpack_run(pending.get("run"))
    lead_hour = int(pending.get("lead_hour", DEFAULT_LEAD))
    lead_from_user = bool(pending.get("lead_from_user", False))
    context.user_data.pop("pending_candidates", None)
    if not query.message:
        return
    if lead_from_user:
        await query.edit_message_text(f"Выбрано:\n{_point_brief(point)}\nСтрою профиль +{lead_hour} ч…")
        await run_profile(query.message, point, lead_hour, run)
        return
    _set_pending_point(context, point, run)
    await query.edit_message_text(f"Выбрано:\n{_point_brief(point)}\n\n{lead_page_text(0)}", reply_markup=lead_keyboard(0))


def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Нужно задать TELEGRAM_BOT_TOKEN или BOT_TOKEN")
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("cycle", cycle_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(MessageHandler(filters.LOCATION, location_message))
    application.add_handler(CallbackQueryHandler(lead_callback, pattern=r"^lead:\d+$"))
    application.add_handler(CallbackQueryHandler(lead_page_callback, pattern=r"^leadpage:\d+$"))
    application.add_handler(CallbackQueryHandler(noop_callback, pattern=r"^noop$"))
    application.add_handler(CallbackQueryHandler(place_callback, pattern=r"^(place:\d+|cancel)$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    return application


def main() -> None:
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
