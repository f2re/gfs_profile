# Cloudgram: облачность, осадки, ВНГО и Cb по GFS

## Цель

Добавить новый Telegram-продукт по типу windgram, но для оперативной оценки облачности и осадков в точке:

- облачность по ярусам и/или вертикальному профилю;
- осадки: факт/интенсивность/накопление за шаг;
- ВНГО / cloud ceiling;
- признак конвективной/кучево-дождевой облачности;
- меньшая дискретность по времени, чем у windgram.

Рабочее название: `cloudgram`.

## Источники GFS-полей

Текущий `gfs_core.py` запрашивает только профильные переменные:

```text
TMP, RH, UGRD, VGRD, HGT
```

и только изобарические уровни. Этого недостаточно для облачно-осадочного продукта.

В GFS 0.25 pgrb2 доступны следующие группы полей:

### Изобарические уровни

Используются для вертикальной структуры облаков:

```text
TCDC   Total Cloud Cover [%]
CLWMR  Cloud Mixing Ratio [kg/kg]
ICMR   Ice Water Mixing Ratio [kg/kg]
RWMR   Rain Mixing Ratio [kg/kg]
SNMR   Snow Mixing Ratio [kg/kg]
GRLE   Graupel [kg/kg]
RH     Relative Humidity [%]
VVEL   Vertical Velocity (Pressure) [Pa/s]
HGT    Geopotential Height [gpm]
TMP    Temperature [K]
UGRD/VGRD wind, optional
```

Практически полезные уровни первой версии:

```text
1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200
```

Для Telegram можно ограничиться до 300 гПа или до 200 гПа. Для авиационного/оперативного облачного продукта достаточно 1000–300 гПа.

### Слойные и интегральные облачные поля

Используются для верхней диагностической полосы:

```text
LCDC  Low Cloud Cover [%]
MCDC  Medium Cloud Cover [%]
HCDC  High Cloud Cover [%]
TCDC  Total Cloud Cover [%] entire atmosphere
HGT   cloud ceiling, geopotential height [gpm]
PRES  convective cloud bottom level [Pa]
PRES  convective cloud top level [Pa]
TCDC  convective cloud layer [%]
CWAT  Cloud Water [kg/m^2]
PWAT  Precipitable Water [kg/m^2]
```

### Осадки и тип осадков

Используются для нижней диагностической полосы:

```text
PRATE  Precipitation Rate [kg/m^2/s]
CPRAT  Convective Precipitation Rate [kg/m^2/s]
APCP   Total Precipitation [kg/m^2], accumulated
ACPCP  Convective Precipitation [kg/m^2], accumulated
CRAIN  Categorical Rain [-]
CSNOW  Categorical Snow [-]
CFRZR  Categorical Freezing Rain [-]
CICEP  Categorical Ice Pellets [-]
CPOFP  Percent frozen precipitation [%]
```

Единицы:

- `kg/m^2` для накопленных осадков практически равно `мм` воды;
- `kg/m^2/s` для `PRATE/CPRAT` нужно переводить в `мм/ч` умножением на `3600`.

### Конвекция / Cb proxy

Прямого идеального поля `CB` в pgrb2.0p25 лучше не предполагать. Надёжнее строить proxy:

```text
Cb risk = convective cloud layer TCDC + ACPCP/CPRAT + CAPE + CIN + cloud top/bottom + graupel/ice/rain mixing ratio + VVEL
```

Рекомендуемые признаки:

- `convective cloud layer TCDC > 20–30%`;
- `ACPCP > 0` или `CPRAT > 0`;
- `CAPE > 100–250 J/kg`, высокий риск при `> 500–1000 J/kg`;
- `CIN` не слишком большой по модулю;
- `convective cloud top pressure` достаточно низкое, например `< 400–500 гПа`;
- наличие `GRLE/ICMR/RWMR` на уровнях;
- сильный `VVEL` вверх на средних уровнях.

В интерфейсе это надо называть не «Cb факт», а «Cb proxy / риск Cb по GFS», чтобы не выдавать модельный индикатор за наблюдение.

## Рекомендуемый продукт первой версии

### Команда

```text
/cloudgram Москва
/cloudgram Москва to=72 step=3 mode=cloud
/cloudgram Москва to=72 step=3 mode=precip
/cloudgram Москва to=120 step=6 mode=summary
/clouds Москва
```

`/clouds` можно сделать alias для `/cloudgram`.

### Дискретность

Не копировать windgram один-в-один. Для облачности и осадков лучше меньше горизонтальных ячеек:

```text
from=0
to=72
step=3
```

Причины:

- облачность/осадки более шумные по времени;
- для накопленных осадков GFS часто естественно читать 0–3h / 3h blocks;
- `to=120 step=6` можно оставить как расширенный режим;
- `to=384` для cloudgram не делать default: слишком широкая PNG и сомнительная детализация для point forecast.

### Вертикальная ось

Вариант A — профессиональный cloudgram:

```text
200/250/300/350/400/450/500/550/600/650/700/750/800/850/900/925/950/975/1000 гПа
```

Сверху вниз: высокие уровни → низкие. В подписи уровней добавлять медианную высоту `Z, км`.

Вариант B — упрощённый summary mode:

```text
High cloud
Mid cloud
Low cloud
TCDC total
Ceiling
Cb proxy
Precip type
Precip intensity
```

Для Telegram удобнее начать с B как `/cloudgram mode=summary`, а затем добавить A как `/cloudgram mode=profile`.

## Визуальный дизайн

### Summary cloudgram

Матрица `строки × сроки`:

```text
High cloud       HCDC %
Mid cloud        MCDC %
Low cloud        LCDC %
Total cloud      TCDC %
Ceiling          HGT cloud ceiling, m
Cb proxy         0–3 category
Precip           APCP/PRATE intensity
Precip type      rain/snow/frz/ip mix
```

Цвета:

- облачность: белый/серый/сине-серый, 0–100%;
- ВНГО: зелёный высокий ceiling, жёлтый умеренный, оранжевый/красный низкий;
- осадки: белый → светло-синий → синий → фиолетовый для интенсивности;
- конвекция/Cb: серый/жёлтый/оранжевый/красный;
- тип осадков: маленькая метка в ячейке: `R`, `S`, `FZ`, `IP`, смешанные `R/S`.

### Profile cloudgram

Матрица `pressure × time`:

- заливка по `TCDC` на изобарических уровнях;
- контур/штриховка для `CLWMR+ICMR+RWMR+SNMR+GRLE`;
- нижняя отдельная полоса: `APCP/PRATE`;
- линия/маркер ВНГО по времени;
- линия convective cloud top/bottom по времени;
- marker `Cb` поверх столбца при превышении порога.

Для первой реализации лучше `summary`, потому что она читабельнее и требует меньше тяжёлой интерпретации вертикальных полей.

## Архитектурные изменения

### 1. Нельзя расширять `gfs_core.py` напрямую под cloudgram

Текущий `gfs_core.py` заточен под изобарический профиль и жёстко формирует URL с:

```text
var_TMP, var_RH, var_UGRD, var_VGRD, var_HGT
```

Для cloudgram нужен общий downloader:

```python
build_gfs_subset_url(
    date,
    cycle,
    lead_hour,
    lat,
    lon,
    variables: tuple[str, ...],
    levels: tuple[str, ...] | None,
    product: str = "pgrb2",
)
```

Он должен поддерживать:

- `var_APCP=on`, `var_PRATE=on`, `var_CRAIN=on`, etc.;
- не только `lev_500_mb`, но и `lev_surface`, `lev_low_cloud_layer`, `lev_cloud_ceiling`, `lev_entire_atmosphere`, `lev_convective_cloud_layer`, etc.;
- отдельный cache key по `variables + levels + product_kind`.

### 2. Новый модуль загрузки

```text
gfs_subset.py
```

Функции:

```python
download_gfs_subset_to_disk(..., variables, level_tokens, product_key)
open_subset_dataset(path) -> xarray.Dataset
```

`gfs_product_core.py` можно оставить для pressure-profile products.

### 3. Новый продукт

```text
cloudgram_product.py
cloudgram_plot.py
telegram_cloudgram.py
```

Dataclasses:

```python
CloudgramCell:
    lead_hour
    valid_time_utc
    high_cloud_pct
    mid_cloud_pct
    low_cloud_pct
    total_cloud_pct
    ceiling_m
    precip_mm
    precip_rate_mmh
    conv_precip_mm
    precip_type
    cape_jkg
    cin_jkg
    cb_score
    cb_category
```

```python
CloudgramData:
    run
    point
    leads
    cells
    mode: summary|profile
```

### 4. Telegram UX

Добавить команду:

```text
/cloudgram - ☁️ Облачность и осадки
```

В wizard:

```text
/cloudgram
Шаг 1: точка
Шаг 2: режим
  ☁️ Облачность
  🌧 Осадки
  ⛈ Cb-риск
  🧩 Сводка
Шаг 3: диапазон
  +24 / +48 / +72 / +120
  step 3 / 6
```

Команда для копирования:

```text
/cloudgram 45.0000 39.0000 to=72 step=3 mode=summary
/cloudgram 45.0000 39.0000 to=72 step=3 mode=precip
/cloudgram 45.0000 39.0000 to=72 step=3 mode=cb
```

### 5. Telegram command registration

Обновить `telegram_commands.py`:

```text
cloudgram - ☁️ Облачность и осадки
clouds - ☁️ Быстрый cloudgram
```

Лучше не плодить aliases в меню. В меню зарегистрировать только `/cloudgram`, а `/clouds` оставить скрытым alias.

## Best practices

1. Не называть Cb как факт наблюдения. Только `Cb proxy` или `риск Cb по GFS`.
2. Не смешивать разные GFS cycles в одном продукте. Выбирать run по максимальному lead, как windgram.
3. Не делать default `to=384`. Оптимальный default: `to=72 step=3`.
4. Использовать накопление `APCP/ACPCP` как основной показатель осадков за шаг, а `PRATE/CPRAT` — как интенсивность, если поле доступно и корректно читается.
5. Для `PRATE` переводить `kg/m^2/s` в `мм/ч` умножением на `3600`.
6. Для `APCP` считать `kg/m^2` как `мм` воды.
7. ВНГО брать из `cloud ceiling HGT`, но отображать как модельный ceiling. В горах и при низкой облачности проверять адекватность относительно рельефа/орографии.
8. Для типа осадков использовать categorical fields: `CRAIN/CSNOW/CFRZR/CICEP`, плюс `CPOFP` как дополнительный индикатор замёрзшей фазы.
9. На summary-графике не перегружать каждую ячейку числами. Числа нужны только для `precip_mm`, `ceiling_m`, `cb_score`; облачность можно показать цветом и округлением по желанию.
10. Для profile mode сначала использовать `TCDC` по pressure levels; микрофизику `CLWMR/ICMR/RWMR/SNMR/GRLE` подключить вторым этапом.
11. Делать graceful degradation: если cloud ceiling или convective top/bottom не пришли, строить продукт без этих слоёв и писать в caption, чего не хватило.
12. Перед внедрением проверить реальные NOMADS filter token names через сгенерированный URL и `.idx` для f003/f006/f024.

## Реализация по этапам

### Этап 1. Универсальный GFS subset downloader

- `gfs_subset.py`;
- поддержка произвольных `var_*` и `lev_*`;
- cache key по переменным/уровням;
- тесты URL generation.

### Этап 2. Cloudgram summary data

- скачать surface/layer fields;
- распарсить `LCDC/MCDC/HCDC/TCDC`, `APCP/ACPCP/PRATE/CPRAT`, `CRAIN/CSNOW/CFRZR/CICEP`, `cloud ceiling HGT`, `CAPE/CIN`, `convective cloud layer TCDC`;
- собрать `CloudgramData`.

### Этап 3. Cloudgram renderer

- строки summary;
- палитры clouds/precip/ceiling/cb;
- подписи на русском;
- отправка wide PNG as document при большом `to`.

### Этап 4. Telegram command and wizard

- `/cloudgram`;
- скрытый alias `/clouds`;
- wizard: point → mode → to/step → run;
- команда для копирования.

### Этап 5. Profile cloudgram

- `mode=profile`;
- pressure-time matrix по `TCDC`;
- overlay cloud ceiling, convective top/bottom, precip stripe.

### Этап 6. Validation

- unit tests synthetic datasets;
- smoke test on one real GFS run;
- visual QA for: no precip, stratiform cloud, convective precip, winter precip, low ceiling.

## Acceptance criteria

- `/cloudgram Москва` строит summary на `to=72 step=3`.
- `/cloudgram Москва mode=precip` показывает осадки и тип осадков.
- `/cloudgram Москва mode=cb` показывает Cb proxy, а не утверждает факт Cb.
- `/cloudgram Москва mode=profile` строит pressure-time облачность по `TCDC`, если включён второй этап.
- В каждом результате есть команда для повторного запуска.
- Все продукты используют один GFS run.
- Если часть полей отсутствует, бот не падает, а строит доступные слои и сообщает о пропусках.

## Источники для проверки состава GFS

- NCEP Product Inventory: https://www.nco.ncep.noaa.gov/pmb/products/gfs/
- GFS 0.25 pgrb2 f003 inventory: https://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2.0p25.f003.shtml
- NOMADS GFS 0.25 filter endpoint: https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25_1hr.pl
