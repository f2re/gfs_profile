from __future__ import annotations

"""Persistent messenger-neutral active/recent locations.

The store shares ``MESSENGER_PREFERENCES_DB`` with saved recipes. Identity is
always ``platform + user_id`` so Telegram, MAX and VK data never collide.
Route endpoints may be remembered with ``activate=False`` and therefore cannot
silently replace the user's active point.
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

DEFAULT_DB_PATH = Path(os.getenv("MESSENGER_PREFERENCES_DB", ".cache_gfs/messenger_preferences.sqlite3"))
_LOCK = RLock()
_INITIALIZED: set[Path] = set()


@dataclass(frozen=True, slots=True)
class MessengerLocation:
    location_id: int
    platform: str
    user_id: str
    lat: float
    lon: float
    label: str
    source: str
    use_count: int
    active: bool
    created_at: str
    last_used_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _point(value: Mapping[str, Any] | Any) -> tuple[float, float, str, str]:
    if isinstance(value, Mapping):
        lat, lon = float(value["lat"]), float(value["lon"])
        label = value.get("label")
        source = value.get("source", "manual")
    else:
        lat, lon = float(value.lat), float(value.lon)
        label = getattr(value, "label", None)
        source = getattr(value, "source", "manual")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Координаты точки вне допустимого диапазона")
    clean_label = " ".join(str(label or "").split()) or f"{lat:.4f}, {lon:.4f}"
    return lat, lon, clean_label[:200], str(source or "manual")[:40]


def _coord_key(lat: float, lon: float) -> str:
    return f"{lat:.4f},{lon:.4f}"


class MessengerLocationStore:
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
                        CREATE TABLE IF NOT EXISTS messenger_user_locations(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            platform TEXT NOT NULL,
                            user_id TEXT NOT NULL,
                            coord_key TEXT NOT NULL,
                            lat REAL NOT NULL,
                            lon REAL NOT NULL,
                            label TEXT NOT NULL,
                            source TEXT NOT NULL,
                            use_count INTEGER NOT NULL DEFAULT 1,
                            active INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL,
                            last_used_at TEXT NOT NULL,
                            UNIQUE(platform,user_id,coord_key)
                        );
                        CREATE INDEX IF NOT EXISTS idx_messenger_locations_recent
                          ON messenger_user_locations(platform,user_id,active DESC,last_used_at DESC,id DESC);
                        """
                    )
            finally:
                conn.close()
            _INITIALIZED.add(path)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> MessengerLocation | None:
        if row is None:
            return None
        return MessengerLocation(
            int(row["id"]),
            str(row["platform"]),
            str(row["user_id"]),
            float(row["lat"]),
            float(row["lon"]),
            str(row["label"]),
            str(row["source"]),
            int(row["use_count"]),
            bool(row["active"]),
            str(row["created_at"]),
            str(row["last_used_at"]),
        )

    def remember(
        self,
        platform: str,
        user_id: str | int,
        point: Mapping[str, Any] | Any,
        *,
        activate: bool = True,
    ) -> MessengerLocation:
        """Record real interactive use of a point and increment its counter."""

        platform = str(platform).lower().strip()
        user_id = str(user_id).strip()
        if not platform or not user_id:
            raise ValueError("platform and user_id are required")
        lat, lon, label, source = _point(point)
        key, now = _coord_key(lat, lon), _now()
        self.init()
        conn = self._connect()
        try:
            with conn:
                if activate:
                    conn.execute(
                        "UPDATE messenger_user_locations SET active=0 WHERE platform=? AND user_id=?",
                        (platform, user_id),
                    )
                conn.execute(
                    """
                    INSERT INTO messenger_user_locations(
                        platform,user_id,coord_key,lat,lon,label,source,use_count,active,created_at,last_used_at
                    ) VALUES(?,?,?,?,?,?,?,1,?,?,?)
                    ON CONFLICT(platform,user_id,coord_key) DO UPDATE SET
                        lat=excluded.lat,
                        lon=excluded.lon,
                        label=excluded.label,
                        source=excluded.source,
                        use_count=messenger_user_locations.use_count+1,
                        active=CASE WHEN excluded.active=1 THEN 1 ELSE messenger_user_locations.active END,
                        last_used_at=excluded.last_used_at
                    """,
                    (platform, user_id, key, lat, lon, label, source, 1 if activate else 0, now, now),
                )
                row = conn.execute(
                    "SELECT * FROM messenger_user_locations WHERE platform=? AND user_id=? AND coord_key=?",
                    (platform, user_id, key),
                ).fetchone()
        finally:
            conn.close()
        item = self._row(row)
        if item is None:
            raise RuntimeError("location was not stored")
        return item

    def ensure(
        self,
        platform: str,
        user_id: str | int,
        point: Mapping[str, Any] | Any,
        *,
        activate: bool = False,
        used_at: str | None = None,
    ) -> MessengerLocation:
        """Import/mirror a known point without inflating ``use_count``.

        This is used to migrate locations from already existing saved recipes.
        Newer timestamps win, while a non-active import never clears the current
        active point.
        """

        platform = str(platform).lower().strip()
        user_id = str(user_id).strip()
        if not platform or not user_id:
            raise ValueError("platform and user_id are required")
        lat, lon, label, source = _point(point)
        key = _coord_key(lat, lon)
        timestamp = str(used_at or _now())
        self.init()
        conn = self._connect()
        try:
            with conn:
                if activate:
                    conn.execute(
                        "UPDATE messenger_user_locations SET active=0 WHERE platform=? AND user_id=?",
                        (platform, user_id),
                    )
                conn.execute(
                    """
                    INSERT INTO messenger_user_locations(
                        platform,user_id,coord_key,lat,lon,label,source,use_count,active,created_at,last_used_at
                    ) VALUES(?,?,?,?,?,?,?,1,?,?,?)
                    ON CONFLICT(platform,user_id,coord_key) DO UPDATE SET
                        lat=excluded.lat,
                        lon=excluded.lon,
                        label=excluded.label,
                        source=excluded.source,
                        active=CASE WHEN excluded.active=1 THEN 1 ELSE messenger_user_locations.active END,
                        last_used_at=CASE
                            WHEN excluded.last_used_at > messenger_user_locations.last_used_at
                            THEN excluded.last_used_at
                            ELSE messenger_user_locations.last_used_at
                        END
                    """,
                    (platform, user_id, key, lat, lon, label, source, 1 if activate else 0, timestamp, timestamp),
                )
                row = conn.execute(
                    "SELECT * FROM messenger_user_locations WHERE platform=? AND user_id=? AND coord_key=?",
                    (platform, user_id, key),
                ).fetchone()
        finally:
            conn.close()
        item = self._row(row)
        if item is None:
            raise RuntimeError("location was not ensured")
        return item

    def recent(self, platform: str, user_id: str | int, *, limit: int = 10) -> list[MessengerLocation]:
        self.init()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM messenger_user_locations
                WHERE platform=? AND user_id=?
                ORDER BY active DESC,last_used_at DESC,id DESC LIMIT ?
                """,
                (str(platform).lower(), str(user_id), max(1, min(int(limit), 50))),
            ).fetchall()
        finally:
            conn.close()
        return [item for row in rows if (item := self._row(row))]

    def active(self, platform: str, user_id: str | int) -> MessengerLocation | None:
        self.init()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM messenger_user_locations
                WHERE platform=? AND user_id=? AND active=1
                ORDER BY last_used_at DESC,id DESC LIMIT 1
                """,
                (str(platform).lower(), str(user_id)),
            ).fetchone()
        finally:
            conn.close()
        return self._row(row)

    def set_active(self, platform: str, user_id: str | int, location_id: int) -> MessengerLocation | None:
        platform, user_id = str(platform).lower(), str(user_id)
        self.init()
        conn = self._connect()
        try:
            with conn:
                row = conn.execute(
                    "SELECT * FROM messenger_user_locations WHERE id=? AND platform=? AND user_id=?",
                    (int(location_id), platform, user_id),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "UPDATE messenger_user_locations SET active=0 WHERE platform=? AND user_id=?",
                    (platform, user_id),
                )
                conn.execute(
                    "UPDATE messenger_user_locations SET active=1,last_used_at=? WHERE id=?",
                    (_now(), int(location_id)),
                )
                row = conn.execute(
                    "SELECT * FROM messenger_user_locations WHERE id=?",
                    (int(location_id),),
                ).fetchone()
        finally:
            conn.close()
        return self._row(row)

    def clear(self, platform: str, user_id: str | int) -> None:
        self.init()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM messenger_user_locations WHERE platform=? AND user_id=?",
                    (str(platform).lower(), str(user_id)),
                )
        finally:
            conn.close()
