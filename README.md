# Профиль атмосферы GFS (веб-приложение)

Интерактивный сервис для построения вертикального профиля атмосферы по модели **GFS 0.25°** с визуальным UX-интерфейсом на русском языке.

## Что изменено в источнике данных

С 2026 года OpenDAP на NOMADS прекращён, поэтому проект **переведён на GRIB Filter** (`filter_gfs_0p25_1hr.pl`) и парсинг GRIB2 через `cfgrib/eccodes`.

## Возможности

- выбор даты запуска модели (UTC);
- выбор доступного срока (`00/06/12/18z`) и заблаговременности;
- выбор точки расчёта на карте;
- отображение городов и административных границ на подложке карты;
- извлечение профиля по ближайшей точке сетки для параметров:
  - температура;
  - относительная влажность;
  - компоненты ветра U/V;
  - геопотенциальная высота;
  - давление по уровням;
- построение графиков профиля;
- отдельная панель с отображением ветра **метеорологическим пером**;
- экспорт таблицы в CSV и копирование в буфер обмена;
- уведомления/тосты, индикатор загрузки и отображение статистики кэша сервера.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Открыть: http://127.0.0.1:8000

## Бесшовный деплой на Railway

Проект уже подготовлен для Railway:

- `main.py` добавлен как ASGI-входная точка совместимости (`main:app`);
- `railway.json` содержит build/deploy-конфигурацию и healthcheck `/healthz`;
- `Procfile` задаёт старт web-процесса через `uvicorn`;
- endpoint `GET /healthz` возвращает `{"status":"ok"}`.

### Шаги деплоя

1. Запушить репозиторий в GitHub.
2. В Railway выбрать **New Project → Deploy from GitHub Repo**.
3. Выбрать этот репозиторий (Railway автоматически подхватит `railway.json`).
4. Дождаться билда и деплоя.
5. Открыть вкладку **Settings → Networking** и сгенерировать публичный домен.

После этого приложение будет доступно по выданному URL.

## API

- `GET /api/available-cycles?date=YYYYMMDD`
- `GET /api/available-leads?date=YYYYMMDD&cycle=00|06|12|18`
- `GET /api/profile?date=YYYYMMDD&cycle=..&lead_index=..&lat=..&lon=..`
- `GET /api/cache-info` — статистика LRU-кэша данных профиля и найденных lead-часов.
- `GET /healthz` — healthcheck для платформы деплоя.

## Источник данных

- GRIB Filter: `https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25_1hr.pl`
- Файлы GFS: `https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/`
