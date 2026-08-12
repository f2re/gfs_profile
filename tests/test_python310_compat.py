from __future__ import annotations

import unittest
from pathlib import Path


class Python310CompatibilityTests(unittest.TestCase):
    def test_meteogram_modules_do_not_use_python311_datetime_utc(self) -> None:
        root = Path(__file__).resolve().parents[1]
        paths = sorted(root.glob("meteogram*.py"))
        paths.append(root / "telegram_meteogram.py")
        self.assertTrue(paths)

        forbidden = (
            "from datetime import UTC",
            "datetime.UTC",
            "datetime.now(UTC)",
            "datetime.now(tz=UTC)",
            "tzinfo=UTC",
            "astimezone(UTC)",
        )
        for path in paths:
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                for token in forbidden:
                    self.assertNotIn(token, source, path.name)


if __name__ == "__main__":
    unittest.main()
