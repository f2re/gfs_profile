# Общая `/meteogram` для Telegram, MAX и VK

## Архитектура

```text
point/model/ensemble/period/format
→ messenger/meteogram_service.py
→ meteogram_core.fetch_meteogram
→ meteogram_plot / meteogram_report
→ CommonProductResult
→ Telegram / MAX / VK gateway
```

Telegram сохраняет существующий мастер, но его product runner вызывает тот же common service, что MAX/VK.

## Первый запуск

```text
модель: GFS · NOAA/NCEP
период: 5 суток
результат: PNG
```

## Источники

Deterministic:

- GFS · NOAA/NCEP;
- ECMWF IFS;
- ECMWF AIFS;
- DWD ICON Global;
- ECCC GEM/GDPS.

Ensemble:

- NOAA GEFS;
- ECMWF IFS ENS;
- ECMWF AIFS ENS;
- DWD ICON-EPS;
- ECCC GEPS.

Разные ensemble systems не смешиваются. Периоды берутся из `available_periods(source)` и ограничиваются horizon конкретного источника.

## Форматы

```text
PNG
DOCX
PDF
```

PNG строится один раз. DOCX/PDF формируются из того же загруженного ряда и PNG без повторного запроса метеоданных. Если PDF renderer использует fallback, actual format и причина отражаются в metadata/result.

## Cycle/run

Для временных рядов Open-Meteo проект не получает достоверный исходный model cycle. Поэтому common result содержит:

```text
cycle = null
```

и текст прямо говорит, что cycle не указывается, если поставщик его не передал. Нельзя подставлять предполагаемый GFS/ECMWF run по времени получения.

## Common result

Сводка включает:

- requested point;
- provider/grid point при наличии;
- модель и provider;
- deterministic/ensemble;
- period/timezone;
- фактическое число ensemble members, если применимо;
- warnings/coverage;
- output format;
- маркировку как модельный прогноз, не наблюдение.

## Saved recipes

Recipe хранит:

```text
point
source
days
format
```

`run/cycle` отсутствуют. Можно отдельно сохранить, например, GFS 5 суток PNG и GEFS 10 суток PDF.

## Runtime resources

MAX/VK используют тот же process-wide:

```env
MAX_CONCURRENT_METEOGRAM=2
```

что и Telegram. Добавление новой платформы не умножает число тяжёлых fetch/render/report операций.

## MAX/VK flow

```text
/meteogram
→ точка / координаты / native location
→ одна модель / ансамбль
→ модель
→ период
→ PNG/DOCX/PDF
→ Построить
```

Прямые команды:

```text
/meteogram Москва source=gfs days=5 format=png
/meteogram Москва source=ecmwf_ifs days=10 format=pdf
/meteogram Москва ensemble=gefs days=10 format=docx
```

Ambiguous city сохраняет выбранные параметры до callback выбора точки.

## Fault isolation

Ошибка fetch/report/upload в VK влияет только на конкретный VK request. Общий runtime, Telegram и MAX продолжают работать. Аналогично для остальных платформ.
