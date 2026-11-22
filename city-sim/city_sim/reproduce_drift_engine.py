"""Offline paired experiments on saved PKC DSM and OSM footprints."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import numpy as np
from shapely import contains_xy
from shapely.geometry import shape
from shapely.ops import transform
from .drift_engine import run
from .terrain import Terrain, TO_GEOGRAPHIC, TO_METRIC
from .geodata import CityData
from .snow_state import SnowState


def scene(n):
    # Fixed 960x960 m PKC window; rows north to south, EPSG:32657.
    cx,cy=TO_METRIC.transform(158.65,53.04)
    d=960/n
    x,y=np.meshgrid(cx-480+(np.arange(n)+.5)*d,cy+480-(np.arange(n)+.5)*d)
    lon,lat=TO_GEOGRAPHIC.transform(x,y)
    z=Terrain().sample(lon,lat)
    if not np.isfinite(z).all():raise ValueError('DSM window contains nodata')
    source=Path(__file__).parents[1]/'data/pkc/city.osm.gz'
    city=CityData(source,source)
    bounds=(float(lon.min()),float(lat.min()),float(lon.max()),float(lat.max()))
    mask=np.zeros(z.shape,bool)
    count=0
    for feature in city.osm_layers[0].query(bounds):
        polygon=transform(TO_METRIC.transform,shape(feature['geometry']))
        mask |= contains_xy(polygon,x,y);count+=1
    return z,mask,d,dict(source='saved Copernicus GLO-30 DSM, NOT DTM; OSM footprints',
                         crs='EPSG:32657',center_lon_lat=[158.65,53.04],extent_m=960,
                         buildings_queried=count,snow='scenario, no field observations')


def main(output):
    output.mkdir(parents=True,exist_ok=True)
    results={}
    for n in (16,32,64):
        z,mask,d,provenance=scene(n)
        cases=dict(real={},flat={'terrain':False},reverse={'wind_from_deg':90.},
                   no_buildings={'buildings':np.zeros_like(mask)},fresh={'snowfall_kg_m2_h':2.,'loose':0.},
                   feedback={'feedback_m':.0001})
        for name,options in cases.items():
            opts=dict(options);loose=opts.pop('loose',5.)
            obstacles=opts.pop('buildings',mask)
            result=run(z,loose,dx_m=d,dy_m=d,buildings=obstacles,provenance=provenance,**opts)
            results[f'{n}_{name}']=result
        # Original dynamic model on exactly the same grid, initial snow and wind.
        area=np.where(mask,0.,d*d);m=np.where(mask,0.,5.)
        legacy=SnowState((0,0,960,960),m,np.zeros_like(m),area,np.ones_like(m),np.full_like(m,np.nan),200.,100.,400.)
        legacy.parameters.update(boundary='closed',ageing_per_hour=0.)
        legacy.ledger['initial_kg']=legacy.total_kg()
        for _ in range(20):
            legacy.weather(30.,12.,270.)
        results[f'{n}_legacy']={'solver':'snow-mass-fv-1','balance':legacy.balance(),
                                'mass_kg_m2':legacy.mass.tolist()}
    diagnostics={tool:shutil.which(tool) for tool in ('WindNinja_cli','blockMesh','driftScalarDyFoam','snowBedFoam')}
    root=Path(__file__).parents[1]
    hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (root/'city_sim/data/terrain.npz',root/'city_sim/data/terrain.json',root/'data/pkc/city.osm.gz')}
    report={'python':platform.python_version(),'numpy':np.__version__,
            'code_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            'code_note':'working tree drift_engine implementation; committed source in same PR',
            'source_sha256':hashlib.sha256(Path(__file__).with_name('drift_engine.py').read_bytes()).hexdigest(),
            'data_hashes':hashes,'external_solver_paths':diagnostics,'results':results,
            'limitations':['This reduced experiment runner does not execute CFD; see cfd/results separately','DSM is not bare-earth DTM','Building mask only; no building wakes',
                           'No suspension or evolving mesh','Resolution sensitivity is not convergence proof',
                           'Large/small grids benchmark reduced solver, not detailed CFD']}
    metrics = {}
    for n in (16,32,64):
        reference = np.asarray(results[f'{n}_real']['mass_kg_m2'])
        metrics[str(n)] = {name: float(np.mean(np.abs(reference - np.asarray(results[f'{n}_{name}']['mass_kg_m2']))))
                           for name in ('flat','reverse','no_buildings','legacy')}
        metrics[str(n)]['wind_updates'] = results[f'{n}_feedback']['wind_updates']
        metrics[str(n)]['runtime_seconds'] = results[f'{n}_real']['runtime_seconds']
    for key, result in results.items():
        balance = result['balance']
        if abs(balance['error_kg']) > 1e-6 * max(1.,balance['initial_kg']):
            raise AssertionError(f'Mass balance failed: {key}')
        if np.min(result['mass_kg_m2']) < 0:
            raise AssertionError(f'Negative inventory: {key}')
    report['paired_mean_absolute_difference_kg_m2'] = metrics
    (output/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'cases':len(results),'max_mass_error_kg':max(abs(r['balance']['error_kg']) for r in results.values()),
                      'external_solver_paths':diagnostics},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=Path('out/drift-engine'))
    main(p.parse_args().output)
