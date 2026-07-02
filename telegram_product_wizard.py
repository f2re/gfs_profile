from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PRODUCT_WIZARD_KEY = "product_wizard"

AERO_LEADS = (12, 24, 48, 72, 120)
AERO_TYPES = ("stuve", "emagram", "skewt")
WINDGRAM_PARAMS = (("wind", "Ветер"), ("temp", "Температура"), ("rh", "Влажность"))
WINDGRAM_TO_HOURS = (120, 240, 384)
WINDGRAM_STEPS = (3, 6, 12)
WINDGRAM_TOPS = (500,)
CLOUDGRAM_TO_HOURS = (24, 48, 72, 120)
CLOUDGRAM_STEPS = (3, 6)
CLOUDGRAM_MODES = (("pro", "Профи"), ("simple", "Упрощённо"))
MAP_LEADS = (12, 24, 48, 72)
MAP_TO_HOURS = (24, 48, 72)
MAP_STEPS = (3, 6)


def start_aero_wizard_state(default_lead: int, diagram_type: str = "stuve") -> dict[str, object]:
    return {"product": "aero", "step": "await_point", "lead": int(default_lead), "diagram_type": diagram_type}


def start_windgram_wizard_state() -> dict[str, object]:
    return {"product": "windgram", "step": "await_point", "from": 0, "to": 120, "time_step": 6, "top": 500, "param": "wind"}


def start_cloudgram_wizard_state() -> dict[str, object]:
    return {"product": "cloudgram", "step": "await_point", "from": 0, "to": 72, "time_step": 3, "mode": "pro"}


def start_map_wizard_state(default_lead: int = 24) -> dict[str, object]:
    return {"product": "map", "step": "await_point", "lead": int(default_lead), "from": 0, "to": 24, "time_step": 3, "anim": False, "radius": 100}


def product_title(product: str) -> str:
    if product == "aero":
        return "Аэродиаграмма GFS"
    if product == "windgram":
        return "Windgram GFS"
    if product == "cloudgram":
        return "Cloudgram GFS"
    if product == "map":
        return "Карта GFS"
    return "Продукт GFS"


def point_prompt_text(state: dict[str, object]) -> str:
    product = str(state.get("product", ""))
    return (
        f"{product_title(product)}\n"
        "Шаг 1/3 — точка\n\n"
        "Отправьте город, координаты или геолокацию Telegram.\n\n"
        "Примеры:\n"
        "Москва\n"
        "55.75 37.62\n"
        "Краснодар"
    )


def place_keyboard(labels: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label[:58], callback_data=f"wiz:place:{index}")] for index, label in enumerate(labels[:5])]
    rows.append([InlineKeyboardButton("Отмена", callback_data="wiz:cancel")])
    return InlineKeyboardMarkup(rows)


def _point_line(state: dict[str, object]) -> str:
    point = state.get("point")
    if not isinstance(point, dict):
        return "Точка: не выбрана"
    label = str(point.get("label", "точка"))
    lat = float(point.get("lat", 0.0))
    lon = float(point.get("lon", 0.0))
    return f"Точка: {label}\n{lat:.4f}, {lon:.4f}"


def _param_label(param: str) -> str:
    return {"wind": "ветер", "temp": "температура", "rh": "влажность"}.get(param, param)


def _cloud_mode_label(mode: str) -> str:
    return "упрощённо" if mode == "simple" else "профи"


def copy_command(state: dict[str, object]) -> str | None:
    point = state.get("point")
    if not isinstance(point, dict):
        return None
    lat = float(point.get("lat", 0.0))
    lon = float(point.get("lon", 0.0))
    product = str(state.get("product", ""))
    if product == "aero":
        return f"/aero {lat:.4f} {lon:.4f} +{int(state.get('lead', 24))} type={str(state.get('diagram_type', 'stuve'))}"
    if product == "windgram":
        return (
            f"/windgram {lat:.4f} {lon:.4f} "
            f"from={int(state.get('from', 0))} to={int(state.get('to', 120))} "
            f"step={int(state.get('time_step', 6))} top={int(state.get('top', 500))} param={str(state.get('param', 'wind'))}"
        )
    if product == "cloudgram":
        return (
            f"/cloudgram {lat:.4f} {lon:.4f} "
            f"from={int(state.get('from', 0))} to={int(state.get('to', 72))} "
            f"step={int(state.get('time_step', 3))} mode={str(state.get('mode', 'pro'))}"
        )
    if product == "map":
        radius = int(state.get("radius", 100))
        radius_part = "" if radius == 100 else f" radius={radius}"
        if bool(state.get("anim", False)):
            return (
                f"/map {lat:.4f} {lon:.4f} "
                f"from={int(state.get('from', 0))} to={int(state.get('to', 24))} "
                f"step={int(state.get('time_step', 3))} anim=1{radius_part}"
            )
        return f"/map {lat:.4f} {lon:.4f} +{int(state.get('lead', 24))}{radius_part}"
    return None


def _command_block(state: dict[str, object]) -> str:
    command = copy_command(state)
    return f"\n\nКоманда:\n{command}" if command else ""


def params_text(state: dict[str, object]) -> str:
    product = str(state.get("product", ""))
    if product == "aero":
        return (
            f"{product_title(product)}\n"
            "Шаг 2/3 — параметры\n\n"
            f"{_point_line(state)}\n\n"
            f"Тип: {str(state.get('diagram_type', 'stuve')).upper()}\n"
            f"Срок: +{int(state.get('lead', 24))} ч"
            f"{_command_block(state)}\n\n"
            "Измените параметр кнопкой или нажмите «Построить»."
        )
    if product == "windgram":
        param = str(state.get("param", "wind"))
        return (
            f"{product_title(product)}\n"
            "Шаг 2/3 — параметры\n\n"
            f"{_point_line(state)}\n\n"
            f"Параметр: {_param_label(param)}\n"
            f"Диапазон: +{int(state.get('from', 0))}…+{int(state.get('to', 120))} ч\n"
            f"Шаг: {int(state.get('time_step', 6))} ч\n"
            f"Уровни: до {int(state.get('top', 500))} гПа"
            f"{_command_block(state)}\n\n"
            "Цвет/число — выбранный параметр; стрелка — направление ветра."
        )
    if product == "cloudgram":
        mode = str(state.get("mode", "pro"))
        mode_hint = "детальная таблица" if mode == "pro" else "простая схема"
        return (
            f"{product_title(product)}\n"
            "Шаг 2/3 — параметры\n\n"
            f"{_point_line(state)}\n\n"
            f"Режим: {_cloud_mode_label(mode)} ({mode_hint})\n"
            f"Диапазон: +{int(state.get('from', 0))}…+{int(state.get('to', 72))} ч\n"
            f"Шаг: {int(state.get('time_step', 3))} ч"
            f"{_command_block(state)}\n\n"
            "Профи — больше параметров; упрощённо — для быстрого чтения."
        )
    if product == "map":
        animated = bool(state.get("anim", False))
        time_line = (
            f"Анимация: +{int(state.get('from', 0))}…+{int(state.get('to', 24))} ч, шаг {int(state.get('time_step', 3))} ч"
            if animated
            else f"Срок: +{int(state.get('lead', 24))} ч"
        )
        return (
            f"{product_title(product)}\n"
            "Шаг 2/3 — параметры\n\n"
            f"{_point_line(state)}\n\n"
            f"{time_line}\n"
            f"Радиус: {int(state.get('radius', 100))} км"
            f"{_command_block(state)}\n\n"
            "Карта объединяет осадки, облачность, грозовой риск, явления, видимость и ветер AT500."
        )
    return "Параметры продукта"


def params_keyboard(state: dict[str, object]) -> InlineKeyboardMarkup:
    product = str(state.get("product", ""))
    rows: list[list[InlineKeyboardButton]] = []
    if product == "aero":
        current_type = str(state.get("diagram_type", "stuve"))
        rows.append([InlineKeyboardButton(("✓ " if current_type == item else "") + item.upper(), callback_data=f"wiz:aero:type:{item}") for item in AERO_TYPES])
        current_lead = int(state.get("lead", 24))
        rows.append([InlineKeyboardButton(("✓ " if current_lead == lead else "") + f"+{lead}ч", callback_data=f"wiz:aero:lead:{lead}") for lead in AERO_LEADS[:3]])
        rows.append([InlineKeyboardButton(("✓ " if current_lead == lead else "") + f"+{lead}ч", callback_data=f"wiz:aero:lead:{lead}") for lead in AERO_LEADS[3:]])
    elif product == "windgram":
        current_param = str(state.get("param", "wind"))
        rows.append([InlineKeyboardButton(("✓ " if current_param == key else "") + label, callback_data=f"wiz:wind:param:{key}") for key, label in WINDGRAM_PARAMS])
        current_to = int(state.get("to", 120))
        rows.append([InlineKeyboardButton(("✓ " if current_to == value else "") + f"до +{value}", callback_data=f"wiz:wind:to:{value}") for value in WINDGRAM_TO_HOURS])
        current_step = int(state.get("time_step", 6))
        rows.append([InlineKeyboardButton(("✓ " if current_step == value else "") + f"шаг {value}ч", callback_data=f"wiz:wind:step:{value}") for value in WINDGRAM_STEPS])
        rows.append([InlineKeyboardButton("✓ top 500 гПа", callback_data="wiz:wind:top:500")])
    elif product == "cloudgram":
        current_mode = str(state.get("mode", "pro"))
        rows.append([InlineKeyboardButton(("✓ " if current_mode == key else "") + label, callback_data=f"wiz:cloud:mode:{key}") for key, label in CLOUDGRAM_MODES])
        current_to = int(state.get("to", 72))
        rows.append([InlineKeyboardButton(("✓ " if current_to == value else "") + f"до +{value}", callback_data=f"wiz:cloud:to:{value}") for value in CLOUDGRAM_TO_HOURS])
        current_step = int(state.get("time_step", 3))
        rows.append([InlineKeyboardButton(("✓ " if current_step == value else "") + f"шаг {value}ч", callback_data=f"wiz:cloud:step:{value}") for value in CLOUDGRAM_STEPS])
    elif product == "map":
        animated = bool(state.get("anim", False))
        rows.append([
            InlineKeyboardButton(("✓ " if not animated else "") + "PNG", callback_data="wiz:map:anim:0"),
            InlineKeyboardButton(("✓ " if animated else "") + "GIF", callback_data="wiz:map:anim:1"),
        ])
        current_lead = int(state.get("lead", 24))
        rows.append([InlineKeyboardButton(("✓ " if current_lead == lead else "") + f"+{lead}ч", callback_data=f"wiz:map:lead:{lead}") for lead in MAP_LEADS])
        current_to = int(state.get("to", 24))
        rows.append([InlineKeyboardButton(("✓ " if current_to == value else "") + f"до +{value}", callback_data=f"wiz:map:to:{value}") for value in MAP_TO_HOURS])
        current_step = int(state.get("time_step", 3))
        rows.append([InlineKeyboardButton(("✓ " if current_step == value else "") + f"шаг {value}ч", callback_data=f"wiz:map:step:{value}") for value in MAP_STEPS])

    rows.append([InlineKeyboardButton("Построить", callback_data="wiz:run")])
    rows.append([InlineKeyboardButton("Другая точка", callback_data="wiz:point"), InlineKeyboardButton("Отмена", callback_data="wiz:cancel")])
    return InlineKeyboardMarkup(rows)


def set_point(state: dict[str, object], point: dict[str, object]) -> dict[str, object]:
    new_state = dict(state)
    new_state["point"] = point
    new_state["step"] = "params"
    new_state.pop("candidates", None)
    return new_state
