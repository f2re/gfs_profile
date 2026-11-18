"""Validated, messenger-neutral requests for regional GFS icing diagnostics."""
from __future__ import annotations

import math
import os
import re
import shlex
from dataclasses import dataclass, field

from gfs_core import GfsProfileError, GfsRun, validate_lead

METHOD_VERSION = "gfs_m8d20_spfh_multilayer_v1"
ICING_LEVELS_HPA = (1000, 975, 950, 925, 900, 850, 800, 750, 700, 650,
                    600, 550, 500, 450, 400, 350, 300)
ICING_PRODUCTS = {
    "slw_level": "Переохлаждённая вода на уровне",
    "godske_sum": "Суммарная мощность по Годске",
    "godske_max": "Мощность наибольшего слоя по Годске",
    "godske_base": "Нижняя граница первого слоя по Годске",
    "godske_top": "Верхняя граница первого слоя по Годске",
    "godske_count": "Число слоёв по Годске",
    "slw_max": "Максимальная переохлаждённая водность",
    "slw_height": "Высота максимальной водности",
    "slwp": "Запас переохлаждённой облачной воды",
    "agreement": "Сопоставление Годске и модельной воды",
}
DEFAULT_ICING_BBOX = (-15.0, 100.0, 34.0, 80.0)
ICING_MAX_GRID_POINTS = 150_000


def validate_icing_bbox(value) -> tuple[float, float, float, float]:
    try:
        if len(value) != 4:
            raise ValueError
        west, east, south, north = (round(float(x), 3) for x in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GfsProfileError("Район задаётся четырьмя числами: запад,восток,юг,север") from exc
    if not all(math.isfinite(x) for x in (west, east, south, north)):
        raise GfsProfileError("Границы района должны быть конечными числами")
    if not (-180 <= west < east <= 180 and 0 <= south < north <= 85):
        raise GfsProfileError("Район: −180 ≤ запад < восток ≤ 180; 0 ≤ юг < север ≤ 85°")
    if east - west < .25 or north - south < .25 or east - west > 180:
        raise GfsProfileError("Размер района — от 0,25° по каждой оси; ширина не более 180°")
    nodes = (math.ceil((east-west)/.25)+1) * (math.ceil((north-south)/.25)+1)
    if nodes > ICING_MAX_GRID_POINTS:
        raise GfsProfileError("Район слишком велик: уменьшите границы (не более 150 000 узлов GFS)")
    return west, east, south, north


def default_icing_bbox() -> tuple[float, float, float, float]:
    raw = os.getenv("ICING_BBOX", "").strip()
    return validate_icing_bbox(raw.split(",") if raw else DEFAULT_ICING_BBOX)


@dataclass(frozen=True)
class IcingRequest:
    product: str = "godske_sum"
    lead: int = 24
    level_hpa: int = 850
    contour_step: int = 500
    bbox: tuple[float, float, float, float] = field(default_factory=default_icing_bbox)
    run: GfsRun | None = None


def _integer(value, label: str) -> int:
    try:
        result = int(value)
        if isinstance(value, bool) or float(value) != result:
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        raise GfsProfileError(f"{label}: требуется целое число") from exc


def validate_icing_request(request: IcingRequest) -> IcingRequest:
    if not isinstance(request, IcingRequest):
        raise GfsProfileError("Некорректный запрос карты обледенения")
    if request.product not in ICING_PRODUCTS:
        raise GfsProfileError("Неизвестная карта. Откройте /icing и выберите продукт кнопками")
    lead = validate_lead(_integer(request.lead, "Срок"))
    level = _integer(request.level_hpa, "Уровень")
    if level not in ICING_LEVELS_HPA:
        raise GfsProfileError("Уровень должен входить в набор GFS 1000…300 гПа")
    contour = _integer(request.contour_step, "Шаг изолиний")
    if contour not in (100, 500):
        raise GfsProfileError("Шаг изолиний: 100 или 500 гп.м")
    bbox = validate_icing_bbox(request.bbox)
    run = request.run
    if run is not None:
        try:
            if not isinstance(run, GfsRun) or run.cycle not in ("00", "06", "12", "18"):
                raise ValueError
            if not re.fullmatch(r"\d{8}", run.date):
                raise ValueError
            run.run_datetime_utc
        except (ValueError, TypeError) as exc:
            raise GfsProfileError("Цикл задаётся как run=ГГГГММДД/00, /06, /12 или /18") from exc
    return IcingRequest(request.product, lead, level, contour, bbox, run)


def parse_icing_input(text: str) -> IcingRequest:
    """Parse optional command, product, +lead and explicit reproducible parameters."""
    try:
        tokens = shlex.split(str(text or ""))
    except ValueError as exc:
        raise GfsProfileError("Не удалось прочитать параметры /icing") from exc
    if tokens and tokens[0].split("@")[0].lower() == "/icing":
        tokens.pop(0)
    values: dict = {}
    aliases = {"level": "level_hpa", "contours": "contour_step", "contour": "contour_step"}
    for token in tokens:
        if re.fullmatch(r"\+\d{1,3}", token):
            key, value = "lead", token[1:]
        elif "=" in token:
            key, value = token.split("=", 1)
            key = aliases.get(key.lower(), key.lower())
        elif token in ICING_PRODUCTS:
            key, value = "product", token
        else:
            raise GfsProfileError("Пример: /icing product=godske_sum +24 contours=100")
        if key in values:
            raise GfsProfileError(f"Параметр {key} задан дважды")
        if key in ("lead", "level_hpa", "contour_step"):
            values[key] = _integer(value, key)
        elif key == "product":
            values[key] = value
        elif key == "bbox":
            values[key] = validate_icing_bbox(value.split(","))
        elif key == "run":
            match = re.fullmatch(r"(\d{8})/?(00|06|12|18)", value)
            if not match:
                raise GfsProfileError("Пример цикла: run=20261117/06")
            values[key] = GfsRun(*match.groups())
        else:
            raise GfsProfileError(f"Неизвестный параметр /icing: {key}")
    return validate_icing_request(IcingRequest(**values))


def icing_repeat_command(request: IcingRequest) -> str:
    r = validate_icing_request(request)
    run = f" run={r.run.date}/{r.run.cycle}" if r.run else ""
    bbox = ",".join(f"{x:g}" for x in r.bbox)
    return (f"/icing product={r.product} +{r.lead} level={r.level_hpa} "
            f"contours={r.contour_step} bbox={bbox}{run}")
