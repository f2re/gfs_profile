# Общая `/route` для Telegram, MAX и VK

## Архитектура

```text
origin -> destination + параметры
→ messenger/route_router.py
→ messenger/route_service.py
→ route_profile_contract / route_profile
→ PNG + CSV + CommonProductResult
→ Telegram / MAX / VK gateway
```

Метеорологический расчёт, waypoint/ETA, выбор GFS run, категории рисков и файлы не зависят от платформы.

## Ввод

Прямая команда:

```text
/route Москва -> Санкт-Петербург +24 speed=300 step=50 mode=pro
```

Интерактивно:

```text
/route
→ origin -> destination
или native location как origin
→ destination
→ lead / speed / grid / mode
→ Построить
```

## Параметры

```text
lead: +0, +6, +12, +24, +48
speed: 150, 300, 450, 600 км/ч
spatial_step: 25, 50, 100 км
mode: simple / pro
```

`simple` и `pro` используют одинаковые исходные данные и risk contract; отличается только presentation.

## Выбор GFS run

Маршрут не может выбирать цикл только по departure lead. Service сначала строит waypoint/ETA specs и вычисляет максимальный реально требуемый lead на конце маршрута. Затем `latest_available_run_for_lead(max_eta_lead)` проверяет публикацию именно этого срока.

Поэтому длинный маршрут с вылетом `+24`, но ETA `+30`, не привязывается к новому циклу, где опубликован только `f024`.

Явный `run=YYYYMMDD/HH` в прямой команде сохраняет прежнюю семантику.

## Result

Общий результат содержит:

- фактический GFS run/cycle UTC;
- departure lead и max ETA lead;
- origin/destination;
- distance/duration;
- скорость и пространственный шаг;
- число waypoint;
- PNG разрез;
- CSV точек/ETA/уровней/диагностик;
- одинаковую краткую модельную сводку.

GFS явно обозначается как модель, не радиозонд и не наблюдение.

## Saved recipe

Route recipe хранит:

```text
origin={lat,lon,label,source}
destination={lat,lon,label,source}
lead
speed
spatial_step
mode
```

`run/cycle` не сохраняются. Repeat выбирает новый подходящий run по максимальному ETA lead.

Критически важно: origin/destination маршрута **не заменяют active point пользователя** для `/profile`, `/map`, `/meteogram` и других point-products.

## Progress

Одна status message редактируется по стадиям:

```text
проверка max lead / run
→ waypoint/GFS profiles
→ GRIB parse
→ диагностика
→ PNG/CSV
→ отправка
```

## Fault isolation

Ошибка route request или media upload одной платформы не влияет на остальные платформы. Provider lifecycle использует общий `ready/degraded/off` contract.

## Router isolation

Все helper methods route-prefixed (`_run_route`, `_send_route_card`, `_default_route_recipe` и т.д.). Это обязательный architectural rule: новый child router не должен случайно override методы предыдущего vertical slice через Python dynamic dispatch.
