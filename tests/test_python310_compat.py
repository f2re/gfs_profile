from __future__ import annotations

import unittest
from pathlib import Path


class Python310CompatibilityTests(unittest.TestCase):
    def test_meteogram_modules_do_not_use_datetime_utc_alias(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for name in (
            "meteogram_fetch.py",
            "meteogram_models.py",
            "meteogram_parse.py",
        ):
            source = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("from datetime import UTC", source, name)
            self.assertNotIn("datetime.now(UTC)", source, name)
            self.assertNotIn("tzinfo=UTC", source, name)


if __name__ == "__main__":
    unittest.main()
