from __future__ import annotations

import asyncio
from threading import Lock
from typing import Any

from geocode import GeoPoint

from .callback_codec import CallbackCodecError, decode_callback, encode_callback
from .contracts import MessengerGateway, NormalizedEvent, ProgressEvent, UiButton, UiKeyboard
from .profile_service import cleanup_product_result
from .router import MessengerRouter, RouterDependencies, START_TEXT, _command_args, _profile_progress_text
from .user_recipes import RecipeLimitError, UserRecipe, UserRecipeStore


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


def _recipe_label(recipe: UserRecipe) -> str:
    point = recipe.point or {}
    lead = int(recipe.params.get("lead", 24))
    marker = "★" if recipe.pinned else "▶"
    return f"{marker} Профиль · {point.get('label', 'точка')} · +{lead} ч"[:62]


class PersonalMessengerRouter(MessengerRouter):
    """Profile vertical slice with persistent quick/pinned scenarios for MAX/VK."""

    def __init__(self, dependencies: RouterDependencies, *, recipes: UserRecipeStore | None = None, **kwargs: Any) -> None:
        super().__init__(dependencies, **kwargs)
        self.recipes = recipes or UserRecipeStore()

    @classmethod
    def default(cls, **kwargs: Any) -> "PersonalMessengerRouter":
        from geocode_choices import search_location_candidates

        return cls(RouterDependencies(geocode=search_location_candidates), **kwargs)

    def _default_profile_recipe(self, event: NormalizedEvent) -> UserRecipe | None:
        return self.recipes.default_for_product(event.platform, event.user_id, "profile") or self.recipes.latest_for_product(
            event.platform,
            event.user_id,
            "profile",
        )

    async def _start(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        self.sessions.clear(event.platform, event.user_id, event.chat_id)
        rows: list[list[UiButton]] = []
        for recipe in self.recipes.quick(event.platform, event.user_id, limit=2):
            if recipe.product == "profile":
                rows.append(
                    [
                        UiButton(
                            _recipe_label(recipe),
                            "callback",
                            encode_callback("recipe", "run", recipe.recipe_id),
                        )
                    ]
                )
        rows.extend(
            [
                [UiButton("📈 Профиль", "callback", encode_callback("product", "open", "profile"))],
                [UiButton("📍 Геолокация", "request_location")],
            ]
        )
        await gateway.send_text(event.chat_id, START_TEXT, keyboard=UiKeyboard.from_rows(rows))

    async def _text(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        command = (event.command or "").lower().lstrip("/")
        text = (event.text or "").strip()
        if command == "profile" and not _command_args(text):
            recipe = self._default_profile_recipe(event)
            if recipe is not None:
                await self._send_recipe_card(event, gateway, recipe)
                return
        await super()._text(event, gateway)

    async def _callback(self, event: NormalizedEvent, gateway: MessengerGateway) -> None:
        try:
            data = decode_callback(event.callback_payload or "")
        except CallbackCodecError:
            await super()._callback(event, gateway)
            return

        if data.scope == "recipe":
            await gateway.answer_callback(event)
            try:
                recipe_id = int(data.value or "")
            except ValueError:
                await gateway.send_text(event.chat_id, "Сохранённый сценарий повреждён. Откройте /start.")
                return
            recipe = self.recipes.get(event.platform, event.user_id, recipe_id)
            if recipe is None:
                await gateway.send_text(event.chat_id, "Сценарий больше недоступен. Откройте /start.")
                return
            if data.action == "run":
                point = _recipe_point(recipe)
                if point is None:
                    await gateway.send_text(event.chat_id, "В сценарии потеряна точка. Создайте профиль заново.")
                    return
                await self._run_profile(event, gateway, point, int(recipe.params.get("lead", 24)), None)
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
                await self._send_recipe_card(event, gateway, updated)
                return
            if data.action == "change":
                await self._ask_point(event, gateway)
                return

        if data.scope == "product" and data.action == "open" and data.value == "profile":
            recipe = self._default_profile_recipe(event)
            if recipe is not None:
                await gateway.answer_callback(event)
                await self._send_recipe_card(event, gateway, recipe)
                return
        await super()._callback(event, gateway)

    async def _send_recipe_card(self, event: NormalizedEvent, gateway: MessengerGateway, recipe: UserRecipe) -> None:
        point = recipe.point or {}
        lead = int(recipe.params.get("lead", 24))
        pin_text = "★ Открепить" if recipe.pinned else "⭐ Закрепить"
        await gateway.send_text(
            event.chat_id,
            f"📈 Профиль GFS\n📍 {point.get('label', 'точка')}\nСрок: +{lead} ч",
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
                "profile",
                {"lead": int(lead)},
                point,
            )
            await self._send_recipe_card(event, gateway, recipe)
        except Exception as exc:
            stop = True
            await reporter_task
            await gateway.edit_text(event.chat_id, status.message_id, f"Ошибка расчёта: {exc}")
        finally:
            if result is not None:
                cleanup_product_result(result)
