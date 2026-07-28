from __future__ import annotations

from telegram import ReplyKeyboardMarkup

ADMIN_COMMAND_ROWS: tuple[tuple[str, ...], ...] = (
    ("/admin stats 7", "/admin stats 30"),
    ("/admin recent 10", "/admin recent 25"),
    ("/admin users", "/admin find"),
    ("/admin report requests 30", "/admin report users"),
    ("/admin help",),
)


def admin_keyboard() -> ReplyKeyboardMarkup:
    """Compact reply keyboard for administrator commands.

    Buttons intentionally send regular /admin commands. This keeps the admin UI
    usable even if callback updates are delayed and preserves CLI-style commands
    for copy/paste and logs.
    """

    return ReplyKeyboardMarkup(
        [list(row) for row in ADMIN_COMMAND_ROWS],
        resize_keyboard=True,
        selective=True,
    )
