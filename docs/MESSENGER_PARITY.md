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
| WN3 saved recipes / schedules | ✅ | ✅ | ✅ |
| WN3 profile/aero/windgram (GCS) | ✅ | ✅ | ✅ |
| WN3 CSV, T2 spread, wind100, solar | ✅ | ✅ | ✅ |
| `/settings` | ✅ | ✅ | ✅ |
| active/recent point | ✅ | ✅ | ✅ |
| `/schedule` для 7 исходных продуктов | ✅ | ✅ | ✅ |
| route schedules | ✅ | ✅ | ✅ |
| platform fault isolation | ✅ | ✅ | ✅ |
| shared server resource limits | ✅ | ✅ | ✅ |
| отдельный WN3 BigQuery limit | ✅ | ✅ | ✅ |
| production install/deploy | ✅ | ✅ | ✅ |

Отметки описывают реализацию и контрактные проверки. Google live-доступ и production-доставка подтверждаются отдельно. В Telegram прежние GFS-расписания и WN3-расписания используют разные совместимые хранилища.

## Общие продукты

### GFS GRIB/NOMADS

`profile`, `aero`, `windgram`, `cloudgram`, `map`, `route` используют GFS/NOMADS common services. Cycle выбирается по публикации максимального фактически требуемого lead.

### Метеограмма

`meteogram` использует общий model/ensemble service. Для Open-Meteo upstream cycle не выдумывается. WeatherNext 3 также доступен как ensemble source и передаёт фактический `init_time`.

### WeatherNext 3

`/wn3` использует один `weathernext3_provider.py` и `messenger/weathernext3_service.py` для всех платформ. BigQuery surface statistics дают point forecast, ансамблевую метеограмму, total/low/mid/high cloud maps и три precipitation heads. Карты используют тот же локальный Natural Earth basemap и MP4/GIF media contract, но не GFS-специфичные meteorological layers.

BigQuery не содержит верхнюю атмосферу; `/wn3 kind=profile|aero|windgram` получает её из GCS/Zarr. Обычные `/profile`/`/aero` остаются GFS.

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


## WN3 RC: расширение 8 сентября 2026

Общий сценарий Telegram/MAX/VK: owner-bound кнопки после перезапуска, пагинация +1…+360, статус/отмена, защита от двойного запуска, сценарии/повтор/расписания. BigQuery: точка, метеограмма PNG/DOCX/PDF, облачность по времени, три варианта осадков, карты/MP4/GIF/CSV, T2, p90−p10, ветер 100 м и радиация. GCS: profile/aero/windgram на 13 уровнях, средний профиль или member=0..63.

Точные команды, подключение и ограничения: [WEATHERNEXT3.md](WEATHERNEXT3.md). Google live-доступ и production-доставка не проверены этим RC. Маршрут WN3 и вероятности событий не заявляются реализованными.
