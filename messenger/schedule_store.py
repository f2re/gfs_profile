from __future__ import annotations

"""Persistent platform-neutral schedules for common messenger products."""

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .product_executor import ProductSnapshot

DEFAULT_DB_PATH = Path(os.getenv("MESSENGER_PREFERENCES_DB", ".cache_gfs/messenger_preferences.sqlite3"))
MAX_SCHEDULES_PER_USER = 2
DEFAULT_MAX_LATE_MINUTES = 180
_LOCK = RLock()
_INITIALIZED: set[Path] = set()


class ScheduleError(RuntimeError):
    pass


class ScheduleLimitError(ScheduleError):
    pass


@dataclass(frozen=True, slots=True)
class MessengerSchedule:
    schedule_id: int
    platform: str
    user_id: str
    chat_id: str
    product: str
    point: dict[str, Any] | None
    params: dict[str, Any]
    timezone: str
    local_time: str
    every_days: int
    next_run_utc: str
    created_at_utc: str
    last_started_utc: str | None
    last_finished_utc: str | None
    last_status: str | None
    last_error: str | None
    consecutive_failures: int
    enabled: bool

    @property
    def next_run_datetime_utc(self) -> datetime:
        return parse_utc(self.next_run_utc)

    def snapshot(self) -> ProductSnapshot:
        return ProductSnapshot(self.product, self.point, dict(self.params))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def normalize_time(value: str) -> str:
    text = str(value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, TypeError) as exc:
        raise ScheduleError("Время задаётся как ЧЧ:ММ, например 06:00") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduleError("Время задаётся как ЧЧ:ММ, например 06:00")
    return f"{hour:02d}:{minute:02d}"


def validate_interval(value: int) -> int:
    value = int(value)
    if not 1 <= value <= 30:
        raise ScheduleError("Интервал должен быть от 1 до 30 дней")
    return value


def validate_timezone(value: str) -> str:
    name = str(value or "").strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError("Неизвестный часовой пояс") from exc
    return name


def _valid_local_datetime(day: date, local_time: str, tz: ZoneInfo) -> datetime:
    hour, minute = (int(part) for part in normalize_time(local_time).split(":", 1))
    naive = datetime.combine(day, dt_time(hour=hour, minute=minute))
    for offset_minute in range(0, 181):
        candidate_naive = naive + timedelta(minutes=offset_minute)
        for fold in (0, 1):
            candidate = candidate_naive.replace(tzinfo=tz, fold=fold)
            round_trip = candidate.astimezone(timezone.utc).astimezone(tz)
            if round_trip.replace(tzinfo=None) == candidate_naive and round_trip.fold == fold:
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
    timezone_name = validate_timezone(timezone_name)
    local_time = normalize_time(local_time)
    every_days = validate_interval(every_days)
    tz = ZoneInfo(timezone_name)
    if previous_scheduled_utc is not None:
        previous_local = as_utc(previous_scheduled_utc).astimezone(tz)
        target_day = previous_local.date() + timedelta(days=every_days)
        return _valid_local_datetime(target_day, local_time, tz).astimezone(timezone.utc)
    now = as_utc(now_utc or utc_now())
    local_now = now.astimezone(tz)
    candidate = _valid_local_datetime(local_now.date(), local_time, tz)
    if candidate <= local_now:
        candidate = _valid_local_datetime(local_now.date() + timedelta(days=1), local_time, tz)
    return candidate.astimezone(timezone.utc)


def resolve_point_timezone(point: Mapping[str, Any] | Any, timeout: float | None = None) -> str:
    lat = float(point["lat"] if isinstance(point, Mapping) else point.lat)
    lon = float(point["lon"] if isinstance(point, Mapping) else point.lon)
    timeout = float(timeout or os.getenv("MESSENGER_SCHEDULE_TIMEZONE_TIMEOUT", "10"))
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1, "current": "temperature_2m"},
        timeout=max(2.0, timeout),
    )
    response.raise_for_status()
    payload = response.json()
    name = str(payload.get("timezone") or "").strip()
    if not name:
        raise ScheduleError("Сервис не вернул часовой пояс точки")
    return validate_timezone(name)


def _signature(snapshot: ProductSnapshot, timezone_name: str, local_time: str, every_days: int) -> str:
    raw = json.dumps(
        {
            "product": snapshot.product,
            "point": snapshot.point,
            "params": snapshot.params,
            "timezone": timezone_name,
            "local_time": local_time,
            "every_days": int(every_days),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class MessengerScheduleStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def init(self) -> None:
        path = self.path.resolve()
        with _LOCK:
            if path in _INITIALIZED and path.exists():
                return
            conn = self._connect()
            try:
                with conn:
                    conn.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS messenger_schedules(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            platform TEXT NOT NULL,
                            user_id TEXT NOT NULL,
                            chat_id TEXT NOT NULL,
                            product TEXT NOT NULL,
                            point_json TEXT,
                            params_json TEXT NOT NULL,
                            signature TEXT NOT NULL,
                            timezone TEXT NOT NULL,
                            local_time TEXT NOT NULL,
                            every_days INTEGER NOT NULL,
                            next_run_utc TEXT NOT NULL,
                            created_at_utc TEXT NOT NULL,
                            last_started_utc TEXT,
                            last_finished_utc TEXT,
                            last_status TEXT,
                            last_error TEXT,
                            consecutive_failures INTEGER NOT NULL DEFAULT 0,
                            enabled INTEGER NOT NULL DEFAULT 1,
                            UNIQUE(platform,user_id,signature)
                        );
                        CREATE INDEX IF NOT EXISTS idx_messenger_schedules_due
                          ON messenger_schedules(enabled,next_run_utc);
                        CREATE INDEX IF NOT EXISTS idx_messenger_schedules_user
                          ON messenger_schedules(platform,user_id,next_run_utc);
                        """
                    )
            finally:
                conn.close()
            _INITIALIZED.add(path)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> MessengerSchedule | None:
        if row is None:
            return None
        point = json.loads(row["point_json"]) if row["point_json"] else None
        params = json.loads(row["params_json"])
        return MessengerSchedule(
            int(row["id"]), str(row["platform"]), str(row["user_id"]), str(row["chat_id"]), str(row["product"]),
            dict(point) if isinstance(point, dict) else None, dict(params), str(row["timezone"]), str(row["local_time"]),
            int(row["every_days"]), str(row["next_run_utc"]), str(row["created_at_utc"]),
            str(row["last_started_utc"]) if row["last_started_utc"] else None,
            str(row["last_finished_utc"]) if row["last_finished_utc"] else None,
            str(row["last_status"]) if row["last_status"] else None,
            str(row["last_error"]) if row["last_error"] else None,
            int(row["consecutive_failures"]), bool(row["enabled"]),
        )

    def add(
        self,
        platform: str,
        user_id: str | int,
        chat_id: str | int,
        snapshot: ProductSnapshot,
        timezone_name: str,
        local_time: str,
        every_days: int,
        *,
        now_utc: datetime | None = None,
    ) -> MessengerSchedule:
        platform, user_id, chat_id = str(platform).lower(), str(user_id), str(chat_id)
        timezone_name = validate_timezone(timezone_name)
        local_time = normalize_time(local_time)
        every_days = validate_interval(every_days)
        now = as_utc(now_utc or utc_now())
        next_run = next_run_utc(timezone_name, local_time, every_days, now_utc=now)
        signature = _signature(snapshot, timezone_name, local_time, every_days)
        self.init()
        conn = self._connect()
        try:
            with conn:
                count = int(conn.execute(
                    "SELECT COUNT(*) n FROM messenger_schedules WHERE platform=? AND user_id=? AND enabled=1",
                    (platform, user_id),
                ).fetchone()["n"])
                if count >= MAX_SCHEDULES_PER_USER:
                    raise ScheduleLimitError(f"Можно создать не более {MAX_SCHEDULES_PER_USER} расписаний")
                try:
                    cur = conn.execute(
                        """
                        INSERT INTO messenger_schedules(
                            platform,user_id,chat_id,product,point_json,params_json,signature,timezone,local_time,every_days,next_run_utc,created_at_utc
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            platform, user_id, chat_id, snapshot.product,
                            json.dumps(snapshot.point, ensure_ascii=False, sort_keys=True) if snapshot.point else None,
                            json.dumps(snapshot.params, ensure_ascii=False, sort_keys=True), signature,
                            timezone_name, local_time, every_days, iso_utc(next_run), iso_utc(now),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ScheduleError("Такое расписание уже существует") from exc
                row = conn.execute("SELECT * FROM messenger_schedules WHERE id=?", (int(cur.lastrowid),)).fetchone()
        finally:
            conn.close()
        item = self._row(row)
        if item is None:
            raise RuntimeError("schedule was not stored")
        return item

    def get(self, platform: str, user_id: str | int, schedule_id: int) -> MessengerSchedule | None:
        self.init(); conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM messenger_schedules WHERE id=? AND platform=? AND user_id=?",
                (int(schedule_id), str(platform).lower(), str(user_id)),
            ).fetchone()
        finally: conn.close()
        return self._row(row)

    def list_for_user(self, platform: str, user_id: str | int) -> list[MessengerSchedule]:
        self.init(); conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM messenger_schedules WHERE platform=? AND user_id=? AND enabled=1 ORDER BY next_run_utc,id",
                (str(platform).lower(), str(user_id)),
            ).fetchall()
        finally: conn.close()
        return [item for row in rows if (item := self._row(row))]

    def delete(self, platform: str, user_id: str | int, schedule_id: int) -> bool:
        self.init(); conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "DELETE FROM messenger_schedules WHERE id=? AND platform=? AND user_id=?",
                    (int(schedule_id), str(platform).lower(), str(user_id)),
                )
            return bool(cur.rowcount)
        finally: conn.close()

    def claim_due(self, *, now_utc: datetime | None = None, max_late_minutes: int = DEFAULT_MAX_LATE_MINUTES) -> tuple[list[MessengerSchedule], list[MessengerSchedule]]:
        now = as_utc(now_utc or utc_now())
        max_late = timedelta(minutes=max(0, int(max_late_minutes)))
        due: list[MessengerSchedule] = []
        skipped: list[MessengerSchedule] = []
        self.init(); conn = self._connect()
        try:
            with conn:
                rows = conn.execute(
                    "SELECT * FROM messenger_schedules WHERE enabled=1 AND next_run_utc<=? ORDER BY next_run_utc,id",
                    (iso_utc(now),),
                ).fetchall()
                for row in rows:
                    item = self._row(row)
                    if item is None:
                        continue
                    scheduled = item.next_run_datetime_utc
                    if now - scheduled > max_late:
                        skipped.append(item)
                        next_value = scheduled
                        for _ in range(370):
                            if next_value > now:
                                break
                            next_value = next_run_utc(item.timezone, item.local_time, item.every_days, previous_scheduled_utc=next_value)
                        conn.execute(
                            "UPDATE messenger_schedules SET next_run_utc=?,last_status='skipped',last_error=? WHERE id=?",
                            (iso_utc(next_value), "пропущено после длительного простоя", item.schedule_id),
                        )
                        continue
                    due.append(item)
                    next_value = next_run_utc(item.timezone, item.local_time, item.every_days, previous_scheduled_utc=scheduled)
                    conn.execute(
                        "UPDATE messenger_schedules SET last_started_utc=?,next_run_utc=? WHERE id=?",
                        (iso_utc(now), iso_utc(next_value), item.schedule_id),
                    )
        finally: conn.close()
        return due, skipped

    def mark_result(self, schedule_id: int, *, success: bool, error: str | None = None, finished_at_utc: datetime | None = None) -> None:
        self.init(); conn = self._connect(); finished = iso_utc(finished_at_utc or utc_now())
        try:
            with conn:
                if success:
                    conn.execute(
                        "UPDATE messenger_schedules SET last_finished_utc=?,last_status='ok',last_error=NULL,consecutive_failures=0 WHERE id=?",
                        (finished, int(schedule_id)),
                    )
                else:
                    conn.execute(
                        "UPDATE messenger_schedules SET last_finished_utc=?,last_status='error',last_error=?,consecutive_failures=consecutive_failures+1 WHERE id=?",
                        (finished, str(error or "неизвестная ошибка")[:500], int(schedule_id)),
                    )
        finally: conn.close()
