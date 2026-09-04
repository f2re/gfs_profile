from __future__ import annotations

"""Persistent messenger-neutral successful product scenarios."""

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

DEFAULT_DB_PATH = Path(os.getenv("MESSENGER_PREFERENCES_DB", ".cache_gfs/messenger_preferences.sqlite3"))
MAX_RECIPES_PER_USER = 24
MAX_PINNED_RECIPES = 8
_TRANSIENT = {"run", "cycle", "run_date", "run_cycle", "message_id", "status_message_id", "callback_id", "candidates", "step", "_schedule_setup"}
_LOCK = RLock()
_INITIALIZED: set[Path] = set()


class RecipeLimitError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UserRecipe:
    recipe_id: int
    platform: str
    user_id: str
    product: str
    params: dict[str, Any]
    point: dict[str, Any] | None
    signature: str
    pinned: bool
    pinned_at: str | None
    success_count: int
    created_at: str
    last_success_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _point(value: Mapping[str, Any] | Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        lat, lon = float(value["lat"]), float(value["lon"])
        label, source = value.get("label"), value.get("source", "manual")
    else:
        lat, lon = float(value.lat), float(value.lon)
        label, source = getattr(value, "label", None), getattr(value, "source", "manual")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Координаты сценария вне допустимого диапазона")
    label = " ".join(str(label or "").split()) or f"{lat:.4f}, {lon:.4f}"
    return {"lat": lat, "lon": lon, "label": label[:200], "source": str(source or "manual")[:40]}


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items() if str(k) not in _TRANSIENT}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "lat") and hasattr(value, "lon"):
        return _point(value)
    return str(value)


def _sig_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if "lat" in value and "lon" in value:
            return {"lat": round(float(value["lat"]), 4), "lon": round(float(value["lon"]), 4)}
        return {str(k): _sig_value(v) for k, v in value.items() if str(k) not in _TRANSIENT}
    if isinstance(value, (list, tuple)):
        return [_sig_value(v) for v in value]
    return _clean(value)


def _signature(product: str, params: Mapping[str, Any], point: Mapping[str, Any] | None) -> str:
    payload = {"product": product, "params": _sig_value(params), "point": _sig_value(point) if point else None}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class UserRecipeStore:
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
                    conn.executescript("""
                    CREATE TABLE IF NOT EXISTS messenger_user_recipes(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform TEXT NOT NULL, user_id TEXT NOT NULL, product TEXT NOT NULL,
                        signature TEXT NOT NULL, params_json TEXT NOT NULL, point_json TEXT,
                        pinned INTEGER NOT NULL DEFAULT 0, pinned_at TEXT,
                        success_count INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL, last_success_at TEXT NOT NULL,
                        UNIQUE(platform,user_id,signature));
                    CREATE INDEX IF NOT EXISTS idx_messenger_recipes_quick
                      ON messenger_user_recipes(platform,user_id,pinned DESC,pinned_at DESC,success_count DESC,last_success_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_messenger_recipes_product
                      ON messenger_user_recipes(platform,user_id,product);
                    """)
            finally:
                conn.close()
            _INITIALIZED.add(path)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> UserRecipe | None:
        if row is None:
            return None
        params = json.loads(row["params_json"])
        point = json.loads(row["point_json"]) if row["point_json"] else None
        return UserRecipe(int(row["id"]), str(row["platform"]), str(row["user_id"]), str(row["product"]),
                          dict(params), dict(point) if isinstance(point, dict) else None, str(row["signature"]),
                          bool(row["pinned"]), str(row["pinned_at"]) if row["pinned_at"] else None,
                          int(row["success_count"]), str(row["created_at"]), str(row["last_success_at"]))

    def _one(self, sql: str, args: tuple[Any, ...]) -> UserRecipe | None:
        self.init(); conn = self._connect()
        try: return self._row(conn.execute(sql, args).fetchone())
        finally: conn.close()

    def record_success(self, platform: str, user_id: str | int, product: str,
                       params: Mapping[str, Any] | None, point: Mapping[str, Any] | Any | None) -> UserRecipe:
        platform, user_id, product = str(platform).lower().strip(), str(user_id).strip(), str(product).lower().strip()
        if not platform or not user_id or not product:
            raise ValueError("platform, user_id and product are required")
        clean_params = _clean(dict(params or {})); clean_params = clean_params if isinstance(clean_params, dict) else {}
        clean_point = _point(point); signature = _signature(product, clean_params, clean_point); now = _now()
        self.init(); conn = self._connect()
        try:
            with conn:
                conn.execute("""INSERT INTO messenger_user_recipes(platform,user_id,product,signature,params_json,point_json,pinned,pinned_at,success_count,created_at,last_success_at)
                VALUES(?,?,?,?,?,?,0,NULL,1,?,?) ON CONFLICT(platform,user_id,signature) DO UPDATE SET
                params_json=excluded.params_json,point_json=excluded.point_json,success_count=messenger_user_recipes.success_count+1,last_success_at=excluded.last_success_at""",
                (platform,user_id,product,signature,json.dumps(clean_params,ensure_ascii=False,sort_keys=True),
                 json.dumps(clean_point,ensure_ascii=False,sort_keys=True) if clean_point else None,now,now))
                conn.execute("""DELETE FROM messenger_user_recipes WHERE id IN(
                SELECT id FROM messenger_user_recipes WHERE platform=? AND user_id=? AND pinned=0
                ORDER BY last_success_at DESC LIMIT -1 OFFSET ?)""", (platform,user_id,MAX_RECIPES_PER_USER))
                row = conn.execute("SELECT * FROM messenger_user_recipes WHERE platform=? AND user_id=? AND signature=?", (platform,user_id,signature)).fetchone()
        finally: conn.close()
        recipe = self._row(row)
        if recipe is None: raise RuntimeError("recipe was not stored")
        return recipe

    def get(self, platform: str, user_id: str | int, recipe_id: int) -> UserRecipe | None:
        return self._one("SELECT * FROM messenger_user_recipes WHERE id=? AND platform=? AND user_id=?",
                         (int(recipe_id), str(platform).lower(), str(user_id)))

    def latest(self, platform: str, user_id: str | int) -> UserRecipe | None:
        return self._one("SELECT * FROM messenger_user_recipes WHERE platform=? AND user_id=? ORDER BY last_success_at DESC,id DESC LIMIT 1",
                         (str(platform).lower(), str(user_id)))

    def latest_for_product(self, platform: str, user_id: str | int, product: str) -> UserRecipe | None:
        return self._one("SELECT * FROM messenger_user_recipes WHERE platform=? AND user_id=? AND product=? ORDER BY last_success_at DESC,id DESC LIMIT 1",
                         (str(platform).lower(), str(user_id), str(product).lower()))

    def default_for_product(self, platform: str, user_id: str | int, product: str) -> UserRecipe | None:
        return self._one("SELECT * FROM messenger_user_recipes WHERE platform=? AND user_id=? AND product=? AND pinned=1 ORDER BY pinned_at DESC,last_success_at DESC,id DESC LIMIT 1",
                         (str(platform).lower(), str(user_id), str(product).lower()))

    def find_matching(self, platform: str, user_id: str | int, product: str,
                      params: Mapping[str, Any] | None, point: Mapping[str, Any] | Any | None) -> UserRecipe | None:
        clean_params = _clean(dict(params or {})); clean_params = clean_params if isinstance(clean_params, dict) else {}
        sig = _signature(str(product).lower(), clean_params, _point(point))
        return self._one("SELECT * FROM messenger_user_recipes WHERE platform=? AND user_id=? AND signature=?",
                         (str(platform).lower(), str(user_id), sig))

    def list(self, platform: str, user_id: str | int, *, limit: int = 20) -> list[UserRecipe]:
        self.init(); conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM messenger_user_recipes WHERE platform=? AND user_id=? ORDER BY pinned DESC,pinned_at DESC,last_success_at DESC,success_count DESC,id DESC LIMIT ?",
                                (str(platform).lower(), str(user_id), max(1,min(int(limit),100)))).fetchall()
        finally: conn.close()
        return [item for row in rows if (item := self._row(row))]

    def quick(self, platform: str, user_id: str | int, *, limit: int = 2) -> list[UserRecipe]:
        limit = max(1,min(int(limit),5)); items = self.list(platform,user_id,limit=MAX_RECIPES_PER_USER+MAX_PINNED_RECIPES)
        chosen: list[UserRecipe] = []; seen: set[int] = set()
        for item in items:
            if item.pinned and item.recipe_id not in seen:
                chosen.append(item); seen.add(item.recipe_id)
                if len(chosen) >= limit: return chosen
        if items:
            latest = max(items,key=lambda x:(x.last_success_at,x.recipe_id))
            if latest.recipe_id not in seen: chosen.append(latest); seen.add(latest.recipe_id)
        for item in sorted(items,key=lambda x:(x.success_count,x.last_success_at,x.recipe_id),reverse=True):
            if len(chosen) >= limit: break
            if item.recipe_id not in seen: chosen.append(item); seen.add(item.recipe_id)
        return chosen

    def set_pinned(self, platform: str, user_id: str | int, recipe_id: int, pinned: bool) -> UserRecipe | None:
        platform,user_id = str(platform).lower(),str(user_id); current = self.get(platform,user_id,recipe_id)
        if current is None: return None
        self.init(); conn = self._connect()
        try:
            with conn:
                if pinned and not current.pinned:
                    n = conn.execute("SELECT COUNT(*) n FROM messenger_user_recipes WHERE platform=? AND user_id=? AND pinned=1",(platform,user_id)).fetchone()["n"]
                    if int(n) >= MAX_PINNED_RECIPES: raise RecipeLimitError(f"Можно закрепить не более {MAX_PINNED_RECIPES} сценариев")
                conn.execute("UPDATE messenger_user_recipes SET pinned=?,pinned_at=? WHERE id=?",(1 if pinned else 0,_now() if pinned else None,int(recipe_id)))
                row = conn.execute("SELECT * FROM messenger_user_recipes WHERE id=?",(int(recipe_id),)).fetchone()
        finally: conn.close()
        return self._row(row)

    def toggle_pinned(self, platform: str, user_id: str | int, recipe_id: int) -> UserRecipe | None:
        current = self.get(platform,user_id,recipe_id)
        return None if current is None else self.set_pinned(platform,user_id,recipe_id,not current.pinned)

    def delete(self, platform: str, user_id: str | int, recipe_id: int) -> bool:
        self.init(); conn=self._connect()
        try:
            with conn: cur=conn.execute("DELETE FROM messenger_user_recipes WHERE id=? AND platform=? AND user_id=?",(int(recipe_id),str(platform).lower(),str(user_id)))
            return bool(cur.rowcount)
        finally: conn.close()

    def clear_product(self, platform: str, user_id: str | int, product: str) -> None:
        self.init(); conn=self._connect()
        try:
            with conn: conn.execute("DELETE FROM messenger_user_recipes WHERE platform=? AND user_id=? AND product=?",(str(platform).lower(),str(user_id),str(product).lower()))
        finally: conn.close()

    def clear_user(self, platform: str, user_id: str | int) -> None:
        self.init(); conn=self._connect()
        try:
            with conn: conn.execute("DELETE FROM messenger_user_recipes WHERE platform=? AND user_id=?",(str(platform).lower(),str(user_id)))
        finally: conn.close()
