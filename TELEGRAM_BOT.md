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
- 🗺️ `/map`: одна PNG-карта, серия PNG или Telegram-анимация.

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
/map        карта: одна PNG, серия PNG или MP4-анимация
/admin      админ-статистика, пользователи и CSV-отчёты
```

Примеры:

```text
/profile Москва +24
/profile 59.93 30.31 +12
/profile Москва run=20260630/06 +24
/map Москва +24
/map Москва from=0 to=24 step=3 mode=gif
```

`/map ... mode=gif` оставлен как совместимый пользовательский режим, но при наличии системного `ffmpeg` бот создаёт silent H.264/MP4 и отправляет его через Telegram animation. Это отображается в чате как анимация, а не как файл. Если `ffmpeg` не найден, используется GIF fallback с худшим качеством.

## 🔐 Admin

Доступ к `/admin` есть только у numeric Telegram user id из `.env` или у пользователей, добавленных действующим администратором.

```text
TELEGRAM_ADMIN_IDS=123456789,987654321
TELEGRAM_ADMIN_DB=.cache_gfs/admin_stats.sqlite3
```

Команды администратора:

```text
/admin                         сводка за 7 дней
/admin stats 30                сводка за 30 дней
/admin recent 20               последние запросы
/admin users                   последние известные пользователи
/admin find @username          поиск пользователя
/admin add @username           добавить администратора из найденных пользователей
/admin add 123456789           добавить администратора по точному id
/admin report requests 30      скачать CSV запросов за 30 дней
/admin report users            скачать CSV пользователей
```

Учитывается:

```text
user_id, username, first/last name
первый и последний визит
тип продукта: profile/aero/skewt/windgram/cloudgram/map
город/точка или исходный запрос
lead_from/lead_to, если известны
статус: ok/failed/error/running
время выполнения, мс
текст запроса
```

Ограничение: Telegram Bot API не предоставляет глобальный поиск пользователей. `/admin find` ищет только среди пользователей, которые уже писали боту или нажимали inline-кнопки. Если известен numeric Telegram id, его можно добавить напрямую через `/admin add <id>`.

SQLite-файл статистики лежит в `.cache_gfs` по умолчанию и сохраняется при deploy вместе с GRIB-кэшем.

## 📦 Установка

Рекомендуемый способ:

```bash
bash install_telegram_bot.sh
```

Для качественных анимаций карты:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

Скрипт создаёт `.venv`, `.env`, systemd-сервис, пользователя `gfsbot`, сохраняет старый `.env` при повторной установке и запускает бота.

Проверка:

```bash
bash install_telegram_bot.sh --status
ffmpeg -version | head -n 1
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
TELEGRAM_ADMIN_IDS=123456789
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
TELEGRAM_ADMIN_DB=.cache_gfs/admin_stats.sqlite3
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

В `/windgram` подпись высоты слева `Zср` означает среднюю геопотенциальную высоту данной изобарической поверхности по всем срокам, вошедшим в диаграмму. Это не стандартная атмосфера и не высота одного срока.

## 🔎 Типовые ошибки

```text
Нужно задать TELEGRAM_BOT_TOKEN
```
Токен не загружен из `.env`.

```text
Доступ закрыт. Укажите TELEGRAM_ADMIN_IDS
```
Для `/admin` не задан numeric Telegram id администратора или пользователь не добавлен через `/admin add`.

```text
Пользователь не найден или найдено несколько совпадений
```
`/admin find` и `/admin add @username` работают только по локально известным пользователям. Попросите пользователя написать боту или добавьте точный numeric id.

```text
ffmpeg не найден
```
Для качественной `/map ... mode=gif` MP4-анимации установите `sudo apt-get install -y ffmpeg`. Без него бот использует GIF fallback.
