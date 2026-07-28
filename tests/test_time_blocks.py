from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timezone

from time_blocks import utc_day_blocks


@dataclass(frozen=True)
class Item:
    valid_time_utc: datetime


class TimeBlocksTest(unittest.TestCase):
    def test_utc_day_blocks(self) -> None:
        items = [
            Item(datetime(2026, 7, 3, 0, tzinfo=timezone.utc)),
            Item(datetime(2026, 7, 3, 6, tzinfo=timezone.utc)),
            Item(datetime(2026, 7, 4, 0, tzinfo=timezone.utc)),
            Item(datetime(2026, 7, 4, 6, tzinfo=timezone.utc)),
            Item(datetime(2026, 7, 5, 0, tzinfo=timezone.utc)),
        ]
        self.assertEqual(utc_day_blocks(items), [("03.07", 0, 1), ("04.07", 2, 3), ("05.07", 4, 4)])


if __name__ == "__main__":
    unittest.main()
