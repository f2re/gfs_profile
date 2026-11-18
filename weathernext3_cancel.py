"""Cooperative cancellation prevents new cloud requests after user cancellation."""
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT = ContextVar('wn3_cancel_event', default=None)


class Cancelled(RuntimeError):
    pass


def check_cancelled():
    event = _CURRENT.get()
    if event is not None and event.is_set():
        raise Cancelled('Запрос WeatherNext 3 отменён')


@contextmanager
def cancellation(event):
    token = _CURRENT.set(event)
    try:
        check_cancelled()
        yield
    finally:
        _CURRENT.reset(token)
