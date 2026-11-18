from __future__ import annotations

"""Expose one aerological product and remove historical diagram aliases."""

from typing import Any

from telegram.ext import CommandHandler


def _human_home_text() -> str:
    from telegram_concise_ux import home_text
    return home_text()


def _human_help_text() -> str:
    from telegram_concise_ux import help_text
    return help_text()


def install(namespace: dict[str, Any]) -> None:
    # Compatibility hook; copy lives in telegram_concise_ux for every entry path.
    namespace["_AERO_SINGLE_MODE_INSTALLED"] = True


def configure_application(application) -> None:
    """Remove /skewt handler after the embedded core builds the app."""

    for group, handlers in list(application.handlers.items()):
        for handler in list(handlers):
            commands = set(getattr(handler, "commands", ()) or ())
            if isinstance(handler, CommandHandler) and "skewt" in commands:
                application.remove_handler(handler, group=group)
