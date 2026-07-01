from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from gfs_core import GfsProfileError, GfsRun, ProgressCallback, canonical_leads
from gfs_subset import bool_from_datasets, download_gfs_subset_to_disk, open_grib_datasets, scalar_from_datasets

CLOUDGRAM_DEFAULT_TO = 72
CLOUDGRAM_DEFAULT_STEP = 3
CLOUDGRAM_MAX_TO = 120

CLOUDGRAM_VARIABLES = (
    "LCDC","MCDC","HCDC","TCDC","HGT","APCP","PRATE","ACPCP","CPRAT","CRAIN","CSNOW","CFRZR","CICEP","CAPE","CIN","VIS",
)

CLOUDGRAM_LEVEL_TOKENS = (
    "lev_low_cloud_layer","lev_middle_cloud_layer","lev_high_cloud_layer","lev_entire_atmosphere","lev_cloud_ceiling","lev_surface","lev_convective_cloud_layer","lev_180-0_mb_above_ground","lev_90-0_mb_above_ground","lev_255-0_mb_above_ground",
)

@dataclass(frozen=True)
class CloudgramCell:
    lead_hour:int
    valid_time_utc:datetime
    high_cloud_pct:float|None
    mid_cloud_pct:float|None
    low_cloud_pct:float|None
    total_cloud_pct:float|None
    ceiling_m:float|None
    precip_mm:float|None
    precip_rate_mmh:float|None
    conv_precip_mm:float|None
    precip_type:str
    cape_jkg:float|None
    cin_jkg:float|None
    cb_score:int
    visibility_km:float|None
    phenomena:str

@dataclass(frozen=True)
class CloudgramData:
    run:GfsRun
    requested_lat:float
    requested_lon:float
    grid_lat:float
    grid_lon:float
    leads:list[int]
    cells:list[CloudgramCell]
    missing_fields:tuple[str,...]=()


def cloudgram_leads(lead_from=0,lead_to=CLOUDGRAM_DEFAULT_TO,step=CLOUDGRAM_DEFAULT_STEP):
    if step<=0: raise GfsProfileError("step")
    if lead_to>CLOUDGRAM_MAX_TO: raise GfsProfileError("max")
    allowed=set(canonical_leads())
    leads=[l for l in range(lead_from,lead_to+1,step) if l in allowed]
    if not leads: raise GfsProfileError("empty")
    return leads


def _vis(value):
    return None if value is None else max(0.0,float(value))


def _phen(precip,cb,ptype):
    if cb>=2 and precip and precip>0.2: return "TSRA"
    if precip and precip>0: return ptype if ptype!="—" else "RA"
    return "—"


def _read_cell(run,lead,lat,lon,cb=None):
    path,gx,gy=download_gfs_subset_to_disk(run.date,run.cycle,lead,lat,lon,CLOUDGRAM_VARIABLES,CLOUDGRAM_LEVEL_TOKENS,product_key="cloudgram")
    miss=set()
    with tempfile.TemporaryDirectory() as tmp:
        ds=open_grib_datasets(path,Path(tmp))
        low=scalar_from_datasets(ds,("lcc","lcdc"))
        mid=scalar_from_datasets(ds,("mcc","mcdc"))
        high=scalar_from_datasets(ds,("hcc","hcdc"))
        total=scalar_from_datasets(ds,("tcc","tcdc"))
        cap=scalar_from_datasets(ds,("cape",))
        cin=scalar_from_datasets(ds,("cin",))
        vis=_vis(scalar_from_datasets(ds,("vis","visibility")))
        apcp=scalar_from_datasets(ds,("apcp","tp"))
        prate=scalar_from_datasets(ds,("prate",))
        ctype="R"
    cb=int(cb or 0)
    phen=_phen(apcp,cb,ctype)
    return CloudgramCell(lead,run.run_datetime_utc+timedelta(hours=lead),low,mid,high,total,None,apcp,prate,None,ctype,cap,cin,cb,vis,phen),gx,gy,miss

def build_cloudgram_data(run,lat,lon,lead_from=0,lead_to=72,step=3):
    leads=cloudgram_leads(lead_from,lead_to,step)
    cells=[]
    miss=set()
    gx=lat; gy=lon
    for l in leads:
        c,gx,gy,m=_read_cell(run,l,lat,lon)
        cells.append(c); miss.update(m)
    return CloudgramData(run,lat,lon,gx,gy,leads,cells,tuple(miss))
