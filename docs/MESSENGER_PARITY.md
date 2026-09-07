# Функциональный паритет Telegram / MAX / VK

Статус документа: production architecture после переноса основных продуктов в messenger-neutral services и добавления интерактивного WeatherNext 3.

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

Метеорологические расчёты, выбор GFS run, WeatherNext 3 BigQuery queries/run selection, geocoder contracts, saved recipes и schedule snapshots не копируются по платформам.

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
| `/wn3` point forecast | ✅ | ✅ | ✅ |
| `/wn3` ensemble meteogram | ✅ | ✅ | ✅ |
| `/wn3` cloud/layer maps | ✅ | ✅ | ✅ |
| `/wn3` native/IMERG/experimental precipitation maps | ✅ | ✅ | ✅ |
| `/wn3` single/series/animation | ✅ | ✅ | ✅ |
| saved recipes / repeat / pin для 7 исходных продуктов | ✅ | ✅ | ✅ |
| WN3 saved recipes / schedules | ⏳ | ⏳ | ⏳ |
| `/settings` | ✅ | ✅ | ✅ |
| active/recent point | ✅ | ✅ | ✅ |
| `/schedule` для 7 исходных продуктов | ✅ | ✅ | ✅ |
| route schedules | ✅ | ✅ | ✅ |
| platform fault isolation | ✅ | ✅ | ✅ |
| shared server resource limits | ✅ | ✅ | ✅ |
| отдельный WN3 BigQuery limit | ✅ | ✅ | ✅ |
| production install/deploy | ✅ | ✅ | ✅ |

`⏳` означает одинаково задокументированное ограничение всех платформ, а не Telegram-only/MAX-only реализацию.

## Общие продукты

### GFS GRIB/NOMADS

`profile`, `aero`, `windgram`, `cloudgram`, `map`, `route` используют GFS/NOMADS common services. Cycle выбирается по публикации максимального фактически требуемого lead.

### Метеограмма

`meteogram` использует общий model/ensemble service. Для Open-Meteo upstream cycle не выдумывается. WeatherNext 3 также доступен как ensemble source и передаёт фактический `init_time`.

### WeatherNext 3

`/wn3` использует один `weathernext3_provider.py` и `messenger/weathernext3_service.py` для всех платформ. BigQuery surface statistics дают point forecast, ансамблевую метеограмму, total/low/mid/high cloud maps и три precipitation heads. Карты используют тот же локальный Natural Earth basemap и MP4/GIF media contract, но не GFS-специфичные meteorological layers.

BigQuery не содержит WN3 pressure-level fields; поэтому `/profile`/`/aero` не маркируются как WN3 и остаются GFS до отдельного GCS/Zarr provider.

## Shared capacity

```env
MAX_CONCURRENT_GFS=2
MAX_CONCURRENT_GEOCODE=2
MAX_CONCURRENT_METEOGRAM=2
MAX_CONCURRENT_WEATHERNEXT3=2
MAX_CONCURRENT_SCHEDULED=1
```

Лимиты суммарные на один server process. BigQuery/render WN3 не занимает GFS gate.

## Definition of Done платформенной функции

Функция считается паритетной, если использует один common service/use-case, одинаковые defaults/параметры/result metadata, честно показывает model/source/run, имеет native controls/media и cross-platform contract tests. Ошибка одной платформы не должна влиять на соседние.
