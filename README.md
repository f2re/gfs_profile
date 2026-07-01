# 🌦️ Профиль атмосферы GFS 0.25

Telegram-бот и веб-интерфейс для модельных вертикальных и временных продуктов GFS 0.25 по точке. Это **модельная точка GFS**, не радиозонд, не метеостанция и не наблюдение.

## Что умеет

- 📍 Точка по координатам, городу или Telegram-геолокации.
- ⏱️ Сроки GFS до `+384 ч` для профильных продуктов.
- 🔄 Progress: публикация файла, загрузка GRIB2, чтение `cfgrib/eccodes`, построение PNG/CSV.
- ⚡ NOMADS GRIB Filter: скачивается маленький subset, а не глобальный GRIB.
- 💾 Файловый кэш по циклу, сроку, точке, набору уровней и продукту.
- 📈 `/profile` — вертикальный профиль T/Td/RH/ветер/изотермы.
- 🧾 `/aero` и `/skewt` — аэрологические диаграммы Stüve/Emagram/Skew-T через MetPy.
- 🟦 `/windgram` — срок × уровень: ветер, температура или влажность, со стрелками направления ветра.
- ☁️ `/cloudgram` — облачность, осадки, явления, видимость, грозовой риск и общая опасность в режимах `pro` и `simple`.
- 📋 После настройки wizard показывает команду для копирования и повторного запуска.

## Быстрая установка

```bash
bash install_telegram_bot.sh
```

Автоматически:

```bash
TELEGRAM_BOT_TOKEN='123456:AA...' bash install_telegram_bot.sh --yes
```

Обновление после `git pull`:

```bash
git pull
bash deploy_telegram_bot.sh --yes
```

Проверка:

```bash
python runtime_check.py
python -m unittest discover -s tests
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

## Telegram-команды

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

Регистрация из установленного каталога:

```bash
cd /opt/gfs_profile
source .venv/bin/activate
python register_telegram_commands.py
```

`/clouds` работает как alias `/cloudgram`, но в меню Telegram регистрируется только `/cloudgram`, чтобы не раздувать список команд.

## Wizard-flow

Команды без параметров запускают пошаговый выбор:

```text
/aero
/skewt
/windgram
/cloudgram
```

Flow:

1. Бот просит точку: город, координаты или Telegram-геолокация.
2. Если найдено несколько точек, показывает inline-выбор.
3. Показывает параметры кнопками.
4. Показывает команду для копирования.
5. `▶ Построить` запускает расчёт.

Примеры команд, которые можно получить из wizard:

```text
/aero 45.0000 39.0000 +24 type=skewt
/windgram 45.0000 39.0000 from=0 to=120 step=6 top=500 param=temp
/cloudgram 45.0000 39.0000 from=0 to=72 step=3 mode=pro
/cloudgram 45.0000 39.0000 from=0 to=72 step=3 mode=simple
```

## Примеры ручных команд

```text
/profile Москва +24
/profile 55.75 37.62 run=20260701/00 +48

/aero Москва +24 type=stuve
/aero Москва +24 type=emagram
/skewt Москва +24

/windgram Москва to=120 param=wind
/windgram Москва to=120 param=temp
/windgram Москва to=120 param=rh
/windgram 55.75 37.62 run=20260701/00 from=0 to=120 step=6 top=500 param=temp

/cloudgram Москва
/cloudgram Москва to=72 step=3 mode=pro
/cloudgram Москва to=72 step=3 mode=simple
/clouds Москва to=72 step=3 mode=simple
/cloudgram 55.75 37.62 run=20260701/00 from=0 to=72 step=3 mode=pro
```

## Cloudgram

`/cloudgram` строит единый график состояния погоды по срокам GFS. Поддерживаются два режима:

```text
mode=pro      профессиональная таблица параметров
mode=simple   упрощённая схема для неметеорологов
```

### `mode=pro`

Профессиональный режим показывает фиксированные строки:

```text
Высокая облачность  HCDC, %
Средняя облачность  MCDC, %
Низкая облачность   LCDC, %
Общая облачность    TCDC, %
Осадки              APCP, мм за срок
Тип осадков         R / S / FZ / IP / mix
Явления             RA / SN / FZRA / FG / TSRA
Видимость           VIS, км
ВНГО                cloud ceiling, м
Грозовой риск       proxy 0–3
Опасность           composite 0–4
```

### `mode=simple`

Упрощённый режим агрегирует параметры в понятные строки:

```text
Облака      ☀️ / 🌤️ / ⛅ / ☁️
Осадки      🌦️ / 🌧️ / 🌧️🌧️
Гроза       ⚡ / ⚡⚡ / ⛈️
Явления     🌧️ / 🌨️ / 🧊🌧️ / 🌫️ / ⛈️
Видимость   км
Опасность   ✅ / 🟡 / 🟠 / 🔴 / ⛔
```

Для обоих режимов ось X подписывается горизонтально: сверху дата/час UTC, ниже заблаговременность `+N`.

## Windgram

`/windgram` строит матрицу срок × изобарический уровень до 500 гПа. Параметр заливки:

```text
param=wind  скорость ветра V, м/с
param=temp  температура T, °C
param=rh    относительная влажность RH, %
```

Стрелка ветра остаётся внутри каждой ячейки.

## Аэрологические диаграммы

`/aero` поддерживает:

```text
type=stuve
type=emagram
type=skewt
```

`/skewt` — короткий alias для `/aero ... type=skewt`.

## Архитектура

```text
gfs_core.py              базовый профиль GFS
gfs_product_core.py      level-aware профиль для aero/windgram
gfs_subset.py            generic NOMADS subset downloader для непрофильных полей
plot_style.py            общая метеорологическая цветовая система
profile_plot_ru.py       профильный PNG
aero_plot.py             MetPy Stüve/Emagram/Skew-T
windgram_product.py      V/T/RH matrix
windgram_plot.py         windgram renderer
cloudgram_product.py     cloud/precip/thunder/ceiling summary
cloudgram_plot.py        cloudgram pro/simple renderer
telegram_product_wizard.py wizard UI
telegram_aero.py         /aero и /skewt
telegram_windgram.py     /windgram
telegram_cloudgram.py    /cloudgram и alias /clouds
telegram_commands.py     BotCommand definitions
register_telegram_commands.py registration helper
runtime_check.py         smoke-check импортов
```

## Переменные окружения

```text
TELEGRAM_BOT_TOKEN       токен Telegram-бота
DEFAULT_LEAD             срок профиля по умолчанию
MAX_CONCURRENT_GFS       лимит одновременных GFS-запросов
MAX_CONCURRENT_GEOCODE   лимит геокодинга
GFS_CACHE_DIR            каталог кэша GRIB2
GFS_CACHE_TTL_SECONDS    TTL кэша GRIB2
GFS_REQUEST_TIMEOUT      timeout NOMADS
GFS_PRESSURE_LEVELS_HPA  уровни для базового профиля: all/profile/list
MPLBACKEND               для сервиса: Agg
PYTHONUNBUFFERED         1
```

## Ограничения

- Все продукты — модель GFS, не наблюдения.
- ВНГО, грозовой риск и общая опасность в `/cloudgram` — модельная диагностика, не факт наблюдения.
- Для части GFS-полей возможны пропуски; продукт должен строиться с доступными слоями и показывать список отсутствующих полей.
- Для широких PNG Telegram может отправлять документ вместо фото, чтобы не сжимать изображение.
