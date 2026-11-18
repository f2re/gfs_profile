"""UTC labels derived only from an actual, selected GFS run."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _run_datetime(run) -> datetime:
    value = getattr(run, "run_datetime_utc", None)
    if value is None:
        value = datetime.strptime(f"{run.date}{run.cycle}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def run_time_label(run) -> str:
    return _run_datetime(run).strftime("%d.%m.%Y %H:%M UTC")


def forecast_lead_label(run, lead: int) -> str:
    """Compact button label; its picker header explicitly declares UTC."""
    valid = _run_datetime(run) + timedelta(hours=int(lead))
    return f"+{int(lead)} · {valid:%d.%m %H:%M}"


def forecast_time_lines(run, lead: int) -> str:
    valid = _run_datetime(run) + timedelta(hours=int(lead))
    return f"Данные GFS: {run_time_label(run)}\nПрогноз +{int(lead)} ч → {valid:%d.%m.%Y %H:%M} UTC"


def forecast_range_lines(run, lead_from: int, lead_to: int) -> str:
    if int(lead_from) == int(lead_to):
        return forecast_time_lines(run, lead_from)
    start = _run_datetime(run) + timedelta(hours=int(lead_from))
    end = _run_datetime(run) + timedelta(hours=int(lead_to))
    return (f"Данные GFS: {run_time_label(run)}\n"
            f"Прогноз +{int(lead_from)}…+{int(lead_to)} ч:\n"
            f"{start:%d.%m.%Y %H:%M} — {end:%d.%m.%Y %H:%M} UTC")
