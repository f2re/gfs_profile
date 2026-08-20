from __future__ import annotations

"""Persistent Telegram user locations and product preferences.

The bot remains a single-process long-polling application.  This module uses a
small SQLite database under ``.cache_gfs`` and stores only validated scalar
settings.  GFS run/cycle values and transient Telegram callback state are never
persisted.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

DEFAULT_DB_PATH = Path(
    os.getenv("TELEGRAM_PREFERENCES_DB", ".cache_gfs/telegram_preferences.sqlite3")
)
SCHEMA_VERSION = 1
RECENT_LOCATION_LIMIT = 10
_SCHEMA_LOCK = threading.RLock()
_INITIALIZED: set[Path] = set()

_ALLOWED_KEYS: dict[str, tuple[str, ...]] = {
    "profile": ("lead",),
    "aero": ("lead", "diagram_type"),
    "windgram": ("from", "to", "time_step", "top", "param"),
    "cloudgram": ("from", "to", "time_step", "mode"),
    "map": (
        "mode",
        "lead",
        "from",
        "to",
        "time_step",
        "basemap",
        "radius",
        "variants",
    ),
    "meteogram": ("source_id", "days", "output_format"),
    "route": ("origin", "destination", "lead", "speed", "mode", "spatial_step"),
}

_DEFAULTS: dict[str, dict[str, Any]] = {
    "profile": {"lead": 24},
    "aero": {"lead": 24, "diagram_type": "skewt"},
    "windgram": {
        "from": 0,
        "to": 120,
        "time_step": 6,
        "top": 500,
        "param": "wind",
    },
    "cloudgram": {"from": 0, "to": 72, "time_step": 3, "mode": "pro"},
    "map": {
        "mode": "gif",
        "from": 0,
        "to": 48,
        "time_step": 3,
        "basemap": "places",
        "radius": 100,
    },
    "meteogram": {"source_id": "gfs", "days": 5, "output_format": "png"},
    "route": {"lead": 24, "speed": 300, "mode": "simple", "spatial_step": 50},
}

_MAP_VARIANT_DEFAULTS: dict[str, dict[str, Any]] = {
    "gif": {"from": 0, "to": 48, "time_step": 3, "basemap": "places", "radius": 100},
    "series": {"from": 0, "to": 48, "time_step": 3, "basemap": "places", "radius": 100},
    "single": {"lead": 24, "basemap": "places", "radius": 100},
}


@dataclass(frozen=True, slots=True)
class StoredLocation:
    location_id: int
    user_id: int
    lat: float
    lon: float
    label: str
    source: str
    use_count: int
    pinned: bool
    last_used_at: str


@dataclass(frozen=True, slots=True)
class ProductPreference:
    user_id: int
    product: str
    params: dict[str, Any]
    point: dict[str, Any] | None
    success_count: int
    selected_at: str | None
    last_success_at: str | None
    kind: str


def _utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_DB_PATH


@contextmanager
def _connection(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    path = _db_path(db_path).resolve()
    with _SCHEMA_LOCK:
        if path in _INITIALIZED and path.exists():
            return
        with _connection(path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    label TEXT NOT NULL,
                    source TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_user_locations_recent
                    ON user_locations(user_id, pinned DESC, last_used_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_user_locations_coords
                    ON user_locations(user_id, lat, lon);

                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    active_location_id INTEGER,
                    last_product TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(active_location_id) REFERENCES user_locations(id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS product_state (
                    user_id INTEGER NOT NULL,
                    product TEXT NOT NULL,
                    selected_params_json TEXT,
                    selected_point_json TEXT,
                    selected_at TEXT,
                    success_params_json TEXT,
                    success_point_json TEXT,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    last_success_at TEXT,
                    PRIMARY KEY(user_id, product)
                );
                CREATE INDEX IF NOT EXISTS idx_product_state_quick
                    ON product_state(user_id, success_count DESC, last_success_at DESC);
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        _INITIALIZED.add(path)


def _clean_label(label: object, lat: float, lon: float) -> str:
    value = " ".join(str(label or "").split())
    return (value or f"{lat:.4f}, {lon:.4f}")[:200]


def normalise_point(point: Mapping[str, Any] | Any | None) -> dict[str, Any] | None:
    if point is None:
        return None
    if isinstance(point, Mapping):
        lat = float(point["lat"])
        lon = float(point["lon"])
        label = point.get("label")
        source = point.get("source", "manual")
    else:
        lat = float(getattr(point, "lat"))
        lon = float(getattr(point, "lon"))
        label = getattr(point, "label", None)
        source = getattr(point, "source", "manual")
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError("Координаты вне допустимого диапазона")
    return {
        "lat": lat,
        "lon": lon,
        "label": _clean_label(label, lat, lon),
        "source": str(source or "manual")[:40],
    }


def _row_to_location(row: sqlite3.Row) -> StoredLocation:
    return StoredLocation(
        location_id=int(row["id"]),
        user_id=int(row["user_id"]),
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        label=str(row["label"]),
        source=str(row["source"]),
        use_count=int(row["use_count"]),
        pinned=bool(row["pinned"]),
        last_used_at=str(row["last_used_at"]),
    )


def remember_location(
    user_id: int,
    point: Mapping[str, Any] | Any,
    *,
    activate: bool = True,
    db_path: str | Path | None = None,
) -> StoredLocation | None:
    user_id = int(user_id)
    if user_id <= 0:
        return None
    value = normalise_point(point)
    assert value is not None
    init_db(db_path)
    now = _utc_ts()
    with _connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM user_locations
            WHERE user_id=? AND ABS(lat - ?) <= 0.01 AND ABS(lon - ?) <= 0.01
            ORDER BY last_used_at DESC, id DESC
            LIMIT 1
            """,
            (user_id, value["lat"], value["lon"]),
        ).fetchone()
        if row is None:
            cursor = conn.execute(
                """
                INSERT INTO user_locations(
                    user_id, lat, lon, label, source, use_count, pinned,
                    created_at, last_used_at
                ) VALUES(?, ?, ?, ?, ?, 1, 0, ?, ?)
                """,
                (
                    user_id,
                    value["lat"],
                    value["lon"],
                    value["label"],
                    value["source"],
                    now,
                    now,
                ),
            )
            location_id = int(cursor.lastrowid)
        else:
            location_id = int(row["id"])
            conn.execute(
                """
                UPDATE user_locations
                SET lat=?, lon=?, label=?, source=?,
                    use_count=use_count+1, last_used_at=?
                WHERE id=?
                """,
                (
                    value["lat"],
                    value["lon"],
                    value["label"],
                    value["source"],
                    now,
                    location_id,
                ),
            )

        settings = conn.execute(
            "SELECT active_location_id FROM user_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        should_activate = activate or settings is None or settings["active_location_id"] is None
        if settings is None:
            conn.execute(
                """
                INSERT INTO user_settings(
                    user_id, active_location_id, created_at, updated_at
                ) VALUES(?, ?, ?, ?)
                """,
                (user_id, location_id if should_activate else None, now, now),
            )
        elif should_activate:
            conn.execute(
                "UPDATE user_settings SET active_location_id=?, updated_at=? WHERE user_id=?",
                (location_id, now, user_id),
            )
        row = conn.execute("SELECT * FROM user_locations WHERE id=?", (location_id,)).fetchone()
    return _row_to_location(row)


def get_recent_locations(
    user_id: int,
    limit: int = 4,
    *,
    db_path: str | Path | None = None,
) -> list[StoredLocation]:
    user_id = int(user_id)
    if user_id <= 0 or int(limit) <= 0:
        return []
    init_db(db_path)
    with _connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM user_locations
            WHERE user_id=?
            ORDER BY pinned DESC, last_used_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, max(1, min(int(limit), 50))),
        ).fetchall()
    return [_row_to_location(row) for row in rows]


def get_active_location(
    user_id: int,
    *,
    db_path: str | Path | None = None,
) -> StoredLocation | None:
    user_id = int(user_id)
    if user_id <= 0:
        return None
    init_db(db_path)
    with _connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT l.*
            FROM user_settings s
            JOIN user_locations l ON l.id=s.active_location_id
            WHERE s.user_id=? AND l.user_id=?
            """,
            (user_id, user_id),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT * FROM user_locations
                WHERE user_id=?
                ORDER BY pinned DESC, last_used_at DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
    return _row_to_location(row) if row is not None else None


def set_active_location(
    user_id: int,
    location_id: int,
    *,
    db_path: str | Path | None = None,
) -> bool:
    user_id = int(user_id)
    location_id = int(location_id)
    init_db(db_path)
    now = _utc_ts()
    with _connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM user_locations WHERE id=? AND user_id=?",
            (location_id, user_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            """
            INSERT INTO user_settings(user_id, active_location_id, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                active_location_id=excluded.active_location_id,
                updated_at=excluded.updated_at
            """,
            (user_id, location_id, now, now),
        )
    return True


def clear_locations(user_id: int, *, db_path: str | Path | None = None) -> None:
    user_id = int(user_id)
    if user_id <= 0:
        return
    init_db(db_path)
    with _connection(db_path) as conn:
        conn.execute(
            "UPDATE user_settings SET active_location_id=NULL, updated_at=? WHERE user_id=?",
            (_utc_ts(), user_id),
        )
        conn.execute("DELETE FROM user_locations WHERE user_id=?", (user_id,))


def default_product_params(product: str) -> dict[str, Any]:
    return normalise_product_params(product, {})


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = int(default)
    return max(minimum, min(result, maximum))


def _normalise_map_variant(mode: str, raw: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(raw) if isinstance(raw, Mapping) else {}
    basemap = str(source.get("basemap", "places"))
    if basemap not in {"basic", "water", "places", "roads"}:
        basemap = "places"
    radius = _safe_int(source.get("radius"), 100, 1, 100)
    if mode == "single":
        return {
            "lead": _safe_int(source.get("lead"), 24, 0, 384),
            "basemap": basemap,
            "radius": radius,
        }

    lead_from = _safe_int(source.get("from"), 0, 0, 384)
    lead_to = _safe_int(source.get("to"), 48, lead_from, 384)
    step = _safe_int(source.get("time_step"), 3 if mode == "gif" else 3, 1, 24)
    frame_count = ((lead_to - lead_from) // step) + 1
    if (lead_to - lead_from) % step:
        frame_count += 1
    if frame_count > 18:
        for candidate in (3, 6, 12, 24):
            count = ((lead_to - lead_from) // candidate) + 1
            if (lead_to - lead_from) % candidate:
                count += 1
            if count <= 18:
                step = candidate
                break
    return {
        "from": lead_from,
        "to": lead_to,
        "time_step": step,
        "basemap": basemap,
        "radius": radius,
    }


def normalise_product_params(
    product: str,
    params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    product = "aero" if str(product).lower() == "skewt" else str(product or "").lower()
    if product not in _ALLOWED_KEYS:
        return {}
    raw = dict(params or {})
    defaults = dict(_DEFAULTS[product])
    defaults.update({key: raw[key] for key in _ALLOWED_KEYS[product] if key in raw})

    if product == "profile":
        return {"lead": _safe_int(defaults.get("lead"), 24, 0, 384)}
    if product == "aero":
        return {"lead": _safe_int(defaults.get("lead"), 24, 0, 384), "diagram_type": "skewt"}
    if product == "windgram":
        lead_from = _safe_int(defaults.get("from"), 0, 0, 384)
        param = str(defaults.get("param", "wind"))
        if param not in {"wind", "temp", "rh"}:
            param = "wind"
        return {
            "from": lead_from,
            "to": _safe_int(defaults.get("to"), 120, lead_from, 384),
            "time_step": _safe_int(defaults.get("time_step"), 6, 1, 24),
            "top": _safe_int(defaults.get("top"), 500, 100, 1000),
            "param": param,
        }
    if product == "cloudgram":
        lead_from = _safe_int(defaults.get("from"), 0, 0, 384)
        mode = str(defaults.get("mode", "pro"))
        if mode not in {"pro", "simple"}:
            mode = "pro"
        return {
            "from": lead_from,
            "to": _safe_int(defaults.get("to"), 72, lead_from, 384),
            "time_step": _safe_int(defaults.get("time_step"), 3, 1, 24),
            "mode": mode,
        }
    if product == "map":
        mode = str(defaults.get("mode", "gif")).lower()
        if mode in {"animation", "anim"}:
            mode = "gif"
        if mode not in {"gif", "series", "single"}:
            mode = "gif"
        incoming_variants = raw.get("variants") or raw.get("_map_variants") or {}
        if not isinstance(incoming_variants, Mapping):
            incoming_variants = {}
        variants: dict[str, dict[str, Any]] = {}
        for variant_mode in ("gif", "series", "single"):
            base = dict(_MAP_VARIANT_DEFAULTS[variant_mode])
            candidate = incoming_variants.get(variant_mode)
            if isinstance(candidate, Mapping):
                base.update(candidate)
            variants[variant_mode] = _normalise_map_variant(variant_mode, base)
        current = dict(variants[mode])
        for key in ("lead", "from", "to", "time_step", "basemap", "radius"):
            if key in raw:
                current[key] = raw[key]
        variants[mode] = _normalise_map_variant(mode, current)
        result = {"mode": mode, **variants[mode], "variants": variants}
        if mode == "single":
            result["from"] = int(result["lead"])
            result["to"] = int(result["lead"])
            result["time_step"] = 3
        else:
            result["lead"] = _safe_int(raw.get("lead"), 24, 0, 384)
        return result
    if product == "meteogram":
        output = str(defaults.get("output_format", "png")).lower().lstrip(".")
        if output not in {"png", "docx", "pdf"}:
            output = "png"
        return {
            "source_id": str(defaults.get("source_id", "gfs"))[:40],
            "days": _safe_int(defaults.get("days"), 5, 1, 16),
            "output_format": output,
        }
    if product == "route":
        mode = "pro" if str(defaults.get("mode")) == "pro" else "simple"
        step = _safe_int(defaults.get("spatial_step"), 50, 25, 100)
        step = min((25, 50, 100), key=lambda candidate: abs(candidate - step))
        result: dict[str, Any] = {
            "lead": _safe_int(defaults.get("lead"), 24, 0, 384),
            "speed": _safe_int(defaults.get("speed"), 300, 50, 1000),
            "mode": mode,
            "spatial_step": step,
        }
        for key in ("origin", "destination"):
            point = normalise_point(defaults.get(key))
            if point is not None:
                result[key] = point
        return result
    return {}


def _json_dict(value: object) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _json_point(value: object) -> dict[str, Any] | None:
    raw = _json_dict(value)
    try:
        return normalise_point(raw) if raw else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _preference_from_row(row: sqlite3.Row, kind: str) -> ProductPreference | None:
    params_column = "selected_params_json" if kind == "selected" else "success_params_json"
    point_column = "selected_point_json" if kind == "selected" else "success_point_json"
    timestamp_column = "selected_at" if kind == "selected" else "last_success_at"
    if not row[params_column] or not row[timestamp_column]:
        return None
    product = str(row["product"])
    return ProductPreference(
        user_id=int(row["user_id"]),
        product=product,
        params=normalise_product_params(product, _json_dict(row[params_column])),
        point=_json_point(row[point_column]),
        success_count=int(row["success_count"] or 0),
        selected_at=str(row["selected_at"]) if row["selected_at"] else None,
        last_success_at=str(row["last_success_at"]) if row["last_success_at"] else None,
        kind=kind,
    )


def save_product_selection(
    user_id: int,
    product: str,
    params: Mapping[str, Any] | None,
    point: Mapping[str, Any] | Any | None,
    *,
    db_path: str | Path | None = None,
) -> ProductPreference | None:
    user_id = int(user_id)
    product = "aero" if str(product).lower() == "skewt" else str(product or "").lower()
    if user_id <= 0 or product not in _ALLOWED_KEYS:
        return None
    normalised = normalise_product_params(product, params)
    point_value = normalise_point(point)
    now = _utc_ts()
    init_db(db_path)
    with _connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO product_state(
                user_id, product, selected_params_json, selected_point_json, selected_at
            ) VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id, product) DO UPDATE SET
                selected_params_json=excluded.selected_params_json,
                selected_point_json=excluded.selected_point_json,
                selected_at=excluded.selected_at
            """,
            (
                user_id,
                product,
                json.dumps(normalised, ensure_ascii=False, sort_keys=True),
                json.dumps(point_value, ensure_ascii=False, sort_keys=True) if point_value else None,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM product_state WHERE user_id=? AND product=?",
            (user_id, product),
        ).fetchone()
    return _preference_from_row(row, "selected")


def record_product_success(
    user_id: int,
    product: str,
    params: Mapping[str, Any] | None,
    point: Mapping[str, Any] | Any | None,
    *,
    db_path: str | Path | None = None,
) -> ProductPreference | None:
    user_id = int(user_id)
    product = "aero" if str(product).lower() == "skewt" else str(product or "").lower()
    if user_id <= 0 or product not in _ALLOWED_KEYS:
        return None
    normalised = normalise_product_params(product, params)
    point_value = normalise_point(point)
    now = _utc_ts()
    init_db(db_path)
    with _connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO product_state(
                user_id, product,
                selected_params_json, selected_point_json, selected_at,
                success_params_json, success_point_json, success_count, last_success_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id, product) DO UPDATE SET
                selected_params_json=excluded.selected_params_json,
                selected_point_json=excluded.selected_point_json,
                selected_at=excluded.selected_at,
                success_params_json=excluded.success_params_json,
                success_point_json=excluded.success_point_json,
                success_count=product_state.success_count+1,
                last_success_at=excluded.last_success_at
            """,
            (
                user_id,
                product,
                json.dumps(normalised, ensure_ascii=False, sort_keys=True),
                json.dumps(point_value, ensure_ascii=False, sort_keys=True) if point_value else None,
                now,
                json.dumps(normalised, ensure_ascii=False, sort_keys=True),
                json.dumps(point_value, ensure_ascii=False, sort_keys=True) if point_value else None,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO user_settings(user_id, last_product, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_product=excluded.last_product,
                updated_at=excluded.updated_at
            """,
            (user_id, product, now, now),
        )
        row = conn.execute(
            "SELECT * FROM product_state WHERE user_id=? AND product=?",
            (user_id, product),
        ).fetchone()
    return _preference_from_row(row, "success")


def get_product_preference(
    user_id: int,
    product: str,
    *,
    include_selection: bool = True,
    db_path: str | Path | None = None,
) -> ProductPreference | None:
    user_id = int(user_id)
    product = "aero" if str(product).lower() == "skewt" else str(product or "").lower()
    if user_id <= 0:
        return None
    init_db(db_path)
    with _connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM product_state WHERE user_id=? AND product=?",
            (user_id, product),
        ).fetchone()
    if row is None:
        return None
    if include_selection:
        selected = _preference_from_row(row, "selected")
        if selected is not None:
            return selected
    return _preference_from_row(row, "success")


def get_success_preference(
    user_id: int,
    product: str,
    *,
    db_path: str | Path | None = None,
) -> ProductPreference | None:
    return get_product_preference(
        user_id,
        product,
        include_selection=False,
        db_path=db_path,
    )


def get_last_success_preference(
    user_id: int,
    *,
    db_path: str | Path | None = None,
) -> ProductPreference | None:
    user_id = int(user_id)
    if user_id <= 0:
        return None
    init_db(db_path)
    with _connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT p.*
            FROM user_settings s
            JOIN product_state p
              ON p.user_id=s.user_id AND p.product=s.last_product
            WHERE s.user_id=? AND p.last_success_at IS NOT NULL
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT * FROM product_state
                WHERE user_id=? AND last_success_at IS NOT NULL
                ORDER BY last_success_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
    return _preference_from_row(row, "success") if row is not None else None


def get_quick_preferences(
    user_id: int,
    limit: int = 2,
    *,
    db_path: str | Path | None = None,
) -> list[ProductPreference]:
    user_id = int(user_id)
    if user_id <= 0 or int(limit) <= 0:
        return []
    init_db(db_path)
    with _connection(db_path) as conn:
        settings = conn.execute(
            "SELECT last_product FROM user_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        last_product = str(settings["last_product"]) if settings and settings["last_product"] else ""
        rows = conn.execute(
            """
            SELECT * FROM product_state
            WHERE user_id=? AND last_success_at IS NOT NULL
            ORDER BY CASE WHEN product=? THEN 0 ELSE 1 END,
                     success_count DESC,
                     last_success_at DESC
            LIMIT ?
            """,
            (user_id, last_product, max(1, min(int(limit), 5))),
        ).fetchall()
    return [preference for row in rows if (preference := _preference_from_row(row, "success"))]


def list_product_preferences(
    user_id: int,
    *,
    db_path: str | Path | None = None,
) -> list[ProductPreference]:
    user_id = int(user_id)
    if user_id <= 0:
        return []
    init_db(db_path)
    with _connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM product_state
            WHERE user_id=? AND last_success_at IS NOT NULL
            ORDER BY last_success_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [preference for row in rows if (preference := _preference_from_row(row, "success"))]


def clear_product_preference(
    user_id: int,
    product: str,
    *,
    db_path: str | Path | None = None,
) -> None:
    user_id = int(user_id)
    product = "aero" if str(product).lower() == "skewt" else str(product or "").lower()
    init_db(db_path)
    with _connection(db_path) as conn:
        conn.execute("DELETE FROM product_state WHERE user_id=? AND product=?", (user_id, product))
        conn.execute(
            """
            UPDATE user_settings SET last_product=NULL, updated_at=?
            WHERE user_id=? AND last_product=?
            """,
            (_utc_ts(), user_id, product),
        )


def clear_user_data(user_id: int, *, db_path: str | Path | None = None) -> None:
    user_id = int(user_id)
    if user_id <= 0:
        return
    init_db(db_path)
    with _connection(db_path) as conn:
        conn.execute("DELETE FROM product_state WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM user_settings WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM user_locations WHERE user_id=?", (user_id,))
