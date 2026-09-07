from __future__ import annotations

"""Common WeatherNext 3 flow for MAX/VK and future messenger adapters."""

import asyncio
import os
from threading import Lock
from typing import Any, Callable

from geocode import GeoPoint

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .profile_service import cleanup_product_result
from .router import RouterDependencies, _command_args, _short_label
from .schedule_router import ScheduleMessengerRouter
from .settings_router import _recipe_label
from .state import FlowState
from .weathernext3_service import (
    DEFAULT_WN3_PARAMS,
    MAP_KINDS,
    ParsedWeatherNext3Input,
    build_weathernext3_product_result,
    normalize_wn3_params,
    parse_weathernext3_input,
    wn3_kind_title,
)

KIND_BUTTONS = (
    ("point", "🌡 Прогноз"), ("meteogram", "📊 Метеограмма"),
    ("clouds", "☁ Облачность"), ("cloud_layers", "☁ Слои"),
    ("precip_native", "🌧 Native"), ("precip_imerg", "🛰 IMERG"),
    ("precip_experimental", "🧪 Experimental"), ("combo", "🗺 Облака+осадки"),
)


def _point_from_location(item: Any) -> GeoPoint:
    return GeoPoint(float(item.lat), float(item.lon), str(item.label), str(getattr(item, "source", "saved")))


def _card_text(point: Any, params: dict[str, Any]) -> str:
    p = normalize_wn3_params(params)
    kind = p["kind"]
    if kind == "point":
        detail = f"Срок: +{p['hours']} ч"
    elif kind == "meteogram":
        detail = f"Период: {p['days']} суток · p10/p25/p50/p75/p90"
    else:
        mode = {"animation": "анимация", "single": "одна карта", "series": "серия PNG"}[p["mode"]]
        detail = (
            f"{mode} · +{p['from']}…+{p['to']} ч · шаг {p['step']} ч · "
            f"радиус {int(p['radius'])} км"
        )
    return (
        "🛰 WeatherNext 3\n"
        f"📍 {getattr(point, 'label', 'точка')} · {float(point.lat):.4f}, {float(point.lon):.4f}\n"
        f"Продукт: {wn3_kind_title(kind)}\n{detail}\n\n"
        "64-членный ансамбль. T/Td: station head 0.05°; surface/maps: 0.1°."
    )


def _card_keyboard(params: dict[str, Any]) -> UiKeyboard:
    p = normalize_wn3_params(params)
    rows: list[list[UiButton]] = [
        [UiButton("▶ Построить", "callback", encode_callback("wn3", "run"))],
    ]
    for index in range(0, len(KIND_BUTTONS), 2):
        row = []
        for key, label in KIND_BUTTONS[index:index + 2]:
            row.append(UiButton(("✓ " if key == p["kind"] else "") + label, "callback", encode_callback("wn3", "kind", key)))
        rows.append(row)

    if p["kind"] == "point":
        rows.append([UiButton(("✓ " if p["hours"] == value else "") + f"+{value}ч", "callback", encode_callback("wn3", "hours", value)) for value in (6, 12, 24, 48)])
    elif p["kind"] == "meteogram":
        rows.append([UiButton(("✓ " if p["days"] == value else "") + f"{value} сут", "callback", encode_callback("wn3", "days", value)) for value in (3, 5, 10, 15)])
    else:
        if p["mode"] == "single":
            rows.append([UiButton(("✓ " if p["from"] == value else "") + f"+{value}ч", "callback", encode_callback("wn3", "lead", value)) for value in (6, 12, 24, 48)])
        else:
            rows.append([UiButton(("✓ " if p["to"] == value else "") + f"до +{value}", "callback", encode_callback("wn3", "to", value)) for value in (24, 48, 72, 120)])
            rows.append([UiButton(("✓ " if p["step"] == value else "") + f"шаг {value}", "callback", encode_callback("wn3", "step", value)) for value in (1, 3, 6, 12)])
        rows.append([UiButton(("✓ " if int(p["radius"]) == value else "") + f"{value} км", "callback", encode_callback("wn3", "radius", value)) for value in (100, 150, 250, 400)])
        rows.append([
            UiButton(("✓ " if p["mode"] == "animation" else "") + "Анимация", "callback", encode_callback("wn3", "mode", "animation")),
            UiButton(("✓ " if p["mode"] == "single" else "") + "Одна карта", "callback", encode_callback("wn3", "mode", "single")),
            UiButton(("✓ " if p["mode"] == "series" else "") + "PNG", "callback", encode_callback("wn3", "mode", "series")),
        ])
    rows.append([
        UiButton("📍 Другая точка", "callback", encode_callback("wn3", "point")),
        UiButton("🏠 Главное меню", "callback", encode_callback("wn3", "home")),
    ])
    return UiKeyboard.from_rows(rows)


def _progress_text(point: Any, params: dict[str, Any], event: ProgressEvent) -> str:
    kind = normalize_wn3_params(params)["kind"]
    if event.stage in {"check", "fetch_start"}:
        body = "1/4 Проверяю опубликованный init…"
    elif event.stage in {"fetch", "format"}:
        body = f"2/4 {event.message or 'Получаю BigQuery данные'}…"
    elif event.stage in {"render", "plot_start", "plot_frame"}:
        if event.current and event.total:
            body = f"3/4 Рисую кадры {event.current}/{event.total}…"
        else:
            body = f"3/4 {event.message or 'Рисую продукт'}…"
    elif event.stage in {"encode", "done"}:
        body = "4/4 Кодирую анимацию…" if event.stage == "encode" else "4/4 Готово"
    else:
        body = event.message or "Выполняю расчёт…"
    return f"⏳ WeatherNext 3 · {wn3_kind_title(kind)}\n📍 {getattr(point, 'label', '')}\n{body}"


class WeatherNext3MessengerRouter(ScheduleMessengerRouter):
    def __init__(
        self,
        dependencies: RouterDependencies,
        *,
        wn3_builder: Callable[..., Any] = build_weathernext3_product_result,
        wn3_parser: Callable[[str], ParsedWeatherNext3Input] = parse_weathernext3_input,
        **kwargs: Any,
    ) -> None:
        super().__init__(dependencies, **kwargs)
        self.wn3_builder = wn3_builder
        self.wn3_parser = wn3_parser
        self.wn3_semaphore = asyncio.Semaphore(max(1, int(os.getenv("MAX_CONCURRENT_WEATHERNEXT3", "2"))))

    @classmethod
    def default(cls, **kwargs: Any) -> "WeatherNext3MessengerRouter":
        from geocode_choices import search_location_candidates
        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    async def _start(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        self._sync_locations_from_recipes(event)
        active = self.locations.active(event.platform, event.user_id)
        rows: list[list[UiButton]] = []
        for recipe in self.recipes.quick(event.platform, event.user_id, limit=2):
            rows.append([UiButton("▶ " + _recipe_label(recipe)[2:], "callback", encode_callback("recipe", "run", recipe.recipe_id))])
        rows.extend([
            [UiButton("📈 Профиль", "callback", encode_callback("product", "open", "profile")), UiButton("🧾 Аэродиаграмма", "callback", encode_callback("product", "open", "aero"))],
            [UiButton("🟦 Срок × уровень", "callback", encode_callback("product", "open", "windgram")), UiButton("☁ Облака", "callback", encode_callback("product", "open", "cloudgram"))],
            [UiButton("🗺 Карта GFS", "callback", encode_callback("product", "open", "map")), UiButton("📊 Метеограмма", "callback", encode_callback("product", "open", "meteogram"))],
            [UiButton("🛰 WeatherNext 3", "callback", encode_callback("product", "open", "weathernext3")), UiButton("✈ Маршрут", "callback", encode_callback("product", "open", "route"))],
            [UiButton("🕒 Расписания", "callback", encode_callback("schedule", "open")), UiButton("⚙ Настройки", "callback", encode_callback("settings", "open"))],
            [UiButton("📍 Геолокация", "request_location")],
        ])
        point = f"\n📍 Основная точка: {active.label}" if active else ""
        await gateway.send_text(
            event.chat_id,
            "🌦 Модельные прогнозы · GFS 0.25 + WeatherNext 3"
            f"{point}\n"
            "GFS: вертикальные/маршрутные продукты. WeatherNext 3: ансамблевая поверхность, облачность и осадки.\n"
            "Обе системы — модели, не наблюдения.",
            keyboard=UiKeyboard.from_rows(rows),
        )

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if not command and text and state is not None and state.product == "weathernext3" and state.step == "await_point":
            await self._resolve_wn3_point(event, gateway, text, state.params)
            return
        if command in {"wn3", "weathernext3"}:
            args = _command_args(text)
            if not args:
                active = self.locations.active(event.platform, event.user_id)
                if active is not None:
                    await self._show_wn3_card(event, gateway, _point_from_location(active), dict(DEFAULT_WN3_PARAMS))
                else:
                    await self._ask_wn3_point(event, gateway, dict(DEFAULT_WN3_PARAMS))
                return
            active = self.locations.active(event.platform, event.user_id)
            if active is not None and (args.startswith("+") or any(args.startswith(prefix) for prefix in ("kind=", "days=", "hours=", "lead=", "from=", "to=", "step=", "radius=", "mode=", "basemap=", "base="))):
                parsed = self.wn3_parser(f"0 0 {args}")
                await self._run_wn3(event, gateway, _point_from_location(active), parsed.params)
                return
            await self._resolve_wn3_direct(event, gateway, args)
            return
        await super()._text(event, gateway)

    async def _location(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "weathernext3":
            await super()._location(event, gateway)
            return
        if event.location is None:
            await gateway.send_text(event.chat_id, "В сообщении нет координат.")
            return
        point = GeoPoint(float(event.location.lat), float(event.location.lon), f"геолокация {event.location.lat:.4f}, {event.location.lon:.4f}", event.platform)
        await self._show_wn3_card(event, gateway, point, state.params)

    async def _callback(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        try:
            data = decode_callback(event.callback_payload or "")
        except CallbackCodecError:
            await super()._callback(event, gateway)
            return
        if data.scope == "product" and data.action == "open" and data.value in {"weathernext3", "wn3"}:
            await gateway.answer_callback(event)
            active = self.locations.active(event.platform, event.user_id)
            if active is not None:
                await self._show_wn3_card(event, gateway, _point_from_location(active), dict(DEFAULT_WN3_PARAMS))
            else:
                await self._ask_wn3_point(event, gateway, dict(DEFAULT_WN3_PARAMS))
            return
        if data.scope != "wn3":
            await super()._callback(event, gateway)
            return

        await gateway.answer_callback(event)
        if data.action == "home":
            await self._start(event, gateway)
            return
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "weathernext3":
            await gateway.send_text(event.chat_id, "Сценарий WeatherNext 3 устарел. Откройте /wn3.")
            return
        if data.action == "place":
            try:
                point = state.candidates[int(data.value or "")]
            except (ValueError, IndexError):
                await gateway.send_text(event.chat_id, "Вариант точки устарел. Откройте /wn3 заново.")
                return
            if state.params.pop("_direct", False):
                await self._run_wn3(event, gateway, point, state.params)
            else:
                await self._show_wn3_card(event, gateway, point, state.params)
            return
        if data.action == "point":
            await self._ask_wn3_point(event, gateway, state.params)
            return
        if state.point is None:
            await gateway.send_text(event.chat_id, "Точка WeatherNext 3 потеряна. Откройте /wn3 заново.")
            return

        p = normalize_wn3_params(state.params)
        if data.action == "kind":
            p["kind"] = str(data.value)
        elif data.action in {"hours", "days", "to", "step", "radius"}:
            p[data.action] = int(data.value or 0)
        elif data.action == "lead":
            lead = int(data.value or 24)
            p["mode"] = "single"
            p["from"] = lead
            p["to"] = lead
        elif data.action == "mode":
            p["mode"] = str(data.value)
            if p["mode"] == "single":
                p["from"] = min(max(1, int(p["hours"])), int(p["to"]))
                p["to"] = p["from"]
            elif p["to"] <= p["from"]:
                p["from"], p["to"] = 1, 48
        elif data.action == "run":
            await self._run_wn3(event, gateway, state.point, p)
            return
        else:
            await gateway.send_text(event.chat_id, "Кнопка WeatherNext 3 устарела. Откройте /wn3.")
            return
        await self._show_wn3_card(event, gateway, state.point, p)

    async def _ask_wn3_point(self, event: NormalizedEvent, gateway: MessengerGateway, params: dict[str, Any]) -> None:
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product="weathernext3", step="await_point", params=normalize_wn3_params(params)))
        await gateway.send_text(
            event.chat_id,
            "🛰 WeatherNext 3\nУкажите город, координаты или отправьте геолокацию.",
            keyboard=UiKeyboard.from_rows([[UiButton("📍 Отправить геолокацию", "request_location")], [UiButton("🏠 Главное меню", "callback", encode_callback("wn3", "home"))]]),
        )

    async def _resolve_wn3_direct(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str) -> None:
        try:
            parsed = self.wn3_parser(raw)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка WeatherNext 3: {exc}")
            return
        await self._resolve_wn3_point(event, gateway, parsed.location_query, {**parsed.params, "_direct": parsed.direct_run})

    async def _resolve_wn3_point(self, event: NormalizedEvent, gateway: MessengerGateway, query: str, params: dict[str, Any]) -> None:
        try:
            candidates = await asyncio.to_thread(self.deps.geocode, query, 5)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Не удалось определить точку: {exc}")
            return
        if not candidates:
            await gateway.send_text(event.chat_id, "Точка не найдена. Уточните город или используйте координаты.")
            return
        if len(candidates) > 1:
            state = FlowState(product="weathernext3", step="choose_place", candidates=list(candidates[:5]), params=dict(params))
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [[UiButton(_short_label(point), "callback", encode_callback("wn3", "place", index))] for index, point in enumerate(state.candidates)]
            rows.append([UiButton("🏠 Главное меню", "callback", encode_callback("wn3", "home"))])
            await gateway.send_text(event.chat_id, "Найдено несколько точек. Выберите нужную:", keyboard=UiKeyboard.from_rows(rows))
            return
        point = candidates[0]
        if params.pop("_direct", False):
            await self._run_wn3(event, gateway, point, params)
        else:
            await self._show_wn3_card(event, gateway, point, params)

    async def _show_wn3_card(self, event: NormalizedEvent, gateway: MessengerGateway, point: Any, params: dict[str, Any]) -> None:
        state = FlowState(product="weathernext3", step="params", point=point, params=normalize_wn3_params(params))
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await gateway.send_text(event.chat_id, _card_text(point, state.params), keyboard=_card_keyboard(state.params))

    async def _run_wn3(self, event: NormalizedEvent, gateway: MessengerGateway, point: Any, params: dict[str, Any]) -> None:
        p = normalize_wn3_params(params)
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product="weathernext3", step="params", point=point, params=p))
        status = await gateway.send_text(event.chat_id, _progress_text(point, p, ProgressEvent("check", "")))
        snapshot = {"event": ProgressEvent("check", "")}
        lock = Lock()
        stop = False
        last_text = ""

        def progress(value: ProgressEvent) -> None:
            with lock:
                snapshot["event"] = value

        async def reporter() -> None:
            nonlocal last_text
            while not stop:
                with lock:
                    value = snapshot["event"]
                text = _progress_text(point, p, value)
                if text != last_text:
                    try:
                        await gateway.edit_text(event.chat_id, status.message_id, text)
                        last_text = text
                    except Exception:
                        pass
                await asyncio.sleep(self.progress_interval_seconds)

        task = asyncio.create_task(reporter())
        result = None
        try:
            async with self.wn3_semaphore:
                result = await asyncio.to_thread(self.wn3_builder, point, p["kind"], progress_callback=progress, **{k: v for k, v in p.items() if k != "kind"})
            stop = True
            await task
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
            try:
                self.locations.remember(event.platform, event.user_id, point, activate=True)
            except Exception:
                pass
        except Exception as exc:
            stop = True
            await task
            await gateway.edit_text(event.chat_id, status.message_id, f"Ошибка WeatherNext 3: {exc}")
        finally:
            if result is not None:
                cleanup_product_result(result)
