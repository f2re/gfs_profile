from __future__ import annotations

"""Common MAX/VK route-profile flow.

Every helper is route-prefixed so this vertical slice cannot override parent
meteogram/map helpers by accident.
"""

import asyncio
from threading import Lock
from typing import Any, Callable

from geocode import GeoPoint

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .meteogram_router import MeteogramMessengerRouter
from .profile_service import cleanup_product_result
from .route_service import (
    DEFAULT_ROUTE_PARAMS,
    ParsedRouteInput,
    ROUTE_LEADS,
    ROUTE_MODES,
    ROUTE_SPEEDS,
    ROUTE_SPATIAL_STEPS_KM,
    build_route_product_result,
    normalize_route_params,
    parse_route_input,
    route_plan,
    route_recipe_params,
)
from .router import RouterDependencies, _command_args
from .state import FlowState
from .user_recipes import RecipeLimitError, UserRecipe

START_TEXT = (
    "🌦 GFS 0.25 — модельный прогноз\n"
    "Доступны профиль, аэродиаграмма, срок × уровень, облака, карта, метеограмма и маршрут.\n"
    "GFS — модель, не наблюдение и не радиозонд."
)


def _route_params(value: dict[str, Any] | None = None) -> dict[str, Any]:
    return normalize_route_params(value)


def _unpack(value: dict[str, Any]) -> GeoPoint:
    return GeoPoint(float(value["lat"]), float(value["lon"]), str(value["label"]), str(value.get("source", "route")))


def _recipe_endpoints(recipe: UserRecipe) -> tuple[GeoPoint, GeoPoint] | None:
    params = recipe.params
    if not isinstance(params.get("origin"), dict) or not isinstance(params.get("destination"), dict):
        return None
    return _unpack(params["origin"]), _unpack(params["destination"])


def _route_card_text(origin: GeoPoint, destination: GeoPoint, params: dict[str, Any]) -> str:
    p = _route_params(params)
    distance, duration, specs, max_lead = route_plan(origin, destination, p)
    mode = "Профи" if p["mode"] == "pro" else "Простой"
    warning = "\n⚠️ Детальная сетка: расчёт может быть долгим." if len(specs) >= 60 else ""
    return (
        "✈️ Маршрутный профиль GFS\n"
        f"🧭 {origin.label} → {destination.label}\n"
        f"📏 {distance:.0f} км · расчётное время {duration:.1f} ч\n"
        f"🕒 вылет +{p['lead']} ч · прибытие около +{max_lead} ч\n"
        f"🚀 {p['speed']} км/ч · сетка {p['spatial_step']} км · {len(specs)} точек\n"
        f"📊 {mode} · профиль до 500 гПа{warning}\n\n"
        "Simple/Pro используют одинаковые исходные данные и риск; различается только представление."
    )


def _route_keyboard(params: dict[str, Any]) -> UiKeyboard:
    p = _route_params(params)
    lead_rows = [[UiButton(("✓ " if p["lead"] == value else "") + f"+{value}ч", "callback", encode_callback("route", "lead", value)) for value in ROUTE_LEADS[:3]],
                 [UiButton(("✓ " if p["lead"] == value else "") + f"+{value}ч", "callback", encode_callback("route", "lead", value)) for value in ROUTE_LEADS[3:]]]
    return UiKeyboard.from_rows([
        [UiButton("▶ Построить", "callback", encode_callback("route", "run"))],
        *lead_rows,
        [UiButton(("✓ " if p["speed"] == value else "") + f"{value}", "callback", encode_callback("route", "speed", value)) for value in ROUTE_SPEEDS],
        [UiButton(("✓ " if p["spatial_step"] == value else "") + f"{value} км", "callback", encode_callback("route", "grid", value)) for value in ROUTE_SPATIAL_STEPS_KM],
        [UiButton(("✓ " if p["mode"] == value else "") + ("Профи" if value == "pro" else "Простой"), "callback", encode_callback("route", "mode", value)) for value in ROUTE_MODES],
        [UiButton("↩ Другой маршрут", "callback", encode_callback("route", "restart")), UiButton("Отмена", "callback", encode_callback("route", "cancel"))],
    ])


def _route_progress(origin: GeoPoint, destination: GeoPoint, params: dict[str, Any], event: ProgressEvent) -> str:
    p = _route_params(params)
    header = f"⏳ Маршрутный профиль GFS\n🧭 {origin.label} → {destination.label}\n+{p['lead']} ч · {p['speed']} км/ч · сетка {p['spatial_step']} км\n"
    if event.stage in {"check", "run"}:
        body = "1/6 Проверяю опубликованный цикл…"
        if event.stage == "run" and event.data.get("run_date"):
            body = f"1/6 GFS {event.data['run_date']} {event.data.get('run_cycle')}Z"
    elif event.stage in {"route_step", "download_start", "download", "download_done", "cache"}:
        current = event.data.get("index") or event.current
        total = event.data.get("total") or event.total
        body = f"2/6 Рассчитываю точки {current}/{total}…" if current and total else "2/6 Загружаю модельные профили…"
    elif event.stage in {"parse_start", "parse_done"}:
        body = "3/6 Читаю вертикальные профили…"
    elif event.stage in {"plot_start", "plot_done"}:
        body = "5/6 Формирую PNG/CSV…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


def _quick_label(recipe: UserRecipe) -> str:
    marker = "★" if recipe.pinned else "▶"
    if recipe.product == "route":
        endpoints = _recipe_endpoints(recipe)
        if endpoints:
            return f"{marker} Маршрут · {endpoints[0].label} → {endpoints[1].label}"[:62]
    label = (recipe.point or {}).get("label", "точка")
    titles = {"profile": "Профиль", "aero": "Аэродиаграмма", "windgram": "Срок × уровень", "cloudgram": "Облака", "map": "Карта", "meteogram": "Метеограмма"}
    return f"{marker} {titles.get(recipe.product, recipe.product)} · {label}"[:62]


class RouteMessengerRouter(MeteogramMessengerRouter):
    def __init__(self, dependencies: RouterDependencies, *, route_builder: Callable[..., Any] = build_route_product_result, route_parser: Callable[[str], ParsedRouteInput] = parse_route_input, **kwargs: Any) -> None:
        super().__init__(dependencies, **kwargs)
        self.route_builder = route_builder
        self.route_parser = route_parser

    @classmethod
    def default(cls, **kwargs: Any) -> "RouteMessengerRouter":
        from geocode_choices import search_location_candidates
        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    def _default_route_recipe(self, event: NormalizedEvent) -> UserRecipe | None:
        return self.recipes.default_for_product(event.platform, event.user_id, "route") or self.recipes.latest_for_product(event.platform, event.user_id, "route")

    async def _start(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        rows: list[list[UiButton]] = []
        for recipe in self.recipes.quick(event.platform, event.user_id, limit=2):
            if recipe.product in {"profile", "aero", "windgram", "cloudgram", "map", "meteogram", "route"}:
                rows.append([UiButton(_quick_label(recipe), "callback", encode_callback("recipe", "run", recipe.recipe_id))])
        rows.extend([
            [UiButton("📈 Профиль", "callback", encode_callback("product", "open", "profile")), UiButton("🧾 Аэродиаграмма", "callback", encode_callback("product", "open", "aero"))],
            [UiButton("🟦 Срок × уровень", "callback", encode_callback("product", "open", "windgram")), UiButton("☁ Облака", "callback", encode_callback("product", "open", "cloudgram"))],
            [UiButton("🗺 Карта", "callback", encode_callback("product", "open", "map")), UiButton("📊 Метеограмма", "callback", encode_callback("product", "open", "meteogram"))],
            [UiButton("✈ Маршрут", "callback", encode_callback("product", "open", "route")), UiButton("📍 Геолокация", "request_location")],
        ])
        await gateway.send_text(event.chat_id, START_TEXT, keyboard=UiKeyboard.from_rows(rows))

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if not command and text and state is not None and state.product == "route":
            if state.step == "await_route":
                await self._resolve_route_text(event, gateway, text, state.params)
            elif state.step == "await_destination" and state.point is not None:
                await self._resolve_route_destination(event, gateway, state.point, text, state.params)
            else:
                await gateway.send_text(event.chat_id, "Продолжите маршрут кнопками или отправьте /route заново.")
            return
        if command == "route":
            args = _command_args(text)
            if not args:
                recipe = self._default_route_recipe(event)
                if recipe:
                    await self._send_route_recipe(event, gateway, recipe)
                else:
                    await self._ask_route(event, gateway, dict(DEFAULT_ROUTE_PARAMS))
            else:
                await self._resolve_route_direct(event, gateway, args)
            return
        await super()._text(event, gateway)

    async def _location(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "route":
            await super()._location(event, gateway)
            return
        if event.location is None:
            await gateway.send_text(event.chat_id, "В сообщении нет координат геолокации.")
            return
        origin = GeoPoint(float(event.location.lat), float(event.location.lon), f"геолокация {event.location.lat:.4f}, {event.location.lon:.4f}", event.platform)
        state.step = "await_destination"
        state.point = origin
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await gateway.send_text(event.chat_id, "Точка старта принята. Теперь укажите город или координаты назначения.")

    async def _callback(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        try:
            data = decode_callback(event.callback_payload or "")
        except CallbackCodecError:
            await super()._callback(event, gateway)
            return
        if data.scope == "recipe":
            try: recipe_id = int(data.value or "")
            except ValueError:
                await super()._callback(event, gateway); return
            recipe = self.recipes.get(event.platform, event.user_id, recipe_id)
            if recipe is None or recipe.product != "route":
                await super()._callback(event, gateway); return
            await gateway.answer_callback(event)
            endpoints = _recipe_endpoints(recipe)
            if endpoints is None:
                await gateway.send_text(event.chat_id, "Маршрут в recipe повреждён. Создайте заново."); return
            if data.action == "run":
                await self._run_route(event, gateway, endpoints[0], endpoints[1], _route_params(recipe.params), None); return
            if data.action == "toggle":
                try: updated = self.recipes.toggle_pinned(event.platform, event.user_id, recipe_id)
                except RecipeLimitError as exc:
                    await gateway.send_text(event.chat_id, str(exc)); return
                if updated: await self._send_route_recipe(event, gateway, updated)
                return
            if data.action == "change":
                await self._send_route_card(event, gateway, endpoints[0], endpoints[1], _route_params(recipe.params)); return
        if data.scope == "product" and data.action == "open" and data.value == "route":
            await gateway.answer_callback(event)
            recipe = self._default_route_recipe(event)
            if recipe: await self._send_route_recipe(event, gateway, recipe)
            else: await self._ask_route(event, gateway, dict(DEFAULT_ROUTE_PARAMS))
            return
        if data.scope != "route":
            await super()._callback(event, gateway); return
        await gateway.answer_callback(event)
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "route" or not isinstance(state.params.get("origin"), dict) or not isinstance(state.params.get("destination"), dict):
            await gateway.send_text(event.chat_id, "Сценарий маршрута устарел. Запустите /route."); return
        origin = _unpack(state.params["origin"]); destination = _unpack(state.params["destination"]); p = _route_params(state.params)
        try:
            if data.action == "lead": p["lead"] = int(data.value)
            elif data.action == "speed": p["speed"] = int(data.value)
            elif data.action == "grid": p["spatial_step"] = int(data.value)
            elif data.action == "mode": p["mode"] = str(data.value)
            elif data.action == "run": await self._run_route(event, gateway, origin, destination, p, None); return
            elif data.action == "restart": await self._ask_route(event, gateway, p); return
            elif data.action == "cancel": self.sessions.clear(event.platform, event.user_id, event.chat_id); await gateway.send_text(event.chat_id, "Маршрут отменён."); return
            else: return
            p = _route_params(p)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Параметр маршрута недоступен: {exc}"); return
        await self._send_route_card(event, gateway, origin, destination, p)

    async def _ask_route(self, event: NormalizedEvent, gateway: MessengerGateway, params: dict[str, Any]) -> None:
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product="route", step="await_route", params=_route_params(params)))
        await gateway.send_text(event.chat_id, "✈ Маршрут GFS. Введите `Москва -> Санкт-Петербург` или отправьте геолокацию как точку старта.", keyboard=UiKeyboard.from_rows([[UiButton("📍 Старт из геолокации", "request_location")]]))

    async def _geocode_one(self, query: str) -> GeoPoint:
        candidates = await asyncio.to_thread(self.deps.geocode, query, 1)
        if not candidates:
            raise ValueError(f"Точка не найдена: {query}")
        return candidates[0]

    async def _resolve_route_text(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str, params: dict[str, Any]) -> None:
        try:
            parsed = self.route_parser(raw)
            origin, destination = await asyncio.gather(self._geocode_one(parsed.origin_query), self._geocode_one(parsed.destination_query))
            p = _route_params({"lead": parsed.departure_lead, "speed": parsed.speed_kmh, "mode": parsed.mode, "spatial_step": parsed.spatial_step_km})
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка маршрута: {exc}"); return
        await self._send_route_card(event, gateway, origin, destination, p)

    async def _resolve_route_destination(self, event: NormalizedEvent, gateway: MessengerGateway, origin: GeoPoint, raw: str, params: dict[str, Any]) -> None:
        try: destination = await self._geocode_one(raw)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Точка назначения не найдена: {exc}"); return
        await self._send_route_card(event, gateway, origin, destination, _route_params(params))

    async def _resolve_route_direct(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str) -> None:
        try:
            parsed = self.route_parser(raw)
            origin, destination = await asyncio.gather(self._geocode_one(parsed.origin_query), self._geocode_one(parsed.destination_query))
            p = _route_params({"lead": parsed.departure_lead, "speed": parsed.speed_kmh, "mode": parsed.mode, "spatial_step": parsed.spatial_step_km})
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка маршрута: {exc}"); return
        # Explicit direct command preserves its requested run.
        await self._run_route(event, gateway, origin, destination, p, parsed.run)

    async def _send_route_card(self, event: NormalizedEvent, gateway: MessengerGateway, origin: GeoPoint, destination: GeoPoint, params: dict[str, Any]) -> None:
        p = _route_params(params)
        state_params = route_recipe_params(origin, destination, p)
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product="route", step="params", params=state_params))
        await gateway.send_text(event.chat_id, _route_card_text(origin, destination, p), keyboard=_route_keyboard(p))

    async def _send_route_recipe(self, event: NormalizedEvent, gateway: MessengerGateway, recipe: UserRecipe) -> None:
        endpoints = _recipe_endpoints(recipe)
        if endpoints is None: return
        pin = "★ Открепить" if recipe.pinned else "⭐ Закрепить"
        await gateway.send_text(event.chat_id, _route_card_text(endpoints[0], endpoints[1], recipe.params), keyboard=UiKeyboard.from_rows([
            [UiButton("▶ Построить", "callback", encode_callback("recipe", "run", recipe.recipe_id))],
            [UiButton(pin, "callback", encode_callback("recipe", "toggle", recipe.recipe_id)), UiButton("⚙ Изменить", "callback", encode_callback("recipe", "change", recipe.recipe_id))],
        ]))

    async def _run_route(self, event: NormalizedEvent, gateway: MessengerGateway, origin: GeoPoint, destination: GeoPoint, params: dict[str, Any], run: Any | None) -> bool:
        p = _route_params(params); self.sessions.clear(event.platform, event.user_id, event.chat_id)
        status = await gateway.send_text(event.chat_id, _route_progress(origin, destination, p, ProgressEvent("check", "")))
        snapshot = {"event": ProgressEvent("check", "")}; lock = Lock(); stop = asyncio.Event(); last = ""; result = None
        def progress(value: ProgressEvent) -> None:
            with lock: snapshot["event"] = value
        async def reporter() -> None:
            nonlocal last
            while not stop.is_set():
                with lock: value = snapshot["event"]
                text = _route_progress(origin, destination, p, value)
                if text != last:
                    try: await gateway.edit_text(event.chat_id, status.message_id, text); last = text
                    except Exception: pass
                try: await asyncio.wait_for(stop.wait(), timeout=self.progress_interval_seconds)
                except asyncio.TimeoutError: pass
        task = asyncio.create_task(reporter())
        try:
            async with self.gfs_semaphore:
                result = await asyncio.to_thread(self.route_builder, origin, destination, p["lead"], p["speed"], p["mode"], p["spatial_step"], run, progress_callback=progress)
            stop.set(); await task
            await gateway.edit_text(event.chat_id, status.message_id, result.summary)
            for attachment in result.attachments:
                if attachment.kind == "image": await gateway.send_image(event.chat_id, attachment.path, caption=attachment.caption)
                else: await gateway.send_file(event.chat_id, attachment.path, caption=attachment.caption, filename=attachment.filename)
            recipe_params = route_recipe_params(origin, destination, p)
            # Store origin as the recipe point only for indexing/display. Routers do
            # not promote it to any global active point.
            recipe = self.recipes.record_success(event.platform, event.user_id, "route", recipe_params, origin)
            await self._send_route_recipe(event, gateway, recipe)
            return True
        except Exception as exc:
            stop.set(); await task; await gateway.edit_text(event.chat_id, status.message_id, f"Ошибка маршрута: {exc}"); return False
        finally:
            stop.set()
            if not task.done(): await task
            if result is not None: cleanup_product_result(result)
