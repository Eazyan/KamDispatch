"""Reduced terrain/snow coupling around the existing conservative SnowState.

Default: research slope closure. Optional: explicit offline static RANS vectors.
Snow remains a surface saltation closure, not a CFD snow phase.
"""
import hashlib
import json
import math
import time
import numpy as np
from .snow_state import SnowState

VERSION = 'drift-engine-reduced-0.2'


def run(elevation_m, loose_kg_m2, *, dx_m=30., dy_m=30., buildings=None,
        wind_speed_m_s=12., wind_from_deg=270., seconds=600., snowfall_kg_m2_h=0.,
        terrain=True, feedback_m=.02, provenance=None, wind_field=None):
    start = time.perf_counter()
    z = np.asarray(elevation_m, dtype=float)
    if z.ndim != 2 or min(z.shape) < 3 or not np.isfinite(z).all():
        raise ValueError('Finite elevation grid >=3x3 required; nodata is not zero')
    values = [dx_m,dy_m,wind_speed_m_s,wind_from_deg,seconds,snowfall_kg_m2_h,feedback_m]
    if (not np.isfinite(values).all() or min(dx_m,dy_m,feedback_m) <= 0 or
        min(wind_speed_m_s,seconds,snowfall_kg_m2_h) < 0 or seconds > 86400):
        raise ValueError('Invalid scenario inputs')
    mask = np.zeros(z.shape,bool) if buildings is None else np.asarray(buildings,dtype=bool)
    if mask.shape != z.shape: raise ValueError('Building grid shape mismatch')
    m = np.broadcast_to(np.asarray(loose_kg_m2,dtype=float),z.shape).copy()
    if not np.isfinite(m).all() or (m<0).any(): raise ValueError('Unknown snow is not zero')
    m[mask]=0
    area=np.where(mask,0.,dx_m*dy_m)
    if not np.any(area): raise ValueError('No open ground')
    rows,cols=z.shape
    state=SnowState((0.,0.,cols*dx_m,rows*dy_m),m,np.zeros_like(m),area,
                    np.ones_like(m),np.full_like(m,np.nan),200.,100.,400.)
    state.parameters.update(boundary='closed',ageing_per_hour=0.)
    state.ledger['initial_kg']=state.total_kg()
    angle=math.radians(wind_from_deg)
    ex,ey=-math.sin(angle),math.cos(angle)  # east columns, south rows
    base=z if terrain else np.zeros_like(z)
    if wind_field is not None:
        vector=np.asarray(wind_field['vector_east_south_m_s'],dtype=float)
        height=np.asarray(wind_field['height_agl_m'],dtype=float)
        if vector.shape != z.shape+(2,) or height.shape != z.shape:
            raise ValueError('Wind field must use the exact snow grid')
        if not np.isfinite(vector).all() or not np.isfinite(height).all() or (height<=.001).any():
            raise ValueError('Invalid wind field or sampling height')
    reference=None
    updates=0
    elapsed=0.
    initial=m.copy()
    while elapsed < seconds:
        surface=base+state.mass/200.
        if wind_field is None and (reference is None or np.max(np.abs(surface-reference)) >= feedback_m):
            gy,gx=np.gradient(surface,dy_m,dx_m)
            # Bounded directional speedup. SnowState exposure scales friction speed.
            state.exposure[:]=np.clip(1.+2.*(gx*ex+gy*ey),.25,2.)
            state.exposure[mask]=0
            reference=surface.copy();updates+=1
        dt=min(30.,seconds-elapsed)
        state.weather(dt,wind_speed_m_s,wind_from_deg,snowfall_kg_m2_h,
                      **({} if wind_field is None else dict(wind_vector=vector,wind_height_m=height)))
        elapsed+=dt
    h=hashlib.sha256(z.astype('<f8').tobytes()+mask.tobytes()+initial.astype('<f8').tobytes())
    config=dict(dx_m=dx_m,dy_m=dy_m,wind_speed_m_s=wind_speed_m_s,
                wind_from_deg=wind_from_deg,seconds=seconds,snowfall_kg_m2_h=snowfall_kg_m2_h,
                terrain=terrain,feedback_m=feedback_m,provenance=provenance or {})
    if wind_field is not None:
        h.update(vector.astype('<f8').tobytes()+height.astype('<f8').tobytes())
        config['wind_field']=wind_field['provenance']
    h.update(json.dumps({'shape': z.shape, 'solver': VERSION, **config},sort_keys=True).encode())
    return dict(mode='drift_engine',solver=VERSION,transport_solver='snow-mass-fv-1',
                wind_solver=('directional_slope_speed_closure_NOT_CFD' if wind_field is None
                             else wind_field['provenance']['solver']),
                evidence='research_unvalidated',input_sha256=h.hexdigest(),inputs=config,
                shape=list(z.shape),cell_area_m2=dx_m*dy_m,
                warnings=['Scenario snow inventory; no field validation',
                          ('Slope wind closure is not CFD; no building wakes or suspension' if wind_field is None
                           else 'Static RANS wind; log-law saltation closure; no snow feedback or suspension')],
                runtime_seconds=time.perf_counter()-start,wind_updates=updates,
                balance=state.balance(),mass_kg_m2=state.mass.tolist())
