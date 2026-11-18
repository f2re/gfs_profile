from __future__ import annotations

"""Common MAX/VK router including the GFS composite map vertical slice."""

import asyncio
from threading import Lock
from typing import Any, Callable

from forecast_menu import UNVERIFIED_TIME_TEXT
from forecast_time import forecast_lead_label, forecast_range_lines

from geocode import GeoPoint

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .icing_router import IcingMessengerRouter
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .map_service import (
    DEFAULT_MAP_PARAMS,
    MAP_STEPS,
    MAP_TO_HOURS,
    ParsedMapInput,
    build_map_product_result,
    normalize_map_params,
    parse_map_input,
)
from .profile_service import cleanup_product_result
from .router import RouterDependencies, _command_args, _short_label, menu_keyboard
from .state import FlowState
from .user_recipes import RecipeLimitError, UserRecipe

MAP_MODES = (("gif", "Анимация"), ("single", "Одна карта"), ("series", "Серия PNG"))
MAP_BASEMAP_CHOICES = (("places", "Города"), ("basic", "База"), ("roads", "Дороги"), ("water", "Вода"))
MAP_RADII = (50, 100)


def _point(recipe: UserRecipe) -> GeoPoint | None:
    value = recipe.point
    if not value:
        return None
    return GeoPoint(float(value["lat"]), float(value["lon"]), str(value.get("label", "сохранённая точка")), str(value.get("source", recipe.platform)))


def _params(value: dict[str, Any] | None = None) -> dict[str, Any]:
    return normalize_map_params(value)


def _parsed_params(parsed: ParsedMapInput) -> dict[str, Any]:
    return normalize_map_params({
        "from": parsed.lead_from,
        "to": parsed.lead_to,
        "step": parsed.step,
        "mode": parsed.mode,
        "radius": parsed.radius_km,
        "basemap": parsed.basemap,
    })


def _progress_text(point: Any, params: dict[str, Any], event: ProgressEvent) -> str:
    mode = {"gif": "Анимация", "single": "Одна карта", "series": "Серия PNG"}[params["mode"]]
    period = f"+{params['from']} ч" if params["mode"] == "single" else f"+{params['from']}…+{params['to']} ч · шаг {params['step']} ч"
    data = dict(event.data)
    header = f"⏳ {mode} GFS\n📍 {getattr(point, 'label', '')}\n{period}\n"
    if event.stage in {"check", "run"}:
        body = "1/6 Проверяю опубликованный цикл…"
        if event.stage == "run" and data.get("run_date"):
            body = f"1/6 GFS {data['run_date']} {data.get('run_cycle')}Z"
    elif event.stage in {"map_step", "download_start", "download", "download_done", "cache"}:
        current = data.get("index") or event.current
        total = data.get("total") or event.total
        lead = data.get("lead_hour")
        body = f"2/6 Загружаю поля {current}/{total} · +{lead} ч" if current and total and lead is not None else "2/6 Загружаю модельные поля…"
    elif event.stage in {"parse_start", "parse_done", "map_done"}:
        body = "3/6 Считаю слои карты…"
    elif event.stage in {"plot_start", "map_series_frame", "map_animation_start", "map_animation_frame", "map_animation_done"}:
        body = event.message or "4/6 Формирую изображение…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


def _card_text(point: Any, params: dict[str, Any], run=None) -> str:
    mode = {"gif": "Анимация", "single": "Одна карта", "series": "Серия PNG"}[params["mode"]]
    period = f"+{params['from']} ч" if params["mode"] == "single" else f"+{params['from']}…+{params['to']} ч · шаг {params['step']} ч"
    timing = forecast_range_lines(run, params["from"], params["to"]) if run is not None else UNVERIFIED_TIME_TEXT
    return (
        "🗺 Композитная карта GFS\n"
        f"📍 {getattr(point, 'label', 'точка')}\n"
        f"Режим: {mode}\nПериод: {period}\n{timing}\n"
        f"Радиус: {int(params['radius'])} км\nПодложка: {dict(MAP_BASEMAP_CHOICES).get(params['basemap'], params['basemap'])}\n\n"
        "Слои: осадки, облачность, гроза, ветер 500 гПа, явления и видимость."
    )


def _keyboard(params: dict[str, Any], run=None) -> UiKeyboard:
    mode = params["mode"]
    lead_to = params["from"] if mode == "single" else params["to"]
    time_buttons = [UiButton(("✓ " if int(lead_to) == value else "") +
                            (forecast_lead_label(run, value) if run is not None else f"+{value}"),
                            "callback", encode_callback("map", "to", value)) for value in MAP_TO_HOURS]
    return menu_keyboard([
        [UiButton("▶ Построить", "callback", encode_callback("map", "run"))],
        [UiButton(("✓ " if mode == key else "") + label, "callback", encode_callback("map", "mode", key)) for key, label in MAP_MODES],
        *[time_buttons[index:index + 2] for index in range(0, len(time_buttons), 2)],
        [UiButton(("✓ " if int(params["step"]) == value else "") + f"шаг {value}ч", "callback", encode_callback("map", "step", value)) for value in MAP_STEPS],
        [UiButton(("✓ " if int(params["radius"]) == value else "") + f"{value} км", "callback", encode_callback("map", "radius", value)) for value in MAP_RADII],
        [UiButton(("✓ " if params["basemap"] == key else "") + label, "callback", encode_callback("map", "base", key)) for key, label in MAP_BASEMAP_CHOICES],
        [UiButton("📍 Другая точка", "callback", encode_callback("map", "point")), UiButton("Отмена", "callback", encode_callback("map", "cancel"))],
        [UiButton("❄ Обледенение по району", "callback", encode_callback("product", "open", "icing"))],
    ])


class MapMessengerRouter(IcingMessengerRouter):

    home_products = IcingMessengerRouter.home_products + ("map",)

    def __init__(self, dependencies: RouterDependencies, *, map_builder: Callable[..., Any] = build_map_product_result, map_parser: Callable[[str], ParsedMapInput] = parse_map_input, **kwargs: Any) -> None:
        super().__init__(dependencies, **kwargs)
        self.map_builder = map_builder
        self.map_parser = map_parser

    @classmethod
    def default(cls, **kwargs: Any) -> "MapMessengerRouter":
        from geocode_choices import search_location_candidates
        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    def _default_recipe(self, event: NormalizedEvent) -> UserRecipe | None:
        return self.recipes.default_for_product(event.platform, event.user_id, "map") or self.recipes.latest_for_product(event.platform, event.user_id, "map")


    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if not command and text and state is not None and state.product == "map":
            if state.step == "await_point":
                await self._resolve_point(event, gateway, text, state.params)
            else:
                await gateway.send_text(event.chat_id, "Продолжите настройку карты кнопками или отправьте /map заново.")
            return
        if command == "map":
            args = _command_args(text)
            if not args:
                recipe = self._default_recipe(event)
                if recipe is not None:
                    await self._send_recipe(event, gateway, recipe)
                else:
                    await self._ask_map_point(event, gateway, dict(DEFAULT_MAP_PARAMS))
            else:
                await self._resolve_direct(event, gateway, args)
            return
        await super()._text(event, gateway)

    async def _location(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "map":
            await super()._location(event, gateway)
            return
        if event.location is None:
            await gateway.send_text(event.chat_id, "В сообщении нет координат геолокации.")
            return
        point = GeoPoint(float(event.location.lat), float(event.location.lon), f"геолокация {event.location.lat:.4f}, {event.location.lon:.4f}", event.platform)
        await self._send_card(event, gateway, FlowState(product="map", step="params", point=point, params=_params(state.params)))

    async def _callback(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        try:
            data = decode_callback(event.callback_payload or "")
        except CallbackCodecError:
            await super()._callback(event, gateway)
            return

        if data.scope == "recipe":
            try:
                recipe_id = int(data.value or "")
            except ValueError:
                await super()._callback(event, gateway)
                return
            recipe = self.recipes.get(event.platform, event.user_id, recipe_id)
            if recipe is None or recipe.product != "map":
                await super()._callback(event, gateway)
                return
            await gateway.answer_callback(event)
            if data.action == "run":
                point = _point(recipe)
                if point is None:
                    await gateway.send_text(event.chat_id, "В сценарии потеряна точка. Создайте карту заново.")
                    return
                params = _params(recipe.params)
                run = await self._recipe_forecast_run(event, gateway, recipe, params, int(params["to"]))
                if run is not None:
                    await self._run(event, gateway, point, params, run)
                return
            if data.action == "toggle":
                try:
                    updated = self.recipes.toggle_pinned(event.platform, event.user_id, recipe_id)
                except RecipeLimitError as exc:
                    await gateway.send_text(event.chat_id, str(exc)); return
                if updated is not None:
                    await self._send_recipe(event, gateway, updated)
                return
            if data.action == "change":
                point = _point(recipe)
                if point is None:
                    await self._ask_map_point(event, gateway, _params(recipe.params)); return
                await self._send_card(event, gateway, FlowState(product="map", step="params", point=point, params=_params(recipe.params)))
                return

        if data.scope == "product" and data.action == "open" and data.value == "map":
            await gateway.answer_callback(event)
            recipe = self._default_recipe(event)
            if recipe is not None:
                await self._send_recipe(event, gateway, recipe)
            else:
                await self._ask_map_point(event, gateway, dict(DEFAULT_MAP_PARAMS))
            return
        if data.scope != "map":
            await super()._callback(event, gateway); return

        await gateway.answer_callback(event)
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "map":
            await gateway.send_text(event.chat_id, "Сценарий карты устарел. Начните заново: /map"); return
        if data.action == "place":
            try:
                point = state.candidates[int(data.value or "")]
            except (ValueError, IndexError):
                await gateway.send_text(event.chat_id, "Вариант точки устарел. Повторите /map."); return
            if state.params.get("_direct"):
                p = _params(state.params); p.pop("_direct", None)
                await self._run(event, gateway, point, p, state.pending_run)
            else:
                await self._send_card(event, gateway, FlowState(product="map", step="params", point=point, params=_params(state.params)))
            return
        if data.action in {"mode", "to", "step", "radius", "base"}:
            p = _params(state.params)
            try:
                if data.action == "mode":
                    value = str(data.value); assert value in {item[0] for item in MAP_MODES}
                    if value == "single":
                        lead = 24 if p["from"] == 0 else int(p["from"])
                        p.update({"mode": "single", "from": lead, "to": lead})
                    else:
                        p["mode"] = value
                        if p["to"] <= p["from"]:
                            p.update({"from": 0, "to": 48})
                elif data.action == "to":
                    value = int(data.value or ""); assert value in MAP_TO_HOURS
                    if p["mode"] == "single": p.update({"from": value, "to": value})
                    else: p["to"] = value
                elif data.action == "step":
                    value = int(data.value or ""); assert value in MAP_STEPS; p["step"] = value
                elif data.action == "radius":
                    value = int(data.value or ""); assert value in MAP_RADII; p["radius"] = value
                else:
                    value = str(data.value); assert value in {item[0] for item in MAP_BASEMAP_CHOICES}; p["basemap"] = value
                p = _params(p)
            except (AssertionError, TypeError, ValueError, Exception) as exc:
                await gateway.send_text(event.chat_id, f"Параметр карты недоступен: {exc}"); return
            state.params = {**p, **{key: value for key, value in state.params.items() if key.startswith("_forecast_")}}
            await self._send_card(event, gateway, state)
            return
        if data.action == "run":
            if state.point is None:
                await gateway.send_text(event.chat_id, "Точка потеряна. Начните /map заново."); return
            p = _params(state.params)
            run = await self._forecast_selection_run(event, gateway, state, int(p["to"]))
            if (run is None or self.sessions.get(event.platform, event.user_id, event.chat_id) is not state
                    or state.params.get("_forecast_lead", int(p["to"])) != int(p["to"])):
                return
            await self._run(event, gateway, state.point, p, run); return
        if data.action == "point":
            await self._ask_map_point(event, gateway, _params(state.params)); return
        if data.action == "cancel":
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            await gateway.send_text(event.chat_id, "Выбор отменён.", keyboard=menu_keyboard([])); return

    async def _ask_map_point(self, event: NormalizedEvent, gateway: MessengerGateway, params: dict[str, Any]) -> None:
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product="map", step="await_point", params=_params(params)))
        await gateway.send_text(event.chat_id, "Карта · GFS data\nУкажите город, координаты или геолокацию.", keyboard=menu_keyboard([[UiButton("Геолокация", "request_location")]]))

    async def _resolve_point(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str, params: dict[str, Any]) -> None:
        try:
            candidates = await asyncio.to_thread(self.deps.geocode, raw, 5)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка: {exc}"); return
        if not candidates:
            await gateway.send_text(event.chat_id, "Точка не найдена. Уточните город или координаты."); return
        if len(candidates) > 1:
            state = FlowState(product="map", step="choose_place", candidates=list(candidates[:5]), params={**_params(params), "_direct": False})
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [[UiButton(_short_label(point), "callback", encode_callback("map", "place", index))] for index, point in enumerate(state.candidates)]
            await gateway.send_text(event.chat_id, "Найдено несколько точек. Выберите нужную:", keyboard=menu_keyboard(rows)); return
        await self._send_card(event, gateway, FlowState(product="map", step="params", point=candidates[0], params=_params(params)))

    async def _resolve_direct(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str) -> None:
        try:
            parsed = self.map_parser(raw)
            candidates = await asyncio.to_thread(self.deps.geocode, parsed.location_query, 5)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка: {exc}"); return
        if not candidates:
            await gateway.send_text(event.chat_id, "Точка не найдена."); return
        p = _parsed_params(parsed)
        if len(candidates) > 1:
            state = FlowState(product="map", step="choose_place", candidates=list(candidates[:5]), pending_run=parsed.run, params={**p, "_direct": True})
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [[UiButton(_short_label(point), "callback", encode_callback("map", "place", index))] for index, point in enumerate(state.candidates)]
            await gateway.send_text(event.chat_id, "Найдено несколько точек. Выберите нужную:", keyboard=menu_keyboard(rows)); return
        await self._run(event, gateway, candidates[0], p, parsed.run)

    async def _send_card(self, event: NormalizedEvent, gateway: MessengerGateway, state: FlowState) -> None:
        timing_state = {key: value for key, value in state.params.items() if key.startswith("_forecast_")}
        state.product, state.step, state.params = "map", "params", {**_params(state.params), **timing_state}
        token = int(state.params.get("_forecast_view", 0)) + 1
        state.params["_forecast_view"] = token
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        check_text = f"Проверяю данные GFS для +{state.params['to']} ч…"
        if event.event_type.upper() == "CALLBACK" and event.message_id:
            status = await gateway.edit_text(event.chat_id, event.message_id, check_text)
        else:
            status = await gateway.send_text(event.chat_id, check_text)
        run = await self._forecast_menu_run(state, int(state.params["to"]))
        if (self.sessions.get(event.platform, event.user_id, event.chat_id) is not state
                or state.params.get("_forecast_view") != token):
            return
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await gateway.edit_text(event.chat_id, status.message_id, _card_text(state.point, state.params, run), keyboard=_keyboard(state.params, run))

    async def _send_recipe(self, event: NormalizedEvent, gateway: MessengerGateway, recipe: UserRecipe) -> None:
        point = recipe.point or {}; p = _params(recipe.params)
        state = self._forecast_recipe_state(recipe, p)
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        status = await gateway.send_text(event.chat_id, f"Проверяю данные GFS для +{p['to']} ч…")
        run = await self._forecast_menu_run(state, int(p["to"]))
        if self.sessions.get(event.platform, event.user_id, event.chat_id) is not state:
            return
        pin = "★ Открепить" if recipe.pinned else "⭐ Закрепить"
        await gateway.edit_text(event.chat_id, status.message_id, _card_text(SimplePoint(point), p, run), keyboard=menu_keyboard([
            [UiButton("▶ Построить", "callback", encode_callback("recipe", "run", recipe.recipe_id))],
            [UiButton(pin, "callback", encode_callback("recipe", "toggle", recipe.recipe_id)), UiButton("⚙ Изменить", "callback", encode_callback("recipe", "change", recipe.recipe_id))],
        ]))

    async def _run(self, event: NormalizedEvent, gateway: MessengerGateway, point: Any, params: dict[str, Any], run: Any | None) -> bool:
        p = _params(params)
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        status = await gateway.send_text(event.chat_id, _progress_text(point, p, ProgressEvent("check", "")))
        snapshot = {"event": ProgressEvent("check", "")}; lock = Lock(); stop = asyncio.Event(); last = ""; result = None
        def progress(value: ProgressEvent) -> None:
            with lock: snapshot["event"] = value
        async def reporter() -> None:
            nonlocal last
            while not stop.is_set():
                with lock: value = snapshot["event"]
                text = _progress_text(point, p, value)
                if text != last:
                    try: await gateway.edit_text(event.chat_id, status.message_id, text); last = text
                    except Exception: pass
                try: await asyncio.wait_for(stop.wait(), timeout=self.progress_interval_seconds)
                except asyncio.TimeoutError: pass
        task = asyncio.create_task(reporter())
        try:
            async with self.gfs_semaphore:
                result = await asyncio.to_thread(self.map_builder, point, p["from"], p["to"], p["step"], p["mode"], p["radius"], p["basemap"], run, progress_callback=progress)
            stop.set(); await task
            await gateway.edit_text(event.chat_id, status.message_id, result.summary)
            for attachment in result.attachments:
                if attachment.kind == "animation": await gateway.send_animation(event.chat_id, attachment.path, caption=attachment.caption)
                elif attachment.kind == "image": await gateway.send_image(event.chat_id, attachment.path, caption=attachment.caption)
                else: await gateway.send_file(event.chat_id, attachment.path, caption=attachment.caption, filename=attachment.filename)
            recipe = self.recipes.record_success(event.platform, event.user_id, "map", p, point)
            await self._send_recipe(event, gateway, recipe)
            return True
        except Exception as exc:
            stop.set(); await task
            await gateway.edit_text(event.chat_id, status.message_id, f"Ошибка карты: {exc}")
            return False
        finally:
            stop.set()
            if not task.done(): await task
            if result is not None: cleanup_product_result(result)


class SimplePoint:
    def __init__(self, value: dict[str, Any]) -> None:
        self.label = str(value.get("label", "точка"))
        self.lat = float(value.get("lat", 0.0))
        self.lon = float(value.get("lon", 0.0))
