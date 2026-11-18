"""WN3 configuration/preflight. Cloud requests are opt-in (--live) and billable."""
from __future__ import annotations

from feature_flags import DISABLED_SOURCE_MESSAGE, weathernext3_enabled
import argparse
import json
from weathernext3_status import status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Сделать платный запрос к Google с настроенными лимитами')
    parser.add_argument('--upper', action='store_true', help='Проверить GCS-профиль члена 0 вместо BigQuery-точки')
    parser.add_argument('--lat', type=float, default=55.75)
    parser.add_argument('--lon', type=float, default=37.62)
    parser.add_argument('--lead', type=int, default=1)
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if not weathernext3_enabled():
        print(json.dumps({'cloud_access_verified': False, 'error': DISABLED_SOURCE_MESSAGE}, ensure_ascii=False))
        return 2
    from geocode import GeoPoint
    from messenger.weathernext3_service import build_weathernext3_product_result
    from messenger.profile_service import cleanup_product_result
    result = None
    try:
        result = build_weathernext3_product_result(GeoPoint(args.lat,args.lon,'Проверка WN3','cli'),
            'profile' if args.upper else 'point', hours=args.lead, member='0' if args.upper else 'mean')
        print(json.dumps({'cloud_access_verified':True, 'scope':'GCS profile member 0' if args.upper else 'BigQuery point',
                         'metadata':result.metadata, 'summary':result.summary},ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'cloud_access_verified':False,'error':str(exc)},ensure_ascii=False))
        return 2
    finally:
        if result is not None:
            cleanup_product_result(result)


if __name__ == '__main__':
    raise SystemExit(main())
