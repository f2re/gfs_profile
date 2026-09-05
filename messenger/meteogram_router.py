from __future__ import annotations

"""Common MAX/VK router including the meteogram vertical slice.

All meteogram helpers are product-prefixed on purpose. Parent routers call their
own product-specific helpers, so adding a child vertical slice cannot silently
change `/map`, `/cloudgram`, etc. through Python virtual method dispatch.
"""

import asyncio
from threading import Lock
from typing import Any, Callable

from geocode import GeoPoint
from meteogram_core import available_periods, source_for_id, sources_by_kind

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .map_router import MapMessengerRouter
from .meteogram_service import (
    DEFAULT_METEOGRAM_PARAMS,
    ParsedMeteogramInput,
    build_meteogram_product_result,
    normalize_meteogram_params,
    parse_meteogram_input,
)
from .profile_service import cleanup_product_result
from .router import RouterDependencies, _command_args, _short_label
from .state import FlowState
from .user_recipes import RecipeLimitError, UserRecipe

OUTPUTS = (("png", "PNG"), ("docx", "DOCX"), ("pdf", "PDF"))
START_TEXT = (
    "🌦 GFS 0.25 — модельный прогноз\n"
    "Доступны профиль, аэродиаграмма, срок × уровень, облака, карта и метеограмма.\n"
    "Метеограмма поддерживает несколько моделей и ансамблей; модель — не наблюдение."
)


def _meteogram_params(value: dict[str, Any] | None = None) -> dict[str, Any]:
    return normalize_meteogram_params(value)


def _recipe_point(recipe: UserRecipe) -> GeoPoint | None:
    value = recipe.point
    if not value:
        return None
    return GeoPoint(
        float(value["lat"]), float(value["lon"]),
        str(value.get("label", "сохранённая точка")),
        str(value.get("source", recipe.platform)),
    )


def _quick_label(recipe: UserRecipe) -> str:
    marker = "★" if recipe.pinned else "▶"
    label = (recipe.point or {}).get("label", "точка")
    if recipe.product == "meteogram":
        p = _meteogram_params(recipe.params)
        source = source_for_id(p["source"])
        return f"{marker} Метеограмма · {label} · {source.label} · {p['days']} сут"[:62]
    titles = {
        "profile": "Профиль", "aero": "Аэродиаграмма", "windgram": "Срок × уровень",
        "cloudgram": "Облака", "map": "Карта",
    }
    return f"{marker} {titles.get(recipe.product, recipe.product)} · {label}"[:62]


def _card_text(point: Any, params: dict[str, Any]) -> str:
    source = source_for_id(params["source"])
    return (
        "📊 Метеограмма\n"
        f"📍 {getattr(point, 'label', 'точка')}\n"
        f"Тип: {'Ансамбль' if source.ensemble else 'Одна модель'}\n"
        f"Модель: {source.label}\n"
        f"Период: {params['days']} суток\n"
        f"Результат: {params['format'].upper()}\n\n"
        "Модельный прогноз; разные ансамблевые системы не смешиваются."
    )


def _main_keyboard(params: dict[str, Any]) -> UiKeyboard:
    source = source_for_id(params["source"])
    return UiKeyboard.from_rows([
        [UiButton("▶ Построить", "callback", encode_callback("meteo", "run"))],
        [
            UiButton("✓ Одна модель" if not source.ensemble else "Одна модель", "callback", encode_callback("meteo", "kind", "det")),
            UiButton("✓ Ансамбль" if source.ensemble else "Ансамбль", "callback", encode_callback("meteo", "kind", "ens")),
        ],
        [
            UiButton("Модель", "callback", encode_callback("meteo", "model")),
            UiButton("Период", "callback", encode_callback("meteo", "period")),
            UiButton("Формат", "callback", encode_callback("meteo", "format")),
        ],
        [
            UiButton("📍 Другая точка", "callback", encode_callback("meteo", "point")),
            UiButton("Отмена", "callback", encode_callback("meteo", "cancel")),
        ],
    ])


def _sources_keyboard(ensemble: bool) -> UiKeyboard:
    rows = [[UiButton(source.label, "callback", encode_callback("meteo", "source", source.source_id))] for source in sources_by_kind(ensemble)]
    rows.append([UiButton("Назад", "callback", encode_callback("meteo", "back"))])
    return UiKeyboard.from_rows(rows)


def _period_keyboard(source_id: str, current: int) -> UiKeyboard:
    values = available_periods(source_for_id(source_id))
    rows: list[list[UiButton]] = []
    for start in range(0, len(values), 3):
        rows.append([
            UiButton(("✓ " if value == current else "") + f"{value} сут", "callback", encode_callback("meteo", "days", value))
            for value in values[start:start + 3]
        ])
    rows.append([UiButton("Назад", "callback", encode_callback("meteo", "back"))])
    return UiKeyboard.from_rows(rows)


def _format_keyboard(current: str) -> UiKeyboard:
    return UiKeyboard.from_rows([
        [UiButton(("✓ " if key == current else "") + label, "callback", encode_callback("meteo", "out", key)) for key, label in OUTPUTS],
        [UiButton("Назад", "callback", encode_callback("meteo", "back"))],
    ])


def _progress_text(point: Any, source_id: str, event: ProgressEvent) -> str:
    source = source_for_id(source_id)
    if event.stage == "fetch_start":
        body = "1/5 Получаю прогноз…"
    elif event.stage == "fetch":
        body = f"2/5 {event.message}…"
    elif event.stage == "plot_start":
        body = "3/5 Строю PNG…"
    elif event.stage == "report_start":
        body = f"4/5 {event.message}…"
    else:
        body = event.message or "Выполняю расчёт…"
    return f"⏳ Метеограмма · {source.label}\n📍 {getattr(point, 'label', '')}\n{body}"


class MeteogramMessengerRouter(MapMessengerRouter):
    def __init__(
        self,
        dependencies: RouterDependencies,
        *,
        meteogram_builder: Callable[..., Any] = build_meteogram_product_result,
        meteogram_parser: Callable[[str], ParsedMeteogramInput] = parse_meteogram_input,
        **kwargs: Any,
    ) -> None:
        super().__init__(dependencies, **kwargs)
        self.meteogram_builder = meteogram_builder
        self.meteogram_parser = meteogram_parser

    @classmethod
    def default(cls, **kwargs: Any) -> "MeteogramMessengerRouter":
        from geocode_choices import search_location_candidates
        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    def _default_meteogram_recipe(self, event: NormalizedEvent) -> UserRecipe | None:
        return self.recipes.default_for_product(event.platform, event.user_id, "meteogram") or self.recipes.latest_for_product(
            event.platform, event.user_id, "meteogram"
        )

    async def _start(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        rows: list[list[UiButton]] = []
        for recipe in self.recipes.quick(event.platform, event.user_id, limit=2):
            if recipe.product in {"profile", "aero", "windgram", "cloudgram", "map", "meteogram"}:
                rows.append([UiButton(_quick_label(recipe), "callback", encode_callback("recipe", "run", recipe.recipe_id))])
        rows.extend([
            [UiButton("📈 Профиль", "callback", encode_callback("product", "open", "profile")), UiButton("🧾 Аэродиаграмма", "callback", encode_callback("product", "open", "aero"))],
            [UiButton("🟦 Срок × уровень", "callback", encode_callback("product", "open", "windgram")), UiButton("☁ Облака", "callback", encode_callback("product", "open", "cloudgram"))],
            [UiButton("🗺 Карта", "callback", encode_callback("product", "open", "map")), UiButton("📊 Метеограмма", "callback", encode_callback("product", "open", "meteogram"))],
            [UiButton("📍 Геолокация", "request_location")],
        ])
        await gateway.send_text(event.chat_id, START_TEXT, keyboard=UiKeyboard.from_rows(rows))

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if not command and text and state is not None and state.product == "meteogram":
            if state.step == "await_point":
                await self._resolve_meteogram_point(event, gateway, text, state.params)
            else:
                await gateway.send_text(event.chat_id, "Продолжите настройку метеограммы кнопками или отправьте /meteogram заново.")
            return
        if command == "meteogram":
            args = _command_args(text)
            if not args:
                recipe = self._default_meteogram_recipe(event)
                if recipe:
                    await self._send_meteogram_recipe(event, gateway, recipe)
                else:
                    await self._ask_meteogram_point(event, gateway, dict(DEFAULT_METEOGRAM_PARAMS))
            else:
                await self._resolve_meteogram_direct(event, gateway, args)
            return
        await super()._text(event, gateway)

    async def _location(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "meteogram":
            await super()._location(event, gateway)
            return
        if event.location is None:
            await gateway.send_text(event.chat_id, "В сообщении нет координат геолокации.")
            return
        point = GeoPoint(float(event.location.lat), float(event.location.lon), f"геолокация {event.location.lat:.4f}, {event.location.lon:.4f}", event.platform)
        await self._send_meteogram_card(event, gateway, FlowState(product="meteogram", step="params", point=point, params=_meteogram_params(state.params)))

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
            if recipe is None or recipe.product != "meteogram":
                await super()._callback(event, gateway)
                return
            await gateway.answer_callback(event)
            if data.action == "run":
                point = _recipe_point(recipe)
                if point:
                    await self._run_meteogram(event, gateway, point, _meteogram_params(recipe.params))
                return
            if data.action == "toggle":
                try:
                    updated = self.recipes.toggle_pinned(event.platform, event.user_id, recipe_id)
                except RecipeLimitError as exc:
                    await gateway.send_text(event.chat_id, str(exc))
                    return
                if updated:
                    await self._send_meteogram_recipe(event, gateway, updated)
                return
            if data.action == "change":
                point = _recipe_point(recipe)
                if point:
                    await self._send_meteogram_card(event, gateway, FlowState(product="meteogram", step="params", point=point, params=_meteogram_params(recipe.params)))
                return

        if data.scope == "product" and data.action == "open" and data.value == "meteogram":
            await gateway.answer_callback(event)
            recipe = self._default_meteogram_recipe(event)
            if recipe:
                await self._send_meteogram_recipe(event, gateway, recipe)
            else:
                await self._ask_meteogram_point(event, gateway, dict(DEFAULT_METEOGRAM_PARAMS))
            return
        if data.scope != "meteo":
            await super()._callback(event, gateway)
            return

        await gateway.answer_callback(event)
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "meteogram":
            await gateway.send_text(event.chat_id, "Сценарий метеограммы устарел. Запустите /meteogram.")
            return

        if data.action == "place":
            try:
                point = state.candidates[int(data.value or "")]
            except (ValueError, IndexError):
                await gateway.send_text(event.chat_id, "Вариант точки устарел.")
                return
            if state.params.get("_direct"):
                await self._run_meteogram(event, gateway, point, _meteogram_params(state.params))
            else:
                await self._send_meteogram_card(event, gateway, FlowState(product="meteogram", step="params", point=point, params=_meteogram_params(state.params)))
            return

        p = _meteogram_params(state.params)
        if data.action == "kind":
            await gateway.send_text(event.chat_id, "Выберите ансамблевую систему." if data.value == "ens" else "Выберите модель.", keyboard=_sources_keyboard(data.value == "ens"))
            return
        if data.action == "model":
            await gateway.send_text(event.chat_id, "Выберите модель.", keyboard=_sources_keyboard(source_for_id(p["source"]).ensemble))
            return
        if data.action == "source":
            source = source_for_id(str(data.value))
            p["source"] = source.source_id
            if p["days"] > source.horizon_days:
                p["days"] = min(5, source.horizon_days)
            state.params = _meteogram_params(p)
            await self._send_meteogram_card(event, gateway, state)
            return
        if data.action == "period":
            await gateway.send_text(event.chat_id, "Выберите период.", keyboard=_period_keyboard(p["source"], p["days"]))
            return
        if data.action == "days":
            p["days"] = int(data.value or 5)
            state.params = _meteogram_params(p)
            await self._send_meteogram_card(event, gateway, state)
            return
        if data.action == "format":
            await gateway.send_text(event.chat_id, "Выберите формат.", keyboard=_format_keyboard(p["format"]))
            return
        if data.action == "out":
            p["format"] = str(data.value)
            state.params = _meteogram_params(p)
            await self._send_meteogram_card(event, gateway, state)
            return
        if data.action == "back":
            await self._send_meteogram_card(event, gateway, state)
            return
        if data.action == "run":
            if state.point:
                await self._run_meteogram(event, gateway, state.point, p)
            return
        if data.action == "point":
            await self._ask_meteogram_point(event, gateway, p)
            return
        if data.action == "cancel":
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            await gateway.send_text(event.chat_id, "Выбор метеограммы отменён.")

    async def _ask_meteogram_point(self, event: NormalizedEvent, gateway: MessengerGateway, params: dict[str, Any]) -> None:
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product="meteogram", step="await_point", params=_meteogram_params(params)))
        await gateway.send_text(event.chat_id, "📊 Метеограмма · укажите город, координаты или геолокацию.", keyboard=UiKeyboard.from_rows([[UiButton("📍 Отправить геолокацию", "request_location")]]))

    async def _resolve_meteogram_point(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str, params: dict[str, Any]) -> None:
        try:
            candidates = await asyncio.to_thread(self.deps.geocode, raw, 5)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка: {exc}")
            return
        if not candidates:
            await gateway.send_text(event.chat_id, "Точка не найдена.")
            return
        if len(candidates) > 1:
            state = FlowState(product="meteogram", step="choose_place", candidates=list(candidates[:5]), params={**_meteogram_params(params), "_direct": False})
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [[UiButton(_short_label(point), "callback", encode_callback("meteo", "place", i))] for i, point in enumerate(state.candidates)]
            await gateway.send_text(event.chat_id, "Выберите точку:", keyboard=UiKeyboard.from_rows(rows))
            return
        await self._send_meteogram_card(event, gateway, FlowState(product="meteogram", step="params", point=candidates[0], params=_meteogram_params(params)))

    async def _resolve_meteogram_direct(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str) -> None:
        try:
            parsed = self.meteogram_parser(raw)
            candidates = await asyncio.to_thread(self.deps.geocode, parsed.location_query, 5)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка: {exc}")
            return
        if not candidates:
            await gateway.send_text(event.chat_id, "Точка не найдена.")
            return
        p = _meteogram_params({"source": parsed.source_id, "days": parsed.days, "format": parsed.output_format})
        if len(candidates) > 1:
            state = FlowState(product="meteogram", step="choose_place", candidates=list(candidates[:5]), params={**p, "_direct": True})
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [[UiButton(_short_label(point), "callback", encode_callback("meteo", "place", i))] for i, point in enumerate(state.candidates)]
            await gateway.send_text(event.chat_id, "Выберите точку:", keyboard=UiKeyboard.from_rows(rows))
            return
        await self._run_meteogram(event, gateway, candidates[0], p)

    async def _send_meteogram_card(self, event: NormalizedEvent, gateway: MessengerGateway, state: FlowState) -> None:
        state.product = "meteogram"
        state.step = "params"
        state.params = _meteogram_params(state.params)
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await gateway.send_text(event.chat_id, _card_text(state.point, state.params), keyboard=_main_keyboard(state.params))

    async def _send_meteogram_recipe(self, event: NormalizedEvent, gateway: MessengerGateway, recipe: UserRecipe) -> None:
        point = _recipe_point(recipe)
        if point is None:
            return
        p = _meteogram_params(recipe.params)
        pin = "★ Открепить" if recipe.pinned else "⭐ Закрепить"
        await gateway.send_text(event.chat_id, _card_text(point, p), keyboard=UiKeyboard.from_rows([
            [UiButton("▶ Построить", "callback", encode_callback("recipe", "run", recipe.recipe_id))],
            [UiButton(pin, "callback", encode_callback("recipe", "toggle", recipe.recipe_id)), UiButton("⚙ Изменить", "callback", encode_callback("recipe", "change", recipe.recipe_id))],
        ]))

    async def _run_meteogram(self, event: NormalizedEvent, gateway: MessengerGateway, point: Any, params: dict[str, Any]) -> bool:
        p = _meteogram_params(params)
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        status = await gateway.send_text(event.chat_id, _progress_text(point, p["source"], ProgressEvent("fetch_start", "")))
        snapshot = {"event": ProgressEvent("fetch_start", "")}
        lock = Lock()
        stop = asyncio.Event()
        last = ""
        result = None

        def progress(value: ProgressEvent) -> None:
            with lock:
                snapshot["event"] = value

        async def reporter() -> None:
            nonlocal last
            while not stop.is_set():
                with lock:
                    value = snapshot["event"]
                text = _progress_text(point, p["source"], value)
                if text != last:
                    try:
                        await gateway.edit_text(event.chat_id, status.message_id, text)
                        last = text
                    except Exception:
                        pass
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.progress_interval_seconds)
                except asyncio.TimeoutError:
                    pass

        task = asyncio.create_task(reporter())
        try:
            semaphore = getattr(self, "meteogram_semaphore", self.gfs_semaphore)
            async with semaphore:
                result = await asyncio.to_thread(
                    self.meteogram_builder,
                    point, p["source"], p["days"], p["format"],
                    progress_callback=progress,
                )
            stop.set()
            await task
            await gateway.edit_text(event.chat_id, status.message_id, result.summary)
            for attachment in result.attachments:
                if attachment.kind == "image":
                    await gateway.send_image(event.chat_id, attachment.path, caption=attachment.caption)
                else:
                    await gateway.send_file(event.chat_id, attachment.path, caption=attachment.caption, filename=attachment.filename)
            recipe = self.recipes.record_success(event.platform, event.user_id, "meteogram", p, point)
            await self._send_meteogram_recipe(event, gateway, recipe)
            return True
        except Exception as exc:
            stop.set()
            await task
            await gateway.edit_text(event.chat_id, status.message_id, f"Ошибка метеограммы: {exc}")
            return False
        finally:
            stop.set()
            if not task.done():
                await task
            if result is not None:
                cleanup_product_result(result)
