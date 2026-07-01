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


def start_aero_wizard_state(default_lead: int, diagram_type: str = "stuve") -> dict[str, object]:
    return {
        "product": "aero",
        "step": "await_point",
        "lead": int(default_lead),
        "diagram_type": diagram_type,
    }


def start_windgram_wizard_state() -> dict[str, object]:
    return {
        "product": "windgram",
        "step": "await_point",
        "from": 0,
        "to": 120,
        "time_step": 6,
        "top": 500,
        "param": "wind",
    }


def start_cloudgram_wizard_state() -> dict[str, object]:
    return {
        "product": "cloudgram",
        "step": "await_point",
        "from": 0,
        "to": 72,
        "time_step": 3,
    }


def product_title(product: str) -> str:
    if product == "aero":
        return "🧾 Аэрологическая диаграмма"
    if product == "windgram":
        return "🟦 Windgram: срок × уровень"
    if product == "cloudgram":
        return "☁️ Cloudgram: облачность и осадки"
    return "Продукт GFS"


def point_prompt_text(state: dict[str, object]) -> str:
    product = str(state.get("product", ""))
    examples = "Москва\n55.75 37.62\nКраснодар"
    return (
        f"{product_title(product)}\n\n"
        "Шаг 1/3 — выберите точку.\n"
        "Отправьте геолокацию Telegram, город или координаты.\n\n"
        f"Примеры:\n{examples}"
    )


def place_keyboard(labels: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label[:58], callback_data=f"wiz:place:{index}")] for index, label in enumerate(labels[:5])]
    rows.append([InlineKeyboardButton("✖ Отмена", callback_data="wiz:cancel")])
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
    return {"wind": "ветер V", "temp": "температура T", "rh": "влажность RH"}.get(param, param)


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
            f"step={int(state.get('time_step', 6))} top={int(state.get('top', 500))} "
            f"param={str(state.get('param', 'wind'))}"
        )
    if product == "cloudgram":
        return (
            f"/cloudgram {lat:.4f} {lon:.4f} "
            f"from={int(state.get('from', 0))} to={int(state.get('to', 72))} "
            f"step={int(state.get('time_step', 3))}"
        )
    return None


def _command_block(state: dict[str, object]) -> str:
    command = copy_command(state)
    if not command:
        return ""
    return f"\n\nКоманда для копирования:\n{command}"


def params_text(state: dict[str, object]) -> str:
    product = str(state.get("product", ""))
    if product == "aero":
        return (
            f"{product_title(product)}\n\n"
            f"{_point_line(state)}\n\n"
            "Шаг 2/3 — параметры.\n"
            f"Тип: {str(state.get('diagram_type', 'stuve')).upper()}\n"
            f"Срок: +{int(state.get('lead', 24))} ч"
            f"{_command_block(state)}\n\n"
            "Нажмите параметр для изменения или «Построить»."
        )
    if product == "windgram":
        param = str(state.get("param", "wind"))
        return (
            f"{product_title(product)}\n\n"
            f"{_point_line(state)}\n\n"
            "Шаг 2/3 — параметры.\n"
            f"Подсветка: {_param_label(param)}\n"
            f"Диапазон: +{int(state.get('from', 0))}…+{int(state.get('to', 120))} ч\n"
            f"Шаг: {int(state.get('time_step', 6))} ч\n"
            f"Верхняя граница: {int(state.get('top', 500))} гПа\n"
            "Стрелки ветра будут внутри каждой ячейки."
            f"{_command_block(state)}\n\n"
            "Нажмите параметр для изменения или «Построить»."
        )
    if product == "cloudgram":
        return (
            f"{product_title(product)}\n\n"
            f"{_point_line(state)}\n\n"
            "Шаг 2/3 — параметры.\n"
            "График один: высокая/средняя/низкая/общая облачность, зелёные осадки, тип осадков, грозовой риск и ВНГО.\n"
            f"Диапазон: +{int(state.get('from', 0))}…+{int(state.get('to', 72))} ч\n"
            f"Шаг: {int(state.get('time_step', 3))} ч"
            f"{_command_block(state)}\n\n"
            "Нажмите параметр для изменения или «Построить»."
        )
    return "Параметры продукта"


def params_keyboard(state: dict[str, object]) -> InlineKeyboardMarkup:
    product = str(state.get("product", ""))
    rows: list[list[InlineKeyboardButton]] = []
    if product == "aero":
        current_type = str(state.get("diagram_type", "stuve"))
        rows.append([
            InlineKeyboardButton(("✓ " if current_type == item else "") + item.upper(), callback_data=f"wiz:aero:type:{item}")
            for item in AERO_TYPES
        ])
        current_lead = int(state.get("lead", 24))
        rows.append([
            InlineKeyboardButton(("✓ " if current_lead == lead else "") + f"+{lead}ч", callback_data=f"wiz:aero:lead:{lead}")
            for lead in AERO_LEADS[:3]
        ])
        rows.append([
            InlineKeyboardButton(("✓ " if current_lead == lead else "") + f"+{lead}ч", callback_data=f"wiz:aero:lead:{lead}")
            for lead in AERO_LEADS[3:]
        ])
    elif product == "windgram":
        current_param = str(state.get("param", "wind"))
        rows.append([
            InlineKeyboardButton(("✓ " if current_param == key else "") + label, callback_data=f"wiz:wind:param:{key}")
            for key, label in WINDGRAM_PARAMS
        ])
        current_to = int(state.get("to", 120))
        rows.append([
            InlineKeyboardButton(("✓ " if current_to == value else "") + f"до +{value}", callback_data=f"wiz:wind:to:{value}")
            for value in WINDGRAM_TO_HOURS
        ])
        current_step = int(state.get("time_step", 6))
        rows.append([
            InlineKeyboardButton(("✓ " if current_step == value else "") + f"шаг {value}ч", callback_data=f"wiz:wind:step:{value}")
            for value in WINDGRAM_STEPS
        ])
        rows.append([
            InlineKeyboardButton("✓ top 500 гПа", callback_data="wiz:wind:top:500")
        ])
    elif product == "cloudgram":
        current_to = int(state.get("to", 72))
        rows.append([
            InlineKeyboardButton(("✓ " if current_to == value else "") + f"до +{value}", callback_data=f"wiz:cloud:to:{value}")
            for value in CLOUDGRAM_TO_HOURS
        ])
        current_step = int(state.get("time_step", 3))
        rows.append([
            InlineKeyboardButton(("✓ " if current_step == value else "") + f"шаг {value}ч", callback_data=f"wiz:cloud:step:{value}")
            for value in CLOUDGRAM_STEPS
        ])

    rows.append([InlineKeyboardButton("▶ Построить", callback_data="wiz:run")])
    rows.append([
        InlineKeyboardButton("↩ Выбрать другую точку", callback_data="wiz:point"),
        InlineKeyboardButton("✖ Отмена", callback_data="wiz:cancel"),
    ])
    return InlineKeyboardMarkup(rows)


def set_point(state: dict[str, object], point: dict[str, object]) -> dict[str, object]:
    new_state = dict(state)
    new_state["point"] = point
    new_state["step"] = "params"
    new_state.pop("candidates", None)
    return new_state
