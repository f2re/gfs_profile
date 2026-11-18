"""One regional icing flow shared by Telegram, MAX and VK.

Every navigation button carries the complete request. Only running jobs live
in memory; an old product/lead/export button still works after a restart.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import struct
import time
from dataclasses import replace
from threading import Event, Lock
from typing import Any, Callable

from gfs_core import GfsProfileError, GfsRun, canonical_leads
from geocode import GeoPoint
from icing_config import (
    ICING_LEVELS_HPA, ICING_PRODUCTS, IcingRequest,
    parse_icing_input, validate_icing_request,
)
from icing_io import select_icing_run
from icing_presentation import DISPLAY_UNITS, PRODUCT_HINTS, SOURCE_LABEL, method_help_text
from forecast_time import forecast_lead_label, forecast_time_lines, run_time_label

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .icing_service import build_icing_product_result
from .cloudgram_router import CloudgramMessengerRouter
from .profile_service import cleanup_product_result
from .router import LEAD_PAGE_SIZE, QUICK_LEADS, RouterDependencies, _command_args, _short_label, menu_keyboard
from .state import FlowState

LOG = logging.getLogger(__name__)
# Fixed wire ordering, independent of display order and dict ordering.
PRODUCT_CODES = (
    "slw_level", "godske_sum", "godske_max", "godske_base", "godske_top",
    "godske_count", "slw_max", "slw_height", "slwp", "agreement",
)
_WIRE = struct.Struct("!8sBHHBffffIB")
# Version 2 adds a point without exceeding Telegram's 64-byte callback limit.
# Six signed 24-bit coordinates at 1e-4 degrees replace the old bbox floats.
_POINT_WIRE_HEAD = struct.Struct("!B8sBHHB")
_POINT_WIRE_TAIL = struct.Struct("!IB")
_POINT_WIRE_VERSION = 2
_POINT_WIRE_SIZE = _POINT_WIRE_HEAD.size + 18 + _POINT_WIRE_TAIL.size
_POINT_STEPS = {"await_point", "point_search", "choose_point"}

METHOD_TEXT = method_help_text()
_RUN_CACHE_SECONDS = 90.0
_RUN_CHECK_TIMEOUT = 35.0


def icing_owner(event: NormalizedEvent) -> bytes:
    value = "\0".join((event.platform, str(event.user_id), str(event.chat_id)))
    return hashlib.blake2s(value.encode("utf-8"), digest_size=8).digest()


def icing_payload(action: str, request: IcingRequest, *, owner: bytes = b"\0" * 8) -> str:
    request = validate_icing_request(request)
    fields = (owner, PRODUCT_CODES.index(request.product), request.lead, request.level_hpa,
              0 if request.contour_step == 100 else 1)
    run_fields = (int(request.run.date) if request.run else 0,
                  int(request.run.cycle) if request.run else 255)
    if request.point is None:
        # Continue emitting the original format for maps without a marker.
        raw = _WIRE.pack(*fields, *request.bbox, *run_fields)
    else:
        coordinates = b"".join(int(round(value * 10000)).to_bytes(3, "big", signed=True)
                               for value in (*request.bbox, *request.point))
        raw = (_POINT_WIRE_HEAD.pack(_POINT_WIRE_VERSION, *fields) + coordinates
               + _POINT_WIRE_TAIL.pack(*run_fields))
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return encode_callback("icing", action, token)


def icing_request_from_payload(payload: str, *, owner: bytes | None = None) -> tuple[str, IcingRequest]:
    data = decode_callback(payload)
    if data.scope != "icing" or not data.value:
        raise ValueError("Кнопка обледенения не содержит параметров")
    try:
        raw = base64.b64decode(data.value + "=" * (-len(data.value) % 4), altchars=b"-_", validate=True)
        point = None
        if len(raw) == _WIRE.size:
            bound_owner, product, lead, level, contour, west, east, south, north, date, cycle = _WIRE.unpack(raw)
        elif len(raw) == _POINT_WIRE_SIZE:
            version, bound_owner, product, lead, level, contour = _POINT_WIRE_HEAD.unpack_from(raw)
            if version != _POINT_WIRE_VERSION:
                raise ValueError("Неизвестная версия параметров карты")
            start = _POINT_WIRE_HEAD.size
            values = [int.from_bytes(raw[i:i + 3], "big", signed=True) / 10000
                      for i in range(start, start + 18, 3)]
            west, east, south, north, lat, lon = values
            point = (lat, lon)
            date, cycle = _POINT_WIRE_TAIL.unpack_from(raw, start + 18)
        else:
            raise ValueError("Некорректный размер параметров")
        if owner is not None and not hmac.compare_digest(owner, bound_owner):
            raise ValueError("Эта кнопка относится к другому пользователю или чату. Откройте /icing")
        if product >= len(PRODUCT_CODES) or contour not in (0, 1):
            raise ValueError("Неизвестные параметры карты")
        if (date == 0) != (cycle == 255):
            raise ValueError("Некорректный цикл")
        request = IcingRequest(
            product=PRODUCT_CODES[product], lead=lead, level_hpa=level,
            contour_step=100 if contour == 0 else 500,
            bbox=tuple(round(value, 4) for value in (west, east, south, north)),
            run=GfsRun(f"{date:08d}", f"{cycle:02d}") if date else None,
            point=point,
        )
        return data.action, validate_icing_request(request)
    except (binascii.Error, struct.error, IndexError, TypeError) as exc:
        raise ValueError("Параметры кнопки повреждены. Откройте /icing") from exc


def _button(text: str, action: str, request: IcingRequest) -> UiButton:
    return UiButton(text, "callback", icing_payload(action, request))


def _owned_keyboard(keyboard: UiKeyboard, event: NormalizedEvent) -> UiKeyboard:
    rows = []
    for row in keyboard.rows:
        bound = []
        for button in row:
            if button.action == "callback" and (button.payload or "").startswith("v1|icing|"):
                action, request = icing_request_from_payload(button.payload)
                button = replace(button, payload=icing_payload(action, request, owner=icing_owner(event)))
            bound.append(button)
        rows.append(bound)
    return menu_keyboard(rows)


def _region_text(request: IcingRequest) -> str:
    west, east, south, north = request.bbox
    text = f"Район: {west:g}…{east:g}° долготы; {south:g}…{north:g}° широты"
    if request.point is not None:
        text += f"\nВыбранный пункт: {request.point[0]:.4f}, {request.point[1]:.4f}"
    return text


def _point_keyboard(request: IcingRequest) -> UiKeyboard:
    rows = [[UiButton("Геолокация", "request_location")]]
    controls = [_button("Назад к карте", "c", request)]
    if request.point is not None:
        controls.append(_button("Убрать пункт", "d", request))
    rows.append(controls)
    return menu_keyboard(rows)


def icing_menu_keyboard(request: IcingRequest) -> UiKeyboard:
    return menu_keyboard([
        [_button("Вода на уровне", "c", replace(request, product="slw_level")),
         _button("Толщина слоёв", "g", request)],
        [_button("Границы слоёв", "b", request),
         _button("Максимум по высоте", "w", request)],
        [_button("Запас воды", "c", replace(request, product="slwp")),
         _button("Сравнение методов", "c", replace(request, product="agreement"))],
        [_button("Пункт на карте", "p", request)],
        [_button("Как читать карту", "a", request), _button("Отмена", "x", request)],
    ])


def icing_card_text(request: IcingRequest) -> str:
    lines = [f"❄ {ICING_PRODUCTS[request.product]}", PRODUCT_HINTS[request.product],
             forecast_time_lines(request.run, request.lead) if request.run else "Проверяю дату цикла GFS…",
             _region_text(request)]
    if request.product == "slw_level":
        lines.append(f"Уровень: {request.level_hpa} гПа · {DISPLAY_UNITS[request.product]}")
    else:
        lines.append("Диапазон: 1000–300 гПа, только надземные уровни")
        if request.product != "agreement":
            lines.append(f"Шкала: {DISPLAY_UNITS[request.product]}")
    if request.product in {"godske_sum", "godske_max"}:
        lines.append(f"Изолинии через {request.contour_step // 10} дам")
    lines.append(SOURCE_LABEL)
    return "\n".join(lines)


def icing_card_keyboard(request: IcingRequest) -> UiKeyboard:
    controls = [_button("Срок", "l0", request)]
    if request.product == "slw_level":
        controls.append(_button("Уровень", "z", request))
    elif request.product in {"godske_sum", "godske_max"}:
        controls.append(_button("Изолинии", "o", request))
    return menu_keyboard([
        [_button("Построить", "r", request)], controls,
        [_button("Пункт на карте", "p", request)],
        [_button("Другой продукт", "h", request), _button("Как читать карту", "m", request)],
        [_button("Обновить данные", "u", replace(request, run=None)), _button("Отмена", "x", request)],
    ])


def icing_lead_keyboard(request: IcingRequest, page: int = 0) -> UiKeyboard:
    leads = canonical_leads()
    pages = (len(leads) + LEAD_PAGE_SIZE - 1) // LEAD_PAGE_SIZE
    page = max(0, min(int(page), pages))
    values = [v for v in QUICK_LEADS if v in leads] if page == 0 else leads[(page - 1) * LEAD_PAGE_SIZE:page * LEAD_PAGE_SIZE]
    rows = [[_button(forecast_lead_label(request.run, v) if request.run else f"+{v} ч",
                     "t", replace(request, lead=v)) for v in values[i:i + 2]]
            for i in range(0, len(values), 2)]
    if page == 0:
        rows.append([_button("Все сроки до +384", "l1", request)])
    else:
        nav = []
        if page > 1:
            nav.append(_button("‹", f"l{page - 1}", request))
        if page < pages:
            nav.append(_button(f"{page}/{pages} ›", f"l{page + 1}", request))
        if nav:
            rows.append(nav)
        rows.append([_button("Быстрые сроки", "l0", request)])
    rows.append([_button("Назад", "c", request)])
    return menu_keyboard(rows)


def _result_keyboard(request: IcingRequest) -> UiKeyboard:
    leads = canonical_leads()
    index = leads.index(request.lead)
    nav = []
    if index:
        previous = leads[index - 1]
        nav.append(_button("‹ " + forecast_lead_label(request.run, previous) if request.run else f"‹ +{previous} ч",
                           "n", replace(request, lead=previous)))
    if index + 1 < len(leads):
        following = leads[index + 1]
        nav.append(_button(forecast_lead_label(request.run, following) + " ›" if request.run else f"+{following} ч ›",
                           "n", replace(request, lead=following)))
    rows = [nav] if nav else []
    rows.extend([
        [_button("PNG целиком", "i", request), _button("Как читать карту", "m", request)],
        [_button("Другой продукт", "h", request), _button("Параметры", "c", request)],
        [_button("Пункт на карте", "p", request),
         _button("Обновить данные", "u", replace(request, run=None))],
    ])
    return menu_keyboard(rows)


def _progress_text(request: IcingRequest, progress: ProgressEvent) -> str:
    message = progress.message or "Выполняю расчёт"
    return f"❄ {ICING_PRODUCTS[request.product]} · +{request.lead} ч\n{message}"


class IcingMessengerRouter(CloudgramMessengerRouter):

    home_products = CloudgramMessengerRouter.home_products + ("icing",)

    def __init__(self, dependencies: RouterDependencies, *, icing_builder: Callable[..., Any] = build_icing_product_result,
                 icing_parser: Callable[[str], IcingRequest] = parse_icing_input,
                 icing_run_selector: Callable[[IcingRequest], Any] | None = None,
                 **kwargs: Any) -> None:
        super().__init__(dependencies, **kwargs)
        self.icing_builder = icing_builder
        self.icing_parser = icing_parser
        self.icing_run_selector = icing_run_selector or select_icing_run
        self._icing_builtin_selector = icing_run_selector is None
        self._icing_run_cache: dict[tuple, tuple[float, GfsRun]] = {}
        self._icing_run_lock = asyncio.Lock()
        self._icing_pending: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.icing_jobs: dict[tuple[str, str, str], dict[str, Any]] = {}

    @staticmethod
    def _icing_key(event: NormalizedEvent) -> tuple[str, str, str]:
        return event.platform, event.user_id, event.chat_id

    async def _text(self, event, gateway):
        command = (event.command or "").lower().lstrip("/")
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        active = self._icing_key(event) in self.icing_jobs or (state is not None and state.product == "icing")
        if active and command == "cancel":
            await self.cancel_icing(event, gateway)
            return
        if active and command == "status":
            await self._icing_status(event, gateway)
            return
        if command == "icing":
            raw = _command_args(event.text or "")
            try:
                request = self.icing_parser(raw)
                if raw:
                    await self._run_icing(event, gateway, request)
                else:
                    await self._icing_menu(event, gateway, request)
            except (GfsProfileError, ValueError) as exc:
                await gateway.send_text(event.chat_id, f"Параметры обледенения: {exc}")
            return
        if not command and state is not None and state.product == "icing":
            text = (event.text or "").strip()
            if text in {"Отмена", "✖ Отмена"}:
                await self.cancel_icing(event, gateway)
            elif state.step in _POINT_STEPS:
                request = state.params["request"]
                if text in {"Назад", "Назад к карте", "Отмена выбора пункта"}:
                    await self._icing_card(event, gateway, request)
                elif text == "Убрать пункт":
                    await self._icing_card(event, gateway, replace(request, point=None), note="Пункт убран с карты.")
                else:
                    await self._icing_find_point(event, gateway, request, text)
            else:
                await gateway.send_text(event.chat_id, "Выберите продукт и срок кнопками. Чтобы отметить город или координаты, откройте «Пункт на карте».")
            return
        await super()._text(event, gateway)

    async def _location(self, event, gateway):
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is not None and state.product == "icing" and state.step in _POINT_STEPS:
            request = state.params["request"]
            if event.location is None:
                await self._icing_point_prompt(event, gateway, request, note="В сообщении нет координат геолокации.")
            else:
                point = GeoPoint(event.location.lat, event.location.lon, "Геолокация", event.platform)
                await self._icing_accept_point(event, gateway, request, point)
            return
        await super()._location(event, gateway)

    async def _callback(self, event, gateway):
        try:
            data = decode_callback(event.callback_payload or "")
        except CallbackCodecError:
            await super()._callback(event, gateway)
            return
        if data.scope == "product" and data.action == "open" and data.value == "icing":
            await gateway.answer_callback(event)
            try:
                await self._icing_menu(event, gateway, self.icing_parser(""))
            except (GfsProfileError, ValueError) as exc:
                await gateway.send_text(event.chat_id, f"Параметры района: {exc}")
            return
        if data.scope != "icing":
            await super()._callback(event, gateway)
            return
        callback_state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        await gateway.answer_callback(event)
        if data.action != "x" and self.sessions.get(event.platform, event.user_id, event.chat_id) is not callback_state:
            # Cancelling or navigating while the transport acknowledges a button
            # must not let the late callback restore the old point-selection flow.
            return
        try:
            action, request = icing_request_from_payload(event.callback_payload or "", owner=icing_owner(event))
            if action == "h":
                await self._icing_menu(event, gateway, request)
            elif action == "c":
                await self._icing_card(event, gateway, request)
            elif action == "p":
                await self._icing_point_prompt(event, gateway, request)
            elif action == "d":
                await self._icing_card(event, gateway, replace(request, point=None), note="Пункт убран с карты.")
            elif action == "q":
                if request.point is None:
                    raise ValueError("В кнопке не указан пункт карты")
                state = self.sessions.get(event.platform, event.user_id, event.chat_id)
                label = "Выбранный пункт"
                if state is not None and state.product == "icing":
                    for candidate in state.candidates:
                        if (round(candidate.lat, 4), round(candidate.lon, 4)) == request.point:
                            label = candidate.label
                            break
                await self._icing_card(event, gateway, request, note=f"Отмечен пункт: {label}.")
            elif action == "u":
                self._icing_run_cache.pop(self._icing_run_key(replace(request, run=None)), None)
                await self._icing_card(event, gateway, replace(request, run=None))
            elif action == "t":
                await self._icing_card(event, gateway, request, verify=True, allow_fallback=True)
            elif action == "n":
                if self._icing_key(event) in self.icing_jobs:
                    await gateway.send_text(event.chat_id, "Расчёт обледенения уже выполняется. /status или /cancel; повторный запуск не создан.")
                    return
                ready = await self._prepare_icing_request(event, gateway, request,
                                                         verify=True, allow_fallback=True)
                if ready:
                    actual, display_event, changed_cycle = ready
                    if changed_cycle:
                        await self._icing_card(display_event, gateway, actual, note=changed_cycle)
                    else:
                        await self._run_icing(display_event, gateway, actual, reuse_status=True)
            elif action in {"r", "i"}:
                await self._run_icing(event, gateway, request, export=action)
            elif action == "v":
                # Retired CSV buttons from old messages remain safe after an update.
                await self._icing_card(event, gateway, request,
                    note="Численные таблицы убраны из чата. Здесь можно открыть карту и пояснения.")
            elif action == "x":
                await self.cancel_icing(event, gateway)
            elif action == "s":
                await self._icing_status(event, gateway)
            elif action in {"m", "a"}:
                await self._icing_page(event, gateway, request,
                    method_help_text(request.product if action == "m" else None),
                    menu_keyboard([[_button("К параметрам", "c", request), _button("Все продукты", "h", request)]]))
            elif action.startswith("l") and action[1:].isdigit():
                ready = await self._prepare_icing_request(event, gateway, request)
                if ready:
                    request, display_event, _ = ready
                    text = ("Обледенение · срок прогноза\n" + forecast_time_lines(request.run, request.lead)
                            + "\n\nНа кнопках — дата и время прогноза, UTC."
                            + "\nПри выборе проверю публикацию этого срока.")
                    await self._icing_page(display_event, gateway, request, text,
                                           icing_lead_keyboard(request, int(action[1:])))
            elif action == "z":
                rows = [[_button(f"{p} гПа", "c", replace(request, level_hpa=p)) for p in ICING_LEVELS_HPA[i:i+3]] for i in range(0, len(ICING_LEVELS_HPA), 3)]
                rows.append([_button("Назад", "c", request)])
                await self._icing_page(event, gateway, request, "Переохлаждённая облачная вода · уровень", menu_keyboard(rows))
            elif action == "o":
                await self._icing_page(event, gateway, request, "Шаг изолиний толщины слоя\n1 дам = 10 м.", menu_keyboard([
                    [_button("10 дам", "c", replace(request, contour_step=100)), _button("50 дам", "c", replace(request, contour_step=500))],
                    [_button("Назад", "c", request)],
                ]))
            elif action in {"g", "b", "w"}:
                groups = {"g": ("Толщина слоёв с условиями обледенения\nМетод Годске", ("godske_sum", "godske_max")),
                          "b": ("Слои по Годске", ("godske_base", "godske_top", "godske_count")),
                          "w": ("Переохлаждённая облачная вода\nНаибольшее содержание капель над каждой точкой и высота этого максимума.", ("slw_max", "slw_height"))}
                title, products = groups[action]
                rows = [[_button(ICING_PRODUCTS[p], "c", replace(request, product=p))] for p in products]
                rows.append([_button("Все продукты", "h", request)])
                if request.run:
                    title += "\n\n" + forecast_time_lines(request.run, request.lead)
                await self._icing_page(event, gateway, request, title, menu_keyboard(rows))
            else:
                raise ValueError("Кнопка больше не поддерживается. Откройте /icing")
        except (GfsProfileError, ValueError) as exc:
            await gateway.send_text(event.chat_id, f"Параметры обледенения: {exc}")

    @staticmethod
    def _icing_run_key(request):
        return (request.lead, request.run.date if request.run else None,
                request.run.cycle if request.run else None)

    async def _checked_icing_run(self, request, *, cancel_event=None):
        """Share brief metadata checks across chats, without downloading GRIB."""
        key = self._icing_run_key(request)
        async with self._icing_run_lock:
            now = time.monotonic()
            cached = self._icing_run_cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
            cancel = cancel_event or Event()
            try:
                if self._icing_builtin_selector:
                    selected = await asyncio.to_thread(self.icing_run_selector, request, cancel_event=cancel)
                else:
                    selected = await asyncio.to_thread(self.icing_run_selector, request)
            except asyncio.CancelledError:
                # A cancelled asyncio waiter cannot stop requests.get, but it can
                # prevent another fallback HTTP request once the current one ends.
                cancel.set()
                raise
            run = selected[0] if isinstance(selected, tuple) else selected
            if not isinstance(run, GfsRun) or (request.run and run != request.run):
                raise GfsProfileError("Не удалось подтвердить выбранный цикл GFS")
            expiry = time.monotonic() + _RUN_CACHE_SECONDS
            self._icing_run_cache = {k: item for k, item in self._icing_run_cache.items() if item[0] > now}
            self._icing_run_cache[key] = (expiry, run)
            self._icing_run_cache[(request.lead, run.date, run.cycle)] = (expiry, run)
            return run

    async def _prepare_icing_request(self, event, gateway, request, *, verify=False, allow_fallback=False):
        """Resolve the exact forecast before presenting a dated, runnable card."""
        if request.run and not verify:
            return request, event, ""
        cached = self._icing_run_cache.get(self._icing_run_key(request))
        if cached and cached[0] > time.monotonic():
            return replace(request, run=cached[1]), event, ""
        key = self._icing_key(event)
        state = FlowState(product="icing", step="checking", params={"request": request})
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        pending = {"state": state, "event": event, "cancel": Event()}
        self._icing_pending[key] = pending
        keyboard = _owned_keyboard(menu_keyboard([[_button("Отмена", "x", request)]]), event)
        text = f"❄ Обледенение\nПроверяю данные GFS для +{request.lead} ч…"
        try:
            if event.event_type.upper() == "CALLBACK" and event.message_id:
                try:
                    await gateway.edit_text(event.chat_id, event.message_id, text, keyboard=keyboard)
                except Exception as exc:
                    if "not modified" not in str(exc).lower():
                        message = await gateway.send_text(event.chat_id, text, keyboard=keyboard)
                        event = replace(event, message_id=message.message_id, event_type="CALLBACK", callback_id=None)
            else:
                message = await gateway.send_text(event.chat_id, text, keyboard=keyboard)
                event = replace(event, message_id=message.message_id, event_type="CALLBACK", callback_id=None)
        except BaseException:
            if self._icing_pending.get(key) is pending:
                self._icing_pending.pop(key, None)
            if self.sessions.get(event.platform, event.user_id, event.chat_id) is state:
                self.sessions.clear(event.platform, event.user_id, event.chat_id)
            raise
        pending["event"] = event
        note = ""
        try:
            async def check():
                nonlocal note
                try:
                    return await self._checked_icing_run(request, cancel_event=pending["cancel"])
                except GfsProfileError:
                    if pending["cancel"].is_set() or not (allow_fallback and request.run):
                        raise
                    actual = await self._checked_icing_run(replace(request, run=None), cancel_event=pending["cancel"])
                    if actual != request.run:
                        note = (f"Для +{request.lead} ч данные цикла {run_time_label(request.run)} недоступны. "
                                "Выбран доступный цикл; время прогноза уточнено ниже.")
                    return actual
            run = await asyncio.wait_for(check(), timeout=_RUN_CHECK_TIMEOUT)
            if self.sessions.get(event.platform, event.user_id, event.chat_id) is not state:
                return None
            return replace(request, run=run), event, note
        except (GfsProfileError, asyncio.TimeoutError) as exc:
            if self.sessions.get(event.platform, event.user_id, event.chat_id) is not state:
                return None
            reason = "Сервер GFS не ответил вовремя." if isinstance(exc, asyncio.TimeoutError) else str(exc)
            await self._icing_page(event, gateway, request,
                f"Не удалось проверить срок +{request.lead} ч.\n{reason[:450]}",
                menu_keyboard([[_button("Проверить ещё раз", "u", replace(request, run=None))],
                                      [_button("Как читать карту", "a", request), _button("Отмена", "x", request)]]))
            return None
        finally:
            pending["cancel"].set()
            if self._icing_pending.get(key) is pending:
                self._icing_pending.pop(key, None)

    async def _icing_page(self, event, gateway, request, text, keyboard, *, step="params", candidates=()):
        keyboard = _owned_keyboard(keyboard, event)
        state = FlowState(product="icing", step=step, candidates=list(candidates), params={"request": request})
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        if event.event_type.upper() == "CALLBACK" and event.message_id:
            try:
                message = await gateway.edit_text(event.chat_id, event.message_id, text, keyboard=keyboard)
                return state, replace(event, message_id=message.message_id)
            except Exception as exc:
                # Repeating a button in Telegram can return "message is not modified".
                if "not modified" in str(exc).lower():
                    return state, event
                if self.sessions.get(event.platform, event.user_id, event.chat_id) is not state:
                    return state, event
                LOG.debug("Could not edit icing card; sending a new one", exc_info=True)
        message = await gateway.send_text(event.chat_id, text, keyboard=keyboard)
        return state, replace(event, message_id=message.message_id, event_type="CALLBACK", callback_id=None)

    async def _icing_point_prompt(self, event, gateway, request, *, note=""):
        text = ((note + "\n\n") if note else "") + (
            "📍 Пункт на карте\nНапишите город или координаты (55.75 37.62), "
            "либо отправьте геолокацию.\nПункт будет отмечен на карте; район и срок сохраняются.\n\n"
            + _region_text(request))
        await self._icing_page(event, gateway, request, text, _point_keyboard(request), step="await_point")

    async def _icing_accept_point(self, event, gateway, request, point):
        try:
            selected = validate_icing_request(replace(request, point=(float(point.lat), float(point.lon))))
        except (GfsProfileError, TypeError, ValueError) as exc:
            await self._icing_point_prompt(event, gateway, request, note=f"Не удалось отметить пункт: {exc}")
            return
        await self._icing_card(event, gateway, selected, note=f"Отмечен пункт: {point.label}.")

    async def _icing_find_point(self, event, gateway, request, query):
        state, display_event = await self._icing_page(event, gateway, request,
            "📍 Ищу пункт на карте…", _point_keyboard(request), step="point_search")
        if self.sessions.get(event.platform, event.user_id, event.chat_id) is not state:
            return
        try:
            candidates = await asyncio.wait_for(asyncio.to_thread(self.deps.geocode, query, 5), timeout=30)
        except Exception as exc:
            if self.sessions.get(event.platform, event.user_id, event.chat_id) is not state:
                return
            reason = "Поиск не ответил вовремя." if isinstance(exc, asyncio.TimeoutError) else str(exc)[:300]
            await self._icing_point_prompt(display_event, gateway, request, note=f"Не удалось найти пункт: {reason}")
            return
        if self.sessions.get(event.platform, event.user_id, event.chat_id) is not state:
            return
        if not candidates:
            await self._icing_point_prompt(display_event, gateway, request,
                note="Пункт не найден. Уточните город или введите координаты.")
            return
        valid = []
        for point in candidates[:5]:
            try:
                selected = validate_icing_request(replace(request, point=(float(point.lat), float(point.lon))))
            except (GfsProfileError, TypeError, ValueError):
                continue
            valid.append((point, selected))
        if not valid:
            await self._icing_point_prompt(display_event, gateway, request,
                note="Найденные пункты находятся вне района карты. Укажите пункт внутри указанного района.")
            return
        if len(valid) == 1 and len(candidates) == 1:
            await self._icing_accept_point(display_event, gateway, request, valid[0][0])
            return
        rows = [[_button(_short_label(point), "q", selected)] for point, selected in valid]
        rows.append([_button("Назад к карте", "c", request)])
        text = "📍 Выберите пункт на карте."
        if len(valid) != len(candidates):
            text += "\nПоказаны варианты внутри заданного района."
        await self._icing_page(display_event, gateway, request, text, menu_keyboard(rows),
                               step="choose_point", candidates=[point for point, _ in valid])

    async def _icing_menu(self, event, gateway, request):
        ready = await self._prepare_icing_request(event, gateway, request)
        if not ready:
            return
        request, event, _ = ready
        await self._icing_page(event, gateway, request,
            f"❄ Обледенение\n{forecast_time_lines(request.run, request.lead)}\n"
            f"{_region_text(request)}\nВыберите карту.",
            icing_menu_keyboard(request))

    async def _icing_card(self, event, gateway, request, *, verify=False, allow_fallback=False, note=""):
        ready = await self._prepare_icing_request(event, gateway, request, verify=verify, allow_fallback=allow_fallback)
        if not ready:
            return
        request, event, changed_cycle = ready
        notice = changed_cycle or note
        text = (notice + "\n\n" if notice else "") + icing_card_text(request)
        await self._icing_page(event, gateway, request, text, icing_card_keyboard(request))

    async def _icing_status(self, event, gateway):
        job = self.icing_jobs.get(self._icing_key(event))
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if self._icing_key(event) in self._icing_pending:
            text = "Обледенение: проверяю публикацию выбранного срока GFS."
        elif state is not None and state.product == "icing" and state.step == "point_search":
            text = "Обледенение: ищу выбранный пункт; можно ввести другой или отменить выбор."
        elif job:
            text = "Обледенение: отмена принята, завершается текущий этап." if job["cancel"].is_set() else "Обледенение: расчёт выполняется; состояние обновляется в карточке."
        else:
            text = "Обледенение: активного расчёта нет. /icing — выбрать карту."
        await gateway.send_text(event.chat_id, text)

    async def cancel_icing(self, event, gateway):
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        pending = self._icing_pending.pop(self._icing_key(event), None)
        if pending:
            pending["cancel"].set()
            status = pending["event"]
            if status.message_id:
                await gateway.edit_text(event.chat_id, status.message_id, "Обледенение: выбор сброшен. /icing — открыть меню.")
        job = self.icing_jobs.get(self._icing_key(event))
        if job:
            job["cancel"].set()
            job["stop"].set()
            if job.get("reporter"):
                await job["reporter"]
            status = job.get("status")
            if status:
                await gateway.edit_text(event.chat_id, status.message_id, "Обледенение: отменено. Текущий сетевой этап может завершиться по тайм-ауту.")
                return
        if pending:
            return
        await gateway.send_text(event.chat_id, "Обледенение: выбор сброшен. /icing — открыть меню.", keyboard=menu_keyboard([]))

    async def icing_wait_idle(self):
        tasks = [job["task"] for job in list(self.icing_jobs.values()) if "task" in job]
        if tasks:
            await asyncio.gather(*tasks)

    async def shutdown_icing(self):
        for job in self.icing_jobs.values():
            job["cancel"].set()
            job["stop"].set()
        await self.icing_wait_idle()

    async def _run_icing(self, event, gateway, request, *, export="r", reuse_status=False):
        request = validate_icing_request(request)
        if export == "v":
            await self._icing_card(event, gateway, request,
                note="Численные таблицы убраны из чата. Здесь можно открыть карту и пояснения.")
            return
        key = self._icing_key(event)
        if key in self.icing_jobs:
            await gateway.send_text(event.chat_id, "Расчёт обледенения уже выполняется. /status или /cancel; повторный запуск не создан.")
            return
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product="icing", step="params", params={"request": request}))
        job = {"cancel": Event(), "stop": asyncio.Event()}
        self.icing_jobs[key] = job  # Reserve before the first await.
        keyboard = _owned_keyboard(menu_keyboard([[_button("Статус", "s", request), _button("Отмена", "x", request)]]), event)
        try:
            text = f"❄ {ICING_PRODUCTS[request.product]}\nОжидаю свободный слот…"
            if reuse_status and event.message_id:
                job["status"] = await gateway.edit_text(event.chat_id, event.message_id, text, keyboard=keyboard)
            else:
                job["status"] = await gateway.send_text(event.chat_id, text, keyboard=keyboard)
        except BaseException:
            self.icing_jobs.pop(key, None)
            raise
        if job["cancel"].is_set():
            await gateway.edit_text(event.chat_id, job["status"].message_id, "Обледенение: отменено.")
            self.icing_jobs.pop(key, None)
            return

        async def execute():
            result = None
            snapshot = {"event": ProgressEvent("check", "Проверяю индекс требуемого срока")}
            lock = Lock()
            def progress(value):
                with lock:
                    snapshot["event"] = value
            async def report():
                previous = ""
                while not job["stop"].is_set():
                    with lock:
                        text = _progress_text(request, snapshot["event"])
                    if text != previous:
                        try:
                            await gateway.edit_text(event.chat_id, job["status"].message_id, text, keyboard=keyboard)
                            previous = text
                        except Exception:
                            LOG.debug("Icing progress update failed", exc_info=True)
                    try:
                        await asyncio.wait_for(job["stop"].wait(), timeout=max(1.0, self.progress_interval_seconds))
                    except asyncio.TimeoutError:
                        pass
            try:
                async with self.gfs_semaphore:
                    if job["cancel"].is_set():
                        return
                    job["reporter"] = asyncio.create_task(report())
                    worker = asyncio.create_task(asyncio.to_thread(self.icing_builder, request,
                        progress_callback=progress, cancel_event=job["cancel"]))
                    try:
                        result = await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        # Keep the shared GFS capacity until the blocking thread exits.
                        job["cancel"].set()
                        try:
                            result = await worker
                        except Exception:
                            pass
                        raise
                job["stop"].set()
                if job.get("reporter"):
                    await job["reporter"]
                if job["cancel"].is_set():
                    return
                actual = request
                metadata = result.metadata
                if metadata.get("run_date") and metadata.get("run_cycle") is not None:
                    actual = replace(request, run=GfsRun(str(metadata["run_date"]), str(metadata["run_cycle"]).zfill(2)))
                await gateway.edit_text(event.chat_id, job["status"].message_id, result.summary,
                                        keyboard=_owned_keyboard(_result_keyboard(actual), event))
                for attachment in result.attachments:
                    if job["cancel"].is_set():
                        return
                    if export == "i":
                        if attachment.kind == "image":
                            await gateway.send_file(event.chat_id, attachment.path, caption=attachment.caption, filename=attachment.filename)
                    elif attachment.kind == "image":
                        await gateway.send_image(event.chat_id, attachment.path, caption=attachment.caption)
            except Exception as exc:
                job["stop"].set()
                if job.get("reporter"):
                    await job["reporter"]
                if not job["cancel"].is_set():
                    await gateway.edit_text(event.chat_id, job["status"].message_id,
                        f"Не удалось построить карту обледенения: {str(exc)[:600]}", keyboard=_owned_keyboard(icing_card_keyboard(request), event))
            finally:
                job["stop"].set()
                if job.get("reporter"):
                    await job["reporter"]
                if result is not None:
                    cleanup_product_result(result)
                if self.icing_jobs.get(key) is job:
                    self.icing_jobs.pop(key, None)
        job["task"] = asyncio.create_task(execute(), name=f"icing-{event.platform}-{event.user_id}")
