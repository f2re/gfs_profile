# Геокодирование через DaData

## Что используется

Основной провайдер геокодирования Telegram-бота — DaData Suggestions API:

```text
POST https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address
```

Каскад по умолчанию:

```text
координаты из строки
→ кэш результата DaData
→ DaData
→ локальный словарь популярных пунктов
→ Nominatim как резерв
```

Старый кэш Nominatim не обходит DaData: кэш используется до запроса только тогда, когда его `source` совпадает с основным провайдером.

## Как получить API-ключ

1. Откройте `https://dadata.ru/`.
2. Зарегистрируйтесь.
3. Подтвердите адрес электронной почты.
4. Откройте личный кабинет.
5. Скопируйте **API-ключ**.
6. Передайте его установщику или запишите в `.env`:

```text
DADATA_API_KEY=<API-ключ>
```

Для API подсказок нужен только API-ключ. `Secret Key` не используется и не должен запрашиваться установщиком.

## Установка

Интерактивно:

```bash
bash install_telegram_bot.sh
```

Скрипт запросит:

```text
TELEGRAM_BOT_TOKEN
DADATA_API_KEY
```

Неинтерактивно:

```bash
TELEGRAM_BOT_TOKEN='...' \
DADATA_API_KEY='...' \
GEOCODER_PROVIDERS='dadata,local,nominatim' \
bash install_telegram_bot.sh --yes
```

## Обновление существующей установки

```bash
git pull
bash deploy_telegram_bot.sh
```

Если в `/opt/gfs_profile/.env` ещё нет `DADATA_API_KEY`, deploy запросит его и сохранит перед проверкой и перезапуском сервиса.

Для `--yes` ключ необходимо передать через окружение:

```bash
DADATA_API_KEY='...' bash deploy_telegram_bot.sh --yes
```

Deploy до перезапуска выполняет контрольный запрос `Москва` и проверяет, что DaData вернула координаты.

## Переменные окружения

```text
GEOCODER_PROVIDERS=dadata,local,nominatim
DADATA_API_KEY=
DADATA_SUGGEST_URL=https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address
DADATA_TIMEOUT=12
GEOCODE_CACHE_DIR=.cache_gfs/geocode
GEOCODE_CACHE_TTL_SECONDS=2592000
GEOCODE_TIMEOUT=12
NOMINATIM_URL=https://nominatim.openstreetmap.org/search
GEOCODER_USER_AGENT=gfs-profile-telegram-bot/0.1
```

Только DaData, без внешнего fallback:

```text
GEOCODER_PROVIDERS=dadata,local
```

Временно оставить только старый провайдер:

```text
GEOCODER_PROVIDERS=local,nominatim
```

Это явная конфигурация резерва, а не автоматическая обратная совместимость.

## Проверка

```bash
cd /opt/gfs_profile
set -a
source .env
set +a
.venv/bin/python geocoder_preflight.py
```

Ожидаемый результат:

```text
Geocoder providers: dadata,local,nominatim
DaData OK: Москва -> 55...., 37....
```

## Типовые ошибки

### `DADATA_API_KEY is required`

Ключ отсутствует в `.env`. Запустите интерактивный deploy или задайте переменную окружения.

### HTTP 401

API-ключ не передан.

### HTTP 403

Возможные причины:

- неверный API-ключ;
- не подтверждена почта;
- закончился дневной лимит;
- Suggestions не активированы для ключа.

### HTTP 429

Превышена частота запросов. Бот попробует следующий провайдер из `GEOCODER_PROVIDERS`, если он настроен.

## Лимит

Бесплатный тариф DaData Suggestions предоставляет до 10 000 запросов в сутки суммарно по всем видам подсказок. После исчерпания дневного лимита основной провайдер перестанет отвечать до следующего дня; при стандартной конфигурации бот перейдёт к локальному словарю и Nominatim.
