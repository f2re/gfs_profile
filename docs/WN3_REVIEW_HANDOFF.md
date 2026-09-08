# Независимые направления приёмки WN3

В этой сессии инструмент запуска автономных ИИ-агентов отсутствовал. Изменения реализованы непосредственно; следующие направления разделены между проверочными заданиями, а не выданы за работу автономных агентов.

| Роль при дальнейшем агентном аудите | Область | Воспроизводимая проверка |
|---|---|---|
| Данные и метеорология | BigQuery exact run, units, cache, GCS subset/member/level, отсутствие подмены GFS | `python -m unittest discover -s tests -p test_wn3_release_data.py -v` |
| Сценарии и платформы | Telegram/MAX/VK, callback owner/restart, cancel, single-flight, API auth | `python -m unittest discover -s tests -p test_wn3_release_flow.py -v` |
| Визуализация и поставка | Все map kinds, PNG/CSV, профиль/Skew-T, MP4/GIF, DOCX/PDF | `python -m unittest discover -s tests -p test_wn3_release_render.py -v` |
| Google-инфраструктура (Юрий) | Allowlist, ADC пользователя службы, linked dataset, точная актуальная схема, реальные чанки и стоимость | `python weathernext3_check.py --live` и `--live --upper`; сопоставить с исходными таблицами/официальным Zarr |

Параллельные задания релизной проверки выполняют полный набор на Python 3.10/3.11. Шаг live-GFS в задании Python 3.11 проверяет +24/+384 и существующие weather smoke. Реальная приёмка WN3 с учётной записью Google не делегировалась фиктивному агенту и остаётся явно выделенной эксплуатационной проверкой. При её выполнении не снимать лимиты автоматически и не публиковать credentials в логах.

Python 3.10: GFS/BigQuery и общие сценарии; Python 3.11+: полный набор и обязательный файловый Zarr roundtrip. В 3.10 единственный файловый Zarr-тест пропущен по явному ограничению поставщика, проверяется отказ до сетевого запроса.
