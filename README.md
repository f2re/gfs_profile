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
- 🗺️ `/map` — единая композитная карта GFS вокруг точки: осадки, облачность, гроза, ветер AT500, явления и видимость; режимы одна PNG, серия PNG и GIF.
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
map - 🗺️ Карта: PNG-серия/GIF
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
/map
```

Flow:

1. Бот просит точку: город, координаты или Telegram-геолокация.
2. В reply-клавиатуре показывает кнопку отправки текущей геолокации и до 4 последних локаций пользователя.
3. Если найдено несколько точек, показывает inline-выбор.
4. Показывает параметры кнопками.
5. Показывает команду для копирования.
6. `▶ Построить` запускает расчёт.

Последние локации общие для `/profile`, `/aero`, `/skewt`, `/windgram`, `/cloudgram` и `/map`. Они хранятся только в памяти процесса бота: после рестарта история очищается, база данных не используется.

Примеры команд, которые можно получить из wizard:

```text
/aero 45.0000 39.0000 +24 type=skewt
/windgram 45.0000 39.0000 from=0 to=120 step=6 top=500 param=temp
/cloudgram 45.0000 39.0000 from=0 to=72 step=3 mode=pro
/cloudgram 45.0000 39.0000 from=0 to=72 step=3 mode=simple
/map 45.0000 39.0000 +24
/map 45.0000 39.0000 from=0 to=24 step=3 mode=series
/map 45.0000 39.0000 from=0 to=24 step=3 mode=gif
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

/map Москва +24
/map 45.00 39.00 +24
/map Краснодар from=0 to=24 step=3 mode=series
/map Краснодар from=0 to=24 step=3 mode=gif
/map Москва +24 basemap=roads
/map Москва run=20260702/00 +36
```

## Map

`/map` всегда строит один интегрированный метеорологический продукт, а не отдельные пользовательские слои. Это модель GFS, не радар и не наблюдения. По умолчанию радиус `100 км`, кольца подписаны через `25 км`, одна статическая карта строится на `+24 ч`.

Режимы:

```text
/map Москва +24
  одна PNG-карта на срок +24 ч

/map Краснодар from=0 to=24 step=3 mode=series
  серия PNG-карт: +0, +3, +6, ... +24; сначала альбомом Telegram, при ошибке отправки — по одной

/map Краснодар from=0 to=24 step=3 mode=gif
  одна GIF-анимация по тем же срокам
```

Для совместимости старый `anim=1` по-прежнему включает GIF. Диапазон `from/to/step` без `mode=gif` трактуется как серия PNG, а не как одна последняя картинка. Количество кадров ограничено безопасным лимитом для Telegram.

Слои и легенда:

```text
Осадки       APCP, мм; если APCP нет — PRATE, мм/ч
Облачность   TCDC/TCC, %, серый прозрачный слой
Гроза        ⚡ — модельный риск по CAPE/CIN/конвективным полям, не наблюдения
AT500        разреженные стрелки UGRD/VGRD 500 гПа, м/с
Явления      значки на карте: ☔ дождь, ❄ снег/переохл. дождь, ≋ туман, ⚡ гроза
Видимость    подписи только при VIS < 10 км: например 9 км, 3 км, 0.8 км
```

Подложка использует лёгкие данные OSM/Overpass с кэшем и имеет режимы:

```text
basemap=basic   белая подложка, центр и радиусы
basemap=water   реки, водоёмы и озёра
basemap=places  города/населённые пункты + вода, режим по умолчанию
basemap=roads   города, вода и основные дороги
```

Запрос к Overpass использует `out geom`: water/rivers для `water`, water/rivers/places для `places`, water/rivers/places/major roads для `roads`. Если основной endpoint недоступен или пустой, бот пробует fallback endpoints и использует кэш успешного ответа. Если OSM всё равно недоступен, карта строится на простой белой подложке, центральная точка всё равно подписана, а footer/caption сообщает `OSM-подложка не получена` или `OSM города не получены`.

## Cloudgram

`/cloudgram` строит единый график состояния погоды по срокам GFS. Поддерживаются два режима:

```text
mode=pro      профессиональная таблица параметров
mode=simple   упрощённая схема для неметеорологов
```

### `mode=pro`

Профессиональный режим оптимизирован под оперативное чтение. Облачность больше не занимает четыре отдельные строки: одна ячейка разбита на три горизонтальные полосы `H/M/L` сверху вниз, а число в центре показывает общую облачность `TCDC, %`.

```text
Облачность          H/M/L полосы + общая облачность, %
Осадки              APCP, мм за срок
Явления             дождь / снег / переохл. дождь / туман / гроза
Видимость           VIS, км
ВНГО                cloud ceiling, м
Грозовой риск       proxy 0–3
Опасность           composite 0–4
```

Ось X: часы UTC подписаны горизонтально, под ними указана заблаговременность `+N`, а дата выводится отдельной горизонтальной строкой по центру каждого дня.

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

Для обоих режимов ось X подписывается горизонтально: UTC-время и заблаговременность `+N`.

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
weather_diagnostics.py   общие диагностики видимости, грозы и явлений
composite_map.py         загрузка spatial GRIB2 и renderer /map
telegram_product_wizard.py wizard UI
telegram_aero.py         /aero и /skewt
telegram_windgram.py     /windgram
telegram_cloudgram.py    /cloudgram и alias /clouds
telegram_map.py          /map
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
- Грозовой слой `/map` — модельный риск, не фактические молнии; пространственная детализация ограничена сеткой GFS 0.25.
- Подложка `/map` зависит от доступности Overpass, но при сбое заменяется простой белой картой.
- Для части GFS-полей возможны пропуски; продукт должен строиться с доступными слоями и показывать список отсутствующих полей.
- Для широких PNG Telegram может отправлять документ вместо фото, чтобы не сжимать изображение.
