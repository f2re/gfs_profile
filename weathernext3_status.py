"""Local configuration status only; never pretends to verify cloud access."""
from __future__ import annotations

from feature_flags import weathernext3_enabled
import importlib.util
import os
import sys


def status() -> dict:
    if not weathernext3_enabled():
        return {'enabled': False, 'cloud_access_verified': False, 'data_kind': 'model'}
    def present(name, alias=''):
        return bool((os.getenv(name) or (os.getenv(alias) if alias else '') or '').strip())
    bq = present('WEATHERNEXT3_BIGQUERY_PROJECT', 'WN3_BIGQUERY_PROJECT') and present('WEATHERNEXT3_BIGQUERY_DATASET', 'WN3_BIGQUERY_DATASET')
    gcs = present('WEATHERNEXT3_GCS_BILLING_PROJECT')
    deps = all(importlib.util.find_spec(name) is not None for name in ('zarr', 'obstore'))
    return {'bigquery_configured': bq, 'gcs_configured': gcs, 'zarr_dependencies': deps,
            'upper_runtime_supported': sys.version_info >= (3, 11),
            'cloud_access_verified': False, 'data_kind': 'model'}


def status_text() -> str:
    value = status()
    if not weathernext3_enabled():
        return 'Источник временно отключён администратором.'
    return ('WeatherNext 3\nBigQuery: ' + ('настройки заданы' if value['bigquery_configured'] else 'не настроен') +
            '\nGCS: ' + ('настройки заданы' if value['gcs_configured'] else 'не настроен') +
            ('\nВерхняя атмосфера: нужен Python 3.11+' if not value['upper_runtime_supported'] else '') +
            '\nZarr/obstore: ' + ('установлены' if value['zarr_dependencies'] else 'установите requirements-weathernext3.txt') +
            '\nЭто проверка конфигурации, не подтверждение ADC/allowlist или публикации прогноза.')
