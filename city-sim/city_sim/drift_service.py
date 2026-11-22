"""Bounded offline paired scenario; HTTP requests never start external CFD."""
from functools import lru_cache
import hashlib
from pathlib import Path
import time
from typing import Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from .drift_engine import run
from .reproduce_drift_engine import scene
from .snow_state import SnowState

class DriftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    resolution: Literal[16, 32, 64] = 32
    wind_speed_m_s: float = Field(default=12, ge=0, le=40)
    wind_from_deg: float = Field(default=270, ge=0, lt=360)
    seconds: float = Field(default=600, gt=0, le=3600)
    loose_kg_m2: float = Field(default=5, ge=0, le=100)
    snowfall_kg_m2_h: float = Field(default=0, ge=0, le=20)
    terrain: bool = True
    buildings: bool = True

@lru_cache(maxsize=3)
def saved_scene(resolution):
    return scene(resolution)

def compare(payload):
    z, obstacles, d, provenance = saved_scene(payload.resolution)
    mask = obstacles if payload.buildings else np.zeros_like(obstacles)
    root = Path(__file__).parents[1]
    provenance = {**provenance, 'data_sha256': {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root/'city_sim/data/terrain.npz', root/'data/pkc/city.osm.gz')}}
    args = payload.model_dump(exclude={'resolution','buildings','loose_kg_m2'})
    result = run(z,payload.loose_kg_m2,dx_m=d,dy_m=d,buildings=mask,provenance=provenance,**args)
    start = time.perf_counter()
    m = np.where(mask,0.,payload.loose_kg_m2)
    old = SnowState((0,0,960,960),m,np.zeros_like(m),np.where(mask,0.,d*d),
                   np.ones_like(m),np.full_like(m,np.nan),200.,100.,400.)
    old.parameters.update(boundary='closed',ageing_per_hour=0.)
    old.ledger['initial_kg'] = old.total_kg()
    remaining = payload.seconds
    while remaining > 0:
        dt = min(30.,remaining)
        old.weather(dt,payload.wind_speed_m_s,payload.wind_from_deg,payload.snowfall_kg_m2_h)
        remaining -= dt
    return {'scenario':payload.model_dump(),'provenance':provenance,'buildings_mask':mask.tolist(),
            'drift_engine':result,'legacy_dynamic':{'solver':'snow-mass-fv-1',
                'evidence':'research_unvalidated','runtime_seconds':time.perf_counter()-start,
                'balance':old.balance(),'mass_kg_m2':old.mass.tolist()},
            'detailed_cfd':{'status':'not_connected','reason':'Offline RANS smoke is separate; coupled CFD snow transport not implemented'},
            'comparison_note':'Legacy dynamic SnowState, not the empirical CitySnow fresh-snow map'}
