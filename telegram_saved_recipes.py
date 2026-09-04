from __future__ import annotations

"""Multi-state saved scenarios layered on the existing Telegram personal UX."""

from typing import Any, Mapping

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler

from messenger.user_recipes import RecipeLimitError, UserRecipe, UserRecipeStore
import telegram_personal_ux as personal
import telegram_user_state as user_state

_PLATFORM = "telegram"
_INSTALLED = False


def _store(db_path=None) -> UserRecipeStore:
    return UserRecipeStore(db_path or user_state.DEFAULT_DB_PATH)


def _uid(update) -> int:
    return int(getattr(getattr(update, "effective_user", None), "id", 0) or 0)


def _pref(recipe: UserRecipe) -> user_state.ProductPreference:
    return user_state.ProductPreference(int(recipe.user_id), recipe.product, dict(recipe.params),
        dict(recipe.point) if recipe.point else None, recipe.success_count, recipe.last_success_at,
        recipe.last_success_at, "pinned" if recipe.pinned else "recipe")


def _summary(recipe: UserRecipe) -> str:
    return personal._summary(_pref(recipe))


def _pin(recipe: UserRecipe) -> str:
    return "★ Открепить" if recipe.pinned else "⭐ Закрепить"


def _schedulable(recipe: UserRecipe | None) -> bool:
    return bool(recipe and recipe.product in {"profile", "aero", "windgram", "cloudgram", "map", "meteogram"})


def _result_actions_keyboard(recipe: UserRecipe | None, *, include_schedule: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if recipe and include_schedule and _schedulable(recipe):
        rows.append([InlineKeyboardButton("🕒 В расписание", callback_data=f"recipe:schedule:{recipe.recipe_id}")])
    if recipe:
        rows += [[
            InlineKeyboardButton("🔄 Обновить", callback_data=f"recipe:run:{recipe.recipe_id}"),
            InlineKeyboardButton("⚙️ Изменить", callback_data=f"recipe:edit:{recipe.recipe_id}"),
        ], [InlineKeyboardButton(_pin(recipe), callback_data=f"recipe:toggle:{recipe.recipe_id}")]]
    else:
        rows.append([InlineKeyboardButton("🔄 Обновить", callback_data="quick:last")])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="prefs:home")])
    return InlineKeyboardMarkup(rows)


def _home_text(user_id: int) -> str:
    quick = _store().quick(_PLATFORM, user_id, limit=2)
    if not quick:
        return personal._SAVED_RECIPES_ORIGINAL_HOME_TEXT(user_id)
    active = user_state.get_active_location(user_id)
    lines = ["🌦 GFS 0.25 · модельные прогнозы",
             f"📍 {active.label} · {active.lat:.4f}, {active.lon:.4f}" if active else "📍 Основная точка ещё не выбрана"]
    lines += ["", "Быстрые сценарии:", *(_summary(x) for x in quick)]
    return "\n".join([*lines, "", "Выберите продукт.", "ℹ GFS grid, не наблюдение и не радиозонд."])


def _home_keyboard(user_id: int) -> InlineKeyboardMarkup:
    base = personal._SAVED_RECIPES_ORIGINAL_HOME_KEYBOARD(user_id)
    recipes = _store().quick(_PLATFORM, user_id, limit=2)
    if not recipes:
        return base
    rows = [list(row) for row in base.inline_keyboard if not any(str(b.callback_data or "").startswith("quick:") for b in row)]
    quick = [[InlineKeyboardButton(("★ " if r.pinned else "▶ ") + _summary(r)[:58], callback_data=f"recipe:run:{r.recipe_id}")]
             for r in recipes]
    return InlineKeyboardMarkup([*quick, *rows])


def _settings_text(user_id: int) -> str:
    text = personal._SAVED_RECIPES_ORIGINAL_SETTINGS_TEXT(user_id)
    items = _store().list(_PLATFORM, user_id, limit=100)
    return text + (f"\n\n⭐ Сценарии: {len(items)} · закреплено: {sum(x.pinned for x in items)}" if items else "")


def _settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [list(r) for r in personal._SAVED_RECIPES_ORIGINAL_SETTINGS_KEYBOARD(user_id).inline_keyboard]
    rows.insert(1 if rows else 0, [InlineKeyboardButton("⭐ Сохранённые сценарии", callback_data="recipe:list")])
    return InlineKeyboardMarkup(rows)


def _list_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    items = _store().list(_PLATFORM, user_id, limit=20)
    text = "⭐ Сохранённые сценарии\n\n" + ("\n".join(("★" if r.pinned else "•") + f" {_summary(r)} · запусков {r.success_count}" for r in items) if items else "Пока нет успешных сценариев.")
    rows = [[InlineKeyboardButton(("★ " if r.pinned else "") + _summary(r)[:58], callback_data=f"recipe:view:{r.recipe_id}")] for r in items]
    rows.append([InlineKeyboardButton("← Настройки", callback_data="home:settings")])
    return text, InlineKeyboardMarkup(rows)


def _card(recipe: UserRecipe) -> tuple[str, InlineKeyboardMarkup]:
    point = personal._point_line(recipe.point) if recipe.point else "📍 Точка не сохранена"
    rows = [[InlineKeyboardButton("▶ Построить", callback_data=f"recipe:run:{recipe.recipe_id}")], [
        InlineKeyboardButton("⚙️ Изменить", callback_data=f"recipe:edit:{recipe.recipe_id}"),
        InlineKeyboardButton(_pin(recipe), callback_data=f"recipe:toggle:{recipe.recipe_id}"),
    ]]
    if _schedulable(recipe): rows.append([InlineKeyboardButton("🕒 В расписание", callback_data=f"recipe:schedule:{recipe.recipe_id}")])
    rows += [[InlineKeyboardButton("🗑 Удалить", callback_data=f"recipe:deleteconfirm:{recipe.recipe_id}")],
             [InlineKeyboardButton("← Все сценарии", callback_data="recipe:list")]]
    return f"{'★ ' if recipe.pinned else ''}{_summary(recipe)}\n{point}\nУспешных запусков: {recipe.success_count}\n\nПовтор использует актуальный опубликованный цикл модели.", InlineKeyboardMarkup(rows)


def _get_pref(user_id: int, product: str, *, include_selection: bool, db_path=None):
    pinned = _store(db_path).default_for_product(_PLATFORM, user_id, product)
    return _pref(pinned) if pinned else personal._SAVED_RECIPES_ORIGINAL_GET_PRODUCT_PREFERENCE(user_id, product, include_selection=include_selection, db_path=db_path)


def _record(user_id: int, product: str, params: Mapping[str, Any] | None, point, *, db_path=None):
    pref = personal._SAVED_RECIPES_ORIGINAL_RECORD_PRODUCT_SUCCESS(user_id, product, params, point, db_path=db_path)
    if pref and user_id > 0: _store(db_path).record_success(_PLATFORM, user_id, pref.product, pref.params, pref.point)
    return pref


def _clear_product(user_id: int, product: str, *, db_path=None) -> None:
    personal._SAVED_RECIPES_ORIGINAL_CLEAR_PRODUCT_PREFERENCE(user_id, product, db_path=db_path); _store(db_path).clear_product(_PLATFORM, user_id, product)


def _clear_user(user_id: int, *, db_path=None) -> None:
    personal._SAVED_RECIPES_ORIGINAL_CLEAR_USER_DATA(user_id, db_path=db_path); _store(db_path).clear_user(_PLATFORM, user_id)


def _patch_schedule() -> None:
    import telegram_schedule_ux as sx
    async def offer(message, context, spec, user_id: int) -> bool:
        if user_id <= 0 or not sx._valid_spec(spec) or sx._is_scheduled_message(message):
            return False
        try:
            free = len(sx.schedules.schedule_store().list_for_user(user_id)) < sx.schedules.MAX_SCHEDULES_PER_USER
        except Exception:
            return False
        if not free:
            context.user_data.pop(sx.QUICK_SPEC_KEY, None)
            return False
        context.user_data[sx.QUICK_SPEC_KEY] = {
            "product": str(spec["product"]),
            "point": dict(spec["point"]),
            "params": dict(spec["params"]),
        }
        recipe = _store().find_matching(_PLATFORM, user_id, str(spec["product"]), dict(spec["params"]), dict(spec["point"])) or _store().latest_for_product(_PLATFORM, user_id, str(spec["product"]))
        await message.reply_text("Действия с результатом:", reply_markup=_result_actions_keyboard(recipe, include_schedule=True))
        return True
    sx.offer_schedule_for_result = offer


def _patch_route() -> None:
    import telegram_route
    if getattr(telegram_route, "_SAVED_RECIPE_ACTIONS_PATCHED", False): return
    original = telegram_route.run_route_product
    async def run(message, origin, destination, parsed, user=None):
        result = await original(message, origin, destination, parsed, user=user)
        if result is not False and not personal._is_scheduled_message(message):
            uid = int(getattr(user, "id", 0) or getattr(getattr(message, "from_user", None), "id", 0) or 0)
            if uid: await message.reply_text("Действия с результатом:", reply_markup=_result_actions_keyboard(_store().latest_for_product(_PLATFORM, uid, "route"), include_schedule=False))
        return result
    telegram_route.run_route_product = run; telegram_route._SAVED_RECIPE_ACTIONS_PATCHED = True


def install(namespace: dict[str, Any]) -> None:
    global _INSTALLED
    if _INSTALLED: return
    _INSTALLED = True
    personal._SAVED_RECIPES_ORIGINAL_HOME_TEXT = personal.home_text
    personal._SAVED_RECIPES_ORIGINAL_HOME_KEYBOARD = personal.home_keyboard
    personal._SAVED_RECIPES_ORIGINAL_SETTINGS_TEXT = personal._settings_text
    personal._SAVED_RECIPES_ORIGINAL_SETTINGS_KEYBOARD = personal._settings_keyboard
    personal._SAVED_RECIPES_ORIGINAL_GET_PRODUCT_PREFERENCE = personal.get_product_preference
    personal._SAVED_RECIPES_ORIGINAL_RECORD_PRODUCT_SUCCESS = personal.record_product_success
    personal._SAVED_RECIPES_ORIGINAL_CLEAR_PRODUCT_PREFERENCE = personal.clear_product_preference
    personal._SAVED_RECIPES_ORIGINAL_CLEAR_USER_DATA = personal.clear_user_data
    personal.home_text, personal.home_keyboard = _home_text, _home_keyboard
    personal._settings_text, personal._settings_keyboard = _settings_text, _settings_keyboard
    personal.get_product_preference, personal.record_product_success = _get_pref, _record
    personal.clear_product_preference, personal.clear_user_data = _clear_product, _clear_user
    original = namespace["_tracked_product"]
    async def tracked(message, product, city, request_text, runner, *, lead_from=None, lead_to=None, run=None, user=None):
        result = await original(message, product, city, request_text, runner, lead_from=lead_from, lead_to=lead_to, run=run, user=user)
        canonical = "aero" if str(product) == "skewt" else str(product)
        if result is not False and canonical in {"aero","windgram","cloudgram","map"} and personal._PENDING_WIZARD_STATE.get() is None and not personal._is_scheduled_message(message):
            uid = int(getattr(user or getattr(message,"from_user",None),"id",0) or 0)
            if uid: await message.reply_text("Действия с результатом:", reply_markup=_result_actions_keyboard(_store().latest_for_product(_PLATFORM,uid,canonical), include_schedule=True))
        return result
    namespace["_tracked_product"] = tracked
    _patch_schedule(); _patch_route()


async def _run(update, context, namespace, recipe: UserRecipe) -> None:
    await personal._run_preference(update, context, namespace, _pref(recipe))


def register(application, namespace: dict[str, Any]) -> None:
    async def callback(update, context):
        query = update.callback_query
        if not query: return
        data, uid, store = query.data or "", _uid(update), _store()
        if data == "recipe:list":
            await query.answer(); text,kb=_list_view(uid); await query.edit_message_text(text,reply_markup=kb); raise ApplicationHandlerStop
        parts=data.split(":")
        if len(parts)!=3 or parts[0]!="recipe": return
        action=parts[1]
        try: rid=int(parts[2])
        except ValueError: await query.answer("Сценарий устарел"); raise ApplicationHandlerStop
        recipe=store.get(_PLATFORM,uid,rid)
        if not recipe: await query.answer("Сценарий недоступен"); raise ApplicationHandlerStop
        if action=="view": await query.answer(); text,kb=_card(recipe); await query.edit_message_text(text,reply_markup=kb); raise ApplicationHandlerStop
        if action=="run": await query.answer(); await query.edit_message_text(f"Запускаю: {_summary(recipe)}…"); await _run(update,context,namespace,recipe); raise ApplicationHandlerStop
        if action=="edit": await query.answer(); await personal._edit_preference_card(update,context,namespace,_pref(recipe)); raise ApplicationHandlerStop
        if action=="toggle":
            try: updated=store.toggle_pinned(_PLATFORM,uid,rid)
            except RecipeLimitError as exc: await query.answer(str(exc),show_alert=True); raise ApplicationHandlerStop
            await query.answer("Закреплено" if updated and updated.pinned else "Откреплено"); text,kb=_card(updated or recipe); await query.edit_message_text(text,reply_markup=kb); raise ApplicationHandlerStop
        if action=="schedule":
            await query.answer(); import telegram_schedule_ux as sx; import telegram_schedules as schedules
            if not _schedulable(recipe) or not recipe.point: raise ApplicationHandlerStop
            try:
                if len(schedules.schedule_store().list_for_user(uid)) >= schedules.MAX_SCHEDULES_PER_USER: await schedules._show_manager(query,uid); raise ApplicationHandlerStop
            except schedules.ScheduleError: await schedules._show_manager(query,uid); raise ApplicationHandlerStop
            sx._clear_nested_product_state(context); await schedules._begin_timing(query,context,{"product":recipe.product,"point":dict(recipe.point),"params":dict(recipe.params)}); raise ApplicationHandlerStop
        if action=="deleteconfirm":
            await query.answer(); await query.edit_message_text(f"Удалить сценарий «{_summary(recipe)}»?",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Удалить",callback_data=f"recipe:delete:{rid}")],[InlineKeyboardButton("Отмена",callback_data=f"recipe:view:{rid}")]])); raise ApplicationHandlerStop
        if action=="delete": store.delete(_PLATFORM,uid,rid); await query.answer("Сценарий удалён"); text,kb=_list_view(uid); await query.edit_message_text(text,reply_markup=kb); raise ApplicationHandlerStop
    application.add_handler(CallbackQueryHandler(callback,pattern=r"^recipe:"),group=-7)
