# WeatherNext 3 в GFS Bot

WeatherNext 3 подключён как отдельный модельный источник рядом с GFS. Метеорологическая логика не живёт в Telegram/MAX/VK: все платформы вызывают один `weathernext3_provider.py` + `messenger/weathernext3_service.py`.

> WeatherNext 3 — экспериментальная AI-модель Google. Это не наблюдение, не радар, не спутниковый снимок и не официальный warning source.

## Источник и ограничения

Оперативная surface-продукция читается из WeatherNext 3 BigQuery Analytics Hub linked dataset:

```text
weathernext_3_0_0_0p1deg  0.1° surface statistics
weathernext_3_0_0_0p05deg 0.05° station-head T/Td statistics
```

В каждой таблице `init_time` — partition key, `forecast` — repeated record с `time`, `hours` и статистиками `_mean/_p10/_p25/_p50/_p75/_p90`. `forecast.hours` начинается с +1 ч. Основные 00/06/12/18 UTC запуски дают до +360 ч; промежуточные почасовые — до +48 ч.

BigQuery содержит только surface statistics. Полный 64-member ensemble и 3D pressure-level fields 0.25° доступны через GCS/Zarr и намеренно не подменяются surface-полями.

Официальные материалы:

- https://developers.google.com/weathernext/guides/models
- https://developers.google.com/weathernext/guides/bigquery
- https://developers.google.com/weathernext/guides/dissemination

## Реализованная продукция

`/wn3`:

```text
point                краткий прогноз на +N
meteogram            1–15 суток, mean + p10/p25/p50/p75/p90
clouds               общая облачность
cloud_layers          low / medium / high + total cloud
precip_native         total_precipitation_1hr
precip_imerg          imerg_tp_1hr
precip_experimental   experimental_tp_1hr
combo                 total cloud + native precipitation
```

Карты поддерживают `animation`, `single`, `series`. Default карты: +1…+48 ч, step 3 ч, radius 150 км. Анимация ограничена 32 кадрами и использует MP4/H.264; если `ffmpeg` недоступен — GIF fallback.

Примеры:

```text
/wn3 Москва +24
/wn3 Москва kind=meteogram days=5
/wn3 Москва kind=clouds from=1 to=48 step=3 radius=150
/wn3 Москва kind=cloud_layers from=1 to=48 step=3 radius=150
/wn3 Москва kind=precip_imerg from=1 to=72 step=3 radius=250
/wn3 Москва kind=combo from=1 to=48 step=3 radius=150
```

## Метеорологические преобразования

- `station_head_temperature_2m` / `station_head_dewpoint_temperature_2m`: K → °C, 0.05°. При отсутствии station-head значения берутся из 0.1° grid T/Td.
- `mean_sea_level_pressure`: Pa → hPa.
- cloud fractions: 0…1 → 0…100 %.
- все 1-hour precipitation heads: m → mm.
- wind direction вычисляется по `u/v` как метеорологическое направление **откуда** дует.
- RH рассчитывается из T/Td по Magnus approximation.
- day/night для метеограммы определяется астрономически по UTC/координатам, а не по порогу SSRD.

Карты используют ensemble mean. Метеограмма и точечный прогноз показывают uncertainty statistics там, где они доступны.

## Выбор фактического init

Provider не предполагает цикл по часам публикации. Перед запросом он ищет максимальный фактически опубликованный `init_time`, который содержит требуемый `forecast.hours` для выбранной точки:

```sql
WHERE init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 72 HOUR)
  AND ST_INTERSECTS(geography_polygon, ST_GEOGPOINT(@lon, @lat))
GROUP BY init_time
HAVING MAX(f.hours) >= @required_hour
ORDER BY init_time DESC
LIMIT 1
```

Поэтому запрос +120 автоматически отбрасывает свежий interim-run с горизонтом +48 и выбирает последний опубликованный synoptic run.

## BigQuery cost/security

Все рабочие queries:

1. содержат partition filter по `init_time`;
2. выбирают только необходимые columns, без `SELECT *`;
3. ограничивают region `ST_INTERSECTS`/`ST_DWITHIN`;
4. используют query parameters для координат, сроков и run;
5. допускают `maximum_bytes_billed`;
6. кэшируются в `.cache_gfs/weathernext3/`.

Project/dataset identifiers валидируются до SQL interpolation.

## Настройка

После allowlist + Analytics Hub subscription:

```env
WEATHERNEXT3_BIGQUERY_PROJECT=my-project
WEATHERNEXT3_BIGQUERY_DATASET=weathernext3_linked
WEATHERNEXT3_BIGQUERY_BILLING_PROJECT=my-billing-project
WEATHERNEXT3_BIGQUERY_LOCATION=
WEATHERNEXT3_BQ_MAX_BYTES_BILLED=0
WEATHERNEXT3_BIGQUERY_TIMEOUT=180
WEATHERNEXT3_CACHE_TTL=1800
MAX_CONCURRENT_WEATHERNEXT3=2
WEATHERNEXT3_MAP_PIXEL_SIZE=1280
WEATHERNEXT3_MAP_FRAME_DURATION_MS=700
WEATHERNEXT3_MAP_FPS=8
WEATHERNEXT3_PRECIP_VMAX_MM=30
```

Авторизация — Google Application Default Credentials. На сервере предпочтителен service account с минимальными BigQuery read/job permissions; секрет JSON не коммитится.

Проверка ADC:

```bash
gcloud auth application-default login --no-launch-browser
```

или задайте `GOOGLE_APPLICATION_CREDENTIALS` на защищённый локальный service-account JSON.

## Messenger parity

Один common service используется Telegram, MAX и VK. Нативные адаптеры отвечают только за point picker, callbacks, progress и media upload.

В первой версии WN3 использует active point, но не создаёт отдельные saved recipes/schedules. Это одинаковое ограничение всех трёх платформ и не влияет на существующие семь GFS/common scheduled products.

## Следующее расширение

Для полного паритета вертикальной продукции нужен отдельный GCS/Zarr provider для 00/06/12/18 UTC:

```text
geopotential_{level}
temperature_{level}
specific_humidity_{level}
u_component_of_wind_{level}
v_component_of_wind_{level}
vertical_velocity_{level}
```

После него WN3 можно честно подключить к profile/aero/windgram без копирования существующей GFS-визуализации.
