# Сохранённые сценарии мессенджеров

Успешный интерактивный расчёт сохраняется как отдельный recipe:

```text
platform + user + product + point + params
```

В recipe не попадают `run/cycle`, message/status ids, callback ids, кандидаты геокодинга и wizard state. Поэтому repeat повторяет пользовательский сценарий на актуальном опубликованном запуске модели.

Одинаковый recipe дедуплицируется; обновляются `success_count` и `last_success_at`. Для одного пользователя/платформы хранятся до 24 recent и до 8 pinned recipes.

## Telegram

Telegram recipes используют:

```env
TELEGRAM_PREFERENCES_DB=.cache_gfs/telegram_preferences.sqlite3
```

`/settings → Сохранённые сценарии` позволяет повторить, изменить, закрепить, поставить в расписание или удалить точный `recipe_id`.

## MAX / VK

Messenger-neutral store:

```env
MESSENGER_PREFERENCES_DB=.cache_gfs/messenger_preferences.sqlite3
```

Recipe UX подключён к четырём common vertical slices.

### `/profile`

```text
params={lead}
```

### `/aero`

```text
params={lead, diagram_type=skewt}
```

### `/windgram`

```text
params={from, to, step, top, param}
```

Можно хранить, например, два независимых варианта одного пункта: `ветер +0…+120` и `температура +0…+240`.

### `/cloudgram`

```text
params={from, to, step, mode}
```

Отдельными recipes могут быть, например, `Подробно +0…+72` и `Кратко +0…+120`. `run/cycle` не входят в signature. Repeat передаёт `run=None`, а common service заново выбирает фактически опубликованный GFS cycle, содержащий максимальный требуемый lead.

Для всех четырёх продуктов `/start` может показывать быстрый recipe; команда продукта без аргументов открывает pinned/latest вариант; pin/repeat работают после restart процесса.

## Контракты

Обязательно проверяется:

- одинаковый recipe дедуплицируется;
- `run/cycle` отсутствуют в signature;
- разные параметры одного продукта дают разные recipes;
- pin влияет на default/quick ranking;
- Telegram/MAX/VK изолированы по platform;
- callback recipe работает после очистки session state;
- repeat использует новый run;
- `/profile`, `/aero`, `/windgram`, `/cloudgram` MAX/VK используют один store;
- windgram/cloudgram repeat выбирают run по максимальному lead;
- Telegram schedule использует immutable snapshot конкретного recipe.
