from __future__ import annotations

"""Common WeatherNext 3 flow for MAX/VK and future messenger adapters."""

import asyncio
import os
from threading import Lock, Event
from dataclasses import replace
from .weathernext3_cards import CardStore
from typing import Any, Callable

from geocode import GeoPoint
from weathernext3_provider import WeatherNext3Error

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
    ('point', '🌡 Прогноз'), ('meteogram', '📊 Метеограмма'),
    ('cloudgram', '☁ Облака по времени'), ('precip_compare', '🌧 Сравнить осадки'),
    ('profile', '📈 Профиль'), ('aero', '🧾 Аэродиаграмма'), ('windgram', '🟦 Срок × уровень'),
    ('clouds', '🗺 Общая облачность'), ('cloud_layers', '🗺 Ярусы облаков'),
    ('precip_native', '🌧 Основные осадки'), ('precip_imerg', '🛰 Осадки IMERG'),
    ('precip_experimental', '🧪 Осадки эксперимент'), ('combo', '🗺 Облака+осадки'),
    ('temperature', '🌡 Карта T2'), ('temperature_spread', '↔ Разброс T2'),
    ('wind100', '💨 Ветер 100 м'), ('solar', '☀ Солнечная радиация'),
)


def _point_from_location(item: Any) -> GeoPoint:
    return GeoPoint(float(item.lat), float(item.lon), str(item.label), str(getattr(item, "source", "saved")))


def _card_text(point: Any, params: dict[str, Any]) -> str:
    p = normalize_wn3_params(params)
    kind = p["kind"]
    if kind in {"point", "profile", "aero"}:
        detail = f"Срок: +{p['hours']} ч"
    elif kind in {"meteogram", "cloudgram", "precip_compare"}:
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
        f"Статистика: {p['stat']} · член: {p['member']} · модельный прогноз"
    )


def _card_keyboard(params: dict[str, Any], page: int = 0) -> UiKeyboard:
    p = normalize_wn3_params(params)
    rows = [[UiButton('▶ Построить', 'callback', encode_callback('wn3', 'run'))]]
    page = max(0, min(2, int(page)))
    choices = KIND_BUTTONS[page*8:(page+1)*8]
    for i in range(0, len(choices), 2):
        rows.append([UiButton(('✓ ' if key == p['kind'] else '') + label, 'callback', encode_callback('wn3', 'kind', key)) for key, label in choices[i:i+2]])
    rows.append([UiButton(f'Продукция {page+1}/3 →', 'callback', encode_callback('wn3', 'products', (page+1)%3))])
    if p['kind'] in {'point', 'profile', 'aero'} or (p['kind'] in MAP_KINDS and p['mode'] == 'single'):
        action = 'hours' if p['kind'] in {'point', 'profile', 'aero'} else 'lead'
        for values in ((1, 3, 6), (12, 24, 48)):
            rows.append([UiButton(f'+{h} ч', 'callback', encode_callback('wn3', action, h)) for h in values])
        rows.append([UiButton('Все сроки +1…+360', 'callback', encode_callback('wn3', 'page', 0))])
    elif p['kind'] in {'meteogram', 'cloudgram', 'precip_compare'}:
        rows.append([UiButton(f'{h} сут от init', 'callback', encode_callback('wn3', 'days', h)) for h in (1, 3, 5, 10, 15)])
    else:
        rows.append([UiButton(f'до +{h}', 'callback', encode_callback('wn3', 'to', h)) for h in (24, 48, 120, 360)])
        rows.append([UiButton(f'шаг {h}', 'callback', encode_callback('wn3', 'step', h)) for h in (1, 3, 6, 12)])
    rows.append([UiButton('⚙ Параметры', 'callback', encode_callback('wn3', 'options')),
                 UiButton('📍 Точка', 'callback', encode_callback('wn3', 'point'))])
    rows.append([UiButton('📋 Сценарии', 'callback', encode_callback('settings', 'recipes', 0)),
                 UiButton('🕒 Расписания', 'callback', encode_callback('schedule', 'open'))])
    rows.append([UiButton('Статус', 'callback', encode_callback('wn3', 'status')),
                 UiButton('✖ Отмена', 'callback', encode_callback('wn3', 'cancel'))])
    return UiKeyboard.from_rows(rows)


def _options_keyboard(params):
    p = normalize_wn3_params(params)
    rows = []
    def options(action, values):
        rows.append([UiButton(str(label), 'callback', encode_callback('wn3', action, value if value != '' else None)) for value, label in values])
    if p['kind'] in MAP_KINDS:
        options('mode', [('animation', 'Анимация'), ('single', 'Одна карта'), ('series', 'Серия PNG')])
        if p['kind'] != 'temperature_spread':
            options('stat', [(v, v) for v in ('mean', 'p10', 'p50', 'p90')])
        options('radius', [(v, f'{v} км') for v in (100, 150, 250, 400)])
    if p['kind'] in {'profile', 'aero', 'windgram'}:
        options('member', [('mean', 'Средний профиль'), ('0', 'Член 0')])
    if p['kind'] == 'windgram':
        options('param', [('wind', 'Ветер'), ('temp', 'Температура'), ('rh', 'RH')])
        options('top', [(500, 'до 500 гПа'), (100, 'до 100 гПа'), (50, 'до 50 гПа')])
    if p['kind'] == 'meteogram':
        options('format', [(v, v.upper()) for v in ('png', 'pdf', 'docx')])
    options('card', [('', '← Назад')])
    return UiKeyboard.from_rows(rows)


def _lead_keyboard(params, page):
    page = max(0, min(14, int(page)))
    action = 'hours' if params['kind'] in {'point', 'profile', 'aero'} else 'lead'
    values = list(range(page*24+1, min(361, page*24+25)))
    rows = [[UiButton(f'+{hour}', 'callback', encode_callback('wn3', action, hour)) for hour in values[i:i+4]] for i in range(0, len(values), 4)]
    rows.append([UiButton('←', 'callback', encode_callback('wn3', 'page', max(0, page-1))),
                 UiButton(f'{page+1}/15 →', 'callback', encode_callback('wn3', 'page', min(14, page+1)))])
    rows.append([UiButton('Назад', 'callback', encode_callback('wn3', 'card'))])
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
        self.wn3_cards = CardStore(self.recipes.path)
        self.wn3_jobs = {}
        self.wn3_semaphore = asyncio.Semaphore(max(1, int(os.getenv("MAX_CONCURRENT_WEATHERNEXT3", "2"))))

    async def handle(self, event, gateway):
        try:
            await super().handle(event, gateway)
        except ValueError as exc:
            await gateway.send_text(event.chat_id, f'Некорректные параметры: {str(exc)[:500]}')
        except WeatherNext3Error as exc:
            await gateway.send_text(event.chat_id, str(exc)[:700])

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
            "GFS и WeatherNext 3: профили, метеограммы, карты. WN3: облака, осадки и статистики ансамбля.\n"
            "Обе системы — модели, не наблюдения.",
            keyboard=UiKeyboard.from_rows(rows),
        )

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if (command == 'cancel' or text in {'✖ Отмена', 'Отмена', '/cancel'}) and (self._job_key(event) in self.wn3_jobs or (state is not None and state.product == 'weathernext3')):
            await self.cancel_wn3(event, gateway)
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            return
        if command == 'status' and (self._job_key(event) in self.wn3_jobs or (state is not None and state.product == 'weathernext3')):
            await self._wn3_status(event, gateway)
            return
        if not command and text and state is not None and state.product == "weathernext3" and state.step == "await_point":
            parsed = self.wn3_parser(text)
            defaults = ' '.join(f'{k}={v}' for k, v in normalize_wn3_params(state.params).items())
            merged = self.wn3_parser(defaults + ' ' + text)
            await self._resolve_wn3_point(event, gateway, parsed.location_query, {**merged.params, '_direct': parsed.direct_run})
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
        payload = event.callback_payload or ''
        if payload.startswith('w3|'):
            try:
                parts = payload.split('|')
                if len(parts) not in {3, 4} or len(payload.encode()) > 64:
                    raise ValueError('Некорректная кнопка WN3')
                point, params = self.wn3_cards.get(event, parts[1])
                self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product='weathernext3', step='params', point=point, params=params))
                event = replace(event, callback_payload=encode_callback('wn3', parts[2], parts[3] if len(parts) == 4 and parts[3] else None))
            except (ValueError, KeyError) as exc:
                await gateway.answer_callback(event)
                await gateway.send_text(event.chat_id, str(exc))
                return
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
        if data.scope == 'recipe':
            try:
                recipe = self.recipes.get(event.platform, event.user_id, int(data.value or ''))
            except ValueError:
                recipe = None
            if recipe is not None and recipe.product == 'weathernext3':
                await gateway.answer_callback(event)
                if data.action == 'toggle':
                    self.recipes.toggle_pinned(event.platform, event.user_id, recipe.recipe_id)
                point = GeoPoint(**recipe.point)
                if data.action == 'run':
                    await self._run_wn3(event, gateway, point, recipe.params)
                else:
                    await self._show_wn3_card(event, gateway, point, recipe.params)
                return
        if data.scope != "wn3":
            await super()._callback(event, gateway)
            return

        await gateway.answer_callback(event)
        if data.action == 'cancel':
            await self.cancel_wn3(event, gateway)
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            return
        if data.action == 'status':
            await self._wn3_status(event, gateway)
            return
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
        if data.action in {'card', 'options', 'page', 'products'}:
            keyboard = _options_keyboard(p) if data.action == 'options' else _lead_keyboard(p, int(data.value or 0)) if data.action == 'page' else _card_keyboard(p, int(data.value or 0) if data.action == 'products' else 0)
            token = self.wn3_cards.save(event, state.point, p)
            await gateway.send_text(event.chat_id, _card_text(state.point, p), keyboard=self.wn3_cards.keyboard(token, keyboard))
            return
        if data.action == "kind":
            p["kind"] = str(data.value)
            p["format"], p["member"], p["stat"] = "png", "mean", "mean"
            p["format"] = "png"
            p["stat"] = "mean"
            p["member"] = "mean"
            p["param"] = "wind"
        elif data.action in {"hours", "days", "to", "step", "radius", "top"}:
            p[data.action] = int(data.value or 0)
        elif data.action in {"stat", "member", "param", "format"}:
            p[data.action] = str(data.value)
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
            rows = [[UiButton(_short_label(point), "callback", self.wn3_cards.payload(self.wn3_cards.save(event, point, normalize_wn3_params(params)), "run" if params.get("_direct") else "card"))] for point in state.candidates]
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
        token = self.wn3_cards.save(event, point, state.params)
        await gateway.send_text(event.chat_id, _card_text(point, state.params), keyboard=self.wn3_cards.keyboard(token, _card_keyboard(state.params)))

    @staticmethod
    def _job_key(event):
        return event.platform, event.user_id, event.chat_id

    async def _wn3_status(self, event, gateway):
        from weathernext3_status import status_text
        job = self.wn3_jobs.get(self._job_key(event))
        await gateway.send_text(event.chat_id, status_text() + ('\nЗапрос отменён, завершается сетевой этап.' if job and job['cancel'].is_set() else '\nЗапрос выполняется.' if job else '\nАктивного запроса нет.'))

    async def cancel_wn3(self, event, gateway):
        job = self.wn3_jobs.get(self._job_key(event))
        if job is not None:
            job['cancel'].set()
            job['stop'].set()
            if 'status' not in job:
                await gateway.send_text(event.chat_id, 'Отмена WN3 принята.')
                return
            await gateway.edit_text(event.chat_id, job['status'].message_id, 'WeatherNext 3: отменено. Новые файлы отправлены не будут; начатый сетевой запрос может завершиться по тайм-ауту.')
        else:
            await gateway.send_text(event.chat_id, 'WeatherNext 3: выбор сброшен.')

    async def wn3_wait_idle(self):
        tasks = [job['task'] for job in list(self.wn3_jobs.values()) if 'task' in job]
        if tasks:
            await asyncio.gather(*tasks)

    async def shutdown_wn3(self):
        for job in self.wn3_jobs.values():
            job['cancel'].set()
            job['stop'].set()
        await self.wn3_wait_idle()

    async def _run_wn3(self, event, gateway, point, params):
        key = self._job_key(event)
        if key in self.wn3_jobs:
            await gateway.send_text(event.chat_id, 'Запрос WN3 уже выполняется. /status или /cancel; повторный запуск не создан.')
            return
        p = normalize_wn3_params(params)
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product='weathernext3', step='params', point=point, params=p))
        # Reserve before the first await to make simultaneous callbacks idempotent.
        job = {'cancel': Event(), 'stop': asyncio.Event()}
        self.wn3_jobs[key] = job
        try:
            token = self.wn3_cards.save(event, point, p)
            keyboard = UiKeyboard.from_rows([[UiButton('✖ Отмена', 'callback', self.wn3_cards.payload(token, 'cancel'))]])
            job['status'] = await gateway.send_text(event.chat_id, '⏳ WeatherNext 3 · ожидаю свободный слот', keyboard=keyboard)
        except BaseException:
            self.wn3_jobs.pop(key, None)
            raise
        if job['cancel'].is_set():
            await gateway.edit_text(event.chat_id, job['status'].message_id, 'WeatherNext 3: отменено.')
            self.wn3_jobs.pop(key, None)
            return
        async def execute():
            result = None
            snapshot = {'event': ProgressEvent('check', 'Проверяю источник')}
            lock = Lock()
            def progress(value):
                if job['cancel'].is_set():
                    raise RuntimeError('Запрос WN3 отменён')
                with lock:
                    snapshot['event'] = value
            async def report():
                previous = ''
                while not job['stop'].is_set():
                    with lock:
                        text = _progress_text(point, p, snapshot['event'])
                    if text != previous:
                        try:
                            await gateway.edit_text(event.chat_id, job['status'].message_id, text, keyboard=keyboard)
                            previous = text
                        except Exception:
                            pass
                    try:
                        await asyncio.wait_for(job['stop'].wait(), timeout=max(1.0, self.progress_interval_seconds))
                    except asyncio.TimeoutError:
                        pass
            reporter = None
            try:
                async with self.wn3_semaphore:
                    if job['cancel'].is_set():
                        return
                    reporter = asyncio.create_task(report())
                    # Do not cancel the worker task and release capacity while its thread is alive.
                    def blocking():
                        from weathernext3_cancel import cancellation
                        with cancellation(job['cancel']):
                            return self.wn3_builder(point, p['kind'], progress_callback=progress,
                                                    **{k: v for k, v in p.items() if k != 'kind'})
                    worker = asyncio.create_task(asyncio.to_thread(blocking))
                    try:
                        result = await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        job['cancel'].set()
                        try:
                            result = await worker
                        except Exception:
                            pass
                        raise
                job['stop'].set()
                if reporter:
                    await reporter
                if job['cancel'].is_set():
                    return
                await gateway.edit_text(event.chat_id, job['status'].message_id, result.summary)
                for attachment in result.attachments:
                    if job['cancel'].is_set():
                        return
                    if attachment.kind == 'image':
                        await gateway.send_image(event.chat_id, attachment.path, caption=attachment.caption)
                    elif attachment.kind == 'animation':
                        await gateway.send_animation(event.chat_id, attachment.path, caption=attachment.caption)
                    else:
                        await gateway.send_file(event.chat_id, attachment.path, caption=attachment.caption, filename=attachment.filename)
                if job['cancel'].is_set():
                    return
                self.locations.remember(event.platform, event.user_id, point, activate=True)
                if getattr(gateway, 'remember_point', None):
                    gateway.remember_point(event.user_id, point)
                recipe = self.recipes.record_success(event.platform, event.user_id, 'weathernext3', p, point)
                await gateway.send_text(event.chat_id, 'WeatherNext 3: сценарий сохранён. При повторе будет выбран новый опубликованный цикл.', keyboard=UiKeyboard.from_rows([
                    [UiButton('Повторить', 'callback', encode_callback('recipe', 'run', recipe.recipe_id)),
                     UiButton('Закрепить/открепить', 'callback', encode_callback('recipe', 'toggle', recipe.recipe_id))],
                    [UiButton('По расписанию', 'callback', encode_callback('schedule', 'recipe', recipe.recipe_id))]]))
            except Exception as exc:
                job['stop'].set()
                if reporter:
                    await reporter
                if not job['cancel'].is_set():
                    await gateway.edit_text(event.chat_id, job['status'].message_id, f'Ошибка WeatherNext 3: {str(exc)[:700]}')
            finally:
                job['stop'].set()
                if reporter:
                    await reporter
                if result is not None:
                    cleanup_product_result(result)
                if self.wn3_jobs.get(key) is job:
                    self.wn3_jobs.pop(key, None)
        job['task'] = asyncio.create_task(execute(), name=f'wn3-{event.platform}-{event.user_id}')
