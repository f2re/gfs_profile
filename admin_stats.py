from __future__ import annotations

import csv
import html
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Iterable

DEFAULT_DB_PATH = Path(os.getenv("TELEGRAM_ADMIN_DB", ".cache_gfs/admin_stats.sqlite3"))
ADMIN_IDS_ENV = ("TELEGRAM_ADMIN_IDS", "TELEGRAM_ADMIN_USER_IDS")
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class KnownUser:
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    is_bot: bool
    first_seen: str
    last_seen: str
    requests_count: int = 0
    last_city: str | None = None
    last_product: str | None = None


def _utc_ts(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time()))


def _db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_DB_PATH


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def parse_admin_ids(raw_values: Iterable[str | None] | None = None) -> set[int]:
    if raw_values is None:
        raw_values = (os.getenv(name) for name in ADMIN_IDS_ENV)
    ids: set[int] = set()
    for raw in raw_values:
        if not raw:
            continue
        for part in re.split(r"[,;\s]+", raw.strip()):
            if not part:
                continue
            try:
                ids.add(int(part))
            except ValueError:
                continue
    return ids


def init_admin_db(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_bot INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                requests_count INTEGER NOT NULL DEFAULT 0,
                last_city TEXT,
                last_product TEXT
            );
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL,
                added_by INTEGER,
                source TEXT NOT NULL DEFAULT 'manual',
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER,
                status TEXT NOT NULL,
                product TEXT NOT NULL,
                user_id INTEGER,
                username TEXT,
                city TEXT,
                request_text TEXT,
                lead_from INTEGER,
                lead_to INTEGER,
                run_date TEXT,
                run_cycle TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_requests_started_at ON requests(started_at);
            CREATE INDEX IF NOT EXISTS idx_requests_product ON requests(product);
            CREATE INDEX IF NOT EXISTS idx_requests_user_id ON requests(user_id);
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
            """
        )
        conn.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
        now = _utc_ts()
        for user_id in parse_admin_ids():
            conn.execute(
                """
                INSERT INTO admins(user_id, added_at, added_by, source, enabled)
                VALUES(?, ?, NULL, 'env', 1)
                ON CONFLICT(user_id) DO UPDATE SET enabled=1, source='env'
                """,
                (user_id, now),
            )


def record_telegram_user(user, db_path: str | Path | None = None) -> None:
    if not user or not getattr(user, "id", None):
        return
    init_admin_db(db_path)
    now = _utc_ts()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, username, first_name, last_name, is_bot, first_seen, last_seen)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                is_bot=excluded.is_bot,
                last_seen=excluded.last_seen
            """,
            (
                int(user.id),
                getattr(user, "username", None),
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
                1 if getattr(user, "is_bot", False) else 0,
                now,
                now,
            ),
        )


def is_admin(user_id: int | None, db_path: str | Path | None = None) -> bool:
    if not user_id:
        return False
    if int(user_id) in parse_admin_ids():
        return True
    init_admin_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT enabled FROM admins WHERE user_id=?", (int(user_id),)).fetchone()
    return bool(row and int(row["enabled"]) == 1)


def add_admin(user_id: int, added_by: int | None = None, source: str = "manual", db_path: str | Path | None = None) -> None:
    init_admin_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO admins(user_id, added_at, added_by, source, enabled)
            VALUES(?, ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET enabled=1, added_by=excluded.added_by, source=excluded.source
            """,
            (int(user_id), _utc_ts(), added_by, source),
        )


def _row_to_user(row: sqlite3.Row) -> KnownUser:
    return KnownUser(
        user_id=int(row["user_id"]),
        username=row["username"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        is_bot=bool(row["is_bot"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        requests_count=int(row["requests_count"] or 0),
        last_city=row["last_city"],
        last_product=row["last_product"],
    )


def find_users(query: str = "", limit: int = 20, db_path: str | Path | None = None) -> list[KnownUser]:
    init_admin_db(db_path)
    query = query.strip().lstrip("@")
    limit = max(1, min(int(limit), 100))
    with _connect(db_path) as conn:
        if query:
            pattern = f"%{query.lower()}%"
            rows = conn.execute(
                """
                SELECT * FROM users
                WHERE CAST(user_id AS TEXT)=?
                   OR lower(COALESCE(username, '')) LIKE ?
                   OR lower(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) LIKE ?
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (query, pattern, pattern, limit),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_user(row) for row in rows]


def add_admin_by_query(query: str, added_by: int | None = None, db_path: str | Path | None = None) -> KnownUser:
    normalized = query.strip().lstrip("@")
    users = find_users(normalized, limit=2, db_path=db_path)
    if len(users) == 1:
        add_admin(users[0].user_id, added_by=added_by, source="admin-command", db_path=db_path)
        return users[0]
    if not users and normalized.lstrip("-").isdigit():
        user_id = int(normalized)
        add_admin(user_id, added_by=added_by, source="admin-command-id", db_path=db_path)
        return KnownUser(user_id, None, None, None, False, "", "")
    raise ValueError("Пользователь не найден или найдено несколько совпадений. Уточните id или @username из /admin users.")


def record_request_start(
    *,
    product: str,
    user_id: int | None,
    username: str | None = None,
    city: str | None = None,
    request_text: str | None = None,
    lead_from: int | None = None,
    lead_to: int | None = None,
    run_date: str | None = None,
    run_cycle: str | None = None,
    db_path: str | Path | None = None,
) -> int:
    init_admin_db(db_path)
    now = _utc_ts()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO requests(started_at, status, product, user_id, username, city, request_text, lead_from, lead_to, run_date, run_cycle)
            VALUES(?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now, product, user_id, username, city, request_text, lead_from, lead_to, run_date, run_cycle),
        )
        if user_id:
            conn.execute(
                """
                UPDATE users
                SET requests_count=requests_count+1, last_city=COALESCE(?, last_city), last_product=?
                WHERE user_id=?
                """,
                (city, product, int(user_id)),
            )
        return int(cursor.lastrowid)


def record_request_finish(
    request_id: int,
    *,
    status: str = "ok",
    duration_ms: int | None = None,
    error: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    init_admin_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT started_at FROM requests WHERE id=?", (int(request_id),)).fetchone()
        if not row:
            return
        conn.execute(
            """
            UPDATE requests
            SET finished_at=?, duration_ms=?, status=?, error=?
            WHERE id=?
            """,
            (_utc_ts(), duration_ms, status, error, int(request_id)),
        )


def _since_expr(days: int) -> str:
    days = max(1, min(int(days), 3650))
    return f"datetime('now', '-{days} days')"


def usage_summary(days: int = 7, db_path: str | Path | None = None) -> dict[str, object]:
    init_admin_db(db_path)
    with _connect(db_path) as conn:
        total_users = int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])
        active_users = int(conn.execute(f"SELECT COUNT(DISTINCT user_id) AS n FROM requests WHERE started_at >= {_since_expr(days)}").fetchone()["n"])
        total_requests = int(conn.execute(f"SELECT COUNT(*) AS n FROM requests WHERE started_at >= {_since_expr(days)}").fetchone()["n"])
        failed_requests = int(conn.execute(f"SELECT COUNT(*) AS n FROM requests WHERE started_at >= {_since_expr(days)} AND status NOT IN ('ok', 'done')").fetchone()["n"])
        avg_duration = conn.execute(f"SELECT AVG(duration_ms) AS n FROM requests WHERE started_at >= {_since_expr(days)} AND duration_ms IS NOT NULL").fetchone()["n"]
        products = conn.execute(
            f"SELECT product, COUNT(*) AS n, AVG(duration_ms) AS avg_ms FROM requests WHERE started_at >= {_since_expr(days)} GROUP BY product ORDER BY n DESC LIMIT 8"
        ).fetchall()
        cities = conn.execute(
            f"SELECT city, COUNT(*) AS n FROM requests WHERE started_at >= {_since_expr(days)} AND city IS NOT NULL AND city != '' GROUP BY city ORDER BY n DESC LIMIT 8"
        ).fetchall()
    return {
        "days": days,
        "total_users": total_users,
        "active_users": active_users,
        "total_requests": total_requests,
        "failed_requests": failed_requests,
        "avg_duration_ms": int(avg_duration or 0),
        "products": [(row["product"], int(row["n"]), int(row["avg_ms"] or 0)) for row in products],
        "cities": [(row["city"], int(row["n"])) for row in cities],
    }


def recent_requests(limit: int = 10, db_path: str | Path | None = None) -> list[sqlite3.Row]:
    init_admin_db(db_path)
    limit = max(1, min(int(limit), 50))
    with _connect(db_path) as conn:
        return conn.execute(
            """
            SELECT id, started_at, duration_ms, status, product, user_id, username, city, lead_from, lead_to, error
            FROM requests
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def format_admin_summary(days: int = 7, db_path: str | Path | None = None) -> str:
    data = usage_summary(days=days, db_path=db_path)
    lines = [
        f"ADMIN · GFS bot · {data['days']}d",
        f"users total/active: {data['total_users']}/{data['active_users']}",
        f"requests: {data['total_requests']} · errors: {data['failed_requests']}",
        f"avg time: {data['avg_duration_ms'] / 1000:.1f}s",
        "",
        "products:",
    ]
    products = data["products"] or []
    if products:
        for product, count, avg_ms in products:
            lines.append(f"{product:<10} {count:>5}  {avg_ms / 1000:>5.1f}s")
    else:
        lines.append("нет данных")
    lines.append("")
    lines.append("cities:")
    cities = data["cities"] or []
    if cities:
        for city, count in cities:
            lines.append(f"{str(city)[:22]:<22} {count:>5}")
    else:
        lines.append("нет данных")
    lines.extend([
        "",
        "Кнопки admin-меню доступны под сообщением.",
        "/admin users — пользователи",
        "/admin find <id|@user|name> — найти",
        "/admin add <id|@user> — добавить админа",
        "/admin recent — последние запросы",
        "/admin report requests — CSV запросов",
        "/admin report users — CSV пользователей",
    ])
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def format_users(users: list[KnownUser]) -> str:
    lines = ["USERS"]
    if not users:
        lines.append("нет совпадений")
    for user in users:
        username = f"@{user.username}" if user.username else "-"
        name = " ".join(part for part in (user.first_name, user.last_name) if part) or "-"
        lines.append(f"{user.user_id} {username:<18} {name[:24]:<24} req={user.requests_count}")
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def format_recent_requests(limit: int = 10, db_path: str | Path | None = None) -> str:
    rows = recent_requests(limit=limit, db_path=db_path)
    lines = ["RECENT REQUESTS"]
    if not rows:
        lines.append("нет данных")
    for row in rows:
        duration = "-" if row["duration_ms"] is None else f"{int(row['duration_ms']) / 1000:.1f}s"
        city = (row["city"] or "-")[:18]
        user = row["username"] or row["user_id"] or "-"
        lines.append(f"{row['started_at'][5:16]} {row['product']:<9} {duration:>6} {row['status']:<7} {city:<18} {user}")
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def _csv_rows(query: str, db_path: str | Path | None = None) -> str:
    init_admin_db(db_path)
    output = StringIO()
    with _connect(db_path) as conn:
        rows = conn.execute(query).fetchall()
        writer = csv.writer(output)
        if rows:
            writer.writerow(rows[0].keys())
            for row in rows:
                writer.writerow([row[key] for key in row.keys()])
        else:
            writer.writerow([])
    return output.getvalue()


def export_requests_csv(days: int = 30, db_path: str | Path | None = None) -> str:
    return _csv_rows(
        f"""
        SELECT id, started_at, finished_at, duration_ms, status, product, user_id, username, city,
               request_text, lead_from, lead_to, run_date, run_cycle, error
        FROM requests
        WHERE started_at >= {_since_expr(days)}
        ORDER BY started_at DESC, id DESC
        """,
        db_path=db_path,
    )


def export_users_csv(db_path: str | Path | None = None) -> str:
    return _csv_rows(
        """
        SELECT user_id, username, first_name, last_name, is_bot, first_seen, last_seen,
               requests_count, last_city, last_product
        FROM users
        ORDER BY last_seen DESC
        """,
        db_path=db_path,
    )


def _looks_like_admin_response(text: str) -> bool:
    return (
        text.startswith("<pre>ADMIN")
        or text.startswith("<pre>USERS")
        or text.startswith("<pre>RECENT REQUESTS")
        or text.startswith("Неизвестная admin-команда")
        or text.startswith("Формат: /admin")
        or text.startswith("Пользователь не найден")
        or text.startswith("Администратор добавлен")
    )


def _install_admin_keyboard_patch() -> None:
    try:
        from telegram import Message
        from telegram_admin_ui import admin_keyboard
    except Exception:
        return

    original = getattr(Message, "reply_text", None)
    if original is None or getattr(original, "_gfs_admin_keyboard_patch", False):
        return

    async def reply_text_with_admin_keyboard(self, text, *args, **kwargs):
        if "reply_markup" not in kwargs and isinstance(text, str) and _looks_like_admin_response(text):
            kwargs["reply_markup"] = admin_keyboard()
        return await original(self, text, *args, **kwargs)

    reply_text_with_admin_keyboard._gfs_admin_keyboard_patch = True  # type: ignore[attr-defined]
    Message.reply_text = reply_text_with_admin_keyboard


_install_admin_keyboard_patch()
