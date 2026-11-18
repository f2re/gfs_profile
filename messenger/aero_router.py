from __future__ import annotations

"""Common MAX/VK router with profile + aerological diagram vertical slices."""

import asyncio
from threading import Lock
from typing import Any, Callable

from geocode import GeoPoint

from .aero_service import ParsedAeroInput, build_aero_product_result, parse_aero_input
from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .personal_router import PersonalMessengerRouter
from .profile_service import cleanup_product_result
from .router import LEAD_PAGE_SIZE, QUICK_LEADS, RouterDependencies, _command_args, _short_label
from .state import FlowState
from .user_recipes import RecipeLimitError, UserRecipe

START_TEXT = (
    "🌦 GFS 0.25 — модельный прогноз\n"
    "Укажите город, координаты или геолокацию. Быстрый запрос: Москва +24.\n\n"
    "Продукты: /profile, /aero. Остальные продукты подключаются к тому же общему service поэтапно.\n"
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


def _quick_recipe_label(recipe: UserRecipe) -> str:
    point = recipe.point or {}
    lead = int(recipe.params.get("lead", 24))
    marker = "★" if recipe.pinned else "▶"
    title = "Аэродиаграмма" if recipe.product == "aero" else "Профиль"
    return f"{marker} {title} · {point.get('label', 'точка')} · +{lead} ч"[:62]


def _aero_progress_text(point: Any, lead: int, event: ProgressEvent) -> str:
    data = dict(event.data)
    header = (
        "⏳ Аэрологическая диаграмма GFS\n"
        f"📍 {getattr(point, 'label', '')}\n"
        f"🕒 +{int(lead)} ч\n"
    )
    if event.stage in {"check", "run"}:
        body = "1/5 Проверяю опубликованный цикл…"
        if event.stage == "run" and data.get("run_date") and data.get("run_cycle"):
            body = f"1/5 GFS {data['run_date']} {data['run_cycle']}Z"
    elif event.stage == "grid":
        body = f"2/5 Узел GFS: {data.get('grid_lat')}, {data.get('grid_lon')}"
    elif event.stage == "cache":
        body = "3/5 Читаю данные из кэша…"
    elif event.stage in {"download_start", "download", "download_done"}:
        total = data.get("total")
        downloaded = data.get("downloaded")
        if total and downloaded:
            pct = min(100.0, float(downloaded) * 100.0 / float(total))
            body = f"3/5 Загружаю данные: {pct:.0f}%"
        else:
            body = "3/5 Загружаю модельные данные…"
    elif event.stage in {"parse_start", "parse_done", "done"}:
        body = "4/5 Считаю профиль и диагностику…"
    elif event.stage in {"plot_start", "plot_done"}:
        body = "5/5 Формирую Skew-T и годограф…"
    else:
        body = event.message or "Выполняю расчёт…"
    return header + body


def _aero_lead_rows(values: list[int]) -> list[list[UiButton]]:
    rows: list[list[UiButton]] = []
    for start in range(0, len(values), 3):
        rows.append(
            [
                UiButton(f"+{lead}", "callback", encode_callback("aero", "lead", lead))
                for lead in values[start : start + 3]
            ]
        )
    return rows


class AeroMessengerRouter(PersonalMessengerRouter):
    """Messenger-neutral profile + /aero UX for MAX and VK."""

    def __init__(
        self,
        dependencies: RouterDependencies,
        *,
        aero_builder: Callable[..., Any] = build_aero_product_result,
        aero_parser: Callable[[str, int], ParsedAeroInput] = parse_aero_input,
        **kwargs: Any,
    ) -> None:
        super().__init__(dependencies, **kwargs)
        self.aero_builder = aero_builder
        self.aero_parser = aero_parser

    @classmethod
    def default(cls, **kwargs: Any) -> "AeroMessengerRouter":
        from geocode_choices import search_location_candidates

        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    def _default_aero_recipe(self, event: NormalizedEvent) -> UserRecipe | None:
        return self.recipes.default_for_product(event.platform, event.user_id, "aero") or self.recipes.latest_for_product(
            event.platform,
            event.user_id,
            "aero",
        )

    async def _start(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        rows: list[list[UiButton]] = []
        for recipe in self.recipes.quick(event.platform, event.user_id, limit=2):
            if recipe.product in {"profile", "aero"}:
                rows.append(
                    [
                        UiButton(
                            _quick_recipe_label(recipe),
                            "callback",
                            encode_callback("recipe", "run", recipe.recipe_id),
                        )
                    ]
                )
        rows.extend(
            [
                [
                    UiButton("📈 Профиль", "callback", encode_callback("product", "open", "profile")),
                    UiButton("🧾 Аэродиаграмма", "callback", encode_callback("product", "open", "aero")),
                ],
                [UiButton("📍 Геолокация", "request_location")],
            ]
        )
        await gateway.send_text(event.chat_id, START_TEXT, keyboard=UiKeyboard.from_rows(rows))

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)

        if not command and text and state is not None and state.product == "aero":
            if state.step == "await_point":
                await self._resolve_aero_text(event, gateway, text)
            else:
                await gateway.send_text(event.chat_id, "Продолжите выбор аэродиаграммы кнопками выше или отправьте /aero заново.")
            return

        if command == "aero":
            args = _command_args(text)
            if not args:
                recipe = self._default_aero_recipe(event)
                if recipe is not None:
                    await self._send_aero_recipe_card(event, gateway, recipe)
                else:
                    await self._ask_aero_point(event, gateway)
            else:
                await self._resolve_aero_text(event, gateway, args)
            return

        await super()._text(event, gateway)

    async def _location(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "aero":
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
        next_state = FlowState(product="aero", step="choose_lead", point=point, pending_run=state.pending_run)
        self.sessions.set(event.platform, event.user_id, event.chat_id, next_state)
        await self._send_aero_lead_picker(event, gateway, next_state, page=0)

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
            if recipe is None or recipe.product != "aero":
                await super()._callback(event, gateway)
                return
            await gateway.answer_callback(event)
            if data.action == "run":
                point = _recipe_point(recipe)
                if point is None:
                    await gateway.send_text(event.chat_id, "В сценарии потеряна точка. Создайте аэродиаграмму заново.")
                    return
                await self._run_aero(event, gateway, point, int(recipe.params.get("lead", 24)), None)
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
                await self._send_aero_recipe_card(event, gateway, updated)
                return
            if data.action == "change":
                await self._ask_aero_point(event, gateway)
                return
            await gateway.send_text(event.chat_id, "Кнопка сценария больше не поддерживается.")
            return

        if data.scope == "product" and data.action == "open" and data.value == "aero":
            await gateway.answer_callback(event)
            recipe = self._default_aero_recipe(event)
            if recipe is not None:
                await self._send_aero_recipe_card(event, gateway, recipe)
            else:
                await self._ask_aero_point(event, gateway)
            return

        if data.scope != "aero":
            await super()._callback(event, gateway)
            return

        await gateway.answer_callback(event)
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if state is None or state.product != "aero":
            await gateway.send_text(event.chat_id, "Сценарий аэродиаграммы устарел. Начните заново: /aero")
            return

        if data.action == "place":
            try:
                point = state.candidates[int(data.value or "")]
            except (ValueError, IndexError):
                await gateway.send_text(event.chat_id, "Вариант точки устарел. Повторите /aero.")
                return
            if state.pending_lead is not None:
                await self._run_aero(event, gateway, point, state.pending_lead, state.pending_run)
                return
            state.point = point
            state.step = "choose_lead"
            state.candidates.clear()
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            await self._send_aero_lead_picker(event, gateway, state, page=0)
            return

        if data.action == "lead":
            if state.point is None:
                await gateway.send_text(event.chat_id, "Точка выбора потеряна. Начните заново: /aero")
                return
            try:
                lead = int(data.value or "")
            except ValueError:
                await gateway.send_text(event.chat_id, "Некорректный срок прогноза.")
                return
            if lead not in self.deps.leads():
                await gateway.send_text(event.chat_id, "Этот срок GFS недоступен.")
                return
            await self._run_aero(event, gateway, state.point, lead, state.pending_run)
            return

        if data.action == "leadpage":
            try:
                page = max(0, int(data.value or "0"))
            except ValueError:
                page = 0
            await self._send_aero_lead_picker(event, gateway, state, page=page)
            return

        await gateway.send_text(event.chat_id, "Кнопка аэродиаграммы больше не поддерживается. Начните заново: /aero")

    async def _ask_aero_point(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.set(
            event.platform,
            event.user_id,
            event.chat_id,
            FlowState(product="aero", step="await_point"),
        )
        await gateway.send_text(
            event.chat_id,
            "🧾 Аэрологическая диаграмма GFS · шаг 1/2. Укажите город, координаты или отправьте геолокацию.",
            keyboard=UiKeyboard.from_rows([[UiButton("📍 Отправить геолокацию", "request_location")]]),
        )

    async def _resolve_aero_text(self, event: NormalizedEvent, gateway: MessengerGateway, raw: str) -> None:
        try:
            parsed = self.aero_parser(raw, self.default_lead)
            candidates = await asyncio.to_thread(self.deps.geocode, parsed.location_query, 5)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Ошибка: {exc}")
            return
        if not candidates:
            await gateway.send_text(event.chat_id, "Точка не найдена. Уточните город или используйте координаты.")
            return
        if len(candidates) > 1:
            state = FlowState(
                product="aero",
                step="choose_place",
                candidates=list(candidates[:5]),
                pending_lead=parsed.lead_hour if parsed.lead_from_user else None,
                pending_run=parsed.run,
            )
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            rows = [
                [UiButton(_short_label(point), "callback", encode_callback("aero", "place", index))]
                for index, point in enumerate(state.candidates)
            ]
            rows.append([UiButton("Отмена", "callback", encode_callback("flow", "cancel"))])
            await gateway.send_text(
                event.chat_id,
                "Найдено несколько точек. Выберите нужную:",
                keyboard=UiKeyboard.from_rows(rows),
            )
            return
        point = candidates[0]
        if parsed.lead_from_user:
            await self._run_aero(event, gateway, point, parsed.lead_hour, parsed.run)
            return
        state = FlowState(product="aero", step="choose_lead", point=point, pending_run=parsed.run)
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await self._send_aero_lead_picker(event, gateway, state, page=0)

    async def _send_aero_lead_picker(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        state: FlowState,
        *,
        page: int,
    ) -> None:
        leads = self.deps.leads()
        if page <= 0:
            values = [lead for lead in QUICK_LEADS if lead in leads]
            rows = _aero_lead_rows(values)
            rows.append([UiButton("Все сроки до +384", "callback", encode_callback("aero", "leadpage", 1))])
            page_text = "быстрые сроки"
        else:
            start = (page - 1) * LEAD_PAGE_SIZE
            values = leads[start : start + LEAD_PAGE_SIZE]
            if not values:
                page = max(1, (len(leads) - 1) // LEAD_PAGE_SIZE + 1)
                start = (page - 1) * LEAD_PAGE_SIZE
                values = leads[start : start + LEAD_PAGE_SIZE]
            rows = _aero_lead_rows(values)
            nav: list[UiButton] = []
            if page > 1:
                nav.append(UiButton("‹", "callback", encode_callback("aero", "leadpage", page - 1)))
            nav.append(UiButton("Быстрые", "callback", encode_callback("aero", "leadpage", 0)))
            if start + LEAD_PAGE_SIZE < len(leads):
                nav.append(UiButton("›", "callback", encode_callback("aero", "leadpage", page + 1)))
            rows.append(nav)
            page_text = f"страница {page}"
        state.product = "aero"
        state.lead_page = page
        state.step = "choose_lead"
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        await gateway.send_text(
            event.chat_id,
            f"📍 {getattr(state.point, 'label', 'выбранная точка')}\nВыберите срок GFS для Skew-T ({page_text}):",
            keyboard=UiKeyboard.from_rows(rows),
        )

    async def _send_aero_recipe_card(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        recipe: UserRecipe,
    ) -> None:
        point = recipe.point or {}
        lead = int(recipe.params.get("lead", 24))
        pin_text = "★ Открепить" if recipe.pinned else "⭐ Закрепить"
        await gateway.send_text(
            event.chat_id,
            f"🧾 Аэрологическая диаграмма GFS\n📍 {point.get('label', 'точка')}\nСрок: +{lead} ч · Skew-T log-P · годограф",
            keyboard=UiKeyboard.from_rows(
                [
                    [UiButton("▶ Построить", "callback", encode_callback("recipe", "run", recipe.recipe_id))],
                    [
                        UiButton(pin_text, "callback", encode_callback("recipe", "toggle", recipe.recipe_id)),
                        UiButton("📍 Другая точка", "callback", encode_callback("recipe", "change", recipe.recipe_id)),
                    ],
                ]
            ),
        )

    async def _run_aero(
        self,
        event: NormalizedEvent,
        gateway: MessengerGateway,
        point: Any,
        lead: int,
        run: Any | None,
    ) -> bool:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        status = await gateway.send_text(
            event.chat_id,
            f"⏳ Аэрологическая диаграмма GFS\n📍 {getattr(point, 'label', '')}\n🕒 +{lead} ч\n1/5 Проверяю опубликованный цикл…",
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
                text = _aero_progress_text(point, lead, value)
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
                    self.aero_builder,
                    point,
                    lead,
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
                "aero",
                {"lead": int(lead), "diagram_type": "skewt"},
                point,
            )
            await self._send_aero_recipe_card(event, gateway, recipe)
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
