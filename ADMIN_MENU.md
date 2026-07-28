# Admin menu

Команда `/admin` теперь показывает reply-клавиатуру администратора.

Кнопки отправляют обычные команды:

```text
/admin stats 7              /admin stats 30
/admin recent 10            /admin recent 25
/admin users                /admin find
/admin report requests 30   /admin report users
/admin help
```

`/admin find` без аргумента показывает подсказку. Для поиска нужно отправить уточнённую команду, например:

```text
/admin find @username
/admin find 123456789
```

Поиск Telegram-пользователей работает только по локальной базе известных пользователей бота. Глобальный поиск через Telegram Bot API недоступен.
