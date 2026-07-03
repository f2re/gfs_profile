from __future__ import annotations


def utc_day_blocks(items, attr_name: str = "valid_time_utc") -> list[tuple[str, int, int]]:
    if not items:
        return []
    result: list[tuple[str, int, int]] = []
    start = 0
    first_time = getattr(items[0], attr_name)
    day = first_time.date()
    for idx, item in enumerate(items[1:], start=1):
        current_time = getattr(item, attr_name)
        current_day = current_time.date()
        if current_day != day:
            result.append((first_time.strftime("%d.%m"), start, idx - 1))
            start = idx
            first_time = current_time
            day = current_day
    result.append((first_time.strftime("%d.%m"), start, len(items) - 1))
    return result
