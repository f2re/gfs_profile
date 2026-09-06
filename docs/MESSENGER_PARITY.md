# Функциональный паритет Telegram / MAX / VK

Статус документа: production architecture после переноса всех основных продуктов в messenger-neutral services.

## Принцип

```text
Telegram / MAX / VK
        ↓
Normalized command/action
        ↓
Common product/service layer
        ↓
CommonProductResult
        ↓
platform-native renderer/gateway
```

Метеорологические расчёты, выбор GFS run, geocoder contracts, saved recipes и schedule snapshots не должны копироваться по платформам.

## Матрица

| Возможность | Telegram | MAX | VK |
|---|:---:|:---:|:---:|
| `/start` | ✅ | ✅ | ✅ |
| город / координаты | ✅ | ✅ | ✅ |
| неоднозначный город | ✅ | ✅ | ✅ |
| native location | ✅ | ✅ | ✅ |
| `/profile` | ✅ | ✅ | ✅ |
| `/aero` | ✅ | ✅ | ✅ |
| `/windgram` | ✅ | ✅ | ✅ |
| `/cloudgram` | ✅ | ✅ | ✅ |
| `/map` single/series/animation | ✅ | ✅ | ✅ |
| `/meteogram` model/ensemble | ✅ | ✅ | ✅ |
| meteogram PNG/DOCX/PDF | ✅ | ✅ | ✅ |
| `/route` PNG/CSV | ✅ | ✅ | ✅ |
| saved recipes / repeat / pin | ✅ | ✅ | ✅ |
| `/settings` | ✅ | ✅ | ✅ |
| active/recent point | ✅ | ✅ | ✅ |
| `/schedule` | ✅ | ✅ | ✅ |
| route schedules | ✅ | ✅ | ✅ |
| platform fault isolation | ✅ | ✅ | ✅ |
| shared server resource limits | ✅ | ✅ | ✅ |
| production install/deploy | ✅ | ✅ | ✅ |

Telegram сохраняет native handlers и совместимые storage/UI там, где это необходимо, но product result строится тем же common service. MAX/VK работают через общий webhook/router runtime.

## Независимость платформ

У каждой платформы есть переключатель:

```env
TELEGRAM_ENABLED=auto
MAX_ENABLED=auto
VK_ENABLED=auto
```

Значения:

```text
auto  включить только при наличии token/полной локальной конфигурации
1     явно запросить платформу
0     выключить/карантинировать только эту платформу
```

Runtime health показывает `ready / degraded / off` отдельно. Отказ Telegram polling не останавливает MAX/VK/web. Ошибка или отсутствие VK Callback API не блокирует Telegram/MAX. Ошибка MAX subscription не блокирует VK/Telegram.

`/ready` относится к shared runtime infrastructure, а не требует, чтобы все три провайдера были healthy.

## Общие продукты

### GFS GRIB/NOMADS

`profile`, `aero`, `windgram`, `cloudgram`, `map`, `route` используют GFS/NOMADS common services. Cycle выбирается по публикации максимального фактически требуемого lead.

### Метеограмма

`meteogram` использует общий model/ensemble service и одинаковые PNG/DOCX/PDF. Поставщик Open-Meteo не всегда сообщает исходный model cycle; в этом случае cycle не выдумывается.

## Карта

Первый default:

```text
Анимация +0…+48 ч
step 3 ч
17 кадров
radius 100 км
basemap places
```

Telegram/MAX отправляют MP4 native video/animation. VK использует native video upload; если конкретный API/token не поддерживает video upload, применяется document fallback без потери результата.

## Состояние пользователя

MAX/VK:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Ключ:

```text
platform + user_id
```

В этом SQLite живут locations, recipes и common schedules. Telegram сохраняет совместимый personal storage, но использует те же common product services.

Route endpoints не заменяют active point.

## Расписания

Все семь продуктов доступны для автоматической отправки. Snapshot не содержит `run/cycle`; каждый запуск берёт свежие данные. Scheduler fault-isolated по gateway платформы.

## Shared capacity

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
MAX_CONCURRENT_SCHEDULED=1
```

Лимиты суммарные на один server process, а не отдельные для каждого мессенджера.

## Production setup

Systemd запускает:

```text
messenger_launcher.py
```

При normal production mode он поднимает Telegram polling + FastAPI MAX/VK webhook + web/API в одном worker.

Подключение MAX/VK: [`MESSENGER_REGISTRATION.md`](MESSENGER_REGISTRATION.md).

Deploy сначала проверяет runtime/env, затем restart, `/ready`, и только после этого выполняет platform registration best-effort. Неработающая optional platform не должна превращать успешный deploy здоровых платформ в outage.

## Definition of Done платформенной функции

Функция считается паритетной, если:

1. использует один common service/use-case;
2. имеет одинаковые defaults и параметры;
3. возвращает одинаковую meteorological summary/files;
4. одинаково маркирует model/source/run;
5. поддерживает recipe/repeat без stale run;
6. имеет native controls/media platform renderer;
7. ошибки одной платформы не влияют на соседние;
8. есть cross-platform contract tests.
