from __future__ import annotations

"""Common MAX/VK router with profile + aero + windgram vertical slices."""

import asyncio
from threading import Lock
from typing import Any, Callable

from geocode import GeoPoint

from .aero_router import AeroMessengerRouter
from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .profile_service import cleanup_product_result
from .router import RouterDependencies, _command_args, _short_label
from .state import FlowState
from .user_recipes import RecipeLimitError, UserRecipe
from .windgram_service import (
    PARAM_NAMES,
    ParsedWindgramInput,
    build_windgram_product_result,
    parse_windgram_input,
)

WINDGRAM_PARAMS = (("wind", "Ветер"), ("temp", "Температура"), ("rh", "Влажность"))
WINDGRAM_TO_HOURS = (120, 240, 384)
WINDGRAM_STEPS = (3, 6, 12)
DEFAULT_WINDGRAM_PARAMS = {"from": 0, "to": 120, "step": 6, "top": 500, "param": "wind"}

START_TEXT = (
    "🌦 GFS 0.25 — модельный прогноз\n"
    "Укажите город, координаты или геолокацию. Быстрый запрос: Москва +24.\n\n"
    "Продукты: /profile, /aero, /windgram. Остальные подключаются к тому же общему service поэтапно.\n"
    "GFS — модель, не наблюдение и не радиозонд."
)


def _recipe_point(recipe: UserRecipe) -> GeoPoint | None:
    point = recipe.point
    if not point:
        return None
    return GeoPoint(
        float(point["lat"]),
        float(point["lon"]),
        str(point.get("label", "сохранённая точка")),
        str(point.get("source", recipe.platform)),
    )


def _windgram_params(value: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(DEFAULT_WINDGRAM_PARAMS)
    if value:
        for key in result:
            if key in value:
                result[key] = value[key]
    result["from"] = int(result["from"])
    result["to"] = int(result["to"])
    result["step"] = int(result["step"])
    result["top"] = int(result["top"])
    result["param"] = str(result["param"])
    return result


def _params_from_parsed(parsed: ParsedWindgramInput) -> dict[str, Any]:
    return {
        "from": int(parsed.lead_from),
        "to": int(parsed.lead_to),
        "step": int(parsed.step),
        "top": int(parsed.top_hpa),
        "param": str(parsed.param),
    }


def _quick_recipe_label(recipe: UserRecipe) -> str:
    point = recipe.point or {}
    marker = "★" if recipe.pinned else "▶"
    if recipe.product == "profile":
        return f"{marker} Профиль · {point.get('label', 'точка')} · +{int(recipe.params.get('lead', 24))} ч"[:62]
    if recipe.product == "aero":
        return f"{marker} Аэродиаграмма · {point.get('label', 'точка')} · +{int(recipe.params.get('lead', 24))} ч"[:62]
    params = _windgram_params(recipe.params)
    return (
        f"{marker} Срок × уровень · {point.get('label', 'точка')} · "
        f"+{params['from']}…+{params['to']}"
    )[:62]


def _windgram_progress_text(point: Any, params: dict[str, Any], event: ProgressEvent) -> str:
    data = dict(event.data)
    header = (
        "⏳ Срок × уровень GFS\n"
        f"📍 {getattr(point, 'label', '')}\n"
        f"+{params['from']}…+{params['to']} ч · шаг {params['step']} ч\n"
    )
    if event.stage in {"check", "run"}:
        body = "1/6 Проверяю опубликованный цикл…"
        if event.stage == "run" and data.get("run_date") and data.get("run_cycle"):
            body = f"1/6 GFS {data['run_date']} {data['run_cycle']}Z"
    elif event.stage == "grid":
        body = f"2/6 Узел GFS: {data.get('grid_lat')}, {data.get('grid_lon')}"
    elif event.stage == "windgram_step":
        current = data.get("index") or event.current
        total = data.get("total") or event.total
        lead = data.get("lead_hour")
        suffix = f" · +{lead} ч" if lead is not None else ""
        body = f"3/6 Загружаю сроки: {current}/{total}{suffix}" if current and total else "3/6 Загружаю сроки…"
    elif event.stage in {"download_start", "download", "download_done", "cache"}:
        body = "3/6 Загружаю модельные данные…"
    elif event.stage in {"parse_start", "parse_done", "done"}:
        body = "4/6 Читаю профили и формирую матрицу…"
    elif event.stage in {"plot_start", "plot_done"}:
        body = "5/6 Формирую PNG…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


def _params_text(point: Any, params: dict[str, Any]) -> str:
    return (
        "🟦 Срок × уровень GFS\n"
        f"📍 {getattr(point, 'label', 'точка')}\n"
        f"Параметр: {PARAM_NAMES.get(str(params['param']), params['param'])}\n"
        f"Период: +{params['from']}…+{params['to']} ч\n"
        f"Шаг: {params['step']} ч\n"
        f"Уровни: до {params['top']} гПа\n\n"
        "Цвет/число — выбранный параметр; стрелка — направление ветра."
    )


def _params_keyboard(params: dict[str, Any]) -> UiKeyboard:
    param = str(params["param"])
    lead_to = int(params["to"])
    step = int(params["step"])
    return UiKeyboard.from_rows(
        [
            [UiButton("▶ Построить", "callback", encode_callback("windgram", "run"))],
            [
                UiButton(("✓ " if param == key else "") + label, "callback", encode_callback("windgram", "param", key))
                for key, label in WINDGRAM_PARAMS
            ],
            [
                UiButton(("✓ " if lead_to == value else "") + f"до +{value}", "callback", encode_callback("windgram", "to", value))
                for value in WINDGRAM_TO_HOURS
            ],
            [
                UiButton(("✓ " if step == value else "") + f"шаг {value}ч", "callback", encode_callback("windgram", "step", value))
                for value in WINDGRAM_STEPS
            ],
            [
                UiButton("📍 Другая точка", "callback", encode_callback("windgram", "point")),
                UiButton("Отмена", "callback", encode_callback("windgram", "cancel")),
            ],
        ]
    )


class WindgramMessengerRouter(AeroMessengerRouter):
    """Messenger-neutral profile + aero + windgram UX for MAX and VK."""

    def __init__(
        self,
        dependencies: RouterDependencies,
        *,
        windgram_builder: Callable[..., Any] = build_windgram_product_result,
        windgram_parser: Callable[[str], ParsedWindgramInput] = parse_windgram_input,
        **kwargs: Any,
    ) -> None:
        super().__init__(dependencies, **kwargs)
        self.windgram_builder = windgram_builder
        self.windgram_parser = windgram_parser

    @classmethod
    def default(cls, **kwargs: Any) -> "WindgramMessengerRouter":
        from geocode_choices import search_location_candidates

        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    def _default_windgram_recipe(self, event: NormalizedEvent) -> UserRecipe | None:
        return self.recipes.default_for_product(event.platform, event.user_id, "windgram") or self.recipes.latest_for_product(
            event.platform,
            event.user_id,
            "windgram",
        )

    async def _start(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        rows: list[list[UiButton]] = []
        for recipe in self.recipes.quick(event.platform, event.user_id, limit=2):
            if recipe.product in {"profile", "aero", "windgram"}:
                rows.append(
                    [UiButton(_quick_recipe_label(recipe), "callback", encode_callback("recipe", "run", recipe.recipe_id))]
                )
        rows.extend(
            [
                [
                    UiButton("📈 Профиль", "callback", encode_callback("product", "open", "profile")),
                    UiButton("🧾 Аэродиаграмма", "callback", encode_callback("product", "open", "aero")),
                ],
                [UiButton("🟦 Срок × уровень", "callback", encode_callback("product", "open", "windgram"))],
                [UiButton("📍 Геолокация", "request_location")],
            ]
        )
        await gateway.send_text(event.chat_id, START_TEXT, keyboard=UiKeyboard.from_rows(rows))

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)

        if not command and text and state is not None and state.product == "windgram":
            if state.step == "await_point":
                await self._resolve_windgram_point_text(event, gateway, text, state.params)
            else:
                await gateway.send_text(event.chat_id, "Продолжите настройку кнопками выше или отправьте /windgram заново.")
            return

        if command == "windgram":
            args = _command_args(text)
            if not args:
                recipe = self._default_windgram_recipe(event)
                if recipe is not None:
                    await self._send_windgram_recipe_card(event, gateway, recipe)
                else:
                    await self._ask_windgram_point(event, gateway)
            else:
                await self._resolve_windgram_direct(event, gateway, args)
            return

        await super()._text(event, gateway)

    async def _location(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "windgram":
            await super()._location(event, gateway)
            return
        if event.location is None:
            await gateway.send_text(event.chat_id, "В сообщении нет координат геолокации.")
            return
        point = GeoPoint(
            float(event.location.lat),
            float(event.location.lon),
            f"геолокация {event.location.lat:.4f}, {event.location.lon:.4f}",
            event.platform,
        )
        next_state = FlowState(product="windgram", step="params", point=point, params=_windgram_params(state.params))
        await self._send_windgram_params_card(event, gateway, next_state)

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
            if recipe is None or recipe.product != "windgram":
                await super()._callback(event, gateway)
                return
            await gateway.answer_callback(event)
            if data.action == "run":
                point = _recipe_point(recipe)
                if point is None:
                    await gateway.send_text(event.chat_id, "В сценарии потеряна точка. Создайте windgram заново.")
                    return
                await self._run_windgram(event, gateway, point, _windgram_params(recipe.params), None)
                return
            if data.action == "toggle":
                try:
                    updated = self.recipes.toggle_pinned(event.platform, event.user_id, recipe_id)
                except RecipeLimitError as exc:
                    await gateway.send_text(event.chat_id, str(exc))
                    return
                if updated is None:
                    await gateway.send_text(event.chat_id, "Сценарий больше недоступен.")
                    return
                await self._send_windgram_recipe_card(event, gateway, updated)
                return
            if data.action == "change":
                point = _recipe_point(recipe)
                if point is None:
                    await self._ask_windgram_point(event, gateway, _windgram_params(recipe.params))
                    return
                await self._send_windgram_params_card(
                    event,
                    gateway,
                    FlowState(product="windgram", step="params", point=point, params=_windgram_params(recipe.params)),
                )
                return
            await gateway.send_text(event.chat_id, "Кнопка сценария больше не поддерживается.")
            return

        if data.scope == "product" and data.action == "open" and data.value == "windgram":
            await gateway.answer_callback(event)
            recipe = self._default_windgram_recipe(event)
            if recipe is not None:
                await self._send_windgram_recipe_card(event, gateway, recipe)
            else:
                await self._ask_windgram_point(event, gateway)
            return

        if data.scope != "windgram":
            await super()._callback(event, gateway)
            return

        await gateway.answer_callback(event)
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "windgram":
            await gateway.send_text(event.chat_id, "Сценарий срока × уровень устарел. Начните заново: /windgram")
            return

        if data.action == "place":
            try:
                point = state.candidates[int(data.value or "")]
            except (ValueError, IndexError):
                await gateway.send_text(event.chat_id, "Вариант точки устарел. Повторите /windgram.")
                return
            params = _windgram_params(state.params)
            direct = bool(state.params.get("_direct"))
            if direct:
                await self._run_windgram(event, gateway, point, params, state.pending_run)
                return
            await self._send_windgram_params_card(
                event,
                gateway,
                FlowState(product="windgram", step="params", point=point, params=params),
            )
            return

        if data.action in {"param", "to", "step"}:
            params = _windgram_params(state.params)
            try:
                if data.action == "param":
                    if str(data.value) not in {item[0] for item in WINDGRAM_PARAMS}:
                        raise ValueError
                    params["param"] = str(data.value)
                elif data.action == "to":
                    value = int(data.value or "")
                    if value not in WINDGRAM_TO_HOURS:
                        raise ValueError
                    params["to"] = value
                    if int(params["from"]) > value:
                        params["from"] = 0
                else:
                    value = int(data.value or "")
                    if value not in WINDGRAM_STEPS:
                        raise ValueError
                    params["step"] = value
            except (TypeError, ValueError):
                await gateway.send_text(event.chat_id, "Параметр кнопки устарел. Откройте /windgram заново.")
                return
            state.params = params
            state.step = "params"
            await self._send_windgram_params_card(event, gateway, state)
            return

        if data.action == "run":
            if state.point is None:
                await gateway.send_text(event.chat_id, "Точка выбора потеряна. Начните заново: /windgram")
                return
            await self._run_windgram(event, gateway, state.point, _windgram_params(state.params), state.pending_run)
            return

        if data.action == "point":
            await self._ask_windgram_point(event, gateway, _windgram_params(state.params))
            return

        if data.action == "cancel":
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            await gateway.send_text(event.chat_id, "Выбор windgram отменён. Откройте /start.")
            return

        await gateway.send_text(event.chat_id, "Кнопка windgram больше не поддерживается. Начните заново: /windgram")

    async def _ask_windgram_point(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.sessions.set(
            event.platform,
            event.user_id,
            event.chat_id,
            FlowState(product="windgram", step="await_point", params=_windgram_params(params)),
        )
        await gateway.send_text(
            event.chat_id,
            "🟦 Срок × уровень GFS · шаг 1/2. Укажите город, координаты или отправьте геолокацию.",
            keyboard=UiKeyboard.from_rows([[UiButton("📍 Отправить геолокацию", "request_location")]]),
        )

    async def _resolve_windgram_point_text(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        raw: str,
        params: dict[str, Any],
    ) -> None:
        try:
            candidates = await asyncio.to_thread(self.deps.geocode, raw, 5)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка: {exc}")
            return
        if not candidates:
            await gateway.send_text(event.chat_id, "Точка не найдена. Уточните город или используйте координаты.")
            return
        if len(candidates) > 1:
            state = FlowState(
                product="windgram",
                step="choose_place",
                candidates=list(candidates[:5]),
                params={**_windgram_params(params), "_direct": False},
            )
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [
                [UiButton(_short_label(point), "callback", encode_callback("windgram", "place", index))]
                for index, point in enumerate(state.candidates)
            ]
            rows.append([UiButton("Отмена", "callback", encode_callback("windgram", "cancel"))])
            await gateway.send_text(event.chat_id, "Найдено несколько точек. Выберите нужную:", keyboard=UiKeyboard.from_rows(rows))
            return
        await self._send_windgram_params_card(
            event,
            gateway,
            FlowState(product="windgram", step="params", point=candidates[0], params=_windgram_params(params)),
        )

    async def _resolve_windgram_direct(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str) -> None:
        try:
            parsed = self.windgram_parser(raw)
            candidates = await asyncio.to_thread(self.deps.geocode, parsed.location_query, 5)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка: {exc}")
            return
        if not candidates:
            await gateway.send_text(event.chat_id, "Точка не найдена. Уточните город или используйте координаты.")
            return
        params = _params_from_parsed(parsed)
        if len(candidates) > 1:
            state = FlowState(
                product="windgram",
                step="choose_place",
                candidates=list(candidates[:5]),
                pending_run=parsed.run,
                params={**params, "_direct": True},
            )
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [
                [UiButton(_short_label(point), "callback", encode_callback("windgram", "place", index))]
                for index, point in enumerate(state.candidates)
            ]
            rows.append([UiButton("Отмена", "callback", encode_callback("windgram", "cancel"))])
            await gateway.send_text(event.chat_id, "Найдено несколько точек. Выберите нужную:", keyboard=UiKeyboard.from_rows(rows))
            return
        await self._run_windgram(event, gateway, candidates[0], params, parsed.run)

    async def _send_windgram_params_card(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        state: FlowState,
    ) -> None:
        state.product = "windgram"
        state.step = "params"
        state.params = _windgram_params(state.params)
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await gateway.send_text(
            event.chat_id,
            _params_text(state.point, state.params),
            keyboard=_params_keyboard(state.params),
        )

    async def _send_windgram_recipe_card(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        recipe: UserRecipe,
    ) -> None:
        point = recipe.point or {}
        params = _windgram_params(recipe.params)
        pin_text = "★ Открепить" if recipe.pinned else "⭐ Закрепить"
        await gateway.send_text(
            event.chat_id,
            "🟦 Срок × уровень GFS\n"
            f"📍 {point.get('label', 'точка')}\n"
            f"{PARAM_NAMES.get(str(params['param']), params['param'])} · +{params['from']}…+{params['to']} ч · "
            f"шаг {params['step']} ч · до {params['top']} гПа",
            keyboard=UiKeyboard.from_rows(
                [
                    [UiButton("▶ Построить", "callback", encode_callback("recipe", "run", recipe.recipe_id))],
                    [
                        UiButton(pin_text, "callback", encode_callback("recipe", "toggle", recipe.recipe_id)),
                        UiButton("⚙ Изменить", "callback", encode_callback("recipe", "change", recipe.recipe_id)),
                    ],
                ]
            ),
        )

    async def _run_windgram(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        point: Any,
        params: dict[str, Any],
        run: Any | None,
    ) -> bool:
        params = _windgram_params(params)
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        status = await gateway.send_text(
            event.chat_id,
            "⏳ Срок × уровень GFS\n"
            f"📍 {getattr(point, 'label', '')}\n"
            f"+{params['from']}…+{params['to']} ч · шаг {params['step']} ч\n"
            "1/6 Проверяю опубликованный цикл…",
        )
        snapshot = {"event": ProgressEvent("check", "Проверяю данные")}
        lock = Lock()
        stop = asyncio.Event()
        last_text = ""
        result = None

        def progress(value: ProgressEvent) -> None:
            with lock:
                snapshot["event"] = value

        async def reporter() -> None:
            nonlocal last_text
            while not stop.is_set():
                with lock:
                    value = snapshot["event"]
                text = _windgram_progress_text(point, params, value)
                if text != last_text:
                    try:
                        await gateway.edit_text(event.chat_id, status.message_id, text)
                        last_text = text
                    except Exception:
                        pass
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.progress_interval_seconds)
                except asyncio.TimeoutError:
                    pass

        reporter_task = asyncio.create_task(reporter())
        try:
            async with self.gfs_semaphore:
                result = await asyncio.to_thread(
                    self.windgram_builder,
                    point,
                    int(params["from"]),
                    int(params["to"]),
                    int(params["step"]),
                    int(params["top"]),
                    str(params["param"]),
                    run,
                    progress_callback=progress,
                )
            stop.set()
            await reporter_task
            await gateway.edit_text(event.chat_id, status.message_id, result.summary)
            for attachment in result.attachments:
                if attachment.kind == "image":
                    await gateway.send_image(event.chat_id, attachment.path, caption=attachment.caption)
                elif attachment.kind == "animation":
                    await gateway.send_animation(event.chat_id, attachment.path, caption=attachment.caption)
                else:
                    await gateway.send_file(
                        event.chat_id,
                        attachment.path,
                        caption=attachment.caption,
                        filename=attachment.filename,
                    )
            if result.repeat_command:
                await gateway.send_text(event.chat_id, f"📋 Повторить:\n{result.repeat_command}")
            recipe = self.recipes.record_success(
                event.platform,
                event.user_id,
                "windgram",
                params,
                point,
            )
            await self._send_windgram_recipe_card(event, gateway, recipe)
            return True
        except Exception as exc:
            stop.set()
            await reporter_task
            await gateway.edit_text(event.chat_id, status.message_id, f"Ошибка расчёта: {exc}")
            return False
        finally:
            stop.set()
            if not reporter_task.done():
                await reporter_task
            if result is not None:
                cleanup_product_result(result)
