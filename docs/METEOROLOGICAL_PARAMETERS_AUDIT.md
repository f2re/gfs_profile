# Аудит исходных полей GFS и расчётных метеорологических параметров

> Статус: аудит реализации ветки `telegram-bot`, 28.07.2026.  
> Область: `/profile`, `/aero`, `/windgram`, `/cloudgram`, `/map`, `/route`.  
> Это описание **фактического кода**, а не проектируемой методики.

## 1. Выводы аудита

В базовом вертикальном профиле температура, относительная влажность, ветер и геопотенциальная высота считываются корректно из изобарических полей GFS. Скорость и метеорологическое направление ветра, потенциальная температура, точка росы и высоты изотерм рассчитываются понятными воспроизводимыми формулами.

При этом в диагностике облачности, видимости, грозы, обледенения, болтанки и интегрального авиационного риска обнаружены ошибки и методические ограничения. До исправления результаты этих блоков следует считать **экспериментальными прокси**, а не эквивалентом специализированных продуктов icing/turbulence/thunderstorm.

### Критические и существенные замечания

| Приоритет | Проблема | Где | Последствие | Требуемое действие |
|---|---|---|---|---|
| **P0** | `VIS` GFS имеет единицу **метр**, но `visibility_km()` делит на 1000 только значения `>200`. Значение 100 м превращается в 100 км | `weather_diagnostics.py`; Cloudgram, Map, Route | Самая опасная низкая видимость может интерпретироваться как отличная; пропадают FG и высокий риск | Всегда переводить `VIS[m] / 1000`; добавить тесты 50/100/200/1000/10000 м |
| **P0** | Cloudgram получает первое поле с подходящим коротким именем, не фильтруя `typeOfLevel`, слой и `stepType` | `gfs_subset.scalar_from_datasets()` | CAPE/CIN, TCDC, облачные ярусы, HGT, APCP/PRATE могут выбираться из другого слоя или статистического интервала | Выбирать по `GRIB_shortName + typeOfLevel + level + stepType + startStep/endStep` |
| **P0** | Map ищет CAPE/CIN с `typeOfLevel="surface"`, тогда как стандартная инвентаризация GFS содержит CAPE/CIN на слоях `180–0`, `90–0`, `255–0 mb above ground` | `composite_map.py` | CAPE/CIN карты, вероятно, остаются `None`; грозовая диагностика карты теряет основной предиктор | Явно выбрать согласованный слой CAPE/CIN, предпочтительно `180–0 mb above ground`, и проверить на реальном GRIB |
| **P0/P1** | `HGT:cloud ceiling` — геопотенциальная высота, но используется как `ceiling_m`/ВНГО без гарантированного выбора `cloudCeiling` и без приведения к AGL | Cloudgram, Route | На возвышенной местности пороги 300/1000 м могут быть физически неверны; возможно чтение вообще другого HGT | Фильтровать `typeOfLevel=cloudCeiling`; определить вертикальный datum; для ВНГО вычитать орографию/высоту поверхности |
| **P1** | В грозовой функции аргумент `conv_cloud_pct` фактически получает общий `TCDC`, а не облачность конвективного слоя | Cloudgram, Map | Усиливающий признак грозы имеет другую физическую семантику | Выбирать `TCDC` на `convectiveCloudLayer` отдельно |
| **P1** | В Cloudgram выражение `cprat or prate` заменяет нулевой CPRAT общим PRATE | `cloudgram_product.py` | Сильные стратиформные осадки могут стать «конвективным» признаком | Использовать `cprat if cprat is not None else prate`; лучше не подменять CPRAT общим PRATE |
| **P1** | Карта рисует ⚡ при `storm >= 2`, тогда как код TSRA подтверждает только при `storm >= 3` и осадках | `composite_map.py` | Ложная маркировка грозы | Для молнии применять тот же контракт, что `weather_code()` |
| **P1** | При отсутствии APCP карта кладёт PRATE, измеряемый в мм/ч, в поле `precip`, которое обычно содержит мм | `composite_map.py` | Один цвет и одни пороги получают разные размерности | Разделить `precip_amount_mm` и `precip_rate_mmh`; легенду и пороги строить по типу поля |
| **P1** | Слой «Осадки» на аэрологической диаграмме проверяет `CLWMR/ICMR/RWMR/SNMR/GRLE`, но профиль их не скачивает | `aero_plot.py`, `gfs_core.py` | Слой осадков фактически всегда пуст | Либо запросить гидрометеоры на изобарических уровнях, либо удалить этот слой |
| **P1** | Высоты изотерм, LCL/LFC/EL и уровней профиля являются геопотенциальными высотами MSL, но в UI не всегда указан datum | Profile, Aero, Windgram | Пользователь может принять MSL за AGL | Везде маркировать `MSL`; для AGL отдельно вычитать высоту поверхности |
| **P1** | «Обледенение» и «болтанка» — эвристические прокси по T/RH и shear/Ri, но местами подписаны как фактическая интенсивность | Aero, Route | Категории могут восприниматься как специализированный авиационный прогноз | Переименовать в «прокси риска»; не сопоставлять 0–3 с TRACE/LGT/MOD/SEV |
| **P1** | Surface parcel для Aero начинается с нижнего доступного изобарического уровня, а не обязательно с фактической поверхности | `aero_plot.py` | SB-CAPE/LCL могут быть искажены над возвышенностями и при отсутствующем 1000 гПа | Добавить surface/2 m данные либо явно назвать «parcel от нижнего доступного изобарического уровня» |
| **P2** | `precipitable_water(p, t, td)` вызывается с тремя позиционными аргументами, но MetPy 1.7 требует `(pressure, dewpoint)` | `aero_plot.py` | PWAT всегда попадает в обработчик ошибки и становится `None` | Вызывать `precipitable_water(p, td)`; добавить unit-test |
| **P2** | Пороги осадков, облаков, грозы, риска и сдвига являются внутренними и не имеют опубликованной калибровки | Cloudgram, Route, Map, Aero | Баллы 0–4 и R0–R3 нельзя трактовать как ICAO/FAA категории | Зафиксировать как локальную шкалу; провести верификацию по METAR/TAF/PIREP/lightning |
| **P3** | GFS `HGT` имеет единицу gpm, в коде называется метрами | все вертикальные продукты | Небольшая систематическая разница между geopotential metre и geometric metre | Переименовать в `geopotential_height_gpm` или выполнять преобразование |
| **P3** | Высота LCL/LFC/EL интерполируется линейно по `p–z`, а не по `ln(p)–z` | Aero | Небольшая интерполяционная погрешность | Интерполировать по `ln(p)` либо использовать высоту, возвращённую согласованной термодинамической процедурой |

## 2. Источник данных и выбор узла

| Параметр | Фактическая реализация | Формула/правило | Статус |
|---|---|---|---|
| Модель | GFS deterministic, `gfs.tCCz.pgrb2.0p25.fFFF` | NOMADS GRIB Filter, subset по точке/области | Корректно |
| Сетка | 0.25° | `round(lat/0.25)*0.25`, аналогично lon | Корректно; на точной половине действует Python round-to-even |
| Срок | +0…+120 каждый час; +123…+384 каждые 3 ч | `canonical_leads()` | Соответствует GFS 0.25 NOMADS |
| Выбор цикла | Проверяется существование именно `fFFF.idx`; при отсутствии берётся предыдущий цикл | `latest_available_run_for_lead()` | Корректно |
| Маршрутный срок | `lead = nearest canonical(departure_lead + distance/speed)` | до +120 округление до часа, далее до 3 ч | Допустимая дискретизация; возможна ошибка времени до 0.5/1.5 ч |
| Высота | `HGT` на изобарической поверхности | GFS: geopotential height `[gpm]` | В коде ошибочно именуется `m` |

## 3. Исходные поля GFS

### 3.1 Изобарический профиль

| GFS shortName | cfgrib/xarray | Уровень | Единица GFS | Использование |
|---|---|---|---|---|
| `TMP` | `t` | `isobaricInhPa` | K | температура, Td, θ, изотермы, CAPE/CIN, icing |
| `RH` | `r` | `isobaricInhPa` | % | точка росы, облачные и ледяные прокси |
| `UGRD` | `u` | `isobaricInhPa` | m/s | скорость/направление, shear, Ri, годограф |
| `VGRD` | `v` | `isobaricInhPa` | m/s | скорость/направление, shear, Ri, годограф |
| `HGT` | `gh` | `isobaricInhPa` | gpm | вертикальная координата и высоты изотерм |

Эти пять полей используются `/profile`, `/windgram`, `/aero` и вертикальной частью `/route`.

### 3.2 Облачность, осадки и явления

| GFS shortName | Уровень/слой, который требуется по смыслу | Единица | Использование в коде | Замечание |
|---|---|---|---|---|
| `LCDC` | low cloud layer | % | низкая облачность | Есть instant и interval average; Cloudgram их не различает |
| `MCDC` | middle cloud layer | % | средняя облачность | То же |
| `HCDC` | high cloud layer | % | высокая облачность | То же |
| `TCDC` | entire atmosphere | % | общая облачность | Есть также isobaric, convective cloud layer и average; требуется строгий metadata-filter |
| `TCDC` | convective cloud layer | % | должен быть конвективным облачным признаком | Сейчас отдельно не выбирается |
| `HGT` | cloud ceiling | gpm | `ceiling_m` | Не доказано AGL; сейчас нет строгого фильтра |
| `APCP` | surface accumulation | kg/m² ≡ mm воды | количество осадков | Необходимо учитывать `startStep/endStep` |
| `PRATE` | surface | kg/m²/s | `×3600 → mm/h` | Есть instantaneous и average |
| `ACPCP` | surface accumulation | kg/m² ≡ mm | конвективные осадки | Требуется временной интервал |
| `CPRAT` | surface | kg/m²/s | `×3600 → mm/h` | Конвективная интенсивность |
| `CRAIN` | surface | category 0/1 | дождь | `>=0.5` |
| `CSNOW` | surface | category 0/1 | снег | `>=0.5` |
| `CFRZR` | surface | category 0/1 | переохлаждённый дождь | `>=0.5` |
| `CICEP` | surface | category 0/1 | ледяные гранулы | `>=0.5` |
| `CAPE` | один выбранный above-ground layer | J/kg | грозовой score | Слой сейчас не унифицирован |
| `CIN` | тот же слой, что CAPE | J/kg | грозовой score | Должен выбираться парой с CAPE |
| `VIS` | surface | m | видимость | Текущий конвертер содержит P0-ошибку |
| `UGRD`, `VGRD` | 500 hPa | m/s | векторный ветер на карте | Корректно |

### 3.3 Поля, на которые ссылается код, но профиль их не загружает

| GFS shortName | Единица | Предполагаемое назначение | Фактический результат |
|---|---|---|---|
| `CLWMR` | kg/kg | облачная жидкая вода | отсутствует в profile DataFrame |
| `ICMR` | kg/kg | облачный лёд | отсутствует |
| `RWMR` | kg/kg | дождевая вода | отсутствует |
| `SNMR` | kg/kg | снег | отсутствует |
| `GRLE` | kg/kg | крупа | отсутствует |

Следствие: `aero_plot._diagnose_layers()` не может обнаружить осадки по гидрометеорам.

## 4. Базовые расчётные параметры профиля

| Параметр | Исходные поля | Фактическая формула | Обоснование | Оценка |
|---|---|---|---|---|
| Температура, °C | `TMP[K]` | `T = TMP − 273.15` | SI | Корректно |
| RH, % | `RH` | `clip(RH, 1, 100)` | защитное ограничение | RH<1% искусственно повышается до 1% |
| Точка росы | T, RH | `α=ln(RH/100)+17.625T/(243.04+T)`; `Td=243.04α/(17.625−α)` | Magnus, Alduchov–Eskridge | Корректная аппроксимация |
| Скорость ветра | U, V | `V=√(U²+V²)` | векторная норма | Корректно |
| Направление ветра | U, V | `D=(270°−atan2(V,U)·180/π) mod 360°` | метеорологическое направление **откуда** | Корректно |
| Потенциальная температура | T, p | `θ=T_K(1000/p)^0.286` | уравнение Пуассона | Корректно как сухая аппроксимация |
| Высота, км | HGT | `z_km=HGT/1000` | численно gpm→kgpm | Нужно маркировать geopotential/MSL |
| Максимальный ветер | speed по всем уровням | `max(V)` | — | Корректно |
| Средняя высота уровня Windgram | HGT по срокам | среднее арифметическое | подпись оси | Описательная величина |

## 5. Высоты изотерм и уровней

| Параметр | Алгоритм | Формула | Ограничение |
|---|---|---|---|
| 0/−10/−20 °C | профиль сортируется по HGT; берётся первое пересечение снизу | `z=z0+(T*−T0)/(T1−T0)·(z1−z0)` | Возвращается только первое пересечение; datum MSL |
| Уровень конденсации | MetPy `lcl(p0,T0,Td0)` | итерационная формула Romps/MetPy | `p0` — нижний доступный изобарический уровень, не обязательно поверхность |
| LFC | MetPy `lfc()` | пересечение parcel/environment | зависит от выбранного parcel |
| EL | MetPy `el(which="top")` | последнее пересечение parcel/environment | зависит от выбранного parcel |
| Высота LCL/LFC/EL | интерполяция GFS HGT по давлению | линейная `p–z` | предпочтительна `ln(p)–z`; MSL |

## 6. Аэрологическая диаграмма `/aero`

### 6.1 Термодинамика

| Показатель | Реализация | Источник/формула | Статус |
|---|---|---|---|
| Кривая частицы | MetPy `parcel_profile(p,T0,Td0)` | сухоадиабатический подъём до LCL, затем влажноадиабатический | Корректно для выбранного parcel |
| SB CAPE/CIN | `cape_cin()` для parcel от нижнего уровня | `−Rd∫(Tv_parcel−Tv_env)dlnp` | Формула корректна; старт не всегда surface |
| ML CAPE/CIN | `mixed_layer_cape_cin()` | default layer 100 hPa | Корректно |
| MU CAPE/CIN | `most_unstable_cape_cin()` | наиболее неустойчивая частица | Корректно |
| CAPE в карточке | `max(SB, ML, MU)` | внутренняя политика | Название «CAPE, максимум» корректно |
| TT | MetPy | `TT=(T850+Td850)−2T500` | Корректно |
| K-index | MetPy | `K=(T850−T500)+Td850−(T700−Td700)` | Корректно |
| PWAT | должен быть `precipitable_water(p,Td)` | `−1/(ρlg)∫r dp` | **Сломан неверной сигнатурой** |
| θe | Bolton-подобная аппроксимация | `θe=θ exp[(3376/TL−2.54)r(1+0.81r)]` | Физически обосновано |
| Кривая насыщения надо льдом | из Td вычисляется e над водой, затем инверсия e над льдом | `Tf=272.62 ln(e/6.112)/(22.46−ln(e/6.112))` | Приближённая frost-point curve, только T≤0 |

### 6.2 Диагностические слои Aero

| Слой | Фактический критерий | Что это означает | Научная оценка |
|---|---|---|---|
| Облачность | `RH≥85% OR (RH≥78% AND T−Td≤3°C)` | влажный слой | Эвристика; не прямое GFS cloud water/cloud fraction |
| Обледенение | cloud-mask AND `−20≤T≤0°C` | возможная видимая влага при отрицательной T | Только potential icing proxy; интенсивность не определяется |
| Болтанка | `Ri<0.25 OR shear≥10 m/s/km` | динамическая неустойчивость/сильный вертикальный сдвиг | Ri<0.25 имеет теоретическую основу как условие неустойчивости, но не даёт авиационную интенсивность |
| Конвективная неустойчивость | `dθe/dz≤−3 K/km` | θe убывает с высотой | Порог −3 — внутренняя эвристика |
| Осадки | любой `CLWMR/ICMR/RWMR/SNMR/GRLE >0` | гидрометеоры | Сейчас не работает: полей нет |

Gradient Richardson number:

`Ri = (g/θ)(∂θ/∂z) / [(∂u/∂z)²+(∂v/∂z)²]`.

Vertical shear:

`S = √[(∂u/∂z)²+(∂v/∂z)²]`, в интерфейсе `m/s/km`.

## 7. Cloudgram

### 7.1 Выходные параметры

| Выход | Исходные поля | Преобразование |
|---|---|---|
| Low/Mid/High/Total cloud | LCDC/MCDC/HCDC/TCDC | clip 0…100% |
| ВНГО/ceiling | HGT cloud ceiling | raw gpm назван `m` |
| Осадки за интервал | APCP | `max(0, APCP)`; kg/m² численно равно mm воды |
| Интенсивность | PRATE | `max(0, PRATE·3600)` mm/h |
| Конвективные осадки | ACPCP | kg/m² → mm |
| Конвективная интенсивность | CPRAT | kg/m²/s ×3600 |
| Тип осадков | CRAIN/CSNOW/CFRZR/CICEP | набор флагов |
| Видимость | VIS | должна быть `VIS/1000` km; сейчас ошибочно условное деление |
| Гроза | CAPE, CIN, ACPCP, CPRAT, convective TCDC | custom `thunder_score` |
| Явление | precip type + storm + visibility | `TSRA/FZRA/SN/RA/FG/—` |
| Опасность 0…4 | осадки, ВНГО, VIS, convective score | максимум внутренних порогов |

### 7.2 Грозовой score

Фактическая шкала:

| Балл | Критерий |
|---|---|
| 0 | признаков недостаточно |
| 1 | `ACPCP≥0.5 mm` или `rate≥1.5 mm/h`, либо `CAPE≥250 J/kg` |
| 2 | `CAPE≥500`, `CIN>−200`, есть `ACPCP≥0.1` или `rate≥0.5` |
| 3 | `CAPE≥1000`, `CIN>−100`, сильные конвективные осадки и дополнительно cloud/rate condition |

`TSRA` выдаётся только при score≥3 и осадках >0.2. Эта шкала является **локальной эвристикой**, а не официальным алгоритмом GFS/NCEP.

### 7.3 Опасность Cloudgram

| Балл | Фактические триггеры |
|---|---|
| 1 | precip≥0.2 mm |
| 2 | precip≥7 mm; ceiling<1000 m; VIS<5 km |
| 3 | ceiling<300 m; VIS<1 km; thunder_score≥2 |
| 4 | TSRA или thunder_score≥3 |

Есть несогласованность: score 2 называется конвективным потенциалом, но Cloudgram уже повышает общую опасность до 3; Route для того же score даёт R2.

## 8. Карта `/map`

| Параметр | Источник | Отображение | Аудит |
|---|---|---|---|
| Осадки | APCP; fallback PRATE | цветовая заливка | fallback смешивает mm и mm/h |
| Облачность | TCDC entire atmosphere | серая прозрачная заливка | Требуется instant/average contract |
| Грозовой риск | CAPE/CIN + ACPCP/CPRAT + cloud | ⚡ | CAPE/CIN ищутся на неверном `surface`; ⚡ порог ниже TSRA |
| Видимость | VIS | подпись при <10 km | P0-конвертер |
| Тип явления | categorical rain/snow/freezing flags | значок | Смешанные типы упрощаются |
| Ветер | UGRD/VGRD 500 hPa | quiver | Корректно, направление вектора «куда» как принято для quiver |

## 9. Маршрут `/route`

### 9.1 Обледенение

Фактический score на каждом изобарическом уровне:

| Score | Критерий |
|---|---|
| 0 | T вне −20…+0.5°C или RH<80% |
| 1 | −20…+0.5°C и RH≥80% |
| 2 | −15…−3°C и RH≥90% |
| 3 | −12…−5°C и RH≥95% |

Это не расчёт ледоводности и не прогноз интенсивности обледенения. Отсутствуют cloud liquid water, supercooled liquid water, размер капель, вертикальная скорость и SLD. FAA связывает потенциальное обледенение с видимой влагой и отрицательной температурой, но степень обледенения нельзя надёжно получить только из T/RH.

### 9.2 Болтанка/сдвиг

Между соседними изобарическими уровнями:

`S = √[(u1−u0)²+(v1−v0)²] / |z1−z0|`, единица m/s/km.

| Score | S |
|---|---|
| 0 | <6 |
| 1 | ≥6 |
| 2 | ≥10 |
| 3 | ≥15 m/s/km |

Сильная категория R3 требует устойчивости по вертикали: icing score 3 на двух соседних узлах либо turbulence score 3 на трёх узлах. Это разумная защита от одиночного шумного слоя, но сами пороги не калиброваны по EDR/PIREP. FAA operational turbulence uses EDR-based GTG, а не одну вертикальную производную ветра.

### 9.3 Облачность

`cloud_mask = RH≥80%`. Это визуальная эвристика, не прямой TCDC/CLWMR.

### 9.4 Итоговый риск точки

`point_risk = max(surface_risk, vertical_risk)`.

Surface risk:

| R | Критерий |
|---|---|
| R1 | precip≥0.2 mm |
| R2 | precip≥7 mm; ceiling<1000 m; VIS<5 km; thunder_score≥2 |
| R3 | ceiling<300 m; VIS<1 km; подтверждённый `phenomena=="TSRA"` |

Vertical risk:

| R | Критерий |
|---|---|
| R1 | icing/turbulence≥1 или max wind≥20 m/s |
| R2 | локальный icing/turbulence≥3, устойчивый moderate, либо max wind≥30 m/s |
| R3 | устойчивый severe icing/turbulence |

Шкала R0–R3 внутренняя. Она не соответствует VFR/MVFR/IFR/LIFR, ICAO SIGMET severity или PIREP intensity.

### 9.5 Агрегация карточки участка

Маршрут разбивается примерно на карточку каждые 115 км, 3…10 карточек. Для участка:

- R3: гроза или VIS<1 km/ceiling<300 m; либо минимум две R3-точки и ≥1/3 участка;
- R2: локальный пик R3 или ≥1/3 точек R2+;
- R1: есть хотя бы R1;
- иначе R0.

Негрозовые пиктограммы показываются, если покрывают ≥20% карточки; облачность — ≥35%.

## 10. Что подтверждено источниками, а что является локальной эвристикой

| Группа | Статус |
|---|---|
| TMP/RH/U/V/HGT и их единицы | подтверждено инвентаризацией NCEP |
| T, wind speed/direction, θ, линейная изотерма | стандартные формулы |
| Magnus Td | опубликованная аппроксимация Alduchov–Eskridge |
| CAPE/CIN, LCL/LFC/EL, TT, K, ML/MU parcel | MetPy и приведённые в его документации первичные формулы |
| θe | Bolton (1980) |
| Ri formula и порог 0.25 как условие shear instability | Miles–Howard/MetPy; **не** категория интенсивности болтанки |
| Потенциальное icing = влага + отрицательная T | соответствует общей FAA-физике |
| RH-пороги облаков и icing severity 0…3 | локальная эвристика, внешней калибровки нет |
| shear 6/10/15 m/s/km → 1/2/3 | локальная эвристика |
| thunder_score | локальная эвристика |
| hazard 0…4 и route R0…R3 | локальная эвристика |
| precip 0.2/7 mm, VIS 5/1 km, ceiling 1000/300 m | локальная шкала; не официальные flight categories |

## 11. Обязательный план исправлений

1. **P0 — VIS:** исправить единицы и покрыть тестами.
2. **P0 — metadata selection:** заменить `scalar_from_datasets()` в метеопродуктах на строгий селектор GRIB metadata.
3. **P0 — CAPE/CIN:** выбрать и документировать один GFS layer во всех продуктах.
4. **P0/P1 — ceiling:** определить datum, получать cloud-ceiling HGT строго и вычислять AGL.
5. **P1 — thunder:** единый контракт Cloudgram/Map/Route; convective TCDC отдельно; убрать `cprat or prate`.
6. **P1 — precipitation semantics:** не смешивать accumulation и rate.
7. **P1 — Aero hydrometeors:** запросить поля или убрать неработающий слой.
8. **P1 — terminology:** «прокси обледенения/болтанки/грозового потенциала», а не наблюдаемое явление.
9. **P2 — PWAT:** исправить сигнатуру.
10. Создать regression-тест с реальным `.idx`/GRIB fixture, проверяющий shortName, level, stepType и единицы.

## 12. Файлы реализации

- [`gfs_core.py`](../gfs_core.py) — профиль и базовые производные.
- [`gfs_subset.py`](../gfs_subset.py) — generic GRIB subset/scalar selector.
- [`weather_diagnostics.py`](../weather_diagnostics.py) — видимость, гроза, код явления.
- [`cloudgram_product.py`](../cloudgram_product.py) — Cloudgram.
- [`composite_map.py`](../composite_map.py) — карта.
- [`aero_plot.py`](../aero_plot.py) — термодинамика и диагностические слои.
- [`route_profile.py`](../route_profile.py) — вертикальные поля маршрута.
- [`route_profile_contract.py`](../route_profile_contract.py) — интегральный риск.
- [`route_profile_vertical_policy.py`](../route_profile_vertical_policy.py) — устойчивость vertical-risk.
- [`route_profile_card_policy.py`](../route_profile_card_policy.py) — агрегация участков.
- [`formatters_ru.py`](../formatters_ru.py) — профиль и изотермы.
- [`windgram_product.py`](../windgram_product.py) — Windgram.

## 13. Первичные и официальные источники

1. [NCEP GFS product inventory](https://www.nco.ncep.noaa.gov/pmb/products/gfs/)
2. [NCEP GFS f003 inventory: fields, levels, units and statistical intervals](https://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2.1p00.f003.shtml)
3. [NCEP NOMADS GFS 0.25 inventory](https://www.nco.ncep.noaa.gov/pmb/products/gfs/nomads/)
4. [MetPy CAPE/CIN](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.cape_cin.html)
5. [MetPy mixed-layer CAPE/CIN](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.mixed_layer_cape_cin.html)
6. [MetPy most-unstable CAPE/CIN](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.most_unstable_cape_cin.html)
7. [MetPy K Index](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.k_index.html)
8. [MetPy Total Totals](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.total_totals_index.html)
9. [MetPy precipitable water](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.precipitable_water.html)
10. [MetPy gradient Richardson number](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.gradient_richardson_number.html)
11. [Alduchov & Eskridge (1996), Magnus approximation](https://doi.org/10.1175/1520-0450(1996)035%3C0601:IMFAOS%3E2.0.CO;2)
12. [Bolton (1980), equivalent potential temperature](https://doi.org/10.1175/1520-0493(1980)108%3C1046:TCOEPT%3E2.0.CO;2)
13. [Miles (1961), stability of heterogeneous shear flows](https://doi.org/10.1017/S0022112061000305)
14. [FAA Aviation Weather Handbook](https://www.faa.gov/regulationspolicies/handbooksmanuals/aviation/faa-h-8083-28b-aviation-weather-handbook)
15. [FAA AIM: airframe icing](https://www.faa.gov/air_traffic/publications/atpubs/aim_html/chap7_section_1.html)
16. [FAA turbulence program: operational EDR/GTG](https://www.faa.gov/nextgen/programs/weather/awrp/turbulence)

## 14. Граница применимости

Все продукты являются диагностикой детерминированной модели GFS на сетке 0.25°. Они не являются радиозондом, наблюдением, радаром, PIREP, SIGMET/GAMET, FIP/CIP или GTG. До устранения P0/P1-дефектов блоки видимости, ВНГО, грозы, обледенения, болтанки и интегрального риска нельзя использовать как самостоятельное основание для авиационного решения.
