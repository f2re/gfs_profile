from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ApplicationHandlerStop

import telegram_schedule_ux as ux


class _Message:
    def __init__(self, text: str = "Санкт-Петербург") -> None:
        self.text = text
        self.location = None
        self.from_user = SimpleNamespace(id=100, username="meteo")
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text: str, *args, **kwargs):
        self.replies.append((text, kwargs.get("reply_markup")))
        return SimpleNamespace()


class _Update:
    def __init__(self, message: _Message | None = None) -> None:
        self.effective_message = message or _Message()
        self.effective_user = SimpleNamespace(id=100, username="meteo")
        self.effective_chat = SimpleNamespace(id=100, type="private")
        self.callback_query = None


class _Store:
    def __init__(self, count: int = 0) -> None:
        self.count = count

    def list_for_user(self, _user_id: int):
        return [SimpleNamespace() for _ in range(self.count)]


class ScheduleProductGuardTests(unittest.TestCase):
    def test_meteogram_guard_restores_lost_nested_state(self) -> None:
        async def check() -> None:
            import telegram_meteogram

            message = _Message()
            update = _Update(message)
            context = SimpleNamespace(
                user_data={ux.PRODUCT_SETUP_KEY: {"product": "meteogram"}}
            )
            profile_text = AsyncMock()
            namespace = {
                "text_message": profile_text,
                "location_message": AsyncMock(),
            }
            meteogram_text = AsyncMock()
            with patch.object(telegram_meteogram, "meteogram_text", meteogram_text):
                with self.assertRaises(ApplicationHandlerStop):
                    await ux.schedule_setup_input_guard(
                        update, context, namespace, location=False
                    )

            state = context.user_data.get(telegram_meteogram.SESSION_KEY)
            self.assertIsInstance(state, dict)
            self.assertEqual(state["step"], "point")
            self.assertTrue(state["_schedule_setup"])
            meteogram_text.assert_awaited_once()
            profile_text.assert_not_awaited()

        asyncio.run(check())

    def test_meteogram_guard_never_falls_back_to_profile_after_point(self) -> None:
        async def check() -> None:
            import telegram_meteogram

            message = _Message("случайный текст")
            update = _Update(message)
            context = SimpleNamespace(
                user_data={
                    ux.PRODUCT_SETUP_KEY: {"product": "meteogram"},
                    telegram_meteogram.SESSION_KEY: {
                        "step": "model",
                        "_schedule_setup": True,
                    },
                }
            )
            profile_text = AsyncMock()
            namespace = {
                "text_message": profile_text,
                "location_message": AsyncMock(),
            }
            with self.assertRaises(ApplicationHandlerStop):
                await ux.schedule_setup_input_guard(
                    update, context, namespace, location=False
                )

            profile_text.assert_not_awaited()
            self.assertTrue(message.replies)
            self.assertIn("кнопками", message.replies[-1][0])

        asyncio.run(check())

    def test_lost_generic_product_state_is_not_reinterpreted_as_profile(self) -> None:
        async def check() -> None:
            message = _Message()
            update = _Update(message)
            context = SimpleNamespace(
                user_data={ux.PRODUCT_SETUP_KEY: {"product": "cloudgram"}}
            )
            profile_text = AsyncMock()
            namespace = {
                "text_message": profile_text,
                "location_message": AsyncMock(),
            }
            with self.assertRaises(ApplicationHandlerStop):
                await ux.schedule_setup_input_guard(
                    update, context, namespace, location=False
                )

            profile_text.assert_not_awaited()
            self.assertNotIn(ux.PRODUCT_SETUP_KEY, context.user_data)
            self.assertIn("выберите продукт заново", message.replies[-1][0])

        asyncio.run(check())


class ScheduleQuickOfferTests(unittest.TestCase):
    @staticmethod
    def _spec() -> dict[str, object]:
        return {
            "product": "meteogram",
            "point": {
                "lat": 59.939,
                "lon": 30.316,
                "label": "Санкт-Петербург",
                "source": "test",
            },
            "params": {
                "source_id": "gfs",
                "days": 5,
                "output_format": "pdf",
            },
        }

    def test_offer_preserves_complete_product_spec(self) -> None:
        async def check() -> None:
            message = _Message()
            context = SimpleNamespace(user_data={})
            with patch.object(ux.schedules, "schedule_store", return_value=_Store(0)):
                shown = await ux.offer_schedule_for_result(
                    message,
                    context,
                    self._spec(),
                    100,
                )
            self.assertTrue(shown)
            self.assertEqual(context.user_data[ux.QUICK_SPEC_KEY], self._spec())
            markup = message.replies[-1][1]
            self.assertEqual(
                markup.inline_keyboard[0][0].callback_data,
                "sched:quick",
            )
            self.assertIn("В расписание", markup.inline_keyboard[0][0].text)

        asyncio.run(check())

    def test_offer_hidden_when_two_slots_are_used(self) -> None:
        async def check() -> None:
            message = _Message()
            context = SimpleNamespace(user_data={ux.QUICK_SPEC_KEY: self._spec()})
            with patch.object(ux.schedules, "schedule_store", return_value=_Store(2)):
                shown = await ux.offer_schedule_for_result(
                    message,
                    context,
                    self._spec(),
                    100,
                )
            self.assertFalse(shown)
            self.assertFalse(message.replies)
            self.assertNotIn(ux.QUICK_SPEC_KEY, context.user_data)

        asyncio.run(check())

    def test_product_picker_prioritises_meteogram_and_profile(self) -> None:
        markup = ux._product_keyboard()
        first_row = markup.inline_keyboard[0]
        self.assertEqual(
            [button.callback_data for button in first_row],
            ["sched:product:meteogram", "sched:product:profile"],
        )


if __name__ == "__main__":
    unittest.main()
