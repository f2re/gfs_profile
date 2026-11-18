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
from dataclasses import replace
from threading import Event, Lock
from typing import Any, Callable

from gfs_core import GfsProfileError, GfsRun, canonical_leads
from icing_config import (
    ICING_LEVELS_HPA, ICING_PRODUCTS, IcingRequest,
    parse_icing_input, validate_icing_request,
)

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .icing_service import build_icing_product_result
from .cloudgram_router import CloudgramMessengerRouter
from .profile_service import cleanup_product_result
from .router import LEAD_PAGE_SIZE, QUICK_LEADS, RouterDependencies, _command_args, menu_keyboard
from .state import FlowState

LOG = logging.getLogger(__name__)
# Fixed wire ordering, independent of display order and dict ordering.
PRODUCT_CODES = (
    "slw_level", "godske_sum", "godske_max", "godske_base", "godske_top",
    "godske_count", "slw_max", "slw_height", "slwp", "agreement",
)
_WIRE = struct.Struct("!8sBHHBffffIB")

METHOD_TEXT = (
    "Обледенение · методика\n\n"
    "Годске M8D20: −20 ≤ T < 0 °C; T + 8(T − Td) ≤ 0. "
    "Td относительно воды восстановлена из удельной влажности и давления. "
    "Границы интерполируются между соседними уровнями; отдельные слои сохраняются.\n\n"
    "Мощность — вертикальная протяжённость условий, гп.м. Она не определяет "
    "интенсивность обледенения или толщину льда на воздушном судне. "
    "Шаг изолиний 100 гп.м не означает точность высоты 100 м.\n\n"
    "Водность — облачная жидкая вода GFS при T < 0 °C, г/м³; "
    "запас воды — её интеграл по доступной части столба, г/м². "
    "Максимум определяется по исходным изобарическим уровням.\n\n"
    "Диапазон 1000–300 гПа. Ниже первого надземного уровня и через пропуски "
    "профиль не достраивается. Незамкнутые границы отмечаются отдельно. "
    "Сравнение методов учитывает их пересечение в одном слое.\n\n"
    "GFS data · 0.25°"
)


def icing_owner(event: NormalizedEvent) -> bytes:
    value = "\0".join((event.platform, str(event.user_id), str(event.chat_id)))
    return hashlib.blake2s(value.encode("utf-8"), digest_size=8).digest()


def icing_payload(action: str, request: IcingRequest, *, owner: bytes = b"\0" * 8) -> str:
    request = validate_icing_request(request)
    raw = _WIRE.pack(
        owner, PRODUCT_CODES.index(request.product), request.lead, request.level_hpa,
        0 if request.contour_step == 100 else 1, *request.bbox,
        int(request.run.date) if request.run else 0,
        int(request.run.cycle) if request.run else 255,
    )
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return encode_callback("icing", action, token)


def icing_request_from_payload(payload: str, *, owner: bytes | None = None) -> tuple[str, IcingRequest]:
    data = decode_callback(payload)
    if data.scope != "icing" or not data.value:
        raise ValueError("Кнопка обледенения не содержит параметров")
    try:
        raw = base64.b64decode(data.value + "=" * (-len(data.value) % 4), altchars=b"-_", validate=True)
        if len(raw) != _WIRE.size:
            raise ValueError("Некорректный размер параметров")
        bound_owner, product, lead, level, contour, west, east, south, north, date, cycle = _WIRE.unpack(raw)
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
    return f"Район: {west:g}…{east:g}° долготы; {south:g}…{north:g}° с.ш."


def icing_menu_keyboard(request: IcingRequest) -> UiKeyboard:
    return menu_keyboard([
        [_button("Вода на уровне", "c", replace(request, product="slw_level")),
         _button("Годске", "g", request)],
        [_button("Границы", "b", request),
         _button("Макс. вода", "w", request)],
        [_button("Запас воды", "c", replace(request, product="slwp")),
         _button("Сравнение", "c", replace(request, product="agreement"))],
        [_button("О методах", "m", request), _button("Отмена", "x", request)],
    ])


def icing_card_text(request: IcingRequest) -> str:
    cycle = f"{request.run.date} {request.run.cycle} UTC" if request.run else "последний опубликованный для срока"
    lines = [f"❄ {ICING_PRODUCTS[request.product]}", _region_text(request),
             f"Срок: +{request.lead} ч · цикл: {cycle}"]
    if request.product == "slw_level":
        lines.append(f"Уровень: {request.level_hpa} гПа")
    else:
        lines.append("Диапазон: 1000–300 гПа, только надземные уровни")
    if request.product.startswith("godske_"):
        lines.append("Метод: Годске M8D20")
    if request.product in {"godske_sum", "godske_max"}:
        lines.append(f"Изолинии: {request.contour_step} гп.м")
    lines.append("GFS data · 0.25°")
    return "\n".join(lines)


def icing_card_keyboard(request: IcingRequest) -> UiKeyboard:
    controls = [_button("Срок", "l0", request)]
    if request.product == "slw_level":
        controls.append(_button("Уровень", "z", request))
    elif request.product in {"godske_sum", "godske_max"}:
        controls.append(_button("Изолинии", "o", request))
    return menu_keyboard([
        [_button("Построить", "r", request)], controls,
        [_button("Другой продукт", "h", request), _button("О методе", "m", request)],
        [_button("Новый цикл", "c", replace(request, run=None)), _button("Отмена", "x", request)],
    ])


def icing_lead_keyboard(request: IcingRequest, page: int = 0) -> UiKeyboard:
    leads = canonical_leads()
    pages = (len(leads) + LEAD_PAGE_SIZE - 1) // LEAD_PAGE_SIZE
    page = max(0, min(int(page), pages))
    values = [v for v in QUICK_LEADS if v in leads] if page == 0 else leads[(page - 1) * LEAD_PAGE_SIZE:page * LEAD_PAGE_SIZE]
    rows = [[_button(f"+{v} ч", "c", replace(request, lead=v)) for v in values[i:i + 3]] for i in range(0, len(values), 3)]
    if page == 0:
        rows.append([_button("Все сроки до +384", "l1", request)])
    else:
        nav = []
        if page > 1:
            nav.append(_button("‹", f"l{page - 1}", request))
        nav.append(_button("Быстрые сроки", "l0", request))
        if page < pages:
            nav.append(_button(f"{page}/{pages} ›", f"l{page + 1}", request))
        rows.append(nav)
    rows.append([_button("Назад", "c", request)])
    return menu_keyboard(rows)


def _result_keyboard(request: IcingRequest) -> UiKeyboard:
    leads = canonical_leads()
    index = leads.index(request.lead)
    nav = []
    if index:
        nav.append(_button(f"‹ +{leads[index - 1]} ч", "r", replace(request, lead=leads[index - 1])))
    if index + 1 < len(leads):
        nav.append(_button(f"+{leads[index + 1]} ч ›", "r", replace(request, lead=leads[index + 1])))
    rows = [nav] if nav else []
    rows.extend([
        [_button("PNG целиком", "i", request), _button("CSV", "v", request)],
        [_button("Другой продукт", "h", request), _button("Параметры", "c", request)],
        [_button("Последний цикл", "c", replace(request, run=None))],
    ])
    return menu_keyboard(rows)


def _progress_text(request: IcingRequest, progress: ProgressEvent) -> str:
    message = progress.message or "Выполняю расчёт"
    return f"❄ {ICING_PRODUCTS[request.product]} · +{request.lead} ч\n{message}"


class IcingMessengerRouter(CloudgramMessengerRouter):

    home_products = CloudgramMessengerRouter.home_products + ("icing",)

    def __init__(self, dependencies: RouterDependencies, *, icing_builder: Callable[..., Any] = build_icing_product_result,
                 icing_parser: Callable[[str], IcingRequest] = parse_icing_input, **kwargs: Any) -> None:
        super().__init__(dependencies, **kwargs)
        self.icing_builder = icing_builder
        self.icing_parser = icing_parser
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
            if (event.text or "").strip() in {"Отмена", "✖ Отмена"}:
                await self.cancel_icing(event, gateway)
            else:
                await gateway.send_text(event.chat_id, "Район выбран. Выберите карту и срок кнопками.")
            return
        await super()._text(event, gateway)

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
        await gateway.answer_callback(event)
        try:
            action, request = icing_request_from_payload(event.callback_payload or "", owner=icing_owner(event))
            if action == "h":
                await self._icing_menu(event, gateway, request)
            elif action == "c":
                await self._icing_card(event, gateway, request)
            elif action in {"r", "i", "v"}:
                await self._run_icing(event, gateway, request, export=action)
            elif action == "x":
                await self.cancel_icing(event, gateway)
            elif action == "s":
                await self._icing_status(event, gateway)
            elif action == "m":
                await self._icing_page(event, gateway, request, METHOD_TEXT,
                    menu_keyboard([[_button("К параметрам", "c", request), _button("Все продукты", "h", request)]]))
            elif action.startswith("l") and action[1:].isdigit():
                await self._icing_page(event, gateway, request, "Обледенение · срок прогноза", icing_lead_keyboard(request, int(action[1:])))
            elif action == "z":
                rows = [[_button(f"{p} гПа", "c", replace(request, level_hpa=p)) for p in ICING_LEVELS_HPA[i:i+3]] for i in range(0, len(ICING_LEVELS_HPA), 3)]
                rows.append([_button("Назад", "c", request)])
                await self._icing_page(event, gateway, request, "Переохлаждённая облачная вода · уровень", menu_keyboard(rows))
            elif action == "o":
                await self._icing_page(event, gateway, request, "Шаг изолиний мощности, гп.м", menu_keyboard([
                    [_button("100 гп.м", "c", replace(request, contour_step=100)), _button("500 гп.м", "c", replace(request, contour_step=500))],
                    [_button("Назад", "c", request)],
                ]))
            elif action in {"g", "b", "w"}:
                groups = {"g": ("Мощность по Годске", ("godske_sum", "godske_max")),
                          "b": ("Слои по Годске", ("godske_base", "godske_top", "godske_count")),
                          "w": ("Максимум водности", ("slw_max", "slw_height"))}
                title, products = groups[action]
                rows = [[_button(ICING_PRODUCTS[p], "c", replace(request, product=p))] for p in products]
                rows.append([_button("Все продукты", "h", request)])
                await self._icing_page(event, gateway, request, title, menu_keyboard(rows))
            else:
                raise ValueError("Кнопка больше не поддерживается. Откройте /icing")
        except (GfsProfileError, ValueError) as exc:
            await gateway.send_text(event.chat_id, f"Параметры обледенения: {exc}")

    async def _icing_page(self, event, gateway, request, text, keyboard):
        keyboard = _owned_keyboard(keyboard, event)
        self.sessions.set(event.platform, event.user_id, event.chat_id,
                          FlowState(product="icing", step="params", params={"request": request}))
        if event.event_type.upper() == "CALLBACK" and event.message_id:
            try:
                await gateway.edit_text(event.chat_id, event.message_id, text, keyboard=keyboard)
                return
            except Exception as exc:
                # Repeating a button in Telegram can return "message is not modified".
                if "not modified" in str(exc).lower():
                    return
                LOG.debug("Could not edit icing card; sending a new one", exc_info=True)
        await gateway.send_text(event.chat_id, text, keyboard=keyboard)

    async def _icing_menu(self, event, gateway, request):
        await self._icing_page(event, gateway, request,
            f"Обледенение · GFS data\n{_region_text(request)}\nВыберите карту.",
            icing_menu_keyboard(request))

    async def _icing_card(self, event, gateway, request):
        await self._icing_page(event, gateway, request, icing_card_text(request), icing_card_keyboard(request))

    async def _icing_status(self, event, gateway):
        job = self.icing_jobs.get(self._icing_key(event))
        if job:
            text = "Обледенение: отмена принята, завершается текущий этап." if job["cancel"].is_set() else "Обледенение: расчёт выполняется; состояние обновляется в карточке."
        else:
            text = "Обледенение: активного расчёта нет. /icing — выбрать карту."
        await gateway.send_text(event.chat_id, text)

    async def cancel_icing(self, event, gateway):
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
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
        await gateway.send_text(event.chat_id, "Выбор отменён.", keyboard=menu_keyboard([]))

    async def icing_wait_idle(self):
        tasks = [job["task"] for job in list(self.icing_jobs.values()) if "task" in job]
        if tasks:
            await asyncio.gather(*tasks)

    async def shutdown_icing(self):
        for job in self.icing_jobs.values():
            job["cancel"].set()
            job["stop"].set()
        await self.icing_wait_idle()

    async def _run_icing(self, event, gateway, request, *, export="r"):
        request = validate_icing_request(request)
        key = self._icing_key(event)
        if key in self.icing_jobs:
            await gateway.send_text(event.chat_id, "Расчёт обледенения уже выполняется. /status или /cancel; повторный запуск не создан.")
            return
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(product="icing", step="params", params={"request": request}))
        job = {"cancel": Event(), "stop": asyncio.Event()}
        self.icing_jobs[key] = job  # Reserve before the first await.
        keyboard = _owned_keyboard(menu_keyboard([[_button("Статус", "s", request), _button("Отмена", "x", request)]]), event)
        try:
            job["status"] = await gateway.send_text(event.chat_id, f"❄ {ICING_PRODUCTS[request.product]}\nОжидаю свободный слот…", keyboard=keyboard)
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
                    elif export == "v":
                        if attachment.path.suffix.lower() == ".csv":
                            await gateway.send_file(event.chat_id, attachment.path, caption=attachment.caption, filename=attachment.filename)
                    elif attachment.kind == "image":
                        await gateway.send_image(event.chat_id, attachment.path, caption=attachment.caption)
                    else:
                        await gateway.send_file(event.chat_id, attachment.path, caption=attachment.caption, filename=attachment.filename)
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
