# Deploy GFS Profile: Telegram + MAX + VK

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


Разработка ведётся в `telegram-bot`, production получает проверенные изменения
из `main`. Установленное приложение работает из `/opt/gfs_profile` одним
systemd-процессом:

```text
systemd
  ↓
messenger_launcher.py
  ├─ Telegram long polling
  ├─ MAX  POST /webhooks/max
  ├─ VK   POST /webhooks/vk
  └─ web/API
```

`MESSENGER_RUNTIME_ENABLED=1` — штатный production mode. `0` оставлен как аварийный Telegram-only fallback без смены systemd unit.

Пошаговое создание ботов и получение token/group id: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md).

## Базовая установка

```bash
bash install_telegram_bot.sh
```

Telegram и web/API могут работать без MAX/VK token.

## Первичная настройка MAX/VK

После базовой установки и настройки публичного HTTPS reverse proxy используйте мастер:

```bash
sudo bash setup_messenger_bots.sh --max
sudo bash setup_messenger_bots.sh --vk
sudo bash setup_messenger_bots.sh --max --vk
```

Мастер требует минимальный набор:

```text
MAX: MAX_BOT_TOKEN + MAX_WEBHOOK_URL
VK:  VK_BOT_TOKEN + VK_GROUP_ID + VK_CALLBACK_URL
```

Он автоматически:

```text
генерирует MAX_WEBHOOK_SECRET
генерирует VK_CALLBACK_SECRET
получает VK_CONFIRMATION_CODE через VK API
проверяет env
запускает штатный deploy
ждёт /ready
регистрирует MAX subscription и VK Callback API server
проверяет фактическую регистрацию
```

Status без изменений:

```bash
sudo bash setup_messenger_bots.sh --status
```

## Ручное обновление

```bash
cd ~/gfs_profile
git fetch origin refs/heads/main:refs/remotes/origin/main
git checkout main
git pull --ff-only origin main
sudo bash deploy_telegram_bot.sh --yes
```

Deploy автоматически мигрирует старый unit `telegram_bot.py` на `messenger_launcher.py`.

## Раздел карт обледенения

Выпуск в `main` включает `/icing` для Telegram, MAX и VK в
общем runtime. Новые токены и дополнительные процессы не нужны. Подложка
используется локально, Cartopy не требуется. Для изменения района добавьте в
сохраняемый `.env`:

```env
ICING_BBOX=-15,100,34,80
```

Порядок границ: west,east,south,north. По умолчанию охвачены Европа — Сибирь,
34–80° с.ш. Поддерживаются непересекающие линию смены дат районы северного
полушария до 85° с.ш.; некорректный район отклоняется до загрузки данных.
Область не связана с активной точкой пользовательского профиля.

GRIB размещается в `GFS_CACHE_DIR`, PNG/CSV — в его подкаталоге `icing`.
Стандартный deploy сохраняет `.env`, `.venv`, `.install-state` и `.cache_gfs`.
Секреты MAX/VK и WeatherNext этой функцией не меняются. Подготовьте локальную
подложку штатным `prepare_basemap_cache.py`; рендер карт не загружает тайлы.

После обновления проверьте `/start` → «Обледенение», затем `/icing`, выбор
продукта и срока, PNG/CSV, `/status` и `/cancel` в каждой включённой платформе.
Прямой контрольный запрос: `/icing product=godske_sum +24 contours=100`.
На изображении должны быть фактические run/valid UTC, единицы и маркировка
«модель GFS». Научная методика и границы выпуска:
[ICING_MAPS_SPEC.md](docs/specs/ICING_MAPS_SPEC.md).

## Production deploy sequence

```text
1. deploy lock
2. сохранить .env/.cache_gfs/.venv/.install-state
3. checkout → /opt/gfs_profile
4. Python dependencies
5. unit tests
6. runtime_check.py
7. messenger_config_check.py
8. DaData preflight
9. systemd unit
10. restart
11. /ready: корректный ответ и готовность всех включённых платформ
12. команды Telegram/MAX и проверка сохранённого списка
13. MAX/VK webhook registration
14. .install-state
```

Webhook регистрируется только после `/ready`.

## Runtime env

```env
MESSENGER_RUNTIME_ENABLED=1
MESSENGER_RUNTIME_HOST=127.0.0.1
MESSENGER_RUNTIME_PORT=8081
MESSENGER_RUNTIME_LOG_LEVEL=info
MESSENGER_RUNTIME_ACCESS_LOG=0

MAX_BOT_TOKEN=
MAX_WEBHOOK_URL=
MAX_WEBHOOK_SECRET=

VK_BOT_TOKEN=
VK_GROUP_ID=
VK_CALLBACK_URL=
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
VK_API_VERSION=5.199
```

Примеры публичных URL:

```text
https://bot.example.ru/webhooks/max
https://bot.example.ru/webhooks/vk
```

Не записывайте example URL в `.env`, если соответствующая платформа не настраивается.

## Ручная подготовка secrets/code

Если `.env` заполнен вручную минимальными platform-реквизитами:

```bash
cd /opt/gfs_profile
sudo .venv/bin/python prepare_messenger_config.py --env-file .env
```

После этого:

```bash
sudo bash deploy_telegram_bot.sh --yes
```

## Health/readiness

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8081/ready
```

`/health` показывает платформы/common products/resource limits без secrets. `/ready` возвращает 200 только после полного запуска Telegram application/runtime.

## Общие лимиты

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
```

Лимиты process-wide: Telegram/MAX/VK/web делят одни permits. Redis/Celery не используются.

## Регистрация и status

Штатный deploy после `/ready` вызывает:

```bash
python register_messenger_webhooks.py
```

Проверка без изменения provider state:

```bash
cd /opt/gfs_profile
set -a
source .env
set +a
.venv/bin/python register_messenger_webhooks.py --status
```

Отдельно:

```bash
.venv/bin/python register_messenger_webhooks.py --max --status
.venv/bin/python register_messenger_webhooks.py --vk --status
```

Status проверяет не только локальный runtime:

- MAX — наличие subscription для точного URL и update types;
- VK — confirmation code, точный Callback server URL, `message_new` и `message_event`.

## Public HTTPS

Внутренний uvicorn обычно слушает:

```text
127.0.0.1:8081
```

Nginx/HAProxy публикует только `/webhooks/max` и `/webhooks/vk` через HTTPS с доверенным сертификатом. Пример есть в `docs/MESSENGER_REGISTRATION.md`.

## Автообновление

```bash
sudo bash install_auto_update.sh --yes
sudo bash install_auto_update.sh --status
```

Новая установка таймера следит за `origin/main`. Разработка в `telegram-bot`
сама по себе не является production-релизом: выпуск происходит после слияния
проверенных изменений в `main`. Таймер опрашивает GitHub раз в 60 секунд
(плюс задержка планировщика и время предыдущего запуска), а затем вызывает
штатный `deploy_telegram_bot.sh`. GitHub Actions проверяет код, но не подключается
к вашему серверу для его установки.

Уже установленный таймер с `AUTO_UPDATE_BRANCH=telegram-bot` получает миграцию
через очередное обновление старой ветки. Новый скрипт проверяет, что актуальный
`main` содержит опубликованный и установленный legacy-коммиты, и переходит на
`main`. Успешный переход сохраняется в состоянии обновлятора. Поэтому старое
значение в systemd unit не требует ручной правки: эффективная ветка показывается
в `--status`. Если нужный коммит ещё не слит, переход откладывается. При ошибке
деплоя переход не закрепляется. Явно закреплённые тестовые ветки не меняются.

Установщик с явным `--branch NAME` записывает `AUTO_UPDATE_BRANCH_PINNED=1`.
Для стандартного production используйте установку без `--branch`: по умолчанию
выбирается `main`. Настройки обновлятора находятся в systemd unit и его файле
состояния; помещение этих параметров в `.env` работающего бота не перенастраивает
таймер.

Обновлятор использует общий deploy lock и отдельный собственный lock, сверяет
не только checkout, но и установленный `source_rev`, повторяет временные ошибки
и откатывает неудачный выпуск на ранее установленный коммит. Деплой считается
полным только после перезапуска, готовности runtime и регистрации меню/webhook
включённых платформ. Ошибка регистрации меню не записывается как успешный
`source_rev`; ручной запуск с пропущенными этапами остаётся неполным.

`messenger_readiness.py` проверяет содержимое `/ready`, а не только HTTP-код:
ответ `200` с `status=degraded` не подтверждает полный выпуск. Рабочие платформы
в самом runtime продолжают изолированно обслуживаться; строгость применяется
к решению о завершении деплоя. Единственный systemd-процесс перезапускается,
поэтому возможна короткая пауза обслуживания. Нулевой простой не заявляется.

Первый выпуск миграции сливается обычным merge с сохранением истории:
`main` должен содержать SHA legacy-ветки, что и проверяет обновлятор.

Если таймер раньше не устанавливался или был отключён, публикация в GitHub не
может включить его удалённо. Один раз выполните установку таймера из checkout
`main` командой выше. Проверка работающего сервера:

```bash
sudo systemctl is-enabled gfs-profile-bot-auto-update.timer
sudo systemctl is-active gfs-profile-bot-auto-update.timer
sudo bash install_auto_update.sh --status
sudo journalctl -u gfs-profile-bot-auto-update.service -n 80 --no-pager
```

Критерии успешного выпуска: эффективная ветка `main`, результат `deployed` или
`up-to-date`, установленный SHA соответствует `origin/main`,
`deploy_status=complete`, runtime отвечает на `/ready`, меню включённых платформ
прошло проверку чтением через API. Подробная приёмка:
[`docs/MAIN_AUTOUPDATE_ACCEPTANCE.md`](docs/MAIN_AUTOUPDATE_ACCEPTANCE.md).

## Обновление меню

После готовности runtime штатный деплой запускает
`register_messenger_commands.py`. Telegram получает актуальные команды, включая
`/icing`, в общих и приватных областях и используемых языковых вариантах.
MAX получает список через `PATCH /me/commands`, затем список проверяется через
`GET /me`. В обоих случаях проверяется результат чтением через API; включённая
платформа с ошибкой регистрации не считается успешно обновлённой. Отключённые
платформы пропускаются.

Кнопки продукции Telegram/MAX/VK формируются общим кодом при новом `/start`.
Ранее отправленные сообщения с inline-кнопками массово не переписываются.
Индивидуальные Telegram command scopes, созданные вне проекта для конкретного
чата, не перечисляются глобальным Bot API и могут требовать отдельного удаления.

## Locks

Deploy:

```text
/run/lock/gfs-profile-bot.deploy.lock
```

Auto-update:

```text
/run/lock/gfs-profile-bot-auto-update.lock
```

## Сохраняемые данные

`rsync --delete` исключает:

```text
.env
.install-state
.venv/
.cache_gfs/
data/basemap/
```

Сохраняются admin stats, Telegram preferences/recipes, MAX/VK recipes, schedules, GFS/geocode/meteogram cache и platform secrets.

## Проверка после deploy

```bash
sudo bash deploy_telegram_bot.sh --status
sudo bash setup_messenger_bots.sh --status
sudo systemctl status gfs-profile-bot.service
sudo systemctl cat gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
curl -fsS http://127.0.0.1:8081/ready
```

Effective unit:

```text
ExecStart=/opt/gfs_profile/.venv/bin/python /opt/gfs_profile/messenger_launcher.py
```

До push/merge:

```bash
python -m unittest discover -s tests
python runtime_check.py
python -m gfs_core --lat 45.0355 --lon 38.9753 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 384
```

## Deploy options

```text
--yes
--install-system-packages
--skip-pip
--skip-tests
--skip-commands
--skip-webhooks
--no-restart
--status
```

`--skip-webhooks` — только для диагностики/reverse-proxy preparation.

## Emergency switch

```bash
sudo bash install_messenger_runtime.sh --status
sudo bash install_messenger_runtime.sh --disable
sudo bash install_messenger_runtime.sh --enable
```

Обычная установка/deploy не требуют отдельного запуска этого helper.


## Обновление WeatherNext 3 RC

Рабочая ветка — `telegram-bot`, `/opt/gfs_profile` — отдельная установленная копия. Релиз в GitHub не означает deploy сервера.

```bash
git checkout telegram-bot
git pull --ff-only
bash deploy_telegram_bot.sh
```

Для полного набора WN3 используйте Python 3.11+; requirements устанавливают GCS/Zarr только на поддерживаемом Python. На Python 3.10 сохраняются GFS и поверхностная WN3 через BigQuery, но верхняя атмосфера требует обновления интерпретатора. Deploy обновляет общий runtime Telegram/MAX/VK; сохраняет `.env`, `.install-state`, `.venv`, `.cache_gfs`. Credential JSON размещайте вне checkout, например `/etc/gfs_profile/google-adc.json`; задайте `GOOGLE_APPLICATION_CREDENTIALS` в установленном `.env`, обеспечьте чтение только пользователю службы. Google project/dataset/billing не угадываются и не создаются автоматически.

Подробная настройка: [WeatherNext 3](docs/WEATHERNEXT3.md). Для WN3-расписаний Telegram оставьте `MESSENGER_RUNTIME_ENABLED=1`. `python weathernext3_check.py` без `--live` не обращается к Google; `--live` и `--live --upper` — отдельные, потенциально платные проверки от имени пользователя службы. `/health` разделяет наличие конфигурации и фактическую проверку облака.

RC не подтверждает ADC/allowlist, production webhook, delivery или состояние автообновления конкретного сервера. При отказе Google GFS продолжает работать; WN3 не заменяется данными другой модели.
