"""Cheap, fail-closed feature policy. No providers, clients or caches imported.

An absent switch is OFF even when credentials or old saved recipes exist.
Restart the service after changing deployment environment variables.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping

DISABLED_SOURCE_MESSAGE = "Этот источник временно отключён. Откройте /start и выберите доступную модель."
_WN3_ALIASES = frozenset({
    'wn3', 'wn3_mean', 'wn3_ensemble', 'weathernext3', 'weathernext3_mean',
    'weather_next3', 'weathernext', 'weather_next_3',
})
_SOURCE_PARAM = re.compile(r'(?:^|\s)(?:source|source_id|model|ensemble|модель|ансамбль)\s*=\s*[\"\']?([\w-]+)', re.I)


class FeatureDisabledError(ValueError):
    pass


def weathernext3_enabled() -> bool:
    return os.getenv('WEATHERNEXT3_ENABLED', '0').strip().lower() in {'1', 'true', 'yes', 'on'}


def is_weathernext3(value: Any) -> bool:
    return str(value or '').strip().lower().replace('-', '_') in _WN3_ALIASES


def require_weathernext3() -> None:
    if not weathernext3_enabled():
        raise FeatureDisabledError(DISABLED_SOURCE_MESSAGE)


def product_available(product: str, params: Mapping[str, Any] | None = None) -> bool:
    if weathernext3_enabled():
        return True
    if is_weathernext3(product):
        return False
    return not (product == 'meteogram' and any(
        is_weathernext3((params or {}).get(key))
        for key in ('source', 'source_id', 'model', 'ensemble')
    ))


def require_product(product: str, params: Mapping[str, Any] | None = None) -> None:
    if not product_available(product, params):
        raise FeatureDisabledError(DISABLED_SOURCE_MESSAGE)


def disabled_input(text: str = '', callback: str = '') -> bool:
    """Reject both generations of old callbacks before geocoding/task creation."""
    if weathernext3_enabled():
        return False
    command = text.strip().split(maxsplit=1)[0].split('@', 1)[0].lower() if text.strip() else ''
    if command in {'/wn3', '/weathernext3'}:
        return True
    if command == '/meteogram' and any(is_weathernext3(m.group(1)) for m in _SOURCE_PARAM.finditer(text)):
        return True
    if callback.startswith(('w3|', 'wn3:', 'home:wn3', 'v1|wn3|')):
        return True
    # Covers native meteo:... and common v1|meteo|source|... controls.
    parts = re.split(r'[:|]', callback)
    return any(is_weathernext3(part) for part in parts)
