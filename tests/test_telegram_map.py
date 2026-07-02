from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geocode import GeoPoint
from gfs_core import GfsRun
from telegram_map import format_repeat_map_message, parse_map_request, send_png_series


class TelegramMapTests(unittest.TestCase):
    def test_parse_static_lead(self) -> None:
        parsed = parse_map_request("Москва +24")
        self.assertEqual(parsed.location_query, "Москва")
        self.assertEqual(parsed.lead_from, 24)
        self.assertEqual(parsed.lead_to, 24)
        self.assertFalse(parsed.animate)
        self.assertEqual(parsed.mode, "single")
        self.assertEqual(parsed.basemap, "places")
        self.assertEqual(parsed.radius_km, 100)

    def test_parse_range_without_animation_is_png_series(self) -> None:
        parsed = parse_map_request("Краснодар from=0 to=24 step=3")
        self.assertEqual(parsed.location_query, "Краснодар")
        self.assertEqual(parsed.lead_from, 0)
        self.assertEqual(parsed.lead_to, 24)
        self.assertEqual(parsed.step, 3)
        self.assertFalse(parsed.animate)
        self.assertEqual(parsed.mode, "series")

    def test_parse_range_with_animation(self) -> None:
        parsed = parse_map_request("Краснодар from=0 to=24 step=3 anim=1")
        self.assertTrue(parsed.animate)
        self.assertEqual(parsed.mode, "gif")

    def test_parse_explicit_modes_and_basemap(self) -> None:
        parsed = parse_map_request("Краснодар from=0 to=24 step=3 mode=gif basemap=roads")
        self.assertTrue(parsed.animate)
        self.assertEqual(parsed.mode, "gif")
        self.assertEqual(parsed.basemap, "roads")

    def test_parse_coordinates_and_run(self) -> None:
        parsed = parse_map_request("45.00 39.00 run=20260702/00 +36")
        self.assertEqual(parsed.location_query, "45.00 39.00")
        self.assertEqual(parsed.run, GfsRun("20260702", "00"))
        self.assertEqual(parsed.lead_from, 36)
        self.assertEqual(parsed.lead_to, 36)

    def test_radius_validation(self) -> None:
        with self.assertRaises(ValueError):
            parse_map_request("Москва +24 radius=101")

    def test_repeat_command_is_copy_friendly(self) -> None:
        point = GeoPoint(55.7558, 37.6173, "Москва", "test")
        parsed = parse_map_request("Москва run=20260702/00 +36")
        text = format_repeat_map_message(point, parsed, GfsRun("20260702", "00"))
        self.assertIn("<code>/map 55.7558 37.6173 run=20260702/00 +36</code>", text)

    def test_repeat_command_for_series_names_mode(self) -> None:
        point = GeoPoint(45.0, 39.0, "Краснодар", "test")
        parsed = parse_map_request("Краснодар from=0 to=24 step=3 basemap=water")
        text = format_repeat_map_message(point, parsed, GfsRun("20260702", "00"))
        self.assertIn("from=0 to=24 step=3 mode=series basemap=water", text)


class TelegramMapSeriesSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_png_series_falls_back_to_single_photos_when_media_group_fails(self) -> None:
        class FakeMessage:
            def __init__(self) -> None:
                self.media_group_calls = 0
                self.photos: list[str | None] = []

            async def reply_media_group(self, media):
                self.media_group_calls += 1
                raise RuntimeError("media not found")

            async def reply_photo(self, photo, caption=None):
                self.photos.append(caption)

        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for lead in (0, 3, 6):
                path = Path(tmp) / f"map_20260702_00_f{lead:03d}_test.png"
                path.write_bytes(b"png")
                paths.append(path)
            message = FakeMessage()
            await send_png_series(message, paths, "first caption")
            self.assertEqual(message.media_group_calls, 1)
            self.assertEqual(len(message.photos), 3)
            self.assertEqual(message.photos[0], "first caption")
            self.assertEqual(message.photos[1], "MAP +003 ч")


if __name__ == "__main__":
    unittest.main()
