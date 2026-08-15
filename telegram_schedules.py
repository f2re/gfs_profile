from __future__ import annotations

"""Persistent per-user scheduler for Telegram weather products.

The scheduler deliberately stays inside the existing single-process long-polling
bot. Schedules are persisted atomically under .cache_gfs, restored on restart,
and executed by one background asyncio task started by Application.post_init.
"""

import asyncio
import json
import os
import re
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputMediaPhoto, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from geocode import GeoPoint
from telegram_product_wizard import (
    PRODUCT_WIZARD_KEY,
    start_aero_wizard_state,
    start_cloudgram_wizard_state,
    start_map_wizard_state,
    start_windgram_wizard_state,
)

SCHEDULE_WIZARD_KEY = "schedule_wizard"
SCHEDULE_PROFILE_SETUP_KEY = "schedule_profile_setup"
MAX_SCHEDULES_PER_USER = 2
DEFAULT_SCHEDULE_FILE = ".cache_gfs/telegram_schedules.json"
DEFAULT_POLL_SECONDS = 30
DEFAULT_MAX_LATE_MINUTES = 180
DEFAULT_TIMEZONE_TIMEOUT = 10
MAX_CONCURRENT_SCHEDULED = max(1, int(os.getenv("MAX_CONCURRENT_SCHEDULED", "1")))
SCHEDULE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_SCHEDULED)

TIME_RE = re.compile(r"^(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)$")


class ScheduleError(RuntimeError):
    pass


class ScheduleLimitError(ScheduleError):
    pass


@dataclass(slots=True)
class ProductSchedule:
    schedule_id: str
    user_id: int
    chat_id: int
    username: str | None
    product: str
    point: dict[str, object]
    params: dict[str, object]
    timezone: str
    local_time: str
    every_days: int
    next_run_utc: str
    created_at_utc: str
    last_started_utc: str | None = None
    last_finished_utc: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ProductSchedule":
        return cls(
            schedule_id=str(value["schedule_id"]),
            user_id=int(value["user_id"]),
            chat_id=int(value["chat_id"]),
            username=str(value["username"]) if value.get("username") else None,
            product=str(value["product"]),
            point=dict(value["point"]),
            params=dict(value.get("params") or {}),
            timezone=str(value["timezone"]),
            local_time=str(value["local_time"]),
            every_days=int(value["every_days"]),
            next_run_utc=str(value["next_run_utc"]),
            created_at_utc=str(value["created_at_utc"]),
            last_started_utc=str(value["last_started_utc"]) if value.get("last_started_utc") else None,
            last_finished_utc=str(value["last_finished_utc"]) if value.get("last_finished_utc") else None,
            last_status=str(value["last_status"]) if value.get("last_status") else None,
            last_error=str(value["last_error"]) if value.get("last_error") else None,
            consecutive_failures=int(value.get("consecutive_failures") or 0),
        )

    @property
    def next_run_datetime_utc(self) -> datetime:
        return _parse_utc(self.next_run_utc)


class ScheduleStore:
    """Small atomic JSON store. No database or second service is required."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load_unlocked(self) -> list[ProductSchedule]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or int(payload.get("version", 0)) != 1:
                raise ScheduleError("Неизвестная версия файла расписаний")
            items = payload.get("schedules", [])
            if not isinstance(items, list):
                raise ScheduleError("Повреждён список расписаний")
            return [ProductSchedule.from_dict(dict(item)) for item in items]
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ScheduleError(f"Не удалось прочитать расписания: {exc}") from exc

    def _save_unlocked(self, schedules: list[ProductSchedule]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at_utc": _iso_utc(_utc_now()),
            "schedules": [asdict(item) for item in schedules],
        }
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def list_for_user(self, user_id: int) -> list[ProductSchedule]:
        with self._lock:
            result = [item for item in self._load_unlocked() if item.user_id == int(user_id)]
        return sorted(result, key=lambda item: item.next_run_datetime_utc)

    def get(self, schedule_id: str) -> ProductSchedule | None:
        with self._lock:
            for item in self._load_unlocked():
                if item.schedule_id == schedule_id:
                    return item
        return None

    def add(
        self,
        *,
        user_id: int,
        chat_id: int,
        username: str | None,
        product: str,
        point: dict[str, object],
        params: dict[str, object],
        timezone_name: str,
        local_time: str,
        every_days: int,
        now_utc: datetime | None = None,
    ) -> ProductSchedule:
        user_id = int(user_id)
        every_days = _validate_interval(every_days)
        local_time = _normalise_time(local_time)
        timezone_name = _validate_timezone(timezone_name)
        now_utc = _as_utc(now_utc or _utc_now())
        next_run = next_run_utc(timezone_name, local_time, every_days, now_utc=now_utc)
        with self._lock:
            schedules = self._load_unlocked()
            owned = [item for item in schedules if item.user_id == user_id]
            if len(owned) >= MAX_SCHEDULES_PER_USER:
                raise ScheduleLimitError(
                    f"Можно создать не более {MAX_SCHEDULES_PER_USER} расписаний. Удалите одно из существующих."
                )
            signature = _schedule_signature(product, point, params, timezone_name, local_time, every_days)
            if any(
                _schedule_signature(
                    item.product,
                    item.point,
                    item.params,
                    item.timezone,
                    item.local_time,
                    item.every_days,
                )
                == signature
                for item in owned
            ):
                raise ScheduleError("Такое расписание уже существует")
            used_ids = {item.schedule_id for item in schedules}
            schedule_id = _new_schedule_id(used_ids)
            item = ProductSchedule(
                schedule_id=schedule_id,
                user_id=user_id,
                chat_id=int(chat_id),
                username=username or None,
                product=str(product),
                point=dict(point),
                params=dict(params),
                timezone=timezone_name,
                local_time=local_time,
                every_days=every_days,
                next_run_utc=_iso_utc(next_run),
                created_at_utc=_iso_utc(now_utc),
            )
            schedules.append(item)
            self._save_unlocked(schedules)
            return item

    def delete(self, schedule_id: str, user_id: int) -> bool:
        with self._lock:
            schedules = self._load_unlocked()
            kept = [
                item
                for item in schedules
                if not (item.schedule_id == schedule_id and item.user_id == int(user_id))
            ]
            if len(kept) == len(schedules):
                return False
            self._save_unlocked(kept)
            return True

    def mark_result(
        self,
        schedule_id: str,
        *,
        success: bool,
        error: str | None = None,
        finished_at_utc: datetime | None = None,
    ) -> None:
        finished = _as_utc(finished_at_utc or _utc_now())
        with self._lock:
            schedules = self._load_unlocked()
            changed = False
            for item in schedules:
                if item.schedule_id != schedule_id:
                    continue
                item.last_finished_utc = _iso_utc(finished)
                item.last_status = "ok" if success else "error"
                item.last_error = None if success else (str(error or "неизвестная ошибка")[:500])
                item.consecutive_failures = 0 if success else item.consecutive_failures + 1
                changed = True
                break
            if changed:
                self._save_unlocked(schedules)

    def claim_due(
        self,
        now_utc: datetime | None = None,
        *,
        max_late_minutes: int = DEFAULT_MAX_LATE_MINUTES,
    ) -> tuple[list[ProductSchedule], list[ProductSchedule]]:
        """Claim due schedules by advancing next_run before execution.

        Returns (due_for_execution, skipped_as_too_stale). Pre-advancing prevents
        duplicate delivery after a process crash between generation and send.
        """

        now = _as_utc(now_utc or _utc_now())
        max_late = timedelta(minutes=max(0, int(max_late_minutes)))
        due: list[ProductSchedule] = []
        skipped: list[ProductSchedule] = []
        with self._lock:
            schedules = self._load_unlocked()
            changed = False
            for item in schedules:
                scheduled = item.next_run_datetime_utc
                if scheduled > now:
                    continue
                lateness = now - scheduled
                if lateness > max_late:
                    skipped.append(ProductSchedule.from_dict(asdict(item)))
                    item.next_run_utc = _iso_utc(
                        _advance_to_future(item, now)
                    )
                    item.last_status = "skipped"
                    item.last_error = "пропущено после длительного простоя"
                    changed = True
                    continue
                claimed = ProductSchedule.from_dict(asdict(item))
                item.last_started_utc = _iso_utc(now)
                item.next_run_utc = _iso_utc(
                    next_run_utc(
                        item.timezone,
                        item.local_time,
                        item.every_days,
                        previous_scheduled_utc=scheduled,
                    )
                )
                due.append(claimed)
                changed = True
            if changed:
                self._save_unlocked(schedules)
        return due, skipped


_STORE: ScheduleStore | None = None


def schedule_store() -> ScheduleStore:
    global _STORE
    path = Path(os.getenv("TELEGRAM_SCHEDULE_FILE", DEFAULT_SCHEDULE_FILE))
    if _STORE is None or _STORE.path != path:
        _STORE = ScheduleStore(path)
    return _STORE


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return _as_utc(parsed)


def _validate_interval(value: int) -> int:
    value = int(value)
    if not 1 <= value <= 30:
        raise ScheduleError("Интервал должен быть от 1 до 30 дней")
    return value


def _normalise_time(value: str) -> str:
    match = TIME_RE.match(str(value).strip())
    if not match:
        raise ScheduleError("Время задаётся как ЧЧ:ММ, например 06:00")
    return f"{int(match.group('hour')):02d}:{int(match.group('minute')):02d}"


def _validate_timezone(value: str) -> str:
    name = str(value).strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(
            "Неизвестный часовой пояс. Используйте IANA-имя, например Europe/Moscow."
        ) from exc
    return name


def _valid_local_datetime(day: date, local_time: str, tz: ZoneInfo) -> datetime:
    hour, minute = [int(part) for part in local_time.split(":", 1)]
    naive = datetime.combine(day, dt_time(hour=hour, minute=minute))
    # Prefer the first occurrence of an ambiguous time. For a non-existent
    # spring-forward wall-clock time, move to the first valid local minute.
    for offset_minute in range(0, 181):
        candidate_naive = naive + timedelta(minutes=offset_minute)
        for fold in (0, 1):
            candidate = candidate_naive.replace(tzinfo=tz, fold=fold)
            round_trip = candidate.astimezone(timezone.utc).astimezone(tz)
            if (
                round_trip.replace(tzinfo=None) == candidate_naive
                and round_trip.fold == fold
            ):
                return candidate
    raise ScheduleError("Не удалось определить локальное время после перехода часов")


def next_run_utc(
    timezone_name: str,
    local_time: str,
    every_days: int,
    *,
    now_utc: datetime | None = None,
    previous_scheduled_utc: datetime | None = None,
) -> datetime:
    timezone_name = _validate_timezone(timezone_name)
    local_time = _normalise_time(local_time)
    every_days = _validate_interval(every_days)
    tz = ZoneInfo(timezone_name)
    if previous_scheduled_utc is not None:
        previous_local = _as_utc(previous_scheduled_utc).astimezone(tz)
        target_day = previous_local.date() + timedelta(days=every_days)
        return _valid_local_datetime(target_day, local_time, tz).astimezone(timezone.utc)

    now = _as_utc(now_utc or _utc_now())
    local_now = now.astimezone(tz)
    candidate = _valid_local_datetime(local_now.date(), local_time, tz)
    if candidate <= local_now:
        candidate = _valid_local_datetime(local_now.date() + timedelta(days=1), local_time, tz)
    return candidate.astimezone(timezone.utc)


def _advance_to_future(item: ProductSchedule, now_utc: datetime) -> datetime:
    value = item.next_run_datetime_utc
    for _ in range(370):
        if value > now_utc:
            return value
        value = next_run_utc(
            item.timezone,
            item.local_time,
            item.every_days,
            previous_scheduled_utc=value,
        )
    raise ScheduleError("Не удалось перенести устаревшее расписание в будущее")


def _new_schedule_id(used: set[str]) -> str:
    while True:
        value = secrets.token_hex(4)
        if value not in used:
            return value


def _schedule_signature(
    product: str,
    point: dict[str, object],
    params: dict[str, object],
    timezone_name: str,
    local_time: str,
    every_days: int,
) -> str:
    value = {
        "product": str(product),
        "point": point,
        "params": params,
        "timezone": timezone_name,
        "local_time": local_time,
        "every_days": int(every_days),
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def resolve_point_timezone(point: GeoPoint) -> str:
    timeout = max(2, int(os.getenv("TELEGRAM_SCHEDULE_TIMEZONE_TIMEOUT", str(DEFAULT_TIMEZONE_TIMEOUT))))
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": point.lat,
            "longitude": point.lon,
            "timezone": "auto",
            "forecast_days": 1,
            "current": "temperature_2m",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    timezone_name = str(payload.get("timezone") or "").strip()
    if not timezone_name:
        raise ScheduleError("Сервис не вернул часовой пояс точки")
    return _validate_timezone(timezone_name)


def _pack_point(point: GeoPoint) -> dict[str, object]:
    return {
        "lat": float(point.lat),
        "lon": float(point.lon),
        "label": str(point.label),
        "source": str(point.source),
    }


def _unpack_point(value: dict[str, object]) -> GeoPoint:
    return GeoPoint(
        float(value["lat"]),
        float(value["lon"]),
        str(value["label"]),
        str(value.get("source", "schedule")),
    )


def schedule_spec_from_product_state(state: dict[str, object]) -> dict[str, object]:
    point = state.get("point")
    if not isinstance(point, dict):
        raise ScheduleError("Сначала выберите точку")
    product = str(state.get("product") or "")
    if product == "aero":
        params = {
            "lead": int(state.get("lead", 24)),
            "diagram_type": str(state.get("diagram_type", "skewt")),
        }
    elif product == "windgram":
        params = {
            "from": int(state.get("from", 0)),
            "to": int(state.get("to", 120)),
            "time_step": int(state.get("time_step", 6)),
            "top": int(state.get("top", 500)),
            "param": str(state.get("param", "wind")),
        }
    elif product == "cloudgram":
        params = {
            "from": int(state.get("from", 0)),
            "to": int(state.get("to", 72)),
            "time_step": int(state.get("time_step", 3)),
            "mode": str(state.get("mode", "pro")),
        }
    elif product == "map":
        params = {
            "mode": str(state.get("mode", "single")),
            "lead": int(state.get("lead", 24)),
            "from": int(state.get("from", 0)),
            "to": int(state.get("to", 24)),
            "time_step": int(state.get("time_step", 6)),
            "basemap": str(state.get("basemap", "places")),
            "radius": float(state.get("radius", 100)),
        }
    else:
        raise ScheduleError("Этот продукт пока нельзя добавить в расписание")
    return {"product": product, "point": dict(point), "params": params}


def schedule_spec_from_meteogram_state(state: dict[str, object]) -> dict[str, object]:
    point = state.get("point")
    if not isinstance(point, dict):
        raise ScheduleError("Сначала выберите точку")
    if not state.get("source_id") or not state.get("days"):
        raise ScheduleError("Выберите модель и период")
    output = str(state.get("output_format", "png")).lower()
    if output not in {"png", "docx", "pdf"}:
        raise ScheduleError("Формат метеограммы должен быть PNG, DOCX или PDF")
    return {
        "product": "meteogram",
        "point": dict(point),
        "params": {
            "source_id": str(state["source_id"]),
            "days": int(state["days"]),
            "output_format": output,
        },
    }


def schedule_spec_from_profile(point_payload: dict[str, object], lead: int) -> dict[str, object]:
    return {
        "product": "profile",
        "point": dict(point_payload),
        "params": {"lead": int(lead)},
    }


def _interval_label(days: int) -> str:
    days = int(days)
    if days == 1:
        return "каждый день"
    last = days % 10
    last_two = days % 100
    noun = "дня" if last in {2, 3, 4} and last_two not in {12, 13, 14} else "дней"
    return f"каждые {days} {noun}"


def _product_title(product: str) -> str:
    return {
        "profile": "📈 Профиль",
        "aero": "🧾 Аэродиаграмма",
        "windgram": "🟦 Срок × уровень",
        "cloudgram": "☁️ Облака и осадки",
        "map": "🗺️ Карта",
        "meteogram": "📊 Метеограмма",
    }.get(product, product)


def _params_summary(item_or_spec: ProductSchedule | dict[str, object]) -> str:
    if isinstance(item_or_spec, ProductSchedule):
        product = item_or_spec.product
        params = item_or_spec.params
    else:
        product = str(item_or_spec["product"])
        params = dict(item_or_spec.get("params") or {})
    if product == "profile":
        return f"срок +{int(params.get('lead', 24))} ч"
    if product == "aero":
        return f"срок +{int(params.get('lead', 24))} ч · Skew-T"
    if product == "windgram":
        return (
            f"{params.get('param', 'wind')} · +{int(params.get('from', 0))}…+{int(params.get('to', 120))} ч · "
            f"шаг {int(params.get('time_step', 6))} ч · до {int(params.get('top', 500))} гПа"
        )
    if product == "cloudgram":
        return (
            f"{params.get('mode', 'pro')} · +{int(params.get('from', 0))}…+{int(params.get('to', 72))} ч · "
            f"шаг {int(params.get('time_step', 3))} ч"
        )
    if product == "map":
        mode = str(params.get("mode", "single"))
        if mode == "single":
            return f"одна карта · +{int(params.get('lead', 24))} ч"
        mode_label = "анимация" if mode == "gif" else "серия PNG"
        return (
            f"{mode_label} · +{int(params.get('from', 0))}…+{int(params.get('to', 24))} ч · "
            f"шаг {int(params.get('time_step', 6))} ч"
        )
    if product == "meteogram":
        try:
            from meteogram_core import source_for_id

            source = source_for_id(str(params.get("source_id", "gfs")))
            source_label = source.label
        except Exception:
            source_label = str(params.get("source_id", "gfs"))
        output = str(params.get("output_format", "png")).upper()
        return f"{source_label} · {int(params.get('days', 5))} сут · {output}"
    return "параметры сохранены"


def _next_local_label(item: ProductSchedule) -> str:
    local = item.next_run_datetime_utc.astimezone(ZoneInfo(item.timezone))
    return f"{local:%d.%m %H:%M}"


def _manager_text(user_id: int, store: ScheduleStore | None = None) -> tuple[str, list[ProductSchedule]]:
    store = store or schedule_store()
    items = store.list_for_user(user_id)
    lines = [f"🕒 Расписания · {len(items)}/{MAX_SCHEDULES_PER_USER}"]
    if not items:
        lines.extend(
            [
                "",
                "Автоматически формируйте и получайте прогнозы в этом чате.",
                "Время задаётся по местному часовому поясу выбранного города.",
            ]
        )
        return "\n".join(lines), items
    for index, item in enumerate(items, 1):
        point = _unpack_point(item.point)
        lines.extend(
            [
                "",
                f"{index}. {_product_title(item.product)} · {point.label}",
                f"   {_params_summary(item)}",
                f"   {item.local_time} {item.timezone} · {_interval_label(item.every_days)}",
                f"   следующее: {_next_local_label(item)}",
            ]
        )
        if item.last_status == "error":
            lines.append("   ⚠️ предыдущая отправка завершилась ошибкой")
    return "\n".join(lines), items


def _manager_keyboard(items: list[ProductSchedule]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, item in enumerate(items, 1):
        point = _unpack_point(item.point)
        label = f"{index}. {_product_title(item.product).split(' ', 1)[0]} {point.label}"[:56]
        rows.append([InlineKeyboardButton(label, callback_data=f"sched:view:{item.schedule_id}")])
    if len(items) < MAX_SCHEDULES_PER_USER:
        rows.append([InlineKeyboardButton("➕ Новое расписание", callback_data="sched:new")])
    rows.append(
        [
            InlineKeyboardButton("🔄 Обновить", callback_data="sched:list"),
            InlineKeyboardButton("🏠 Главное меню", callback_data="sched:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _product_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📈 Профиль", callback_data="sched:product:profile"),
                InlineKeyboardButton("🧾 Аэродиаграмма", callback_data="sched:product:aero"),
            ],
            [
                InlineKeyboardButton("🟦 Срок × уровень", callback_data="sched:product:windgram"),
                InlineKeyboardButton("☁️ Облака", callback_data="sched:product:cloudgram"),
            ],
            [
                InlineKeyboardButton("🗺️ Карта / анимация", callback_data="sched:product:map"),
                InlineKeyboardButton("📊 Метеограмма / отчёт", callback_data="sched:product:meteogram"),
            ],
            [InlineKeyboardButton("← Расписания", callback_data="sched:list")],
        ]
    )


def _frequency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Каждый день", callback_data="sched:freq:1"),
                InlineKeyboardButton("Каждые 2 дня", callback_data="sched:freq:2"),
            ],
            [
                InlineKeyboardButton("Каждые 3 дня", callback_data="sched:freq:3"),
                InlineKeyboardButton("Раз в неделю", callback_data="sched:freq:7"),
            ],
            [InlineKeyboardButton("Другой интервал…", callback_data="sched:freq:custom")],
            [InlineKeyboardButton("Отмена", callback_data="sched:abort")],
        ]
    )


def _time_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("06:00", callback_data="sched:time:06:00"),
                InlineKeyboardButton("09:00", callback_data="sched:time:09:00"),
            ],
            [
                InlineKeyboardButton("13:00", callback_data="sched:time:13:00"),
                InlineKeyboardButton("18:00", callback_data="sched:time:18:00"),
            ],
            [InlineKeyboardButton("Другое время…", callback_data="sched:time:custom")],
            [InlineKeyboardButton("Отмена", callback_data="sched:abort")],
        ]
    )


def _timezone_fallback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Europe/Moscow", callback_data="sched:tz:Europe/Moscow"),
                InlineKeyboardButton("UTC", callback_data="sched:tz:UTC"),
            ],
            [InlineKeyboardButton("Ввести часовой пояс…", callback_data="sched:tz:manual")],
            [InlineKeyboardButton("Отмена", callback_data="sched:abort")],
        ]
    )


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Сохранить расписание", callback_data="sched:save")],
            [
                InlineKeyboardButton("Частота", callback_data="sched:edit:freq"),
                InlineKeyboardButton("Время", callback_data="sched:edit:time"),
            ],
            [InlineKeyboardButton("Отмена", callback_data="sched:abort")],
        ]
    )


def _delivery_control_keyboard(item: ProductSchedule) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚙️ Расписание", callback_data=f"sched:view:{item.schedule_id}"),
                InlineKeyboardButton("🗑 Отменить", callback_data=f"sched:delete:{item.schedule_id}"),
            ]
        ]
    )


def _detail_keyboard(item: ProductSchedule) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶ Прислать сейчас", callback_data=f"sched:run:{item.schedule_id}")],
            [InlineKeyboardButton("🗑 Отменить расписание", callback_data=f"sched:delete:{item.schedule_id}")],
            [InlineKeyboardButton("← Все расписания", callback_data="sched:list")],
        ]
    )


def _delete_confirmation_keyboard(item: ProductSchedule) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, удалить", callback_data=f"sched:delete-confirm:{item.schedule_id}")],
            [InlineKeyboardButton("Нет, оставить", callback_data=f"sched:view:{item.schedule_id}")],
        ]
    )


def _detail_text(item: ProductSchedule) -> str:
    point = _unpack_point(item.point)
    lines = [
        f"{_product_title(item.product)} · {point.label}",
        f"📍 {point.lat:.4f}, {point.lon:.4f}",
        f"Параметры: {_params_summary(item)}",
        f"🕒 {item.local_time} · {item.timezone}",
        f"Повтор: {_interval_label(item.every_days)}",
        f"Следующая отправка: {_next_local_label(item)}",
    ]
    if item.last_finished_utc:
        status = "успешно" if item.last_status == "ok" else item.last_status or "—"
        lines.append(f"Последняя попытка: {status}")
    if item.last_error:
        lines.append(f"⚠️ {item.last_error[:160]}")
    return "\n".join(lines)


def _confirmation_text(state: dict[str, object]) -> str:
    spec = dict(state["spec"])
    point = _unpack_point(dict(spec["point"]))
    timezone_name = str(state["timezone"])
    local_time = str(state["local_time"])
    every_days = int(state["every_days"])
    next_run = next_run_utc(timezone_name, local_time, every_days)
    next_local = next_run.astimezone(ZoneInfo(timezone_name))
    return (
        "🕒 Новое расписание\n\n"
        f"{_product_title(str(spec['product']))} · {point.label}\n"
        f"{_params_summary(spec)}\n\n"
        f"Когда: {local_time} · {timezone_name}\n"
        f"Повтор: {_interval_label(every_days)}\n"
        f"Первая отправка: {next_local:%d.%m.%Y %H:%M}\n\n"
        "При каждом запуске используется актуальный опубликованный цикл/прогноз, а не цикл, существовавший при создании расписания."
    )


class _SilentStatus:
    async def edit_text(self, *args, **kwargs):
        return self

    async def delete(self):
        return None


class ScheduledMessage:
    """Adapter that lets existing Telegram product runners send to a stored chat."""

    def __init__(self, bot, item: ProductSchedule):
        self.bot = bot
        self.chat_id = item.chat_id
        self.from_user = SimpleNamespace(id=item.user_id, username=item.username)

    async def reply_text(self, text: str, *args, **kwargs):
        value = str(text or "")
        # Scheduled deliveries should contain the product, not progress/repeat chatter.
        if value.startswith("⏳") or value.startswith("📋"):
            return _SilentStatus()
        return await self.bot.send_message(chat_id=self.chat_id, text=text, **kwargs)

    async def reply_photo(self, *args, **kwargs):
        return await self.bot.send_photo(chat_id=self.chat_id, *args, **kwargs)

    async def reply_document(self, *args, **kwargs):
        return await self.bot.send_document(chat_id=self.chat_id, *args, **kwargs)

    async def reply_animation(self, *args, **kwargs):
        return await self.bot.send_animation(chat_id=self.chat_id, *args, **kwargs)

    async def reply_video(self, *args, **kwargs):
        return await self.bot.send_video(chat_id=self.chat_id, *args, **kwargs)

    async def reply_media_group(self, *args, **kwargs):
        return await self.bot.send_media_group(chat_id=self.chat_id, *args, **kwargs)


async def execute_schedule(application, namespace: dict[str, Any], item: ProductSchedule) -> bool:
    point = _unpack_point(item.point)
    params = item.params
    message = ScheduledMessage(application.bot, item)
    user = SimpleNamespace(id=item.user_id, username=item.username)
    product = item.product

    async with SCHEDULE_SEMAPHORE:
        if product == "profile":
            lead = int(params.get("lead", namespace.get("DEFAULT_LEAD", 24)))
            return bool(
                await namespace["_tracked_run_profile"](
                    message,
                    point,
                    lead,
                    None,
                    request_text=f"[schedule:{item.schedule_id}] /profile {point.lat:.4f} {point.lon:.4f} +{lead}",
                    user=user,
                )
            )

        if product == "aero":
            from telegram_aero import ParsedAeroRequest, run_aero_product

            parsed = ParsedAeroRequest(
                location_query=f"{point.lat:.4f} {point.lon:.4f}",
                lead_hour=int(params.get("lead", 24)),
                run=None,
                diagram_type=str(params.get("diagram_type", "skewt")),
            )
            return bool(
                await namespace["_tracked_product"](
                    message,
                    "aero",
                    point.label,
                    f"[schedule:{item.schedule_id}] /aero {parsed.location_query} +{parsed.lead_hour}",
                    lambda: run_aero_product(message, point, parsed, namespace["GFS_SEMAPHORE"]),
                    lead_from=parsed.lead_hour,
                    lead_to=parsed.lead_hour,
                    user=user,
                )
            )

        if product == "windgram":
            from telegram_windgram import ParsedWindgramRequest, run_windgram_product

            parsed = ParsedWindgramRequest(
                location_query=f"{point.lat:.4f} {point.lon:.4f}",
                run=None,
                lead_from=int(params.get("from", 0)),
                lead_to=int(params.get("to", 120)),
                step=int(params.get("time_step", 6)),
                top_hpa=int(params.get("top", 500)),
                param=str(params.get("param", "wind")),
            )
            return bool(
                await namespace["_tracked_product"](
                    message,
                    "windgram",
                    point.label,
                    f"[schedule:{item.schedule_id}] windgram",
                    lambda: run_windgram_product(message, point, parsed, namespace["GFS_SEMAPHORE"]),
                    lead_from=parsed.lead_from,
                    lead_to=parsed.lead_to,
                    user=user,
                )
            )

        if product == "cloudgram":
            from telegram_cloudgram import ParsedCloudgramRequest, run_cloudgram_product

            parsed = ParsedCloudgramRequest(
                location_query=f"{point.lat:.4f} {point.lon:.4f}",
                run=None,
                lead_from=int(params.get("from", 0)),
                lead_to=int(params.get("to", 72)),
                step=int(params.get("time_step", 3)),
                mode=str(params.get("mode", "pro")),
            )
            return bool(
                await namespace["_tracked_product"](
                    message,
                    "cloudgram",
                    point.label,
                    f"[schedule:{item.schedule_id}] cloudgram",
                    lambda: run_cloudgram_product(message, point, parsed, namespace["GFS_SEMAPHORE"]),
                    lead_from=parsed.lead_from,
                    lead_to=parsed.lead_to,
                    user=user,
                )
            )

        if product == "map":
            from telegram_map import ParsedMapRequest, run_map_product

            mode = str(params.get("mode", "single"))
            lead = int(params.get("lead", 24))
            parsed = ParsedMapRequest(
                location_query=f"{point.lat:.4f} {point.lon:.4f}",
                run=None,
                lead_from=int(params.get("from", 0)) if mode in {"series", "gif"} else lead,
                lead_to=int(params.get("to", 24)) if mode in {"series", "gif"} else lead,
                step=int(params.get("time_step", 6)),
                animate=mode == "gif",
                radius_km=float(params.get("radius", 100)),
                mode=mode,
                basemap=str(params.get("basemap", "places")),
            )
            return bool(
                await namespace["_tracked_product"](
                    message,
                    "map",
                    point.label,
                    f"[schedule:{item.schedule_id}] map",
                    lambda: run_map_product(message, point, parsed, namespace["GFS_SEMAPHORE"]),
                    lead_from=parsed.lead_from,
                    lead_to=parsed.lead_to,
                    user=user,
                )
            )

        if product == "meteogram":
            import telegram_meteogram
            from meteogram_request import MeteogramRequest

            request = MeteogramRequest(
                f"{point.lat:.4f} {point.lon:.4f}",
                str(params.get("source_id", "gfs")),
                int(params.get("days", 5)),
            )
            return bool(
                await telegram_meteogram._run_product(
                    message,
                    point,
                    request,
                    user,
                    output_format=str(params.get("output_format", "png")),
                )
            )

    raise ScheduleError(f"Неизвестный продукт расписания: {product}")


async def _send_delivery_control(application, item: ProductSchedule, *, store: ScheduleStore) -> None:
    refreshed = store.get(item.schedule_id)
    if refreshed is None:
        return
    await application.bot.send_message(
        chat_id=item.chat_id,
        text=(
            f"🕒 По расписанию · {_interval_label(refreshed.every_days)} в {refreshed.local_time}\n"
            f"Следующая отправка: {_next_local_label(refreshed)} · {refreshed.timezone}"
        ),
        reply_markup=_delivery_control_keyboard(refreshed),
    )


async def _execute_and_record(application, namespace: dict[str, Any], item: ProductSchedule, store: ScheduleStore) -> bool:
    try:
        success = bool(await execute_schedule(application, namespace, item))
        store.mark_result(item.schedule_id, success=success, error=None if success else "продукт завершился без результата")
        if success:
            await _send_delivery_control(application, item, store=store)
        else:
            await application.bot.send_message(
                chat_id=item.chat_id,
                text=f"⚠️ Не удалось сформировать {_product_title(item.product).lower()} по расписанию.",
                reply_markup=_delivery_control_keyboard(store.get(item.schedule_id) or item),
            )
        return success
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        store.mark_result(item.schedule_id, success=False, error=str(exc))
        try:
            await application.bot.send_message(
                chat_id=item.chat_id,
                text=(
                    f"⚠️ Ошибка автоматической отправки: {str(exc)[:240]}\n"
                    "Расписание сохранено и будет запущено в следующий срок."
                ),
                reply_markup=_delivery_control_keyboard(store.get(item.schedule_id) or item),
            )
        except Exception:
            pass
        return False


async def _scheduler_loop(application, namespace: dict[str, Any]) -> None:
    store = schedule_store()
    poll_seconds = max(5, int(os.getenv("TELEGRAM_SCHEDULE_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))))
    max_late = max(0, int(os.getenv("TELEGRAM_SCHEDULE_MAX_LATE_MINUTES", str(DEFAULT_MAX_LATE_MINUTES))))
    while True:
        try:
            due, _skipped = store.claim_due(max_late_minutes=max_late)
            for item in due:
                await _execute_and_record(application, namespace, item, store)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Keep long polling alive even if the schedule file or one task fails.
            try:
                application.bot_data["schedule_last_error"] = str(exc)[:500]
            except Exception:
                pass
        await asyncio.sleep(poll_seconds)


def _private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and str(getattr(chat, "type", "")) == "private")


def _user_id(update: Update) -> int:
    return int(getattr(update.effective_user, "id", 0) or 0)


def _chat_id(update: Update) -> int:
    return int(getattr(update.effective_chat, "id", 0) or 0)


async def _show_manager(target, user_id: int) -> None:
    try:
        text, items = _manager_text(user_id)
    except ScheduleError as exc:
        text, items = f"⚠️ {exc}", []
    markup = _manager_keyboard(items)
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=markup)
    else:
        await target.reply_text(text, reply_markup=markup)


async def schedule_command(update: Update, context) -> None:
    message = update.effective_message
    if not message:
        return
    if not _private_chat(update):
        await message.reply_text("🕒 Расписания создаются в личном чате с ботом.")
        return
    context.user_data.pop(SCHEDULE_WIZARD_KEY, None)
    await _show_manager(message, _user_id(update))


async def _begin_timing(target, context, spec: dict[str, object]) -> None:
    context.user_data[SCHEDULE_WIZARD_KEY] = {
        "step": "frequency",
        "spec": spec,
    }
    point = _unpack_point(dict(spec["point"]))
    text = (
        "🕒 Настройка расписания\n\n"
        f"{_product_title(str(spec['product']))} · {point.label}\n"
        f"{_params_summary(spec)}\n\n"
        "Как часто отправлять?"
    )
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=_frequency_keyboard())
    else:
        await target.reply_text(text, reply_markup=_frequency_keyboard())


async def _show_time_step(target, context) -> None:
    state = context.user_data.get(SCHEDULE_WIZARD_KEY)
    if not isinstance(state, dict):
        return
    state["step"] = "time"
    text = f"Частота: {_interval_label(int(state['every_days']))}\n\nВо сколько отправлять по местному времени города?"
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=_time_keyboard())
    else:
        await target.reply_text(text, reply_markup=_time_keyboard())


async def _resolve_timezone_and_confirm(target, context) -> None:
    state = context.user_data.get(SCHEDULE_WIZARD_KEY)
    if not isinstance(state, dict):
        return
    spec = dict(state["spec"])
    point = _unpack_point(dict(spec["point"]))
    try:
        timezone_name = await asyncio.to_thread(resolve_point_timezone, point)
        state["timezone"] = timezone_name
        state["step"] = "confirm"
        text = _confirmation_text(state)
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(text, reply_markup=_confirmation_keyboard())
        else:
            await target.reply_text(text, reply_markup=_confirmation_keyboard())
    except Exception:
        state["step"] = "timezone"
        text = (
            f"Не удалось автоматически определить часовой пояс для {point.label}.\n\n"
            "Выберите вариант или введите IANA-имя, например Europe/London."
        )
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(text, reply_markup=_timezone_fallback_keyboard())
        else:
            await target.reply_text(text, reply_markup=_timezone_fallback_keyboard())


async def _start_product_setup(update: Update, context, namespace: dict[str, Any], product: str) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    user_id = _user_id(update)
    if len(schedule_store().list_for_user(user_id)) >= MAX_SCHEDULES_PER_USER:
        await _show_manager(query, user_id)
        return

    if product == "meteogram":
        import telegram_meteogram

        namespace["_clear_pending"](context)
        context.user_data[telegram_meteogram.SESSION_KEY] = {
            "step": "point",
            "_schedule_setup": True,
        }
        await query.edit_message_text("Настраиваем метеограмму для расписания.")
        await query.message.reply_text(
            "📊 Метеограмма / отчёт\n\nУкажите город, координаты или отправьте геолокацию.",
            reply_markup=telegram_meteogram._point_keyboard(user_id),
        )
        return

    if product == "profile":
        namespace["_clear_pending"](context)
        context.user_data[SCHEDULE_PROFILE_SETUP_KEY] = True
        await query.edit_message_text("Настраиваем профиль для расписания.")
        await query.message.reply_text(
            "📈 Вертикальный профиль\n\nУкажите город, координаты или отправьте геолокацию.",
            reply_markup=namespace["_location_keyboard_for_user"](user_id),
        )
        return

    builders: dict[str, Callable[[], dict[str, object]]] = {
        "aero": lambda: start_aero_wizard_state(int(namespace.get("DEFAULT_LEAD", 24))),
        "windgram": start_windgram_wizard_state,
        "cloudgram": start_cloudgram_wizard_state,
        "map": lambda: start_map_wizard_state(int(namespace.get("DEFAULT_LEAD", 24))),
    }
    builder = builders.get(product)
    if builder is None:
        raise ScheduleError("Этот продукт нельзя добавить в расписание")
    state = builder()
    state["_schedule_setup"] = True
    context.user_data["_ux_home_user_id"] = user_id
    await query.edit_message_text(f"Настраиваем {_product_title(product).lower()} для расписания.")
    await namespace["_start_product_wizard"](query.message, context, state)


async def schedule_callback(update: Update, context, namespace: dict[str, Any]) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    if not (data.startswith("sched:") or data == "home:schedule"):
        return
    await query.answer()
    if not _private_chat(update):
        await query.edit_message_text("🕒 Расписания создаются в личном чате с ботом.")
        raise ApplicationHandlerStop
    user_id = _user_id(update)

    if data == "home:schedule" or data == "sched:list":
        context.user_data.pop(SCHEDULE_WIZARD_KEY, None)
        await _show_manager(query, user_id)
        raise ApplicationHandlerStop
    if data == "sched:home":
        import telegram_concise_ux

        context.user_data.pop(SCHEDULE_WIZARD_KEY, None)
        await query.edit_message_text(telegram_concise_ux.home_text(), reply_markup=telegram_concise_ux.home_keyboard())
        raise ApplicationHandlerStop
    if data == "sched:new":
        if len(schedule_store().list_for_user(user_id)) >= MAX_SCHEDULES_PER_USER:
            await _show_manager(query, user_id)
        else:
            await query.edit_message_text(
                "🕒 Новое расписание\n\nЧто автоматически формировать?",
                reply_markup=_product_keyboard(),
            )
        raise ApplicationHandlerStop
    if data.startswith("sched:product:"):
        await _start_product_setup(update, context, namespace, data.rsplit(":", 1)[1])
        raise ApplicationHandlerStop
    if data == "sched:abort":
        context.user_data.pop(SCHEDULE_WIZARD_KEY, None)
        context.user_data.pop(SCHEDULE_PROFILE_SETUP_KEY, None)
        await _show_manager(query, user_id)
        raise ApplicationHandlerStop

    state = context.user_data.get(SCHEDULE_WIZARD_KEY)
    if data.startswith("sched:freq:"):
        if not isinstance(state, dict):
            await _show_manager(query, user_id)
            raise ApplicationHandlerStop
        value = data.rsplit(":", 1)[1]
        if value == "custom":
            state["step"] = "custom_interval"
            await query.edit_message_text(
                "Введите интервал в днях от 1 до 30.\nНапример: 3",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="sched:abort")]]),
            )
        else:
            state["every_days"] = _validate_interval(int(value))
            await _show_time_step(query, context)
        raise ApplicationHandlerStop
    if data.startswith("sched:time:"):
        if not isinstance(state, dict):
            await _show_manager(query, user_id)
            raise ApplicationHandlerStop
        value = data.rsplit(":", 1)[1]
        if value == "custom":
            state["step"] = "custom_time"
            await query.edit_message_text(
                "Введите местное время как ЧЧ:ММ.\nНапример: 06:30",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="sched:abort")]]),
            )
        else:
            state["local_time"] = _normalise_time(value)
            await _resolve_timezone_and_confirm(query, context)
        raise ApplicationHandlerStop
    if data.startswith("sched:tz:"):
        if not isinstance(state, dict):
            await _show_manager(query, user_id)
            raise ApplicationHandlerStop
        value = data[len("sched:tz:") :]
        if value == "manual":
            state["step"] = "manual_timezone"
            await query.edit_message_text(
                "Введите IANA-часовой пояс.\nНапример: Europe/Moscow, Europe/London, Asia/Yekaterinburg",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="sched:abort")]]),
            )
        else:
            state["timezone"] = _validate_timezone(value)
            state["step"] = "confirm"
            await query.edit_message_text(_confirmation_text(state), reply_markup=_confirmation_keyboard())
        raise ApplicationHandlerStop
    if data == "sched:edit:freq":
        if isinstance(state, dict):
            state["step"] = "frequency"
            await query.edit_message_text("Как часто отправлять?", reply_markup=_frequency_keyboard())
        raise ApplicationHandlerStop
    if data == "sched:edit:time":
        if isinstance(state, dict) and state.get("every_days"):
            await _show_time_step(query, context)
        raise ApplicationHandlerStop
    if data == "sched:save":
        if not isinstance(state, dict):
            await _show_manager(query, user_id)
            raise ApplicationHandlerStop
        try:
            item = schedule_store().add(
                user_id=user_id,
                chat_id=_chat_id(update),
                username=getattr(update.effective_user, "username", None),
                product=str(dict(state["spec"])["product"]),
                point=dict(dict(state["spec"])["point"]),
                params=dict(dict(state["spec"]).get("params") or {}),
                timezone_name=str(state["timezone"]),
                local_time=str(state["local_time"]),
                every_days=int(state["every_days"]),
            )
        except ScheduleError as exc:
            await query.edit_message_text(str(exc), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Расписания", callback_data="sched:list")]]))
            raise ApplicationHandlerStop
        context.user_data.pop(SCHEDULE_WIZARD_KEY, None)
        await query.edit_message_text(
            "✅ Расписание сохранено\n\n" + _detail_text(item),
            reply_markup=_detail_keyboard(item),
        )
        raise ApplicationHandlerStop

    if data.startswith(("sched:view:", "sched:run:", "sched:delete:", "sched:delete-confirm:")):
        schedule_id = data.rsplit(":", 1)[1]
        item = schedule_store().get(schedule_id)
        if item is None or item.user_id != user_id:
            await query.edit_message_text("Расписание не найдено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Расписания", callback_data="sched:list")]]))
            raise ApplicationHandlerStop
        if data.startswith("sched:view:"):
            await query.edit_message_text(_detail_text(item), reply_markup=_detail_keyboard(item))
        elif data.startswith("sched:delete-confirm:"):
            schedule_store().delete(item.schedule_id, user_id)
            await query.edit_message_text("🗑 Расписание удалено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Расписания", callback_data="sched:list")], [InlineKeyboardButton("➕ Новое", callback_data="sched:new")]]))
        elif data.startswith("sched:delete:"):
            await query.edit_message_text(
                "Удалить это расписание?\n\n" + _detail_text(item),
                reply_markup=_delete_confirmation_keyboard(item),
            )
        else:
            await query.edit_message_text("⏳ Формирую продукцию по сохранённым параметрам…")
            success = await _execute_and_record(context.application, namespace, item, schedule_store())
            refreshed = schedule_store().get(item.schedule_id)
            if refreshed is not None:
                await query.edit_message_text(
                    ("✅ Отправлено сейчас.\n\n" if success else "⚠️ Отправка завершилась ошибкой.\n\n") + _detail_text(refreshed),
                    reply_markup=_detail_keyboard(refreshed),
                )
        raise ApplicationHandlerStop


async def schedule_text(update: Update, context) -> None:
    state = context.user_data.get(SCHEDULE_WIZARD_KEY)
    message = update.effective_message
    if not isinstance(state, dict) or not message or not message.text:
        return
    step = str(state.get("step") or "")
    text = message.text.strip()
    if step == "custom_interval":
        try:
            state["every_days"] = _validate_interval(int(text))
        except (ValueError, ScheduleError) as exc:
            await message.reply_text(f"Ошибка: {exc}\nВведите число от 1 до 30.")
            raise ApplicationHandlerStop
        await _show_time_step(message, context)
        raise ApplicationHandlerStop
    if step == "custom_time":
        try:
            state["local_time"] = _normalise_time(text)
        except ScheduleError as exc:
            await message.reply_text(f"Ошибка: {exc}")
            raise ApplicationHandlerStop
        await _resolve_timezone_and_confirm(message, context)
        raise ApplicationHandlerStop
    if step == "manual_timezone":
        try:
            state["timezone"] = _validate_timezone(text)
        except ScheduleError as exc:
            await message.reply_text(f"Ошибка: {exc}")
            raise ApplicationHandlerStop
        state["step"] = "confirm"
        await message.reply_text(_confirmation_text(state), reply_markup=_confirmation_keyboard())
        raise ApplicationHandlerStop


async def schedule_product_run_interceptor(update: Update, context) -> None:
    query = update.callback_query
    if not query:
        return
    state = context.user_data.get(PRODUCT_WIZARD_KEY)
    if not isinstance(state, dict) or not state.get("_schedule_setup"):
        return
    await query.answer()
    try:
        spec = schedule_spec_from_product_state(state)
    except ScheduleError as exc:
        await query.edit_message_text(f"Ошибка расписания: {exc}")
        raise ApplicationHandlerStop
    context.user_data.pop(PRODUCT_WIZARD_KEY, None)
    await _begin_timing(query, context, spec)
    raise ApplicationHandlerStop


async def schedule_profile_lead_interceptor(update: Update, context) -> None:
    if not context.user_data.get(SCHEDULE_PROFILE_SETUP_KEY):
        return
    query = update.callback_query
    if not query:
        return
    await query.answer()
    pending = context.user_data.get("pending_profile")
    if not isinstance(pending, dict) or not isinstance(pending.get("point"), dict):
        context.user_data.pop(SCHEDULE_PROFILE_SETUP_KEY, None)
        await query.edit_message_text("Сначала выберите точку профиля.")
        raise ApplicationHandlerStop
    lead = int((query.data or "lead:24").split(":", 1)[1])
    spec = schedule_spec_from_profile(dict(pending["point"]), lead)
    context.user_data.pop(SCHEDULE_PROFILE_SETUP_KEY, None)
    context.user_data.pop("pending_profile", None)
    await _begin_timing(query, context, spec)
    raise ApplicationHandlerStop


async def schedule_meteogram_format_interceptor(update: Update, context) -> None:
    query = update.callback_query
    if not query:
        return
    import telegram_meteogram

    state = context.user_data.get(telegram_meteogram.SESSION_KEY)
    if not isinstance(state, dict) or not state.get("_schedule_setup"):
        return
    await query.answer()
    output_format = (query.data or "").rsplit(":", 1)[1]
    state["output_format"] = telegram_meteogram._normalise_output_format(output_format)
    state["step"] = "confirm"
    await query.edit_message_text(
        telegram_meteogram._summary(state),
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🕒 Далее: расписание", callback_data="meteo:run")],
                [
                    InlineKeyboardButton("Формат", callback_data="meteo:back:format"),
                    InlineKeyboardButton("Период", callback_data="meteo:back:period"),
                ],
                [InlineKeyboardButton("Отмена", callback_data="meteo:cancel")],
            ]
        ),
    )
    raise ApplicationHandlerStop


async def schedule_meteogram_run_interceptor(update: Update, context) -> None:
    query = update.callback_query
    if not query:
        return
    import telegram_meteogram

    state = context.user_data.get(telegram_meteogram.SESSION_KEY)
    if not isinstance(state, dict) or not state.get("_schedule_setup"):
        return
    await query.answer()
    try:
        spec = schedule_spec_from_meteogram_state(state)
    except ScheduleError as exc:
        await query.edit_message_text(f"Ошибка расписания: {exc}")
        raise ApplicationHandlerStop
    context.user_data.pop(telegram_meteogram.SESSION_KEY, None)
    await _begin_timing(query, context, spec)
    raise ApplicationHandlerStop


def register_schedule_handlers(application, namespace: dict[str, Any]) -> None:
    """Register manager, wizard interceptors and scheduler lifecycle."""

    application.add_handler(CommandHandler("schedule", schedule_command), group=-10)
    application.add_handler(
        CallbackQueryHandler(
            lambda update, context: schedule_callback(update, context, namespace),
            pattern=r"^(?:home:schedule|sched:)",
        ),
        group=-10,
    )
    application.add_handler(
        CallbackQueryHandler(schedule_product_run_interceptor, pattern=r"^wiz:run$"),
        group=-10,
    )
    application.add_handler(
        CallbackQueryHandler(schedule_profile_lead_interceptor, pattern=r"^lead:\d+$"),
        group=-10,
    )
    application.add_handler(
        CallbackQueryHandler(schedule_meteogram_format_interceptor, pattern=r"^meteo:format:(?:png|docx|pdf)$"),
        group=-10,
    )
    application.add_handler(
        CallbackQueryHandler(schedule_meteogram_run_interceptor, pattern=r"^meteo:run$"),
        group=-10,
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_text), group=-10)

    previous_post_init = getattr(application, "post_init", None)
    previous_post_shutdown = getattr(application, "post_shutdown", None)

    async def post_init(app) -> None:
        if previous_post_init:
            await previous_post_init(app)
        task = asyncio.create_task(_scheduler_loop(app, namespace), name="telegram-schedule-loop")
        app.bot_data["schedule_task"] = task

    async def post_shutdown(app) -> None:
        task = app.bot_data.pop("schedule_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if previous_post_shutdown:
            await previous_post_shutdown(app)

    application.post_init = post_init
    application.post_shutdown = post_shutdown
