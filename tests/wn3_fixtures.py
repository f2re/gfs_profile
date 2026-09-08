"""Synthetic schema fixtures. Not forecasts and never used by production providers."""
from datetime import datetime, timedelta, timezone
import numpy as np
import xarray as xr
from geocode import GeoPoint
from weathernext3_provider import WeatherNext3Run, WeatherNext3MapFrame
from weathernext3_zarr import ZarrRun, LEVELS, WeatherNext3ZarrProvider

INIT = datetime(2026, 9, 8, 0, tzinfo=timezone.utc)
POINT = GeoPoint(55.75, 37.62, 'СИНТЕТИЧЕСКИЙ ТЕСТ', 'fixture')

def upper_dataset(*, flattened=False):
    coords = dict(init_time=[np.datetime64('2026-09-08T00:00')],
        sample=[0, 1, 2, 3], lead_time=np.arange(6, 25, 6).astype('timedelta64[h]'),
        lead_subtime=np.arange(-5, 1).astype('timedelta64[h]'),
        level=list(LEVELS), lat_0p25=[56., 55.75, 55.5], lon_0p25=[37.5, 37.75, 38.])
    dims = tuple(coords)
    shape = tuple(len(coords[name]) for name in dims)
    z = 8000*np.log(1000/np.array(LEVELS))
    temp = np.maximum(18 - .0065*z, -65)
    dew = temp - 8
    vapour = 6.112*np.exp(17.67*dew/(dew+243.5))
    q = .622*vapour/(np.array(LEVELS) - .378*vapour)
    base = {'temperature': temp+273.15, 'specific_humidity': q,
            'geopotential': z*9.80665, 'u_component_of_wind': 3+z/3000,
            'v_component_of_wind': 4+z/4000}
    ds = xr.Dataset({name: (dims, np.broadcast_to(values.reshape(1,1,1,1,13,1,1), shape).copy()) for name, values in base.items()}, coords=coords)
    # Make sample aggregation observable and preserve scalar-speed/vector distinction.
    ds['u_component_of_wind'][dict(sample=1)] *= -1
    ds['v_component_of_wind'][dict(sample=1)] *= -1
    for variable in ds.data_vars.values():
        variable.encoding['chunks'] = (1, 2, 1, 6, 1, 2, 2)
    if flattened:
        out = xr.Dataset()
        for name in base:
            for level in LEVELS:
                arr = ds[name].sel(level=level, drop=True)
                arr.encoding['chunks'] = (1, 2, 1, 6, 2, 2)
                out[f'{name}_{level}'] = arr
        return out
    return ds

def upper_provider(dataset=None, **kwargs):
    ds = dataset if dataset is not None else upper_dataset()
    return WeatherNext3ZarrProvider(candidates=lambda: [ZarrRun(INIT, 'fixture-01')],
                                   opener=lambda uri: ds.copy(deep=True), **kwargs)

def map_frames(kind='combo', leads=(1, 3)):
    output = []
    for h in leads:
        rows = []
        for i, lat in enumerate(np.linspace(55.0, 56.5, 8)):
            for j, lon in enumerate(np.linspace(36, 39, 8)):
                value = (i*9+j*3+h) % 100
                rows.append(dict(latitude=float(lat), longitude=float(lon), cloud_total=value,
                    cloud_low=value, cloud_mid=100-value, cloud_high=value*.8, precip=value/10,
                    temperature=10+value/10, temperature_spread=value/10, wind100=value/5, solar=value*10))
        output.append(WeatherNext3MapFrame(WeatherNext3Run(INIT,360), h, INIT+timedelta(hours=h),
                       POINT.label,POINT.lat,POINT.lon,100,kind,rows,'mean'))
    return output
