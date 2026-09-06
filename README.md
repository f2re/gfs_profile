# 🌦️ GFS Profile 0.25

Профессиональный multi-messenger бот и web/API для модельной продукции GFS 0.25°. Один метеорологический слой обслуживает **Telegram, MAX и VK**: платформы различаются транспортом и native UI, но не расчётами, параметрами или результатами.

> GFS всегда обозначается как модель. Это не наблюдение, не радар и не радиозонд.

## Продукция

| Продукт | Telegram | MAX | VK |
|---|:---:|:---:|:---:|
| 📈 `/profile` — вертикальный профиль | ✅ | ✅ | ✅ |
| 🧾 `/aero` — Skew-T + годограф | ✅ | ✅ | ✅ |
| 🟦 `/windgram` — срок × уровень | ✅ | ✅ | ✅ |
| ☁️ `/cloudgram` — облака/осадки/риски | ✅ | ✅ | ✅ |
| 🗺️ `/map` — PNG/серия/MP4 | ✅ | ✅ | ✅ |
| 📊 `/meteogram` — модели/ансамбли, PNG/DOCX/PDF | ✅ | ✅ | ✅ |
| ✈️ `/route` — маршрутный разрез PNG/CSV | ✅ | ✅ | ✅ |
| ⚙️ `/settings` | ✅ | ✅ | ✅ |
| 🕒 `/schedule` | ✅ | ✅ | ✅ |

Подробная итоговая матрица: [`docs/MESSENGER_PARITY.md`](docs/MESSENGER_PARITY.md).

## Архитектура

```text
Telegram polling ─┐
MAX webhook ──────┼→ normalized action
VK Callback API ──┘
                       ↓
               common router/use-case
                       ↓
       profile/aero/windgram/cloudgram/map/
              meteogram/route services
                       ↓
              CommonProductResult
                       ↓
           native platform gateway
```

Расчёты GFS, geocoder, выбор run, formatter, saved recipes и schedule snapshots не копируются между мессенджерами.

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

## Defaults

```text
/profile     +24 ч
/aero        Skew-T, +24 ч
/windgram    ветер, +0…+120 ч, шаг 6 ч, до 500 гПа
/cloudgram   Подробно, +0…+72 ч, шаг 3 ч
/map         Анимация +0…+48 ч, шаг 3 ч, 17 кадров, radius 100 км, places
/meteogram   GFS, 5 суток, PNG
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

Все семь продуктов доступны для автоматической отправки.

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
MAX_CONCURRENT_SCHEDULED=1
```

Например `MAX_CONCURRENT_GFS=2` означает суммарно два GFS-расчёта для Telegram+MAX+VK+web/API, а не два на каждую платформу.

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

Детерминированные источники: GFS, ECMWF IFS/AIFS, ICON Global, GEM/GDPS. Ансамбли: GEFS, ECMWF ENS/AIFS ENS, ICON-EPS, GEPS.

```text
/meteogram Москва source=gfs days=5
/meteogram Москва source=gfs days=5 format=pdf
/meteogram Москва ensemble=gefs days=10 format=docx
```

Open-Meteo не всегда сообщает исходный model cycle; бот в этом случае честно не указывает его, а не подставляет предполагаемый запуск.

## Маршрут `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

Run выбирается по максимальному ETA lead, а не только по departure lead. `simple/pro` используют одинаковые данные и risk contract; различается presentation.

Подробно: [`docs/MESSENGER_ROUTE_SERVICE.md`](docs/MESSENGER_ROUTE_SERVICE.md).

## Документация

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

CI дополнительно выполняет live weather smoke.

## Важно

Все продукты являются модельными. Диагностические icing/CAT/hazard layers — модельные прокси. Продукция проекта не заменяет официальные METAR/TAF/SIGMET/GAMET, NOTAM и эксплуатационное решение специалиста/командира.
