from __future__ import annotations

"""Keep the established one-click schedule entry while recipe actions are enabled."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def install() -> None:
    import telegram_saved_recipes as recipes
    import telegram_schedule_ux as ux

    if getattr(ux, "_SAVED_RECIPE_SCHEDULE_COMPAT_INSTALLED", False):
        return

    async def offer_schedule_for_result(message, context, spec, user_id: int) -> bool:
        if (
            user_id <= 0
            or not ux._valid_spec(spec)
            or ux._is_scheduled_message(message)
        ):
            return False
        try:
            items = ux.schedules.schedule_store().list_for_user(user_id)
        except Exception:
            return False
        if len(items) >= ux.schedules.MAX_SCHEDULES_PER_USER:
            context.user_data.pop(ux.QUICK_SPEC_KEY, None)
            return False

        # Preserve the existing one-click timing contract: sched:quick consumes
        # this complete immutable snapshot without reopening the product wizard.
        context.user_data[ux.QUICK_SPEC_KEY] = {
            "product": str(spec["product"]),
            "point": dict(spec["point"]),
            "params": dict(spec["params"]),
        }
        recipe = recipes._store().find_matching(
            "telegram",
            user_id,
            str(spec["product"]),
            dict(spec["params"]),
            dict(spec["point"]),
        ) or recipes._store().latest_for_product(
            "telegram",
            user_id,
            str(spec["product"]),
        )

        rows = [
            [InlineKeyboardButton("🕒 В расписание", callback_data="sched:quick")]
        ]
        if recipe is not None:
            rows.extend(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Обновить",
                            callback_data=f"recipe:run:{recipe.recipe_id}",
                        ),
                        InlineKeyboardButton(
                            "⚙️ Изменить",
                            callback_data=f"recipe:edit:{recipe.recipe_id}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            recipes._pin(recipe),
                            callback_data=f"recipe:toggle:{recipe.recipe_id}",
                        )
                    ],
                ]
            )
        rows.append(
            [InlineKeyboardButton("🏠 Главное меню", callback_data="prefs:home")]
        )
        await message.reply_text(
            "Действия с результатом:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return True

    ux.offer_schedule_for_result = offer_schedule_for_result
    ux._SAVED_RECIPE_SCHEDULE_COMPAT_INSTALLED = True
