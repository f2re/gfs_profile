# Telegram products: profile, aero, windgram, cloudgram

## Единый стиль графиков

Все PNG-продукты используют общий модуль `plot_style.py`.

Основные решения по UI/UX:

- светлый оперативный фон вместо дефолтного Matplotlib;
- приглушённая синоптическая сетка, не конкурирующая с данными;
- `T` — тёплый красно-оранжевый контур;
- `Td` — зелёный/бирюзовый контур;
- windgram `param=wind/temp/rh` использует разные профессиональные палитры;
- cloudgram использует единую серо-синюю шкалу облачности, зелёную шкалу осадков, сигнальную шкалу грозового риска и шкалу ВНГО;
- подписи сохраняют профессиональные обозначения `T`, `Td`, `RH`, `p`, `Z`, `V`, `UTC`, `гПа`, `м/с`, `км`, `мм`.

Цель стиля — читаемый метеорологический продукт для оперативного анализа, а не технический график Matplotlib.

## Пошаговый Telegram-flow

Команды `/aero`, `/skewt`, `/windgram` и `/cloudgram` без параметров запускают wizard:

```text
/aero
/skewt
/windgram
/cloudgram
```

Сценарий:

1. Бот просит точку: город, координаты или Telegram-геолокация.
2. Если геокодер нашёл несколько вариантов, бот показывает inline-выбор точки.
3. После выбора точки бот показывает параметры продукта кнопками.
4. На этом же экране бот показывает готовую команду для копирования.
5. Кнопка `▶ Построить` запускает расчёт.

`/clouds` работает как alias `/cloudgram`, но в меню Telegram регистрируется только `/cloudgram`, чтобы не раздувать список команд.

## Команды Telegram для регистрации

Централизованный список находится в `telegram_commands.py`. Зарегистрировать команды можно helper-скриптом:

```bash
TELEGRAM_BOT_TOKEN='123456:AA...' python register_telegram_commands.py
```

Список команд:

```text
start - 🚀 Старт и геолокация
help - ❓ Помощь и примеры
profile - 📈 Профиль GFS
aero - 🧾 Аэродиаграмма
skewt - 📉 Быстрая Skew-T
windgram - 🟦 Срок×уровень V/T/RH
cloudgram - ☁️ Облака, осадки, грозы
cycle - 🕒 Последний цикл GFS
status - ⚙️ Статус и кэш
cancel - ✖️ Сброс выбора
```

## Аэрологическая диаграмма

```text
/aero Москва +24
/aero 55.75 37.62 +48 type=stuve
/aero Санкт-Петербург run=20260701/00 +24 type=emagram
/skewt Москва +24
```

Поддерживаемые типы: `stuve`, `emagram`, `skewt`. Продукт использует модельный изобарический профиль GFS по ближайшему узлу 0.25°, не фактический радиозонд.

## Windgram: срок × уровень

```text
/windgram Москва
/windgram Москва to=120 param=wind
/windgram Москва to=120 param=temp
/windgram Москва to=120 param=rh
/windgram 55.75 37.62 run=20260701/00 from=0 to=120 step=6 top=500 param=temp
```

В каждой ячейке windgram:

- цветовая заливка показывает выбранный параметр;
- стрелка показывает направление переноса воздуха;
- число внутри ячейки показывает значение выбранного параметра.

## Cloudgram: облачность, осадки, грозовой риск, ВНГО

Единый продукт, без переключения режимов:

```text
/cloudgram Москва
/cloudgram Москва to=72 step=3
/clouds Москва to=72 step=3
/cloudgram 55.75 37.62 run=20260701/00 from=0 to=72 step=3
```

Строки графика фиксированы:

```text
Высокая облачность  HCDC, %
Средняя облачность  MCDC, %
Низкая облачность   LCDC, %
Общая облачность    TCDC, %
Осадки              APCP, мм за срок, зелёная шкала
Тип осадков         R / S / FZ / IP / смешанный код
Гроза               Cb proxy / грозовой риск 0–3
ВНГО                cloud ceiling, м
```

Правила визуализации:

- облачность показана серо-синей однородной палитрой;
- в центре каждой облачной ячейки — значение покрытия в процентах;
- осадки всегда зелёные, в центре — мм за срок;
- грозовой риск — отдельная строка 0–3, это proxy по GFS, не факт наблюдения Cb;
- ВНГО — отдельная строка с высотой в метрах или километрах;
- default: `to=72 step=3`, максимум `to=120`.

## Архитектура

```text
gfs_product_core.py        # level-aware загрузка профилей для aero/windgram
gfs_subset.py              # generic NOMADS subset downloader для непрофильных полей
plot_style.py              # общая метеорологическая цветовая система
aero_plot.py               # MetPy renderer: Stuve/Emagram/SkewT
aero_product.py            # сборка aero product
windgram_product.py        # матрица срок×уровень: V/T/RH + направление ветра
windgram_plot.py           # renderer windgram
cloudgram_product.py       # сборка summary: облака/осадки/грозы/ВНГО
cloudgram_plot.py          # renderer cloudgram
tlegram_product_wizard.py  # пошаговый Telegram UI для выбора точки и параметров
telegram_commands.py       # BotCommand definitions для меню Telegram
register_telegram_commands.py # helper регистрации slash-команд
telegram_aero.py           # Telegram command layer для /aero и /skewt
telegram_windgram.py       # Telegram command layer для /windgram
telegram_cloudgram.py      # Telegram command layer для /cloudgram и /clouds
product_progress.py        # общий progress runner для продуктовых задач
```

Существующий `/profile` оставлен как базовый сценарий и не зависит от новых продуктовых команд, но использует тот же визуальный стиль.
