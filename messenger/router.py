from __future__ import annotations

from feature_flags import DISABLED_SOURCE_MESSAGE, disabled_input, product_available

import asyncio
import os
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from forecast_menu import UNVERIFIED_TIME_TEXT, ensure_menu_run, menu_run
from forecast_time import forecast_lead_label, forecast_time_lines, forecast_range_lines

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .menu_catalog import HOME_TEXT, compact_point_label, product_button_label, product_rows
from .profile_service import ParsedProfileInput, build_profile_product, cleanup_product_result, parse_profile_input
from .state import FlowState, InMemorySessionStore

QUICK_LEADS = (0, 3, 6, 12, 24, 48)
LEAD_PAGE_SIZE = 12
START_TEXT = HOME_TEXT
HELP_TEXT = (
    "❓ Помощь · GFS data\n\n"
    "Москва → выбор срока. Москва +24 → профиль сразу.\n"
    "Координаты и геолокация работают так же. Сроки — до +384 ч, время — UTC.\n\n"
    "/profile — профиль · /aero — аэрограмма\n"
    "/windgram — высота × срок · /cloudgram — облака и осадки\n"
    "/meteogram — метеограмма · /map — карта\n"
    "/icing — обледенение по району · /route — маршрут\n"
    "/settings — точки и сценарии · /schedule — расписания\n"
    "/status — состояние · /cancel — отмена · /start — меню"
)


def home_button() -> UiButton:
    return UiButton("🏠 Меню", "callback", encode_callback("flow", "home"))


def menu_keyboard(rows: list[list[UiButton]]) -> UiKeyboard:
    """Keep controls readable and leave a way out of every product card."""
    result = []
    has_home = False
    for row in rows:
        if not row:
            continue
        numeric = all(any(char.isdigit() for char in button.text) and len(button.text) <= 14 for button in row)
        columns = 3 if numeric else 2
        result.extend(list(row[start:start + columns]) for start in range(0, len(row), columns))
        has_home = has_home or any(button.payload in {
            encode_callback("flow", "home"), encode_callback("settings", "home"),
            encode_callback("schedule", "home"), encode_callback("wn3", "home"),
        } for button in row)
    if not has_home:
        if result and len(result[-1]) == 1 and "Отмена" in result[-1][0].text:
            result[-1].append(home_button())
        else:
            result.append([home_button()])
    return UiKeyboard.from_rows(result)


@dataclass
class RouterDependencies:
    geocode: Callable[[str, int], list[Any]]
    profile_builder: Callable[..., Any] = build_profile_product
    profile_parser: Callable[[str, int], ParsedProfileInput] = parse_profile_input
    canonical_leads: Callable[[], list[int]] | None = None
    latest_run_for_lead: Callable[[int], Any] | None = None

    def leads(self) -> list[int]:
        if self.canonical_leads is not None:
            return list(self.canonical_leads())
        from gfs_core import canonical_leads
        return canonical_leads()

    def latest_run(self, lead: int) -> Any:
        if self.latest_run_for_lead is not None:
            return self.latest_run_for_lead(lead)
        from gfs_core import latest_available_run_for_lead
        return latest_available_run_for_lead(lead)


class MessengerRouter:
    home_products = ("profile",)
    home_settings = False
    home_schedule = False

    def __init__(
        self,
        dependencies: RouterDependencies,
        *,
        sessions: InMemorySessionStore | None = None,
        default_lead: int = 24,
        progress_interval_seconds: float = 1.5,
        max_concurrent_gfs: int | None = None,
    ) -> None:
        self.deps = dependencies
        self.sessions = sessions or InMemorySessionStore()
        self.default_lead = int(default_lead)
        self.progress_interval_seconds = max(0.25, float(progress_interval_seconds))
        if max_concurrent_gfs is None:
            max_concurrent_gfs = int(os.getenv("MAX_CONCURRENT_GFS", "2"))
        self.gfs_semaphore = asyncio.Semaphore(max(1, int(max_concurrent_gfs)))

    @classmethod
    def default(cls, **kwargs: Any) -> "MessengerRouter":
        from geocode_choices import search_location_candidates
        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    async def handle(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        if event.platform != gateway.platform:
            raise ValueError(f"Event platform {event.platform!r} does not match gateway {gateway.platform!r}")
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        stale = state is not None and not product_available(state.product, state.params)
        blocked = disabled_input(event.text or ("/" + event.command if event.command else ""), event.callback_payload or "")
        navigation = event.event_type.upper() == "CALLBACK" and event.callback_payload in {
            encode_callback("flow", "home"), encode_callback("flow", "help"),
        }
        if stale:
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
        if blocked or (stale and not event.command and not navigation):
            if event.event_type.upper() == 'CALLBACK':
                await gateway.answer_callback(event)
            await gateway.send_text(event.chat_id, DISABLED_SOURCE_MESSAGE)
            return
        kind = event.event_type.upper()
        if navigation:
            await gateway.answer_callback(event)
            if event.callback_payload == encode_callback("flow", "home"):
                await self._start(event, gateway)
            else:
                await gateway.send_text(event.chat_id, self._help_text(), keyboard=UiKeyboard.from_rows([[home_button()]]))
            return
        if kind == "START":
            await self._start(event, gateway)
        elif kind == "CALLBACK":
            await self._callback(event, gateway)
        elif kind == "LOCATION" or event.location is not None:
            await self._location(event, gateway)
        elif kind in {"TEXT", "COMMAND"}:
            await self._text(event, gateway)

    async def _start(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        enabled = tuple(product for product in self.home_products if product_available(product))
        point_label = self._home_point_label(event)
        rows = self._home_quick_rows(event, enabled)
        rows.extend([
            [UiButton(product_button_label(product), "callback", encode_callback("product", "open", product)) for product in row]
            for row in product_rows(enabled)
        ])
        utility = []
        if self.home_schedule:
            utility.append(UiButton("🕒 Расписания", "callback", encode_callback("schedule", "open")))
        if self.home_settings:
            utility.append(UiButton("⚙️ Настройки", "callback", encode_callback("settings", "open")))
        if utility:
            rows.append(utility)
        rows.append([UiButton("📍 Геолокация", "request_location"), UiButton("❓ Помощь", "callback", encode_callback("flow", "help"))])
        text = HOME_TEXT
        if point_label:
            text = f"🌦️ GFS data\n📍 {compact_point_label(point_label)}\n\nВыберите продукт 👇"
        if "weathernext3" in enabled:
            text += "\nWeatherNext 3 — дополнительный источник."
        await gateway.send_text(event.chat_id, text, keyboard=UiKeyboard.from_rows(rows))

    def _home_quick_rows(self, event: NormalizedEvent, enabled: tuple[str, ...]) -> list[list[UiButton]]:
        return []

    def _home_point_label(self, event: NormalizedEvent) -> str | None:
        return None

    def _help_text(self) -> str:
        if "weathernext3" in self.home_products and product_available("weathernext3"):
            return HELP_TEXT + "\n/wn3 — WeatherNext 3"
        return HELP_TEXT

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        if command:
            args = _command_args(text)
            if command == "start":
                await self._start(event, gateway)
                return
            if command == "help":
                await gateway.send_text(event.chat_id, self._help_text(), keyboard=UiKeyboard.from_rows([[home_button()]]))
                return
            if command == "cancel":
                self.sessions.clear(event.platform, event.user_id, event.chat_id)
                await gateway.send_text(event.chat_id, "Выбор сброшен.", keyboard=UiKeyboard.from_rows([[home_button()]]))
                return
            if command == "status":
                await self._status(event, gateway)
                return
            if command == "profile":
                if not args:
                    await self._ask_point(event, gateway)
                else:
                    await self._resolve_profile_text(event, gateway, args)
                return
            if command in {"aero", "windgram", "cloudgram", "meteogram", "map"}:
                await gateway.send_text(
                    event.chat_id,
                    f"/{command} недоступен в этой конфигурации. Откройте /start.",
                )
                return
        if text:
            await self._resolve_profile_text(event, gateway, text)

    async def _ask_point(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(step="await_point"))
        await gateway.send_text(
            event.chat_id,
            "📈 Профиль · GFS data\n📍 Укажите город, координаты или отправьте геолокацию.",
            keyboard=UiKeyboard.from_rows([[UiButton("📍 Геолокация", "request_location"), home_button()]]),
        )

    async def _resolve_profile_text(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str) -> None:
        try:
            parsed = self.deps.profile_parser(raw, self.default_lead)
            candidates = await asyncio.to_thread(self.deps.geocode, parsed.location_query, 5)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка: {exc}")
            return
        if not candidates:
            await gateway.send_text(event.chat_id, "Точка не найдена. Уточните город или используйте координаты.")
            return
        if len(candidates) > 1:
            state = FlowState(
                step="choose_place",
                candidates=list(candidates[:5]),
                pending_lead=parsed.lead_hour if parsed.lead_from_user else None,
                pending_run=parsed.run,
            )
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [
                [UiButton(_short_label(point), "callback", encode_callback("profile", "place", index))]
                for index, point in enumerate(state.candidates)
            ]
            rows.append([UiButton("✖️ Отмена", "callback", encode_callback("flow", "cancel"))])
            await gateway.send_text(event.chat_id, "Найдено несколько точек. Выберите нужную:", keyboard=UiKeyboard.from_rows(rows))
            return
        point = candidates[0]
        if parsed.lead_from_user:
            await self._run_profile(event, gateway, point, parsed.lead_hour, parsed.run)
            return
        state = FlowState(step="choose_lead", point=point, pending_run=parsed.run)
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await self._send_lead_picker(event, gateway, state, page=0)

    async def _location(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        if event.location is None:
            await gateway.send_text(event.chat_id, "В сообщении нет координат геолокации.")
            return
        from geocode import GeoPoint
        point = GeoPoint(
            float(event.location.lat),
            float(event.location.lon),
            f"геолокация {event.location.lat:.4f}, {event.location.lon:.4f}",
            event.platform,
        )
        state = FlowState(step="choose_lead", point=point)
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await self._send_lead_picker(event, gateway, state, page=0)

    async def _callback(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        try:
            data = decode_callback(event.callback_payload or "")
        except CallbackCodecError:
            await gateway.answer_callback(event, text="Кнопка устарела")
            await gateway.send_text(event.chat_id, "Сценарий устарел. Начните заново: /start")
            return
        await gateway.answer_callback(event)
        if data.scope == "flow" and data.action == "cancel":
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            await gateway.send_text(event.chat_id, "Выбор отменён.", keyboard=UiKeyboard.from_rows([[home_button()]]))
            return
        if data.scope == "product" and data.action == "open" and data.value == "profile":
            await self._ask_point(event, gateway)
            return
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None:
            await gateway.send_text(event.chat_id, "Сценарий устарел после перезапуска. Начните заново: /start")
            return
        if data.scope == "profile" and data.action == "place":
            try:
                point = state.candidates[int(data.value or "")]
            except (ValueError, IndexError):
                await gateway.send_text(event.chat_id, "Вариант точки устарел. Повторите запрос.")
                return
            if state.pending_lead is not None:
                await self._run_profile(event, gateway, point, state.pending_lead, state.pending_run)
                return
            state.point = point
            state.step = "choose_lead"
            state.candidates.clear()
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            await self._send_lead_picker(event, gateway, state, page=0)
            return
        if data.scope == "profile" and data.action == "lead":
            if state.point is None:
                await gateway.send_text(event.chat_id, "Точка выбора потеряна. Начните заново: /profile")
                return
            try:
                lead = int(data.value or "")
            except ValueError:
                await gateway.send_text(event.chat_id, "Некорректный срок прогноза.")
                return
            if lead not in self.deps.leads():
                await gateway.send_text(event.chat_id, "Этот срок GFS недоступен.")
                return
            run = await self._forecast_selection_run(event, gateway, state, lead)
            if (run is None or self.sessions.get(event.platform, event.user_id, event.chat_id) is not state
                    or state.params.get("_forecast_lead", lead) != lead):
                return
            await self._run_profile(event, gateway, state.point, lead, run)
            return
        if data.scope == "profile" and data.action == "leadpage":
            try:
                page = max(0, int(data.value or "0"))
            except ValueError:
                page = 0
            await self._send_lead_picker(event, gateway, state, page=page)
            return
        await gateway.send_text(event.chat_id, "Кнопка больше не поддерживается. Начните заново: /start")

    async def _forecast_menu_run(self, state: FlowState, lead: int | None = None):
        return await ensure_menu_run(state.params, self.default_lead if lead is None else lead,
                                     self.deps.latest_run, explicit_run=state.pending_run)

    async def _forecast_selection_run(self, event, gateway, state: FlowState, lead: int, *, notify=True):
        token = int(state.params.get("_forecast_selection", 0)) + 1
        state.params["_forecast_selection"] = token
        previous = menu_run(state.params)
        run = await self._forecast_menu_run(state, lead)
        if (self.sessions.get(event.platform, event.user_id, event.chat_id) is not state
                or state.params.get("_forecast_lead", lead) != lead
                or state.params.get("_forecast_selection") != token):
            return None
        if run is None:
            if notify and state.params.get("_forecast_error"):
                await gateway.send_text(event.chat_id, UNVERIFIED_TIME_TEXT)
            return None
        if notify and previous is not None and run != previous:
            await gateway.send_text(event.chat_id, "Для выбранного срока доступен другой цикл:\n" + forecast_time_lines(run, lead))
        if (self.sessions.get(event.platform, event.user_id, event.chat_id) is not state
                or state.params.get("_forecast_selection") != token):
            return None
        return run

    async def _send_forecast_parameter_card(self, event, gateway, state: FlowState, text_builder, keyboard_builder):
        """One current card, with exact end-lead selection and cancellation fencing."""
        token = int(state.params.get("_forecast_view", 0)) + 1
        state.params["_forecast_view"] = token
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        lead = int(state.params["to"])
        check_text = f"Проверяю данные GFS для +{lead} ч…"
        if event.event_type.upper() == "CALLBACK" and event.message_id:
            status = await gateway.edit_text(event.chat_id, event.message_id, check_text)
        else:
            status = await gateway.send_text(event.chat_id, check_text)
        run = await self._forecast_menu_run(state, lead)
        if (self.sessions.get(event.platform, event.user_id, event.chat_id) is not state
                or state.params.get("_forecast_view") != token):
            return
        timing = forecast_range_lines(run, state.params["from"], lead) if run is not None else UNVERIFIED_TIME_TEXT
        await gateway.edit_text(event.chat_id, status.message_id,
                                text_builder(state.point, state.params) + "\n" + timing,
                                keyboard=keyboard_builder(state.params))

    async def _send_lead_picker(self, event: NormalizedEvent, gateway: MessengerGateway, state: FlowState, *, page: int) -> None:
        token = int(state.params.get("_forecast_view", 0)) + 1
        state.params["_forecast_view"] = token
        check_text = f"Проверяю данные GFS для +{self.default_lead} ч…"
        if event.event_type.upper() == "CALLBACK" and event.message_id:
            status = await gateway.edit_text(event.chat_id, event.message_id, check_text)
        else:
            status = await gateway.send_text(event.chat_id, check_text)
        run = await self._forecast_menu_run(state)
        if (self.sessions.get(event.platform, event.user_id, event.chat_id) is not state
                or state.params.get("_forecast_view") != token):
            return
        leads = self.deps.leads()
        if page <= 0:
            values = [lead for lead in QUICK_LEADS if lead in leads]
            rows = _lead_rows(values, run)
            rows.append([UiButton("🗓️ Все сроки до +384", "callback", encode_callback("profile", "leadpage", 1))])
            page_text = "быстрые сроки"
        else:
            start = (page - 1) * LEAD_PAGE_SIZE
            values = leads[start : start + LEAD_PAGE_SIZE]
            if not values:
                page = max(1, (len(leads) - 1) // LEAD_PAGE_SIZE + 1)
                start = (page - 1) * LEAD_PAGE_SIZE
                values = leads[start : start + LEAD_PAGE_SIZE]
            rows = _lead_rows(values, run)
            nav: list[UiButton] = []
            if page > 1:
                nav.append(UiButton("‹", "callback", encode_callback("profile", "leadpage", page - 1)))
            nav.append(UiButton("⚡ Быстрые", "callback", encode_callback("profile", "leadpage", 0)))
            if start + LEAD_PAGE_SIZE < len(leads):
                nav.append(UiButton("›", "callback", encode_callback("profile", "leadpage", page + 1)))
            rows.append(nav)
            page_text = f"страница {page}"
        state.lead_page = page
        state.step = "choose_lead"
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        rows.append([UiButton("📍 Другая точка", "callback", encode_callback("product", "open", "profile")), home_button()])
        timing = forecast_time_lines(run, self.default_lead) if run is not None else UNVERIFIED_TIME_TEXT
        await gateway.edit_text(
            event.chat_id, status.message_id,
            f"📈 Профиль\n📍 {getattr(state.point, 'label', 'выбранная точка')}\n{timing}\n\n🕒 Выберите срок ({page_text}, UTC):",
            keyboard=menu_keyboard(rows),
        )

    async def _run_profile(self, event: NormalizedEvent, gateway: MessengerGateway, point: Any, lead: int, run: Any | None) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        status = await gateway.send_text(
            event.chat_id,
            f"⏳ Профиль GFS\n📍 {getattr(point, 'label', '')}\n" +
            (forecast_time_lines(run, lead) if run is not None else f"Прогноз +{lead} ч") +
            "\n1/5 Проверяю опубликованный цикл…",
        )
        snapshot = {"event": ProgressEvent("check", "Проверяю данные")}
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
                text = _profile_progress_text(point, lead, value)
                if text != last_text:
                    try:
                        await gateway.edit_text(event.chat_id, status.message_id, text)
                        last_text = text
                    except Exception:
                        pass
                await asyncio.sleep(self.progress_interval_seconds)

        reporter_task = asyncio.create_task(reporter())
        result = None
        try:
            async with self.gfs_semaphore:
                result = await asyncio.to_thread(
                    self.deps.profile_builder,
                    point,
                    lead,
                    run,
                    progress_callback=progress,
                )
            stop = True
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
        except Exception as exc:
            stop = True
            await reporter_task
            await gateway.edit_text(event.chat_id, status.message_id, f"Ошибка расчёта: {exc}")
        finally:
            if result is not None:
                cleanup_product_result(result)

    async def _status(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        leads = list(dict.fromkeys((0, self.default_lead, 24, 48, 72, 120, 240, 384)))
        lines = ["⚙️ Доступность GFS"]
        for lead in leads:
            try:
                run = await asyncio.to_thread(self.deps.latest_run, lead)
                lines.append(f"+{lead:03d} ч → {run.date} {run.cycle}Z")
            except Exception:
                lines.append(f"+{lead:03d} ч → недоступно")
        lines.append("GFS data")
        await gateway.send_text(event.chat_id, "\n".join(lines))


def _command_args(text: str) -> str:
    if not text.startswith("/"):
        return text
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _short_label(point: Any) -> str:
    return (" ".join(str(getattr(point, "label", point)).split())[:58] or "Точка")


def _lead_rows(values: list[int], run=None) -> list[list[UiButton]]:
    width = 2 if run is not None else 3
    return [
        [UiButton(forecast_lead_label(run, lead) if run is not None else f"+{lead}",
                  "callback", encode_callback("profile", "lead", lead)) for lead in values[start : start + width]]
        for start in range(0, len(values), width)
    ]


def _profile_progress_text(point: Any, lead: int, event: ProgressEvent) -> str:
    data = dict(event.data)
    header = f"⏳ Профиль GFS\n📍 {getattr(point, 'label', '')}\n🕒 +{lead} ч\n"
    if event.stage == "check":
        body = "1/5 Проверяю данные…"
    elif event.stage == "grid":
        body = f"2/5 Узел GFS: {data.get('grid_lat')}, {data.get('grid_lon')}"
    elif event.stage == "cache":
        body = "3/5 Читаю данные из кэша…"
    elif event.stage in {"download_start", "download", "download_done"}:
        total, downloaded = data.get("total"), data.get("downloaded")
        body = f"3/5 Загружаю данные: {min(100, int(downloaded) * 100 / int(total)):.0f}%" if total and downloaded else "3/5 Загружаю данные…"
    elif event.stage in {"parse_start", "parse_done", "done"}:
        body = "4/5 Читаю и рассчитываю профиль…"
    elif event.stage in {"plot_start", "plot_done"}:
        body = "5/5 Формирую PNG/CSV…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body
