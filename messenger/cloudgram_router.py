from __future__ import annotations

"""Common MAX/VK router with profile + aero + windgram + cloudgram slices."""

import asyncio
from threading import Lock
from typing import Any, Callable

from geocode import GeoPoint

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .cloudgram_service import (
    MODE_TITLES,
    ParsedCloudgramInput,
    build_cloudgram_product_result,
    parse_cloudgram_input,
)
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .profile_service import cleanup_product_result
from .router import RouterDependencies, _command_args, _short_label
from .state import FlowState
from .user_recipes import RecipeLimitError, UserRecipe
from .windgram_router import WindgramMessengerRouter

CLOUDGRAM_TO_HOURS = (24, 48, 72, 120)
CLOUDGRAM_STEPS = (3, 6)
CLOUDGRAM_MODES = (("pro", "Подробно"), ("simple", "Кратко"))
DEFAULT_CLOUDGRAM_PARAMS = {"from": 0, "to": 72, "step": 3, "mode": "pro"}

START_TEXT = (
    "🌦 GFS 0.25 — модельный прогноз\n"
    "Укажите город, координаты или геолокацию. Быстрый запрос: Москва +24.\n\n"
    "Продукты: /profile, /aero, /windgram, /cloudgram. Остальные подключаются поэтапно.\n"
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


def _cloudgram_params(value: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(DEFAULT_CLOUDGRAM_PARAMS)
    if value:
        for key in result:
            if key in value:
                result[key] = value[key]
    result["from"] = int(result["from"])
    result["to"] = int(result["to"])
    result["step"] = int(result["step"])
    result["mode"] = "simple" if str(result["mode"]) == "simple" else "pro"
    return result


def _params_from_parsed(parsed: ParsedCloudgramInput) -> dict[str, Any]:
    return {
        "from": int(parsed.lead_from),
        "to": int(parsed.lead_to),
        "step": int(parsed.step),
        "mode": str(parsed.mode),
    }


def _quick_recipe_label(recipe: UserRecipe) -> str:
    point = recipe.point or {}
    marker = "★" if recipe.pinned else "▶"
    if recipe.product == "profile":
        return f"{marker} Профиль · {point.get('label', 'точка')} · +{int(recipe.params.get('lead', 24))} ч"[:62]
    if recipe.product == "aero":
        return f"{marker} Аэродиаграмма · {point.get('label', 'точка')} · +{int(recipe.params.get('lead', 24))} ч"[:62]
    if recipe.product == "windgram":
        return (
            f"{marker} Срок × уровень · {point.get('label', 'точка')} · "
            f"+{int(recipe.params.get('from', 0))}…+{int(recipe.params.get('to', 120))}"
        )[:62]
    params = _cloudgram_params(recipe.params)
    return (
        f"{marker} Облака · {point.get('label', 'точка')} · "
        f"+{params['from']}…+{params['to']} · {MODE_TITLES[params['mode']]}"
    )[:62]


def _cloudgram_progress_text(point: Any, params: dict[str, Any], event: ProgressEvent) -> str:
    data = dict(event.data)
    header = (
        "⏳ Облака и явления GFS\n"
        f"📍 {getattr(point, 'label', '')}\n"
        f"+{params['from']}…+{params['to']} ч · шаг {params['step']} ч · {MODE_TITLES[params['mode']]}\n"
    )
    if event.stage in {"check", "run"}:
        body = "1/6 Проверяю опубликованный цикл…"
        if event.stage == "run" and data.get("run_date") and data.get("run_cycle"):
            body = f"1/6 GFS {data['run_date']} {data['run_cycle']}Z"
    elif event.stage == "grid":
        body = f"2/6 Узел GFS: {data.get('grid_lat')}, {data.get('grid_lon')}"
    elif event.stage in {"cloudgram_step", "download_start", "download", "download_done", "cache"}:
        current = data.get("index") or event.current
        total = data.get("total") or event.total
        lead = data.get("lead_hour")
        suffix = f" · +{lead} ч" if lead is not None else ""
        body = f"3/6 Загружаю сроки: {current}/{total}{suffix}" if current and total else "3/6 Загружаю модельные поля…"
    elif event.stage in {"parse_start", "parse_done", "done"}:
        body = "4/6 Считаю облачность, явления и риски…"
    elif event.stage in {"plot_start", "plot_done"}:
        body = "5/6 Формирую PNG…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


def _params_text(point: Any, params: dict[str, Any]) -> str:
    return (
        "☁️ Облака и явления GFS\n"
        f"📍 {getattr(point, 'label', 'точка')}\n"
        f"Режим: {MODE_TITLES[params['mode']]}\n"
        f"Период: +{params['from']}…+{params['to']} ч\n"
        f"Шаг: {params['step']} ч\n\n"
        "Подробно — облачность H/M/L, осадки, видимость, ВНГО и конвективная диагностика.\n"
        "Кратко — компактное представление тех же модельных данных."
    )


def _params_keyboard(params: dict[str, Any]) -> UiKeyboard:
    mode = str(params["mode"])
    lead_to = int(params["to"])
    step = int(params["step"])
    return UiKeyboard.from_rows(
        [
            [UiButton("▶ Построить", "callback", encode_callback("cloudgram", "run"))],
            [
                UiButton(("✓ " if mode == key else "") + label, "callback", encode_callback("cloudgram", "mode", key))
                for key, label in CLOUDGRAM_MODES
            ],
            [
                UiButton(("✓ " if lead_to == value else "") + f"до +{value}", "callback", encode_callback("cloudgram", "to", value))
                for value in CLOUDGRAM_TO_HOURS
            ],
            [
                UiButton(("✓ " if step == value else "") + f"шаг {value}ч", "callback", encode_callback("cloudgram", "step", value))
                for value in CLOUDGRAM_STEPS
            ],
            [
                UiButton("📍 Другая точка", "callback", encode_callback("cloudgram", "point")),
                UiButton("Отмена", "callback", encode_callback("cloudgram", "cancel")),
            ],
        ]
    )


class CloudgramMessengerRouter(WindgramMessengerRouter):
    """Messenger-neutral profile/aero/windgram/cloudgram UX for MAX and VK."""

    def __init__(
        self,
        dependencies: RouterDependencies,
        *,
        cloudgram_builder: Callable[..., Any] = build_cloudgram_product_result,
        cloudgram_parser: Callable[[str], ParsedCloudgramInput] = parse_cloudgram_input,
        **kwargs: Any,
    ) -> None:
        super().__init__(dependencies, **kwargs)
        self.cloudgram_builder = cloudgram_builder
        self.cloudgram_parser = cloudgram_parser

    @classmethod
    def default(cls, **kwargs: Any) -> "CloudgramMessengerRouter":
        from geocode_choices import search_location_candidates

        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    def _default_cloudgram_recipe(self, event: NormalizedEvent) -> UserRecipe | None:
        return self.recipes.default_for_product(event.platform, event.user_id, "cloudgram") or self.recipes.latest_for_product(
            event.platform,
            event.user_id,
            "cloudgram",
        )

    async def _start(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        rows: list[list[UiButton]] = []
        for recipe in self.recipes.quick(event.platform, event.user_id, limit=2):
            if recipe.product in {"profile", "aero", "windgram", "cloudgram"}:
                rows.append([UiButton(_quick_recipe_label(recipe), "callback", encode_callback("recipe", "run", recipe.recipe_id))])
        rows.extend(
            [
                [
                    UiButton("📈 Профиль", "callback", encode_callback("product", "open", "profile")),
                    UiButton("🧾 Аэродиаграмма", "callback", encode_callback("product", "open", "aero")),
                ],
                [
                    UiButton("🟦 Срок × уровень", "callback", encode_callback("product", "open", "windgram")),
                    UiButton("☁️ Облака", "callback", encode_callback("product", "open", "cloudgram")),
                ],
                [UiButton("📍 Геолокация", "request_location")],
            ]
        )
        await gateway.send_text(event.chat_id, START_TEXT, keyboard=UiKeyboard.from_rows(rows))

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if not command and text and state is not None and state.product == "cloudgram":
            if state.step == "await_point":
                await self._resolve_cloudgram_point_text(event, gateway, text, state.params)
            else:
                await gateway.send_text(event.chat_id, "Продолжите настройку кнопками выше или отправьте /cloudgram заново.")
            return
        if command == "cloudgram":
            args = _command_args(text)
            if not args:
                recipe = self._default_cloudgram_recipe(event)
                if recipe is not None:
                    await self._send_cloudgram_recipe_card(event, gateway, recipe)
                else:
                    await self._ask_cloudgram_point(event, gateway)
            else:
                await self._resolve_cloudgram_direct(event, gateway, args)
            return
        await super()._text(event, gateway)

    async def _location(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "cloudgram":
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
        await self._send_cloudgram_params_card(
            event,
            gateway,
            FlowState(product="cloudgram", step="params", point=point, params=_cloudgram_params(state.params)),
        )

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
            if recipe is None or recipe.product != "cloudgram":
                await super()._callback(event, gateway)
                return
            await gateway.answer_callback(event)
            if data.action == "run":
                point = _recipe_point(recipe)
                if point is None:
                    await gateway.send_text(event.chat_id, "В сценарии потеряна точка. Создайте cloudgram заново.")
                    return
                await self._run_cloudgram(event, gateway, point, _cloudgram_params(recipe.params), None)
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
                await self._send_cloudgram_recipe_card(event, gateway, updated)
                return
            if data.action == "change":
                point = _recipe_point(recipe)
                if point is None:
                    await self._ask_cloudgram_point(event, gateway, _cloudgram_params(recipe.params))
                    return
                await self._send_cloudgram_params_card(
                    event,
                    gateway,
                    FlowState(product="cloudgram", step="params", point=point, params=_cloudgram_params(recipe.params)),
                )
                return
            await gateway.send_text(event.chat_id, "Кнопка сценария больше не поддерживается.")
            return

        if data.scope == "product" and data.action == "open" and data.value == "cloudgram":
            await gateway.answer_callback(event)
            recipe = self._default_cloudgram_recipe(event)
            if recipe is not None:
                await self._send_cloudgram_recipe_card(event, gateway, recipe)
            else:
                await self._ask_cloudgram_point(event, gateway)
            return

        if data.scope != "cloudgram":
            await super()._callback(event, gateway)
            return

        await gateway.answer_callback(event)
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "cloudgram":
            await gateway.send_text(event.chat_id, "Сценарий облаков устарел. Начните заново: /cloudgram")
            return

        if data.action == "place":
            try:
                point = state.candidates[int(data.value or "")]
            except (ValueError, IndexError):
                await gateway.send_text(event.chat_id, "Вариант точки устарел. Повторите /cloudgram.")
                return
            params = _cloudgram_params(state.params)
            if bool(state.params.get("_direct")):
                await self._run_cloudgram(event, gateway, point, params, state.pending_run)
                return
            await self._send_cloudgram_params_card(
                event,
                gateway,
                FlowState(product="cloudgram", step="params", point=point, params=params),
            )
            return

        if data.action in {"mode", "to", "step"}:
            params = _cloudgram_params(state.params)
            try:
                if data.action == "mode":
                    if str(data.value) not in {item[0] for item in CLOUDGRAM_MODES}:
                        raise ValueError
                    params["mode"] = str(data.value)
                elif data.action == "to":
                    value = int(data.value or "")
                    if value not in CLOUDGRAM_TO_HOURS:
                        raise ValueError
                    params["to"] = value
                    if int(params["from"]) > value:
                        params["from"] = 0
                else:
                    value = int(data.value or "")
                    if value not in CLOUDGRAM_STEPS:
                        raise ValueError
                    params["step"] = value
            except (TypeError, ValueError):
                await gateway.send_text(event.chat_id, "Параметр кнопки устарел. Откройте /cloudgram заново.")
                return
            state.params = params
            state.step = "params"
            await self._send_cloudgram_params_card(event, gateway, state)
            return

        if data.action == "run":
            if state.point is None:
                await gateway.send_text(event.chat_id, "Точка выбора потеряна. Начните заново: /cloudgram")
                return
            await self._run_cloudgram(event, gateway, state.point, _cloudgram_params(state.params), state.pending_run)
            return
        if data.action == "point":
            await self._ask_cloudgram_point(event, gateway, _cloudgram_params(state.params))
            return
        if data.action == "cancel":
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            await gateway.send_text(event.chat_id, "Выбор cloudgram отменён. Откройте /start.")
            return
        await gateway.send_text(event.chat_id, "Кнопка cloudgram больше не поддерживается. Начните заново: /cloudgram")

    async def _ask_cloudgram_point(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.sessions.set(
            event.platform,
            event.user_id,
            event.chat_id,
            FlowState(product="cloudgram", step="await_point", params=_cloudgram_params(params)),
        )
        await gateway.send_text(
            event.chat_id,
            "☁️ Облака и явления GFS · шаг 1/2. Укажите город, координаты или отправьте геолокацию.",
            keyboard=UiKeyboard.from_rows([[UiButton("📍 Отправить геолокацию", "request_location")]]),
        )

    async def _resolve_cloudgram_point_text(
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
                product="cloudgram",
                step="choose_place",
                candidates=list(candidates[:5]),
                params={**_cloudgram_params(params), "_direct": False},
            )
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [[UiButton(_short_label(point), "callback", encode_callback("cloudgram", "place", index))] for index, point in enumerate(state.candidates)]
            rows.append([UiButton("Отмена", "callback", encode_callback("cloudgram", "cancel"))])
            await gateway.send_text(event.chat_id, "Найдено несколько точек. Выберите нужную:", keyboard=UiKeyboard.from_rows(rows))
            return
        await self._send_cloudgram_params_card(
            event,
            gateway,
            FlowState(product="cloudgram", step="params", point=candidates[0], params=_cloudgram_params(params)),
        )

    async def _resolve_cloudgram_direct(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str) -> None:
        try:
            parsed = self.cloudgram_parser(raw)
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
                product="cloudgram",
                step="choose_place",
                candidates=list(candidates[:5]),
                pending_run=parsed.run,
                params={**params, "_direct": True},
            )
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [[UiButton(_short_label(point), "callback", encode_callback("cloudgram", "place", index))] for index, point in enumerate(state.candidates)]
            rows.append([UiButton("Отмена", "callback", encode_callback("cloudgram", "cancel"))])
            await gateway.send_text(event.chat_id, "Найдено несколько точек. Выберите нужную:", keyboard=UiKeyboard.from_rows(rows))
            return
        await self._run_cloudgram(event, gateway, candidates[0], params, parsed.run)

    async def _send_cloudgram_params_card(self, event: NormalizedEvent, gateway: MessengerGateway, state: FlowState) -> None:
        state.product = "cloudgram"
        state.step = "params"
        state.params = _cloudgram_params(state.params)
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await gateway.send_text(event.chat_id, _params_text(state.point, state.params), keyboard=_params_keyboard(state.params))

    async def _send_cloudgram_recipe_card(self, event: NormalizedEvent, gateway: MessengerGateway, recipe: UserRecipe) -> None:
        point = recipe.point or {}
        params = _cloudgram_params(recipe.params)
        pin_text = "★ Открепить" if recipe.pinned else "⭐ Закрепить"
        await gateway.send_text(
            event.chat_id,
            "☁️ Облака и явления GFS\n"
            f"📍 {point.get('label', 'точка')}\n"
            f"{MODE_TITLES[params['mode']]} · +{params['from']}…+{params['to']} ч · шаг {params['step']} ч",
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

    async def _run_cloudgram(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        point: Any,
        params: dict[str, Any],
        run: Any | None,
    ) -> bool:
        params = _cloudgram_params(params)
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        status = await gateway.send_text(
            event.chat_id,
            "⏳ Облака и явления GFS\n"
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
                text = _cloudgram_progress_text(point, params, value)
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
                    self.cloudgram_builder,
                    point,
                    int(params["from"]),
                    int(params["to"]),
                    int(params["step"]),
                    str(params["mode"]),
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
                    await gateway.send_file(event.chat_id, attachment.path, caption=attachment.caption, filename=attachment.filename)
            if result.repeat_command:
                await gateway.send_text(event.chat_id, f"📋 Повторить:\n{result.repeat_command}")
            recipe = self.recipes.record_success(event.platform, event.user_id, "cloudgram", params, point)
            await self._send_cloudgram_recipe_card(event, gateway, recipe)
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
