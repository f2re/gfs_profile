from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import NamedTuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from formatters import format_profile_summary, write_profile_csv
from geocode import GeoPoint, GeocodeError, resolve_location
from gfs_core import GfsProfileError, GfsRun, build_profile, latest_available_run, validate_lead
from profile_plot import write_profile_png

DEFAULT_LEAD = int(os.getenv("DEFAULT_LEAD", "24"))
MAX_CONCURRENT_GFS = int(os.getenv("MAX_CONCURRENT_GFS", "2"))
GFS_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_GFS)
LEAD_BUTTONS = (0, 3, 6, 12, 24, 48)
RUN_RE = re.compile(r"\brun=(?P<date>\d{8})[/-]?(?P<cycle>00|06|12|18)\b", re.IGNORECASE)
LEAD_RE = re.compile(r"(?:^|\s)(?:lead=|\+|f)?(?P<lead>\d{1,3})(?:\s*(?:h|ч|час|часа|часов))?\s*$", re.IGNORECASE)


class ParsedRequest(NamedTuple):
    location_query: str
    lead_hour: int
    run: GfsRun | None


def parse_request(raw_text: str) -> ParsedRequest:
    text = raw_text.strip()
    run: GfsRun | None = None

    run_match = RUN_RE.search(text)
    if run_match:
        run = GfsRun(date=run_match.group("date"), cycle=run_match.group("cycle"))
        text = (text[: run_match.start()] + text[run_match.end() :]).strip()

    lead_hour = DEFAULT_LEAD
    lead_match = LEAD_RE.search(text)
    if lead_match:
        lead_hour = int(lead_match.group("lead"))
        text = text[: lead_match.start()].strip()

    validate_lead(lead_hour)
    if not text:
        raise ValueError("Не указана точка. Пример: /profile Москва +24 или /profile 55.75 37.62 +12")
    return ParsedRequest(location_query=text, lead_hour=lead_hour, run=run)


def lead_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(f"+{lead} ч", callback_data=f"lead:{lead}") for lead in LEAD_BUTTONS]
    rows = [buttons[:3], buttons[3:]]
    return InlineKeyboardMarkup(rows)


def location_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Отправить геолокацию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "Бот строит вертикальный профиль GFS 0.25° по координатам, городу или геолокации Telegram.\n\n"
        "Примеры:\n"
        "/profile Москва +24\n"
        "/profile 55.75 37.62 +12\n"
        "/profile Санкт-Петербург run=20260630/06 +48\n\n"
        "Можно также отправить геолокацию кнопкой ниже.",
        reply_markup=location_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def cycle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    try:
        run = await asyncio.to_thread(latest_available_run)
    except GfsProfileError as exc:
        await message.reply_text(str(exc))
        return
    await message.reply_text(f"Последний доступный GFS: {run.date} {run.cycle}Z")


async def run_profile(message, point: GeoPoint, lead_hour: int, run: GfsRun | None = None) -> None:
    status = await message.reply_text("Запрос принят. Ищу доступный цикл GFS и загружаю профиль…")
    csv_path: Path | None = None
    png_path: Path | None = None
    try:
        selected_run = run or await asyncio.to_thread(latest_available_run)
        async with GFS_SEMAPHORE:
            result = await asyncio.to_thread(build_profile, selected_run, lead_hour, point.lat, point.lon)
        summary = format_profile_summary(result)
        csv_path = write_profile_csv(result)
        png_path = write_profile_png(result)
    except (GfsProfileError, GeocodeError, ValueError) as exc:
        await status.edit_text(f"Ошибка: {exc}")
        return
    except Exception as exc:
        await status.edit_text(f"Непредвиденная ошибка: {exc}")
        return

    await status.edit_text(summary)
    try:
        if png_path:
            with png_path.open("rb") as file_obj:
                await message.reply_photo(photo=InputFile(file_obj, filename=png_path.name), caption="График профиля GFS")
        if csv_path:
            with csv_path.open("rb") as file_obj:
                await message.reply_document(document=InputFile(file_obj, filename=csv_path.name))
    finally:
        if png_path:
            png_path.unlink(missing_ok=True)
        if csv_path:
            csv_path.unlink(missing_ok=True)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await message.reply_text("Укажите точку: /profile Москва +24 или /profile 55.75 37.62 +12")
        return

    try:
        parsed = parse_request(raw)
        point = await asyncio.to_thread(resolve_location, parsed.location_query)
    except (GeocodeError, ValueError, GfsProfileError) as exc:
        await message.reply_text(f"Ошибка: {exc}")
        return

    await run_profile(message, point, parsed.lead_hour, parsed.run)


async def location_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.location:
        return
    point = GeoPoint(
        lat=message.location.latitude,
        lon=message.location.longitude,
        label="геолокация Telegram",
        source="telegram",
    )
    context.user_data["last_point"] = {"lat": point.lat, "lon": point.lon, "label": point.label}
    await message.reply_text("Геолокация получена. Выберите срок прогноза:", reply_markup=lead_keyboard())


async def lead_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if not data.startswith("lead:"):
        return

    last_point = context.user_data.get("last_point")
    if not last_point:
        await query.edit_message_text("Сначала отправьте геолокацию или используйте /profile <город|lat lon> +24")
        return

    lead_hour = int(data.split(":", 1)[1])
    point = GeoPoint(lat=float(last_point["lat"]), lon=float(last_point["lon"]), label=str(last_point["label"]), source="telegram")
    if query.message:
        await query.edit_message_text(f"Срок +{lead_hour} ч выбран. Строю профиль…")
        await run_profile(query.message, point, lead_hour)


def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Нужно задать TELEGRAM_BOT_TOKEN или BOT_TOKEN")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cycle", cycle_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(MessageHandler(filters.LOCATION, location_message))
    application.add_handler(CallbackQueryHandler(lead_callback, pattern=r"^lead:\d+$"))
    return application


def main() -> None:
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
