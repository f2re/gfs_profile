"""Opt-in authenticated web/API entry into the same WN3 use case."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from geocode import GeoPoint
from .profile_service import cleanup_product_result
from .runtime_resources import get_runtime_resources
from .weathernext3_service import build_weathernext3_product_result, normalize_wn3_params

router = APIRouter(prefix='/api/wn3', tags=['WeatherNext 3'])


class Wn3Request(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    label: str = Field(default='Точка', max_length=120)
    params: dict = Field(default_factory=dict)


@router.post('/product')
async def product(body: Wn3Request, x_api_key: str = Header(default='')):
    key = os.getenv('WEATHERNEXT3_API_KEY', '')
    if not key:
        raise HTTPException(503, 'WN3 API не включён: задайте WEATHERNEXT3_API_KEY')
    if not hmac.compare_digest(x_api_key.encode(), key.encode()):
        raise HTTPException(403, 'Неверный API key')
    try:
        unknown = set(body.params) - set(__import__('messenger.weathernext3_service', fromlist=['DEFAULT_WN3_PARAMS']).DEFAULT_WN3_PARAMS)
        if unknown:
            raise ValueError('Неизвестные параметры WN3: ' + ', '.join(sorted(unknown)))
        params = normalize_wn3_params(body.params)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    point = GeoPoint(body.lat, body.lon, body.label, 'api')
    result = None
    archive = None
    try:
        async with get_runtime_resources().weathernext3_semaphore:
            worker = asyncio.create_task(asyncio.to_thread(build_weathernext3_product_result, point, **params))
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                try:
                    result = await worker
                except Exception:
                    pass
                raise
        with tempfile.NamedTemporaryFile(prefix='wn3_api_', suffix='.zip', delete=False) as handle:
            archive = Path(handle.name)
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('metadata.json', json.dumps({'summary': result.summary, 'metadata': result.metadata}, ensure_ascii=False, indent=2))
            for index, attachment in enumerate(result.attachments):
                bundle.write(attachment.path, f'{index+1:02d}_{Path(attachment.filename).name}')
        return FileResponse(archive, media_type='application/zip', filename='weathernext3.zip', background=BackgroundTask(archive.unlink, missing_ok=True))
    except asyncio.CancelledError:
        if archive:
            archive.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if archive:
            archive.unlink(missing_ok=True)
        raise HTTPException(502, f'WeatherNext 3: {str(exc)[:500]}') from exc
    finally:
        if result is not None:
            cleanup_product_result(result)
