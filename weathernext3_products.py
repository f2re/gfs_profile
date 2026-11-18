"""WN3 product adapters; reuse existing profile, Skew-T and windgram rendering."""
from __future__ import annotations

from feature_flags import require_weathernext3

import tempfile
from pathlib import Path

import pandas as pd

from messenger.contracts import CommonProductResult, ProductAttachment, ProgressEvent
from messenger.profile_service import plain_profile_summary


def csv_attachment(rows, name: str) -> ProductAttachment:
    with tempfile.NamedTemporaryFile(prefix=f'wn3_{name}_', suffix='.csv', delete=False) as handle:
        path = Path(handle.name)
    try:
        pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig', na_rep='')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return ProductAttachment('file', path, path.name, 'WeatherNext 3 · модельные данные · CSV', 'text/csv')


def upper_product(point, kind, params, *, provider=None, progress_callback=None):
    require_weathernext3()
    from weathernext3_zarr import WeatherNext3ZarrProvider, LEVELS
    from messenger.weathernext3_service import _map_leads
    from formatters import format_profile_summary
    provider = provider or WeatherNext3ZarrProvider.from_env()
    leads = _map_leads({**params, 'mode': 'animation'}) if kind == 'windgram' else [params['hours']]
    def progress(text):
        if progress_callback:
            progress_callback(ProgressEvent('fetch', text))
    profiles = provider.profiles(point.lat, point.lon, leads, member=params['member'], progress=progress)
    first = profiles[0]
    if progress_callback:
        progress_callback(ProgressEvent('render', 'Формирую диаграмму и CSV WN3'))
    paths = []
    try:
        if kind == 'windgram':
            from windgram_product import WindgramData, _cell_from_profile
            from windgram_plot import write_windgram_png
            levels = [level for level in LEVELS if level >= params['top']]
            data = WindgramData(first.run, point.lat, point.lon, first.grid_lat, first.grid_lon,
                               leads, levels, [_cell_from_profile(profile, level) for profile in profiles for level in levels],
                               params['param'], first.model_label)
            path = Path(write_windgram_png(data, params['param']))
            summary = (f'WeatherNext 3 · срок × уровень · {point.label}\n'
                       f'Run {first.run.run_datetime_utc:%Y-%m-%d %H:%M UTC} · +{leads[0]}…+{leads[-1]} ч\n'
                       f'valid {first.valid_time_utc:%d.%m %H:%M} — {profiles[-1].valid_time_utc:%d.%m %H:%M UTC}\n'
                       f'Точка {point.lat:.4f}, {point.lon:.4f}; узел {first.grid_lat:.4f}, {first.grid_lon:.4f}\n'
                       f'{first.model_label}; {len(levels)} уровней\n{first.diagnostics_note}')
        else:
            if kind == 'aero':
                from aero_plot_layout import write_aero_png
                path = Path(write_aero_png(first))
            else:
                from profile_plot import write_profile_png
                path = Path(write_profile_png(first))
            summary = plain_profile_summary(format_profile_summary(first)) + '\n' + first.diagnostics_note
        paths.append(path)
        export = []
        for profile in profiles:
            for row in profile.dataframe.to_dict(orient='records'):
                export.append({'model': 'WeatherNext 3', 'run_utc': profile.run.run_datetime_utc.isoformat(),
                               'valid_utc': profile.valid_time_utc.isoformat(), 'lead_hour': profile.lead_hour,
                               'member': params['member'], 'requested_lat': point.lat, 'requested_lon': point.lon,
                               'grid_lat': profile.grid_lat, 'grid_lon': profile.grid_lon, **row})
        csv = csv_attachment(export, kind)
        paths.append(csv.path)
        return CommonProductResult('weathernext3', summary + '\nGoogle GCS/Zarr · модель, не радиозонд и не наблюдение',
            [ProductAttachment('image', path, path.name, first.model_label + ' · ' + kind, 'image/png'), csv],
            {'kind': kind, 'model': 'WeatherNext 3', 'provider': 'Google GCS/Zarr', 'data_kind': 'model',
             'run': first.run.run_datetime_utc.isoformat(), 'lead_from': leads[0], 'lead_to': leads[-1],
             'valid_from': first.valid_time_utc.isoformat(), 'valid_to': profiles[-1].valid_time_utc.isoformat(),
             'grid_lat': first.grid_lat, 'grid_lon': first.grid_lon, 'member': params['member'],
             'diagnostics_note': first.diagnostics_note})
    except BaseException:
        for path in paths:
            path.unlink(missing_ok=True)
        raise


def surface_plot(point, series, kind: str) -> Path:
    """Surface cloud fractions or the three precipitation heads, not a cloud-base forecast."""
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from weathernext3_surface import surface_rows
    rows = surface_rows(series)
    times = [row['valid_utc'] for row in rows]
    with tempfile.NamedTemporaryFile(prefix=f'wn3_{kind}_', suffix='.png', delete=False) as handle:
        path = Path(handle.name)
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    try:
        if kind == 'cloudgram':
            for layer, label in (('total', 'Общая'), ('low', 'Низкая'), ('mid', 'Средняя'), ('high', 'Высокая')):
                ax.plot(times, [row[f'cloud_{layer}_pct'] for row in rows], label=label, linewidth=1.8)
            ax.set(ylim=(0, 100), ylabel='Облачность, %')
            title = 'Облачность по времени · среднее ансамбля'
            note = 'Доли облачности ярусов, не высота нижней границы. Ярусы могут перекрываться.'
        else:
            for head, label in (('native', 'Основная'), ('imerg', 'Обученная на IMERG'), ('experimental', 'Экспериментальная')):
                ax.plot(times, [row[f'precip_{head}_mean_mm'] for row in rows], label=label, linewidth=1.8)
            ax.set(ylabel='Осадки за предшествующий час, мм', ylim=(0, None))
            title = 'Три варианта осадков · среднее ансамбля'
            note = 'Сравнение модельных голов, не сравнение с наблюдениями. Часовые суммы, не накопленные.'
        ax.set_title(f'WeatherNext 3 · {title}\n{point.label} · run {series.run.init_time_utc:%Y-%m-%d %HZ}', fontsize=12)
        locator = mdates.AutoDateLocator(minticks=5, maxticks=8)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m\n%H:%M'))
        ax.set_xlabel('Срок действия, UTC')
        ax.grid(alpha=.2)
        ax.legend(loc='upper left', bbox_to_anchor=(0, -.16), ncol=2, fontsize=9)
        fig.text(.5, .025, note + '\nGoogle BigQuery · модельный прогноз, не наблюдение', ha='center', fontsize=8)
        fig.subplots_adjust(bottom=.30, left=.09, right=.97, top=.88)
        fig.savefig(path)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        plt.close(fig)
