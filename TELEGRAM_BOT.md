# 🤖 Telegram-бот GFS

Бот строит вертикальный профиль атмосферы GFS 0.25° по городу, координатам или Telegram-геолокации.

Схема работы: точка → кнопочный выбор срока → опубликованный цикл GFS → малый GRIB2-срез → xarray/cfgrib/eccodes → сводка, PNG и CSV.

## ✅ Что возвращает

- 🌡️ Температура и точка росы по изобарическим уровням.
- 💧 Относительная влажность.
- 🌬️ Ветер: направление, скорость, U/V-компоненты.
- ❄️ Уровень 0 °C или статус, если он вне профиля.
- 📈 PNG: T/Td, влажность, скорость ветра, ветровые перья.
- 📄 CSV со всеми уровнями.

## ⚠️ Важно

Это модельный профиль ближайшего узла GFS, не радиозонд. В горах, у моря, в городе и в приземном слое интерпретировать аккуратно.

## 🚀 Лучший сценарий для пользователя

```text
/start
📍 Отправить геолокацию
выбрать срок: частые сроки или полный список до +384 ч
видеть этапы проверки, загрузки GRIB2 и построения
получить сводку, PNG и CSV
```

Без команды `/profile` тоже работает:

```text
Москва
55.75 37.62
Санкт-Петербург +48
```

Если город неоднозначный, бот показывает варианты inline-кнопками. Пользователь выбирает точку, затем выбирает срок прогноза.

## ⏱️ Сроки прогноза

На первой странице показаны частые сроки:

```text
+0 анализ, +3, +6, +12, +24, +48 ч
```

Через кнопку `Все сроки до +384 ч →` открывается пагинация полного GFS-диапазона: `0..120` каждый час, затем `123..384` каждые 3 часа. Есть отдельная кнопка `Макс. +384 ч`.

## 🔄 Что видно во время расчёта

Бот обновляет одно статусное сообщение:

```text
1/5 Проверяю публикацию forecast-файла fXXX.idx
2/5 Привязываю точку к узлу GFS
3/5 Скачиваю GRIB2 из NOMADS, по возможности с процентом и размером
4/5 Читаю GRIB2 через xarray/cfgrib/eccodes
5/5 Формирую сводку, PNG и CSV
```

Если GRIB2 уже есть в кэше, бот прямо пишет, что файл взят из кэша.

## 🧭 Команды

```text
/start      краткий старт и кнопка геолокации
/help       короткая инструкция
/cancel     сброс текущего выбора
/cycle      последний опубликованный анализ GFS f000
/status     доступность GFS для +0, +24, +48, +120, +240, +384 и состояние кэша
/profile    экспертный запрос с точкой, сроком и run
```

Примеры:

```text
/profile Москва +24
/profile 59.93 30.31 +12
/profile Москва run=20260630/06 +24
```

## 📦 Установка

Рекомендуемый способ:

```bash
bash install_telegram_bot.sh
```

Скрипт создаёт `.venv`, `.env`, systemd-сервис, пользователя `gfsbot`, сохраняет старый `.env` при повторной установке и запускает бота.

Проверка:

```bash
bash install_telegram_bot.sh --status
sudo journalctl -u gfs-profile-bot.service -f
```

## 🔄 Обновление после git pull

Если вы просто сделали `git pull`, код в `/opt/gfs_profile` сам не изменится. Для обновления установленного бота выполните:

```bash
git pull
bash deploy_telegram_bot.sh
```

Для автоматического deploy после pull один раз установите локальные git hooks:

```bash
bash install_git_hooks.sh
```

После этого hooks будут вызывать `deploy_telegram_bot.sh --yes`, синхронизировать код в `/opt/gfs_profile`, обновлять зависимости и перезапускать `gfs-profile-bot.service`.

Лог автообновления:

```text
.git/gfs-profile-deploy.log
```

Подробности: `DEPLOY.md`.

## 🛠️ Ручной запуск для разработки

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.telegram.example .env
set -a && source .env && set +a
python telegram_bot.py
```

## ⚙️ Минимальный `.env`

```text
TELEGRAM_BOT_TOKEN=123456:AA...
```

Рекомендуемые параметры:

```text
DEFAULT_LEAD=24
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
GFS_CACHE_DIR=.cache_gfs
GFS_CACHE_TTL_SECONDS=86400
GFS_AVAILABILITY_CACHE_TTL_SECONDS=300
GFS_REQUEST_TIMEOUT=35
GFS_PRESSURE_LEVELS_HPA=profile
GEOCODER_USER_AGENT=gfs-profile-telegram-bot/0.1
GEOCODE_CACHE_DIR=.cache_gfs/geocode
GEOCODE_TIMEOUT=12
```

## 🧪 Проверка без Telegram

```bash
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24
python -m gfs_core --lat 55.75 --lon 37.62 --lead 384
python -m gfs_core --lat 55.75 --lon 37.62 --lead 24 --csv /tmp/profile.csv
python -m unittest discover -s tests
```

## 📊 Как читать ответ

```text
Запуск         цикл модели GFS, UTC
Срок           заблаговременность прогноза, часы
Действительно  срок, на который рассчитан профиль, UTC
Узел GFS       ближайшая модельная точка 0.25°
Уровень 0 °C   интерполированная нулевая изотерма
Макс. ветер    максимум скорости ветра в профиле
```

## 🔎 Типовые ошибки

```text
Нужно задать TELEGRAM_BOT_TOKEN
```
Токен не загружен из `.env`.

```text
Файл GFS для YYYYMMDD HHZ +N ч ещё не опубликован
```
Нужный forecast lead ещё не опубликован. Без фиксированного `run=...` бот сам откатывается на предыдущий опубликованный цикл.

```text
Ошибка чтения GRIB2 через xarray/cfgrib
```
Переустановите зависимости через deploy: `bash deploy_telegram_bot.sh` или вручную `pip install -r requirements.txt` внутри venv.

```text
NOMADS вернул HTML вместо GRIB2
```
NOMADS не отдал GRIB2. Обычно это временная недоступность, неверный путь или слишком ранний цикл.

```text
Город или место не найдено
```
Используйте координаты или Telegram-геолокацию.
