# Общая `/map` для Telegram, MAX и VK

## Архитектура

```text
/map / callback / location
→ platform adapter
→ messenger/map_router.py
→ messenger/map_service.py
→ composite_map.py / map_animation.py
→ CommonProductResult
→ Telegram / MAX / VK gateway
```

Метеорологическая карта, выбор GFS run, диапазон сроков и формирование PNG/MP4 не зависят от платформы.

## Первый запуск

Для нового пользователя:

```text
режим: Анимация
период: +0…+48 ч
шаг: 3 ч
кадров: 17
радиус: 100 км
подложка: places
```

Для анимации длиннее 48 часов шаг автоматически увеличивается так, чтобы число кадров не превышало `MAP_MAX_ANIMATION_FRAMES`. Например `+0…+96 ч` с запрошенным шагом 3 ч нормализуется до 6 ч.

## Режимы

```text
Анимация   mode=gif     MP4 H.264 при доступном ffmpeg, GIF fallback
Одна карта mode=single  один PNG
Серия PNG  mode=series  несколько PNG
```

Внутреннее имя `gif` оставлено для совместимости старых команд; пользовательский результат обычно MP4.

Примеры:

```text
/map Москва
/map Москва +24
/map Москва from=0 to=72 step=6 mode=gif
/map Москва from=0 to=24 step=3 mode=series basemap=roads radius=50
```

`/map Москва +24` всегда означает одну карту. `/map Москва` в общем MAX/VK flow использует закреплённый/последний recipe, а для нового пользователя — анимацию 48 часов.

## GFS run

Service сначала формирует фактический список кадров и проверяет публикацию максимального lead. Если новый цикл ещё не содержит дальний срок, выбирается предыдущий опубликованный run.

Saved recipe никогда не содержит `run/cycle`, поэтому повтор использует свежий подходящий цикл.

## Результат

Общая сводка показывает:

- GFS 0.25 и фактический run/cycle UTC;
- диапазон lead и valid UTC;
- requested point / центр области;
- радиус области;
- сетку GFS 0.25°;
- подложку;
- слои осадков, облачности, конвективной диагностики, ветра 500 гПа, явлений и видимости;
- отсутствующие поля, если они есть;
- явную маркировку карты как модельной, а не радара/наблюдения.

## Media по платформам

### Telegram

MP4 сначала отправляется как animation/video, затем есть document fallback. Серия PNG отправляется media-group пакетами до 10 изображений с fallback на отдельные фото.

### MAX

`send_animation()` использует native `video` upload для MP4:

```text
POST /uploads type=video
→ upload URL
→ attachment token
→ POST /messages
```

### VK

Для MP4 gateway сначала использует:

```text
video.save
→ upload_url
→ binary upload
→ messages.send attachment=video<owner>_<id>
```

Если community token/API не разрешает video upload, срабатывает platform-local fallback на document. Ошибка VK video upload не влияет на Telegram/MAX и не меняет common product.

## Saved recipes

Map recipe:

```text
point
from
to
step
mode
radius
basemap
```

`run/cycle` отсутствуют. Поддержаны repeat/pin/change и несколько разных карт одного пользователя.

## Проверка

Contract tests проверяют:

- default `+0…+48/3`;
- explicit `+24 → single`;
- auto-step для 96 часов;
- run selection по максимальному lead;
- common builder в MAX/VK router;
- geo + callbacks;
- recipe repeat без старого run;
- Telegram compatibility;
- native VK video и document fallback.

GFS — модель, не наблюдение, не радар и не радиозонд.
