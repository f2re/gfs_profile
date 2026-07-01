# 🌦️ Профиль атмосферы GFS 0.25

Инструмент для получения вертикального модельного профиля атмосферы по точке: Telegram-бот + веб-интерфейс.

Важно: это **модельная точка GFS**, а не радиозонд, не метеостанция и не локальный фактический прогноз. Все продукты строятся по ближайшему узлу сетки GFS 0.25°.

## ✅ Что умеет

- 📍 Строит профиль по координатам, городу или Telegram-геолокации.
- ⏱️ Поддерживает весь диапазон заблаговременности GFS до `+384 ч` с пагинацией.
- 🔄 Показывает процесс: проверка публикации, загрузка GRIB2, чтение xarray/cfgrib/eccodes, построение PNG/CSV.
- 🛰️ Берёт данные из NOMADS GRIB Filter, GFS 0.25°.
- ⚡ Скачивает не глобальный GRIB, а server-side subset по точке, переменным и уровням давления.
- 💾 Кэширует GRIB2 по дате, циклу, сроку, узлу сетки и набору уровней.
- 🌡️ Возвращает температуру, точку росы, влажность, геопотенциальную высоту, U/V и скорость/направление ветра.
- ❄️ Диагностирует высоты изотерм `0/-10/-20 °C`.
- 📈 Строит профильный PNG: `T/Td`, влажность, скорость ветра, ветровые барбы.
- 🧾 Строит аэрологические диаграммы `Stüve`, `Эмаграмма`, `Skew-T log-P` через MetPy.
- 🟦 Строит windgram: срок прогноза × изобарический уровень до 500 гПа; в каждой ячейке цвет скорости, стрелка направления переноса и число скорости.
- 📄 Возвращает CSV со всеми доступными изобарическими уровнями.
- 🧪 Имеет unit-тесты, runtime smoke-check и GitHub Actions workflow.

## 🧱 Архитектура

```text
Telegram / Web UI
        ↓
геокодинг / координаты / Telegram location
        ↓
progress reporter
        ↓
gfs_core.py / gfs_product_core.py
        ↓
NOMADS GRIB Filter → малый GRIB2 subset → xarray/cfgrib/eccodes → pandas
        ↓
profile PNG/CSV · MetPy aero PNG · windgram PNG
```

Ключевые модули:

```text
gfs_core.py           базовая загрузка и разбор профиля
gfs_product_core.py   level-aware слой для продуктовых режимов
aero_plot.py          MetPy renderer: Stüve / Emagram / Skew-T
aero_product.py       сборка аэрологического продукта
windgram_product.py   сборка матрицы ветер × время × уровень
windgram_plot.py      renderer windgram: цвет + стрелка + скорость
telegram_aero.py      команды /aero и /skewt
telegram_windgram.py  команда /windgram
product_progress.py   общий progress runner продуктовых задач
runtime_check.py      smoke-check импортов и зависимостей
```

Telegram-бот работает одним Python-процессом через long polling. Нет Redis, Celery, БД, webhook-сервера и отдельного API для Telegram.

## 🚀 Быстрая установка Telegram-бота

Запустите установщик из корня репозитория:

```bash
bash install_telegram_bot.sh
```

Автоматический режим:

```bash
TELEGRAM_BOT_TOKEN='123456:AA...' bash install_telegram_bot.sh --yes
```

Скрипт:

- показывает текущее состояние установки;
- ставит системные пакеты: Python, venv/pip, `rsync`, шрифты DejaVu для русских подписей Matplotlib;
- пробует поставить дополнительные пакеты сборки и `libeccodes0`;
- создаёт системного пользователя `gfsbot`;
- копирует проект в `/opt/gfs_profile`;
- создаёт `.venv`;
- ставит зависимости через `pip install --prefer-binary -r requirements.txt`;
- запускает `runtime_check.py` до старта сервиса;
- записывает `.env` с `MPLBACKEND=Agg` и `PYTHONUNBUFFERED=1`;
- создаёт systemd unit;
- включает автозапуск и запускает бота.

Проверка после установки:

```bash
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -f
```

Проверка состояния без переустановки:

```bash
bash install_telegram_bot.sh --status
```

## 🔄 Обновление после `git pull`

`git pull` обновляет только текущий git checkout. Рабочий бот запускается из `/opt/gfs_profile`, поэтому после pull нужен deploy: синхронизация кода в `/opt`, обновление зависимостей, runtime-check и перезапуск сервиса.

Ручной deploy:

```bash
git pull
bash deploy_telegram_bot.sh
```

Неразговорный режим:

```bash
bash deploy_telegram_bot.sh --yes
```

Deploy-скрипт:

- берёт lock `/tmp/gfs-profile-bot.deploy.lock`, чтобы не было двух параллельных deploy;
- синхронизирует checkout → `/opt/gfs_profile`;
- обновляет `pip`, `setuptools`, `wheel`;
- ставит зависимости с `--prefer-binary`;
- запускает `runtime_check.py` до restart;
- только после успешной проверки перезапускает systemd-сервис.

Автообновление после `git pull` через локальные git hooks:

```bash
bash install_git_hooks.sh
```

После этого `post-merge`, `post-rewrite` и `post-checkout` будут вызывать `deploy_telegram_bot.sh --yes`. Лог hooks пишется в:

```text
.git/gfs-profile-deploy.log
```

Подробности: [DEPLOY.md](DEPLOY.md).

## 🤖 Telegram UX

Лучший базовый сценарий:

```text
/start
📍 Отправить геолокацию
выбрать срок кнопкой: частые сроки или полный список до +384 ч
видеть этапы проверки, загрузки и построения
получить сводку, PNG и CSV
```

Без команды `/profile` тоже работает:

```text
Москва
55.75 37.62
Санкт-Петербург +48
```

Если город неоднозначный, бот показывает варианты inline-кнопками. После выбора точки бот предлагает сроки прогноза. Если срок указан сразу, например `Москва +24`, профиль строится без дополнительного выбора.

## Команды Telegram

```text
/start       старт и кнопка геолокации
/help        короткая инструкция
/cancel      сброс текущего выбора
/cycle       последний опубликованный анализ GFS f000
/status      доступность GFS для +0, +24, +48, +120, +240, +384 и состояние кэша
/profile     профиль атмосферы по точке, сроку и run
/aero        аэрологическая диаграмма Stüve / Emagram / Skew-T
/skewt       алиас для /aero type=skewt
/windgram    ветер по срокам и изобарическим уровням
```

Экспертные примеры:

```text
/profile Москва +24
/profile 55.75 37.62 +12
/profile Санкт-Петербург run=20260701/00 +48

/aero Москва +24
/aero Москва +24 type=stuve
/aero Москва +24 type=emagram
/aero Санкт-Петербург run=20260701/00 +24 type=skewt
/skewt Москва +24

/windgram Москва
/windgram Москва to=120
/windgram Москва to=240
/windgram Москва to=384
/windgram 55.75 37.62 run=20260701/00 from=0 to=120 step=6 top=500
```

## Сроки прогноза

Кнопки показывают частые сроки:

```text
+0 анализ, +3, +6, +12, +24, +48 ч
```

Кнопка `Все сроки до +384 ч →` открывает постраничный список допустимых GFS-сроков: `0..120` каждый час, затем `123..384` каждые 3 часа. Есть отдельная кнопка `Макс. +384 ч`.

## Что видно во время расчёта

Бот обновляет одно статусное сообщение.

Для профиля:

```text
1/5 Проверяю публикацию forecast-файла fXXX.idx
2/5 Привязываю точку к узлу GFS
3/5 Скачиваю GRIB2 из NOMADS, по возможности с процентом и размером
4/5 Читаю GRIB2 через xarray/cfgrib/eccodes
5/5 Формирую сводку, PNG и CSV
```

Для `/aero` и `/windgram` используется общий product progress runner: проверка публикации, узел GFS, cache/download, cfgrib/eccodes, построение графического продукта, отправка результата.

Если GRIB2 уже есть в кэше, бот пишет, что файл взят из кэша.

## 🧾 Аэрологические диаграммы

Команда `/aero` строит профессиональную модельную аэрологическую диаграмму через `MetPy + Matplotlib`.

Поддерживаемые типы:

```text
stuve     Stüve, режим по умолчанию
emagram   эмаграмма, T-logP
skewt     Skew-T log-P
```

На диаграмме:

- `T` — температура;
- `Td` — точка росы;
- ветровые барбы справа по U/V GFS;
- сухие адиабаты;
- влажные адиабаты;
- линии отношения смеси;
- диагностический блок: узел GFS, высоты изотерм `0/-10/-20 °C`, максимальный ветер.

Подписи и пояснения на русском, но метеорологические обозначения сохранены: `T`, `Td`, `p`, `Z`, `V`, `UTC`, `гПа`, `м/с`, `км`.

Не делается в первой версии:

- CAPE/CIN;
- hodograph;
- parcel/path от поверхности.

Причина: профиль строится по изобарическим уровням GFS, без гарантированно корректного surface parcel как у радиозонда.

## 🟦 Windgram: ветер × время × уровень

Команда `/windgram` строит матрицу по одной точке:

- по X — сроки прогноза;
- по Y — изобарические уровни от 1000 до 500 гПа;
- цвет ячейки — скорость ветра `V`, м/с;
- стрелка в ячейке — направление переноса воздуха;
- число в ячейке — скорость ветра, м/с.

Параметры:

```text
from=0      начальный срок, часы
to=120      конечный срок, часы; максимум 384
step=6      шаг по срокам
top=500     верхняя граница профиля, гПа
run=YYYYMMDD/HH  фиксированный цикл GFS
```

Критичное правило: весь windgram строится из **одного запуска GFS**. Если `run=` не указан, бот выбирает самый свежий запуск, где опубликован максимальный срок `to`. Это исключает смешивание разных циклов модели в одной диаграмме.

Для широких диапазонов, например `to=384`, PNG отправляется как документ, чтобы Telegram не сжал изображение как фото.

## ⚡ Оптимизация скачивания GFS

По умолчанию бот не скачивает глобальный GFS-файл. Он формирует NOMADS Filter URL с:

```text
file=gfs.tHHz.pgrb2.0p25.fXXX
var_TMP=on
var_RH=on
var_UGRD=on
var_VGRD=on
var_HGT=on
leftlon/rightlon/toplat/bottomlat около выбранного узла GFS
```

Уровни можно ограничить переменной:

```text
GFS_PRESSURE_LEVELS_HPA=profile
GFS_PRESSURE_LEVELS_HPA=1000,925,850,700,500,300,200,100
```

Режимы:

```text
пусто / all / full      скачать все изобарические уровни
profile / default       скачать рабочий набор уровней профиля
список через запятую    скачать только указанные уровни давления
```

Ключ кэша учитывает набор уровней, поэтому `all` и `profile` не конфликтуют. Продуктовый слой для `/aero` и `/windgram` также передаёт собственный набор уровней и использует отдельные cache keys.

## 🧪 Проверка ядра без Telegram

```bash
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 384
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24 --csv /tmp/profile.csv
```

Фиксированный цикл:

```bash
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24 --date 20260701 --cycle 00
```

Runtime smoke-check:

```bash
python runtime_check.py
```

## 🧪 Тесты

```bash
python -m unittest discover -s tests
```

Тесты покрывают:

- привязку координат к сетке GFS;
- допустимые сроки прогноза;
- сборку имени файла `gfs.tHHz.pgrb2.0p25.fXXX`;
- параметры NOMADS GRIB Filter URL;
- server-side ограничение уровней NOMADS;
- расчёт температуры, точки росы, потенциальной температуры;
- метеорологическое направление ветра по U/V-компонентам;
- интерполяцию уровня 0/-10/-20 °C;
- парсинг пользовательского Telegram-запроса;
- пагинацию сроков Telegram UI;
- форматирование сводки, CSV и PNG;
- генерацию сроков windgram.

GitHub Actions устанавливает зависимости, запускает `runtime_check.py`, затем unit tests.

## 📊 Как читать ответ

```text
Запуск         цикл модели GFS, UTC
Срок           заблаговременность прогноза, часы
Действительно  срок, на который рассчитан профиль, UTC
Узел GFS       ближайшая модельная точка 0.25°
p, гПа         изобарический уровень
Z, км          геопотенциальная высота
T/Td, °C       температура / точка росы
V, м/с         скорость ветра
dd, °          метеорологическое направление ветра, откуда дует ветер
Изотермы       интерполированные высоты 0/-10/-20 °C по изобарическим уровням
```

## 🌐 Запуск веб-интерфейса вручную

```bash
python -m venv .venv
source .venv/bin/activate
pip install --prefer-binary -r requirements.txt
python runtime_check.py
uvicorn main:app --reload
```

Открыть:

```text
http://127.0.0.1:8000
```

## ⚙️ Переменные окружения

```text
TELEGRAM_BOT_TOKEN       токен Telegram-бота
DEFAULT_LEAD             срок прогноза по умолчанию, часы; обычно 24
MAX_CONCURRENT_GFS       максимум одновременных GFS-запросов; обычно 2
MAX_CONCURRENT_GEOCODE   максимум одновременных запросов к геокодеру; обычно 2
GFS_CACHE_DIR            каталог файлового кэша GRIB2
GFS_CACHE_TTL_SECONDS    срок хранения GRIB2; обычно 86400
GFS_AVAILABILITY_CACHE_TTL_SECONDS  срок кеша проверки публикации GFS; обычно 300
GFS_REQUEST_TIMEOUT      timeout загрузки NOMADS, секунды
GFS_PRESSURE_LEVELS_HPA  ограничение уровней: all/profile/список уровней
GEOCODER_USER_AGENT      User-Agent для Nominatim fallback
GEOCODE_CACHE_DIR        каталог кэша геокодирования
GEOCODE_TIMEOUT          timeout геокодера, секунды
MPLBACKEND               backend Matplotlib; для сервиса Agg
PYTHONUNBUFFERED         не буферизовать stdout/stderr сервиса
```

Если `GFS_CACHE_DIR` или `GEOCODE_CACHE_DIR` заданы абсолютным путём, установщик создаёт каталог, выставляет права для пользователя сервиса и добавляет путь в `ReadWritePaths` systemd unit.

## 🧭 API веб-приложения

```text
GET  /healthz
GET  /api/available-cycles?date=YYYYMMDD
GET  /api/available-leads?date=YYYYMMDD&cycle=00|06|12|18
GET  /api/profile?date=YYYYMMDD&cycle=00&lead_index=24&lat=55.75&lon=37.62
POST /api/profile/start?...       фоновый расчёт
GET  /api/profile/status?job_id=...
GET  /api/cache-info
```

`/api/available-leads` возвращает только те сроки, для которых уже опубликован соответствующий `fXXX.idx`.

## 🔎 Типовые ошибки

```text
Нужно задать TELEGRAM_BOT_TOKEN
```

Токен не задан в окружении и отсутствует в `.env`.

```text
Файл GFS для YYYYMMDD HHZ +N ч ещё не опубликован
```

Цикл уже появился, но нужный forecast lead ещё не опубликован. Для автоматического выбора цикла не задавайте `run=...` / `--date --cycle`: бот сам откатится на предыдущий опубликованный цикл.

```text
Runtime check failed: metpy / scipy / pint / matplotlib
```

Зависимости графических продуктов не установились. Запустите:

```bash
bash deploy_telegram_bot.sh --yes
```

Если ошибка связана со сборкой wheel на старой системе, установите системные пакеты:

```bash
sudo apt-get install -y python3-dev build-essential pkg-config fonts-dejavu-core libeccodes0
```

```text
Ошибка чтения GRIB2 через xarray/cfgrib
```

Проверьте зависимости `xarray`, `cfgrib`, `eccodes`, `eccodeslib` и доступность `eccodes` runtime. После обновления кода запустите `runtime_check.py`.

```text
NOMADS вернул HTML вместо GRIB2
```

NOMADS не отдал GRIB2. Обычно причина — временная недоступность, неверный путь или слишком ранний цикл.

```text
Город или место не найдено
```

Используйте координаты или Telegram-геолокацию.

## ⚠️ Ограничения

- GFS 0.25° сглаживает рельеф и локальные эффекты.
- В горах нижние изобарические уровни могут быть ниже поверхности модели.
- Уровни изотерм считаются по доступным изобарическим уровням, а не по фактическому радиозонду.
- Аэрологические диаграммы не рассчитывают CAPE/CIN в первой версии.
- Windgram показывает изобарические уровни до 500 гПа; для `to=384` продукт может требовать значительного времени, но использует кэш и progress.
- Геокодинг неоднозначных городов может вернуть несколько вариантов; в этом случае выберите нужный вариант кнопкой или используйте координаты.
- NOMADS может быть временно недоступен или ещё не опубликовать нужный forecast lead.

## 🧹 Кэш

- GRIB2-файлы сохраняются в `.cache_gfs/` или в `GFS_CACHE_DIR`.
- Повторный запрос той же точки, цикла, срока и набора уровней использует файл из кэша.
- Старые файлы удаляются по `GFS_CACHE_TTL_SECONDS`.
