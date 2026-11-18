"""Shared Russian descriptions and display units for every icing interface.

Numerical kernels and stored GFS heights retain their original units. This
module converts vertical products to decametres only at presentation boundaries.
"""
from __future__ import annotations

import numpy as np

from icing_config import ICING_PRODUCTS
from icing_meteorology import PRODUCT_UNITS, select_icing_field

VERTICAL_PRODUCTS = frozenset({
    "godske_sum", "godske_max", "godske_base", "godske_top", "slw_height",
})
DISPLAY_UNITS = {**PRODUCT_UNITS, **{key: "дам" for key in VERTICAL_PRODUCTS}}
SOURCE_LABEL = "Данные: NOAA/NCEP · модель GFS 0,25°"
SOURCE_URL = "https://nomads.ncep.noaa.gov/"
FIELD_SOURCE_URL = "https://www.nco.ncep.noaa.gov/pmb/products/gfs/"
METHOD_SOURCE_URL = "https://www.jma.go.jp/jma/kishou/books/nwptext/41/chapter3.pdf"
METEOROLOGY_SOURCE_URL = "https://method.meteorf.ru/methods/avia/ice/ice.html"
BASEMAP_SOURCE_URL = "https://www.naturalearthdata.com/"

PRODUCT_HINTS = {
    "slw_level": "Масса жидких облачных капель в 1 м³ воздуха при температуре ниже 0 °C.",
    "godske_sum": "Сумма вертикальной протяжённости всех слоёв с условиями по Годске.",
    "godske_max": "Вертикальная протяжённость самого мощного слоя с условиями по Годске.",
    "godske_base": "Высота нижней границы первого снизу слоя над уровнем моря.",
    "godske_top": "Высота верхней границы первого снизу слоя над уровнем моря.",
    "godske_count": "Количество отдельных слоёв с условиями по Годске над каждой точкой.",
    "slw_max": "Наибольшее содержание жидких облачных капель по высоте при температуре ниже 0 °C.",
    "slw_height": "Высота уровня, где содержание переохлаждённой облачной воды наибольшее.",
    "slwp": "Масса переохлаждённых облачных капель в столбе воздуха над площадью 1 м².",
    "agreement": "Где условия по Годске и жидкие облачные капли совпадают по высоте.",
}

PRODUCT_EXPLANATIONS = {
    "slw_level": (
        "Цвет показывает массу жидких облачных капель в одном кубическом метре воздуха "
        "на выбранном уровне при температуре ниже 0 °C. Это водность, единица — г/м³. "
        "Например, 0,2 г/м³ означает 0,2 грамма таких капель в 1 м³ воздуха. "
        "Линии с отметками 0, −10 и −20 °C показывают температуру на том же уровне."
    ),
    "godske_sum": (
        "Над каждой точкой выделяются слои с температурой и влажностью, отвечающими "
        "критерию Годске. Их вертикальная протяжённость складывается. "
        "Например, два раздельных слоя мощностью 40 и 60 дам дают 100 дам. "
        "Промежуток между слоями в сумму не входит."
    ),
    "godske_max": (
        "Из всех отдельных слоёв, отвечающих критерию Годске, выбирается самый "
        "протяжённый по вертикали. Цвет показывает его мощность в дам. "
        "Для слоёв мощностью 40 и 60 дам на карте будет 60 дам."
    ),
    "godske_base": (
        "Цвет показывает высоту нижней границы первого снизу слоя, отвечающего "
        "критерию Годске. Высота отсчитывается от уровня моря и выражена в дам. "
        "Если слой продолжается ниже первого доступного надземного уровня GFS, "
        "определить его основание по этим данным нельзя; область отмечается серым."
    ),
    "godske_top": (
        "Цвет показывает высоту верхней границы первого снизу слоя, отвечающего "
        "критерию Годске. Высота отсчитывается от уровня моря и выражена в дам. "
        "Если верх этого слоя выходит за доступные данные, область отмечается серым. "
        "При нескольких слоях показывается верх именно самого нижнего."
    ),
    "godske_count": (
        "Цвет показывает число раздельных слоёв, отвечающих критерию Годске, "
        "в доступном профиле над точкой. Слои разделяет участок, где критерий "
        "не выполняется. Пропуски и выход за доступные уровни отмечаются отдельно."
    ),
    "slw_max": (
        "Над каждой точкой сравнивается содержание жидких облачных капель на "
        "доступных уровнях GFS от 1000 до 300 гПа, расположенных выше поверхности. "
        "Из уровней с температурой ниже 0 °C берётся наибольшая водность, в г/м³. "
        "Например, 0,2 г/м³ означает, что на уровне максимума в одном кубическом "
        "метре воздуха содержится 0,2 грамма жидких облачных капель. "
        "Все уровни относятся к одному выбранному сроку прогноза."
    ),
    "slw_height": (
        "Сначала находится наибольшее содержание жидких облачных капель среди "
        "доступных уровней с температурой ниже 0 °C. Затем показывается высота "
        "этого уровня над уровнем моря, в дам. Если переохлаждённой воды нет, "
        "высота максимума не задаётся. При равных максимумах выбирается нижний уровень."
    ),
    "slwp": (
        "Цвет показывает массу жидких облачных капель при температуре ниже 0 °C "
        "во всей доступной части столба воздуха над площадью 1 м². "
        "Единица — г/м²: значение 100 означает 100 граммов облачной воды в таком столбе. "
        "Учитывается вертикальное распределение воды между уровнями GFS."
    ),
    "agreement": (
        "Сопоставляются два признака: условия по Годске и присутствие жидких "
        "облачных капель при температуре ниже 0 °C. Для воды используется порог "
        "0,001 г/м³. Цвет различает отсутствие обоих признаков, один из них, "
        "совпадение в одном слое и наличие в разных слоях. Категории описывают "
        "совпадение условий; численная вероятность обледенения здесь не рассчитывается."
    ),
}


def display_icing_field(fields: dict, product: str, level_hpa: int = 850) -> np.ndarray:
    """Convert vertical values to dam without changing the numerical source arrays."""
    values = select_icing_field(fields, product, level_hpa)
    return np.asarray(values, dtype=float) / 10.0 if product in VERTICAL_PRODUCTS else values


def method_help_text(product: str | None = None) -> str:
    """Plain-text, context-specific help shared by Telegram, MAX and VK."""
    if product is not None and product not in ICING_PRODUCTS:
        raise ValueError("Неизвестная карта обледенения")
    lines = ["Как читать карту · обледенение"]
    if product:
        lines.extend(["", ICING_PRODUCTS[product], PRODUCT_EXPLANATIONS[product]])
    else:
        lines.extend([
            "", "Вода на уровне — содержание жидких облачных капель на выбранном уровне, г/м³.",
            "Максимум по высоте — наибольшее содержание этих капель среди уровней над точкой, г/м³.",
            "Запас в столбе — суммарная масса таких капель над площадью 1 м², г/м².",
            "Годске — мощность, границы и число слоёв с подходящими температурой и влажностью.",
            "Сравнение — совпадают ли эти слои с наличием переохлаждённой воды.",
        ])
    if product is None or product.startswith("godske_") or product == "agreement":
        lines.extend([
            "", "Критерий Годске: −20 ≤ T < 0 °C и T + 8(T − Td) ≤ 0. "
            "T — температура воздуха, Td — температура точки росы относительно воды. "
            "Мощность означает вертикальную протяжённость слоя с этими условиями. "
            "Скорость нарастания и толщина льда на самолёте зависят также от свойств "
            "капель и условий полёта; здесь они не рассчитываются.",
        ])
    if product is None or product in VERTICAL_PRODUCTS:
        lines.extend(["", "Высоты и мощности — в декаметрах (дам): 1 дам = 10 м. "
                      "Высоты отсчитываются от уровня моря."])
    lines.extend([
        "", "Используются надземные уровни GFS 1000–300 гПа. Серая заливка означает "
        "недостаток данных или неопределённую границу; точечная штриховка отмечает "
        "выход слоя за доступные уровни. Диагональная штриховка означает неполные "
        "данные по высоте: часть уровней отсутствует, сумма или максимум определены "
        "только по доступной части профиля.",
        "На картах воды учитываются жидкие облачные капли при температуре ниже 0 °C. "
        "Для переохлаждённого дождя и мороси нужен отдельный расчёт. "
        "Значения ниже порога цветовой шкалы остаются без заливки.",
        "Выбранный пункт выделяется контрастной меткой и подписью с координатами.",
        "", SOURCE_LABEL + ". Поставка через NOMADS:\n" + SOURCE_URL,
        "Описание полей GFS:\n" + FIELD_SOURCE_URL,
    ])
    if product is None or product.startswith("godske_") or product == "agreement":
        lines.append("Критерий Годске: JMA, учебный текст № 41, § 3.3.2:\n" + METHOD_SOURCE_URL)
        lines.append("Условия обледенения: Гидрометцентр России:\n" + METEOROLOGY_SOURCE_URL)
    lines.append("Географическая основа: Natural Earth.\n" + BASEMAP_SOURCE_URL)
    return "\n".join(lines)


def water_profile_missing(fields: dict) -> np.ndarray:
    """Missing above-surface samples, including a missing top/bottom sample.

    One valid warm level does not establish a fully warm column if other
    above-surface levels are unavailable. Use pressure to identify expected
    levels even when their heights or water fields are themselves missing.
    """
    pressure = np.asarray(fields["pressure_hpa"], dtype=float)[:, None, None] * 100
    expected = pressure <= np.asarray(fields["surface_pressure_Pa"], dtype=float)
    return (expected & ~np.asarray(fields["water_valid_above_ground"], dtype=bool)).any(axis=0)
