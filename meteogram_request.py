from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from meteogram_core import MeteogramError, source_for_id, validate_days

DAYS_RE = re.compile(r"^(?:days|сутки|дней)=(?P<days>\d{1,2})$", re.IGNORECASE)
SOURCE_RE = re.compile(r"^(?:source|model|модель)=(?P<source>[\w-]+)$", re.IGNORECASE)
ENSEMBLE_RE = re.compile(r"^(?:ensemble|ансамбль)=(?P<source>[\w-]+)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MeteogramRequest:
    location_query: str
    source_id: str
    days: int


def parse_meteogram_request(raw: str) -> MeteogramRequest:
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise MeteogramError(f"Некорректная команда: {exc}") from exc
    source_id = "gfs"
    days = 5
    location_tokens: list[str] = []
    for token in tokens:
        match = DAYS_RE.match(token)
        if match:
            days = int(match.group("days"))
            continue
        match = SOURCE_RE.match(token)
        if match:
            source_id = match.group("source")
            continue
        match = ENSEMBLE_RE.match(token)
        if match:
            source_id = match.group("source")
            if source_id in {"gfs", "noaa", "gefs025"}:
                source_id = "gefs"
            elif source_id in {"ecmwf", "ifs", "ens"}:
                source_id = "ecmwf_ens"
            elif source_id in {"aifs", "aifs_ensemble"}:
                source_id = "aifs_ens"
            elif source_id in {"icon", "icon_global"}:
                source_id = "icon_eps"
            elif source_id in {"gem", "gdps", "geps"}:
                source_id = "geps"
            continue
        location_tokens.append(token)
    source = source_for_id(source_id)
    validate_days(source, days)
    location = " ".join(location_tokens).strip()
    if not location:
        raise MeteogramError(
            "Не указана точка. Пример: /meteogram Москва source=ecmwf_ifs days=5"
        )
    return MeteogramRequest(location, source.source_id, days)
