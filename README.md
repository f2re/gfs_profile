# 🌦️ GFS Profile 0.25 + WeatherNext 3

## WeatherNext временно отключён

По умолчанию `WEATHERNEXT3_ENABLED=0` (отсутствующее или неизвестное значение также
означает отключение). Наличие старых Google credentials не включает источник.
WeatherNext не показывается в меню, справке, обычных и ансамблевых моделях Telegram,
MAX и VK. Старые команды, кнопки и прямые запросы не запускают расчёт. API WN3
не регистрируется; нет импорта его провайдеров, обработки кэша или задач WN3 при
старте службы. Обычные GFS-продукты и другие источники остаются доступны.

Сохранённые сценарии и расписания WN3 (включая `meteogram` с `source/source_id=wn3`)
скрыты и не исполняются. Записи, `.env`, `.venv` и кэш не удаляются. После обычного
обновления выполните `bash deploy_telegram_bot.sh`; одного `git pull` для копии в
`/opt/gfs_profile` недостаточно. Из старых сообщений кнопки нельзя убрать массово,
но нажатия блокируются; `/start` открывает чистое меню.

`requirements.txt` больше не устанавливает Google BigQuery, Zarr и obstore.
Ранее установленные пакеты могут оставаться на диске, но не импортируются этим
источником. Повторное включение — только администратором после получения доступа:
установить `requirements-weathernext3.txt`, настроить Google, явно задать
`WEATHERNEXT3_ENABLED=1` и перезапустить службу. Для текущей работы бота этого не нужно.


Профессиональный multi-messenger бот и web/API для модельной продукции GFS 0.25° и WeatherNext 3. Один метеорологический слой обслуживает **Telegram, MAX и VK**: платформы различаются транспортом и native UI, но не расчётами, параметрами или результатами.

> GFS и WeatherNext 3 всегда обозначаются как модели. Это не наблюдение, не радар и не радиозонд.

## Продукция

| Продукт | Telegram | MAX | VK |
|---|:---:|:---:|:---:|
| 📈 `/profile` — вертикальный профиль GFS | ✅ | ✅ | ✅ |
| 🧾 `/aero` — Skew-T + годограф GFS | ✅ | ✅ | ✅ |
| 🟦 `/windgram` — срок × уровень GFS | ✅ | ✅ | ✅ |
| ☁️ `/cloudgram` — облака/осадки/риски GFS | ✅ | ✅ | ✅ |
| 🗺️ `/map` — PNG/серия/MP4 GFS | ✅ | ✅ | ✅ |
| 📊 `/meteogram` — модели/ансамбли, PNG/DOCX/PDF | ✅ | ✅ | ✅ |
| 🛰 `/wn3` — WeatherNext 3: point/meteogram/cloud/precip maps | ✅ | ✅ | ✅ |
| ✈️ `/route` — маршрутный разрез GFS PNG/CSV | ✅ | ✅ | ✅ |
| ⚙️ `/settings` | ✅ | ✅ | ✅ |
| 🕒 `/schedule` | ✅ | ✅ | ✅ |

Подробная итоговая матрица: [`docs/MESSENGER_PARITY.md`](docs/MESSENGER_PARITY.md). WeatherNext 3: [`docs/WEATHERNEXT3.md`](docs/WEATHERNEXT3.md).

## Архитектура

```text
Telegram polling ─┐
MAX webhook ──────┼→ normalized action
VK Callback API ──┘
                       ↓
               common router/use-case
                       ↓
       GFS services + WeatherNext 3 service
                       ↓
              CommonProductResult
                       ↓
           native platform gateway
```

Расчёты GFS, WeatherNext 3 provider/query logic, geocoder, выбор run, formatter, saved recipes и schedule snapshots не копируются между мессенджерами.

Production entrypoint:

```text
systemd → messenger_launcher.py
           ├─ Telegram polling
           ├─ FastAPI /webhooks/max
           ├─ FastAPI /webhooks/vk
           └─ web/API
```

Runtime остаётся single-process (`workers=1`), без Redis/Celery/внешней БД.

## Независимость платформ

```env
TELEGRAM_ENABLED=auto
MAX_ENABLED=auto
VK_ENABLED=auto
```

`auto` включает платформу при корректной конфигурации, `1` явно запрашивает её, `0` карантинирует только эту платформу.

Пример:

```text
Telegram ready
MAX      ready
VK       degraded
```

Telegram и MAX продолжают работать. `/ready` относится к общей runtime-инфраструктуре, а `/health` показывает `ready/degraded/off` по каждой платформе. Ошибка webhook/token/polling одного провайдера не должна выключать соседние.

## GFS/NOMADS

Нативные GFS-продукты используют subset, а не глобальный GRIB:

```text
NOMADS GRIB Filter
→ gfs.tHHz.pgrb2.0p25.fXXX
→ необходимые поля/уровни
→ bbox вокруг точки/маршрута
```

Перед выбором цикла проверяется публикация **максимального реально требуемого lead**. Если новый цикл содержит `f000`, но не содержит нужный `fXXX`, выбирается предыдущий опубликованный run.

Пользовательский результат показывает фактический run/cycle UTC, valid UTC, requested point и GFS grid point.

Метеорологические методы: [`docs/METEOROLOGICAL_METHODS.md`](docs/METEOROLOGICAL_METHODS.md).

## WeatherNext 3 / BigQuery

`/wn3` использует WeatherNext 3 surface ensemble statistics из Google BigQuery Analytics Hub. Отдельный provider ищет последний **реально опубликованный** init с требуемым горизонтом и запрашивает только нужную точку/регион.

Реализованы:

```text
point forecast       T/Td, RH, wind, pressure, cloud, 3 precipitation products
meteogram            mean + p10/p25/p50/p75/p90
cloud maps           total + low/mid/high
precip maps          native / IMERG / experimental
combo                cloud + native precip
animation            H.264 MP4, GIF fallback
```

T/Td используют station head 0.05° при наличии; остальные surface fields — 0.1°. BigQuery `forecast.hours` начинается с +1 ч, поэтому WN3 map default — `+1…+48`.

Вертикальная продукция подключена через `/wn3 kind=profile|aero|windgram`: GCS Full Ensemble Zarr, 13 уровней 0.25°, только 00/06/12/18 UTC, без подмены BigQuery-полями. Подробно: [`docs/WEATHERNEXT3.md`](docs/WEATHERNEXT3.md).

## Defaults

```text
/profile     +24 ч
/aero        Skew-T, +24 ч
/windgram    ветер, +0…+120 ч, шаг 6 ч, до 500 гПа
/cloudgram   Подробно, +0…+72 ч, шаг 3 ч
/map         Анимация +0…+48 ч, шаг 3 ч, 17 кадров, radius 100 км, places
/meteogram   GFS, 5 суток, PNG
/wn3         point +24 ч; карты: +1…+48 ч, шаг 3 ч, radius 150 км
/route       +24 ч, 300 км/ч, simple, сетка 50 км
```

Явные параметры команды всегда имеют приоритет над saved defaults.

## Saved recipes и настройки

MAX/VK используют:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Ключ состояния:

```text
platform + user_id
```

В SQLite хранятся active/recent locations, successful recipes и common schedules. `run/cycle`, callback/message ids и process-local wizard state не сохраняются.

Route endpoints записываются в историю, но **не заменяют active point**.

Telegram сохраняет совместимый native personal UX/storage, но все product results строятся теми же common services.

Документация: [`docs/MESSENGER_SETTINGS.md`](docs/MESSENGER_SETTINGS.md), [`docs/MESSENGER_SAVED_RECIPES.md`](docs/MESSENGER_SAVED_RECIPES.md).

## Расписания

Семь исходных продуктов и WN3 поддерживают автоматическую отправку; `run/cycle` выбирается заново. Telegram WN3 использует common scheduler; прежние GFS-расписания сохранены отдельно.

MAX/VK flow:

```text
/schedule
→ сохранённый успешный recipe
→ 1/2/3/7 дней или 1–30
→ местное время
→ IANA timezone точки
→ подтверждение
```

Snapshot не содержит старый `run/cycle`. Каждый запуск использует актуальный common service. Недоступный VK gateway помечает только VK schedule ошибкой и не блокирует MAX/Telegram.

Telegram сохраняет проверенный native scheduler UI; `/route` также поддержан через adapter к common route runner.

Подробно: [`docs/MESSENGER_SCHEDULES.md`](docs/MESSENGER_SCHEDULES.md).

## Shared runtime resources

Один process-wide pool ограничивает реальную нагрузку сервера:

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
MAX_CONCURRENT_WEATHERNEXT3=2
MAX_CONCURRENT_SCHEDULED=1
```

Например `MAX_CONCURRENT_GFS=2` означает суммарно два GFS-расчёта для Telegram+MAX+VK+web/API, а `MAX_CONCURRENT_WEATHERNEXT3=2` — суммарно два WN3 BigQuery/render job для всех мессенджеров.

## Установка

```bash
bash install_telegram_bot.sh
```

Базово нужны `TELEGRAM_BOT_TOKEN` и `DADATA_API_KEY`, если в `GEOCODER_PROVIDERS` включена DaData.

Неинтерактивно:

```bash
TELEGRAM_BOT_TOKEN='<BOT_TOKEN>' \
DADATA_API_KEY='<DADATA_API_KEY>' \
bash install_telegram_bot.sh --yes
```

### WeatherNext 3

После получения доступа к WeatherNext 3 Analytics Hub задайте linked dataset и ADC:

```env
WEATHERNEXT3_BIGQUERY_PROJECT=<project-with-linked-dataset>
WEATHERNEXT3_BIGQUERY_DATASET=<linked-dataset>
WEATHERNEXT3_BIGQUERY_BILLING_PROJECT=<billing-project-optional>
GOOGLE_APPLICATION_CREDENTIALS=/etc/gfs-profile/google-service-account.json
```

Зависимость `google-cloud-bigquery` устанавливается из `requirements.txt`. Секретный JSON credential в репозиторий не добавлять.

### MAX и VK

Пошагово, включая создание бота/сообщества и поля `.env`: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md).

После получения токенов:

```bash
sudo bash setup_messenger_bots.sh --max
sudo bash setup_messenger_bots.sh --vk
# или:
sudo bash setup_messenger_bots.sh --max --vk
```

Для MAX вручную нужны token + public HTTPS webhook URL. Для VK — community token + positive group id + public HTTPS Callback URL. Secrets генерируются локально; VK confirmation code получается через API.

Проверка:

```bash
curl -fsS http://127.0.0.1:8081/ready
curl -fsS http://127.0.0.1:8081/health
sudo bash setup_messenger_bots.sh --status
sudo systemctl status gfs-profile-bot.service
```

## Deploy / update

```bash
git checkout telegram-bot
git pull --ff-only
sudo bash deploy_telegram_bot.sh --yes
```

Deploy сохраняет:

```text
.env
.install-state
.venv/
.cache_gfs/
data/basemap/
```

После restart проверяется `/ready`; Telegram commands и MAX/VK webhook registrations выполняются после готовности runtime. Ошибка optional platform registration не должна превращать здоровые платформы в outage.

Автообновление: [`docs/AUTO_UPDATE.md`](docs/AUTO_UPDATE.md).

## Карта `/map`

```text
/map Москва              сохранённый/default вариант
/map Москва +24          одна карта
/map Москва from=0 to=96 step=6 mode=gif
```

Длинная animation автоматически получает совместимый step, чтобы не превышать лимит кадров. MAX отправляет MP4 native video. VK использует native video upload с document fallback, если video API недоступен.

Подробно: [`docs/MESSENGER_MAP_SERVICE.md`](docs/MESSENGER_MAP_SERVICE.md).

## Метеограмма `/meteogram`

Детерминированные источники: GFS, ECMWF IFS/AIFS, ICON Global, GEM/GDPS. Ансамбли: GEFS, ECMWF ENS/AIFS ENS, ICON-EPS, GEPS и WeatherNext 3 statistics.

```text
/meteogram Москва source=gfs days=5
/meteogram Москва source=weathernext3 days=5
/meteogram Москва source=gfs days=5 format=pdf
/meteogram Москва ensemble=gefs days=10 format=docx
```

Open-Meteo не всегда сообщает исходный model cycle; бот в этом случае честно не указывает его, а не подставляет предполагаемый запуск. В разделе `/wn3` фактический WN3 init выводится явно.

## Маршрут `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

Run выбирается по максимальному ETA lead, а не только по departure lead. `simple/pro` используют одинаковые данные и risk contract; различается presentation.

Подробно: [`docs/MESSENGER_ROUTE_SERVICE.md`](docs/MESSENGER_ROUTE_SERVICE.md).

## Документация

- [`docs/WEATHERNEXT3.md`](docs/WEATHERNEXT3.md) — BigQuery provider, продукты, env и ограничения WeatherNext 3.
- [`docs/MESSENGER_PARITY.md`](docs/MESSENGER_PARITY.md) — итоговый паритет Telegram/MAX/VK.
- [`docs/MESSENGER_RUNTIME.md`](docs/MESSENGER_RUNTIME.md) — production runtime и fault isolation.
- [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md) — регистрация MAX/VK.
- [`docs/MESSENGER_SETTINGS.md`](docs/MESSENGER_SETTINGS.md) — locations/settings.
- [`docs/MESSENGER_SAVED_RECIPES.md`](docs/MESSENGER_SAVED_RECIPES.md) — recipes.
- [`docs/MESSENGER_SCHEDULES.md`](docs/MESSENGER_SCHEDULES.md) — schedules.
- [`docs/MESSENGER_MAP_SERVICE.md`](docs/MESSENGER_MAP_SERVICE.md) — `/map`.
- [`docs/MESSENGER_METEOGRAM_SERVICE.md`](docs/MESSENGER_METEOGRAM_SERVICE.md) — `/meteogram`.
- [`docs/MESSENGER_ROUTE_SERVICE.md`](docs/MESSENGER_ROUTE_SERVICE.md) — `/route`.
- [`docs/METEOROLOGICAL_METHODS.md`](docs/METEOROLOGICAL_METHODS.md) — поля, формулы, единицы и ограничения.
- [`TELEGRAM_BOT.md`](TELEGRAM_BOT.md), [`MAX_BOT.md`](MAX_BOT.md), [`VK_BOT.md`](VK_BOT.md) — платформенные инструкции.

## Проверка перед push/deploy

```bash
python -m unittest discover -s tests
python runtime_check.py
python -m gfs_core --lat 45.0355 --lon 38.9753 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 384
```

WN3 проверяется на fake BigQuery, настоящем локальном Zarr и рендерерах. Приёмка Google-проекта: `python weathernext3_check.py --live`; эта операция может тарифицироваться.

CI дополнительно выполняет live weather smoke.

## Важно

Все продукты являются модельными. Диагностические icing/CAT/hazard layers — модельные прокси. WeatherNext 3 — экспериментальная AI-система Google и не является официальным warning source. Продукция проекта не заменяет официальные METAR/TAF/SIGMET/GAMET, NOTAM и эксплуатационное решение специалиста/командира.


## WN3 RC: расширение 8 сентября 2026

Общий сценарий Telegram/MAX/VK: owner-bound кнопки после перезапуска, пагинация +1…+360, статус/отмена, защита от двойного запуска, сценарии/повтор/расписания. BigQuery: точка, метеограмма PNG/DOCX/PDF, облачность по времени, три варианта осадков, карты/MP4/GIF/CSV, T2, p90−p10, ветер 100 м и радиация. GCS: profile/aero/windgram на 13 уровнях, средний профиль или member=0..63.

Точные команды, подключение и ограничения: [WEATHERNEXT3.md](docs/WEATHERNEXT3.md). Google live-доступ и production-доставка не проверены этим RC. Маршрут WN3 и вероятности событий не заявляются реализованными.

Для GCS-профиля, аэродиаграммы и ветровой матрицы WN3 требуется Python 3.11+. Python 3.10 поддерживает GFS и поверхностную WN3 через BigQuery; неподдерживаемые зависимости Zarr на нём не устанавливаются.


## WN3: два вида метеограммы (RC2)

В обычном выборе моделей доступен **WeatherNext 3 · средний прогноз**,
в ансамблевом — среднее и квантили. В `/wn3` это отдельные кнопки
«Метеограмма» (`kind=meteogram`) и «Ансамбль / разброс» (`kind=ensemble`).
Это два представления одного ансамбля, не отдельная детерминированная модель.
Старый `source=weathernext3` сохранён; среднее доступно как `source=weathernext3_mean`.
Общий сценарий работает в Telegram, MAX и VK, включая PNG/DOCX/PDF/CSV.
Подробности и ограничения: [WeatherNext 3](docs/WEATHERNEXT3.md).
