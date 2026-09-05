# VK Bot — регистрация, настройка и эксплуатация

Статус на 2026-09-05: VK Community Bot + Callback API и общие `/profile`, `/aero`, `/windgram`, `/cloudgram` работают через тот же messenger-neutral service, что Telegram и MAX. VK-слой содержит только transport/UI/media logic.

Полная пошаговая регистрация с Nginx, `.env`, автоматическим confirmation code и диагностикой: [`docs/MESSENGER_REGISTRATION.md`](docs/MESSENGER_REGISTRATION.md).

## Что нужно создать в VK

1. Создайте сообщество VK или используйте существующее, где есть права администратора.
2. Включите сообщения сообщества.
3. Откройте `Управление → Работа с API`.
4. Создайте community access token с правами для сообщений/media и управления Callback API, если этот доступ вынесен отдельным разрешением.
5. Скопируйте token.
6. Запишите числовой ID сообщества положительным числом без `-`.

Проект использует Community Bot + Callback API, а не пользовательский access token.

Используемые методы VK API:

```text
messages.send
messages.edit
messages.sendMessageEventAnswer
photos.getMessagesUploadServer
photos.saveMessagesPhoto
docs.getMessagesUploadServer
docs.save
groups.getCallbackConfirmationCode
groups.getCallbackServers
groups.addCallbackServer
groups.getCallbackSettings
groups.setCallbackSettings
```

Token — секрет. В репозиторий его не коммитить.

## Что вставить в GFS Profile

После базовой установки:

```bash
bash install_telegram_bot.sh
```

запустите:

```bash
sudo bash setup_messenger_bots.sh --vk
```

Мастер запросит только:

```env
VK_BOT_TOKEN=<community access token>
VK_GROUP_ID=123456789
VK_CALLBACK_URL=https://bot.example.ru/webhooks/vk
```

Эти поля можно не заполнять вручную:

```env
VK_CALLBACK_SECRET=
VK_CONFIRMATION_CODE=
```

Мастер:

- генерирует `VK_CALLBACK_SECRET`;
- получает фактический confirmation code через `groups.getCallbackConfirmationCode`;
- записывает оба значения в `/opt/gfs_profile/.env`;
- после restart автоматически создаёт/обновляет Callback API server и включает нужные события.

Неинтерактивно:

```bash
sudo env \
  VK_BOT_TOKEN='<VK_COMMUNITY_TOKEN>' \
  VK_GROUP_ID='123456789' \
  VK_CALLBACK_URL='https://bot.example.ru/webhooks/vk' \
  bash setup_messenger_bots.sh --vk --yes
```

## Production transport

```text
VK Callback event
→ POST /webhooks/vk
→ VK adapter
→ NormalizedEvent
→ common product service
→ CommonProductResult
→ VK gateway
```

Runtime принимает:

```text
confirmation
message_new
message_event
```

`confirmation` возвращает сохранённый confirmation code. Для остальных запросов проверяются `group_id` и `VK_CALLBACK_SECRET`. GFS-расчёт запускается после быстрого ответа HTTP endpoint, а не блокирует Callback API request lifecycle.

Публичный URL:

```text
https://bot.example.ru/webhooks/vk
        ↓
127.0.0.1:8081/webhooks/vk
```

## Автоматическая регистрация Callback API

После `/ready` проект вызывает `register_messenger_webhooks.py`.

Для VK он:

1. сверяет `VK_CONFIRMATION_CODE` с `groups.getCallbackConfirmationCode`;
2. отправляет контрольный `confirmation` POST на публичный URL;
3. получает список Callback API servers;
4. создаёт server, если URL ещё не зарегистрирован;
5. включает `message_new=1` и `message_event=1`.

Поэтому вручную копировать confirmation code и добавлять callback server через интерфейс VK не требуется.

Проверка фактической регистрации:

```bash
cd /opt/gfs_profile
set -a; source .env; set +a
.venv/bin/python register_messenger_webhooks.py --vk --status
```

или:

```bash
sudo bash setup_messenger_bots.sh --status
```

Ожидается `OK VK: callback server активен`.

## Кнопки и callbacks

Общий `UiKeyboard` переводится в native VK keyboard. Callback использует `messages.sendMessageEventAnswer`. Для `messages.send` формируется уникальный `random_id`, чтобы retry не создавал дубли.

Location button нормализуется в общий `NormalizedEvent.location`.

## Общие продукты

### `/profile`

Город/координаты, `Москва +24`, неоднозначность, native location, быстрые сроки, пагинация до `+384`, progress, PNG/CSV и recipes.

### `/aero`

Общий Skew-T log-P + годограф, actual run/cycle, valid UTC, requested/grid point. Icing/CAT — модельные прокси.

### `/windgram`

Default:

```text
ветер
+0…+120 ч
шаг 6 ч
до 500 гПа
```

Доступны wind/temp/RH, `+120/+240/+384`, шаг `3/6/12`.

### `/cloudgram`

Default:

```text
Подробно
+0…+72 ч
шаг 3 ч
```

Доступны `Подробно/Кратко`, `+24/+48/+72/+120` и шаг `3/6`.

Примеры:

```text
/cloudgram Москва to=72 step=3 mode=pro
/cloudgram 55.75 37.62 to=120 step=6 mode=simple
```

Общий service выбирает фактически опубликованный GFS cycle по максимальному требуемому lead. Результат одинаков с Telegram/MAX: run/cycle UTC, valid range, requested point, grid point, max hazard, missing fields и PNG. Гроза/опасность — модельная диагностика, не наблюдавшееся явление.

## Saved recipes

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Для четырёх общих продуктов сохраняются точка и параметры. `run/cycle` исключены. Repeat использует новый подходящий GFS run. Cloudgram recipe:

```text
from / to / step / mode / point
```

Recipes изолированы по `platform + user_id`.

## Общие ресурсы

VK использует тот же process-wide pool, что Telegram/MAX/web:

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
```

## Media

PNG отправляется как VK photo attachment. Файлы используют document upload. Common service ничего не знает о VK attachment ids.

## Проверка после настройки

```bash
curl -fsS http://127.0.0.1:8081/ready
curl -fsS http://127.0.0.1:8081/health
sudo bash setup_messenger_bots.sh --status
sudo systemctl status gfs-profile-bot.service
sudo journalctl -u gfs-profile-bot.service -n 100 --no-pager
```

В VK проверить:

```text
/start
/profile Москва +24
/aero Москва +24
/windgram Москва to=120 step=6 param=wind
/cloudgram Москва to=72 step=3 mode=pro
geo/location
callback
pin/repeat
/status
/cancel
```

## Следующий этап паритета

```text
/map
→ /meteogram
→ /route
→ /settings
→ /schedule
```

Новые продукты должны использовать общий service/result/progress, `RuntimeResources` и `UserRecipeStore`; VK-копии GFS/geocoder/product logic не допускаются.
