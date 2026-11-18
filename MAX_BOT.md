# MAX Bot — регистрация, настройка и эксплуатация

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


MAX использует тот же messenger-neutral product layer, что Telegram и VK. Семь исходных продуктов, saved recipes, `/settings` и `/schedule` сохраняются; WeatherNext 3 добавлен отдельным common разделом `/wn3` без копии BigQuery/метеорологической логики.

Полная регистрация: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md). WeatherNext 3: [`docs/WEATHERNEXT3.md`](docs/WEATHERNEXT3.md).

## 1. Что создать в MAX

Бот создаётся на платформе MAX для партнёров и проходит требуемую платформой модерацию. После готовности бота token берётся в интерфейсе управления чат-ботом:

```text
Чат-боты → бот → Расширенные настройки → Настроить → Токен
```

Перед изменением transport сверять официальные материалы:

```text
https://dev.max.ru/docs/chatbots/bots-create/create
https://dev.max.ru/docs/chatbots/bots-create/manage
https://dev.max.ru/docs-api
https://dev.max.ru/docs-api/methods/POST/subscriptions
https://dev.max.ru/docs-api/changelog-api
```

Token — секрет, в Git не коммитить.

## 2. Что вставить в проект

После базовой установки:

```bash
sudo bash setup_messenger_bots.sh --max
```

Вручную нужны только:

```env
MAX_BOT_TOKEN=<token>
MAX_WEBHOOK_URL=https://bot.example.ru/webhooks/max
```

`MAX_WEBHOOK_SECRET` мастер генерирует и сохраняет в `/opt/gfs_profile/.env`.

MAX production Webhook должен иметь публичный доверенный HTTPS endpoint на 443. Внутри сервера runtime по умолчанию слушает `127.0.0.1:8081`, поэтому нужен reverse proxy.

## 3. Transport

```text
MAX Update
→ POST /webhooks/max
→ secret validation
→ NormalizedEvent
→ common router/service
→ CommonProductResult
→ MaxGateway
```

API:

```text
https://platform-api2.max.ru
```

Token используется только через `Authorization`. Production использует Webhook subscription для `bot_started`, `message_created`, `message_callback`. Endpoint проверяет `X-Max-Bot-Api-Secret`, быстро отвечает 200 и передаёт тяжёлую работу в asyncio-task текущего процесса. Отдельный production Long Polling для того же бота не запускать.

### Обновление приложения и меню

Production получает изменения из `main` через systemd-таймер и штатный
`deploy_telegram_bot.sh`. Разработка остаётся в `telegram-bot`; автоматический
переход установленного legacy-таймера описан в [DEPLOY.md](DEPLOY.md).

После готовности всех включённых платформ деплой запускает
`register_messenger_commands.py`. Список подсказок команд MAX, включая `/icing`,
берётся из общего каталога `messenger/commands.py` и отправляется через
[`PATCH /me/commands`](https://dev.max.ru/docs-api/methods/PATCH/me/commands/).
Результат проверяется чтением `commands` через
[`GET /me`](https://dev.max.ru/docs-api/methods/GET/me).
Официальные методы сверены 18 ноября 2026 года. Токен передаётся в заголовке
`Authorization`; вызовы регистрации не отправляют сообщения пользователям.

Отключённый MAX пропускается. Сбой регистрации включённого MAX не записывается
как успешный деплой. Кнопки продукции создаются общим router при новом `/start`;
старые inline-сообщения в истории автоматически не переписываются.

## 4. Продукты

Common GFS services:

```text
/profile
/aero
/windgram
/cloudgram
/map
/icing
/meteogram
/route
```

WeatherNext 3:

```text
/wn3
```

Поддерживаются город/координаты, неоднозначный город, native location, callbacks и одно редактируемое progress message.

### `/wn3`

Раздел MAX использует тот же `messenger/weathernext3_service.py`, что Telegram/VK:

```text
🌡 point forecast
📊 WN3 meteogram
☁ total cloud
☁ low/mid/high cloud layers
🌧 native precipitation
🛰 IMERG precipitation
🧪 experimental precipitation
🌦 cloud + native precip
▶ MP4/GIF animation
```

При входе раздел показывает point forecast +24 ч. Default карт:

```text
+1…+48 ч
step 3 ч
radius 150 км
combo
```

WN3 BigQuery surface grid — 0.1°. T/Td в point/meteogram используют station head 0.05° при наличии. Ensemble statistics: mean/p10/p25/p50/p75/p90 по 64 членам. Фактический init определяется по реально опубликованной таблице и выводится как `Run ...Z`.

Вертикальные WN3-поля читаются отдельным GCS provider через `/wn3 kind=profile|aero|windgram`. Обычные GFS-команды не меняют модель.

### `/map`

Default нового пользователя:

```text
Анимация +0…+48 ч
step 3 ч
17 кадров
radius 100 км
places
```

MP4 отправляется как native `video` attachment.

### `/icing`

Пункт «Пункт на карте» принимает город, координаты или native location и добавляет
контрастную метку выбранной точки. Неоднозначные города выбираются кнопками;
метка сохраняется в параметрах и удаляется отдельной кнопкой. Точка должна быть
внутри района. Пример: `/icing product=slw_max +24 point=55.75,37.62`.

`/icing` открывает региональные карты условий обледенения без ввода города.
Район берётся из `ICING_BBOX` (по умолчанию `-15,100,34,80`); начальный срок +24 ч.
Параметры, кнопки и расчёт общие с Telegram/VK. Доступны вода на уровне,
мощность/границы/число слоёв по Годске, максимум водности и его высота, запас воды,
сравнение методов; сроки до +384 с пагинацией и изолинии 10/50 дам.
Меню показывает фактический цикл GFS и дату/час действия выбранного срока UTC;
кнопки срока содержат дату и время. Пункт «Как читать карту» даёт описание
выбранного продукта, единиц и ссылки на источники.

```text
/icing product=godske_sum +24 contours=10dam
/icing product=slw_level +12 level=850
/icing product=slwp +48
```

Callback подтверждается до расчёта, статус обновляется в одной карточке,
результаты передаются через штатный MaxGateway как PNG. Оригинал PNG — по кнопке;
CSV не создаётся и не отправляется. Высоты и мощности пересчитаны в дам.
Проверка `.idx` требуемого срока выполняется до карточки с кнопкой построения.
`/status` и
`/cancel` относятся к активной операции. Продукция явно обозначается как модель
GFS; мощность слоя не переводится в интенсивность. WAFS и неготовые продукты
в меню отсутствуют. Подробности: [ICING_MAPS_SPEC.md](docs/specs/ICING_MAPS_SPEC.md).

### `/meteogram`

Доступны GFS, ECMWF IFS/AIFS, ICON, GEM, GEFS/ECMWF ENS/AIFS ENS/ICON-EPS/GEPS и WeatherNext 3 statistics. Форматы PNG/DOCX/PDF.

### `/route`

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

PNG и CSV строятся тем же common route service. Run выбирается по максимальному ETA lead.

## 5. WeatherNext 3 config

```env
WEATHERNEXT3_BIGQUERY_PROJECT=<project-with-linked-dataset>
WEATHERNEXT3_BIGQUERY_DATASET=<linked-dataset>
WEATHERNEXT3_BIGQUERY_BILLING_PROJECT=
WEATHERNEXT3_BIGQUERY_LOCATION=
WEATHERNEXT3_BQ_MAX_BYTES_BILLED=1000000000
WEATHERNEXT3_CACHE_TTL=1800
MAX_CONCURRENT_WEATHERNEXT3=2
GOOGLE_APPLICATION_CREDENTIALS=/path/outside/repo/credentials.json
```

BigQuery SDK использует Google Application Default Credentials. Credential JSON не коммитить. Cache живёт в `.cache_gfs/weathernext3` и сохраняется deploy-скриптом вместе с остальным GFS cache.

## 6. Настройки и recipes

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Ключ: `max + user_id`.

`/settings` позволяет выбрать active point, посмотреть последние точки, запускать/закреплять/удалять recipes и очищать персональные настройки. Route endpoints сохраняются в history, но не заменяют active point. `run/cycle` не сохраняются в recipes.

WeatherNext 3 использует общую active point и записывает сценарии с параметрами, но без фактического init.

## 7. Расписания

`/schedule` поддерживает семь исходных common продуктов. Schedule snapshot не содержит `run/cycle`; каждый automatic run получает актуальные данные. WN3 также поддерживает common schedules.

Подробно: [`docs/MESSENGER_SCHEDULES.md`](docs/MESSENGER_SCHEDULES.md).

## 8. Fault isolation

```env
MAX_ENABLED=auto
```

Можно временно отключить только MAX:

```env
MAX_ENABLED=0
```

Telegram/VK/web продолжат работать. `/health` показывает состояние каждой платформы независимо.

## 9. Shared resources

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
MAX_CONCURRENT_WEATHERNEXT3=2
MAX_CONCURRENT_SCHEDULED=1
```

Это суммарные process-wide лимиты для всех платформ, а не квота MAX.

## 10. Проверка

```bash
curl -fsS http://127.0.0.1:8081/ready
curl -fsS http://127.0.0.1:8081/health
sudo bash setup_messenger_bots.sh --status
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

Ручной smoke:

```text
/start
/profile Москва +24
/aero Москва +24
/windgram Москва
/cloudgram Москва
/map Москва
/icing
/icing product=slw_level +24 level=850
/meteogram Москва source=gfs days=5
/wn3 Москва +24
/wn3 Москва kind=clouds to=24 step=3 radius=150
/wn3 Москва kind=precip_native to=24 step=3 radius=150
/route Москва -> Санкт-Петербург
/settings
/schedule
/status
/cancel
```

GFS-результаты показывают фактические run/cycle, valid UTC и короткую подпись
`GFS data`. Другие источники сохраняют своё имя и фактические init/valid UTC.
Определения диагностик доступны в отдельной справке; повторять оговорки на
каждом экране не требуется.

Карточки сроков и сохранённых сценариев GFS показывают проверенный цикл и
абсолютное время прогноза UTC. Для диапазона проверяется конечный срок.
Изменившийся цикл явно показывается перед запуском; отмена и повторное нажатие
не запускают устаревший запрос.

Названия и порядок главного меню общие с Telegram/VK. Длинные названия получают
широкие кнопки; карточки содержат до двух действий или трёх коротких числовых
вариантов в строке и возврат «Меню». Обход production-роутера и исправленные
переходы описаны в [UI_MENU_ACCEPTANCE.md](docs/UI_MENU_ACCEPTANCE.md).


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
