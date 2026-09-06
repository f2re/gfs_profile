from __future__ import annotations

"""Messenger-neutral user settings for MAX/VK and future adapters.

Settings own no meteorological logic. They manage the same persistent SQLite
state used by saved recipes: active/recent locations plus recipe pin/delete.
All keys are scoped by ``platform + user_id``.
"""

import logging
from pathlib import Path
from typing import Any, Mapping

from geocode import GeoPoint

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, UiButton, UiKeyboard
from .route_router import RouteMessengerRouter
from .router import RouterDependencies
from .user_locations import MessengerLocation, MessengerLocationStore
from .user_recipes import UserRecipe, UserRecipeStore

LOG = logging.getLogger(__name__)
RECIPE_PAGE_SIZE = 4
PRODUCT_TITLES = {
    "profile": "Профиль",
    "aero": "Аэродиаграмма",
    "windgram": "Срок × уровень",
    "cloudgram": "Облака",
    "map": "Карта",
    "meteogram": "Метеограмма",
    "route": "Маршрут",
}


class SettingsRecipeStore(UserRecipeStore):
    """Recipe store that mirrors successful usage into common location history."""

    def __init__(self, path: str | Path | None = None, *, locations: MessengerLocationStore | None = None) -> None:
        super().__init__(path)
        self.locations = locations or MessengerLocationStore(self.path)

    def record_success(
        self,
        platform: str,
        user_id: str | int,
        product: str,
        params: Mapping[str, Any] | None,
        point: Mapping[str, Any] | Any | None,
    ) -> UserRecipe:
        recipe = super().record_success(platform, user_id, product, params, point)
        try:
            if recipe.product == "route":
                for key in ("origin", "destination"):
                    endpoint = recipe.params.get(key)
                    if isinstance(endpoint, Mapping):
                        self.locations.remember(recipe.platform, recipe.user_id, endpoint, activate=False)
            elif recipe.point is not None:
                self.locations.remember(recipe.platform, recipe.user_id, recipe.point, activate=True)
        except Exception:
            # A settings-history failure must never invalidate a successful
            # weather product or prevent the other platforms from operating.
            LOG.exception("Could not mirror successful recipe into location history")
        return recipe


def _geo_point(item: MessengerLocation) -> GeoPoint:
    return GeoPoint(item.lat, item.lon, item.label, item.source)


def _route_endpoints(recipe: UserRecipe) -> tuple[dict[str, Any], dict[str, Any]] | None:
    origin = recipe.params.get("origin")
    destination = recipe.params.get("destination")
    if isinstance(origin, dict) and isinstance(destination, dict):
        return origin, destination
    return None


def _recipe_label(recipe: UserRecipe) -> str:
    marker = "★" if recipe.pinned else "•"
    title = PRODUCT_TITLES.get(recipe.product, recipe.product)
    if recipe.product == "route":
        endpoints = _route_endpoints(recipe)
        if endpoints:
            return f"{marker} {title}: {endpoints[0].get('label', 'старт')} → {endpoints[1].get('label', 'финиш')}"[:58]
    point = recipe.point or {}
    return f"{marker} {title}: {point.get('label', 'точка')}"[:58]


class SettingsMessengerRouter(RouteMessengerRouter):
    def __init__(
        self,
        dependencies: RouterDependencies,
        *,
        locations: MessengerLocationStore | None = None,
        recipes: UserRecipeStore | None = None,
        **kwargs: Any,
    ) -> None:
        if locations is None:
            locations = MessengerLocationStore(recipes.path if recipes is not None else None)
        if recipes is None:
            recipes = SettingsRecipeStore(locations.path, locations=locations)
        super().__init__(dependencies, recipes=recipes, **kwargs)
        self.locations = locations

    @classmethod
    def default(cls, **kwargs: Any) -> "SettingsMessengerRouter":
        from geocode_choices import search_location_candidates

        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    def _sync_locations_from_recipes(self, event: NormalizedEvent) -> None:
        """One-way migration for recipes created before location-store support."""

        recipes = self.recipes.list(event.platform, event.user_id, limit=100)
        for recipe in sorted(recipes, key=lambda item: (item.last_success_at, item.recipe_id)):
            try:
                if recipe.product == "route":
                    endpoints = _route_endpoints(recipe)
                    if endpoints:
                        self.locations.ensure(
                            event.platform,
                            event.user_id,
                            endpoints[0],
                            activate=False,
                            used_at=recipe.last_success_at,
                        )
                        self.locations.ensure(
                            event.platform,
                            event.user_id,
                            endpoints[1],
                            activate=False,
                            used_at=recipe.last_success_at,
                        )
                elif recipe.point:
                    self.locations.ensure(
                        event.platform,
                        event.user_id,
                        recipe.point,
                        activate=False,
                        used_at=recipe.last_success_at,
                    )
            except Exception:
                LOG.exception("Could not migrate recipe location")

        if self.locations.active(event.platform, event.user_id) is None:
            point_recipes = [item for item in recipes if item.product != "route" and item.point]
            if point_recipes:
                latest = max(point_recipes, key=lambda item: (item.last_success_at, item.recipe_id))
                try:
                    self.locations.ensure(
                        event.platform,
                        event.user_id,
                        latest.point,
                        activate=True,
                        used_at=latest.last_success_at,
                    )
                except Exception:
                    LOG.exception("Could not bootstrap active location")

    def _settings_text(self, event: NormalizedEvent) -> str:
        self._sync_locations_from_recipes(event)
        active = self.locations.active(event.platform, event.user_id)
        recipes = self.recipes.list(event.platform, event.user_id, limit=100)
        pinned = sum(1 for item in recipes if item.pinned)
        point_line = (
            f"📍 Основная точка: {active.label} · {active.lat:.4f}, {active.lon:.4f}"
            if active
            else "📍 Основная точка: ещё не выбрана"
        )
        return (
            "⚙ Настройки\n"
            f"{point_line}\n"
            f"📌 Сохранённых сценариев: {len(recipes)} · закреплено: {pinned}\n\n"
            "Точки и сценарии хранятся отдельно для этой платформы и пользователя. "
            "Удаление настроек не удаляет расписания."
        )

    def _settings_keyboard(self) -> UiKeyboard:
        return UiKeyboard.from_rows(
            [
                [UiButton("📍 Мои точки", "callback", encode_callback("settings", "locations"))],
                [UiButton("📌 Сценарии", "callback", encode_callback("settings", "recipes", 0))],
                [UiButton("🧹 Очистить точки", "callback", encode_callback("settings", "clearp"))],
                [UiButton("🗑 Удалить мои настройки", "callback", encode_callback("settings", "cleara"))],
                [UiButton("🏠 Главное меню", "callback", encode_callback("settings", "home"))],
            ]
        )

    async def _start(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        self._sync_locations_from_recipes(event)
        active = self.locations.active(event.platform, event.user_id)
        rows: list[list[UiButton]] = []
        for recipe in self.recipes.quick(event.platform, event.user_id, limit=2):
            rows.append([UiButton("▶ " + _recipe_label(recipe)[2:], "callback", encode_callback("recipe", "run", recipe.recipe_id))])
        rows.extend(
            [
                [UiButton("📈 Профиль", "callback", encode_callback("product", "open", "profile")), UiButton("🧾 Аэродиаграмма", "callback", encode_callback("product", "open", "aero"))],
                [UiButton("🟦 Срок × уровень", "callback", encode_callback("product", "open", "windgram")), UiButton("☁ Облака", "callback", encode_callback("product", "open", "cloudgram"))],
                [UiButton("🗺 Карта", "callback", encode_callback("product", "open", "map")), UiButton("📊 Метеограмма", "callback", encode_callback("product", "open", "meteogram"))],
                [UiButton("✈ Маршрут", "callback", encode_callback("product", "open", "route")), UiButton("⚙ Настройки", "callback", encode_callback("settings", "open"))],
                [UiButton("📍 Геолокация", "request_location")],
            ]
        )
        point = f"\n📍 Основная точка: {active.label}" if active else ""
        await gateway.send_text(
            event.chat_id,
            "🌦 GFS 0.25 — модельный прогноз"
            f"{point}\n"
            "Профиль, аэродиаграмма, срок × уровень, облака, карта, метеограмма и маршрут.\n"
            "GFS — модель, не наблюдение и не радиозонд.",
            keyboard=UiKeyboard.from_rows(rows),
        )

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        if command == "settings":
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            await gateway.send_text(event.chat_id, self._settings_text(event), keyboard=self._settings_keyboard())
            return
        await super()._text(event, gateway)

    async def _callback(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        try:
            data = decode_callback(event.callback_payload or "")
        except CallbackCodecError:
            await super()._callback(event, gateway)
            return
        if data.scope != "settings":
            await super()._callback(event, gateway)
            return

        await gateway.answer_callback(event)
        action = data.action
        if action in {"open", "back"}:
            await gateway.send_text(event.chat_id, self._settings_text(event), keyboard=self._settings_keyboard())
            return
        if action == "home":
            await self._start(event, gateway)
            return
        if action == "locations":
            await self._show_locations(event, gateway)
            return
        if action == "loc":
            try:
                location_id = int(data.value or "")
            except ValueError:
                await gateway.send_text(event.chat_id, "Некорректная точка.")
                return
            item = self.locations.set_active(event.platform, event.user_id, location_id)
            if item is None:
                await gateway.send_text(event.chat_id, "Точка больше недоступна.")
                return
            await self._show_locations(event, gateway, prefix=f"✓ Основная точка: {item.label}\n\n")
            return
        if action == "recipes":
            try:
                page = max(0, int(data.value or "0"))
            except ValueError:
                page = 0
            await self._show_recipes(event, gateway, page)
            return
        if action == "delq":
            try:
                recipe_id = int(data.value or "")
            except ValueError:
                await gateway.send_text(event.chat_id, "Некорректный сценарий.")
                return
            recipe = self.recipes.get(event.platform, event.user_id, recipe_id)
            if recipe is None:
                await gateway.send_text(event.chat_id, "Сценарий уже удалён.")
                return
            await gateway.send_text(
                event.chat_id,
                f"Удалить сценарий «{_recipe_label(recipe)[2:]}»?",
                keyboard=UiKeyboard.from_rows([
                    [UiButton("Удалить", "callback", encode_callback("settings", "del", recipe_id))],
                    [UiButton("Отмена", "callback", encode_callback("settings", "recipes", 0))],
                ]),
            )
            return
        if action == "del":
            try:
                recipe_id = int(data.value or "")
            except ValueError:
                return
            self.recipes.delete(event.platform, event.user_id, recipe_id)
            await self._show_recipes(event, gateway, 0, prefix="Сценарий удалён.\n\n")
            return
        if action == "clearp":
            await gateway.send_text(
                event.chat_id,
                "Удалить историю и основную точку? Сценарии и расписания останутся.",
                keyboard=UiKeyboard.from_rows([
                    [UiButton("Удалить точки", "callback", encode_callback("settings", "clearpy"))],
                    [UiButton("Отмена", "callback", encode_callback("settings", "back"))],
                ]),
            )
            return
        if action == "clearpy":
            self.locations.clear(event.platform, event.user_id)
            await gateway.send_text(event.chat_id, "История точек удалена.\n\n" + self._settings_text(event), keyboard=self._settings_keyboard())
            return
        if action == "cleara":
            await gateway.send_text(
                event.chat_id,
                "Удалить все точки и сохранённые сценарии этой платформы? Расписания удаляются отдельно.",
                keyboard=UiKeyboard.from_rows([
                    [UiButton("Удалить мои настройки", "callback", encode_callback("settings", "clearay"))],
                    [UiButton("Отмена", "callback", encode_callback("settings", "back"))],
                ]),
            )
            return
        if action == "clearay":
            self.locations.clear(event.platform, event.user_id)
            self.recipes.clear_user(event.platform, event.user_id)
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            await gateway.send_text(event.chat_id, "Персональные точки и сценарии удалены. Расписания не изменены.", keyboard=self._settings_keyboard())
            return
        await gateway.send_text(event.chat_id, "Кнопка настроек устарела. Откройте /settings.")

    async def _show_locations(self, event: NormalizedEvent, gateway: MessengerGateway, prefix: str = "") -> None:
        self._sync_locations_from_recipes(event)
        items = self.locations.recent(event.platform, event.user_id, limit=10)
        if not items:
            await gateway.send_text(
                event.chat_id,
                prefix + "📍 Сохранённых точек пока нет.",
                keyboard=UiKeyboard.from_rows([[UiButton("← Настройки", "callback", encode_callback("settings", "back"))]]),
            )
            return
        rows = [
            [UiButton(("✓ " if item.active else "") + item.label[:54], "callback", encode_callback("settings", "loc", item.location_id))]
            for item in items
        ]
        rows.append([UiButton("← Настройки", "callback", encode_callback("settings", "back"))])
        await gateway.send_text(event.chat_id, prefix + "📍 Выберите основную точку:", keyboard=UiKeyboard.from_rows(rows))

    async def _show_recipes(self, event: NormalizedEvent, gateway: MessengerGateway, page: int, prefix: str = "") -> None:
        items = self.recipes.list(event.platform, event.user_id, limit=100)
        total_pages = max(1, (len(items) + RECIPE_PAGE_SIZE - 1) // RECIPE_PAGE_SIZE)
        page = min(max(0, page), total_pages - 1)
        visible = items[page * RECIPE_PAGE_SIZE : (page + 1) * RECIPE_PAGE_SIZE]
        rows: list[list[UiButton]] = []
        for recipe in visible:
            rows.append([UiButton("▶ " + _recipe_label(recipe)[2:], "callback", encode_callback("recipe", "run", recipe.recipe_id))])
            rows.append([
                UiButton("★ Открепить" if recipe.pinned else "⭐ Закрепить", "callback", encode_callback("recipe", "toggle", recipe.recipe_id)),
                UiButton("🗑", "callback", encode_callback("settings", "delq", recipe.recipe_id)),
            ])
        nav: list[UiButton] = []
        if page > 0:
            nav.append(UiButton("‹", "callback", encode_callback("settings", "recipes", page - 1)))
        if total_pages > 1:
            nav.append(UiButton(f"{page + 1}/{total_pages}", "callback", encode_callback("settings", "recipes", page)))
        if page + 1 < total_pages:
            nav.append(UiButton("›", "callback", encode_callback("settings", "recipes", page + 1)))
        if nav:
            rows.append(nav)
        rows.append([UiButton("← Настройки", "callback", encode_callback("settings", "back"))])
        text = prefix + ("📌 Сохранённые сценарии:" if items else "📌 Сохранённых сценариев пока нет.")
        await gateway.send_text(event.chat_id, text, keyboard=UiKeyboard.from_rows(rows))
