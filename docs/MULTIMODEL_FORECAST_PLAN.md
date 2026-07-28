# План мультимодельного прогноза и автоматического выбора модели

Статус: архитектурный и научно-технический план.

Целевая ветка реализации: `telegram-bot`.

Цель: превратить бот из GFS-специализированного приложения в мультимодельный метеорологический инструмент, сохранив простой single-process Telegram long polling runtime, профессиональный компактный UX и явное указание источника каждого прогноза.

## 1. Текущее состояние и основной риск

Сейчас GFS одновременно является:

- источником данных;
- типом run/cycle;
- частью кэша;
- частью предметных моделей результатов;
- частью форматтеров и подписей;
- основой всех Telegram-продуктов.

Прямое добавление ECMWF, ICON и региональных моделей через многочисленные `if model == ...` приведёт к дублированию логики и усложнит поддержку.

Главная задача первого этапа — отделить модельно-независимое ядро от конкретных поставщиков данных.

## 2. Что означает повышение разрешения

Нельзя считать повышением разрешения:

- увеличение PNG;
- сглаживание поля;
- интерполяцию GFS 0.25° на более мелкую сетку;
- формирование почасовых кадров из трёхчасовых данных без маркировки интерполяции.

Реальное улучшение достигается за счёт:

1. модели с более мелкой нативной сеткой;
2. более частого нативного временного шага;
3. дополнительных вертикальных или модельных уровней;
4. статистической коррекции по наблюдениям и рельефу;
5. выбора модели, оптимальной для региона, срока и параметра.

Каждый результат должен содержать:

- модель и провайдера;
- run/cycle UTC;
- valid time UTC;
- нативное пространственное разрешение;
- фактический временной шаг;
- признаки пространственной и временной интерполяции;
- модельную точку или сеточный узел;
- лицензию/атрибуцию;
- дисклеймер «модельный прогноз, не наблюдение/радиозонд».

## 3. Приоритетные модели

### 3.1 GFS

Оставить как:

- глобальный основной fallback;
- источник сроков до `+384 ч`;
- модель, уже поддержанную всеми продуктами;
- стабильную базу для сравнения и верификации.

### 3.2 ECMWF IFS Open Data

Подключить для:

- `/profile`;
- `/aero` и `/skewt`;
- `/windgram`;
- среднесрочного сравнения с GFS;
- прямых GRIB2-продуктов.

Ограничение: открытая сетка IFS 0.25° сама по себе не повышает пространственное разрешение относительно GFS 0.25°. Ценность — другая модель, иная физика и потенциально лучший skill.

### 3.3 ECMWF AIFS

Добавить как отдельную экспериментальную AI-модель:

- не смешивать в UI с IFS;
- показывать отдельное имя модели;
- использовать в сравнении и накоплении статистики;
- не назначать моделью `auto` без достаточной верификации.

### 3.4 DWD ICON

Основной путь к повышению пространственного разрешения по Европе:

- ICON Global;
- ICON-EU;
- ICON-D2 для области покрытия.

Приоритет разработки:

1. ICON-EU для точечных продуктов;
2. ICON-EU для `/map` и `/cloudgram`;
3. ICON-D2 для коротких сроков внутри domain;
4. ICON Global как дополнительная глобальная модель.

### 3.5 Региональные модели

Подключать по покрытию после ICON:

- AROME/ARPEGE;
- HARMONIE;
- MET Nordic;
- UKMO regional;
- MeteoSwiss ICON-CH;
- другие национальные открытые модели.

Модель показывается пользователю только если:

- точка находится внутри domain;
- требуемый срок доступен;
- доступны нужные поля/уровни;
- источник и свежий run работоспособны.

## 4. Бесплатные источники и их роль

### Open-Meteo

Использовать как быстрый мультимодельный MVP для точечных продуктов:

- единый JSON API;
- GFS, IFS, AIFS, ICON и региональные модели;
- поверхностные и изобарические параметры;
- быстрое сравнение моделей.

Не делать единственным источником:

- лицензирование и лимиты могут зависеть от режима использования;
- часть данных может быть интерполирована;
- point API недостаточен как основной источник нативных пространственных полей для `/map`.

### ECMWF Open Data

Использовать как прямой источник IFS/AIFS GRIB2:

- собственный кэш;
- архивирование нужных run;
- контроль полей и уровней;
- собственные карты и профили.

### DWD Open Data

Использовать как прямой источник ICON:

- GRIB2;
- собственная проверка публикации run/file;
- поддержка нативной/rotated/unstructured grid;
- пространственный subset;
- нормализация накопленных осадков и облачности.

### Наблюдения

Для верификации использовать несколько классов наблюдений:

- METAR/синоптические станции — температура, Td, ветер, видимость, облачность;
- ISD и национальные архивы — историческая наземная верификация;
- IGRA/BUFR TEMP — вертикальные профили;
- национальные сети — более плотная региональная проверка.

## 5. Целевая архитектура

```text
forecast/
  domain.py
  capabilities.py
  registry.py
  selector.py
  cache.py

  providers/
    base.py
    gfs_nomads.py
    open_meteo.py
    ecmwf_open.py
    dwd_icon.py
    met_norway.py
    knmi.py

  verification/
    observations.py
    matching.py
    metrics.py
    scoring.py
```

### 5.1 Общий provider contract

```python
class ForecastProvider(Protocol):
    model_id: str

    def capabilities(self, point, product) -> ModelCapabilities: ...
    def latest_run(self, required_lead: int) -> ForecastRun: ...
    def available_leads(self, run: ForecastRun) -> list[int]: ...

    def build_profile(self, request: ForecastRequest) -> ProfileResult: ...
    def build_timeseries(self, request: ForecastRequest) -> TimeseriesResult: ...
    def build_field(self, request: ForecastRequest) -> SpatialFieldResult: ...
```

### 5.2 Нормализованный metadata-контракт

```python
@dataclass
class ForecastMetadata:
    model_id: str
    model_name: str
    provider_id: str
    run_time_utc: datetime
    valid_time_utc: datetime
    lead_hour: int

    requested_point: GeoPoint
    model_grid_point: GeoPoint | None
    native_resolution_km: float | None

    temporal_step_hours: float
    interpolated_in_time: bool
    interpolated_in_space: bool

    source_name: str
    licence: str
    model_cycle_version: str | None
```

### 5.3 Кэш

Ключ кэша обязан включать:

```text
provider
model
model version/cycle
run date/cycle
lead
requested/grid point
variables
levels
product
interpolation mode
```

GFS, IFS и ICON не должны использовать совпадающие cache key.

Повреждённые или HTML-файлы должны инвалидироваться автоматически.

## 6. Telegram UX выбора модели

Модель выбирается после точки и типа продукта, потому что доступность зависит от региона и требуемых полей.

Базовое меню:

```text
Модель прогноза

[🤖 Авто]
[GFS] [ECMWF]
[ICON]
[Сравнить]
```

Региональные модели показываются только внутри их domain.

Командный синтаксис:

```text
/profile Москва +24 model=auto
/profile Москва +24 model=ifs
/map Берлин from=0 to=24 step=1 model=icon_d2
/windgram Париж to=72 model=arome
```

`model=auto` — значение по умолчанию.

Результат обязан показывать фактически выбранную модель:

```text
🤖 Авто → ICON-EU
Причина: покрытие, +24 ч, свежий run, лучший накопленный score.
```

Для режима сравнения:

- GFS/IFS/ICON на одном графике;
- разность температур и ветра;
- разброс высот изотерм;
- межмодельная неопределённость;
- отдельное указание run каждой модели.

## 7. Система верификации

Текущая admin-статистика запросов не измеряет качество прогнозов. Нужна отдельная подсистема.

Нельзя опираться только на пользовательские запросы: это создаёт географическое и событийное смещение. Нужны два потока:

1. сохранять прогнозы, реально выданные пользователю;
2. регулярно сохранять shadow-прогнозы всех моделей по постоянной сети контрольных станций.

Архитектуру сохранять простой:

- SQLite;
- CLI-команды сбора/оценки;
- `systemd timer`;
- без Redis, Celery и внешней очереди.

### 7.1 Таблицы

```text
forecast_samples
  model/provider/version
  run_time/valid_time/lead
  requested/grid point
  model elevation
  variable/level/value/unit

observations
  source/station_id
  valid_time/point/elevation
  variable/level/value
  quality_flag

verification_matches
  forecast_id/observation_id
  time_delta/distance/elevation_delta

verification_scores
  model/region/lead_bin/season
  variable/level/metric
  sample_count/score/updated_at

selection_log
  request_id
  candidate_models
  selected_model
  score_components
  selection_reason
  fallback_reason
```

Прогноз сохраняется в момент выпуска. Нельзя позже запрашивать `latest` и считать его тем же прогнозом.

## 8. Метрики

### Температура и точка росы

- MAE;
- bias;
- RMSE;
- ошибки дневного минимума/максимума.

### Ветер

- ошибки `u` и `v`;
- vector RMSE;
- MAE скорости;
- ошибка направления только при достаточно сильном ветре.

### Осадки

- Brier Score;
- CSI/ETS для нескольких порогов;
- frequency bias;
- FSS для пространственных полей.

### Облачность, ВНГО и видимость

- MAE облачности;
- категории ВНГО;
- Brier Score;
- CSI для порогов видимости;
- confusion matrix явлений.

### Вертикальный профиль

- RMSE T/Td по слоям;
- vector RMSE ветра;
- ошибка высот 0/-10/-20 °C;
- ошибка максимального ветра и его уровня;
- отдельные оценки пограничного слоя и средней тропосферы.

## 9. Автоматический selector

### 9.1 Фильтрация кандидатов

Модель исключается, если:

- точка вне domain;
- срок недоступен;
- нет нужных переменных или уровней;
- run ещё не опубликован;
- источник находится в cooldown после ошибок.

### 9.2 Корзины статистики

Skill хранить по:

- региону;
- заблаговременности;
- сезону;
- параметру;
- уровню;
- продукту;
- высотному/прибрежному классу при достаточном объёме данных.

Lead bins:

```text
0-12 ч
13-36 ч
37-72 ч
73-120 ч
121-240 ч
241+ ч
```

### 9.3 Score

Начальная формула:

```text
total_score =
    0.55 * empirical_skill
  + 0.15 * operational_reliability
  + 0.15 * run_freshness
  + 0.10 * coverage_quality
  + 0.05 * resolution_prior
```

Разрешение не должно доминировать: модель с сеткой 1 км не автоматически лучше модели 9-25 км.

### 9.4 Защита от малого объёма данных

```text
adjusted_skill =
    n / (n + k) * local_skill
  + k / (n + k) * regional_or_global_skill
```

До накопления достаточной выборки использовать:

- общий score модели;
- статические экспертные приоритеты;
- GFS/IFS fallback;
- прозрачное объяснение причины выбора.

### 9.5 Shadow mode

До включения автоматического выбора:

- реальный ответ строится по текущей политике;
- selector только записывает, какую модель выбрал бы;
- все кандидаты проверяются на контрольных станциях;
- расхождения доступны администратору.

Команды администратора:

```text
/admin models
/admin skill
/admin selector
/admin verification
```

Автовыбор включать только после стабильного периода и достаточного числа независимых случаев.

## 10. Смешанный прогноз

После накопления статистики можно добавить взвешенный ансамбль для отдельных скалярных параметров:

```text
forecast = sum(weight_model * forecast_model)
```

Ограничения:

- веса зависят от региона, срока и параметра;
- одна модель не должна получать неограниченный вес;
- нельзя без проверки смешивать T/RH/ветер из разных моделей в единый вертикальный профиль — это может нарушить физическую согласованность;
- для аэрологического профиля на первом этапе выбирается одна основная модель, а остальные показываются как сравнение/неопределённость.

## 11. Этапы реализации

### Этап 1. Provider abstraction без изменения поведения

- ввести `ForecastProvider`;
- перенести GFS в `GfsNomadsProvider`;
- заменить `GfsRun` в Telegram-слое на общий `ForecastRun`;
- сделать форматтеры и графики модельно-независимыми;
- расширить metadata и cache key;
- добавить `model=auto|gfs`;
- добавить provider contract tests;
- сохранить все текущие GFS-сценарии и вывод.

### Этап 2. Мультимодельный point MVP

- добавить `OpenMeteoProvider`;
- реализовать IFS/AIFS/ICON для `/profile`, `/windgram`, `/cloudgram`;
- добавить выбор модели в wizard;
- добавить `/compare` или кнопку `Сравнить`;
- маркировать интерполяцию и источник;
- не переводить `/map` на point API.

### Этап 3. Прямой ECMWF Open Data

- официальный клиент/GRIB2;
- IFS и AIFS как отдельные модели;
- pressure-level profile;
- собственное архивирование run;
- direct provider health/status;
- карты для доступных полей.

### Этап 4. Прямой DWD ICON

- ICON Global/ICON-EU/ICON-D2;
- domain polygons;
- rotated/unstructured grid;
- spatial subset;
- нативные карты;
- cloudgram/windgram/profile;
- контроль накоплений и единиц.

### Этап 5. Верификация

- SQLite schema;
- ingestion METAR/ISD/IGRA/национальных сетей;
- benchmark stations;
- matching с контролем времени, расстояния и высоты;
- метрики;
- admin-отчёты;
- shadow selector.

### Этап 6. Автоматический выбор

Последовательность:

1. coverage + lead + freshness + статический приоритет;
2. empirical skill + operational reliability;
3. динамический selector;
4. параметрические ансамбли после достаточной валидации.

## 12. Definition of Done первого этапа

Первый этап считается готовым, если:

- GFS работает через общий provider contract;
- существующие команды и Telegram wizard не ухудшились;
- в результате явно указан `model_id` и provider;
- кэш разделён по моделям;
- `model=auto` и `model=gfs` дают предсказуемое поведение;
- нет ветвления по модели внутри форматтеров и Telegram flow;
- добавлены unit/contract tests;
- пройдены `python -m unittest discover -s tests` и обязательные GFS smoke;
- обновлены `README.md`, `TELEGRAM_BOT.md` и `.env.telegram.example`, если добавлены настройки;
- deploy не теряет `.env`, `.install-state`, `.venv`, `.cache_gfs` и verification DB.

## 13. Рекомендуемый порядок PR/коммитов

1. `refactor: ввести provider abstraction для прогнозных моделей`
2. `feat: добавить выбор model=auto|gfs`
3. `feat: подключить Open-Meteo для точечных моделей`
4. `feat: добавить сравнение GFS IFS ICON`
5. `feat: подключить ECMWF Open Data`
6. `feat: подключить ICON-EU для пространственных продуктов`
7. `feat: добавить накопление forecast samples и наблюдений`
8. `feat: добавить shadow selector моделей`
9. `feat: включить skill-based auto model selection`

Каждый PR должен быть атомарным, содержать тесты, документацию, smoke и описание deploy.

## 14. Внешние источники для повторной проверки перед реализацией

Условия доступа, лицензии, наборы полей и разрешения могут меняться. Перед каждым provider PR повторно проверить официальную документацию:

- ECMWF Open Data: https://www.ecmwf.int/en/forecasts/datasets/open-data
- DWD Open Data: https://opendata.dwd.de/weather/nwp/
- Open-Meteo Forecast API: https://open-meteo.com/en/docs
- Open-Meteo pricing/licensing: https://open-meteo.com/en/pricing
- MET Norway APIs: https://api.met.no/
- KNMI Data Platform: https://developer.dataplatform.knmi.nl/
- NOAA ISD: https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database
- NOAA IGRA: https://www.ncei.noaa.gov/products/weather-balloon/integrated-global-radiosonde-archive
- Aviation Weather API: https://aviationweather.gov/data/api/
