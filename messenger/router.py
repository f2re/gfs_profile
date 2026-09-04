from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .profile_service import ParsedProfileInput, build_profile_product, cleanup_product_result, parse_profile_input
from .state import FlowState, InMemorySessionStore

QUICK_LEADS = (0, 3, 6, 12, 24, 48)
LEAD_PAGE_SIZE = 12
START_TEXT = (
    "🌦 GFS 0.25 — модельный прогноз\n"
    "Укажите город, координаты или геолокацию. Для быстрого профиля: Москва +24.\n\n"
    "Продукты: /profile, /aero, /windgram, /cloudgram, /meteogram, /map.\n"
    "GFS — модель, не наблюдение и не радиозонд."
)
HELP_TEXT = (
    "Команды: /profile, /aero, /windgram, /cloudgram, /meteogram, /map, /status, /cancel.\n"
    "Пример: Москва +24 или /profile Москва +24. Сроки GFS доступны до +384 ч."
)


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
        kind = event.event_type.upper()
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
        await gateway.send_text(
            event.chat_id,
            START_TEXT,
            keyboard=UiKeyboard.from_rows([
                [UiButton("📈 Профиль", "callback", encode_callback("product", "open", "profile"))],
                [UiButton("📍 Геолокация", "request_location")],
            ]),
        )

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        if command:
            args = _command_args(text)
            if command == "start":
                await self._start(event, gateway)
                return
            if command == "help":
                await gateway.send_text(event.chat_id, HELP_TEXT)
                return
            if command == "cancel":
                self.sessions.clear(event.platform, event.user_id, event.chat_id)
                await gateway.send_text(event.chat_id, "Выбор сброшен. Укажите город, координаты или геолокацию.")
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
                    f"/{command} будет подключён к общему service после profile vertical slice.",
                )
                return
        if text:
            await self._resolve_profile_text(event, gateway, text)

    async def _ask_point(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.set(event.platform, event.user_id, event.chat_id, FlowState(step="await_point"))
        await gateway.send_text(
            event.chat_id,
            "Профиль GFS · шаг 1/2. Укажите город, координаты или отправьте геолокацию.",
            keyboard=UiKeyboard.from_rows([[UiButton("📍 Отправить геолокацию", "request_location")]]),
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
            )
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [
                [UiButton(_short_label(point), "callback", encode_callback("profile", "place", index))]
                for index, point in enumerate(state.candidates)
            ]
            rows.append([UiButton("Отмена", "callback", encode_callback("flow", "cancel"))])
            await gateway.send_text(event.chat_id, "Найдено несколько точек. Выберите нужную:", keyboard=UiKeyboard.from_rows(rows))
            return
        point = candidates[0]
        if parsed.lead_from_user:
            await self._run_profile(event, gateway, point, parsed.lead_hour, parsed.run)
            return
        state = FlowState(step="choose_lead", point=point)
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
            await gateway.send_text(event.chat_id, "Выбор отменён. Укажите город, координаты или геолокацию.")
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
                await self._run_profile(event, gateway, point, state.pending_lead, None)
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
            await self._run_profile(event, gateway, state.point, lead, None)
            return
        if data.scope == "profile" and data.action == "leadpage":
            try:
                page = max(0, int(data.value or "0"))
            except ValueError:
                page = 0
            await self._send_lead_picker(event, gateway, state, page=page)
            return
        await gateway.send_text(event.chat_id, "Кнопка больше не поддерживается. Начните заново: /start")

    async def _send_lead_picker(self, event: NormalizedEvent, gateway: MessengerGateway, state: FlowState, *, page: int) -> None:
        leads = self.deps.leads()
        if page <= 0:
            values = [lead for lead in QUICK_LEADS if lead in leads]
            rows = _lead_rows(values)
            rows.append([UiButton("Все сроки до +384", "callback", encode_callback("profile", "leadpage", 1))])
            page_text = "быстрые сроки"
        else:
            start = (page - 1) * LEAD_PAGE_SIZE
            values = leads[start : start + LEAD_PAGE_SIZE]
            if not values:
                page = max(1, (len(leads) - 1) // LEAD_PAGE_SIZE + 1)
                start = (page - 1) * LEAD_PAGE_SIZE
                values = leads[start : start + LEAD_PAGE_SIZE]
            rows = _lead_rows(values)
            nav: list[UiButton] = []
            if page > 1:
                nav.append(UiButton("‹", "callback", encode_callback("profile", "leadpage", page - 1)))
            nav.append(UiButton("Быстрые", "callback", encode_callback("profile", "leadpage", 0)))
            if start + LEAD_PAGE_SIZE < len(leads):
                nav.append(UiButton("›", "callback", encode_callback("profile", "leadpage", page + 1)))
            rows.append(nav)
            page_text = f"страница {page}"
        state.lead_page = page
        state.step = "choose_lead"
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await gateway.send_text(
            event.chat_id,
            f"📍 {getattr(state.point, 'label', 'выбранная точка')}\nВыберите срок GFS ({page_text}):",
            keyboard=UiKeyboard.from_rows(rows),
        )

    async def _run_profile(self, event: NormalizedEvent, gateway: MessengerGateway, point: Any, lead: int, run: Any | None) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        status = await gateway.send_text(
            event.chat_id,
            f"⏳ Профиль GFS\n📍 {getattr(point, 'label', '')}\n🕒 +{lead} ч\n1/5 Проверяю опубликованный цикл…",
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
        lines.append("GFS — модель, не наблюдение.")
        await gateway.send_text(event.chat_id, "\n".join(lines))


def _command_args(text: str) -> str:
    if not text.startswith("/"):
        return text
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _short_label(point: Any) -> str:
    return (" ".join(str(getattr(point, "label", point)).split())[:58] or "Точка")


def _lead_rows(values: list[int]) -> list[list[UiButton]]:
    return [
        [UiButton(f"+{lead}", "callback", encode_callback("profile", "lead", lead)) for lead in values[start : start + 3]]
        for start in range(0, len(values), 3)
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
