from __future__ import annotations

"""Messenger-neutral /schedule management for MAX/VK common products."""

import asyncio
from typing import Any
from zoneinfo import ZoneInfo

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, UiButton, UiKeyboard
from .product_executor import ProductSnapshot
from .router import RouterDependencies
from .schedule_store import (
    MAX_SCHEDULES_PER_USER,
    MessengerSchedule,
    MessengerScheduleStore,
    ScheduleError,
    ScheduleLimitError,
    next_run_utc,
    normalize_time,
    resolve_point_timezone,
    validate_interval,
)
from .scheduler import ScheduleExecutor
from .settings_router import PRODUCT_TITLES, SettingsMessengerRouter, _recipe_label
from .state import FlowState
from .user_recipes import UserRecipe

FREQUENCIES = (1, 2, 3, 7)
TIMES = ("06:00", "09:00", "12:00", "18:00")


def _interval_label(days: int) -> str:
    days = int(days)
    return "каждый день" if days == 1 else f"каждые {days} дн."


def _schedule_line(item: MessengerSchedule) -> str:
    local = item.next_run_datetime_utc.astimezone(ZoneInfo(item.timezone))
    title = PRODUCT_TITLES.get(item.product, item.product)
    label = str((item.point or {}).get("label", "маршрут" if item.product == "route" else "точка"))
    return f"{title} · {label} · {item.local_time} · {_interval_label(item.every_days)} · след. {local:%d.%m %H:%M}"


class ScheduleMessengerRouter(SettingsMessengerRouter):
    def __init__(
        self,
        dependencies: RouterDependencies,
        *,
        schedule_store: MessengerScheduleStore | None = None,
        schedule_executor: ScheduleExecutor | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(dependencies, **kwargs)
        self.schedule_store = schedule_store or MessengerScheduleStore(self.recipes.path)
        self.schedule_executor = schedule_executor or ScheduleExecutor()

    @classmethod
    def default(cls, **kwargs: Any) -> "ScheduleMessengerRouter":
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
            [UiButton("🗺 Карта", "callback", encode_callback("product", "open", "map")), UiButton("📊 Метеограмма", "callback", encode_callback("product", "open", "meteogram"))],
            [UiButton("✈ Маршрут", "callback", encode_callback("product", "open", "route")), UiButton("🕒 Расписания", "callback", encode_callback("schedule", "open"))],
            [UiButton("⚙ Настройки", "callback", encode_callback("settings", "open")), UiButton("📍 Геолокация", "request_location")],
        ])
        point = f"\n📍 Основная точка: {active.label}" if active else ""
        await gateway.send_text(
            event.chat_id,
            "🌦 GFS 0.25 — модельный прогноз"
            f"{point}\n"
            "Все основные продукты, сохранённые сценарии и расписания доступны в одном flow.\n"
            "GFS — модель, не наблюдение и не радиозонд.",
            keyboard=UiKeyboard.from_rows(rows),
        )

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        if not command and state is not None and state.product == "schedule":
            if state.step == "frequency_custom":
                try:
                    days = validate_interval(int(text))
                except (ValueError, ScheduleError) as exc:
                    await gateway.send_text(event.chat_id, f"Введите число от 1 до 30: {exc}")
                    return
                state.params["every_days"] = days
                await self._show_time_picker(event, gateway, state)
                return
            if state.step == "time_custom":
                try:
                    local_time = normalize_time(text)
                except ScheduleError as exc:
                    await gateway.send_text(event.chat_id, str(exc))
                    return
                state.params["local_time"] = local_time
                await self._resolve_timezone(event, gateway, state)
                return
        if command == "schedule":
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            await self._show_manager(event, gateway)
            return
        await super()._text(event, gateway)

    async def _callback(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        try:
            data = decode_callback(event.callback_payload or "")
        except CallbackCodecError:
            await super()._callback(event, gateway)
            return
        if data.scope != "schedule":
            await super()._callback(event, gateway)
            return
        await gateway.answer_callback(event)
        action = data.action
        if action in {"open", "list"}:
            self.sessions.clear(event.platform, event.user_id, event.chat_id)
            await self._show_manager(event, gateway)
            return
        if action == "new":
            await self._choose_recipe(event, gateway)
            return
        if action == "recipe":
            try: recipe_id = int(data.value or "")
            except ValueError:
                await gateway.send_text(event.chat_id, "Некорректный сценарий."); return
            recipe = self.recipes.get(event.platform, event.user_id, recipe_id)
            if recipe is None:
                await gateway.send_text(event.chat_id, "Сценарий больше недоступен."); return
            state = FlowState(product="schedule", step="frequency", params={"recipe_id": recipe_id})
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            await self._show_frequency_picker(event, gateway, state, recipe)
            return
        if action == "freq":
            state = self._schedule_state(event)
            if state is None: return
            try: state.params["every_days"] = validate_interval(int(data.value or ""))
            except (ValueError, ScheduleError):
                await gateway.send_text(event.chat_id, "Интервал устарел. Откройте /schedule заново."); return
            await self._show_time_picker(event, gateway, state)
            return
        if action == "freqcustom":
            state = self._schedule_state(event)
            if state is None: return
            state.step = "frequency_custom"
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            await gateway.send_text(event.chat_id, "Введите интервал от 1 до 30 дней одним числом.")
            return
        if action == "time":
            state = self._schedule_state(event)
            if state is None: return
            try: state.params["local_time"] = normalize_time(str(data.value))
            except ScheduleError as exc:
                await gateway.send_text(event.chat_id, str(exc)); return
            await self._resolve_timezone(event, gateway, state)
            return
        if action == "timecustom":
            state = self._schedule_state(event)
            if state is None: return
            state.step = "time_custom"
            self.sessions.set(event.platform, event.user_id, event.chat_id, state)
            await gateway.send_text(event.chat_id, "Введите местное время как ЧЧ:ММ, например 05:30.")
            return
        if action == "save":
            state = self._schedule_state(event)
            if state is None: return
            await self._save_schedule(event, gateway, state)
            return
        if action == "view":
            try: schedule_id = int(data.value or "")
            except ValueError: return
            item = self.schedule_store.get(event.platform, event.user_id, schedule_id)
            if item is None:
                await gateway.send_text(event.chat_id, "Расписание больше недоступно."); return
            await self._show_schedule(event, gateway, item)
            return
        if action == "run":
            try: schedule_id = int(data.value or "")
            except ValueError: return
            item = self.schedule_store.get(event.platform, event.user_id, schedule_id)
            if item is None:
                await gateway.send_text(event.chat_id, "Расписание больше недоступно."); return
            try:
                await self.schedule_executor.execute_now(item, gateway)
            except Exception as exc:
                await gateway.send_text(event.chat_id, f"Ошибка ручного запуска: {exc}")
            else:
                await gateway.send_text(event.chat_id, "Расписание выполнено вручную; следующий автоматический срок не изменён.")
            return
        if action == "delq":
            try: schedule_id = int(data.value or "")
            except ValueError: return
            item = self.schedule_store.get(event.platform, event.user_id, schedule_id)
            if item is None:
                await gateway.send_text(event.chat_id, "Расписание уже удалено."); return
            await gateway.send_text(
                event.chat_id,
                f"Удалить расписание «{_schedule_line(item)}»?",
                keyboard=UiKeyboard.from_rows([
                    [UiButton("Удалить", "callback", encode_callback("schedule", "del", schedule_id))],
                    [UiButton("Отмена", "callback", encode_callback("schedule", "view", schedule_id))],
                ]),
            )
            return
        if action == "del":
            try: schedule_id = int(data.value or "")
            except ValueError: return
            self.schedule_store.delete(event.platform, event.user_id, schedule_id)
            await self._show_manager(event, gateway, prefix="Расписание удалено.\n\n")
            return
        if action == "home":
            await self._start(event, gateway)
            return
        await gateway.send_text(event.chat_id, "Кнопка расписания устарела. Откройте /schedule.")

    def _schedule_state(self, event: NormalizedEvent) -> FlowState | None:
        state = self.sessions.get(event.platform, event.user_id, event.chat_id)
        return state if state is not None and state.product == "schedule" else None

    async def _show_manager(self, event: NormalizedEvent, gateway: MessengerGateway, prefix: str = "") -> None:
        items = self.schedule_store.list_for_user(event.platform, event.user_id)
        lines = [f"🕒 Расписания · {len(items)}/{MAX_SCHEDULES_PER_USER}"]
        if not items:
            lines.extend(["", "Автоматическая отправка строится из сохранённого успешного сценария."])
        else:
            for index, item in enumerate(items, 1):
                lines.extend(["", f"{index}. {_schedule_line(item)}"])
                if item.last_status == "error" and item.last_error:
                    lines.append(f"   ⚠ {item.last_error[:120]}")
        rows = [[UiButton(f"{idx + 1}. {PRODUCT_TITLES.get(item.product, item.product)}", "callback", encode_callback("schedule", "view", item.schedule_id))] for idx, item in enumerate(items)]
        if len(items) < MAX_SCHEDULES_PER_USER:
            rows.append([UiButton("➕ Новое расписание", "callback", encode_callback("schedule", "new"))])
        rows.append([UiButton("🏠 Главное меню", "callback", encode_callback("schedule", "home"))])
        await gateway.send_text(event.chat_id, prefix + "\n".join(lines), keyboard=UiKeyboard.from_rows(rows))

    async def _choose_recipe(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        recipes = self.recipes.list(event.platform, event.user_id, limit=24)
        if not recipes:
            await gateway.send_text(event.chat_id, "Сначала успешно постройте нужный продукт. После этого он появится как сохранённый сценарий.")
            return
        rows = [[UiButton(_recipe_label(recipe), "callback", encode_callback("schedule", "recipe", recipe.recipe_id))] for recipe in recipes[:12]]
        rows.append([UiButton("← Расписания", "callback", encode_callback("schedule", "list"))])
        await gateway.send_text(event.chat_id, "Выберите сохранённый сценарий для автоматической отправки:", keyboard=UiKeyboard.from_rows(rows))

    async def _show_frequency_picker(self, event: NormalizedEvent, gateway: MessengerGateway, state: FlowState, recipe: UserRecipe) -> None:
        state.step = "frequency"
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        rows = [[UiButton(_interval_label(value), "callback", encode_callback("schedule", "freq", value)) for value in FREQUENCIES]]
        rows.append([UiButton("Другой интервал 1–30", "callback", encode_callback("schedule", "freqcustom"))])
        await gateway.send_text(event.chat_id, f"🕒 {_recipe_label(recipe)[2:]}\nКак часто отправлять?", keyboard=UiKeyboard.from_rows(rows))

    async def _show_time_picker(self, event: NormalizedEvent, gateway: MessengerGateway, state: FlowState) -> None:
        state.step = "time"
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        rows = [[UiButton(value, "callback", encode_callback("schedule", "time", value)) for value in TIMES[:2]], [UiButton(value, "callback", encode_callback("schedule", "time", value)) for value in TIMES[2:]]]
        rows.append([UiButton("Другое время", "callback", encode_callback("schedule", "timecustom"))])
        await gateway.send_text(event.chat_id, f"Частота: {_interval_label(int(state.params['every_days']))}\nВо сколько отправлять по местному времени точки?", keyboard=UiKeyboard.from_rows(rows))

    async def _resolve_timezone(self, event: NormalizedEvent, gateway: MessengerGateway, state: FlowState) -> None:
        recipe = self.recipes.get(event.platform, event.user_id, int(state.params.get("recipe_id", 0)))
        if recipe is None or not recipe.point:
            await gateway.send_text(event.chat_id, "У сценария отсутствует точка для определения часового пояса.")
            return
        await gateway.send_text(event.chat_id, "Определяю местный часовой пояс точки…")
        try:
            timezone_name = await asyncio.to_thread(resolve_point_timezone, recipe.point)
        except Exception as exc:
            await gateway.send_text(event.chat_id, f"Не удалось определить часовой пояс: {exc}")
            return
        state.params["timezone"] = timezone_name
        state.step = "confirm"
        self.sessions.set(event.platform, event.user_id, event.chat_id, state)
        every_days = int(state.params["every_days"])
        local_time = str(state.params["local_time"])
        first = next_run_utc(timezone_name, local_time, every_days).astimezone(ZoneInfo(timezone_name))
        await gateway.send_text(
            event.chat_id,
            f"🕒 Новое расписание\n{_recipe_label(recipe)[2:]}\n{_interval_label(every_days)} в {local_time} · {timezone_name}\nПервая отправка: {first:%d.%m.%Y %H:%M}\n\nКаждый запуск использует актуальные модельные данные, а не старый run/cycle.",
            keyboard=UiKeyboard.from_rows([
                [UiButton("Сохранить", "callback", encode_callback("schedule", "save"))],
                [UiButton("Отмена", "callback", encode_callback("schedule", "list"))],
            ]),
        )

    async def _save_schedule(self, event: NormalizedEvent, gateway: MessengerGateway, state: FlowState) -> None:
        try:
            recipe = self.recipes.get(event.platform, event.user_id, int(state.params["recipe_id"]))
            if recipe is None:
                raise ScheduleError("Сценарий больше недоступен")
            snapshot = ProductSnapshot.from_values(recipe.product, recipe.point, recipe.params)
            item = self.schedule_store.add(
                event.platform,
                event.user_id,
                event.chat_id,
                snapshot,
                str(state.params["timezone"]),
                str(state.params["local_time"]),
                int(state.params["every_days"]),
            )
        except (KeyError, ValueError, ScheduleError, ScheduleLimitError) as exc:
            await gateway.send_text(event.chat_id, f"Не удалось создать расписание: {exc}")
            return
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        await self._show_schedule(event, gateway, item, prefix="Расписание создано.\n\n")

    async def _show_schedule(self, event: NormalizedEvent, gateway: MessengerGateway, item: MessengerSchedule, prefix: str = "") -> None:
        await gateway.send_text(
            event.chat_id,
            prefix + "🕒 " + _schedule_line(item),
            keyboard=UiKeyboard.from_rows([
                [UiButton("▶ Прислать сейчас", "callback", encode_callback("schedule", "run", item.schedule_id))],
                [UiButton("🗑 Удалить", "callback", encode_callback("schedule", "delq", item.schedule_id))],
                [UiButton("← Все расписания", "callback", encode_callback("schedule", "list"))],
            ]),
        )
